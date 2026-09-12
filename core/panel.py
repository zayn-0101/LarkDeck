"""思考与工具面板的数据层 —— 官方钩子喂数据，卡片更新时读快照。

为什么要有这一层
----------------
卡片的「思考与工具」折叠面板想显示两样东西，而它们**都不在**适配器的
``send()`` / ``edit_message()`` 入参里：

* 推理过程 → 官方钩子 ``on_stream_delta``（``kind="reasoning"``）；
* 工具调用 → 官方钩子 ``pre_tool_call`` / ``post_tool_call``。

其余飞书卡片插件全都靠 patch 核心 ``run_turn.py`` 的实例回调拿这两样数据，
larkdeck 走公开钩子（见 :mod:`larkdeck.core.hooks`），代价是钩子载荷**只有
``session_id``，没有 ``chat_id`` / ``message_id``** —— 适配器更新卡片时
无从知道当前卡片属于哪个会话。

关联策略（已知取舍）
--------------------
按「**最近活跃会话**」取快照：谁最后一次产生推理 / 工具事件，面板就显示谁。
单会话（绝大多数场景）下完全正确；多会话并发时可能短暂串台 —— 换来的是
零源码改写，与页脚指标的取舍一致（见 :mod:`larkdeck.core.context`）。

回合边界：同一会话的 ``turn_id`` 变化时清空过程数据，新回合的面板不残留
上一回合的推理。``turn_id`` 缺失（老版本 Hermes）时保守地继续累积。

线程模型
--------
钩子在调用方线程同步执行，其中 ``pre_tool_call`` 还是 **fail-closed** 钩子
（回调卡住会阻止工具执行），所以所有写入函数必须廉价：锁内只做 list/dict
操作，字符串拼接推迟到 :func:`snapshot`（读取频率远低于增量频率）。
推理增量按片段列表累积，长到阈值才做一次合并，避免热路径上的 O(n²) 拷贝。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

logger = logging.getLogger("larkdeck.panel")

_LOCK = threading.Lock()

#: 最多同时保留的会话数；超出按最近更新淘汰。
_MAX_SESSIONS = 16

#: 会话数据存活时间（秒）；期间没有任何新事件就丢弃。
_TTL_SECONDS = 1800.0

#: 单个会话推理 buffer 上限（字符）；超出后保留最早部分（渲染层再按
#: ``max_reasoning_chars`` 截断并留痕）。防御性上限，正常远达不到。
_MAX_REASONING_CHARS = 262144

#: 单个会话最多缓存的工具步骤数（渲染层只显示最近 ``max_panel_steps`` 步）。
_MAX_BUFFERED_TOOLS = 200

#: 推理片段列表超过该长度就合并成单段，防止 list 无限增长。
_PARTS_COMPACT_AT = 8192

#: 工具参数预览的长度上限（字符）。
_ARGS_PREVIEW_CHARS = 80

#: 每个会话最多记住多少个「已作废的 turn_id」（迟到事件丢弃用）。有界，防长跑会话膨胀。
_MAX_CLOSED_TURNS = 8

#: ``session_id -> state``；state = turn_id / reasoning_parts / tools / ...
_STATE: Dict[str, Dict[str, Any]] = {}

#: 最近有活动的会话 id —— 适配器没有 session_id，只能靠它关联。
_LAST_ACTIVE: str = ""


def _now() -> float:
    """单调钟：TTL 判断不受系统时间调整影响。"""
    return time.monotonic()


def _as_int(value: Any) -> Optional[int]:
    """宽容地把钩子里的数字转成 int；转不了返回 ``None``（不抛）。

    连 ``OverflowError`` 一起接住：``int(float("inf"))`` 抛的是它，不是 ``ValueError``；
    漏了会让坏载荷把 ``pre_tool_call``（fail-closed）回调打炸。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return None


def _purge_locked(now: float) -> None:
    """清理过期会话与超量会话（锁内调用，O(_MAX_SESSIONS)）。"""
    global _LAST_ACTIVE
    expired = [sid for sid, st in _STATE.items()
               if now - st.get("updated", 0.0) > _TTL_SECONDS]
    for sid in expired:
        _STATE.pop(sid, None)
    if len(_STATE) > _MAX_SESSIONS:
        oldest = sorted(_STATE.items(), key=lambda kv: kv[1].get("updated", 0.0))
        for sid, _ in oldest[: len(_STATE) - _MAX_SESSIONS]:
            _STATE.pop(sid, None)
    if _LAST_ACTIVE not in _STATE:
        _LAST_ACTIVE = ""


