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

#: ``session_id -> state``；state = turn_id / reasoning_parts / tools / ...
_STATE: Dict[str, Dict[str, Any]] = {}

#: 最近有活动的会话 id —— 适配器没有 session_id，只能靠它关联。
_LAST_ACTIVE: str = ""


def _now() -> float:
    """单调钟：TTL 判断不受系统时间调整影响。"""
    return time.monotonic()


def _as_int(value: Any) -> Optional[int]:
    """宽容地把钩子里的数字转成 int；转不了返回 ``None``（不抛）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(float(value))
    except (TypeError, ValueError):
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


def _touch_locked(session_id: str, turn_id: str, now: float) -> Dict[str, Any]:
    """取（或新建）会话状态；``turn_id`` 变化视为新回合，清空过程数据。"""
    global _LAST_ACTIVE
    state = _STATE.get(session_id)
    if state is None:
        state = {"turn_id": "", "reasoning_parts": [], "reasoning_len": 0,
                 "tools": [], "started": now, "updated": now}
        _STATE[session_id] = state
    # 新回合：同一会话换了 turn_id 就重置面板，别把上一回合的推理带过来。
    # turn_id 缺失时（老版本 Hermes）保守地继续累积。
    if turn_id and state.get("turn_id") and turn_id != state["turn_id"]:
        state["reasoning_parts"] = []
        state["reasoning_len"] = 0
        state["tools"] = []
        state["started"] = now
    if turn_id:
        state["turn_id"] = turn_id
    state["updated"] = now
    _LAST_ACTIVE = session_id
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
    """工具参数的单行短预览；序列化失败返回空串（预览是装饰，不抛）。"""
    if args is None or args == {}:
        return ""
    try:
        text = json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        return ""
    if len(text) > _ARGS_PREVIEW_CHARS:
        return text[: _ARGS_PREVIEW_CHARS] + "…"
    return text


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
        _touch_locked(sid, tid, now)
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
        state["reasoning_parts"].append(text)
        state["reasoning_len"] += len(text)
        _compact_parts_locked(state)
        _purge_locked(now)


def record_tool_started(session_id: str, turn_id: str, tool_name: str,
                        args: Any = None, tool_call_id: str = "") -> None:
    """``pre_tool_call`` 钩子回调 —— **fail-closed 钩子，必须极快**。"""
    now = _now()
    with _LOCK:
        state = _touch_locked(str(session_id or ""), str(turn_id or ""), now)
        tools: List[Dict[str, Any]] = state["tools"]
        tools.append({
            "id": str(tool_call_id or ""),
            "name": str(tool_name or "tool"),
            "status": "running",
            "duration_ms": None,
            "preview": _args_preview(args),
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
def snapshot(turn_id: str = "") -> Optional[Dict[str, Any]]:
    """最近活跃会话的面板数据；没有内容返回 ``None``（调用方据此不渲染）。

    返回 ``{"session_id", "turn_id", "reasoning", "tools", "age"}``：
    ``reasoning`` 是拼接好的整段推理，``tools`` 每项含
    ``name / status / duration_ms / preview``。

    归属策略分两档：

    * **给了 ``turn_id``（native 流式帧会带）→ 精确定位。** 钩子载荷与
      ``send_stream_frame()`` 拿到的是同一个 ``turn_id`` —— 都由
      ``agent/turn_context.py`` 生成，形如 ``session:task:uuid8``，内含 session
      且带随机尾，跨会话唯一。所以并发会话各查各的，**不会串台**。
      匹配到但没内容就返回 ``None``：空就是空，绝不退回别人的数据。
    * **没给（edit 传输 / 老版本 Hermes）→ 回退「最近活跃」**，多会话并发时
      可能短暂串台（已知限制，见 README）。
    """
    now = _now()
    tid = str(turn_id or "")
    with _LOCK:
        _purge_locked(now)
        if tid:
            matched = next(((s, st) for s, st in _STATE.items()
                            if st.get("turn_id") == tid), None)
            if matched is None:
                return None  # 该回合确实没有面板数据，而不是「显示别人的」
            sid, state = matched
        else:
            sid = _LAST_ACTIVE
            state = _STATE.get(sid) if sid else None
            if state is not None and not (state.get("reasoning_parts") or state.get("tools")):
                state = None  # 活跃会话暂时没内容：走下面的回退
            if state is None:
                # 回退：找「最近更新且真的有内容」的会话 —— 只在拿不到 turn_id 时才走。
                candidates = [(sid2, st) for sid2, st in _STATE.items()
                              if st.get("reasoning_parts") or st.get("tools")]
                if not candidates:
                    return None
                sid, state = max(candidates, key=lambda kv: kv[1].get("updated", 0.0))
        parts = list(state.get("reasoning_parts") or [])
        tools = [dict(item) for item in state.get("tools") or []]
        state_turn_id = state.get("turn_id", "")
        updated = state.get("updated", now)
    # 拼接放到锁外 —— 推理最长可达 _MAX_REASONING_CHARS，锁里做会拖慢
    # fail-closed 的 pre_tool_call 回调（见模块 docstring 线程模型）。
    reasoning = "".join(parts)
    if not reasoning and not tools:
        return None
    return {
        "session_id": sid,
        "turn_id": state_turn_id,
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