def _touch_locked(session_id: str, turn_id: str, now: float, *,
                  reopen: bool = False) -> Optional[Dict[str, Any]]:
    """取（或新建）会话状态；``turn_id`` 变化视为新回合，清空过程数据。

    **返回 ``None`` 表示这次事件属于一个已作废的旧回合，调用方必须原样丢弃** ——
    且在**任何状态写入之前**丢弃（``updated`` / ``_LAST_ACTIVE`` 都不能被碰，否则迟到
    事件仍会刷新 TTL 与「最近活跃」路由）。

    为什么需要它：Hermes 给每个 ``(钩子名, 回调)`` 配一对独立的有界队列 + 独立守护线程
    （见 ``agent/plugin_stream_hooks.py``），**跨钩子没有顺序保证**。于是「t1 的推理增量
    还堵在队列里、t2 的 ``on_stream_start`` 先派发」是结构性的：新回合清空面板并把
    turn_id 改成 t2，随后迟到的 t1 事件又被判成「又换回合」，把 t2 的面板清掉、turn_id
    倒回 t1。把「被替换掉的那个 turn_id」立刻记进作废集，迟到事件就再也进不来。

    注意两处刻意的设计：
    * 登记**必须在替换分支里做**，不能只放在 :func:`begin_turn`。若 t2 的首个事件先于
      t2 的 ``on_stream_start`` 到达（两条队列，属常态），替换是这里自己完成的，
      t1 从未经过 ``begin_turn``，集合会恒空、保护失效。
    * ``reopen=True``（只由 :func:`begin_turn` 传）表示「权威地宣告新回合开始」，
      此时先从作废集里摘掉该 id —— 万一 turn_id 被复用（uuid 碰撞可忽略，但替身/
      老版本可能给稳定 id），不至于让面板永久空掉。
    """
    global _LAST_ACTIVE
    sid = str(session_id or "")
    if not sid:
        # 归属不明的数据不许进桶：空 session_id 会建成匿名桶，而 snapshot() 会把它
        # 当成一个正常会话选中 —— 于是别的卡片上会冒出无主的面板数据。
        return None
    state = _STATE.get(sid)
    if state is None:
        state = {"turn_id": "", "reasoning_parts": [], "reasoning_len": 0,
                 "tools": [], "started": now, "updated": now,
                 "closed": deque(maxlen=_MAX_CLOSED_TURNS)}
        _STATE[sid] = state
    closed = state.get("closed")
    if not isinstance(closed, deque):
        closed = state["closed"] = deque(maxlen=_MAX_CLOSED_TURNS)
    tid = str(turn_id or "")
    if tid:
        if reopen:
            try:
                closed.remove(tid)
            except ValueError:
                pass
        elif tid in closed:
            return None  # 迟到的旧回合事件：丢弃，绝不写任何状态
        current = state.get("turn_id") or ""
        if current and tid != current:
            # 新回合：先把被替换的那个 turn_id 记为作废，再清空过程数据
            closed.append(current)
            state["reasoning_parts"] = []
            state["reasoning_len"] = 0
            state["tools"] = []
            state["started"] = now
        state["turn_id"] = tid
    state["updated"] = now
    _LAST_ACTIVE = sid
    return state


def _compact_parts_locked(state: Dict[str, Any]) -> None:
    """片段过多或超长时合并成单段（摊薄成本，防热路径 O(n²)）。"""
    parts: List[str] = state["reasoning_parts"]
    if len(parts) > _PARTS_COMPACT_AT:
        joined = "".join(parts)
        state["reasoning_parts"] = [joined]
        state["reasoning_len"] = len(joined)
        parts = state["reasoning_parts"]
    if state["reasoning_len"] > _MAX_REASONING_CHARS and parts:
        joined = "".join(parts)[: _MAX_REASONING_CHARS]
        state["reasoning_parts"] = [joined]
        state["reasoning_len"] = len(joined)


def _args_preview(args: Any) -> str:
    """工具参数的单行短预览；序列化失败返回空串（预览是装饰，不抛）。

    ⚠️ **必须是有界序列化**：原始实现直接 ``json.dumps(整个 args)`` 再截 80 字符，
    而 ``pre_tool_call`` 拿到的是**未截断**的原始参数（write_file 类工具会带整篇文件
    内容）—— 实测 5MB 参数 ≈ 10ms、200MB ≈ 470ms。这个回调是 **fail-closed** 的：
    它慢，会拖住整批工具执行，并抬高「回调超时 → 被判 skip → 按 block 处理」的概率。

    做法：先 ``_shrink`` 出一个有界副本（限制每层条目数与超长字符串），再序列化。
    深度与广度都有上限，最坏访问节点数是常数级，不再随参数规模线性增长。
    """
    if args is None or args == {}:
        return ""
    try:
        text = json.dumps(_shrink(args), ensure_ascii=False, default=str)
    except Exception:
        return ""
    if len(text) > _ARGS_PREVIEW_CHARS:
        return text[: _ARGS_PREVIEW_CHARS] + "…"
    return text


#: ``_shrink`` 的预算：每层最多看几个条目、最多下钻几层、单个字符串留多长。
_SHRINK_ITEMS = 8
_SHRINK_DEPTH = 3
_SHRINK_STR_CHARS = 120


def _shrink(value: Any, depth: int = 0) -> Any:
    """生成参数的**有界**副本，供预览序列化使用（有损，仅用于展示）。"""
    if depth >= _SHRINK_DEPTH:
        return "…" if isinstance(value, (dict, list, tuple)) else value
    if isinstance(value, str):
        return value[:_SHRINK_STR_CHARS] + ("…" if len(value) > _SHRINK_STR_CHARS else "")
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, dict):
        out: Dict[Any, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _SHRINK_ITEMS:
                out["…"] = f"+{len(value) - _SHRINK_ITEMS}"
                break
            out[str(key)[:64]] = _shrink(item, depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        out_list = [_shrink(item, depth + 1) for item in items[:_SHRINK_ITEMS]]
        if len(items) > _SHRINK_ITEMS:
            out_list.append(f"+{len(items) - _SHRINK_ITEMS}")
        return out_list
    return value


# --------------------------------------------------------------------------- #
# 采集（钩子回调 → 本模块）
# --------------------------------------------------------------------------- #
def begin_turn(session_id: str, turn_id: str) -> None:
    """``on_stream_start`` 钩子回调：标记新回合开始，清掉上一回合的面板。

    适配器发首帧可能**早于**新回合的第一个推理 / 工具事件 —— 不做这一步，
    新卡片会短暂（若本回合没有面板事件则是永久）带着上一回合的面板。
    ``on_stream_start`` 每次 API 尝试都会触发（含重试），所以这里靠
    ``turn_id`` 比较保持幂等：同一回合内重复调用不会清空任何东西。
    缺 ``session_id`` / ``turn_id``（老版本 Hermes）时保守地什么都不做。
    """
    sid = str(session_id or "")
    tid = str(turn_id or "")
    if not sid or not tid:
        return
    now = _now()
    with _LOCK:
        # reopen=True：这是「新回合开始」的权威信号，允许从作废集里重新收养该 id。
        _touch_locked(sid, tid, now, reopen=True)
        _purge_locked(now)


def record_reasoning(session_id: str, turn_id: str, delta: str) -> None:
    """``on_stream_delta``（``kind="reasoning"``）钩子回调。

    只 append 片段，不做拼接 —— 高频热路径（每个 token 一次）。
    先归一成字符串再入账：坏载荷（数字 / None）不会让 ``len()`` 抛错，
    也不会留下「片段已加、长度没加」的半更新状态。
    """
    text = str(delta) if delta else ""
    if not text:
        return
    now = _now()
    with _LOCK:
        state = _touch_locked(str(session_id or ""), str(turn_id or ""), now)
        if state is None:
            return  # 迟到的旧回合事件 / 无归属：丢弃，别污染当前回合
        state["reasoning_parts"].append(text)
        state["reasoning_len"] += len(text)
        _compact_parts_locked(state)
        _purge_locked(now)


def record_tool_started(session_id: str, turn_id: str, tool_name: str,
                        args: Any = None, tool_call_id: str = "") -> None:
    """``pre_tool_call`` 钩子回调 —— **fail-closed 钩子，必须极快**。"""
    now = _now()
    # 预览的计算（可能序列化很大的 args）必须在取锁**之前**做完：
    # 这个回调是 fail-closed —— 持锁做重活会让并发工具的 pre_tool_call 排队，
    # 抬高「回调超时 → 被判 skip → 按 block 处理」的概率。
    preview = _args_preview(args)
    with _LOCK:
        state = _touch_locked(str(session_id or ""), str(turn_id or ""), now)
        if state is None:
            return
        tools: List[Dict[str, Any]] = state["tools"]
        tools.append({
            "id": str(tool_call_id or ""),
            "name": str(tool_name or "tool"),
            "status": "running",
            "duration_ms": None,
            "preview": preview,
            "t0": now,
        })
        if len(tools) > _MAX_BUFFERED_TOOLS:
            state["tools"] = tools[-_MAX_BUFFERED_TOOLS:]
        _purge_locked(now)


def record_tool_finished(session_id: str, turn_id: str, tool_name: str = "",
                         status: str = "ok", duration_ms: Any = None,
                         tool_call_id: str = "") -> None:
    """``post_tool_call`` 钩子回调。

    先按 ``tool_call_id`` 找开着的步骤（精确配对）；找不到就兜底补一条完成态
    —— 宁可多一行，也不让这次调用凭空消失（pre 丢包 / 顺序颠倒时仍可见）。
    """
    now = _now()
    with _LOCK:
        state = _touch_locked(str(session_id or ""), str(turn_id or ""), now)
        if state is None:
            return  # 迟到的旧回合事件 / 无归属：丢弃
        tools: List[Dict[str, Any]] = state["tools"]
        target: Optional[Dict[str, Any]] = None
        tcid = str(tool_call_id or "")
        if tcid:
            for item in reversed(tools):
                if item.get("id") == tcid and item.get("status") == "running":
                    target = item
                    break
        if target is None:
            target = {"id": tcid, "name": str(tool_name or "tool"),
                      "status": "running", "duration_ms": None,
                      "preview": "", "t0": now}
            tools.append(target)
            if len(tools) > _MAX_BUFFERED_TOOLS:
                state["tools"] = tools[-_MAX_BUFFERED_TOOLS:]
        if not target.get("name") or target["name"] == "tool":
            if tool_name:
                target["name"] = str(tool_name)
        target["status"] = str(status or "ok")
        ms = _as_int(duration_ms)
        if ms is not None:
            target["duration_ms"] = max(0, ms)
        elif target.get("duration_ms") is None:
            # 钩子没给耗时就自己算：墙钟差足够给用户一个量级感。
            target["duration_ms"] = max(0, int((now - target.get("t0", now)) * 1000))
        _purge_locked(now)


# --------------------------------------------------------------------------- #
# 读快照
# --------------------------------------------------------------------------- #
def snapshot() -> Optional[Dict[str, Any]]:
    """最近活跃会话的面板数据；没有内容返回 ``None``（调用方据此不渲染）。

    返回 ``{"session_id", "turn_id", "reasoning", "tools", "age"}``：
    ``reasoning`` 是拼接好的整段推理，``tools`` 每项含
    ``name / status / duration_ms / preview``。

    ⚠️ **不要试图用 ``turn_id`` 把卡片和会话对上 —— 两侧的 ``turn_id`` 不是同一个东西。**
    2026-09-12 踩过：钩子载荷里的 ``turn_id`` 是 ``agent._current_turn_id``
    （``f"{session_id}:{task_id}:{uuid4[:8]}"``，见 ``agent/turn_context.py``）；
    而 ``send_stream_frame()`` 收到的 ``turn_id`` 是 ``GatewayStreamConsumer``
    自己现生成的一个裸 uuid（``gateway/stream_consumer.py``：
    ``self._turn_id = str(uuid.uuid4())  # keys send_stream_frame() per concurrent consumer``）。
    两者命名空间不相干、**永远匹配不上** —— 按它 join 会让面板永久不渲染，
    而且不报任何错。``grep -rn _current_turn_id gateway/`` 一处都没有可证。
    要真正按会话精确定位，得靠 ``(platform, chat_id) -> session_id`` 的反查
    （``gateway/mirror.py`` 的 ``_find_session_id`` 走 state.db），
    代价是一次数据库查询 + 新的私有依赖，尚未接入。
    """
    now = _now()
    with _LOCK:
        _purge_locked(now)
        sid = _LAST_ACTIVE
        state = _STATE.get(sid) if sid else None
        if state is not None and not (state.get("reasoning_parts") or state.get("tools")):
            state = None  # 活跃会话暂时没内容：走下面的回退
        if state is None:
            # 回退：找「最近更新且真的有内容」的会话 —— 钩子载荷没有 chat_id，
            # 会话路由只能尽力而为；多会话并发时可能短暂串台（已知限制）。
            candidates = [(sid2, st) for sid2, st in _STATE.items()
                          if st.get("reasoning_parts") or st.get("tools")]
            if not candidates:
                return None
            sid, state = max(candidates, key=lambda kv: kv[1].get("updated", 0.0))
        parts = list(state.get("reasoning_parts") or [])
        tools = [dict(item) for item in state.get("tools") or []]
        turn_id = state.get("turn_id", "")
        updated = state.get("updated", now)
    # 拼接放到锁外 —— 推理最长可达 _MAX_REASONING_CHARS，锁里做会拖慢
    # fail-closed 的 pre_tool_call 回调（见模块 docstring 线程模型）。
    reasoning = "".join(parts)
    if not reasoning and not tools:
        return None
    return {
        "session_id": sid,
        "turn_id": turn_id,
        "reasoning": reasoning,
        "tools": tools,
        "age": max(0.0, now - updated),
    }


def reset() -> None:
    """清空全部面板数据（测试用）。"""
    global _LAST_ACTIVE
    with _LOCK:
        _STATE.clear()
        _LAST_ACTIVE = ""


__all__ = [
    "begin_turn",
    "record_reasoning",
    "record_tool_started",
    "record_tool_finished",
    "snapshot",
    "reset",
]
