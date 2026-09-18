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
import re
import threading
import time
from collections import deque
from itertools import islice
from typing import Any, Dict, List, Optional

logger = logging.getLogger("larkdeck.panel")

# ⚠️ 这里**曾经**是 `_LOCK = threading.Lock()`（R11-A0 改掉）：锁必须与它守的容器**同源**。
# 容器已经是进程级共享的（见下面 `_shared_state`），而锁是模块级 ⇒ 同一进程里插件的
# **两份模块对象各持一把锁**，互斥从构造上失效：一份在 `_purge_locked` 里迭代
# `_STATE.items()`、另一份同时在插新桶 ⇒ 实测 `RuntimeError: dictionary changed size
# during iteration`（真机上是「偶发丢面板」，完全不可复现的那种）。现在的 `_LOCK` 定义在
# `_SHARED` 之后，直接从共享盒子里取。

#: 最多同时保留的会话数；超出按最近更新淘汰。
_MAX_SESSIONS = 16

#: 会话数据存活时间（秒）；期间没有任何新事件就丢弃。
_TTL_SECONDS = 1800.0

#: 单个会话推理 buffer 上限（字符）；超出后保留最早部分（渲染层再按
#: ``max_reasoning_chars`` 截断并留痕）。防御性上限，正常远达不到。
_MAX_REASONING_CHARS = 262144

#: 单个会话最多缓存的工具步骤数（渲染层只显示最近 ``max_panel_steps`` 步）。
_MAX_BUFFERED_TOOLS = 200

#: R11-A7：本回合**正文累积**的字符上限。到顶就**冻结**（停止入账并标记不完整）——
#: 而不是丢掉早期片段：正文累积的唯一用途是「当**完整**前缀去证明核心叠了进度块」，
#: 于是「不完整」必须是一个**显式状态**，让判据整体退回「不剥」（fail-open），
#: 绝不能留一份「看起来像前缀、其实是残缺」的缓冲区 —— 那会在正文里凭空吞掉一段。
_MAX_ANSWER_CHARS = 262144

#: 单个会话最多保留多少「推理轮」（渲染层再按配置裁剪）。
_MAX_ROUNDS = 50

#: 工具参数预览的长度上限（字符）。
_ARGS_PREVIEW_CHARS = 80

#: 每个会话最多记住多少个「已作废的 turn_id」（迟到事件丢弃用）。有界，防长跑会话膨胀。
_MAX_CLOSED_TURNS = 8

#: ``chat_id -> (session_id, 记录时刻)``：由 ``pre_gateway_dispatch`` 观察到的归属。
# ⚠️⚠️ **进程内状态必须真的「进程内」**（2026-09-14 真机踩到，根因在 Hermes 侧）：
# 同一个网关进程里 **插件发现会跑两遍**（日志实测：`Plugin discovery complete` 出现两次，
# 启动自检打印两遍），于是本模块被加载成**两份模块对象** ⇒ 如果状态绑在模块变量上，
# 就会变成两份：**钩子写 A 份、卡片读 B 份** ⇒ 面板恒空（实测 `buckets=0` 而
# `pre_tool_call 钩子触达` 同时在打）；`context._STATUS`（写卡账本）同理，
# 表现为「最近写卡：无记录 · 累计 0 帧」而卡片明明在往外冒字。
# 修法：把状态容器挂到一个**进程级稳定位置**（`builtins` 上的私有名字），
# 两份模块对象取到的是**同一个 dict** —— 这正是文档里「进程级全局」这句话的实现。
#: **进程级共享盒子的唯一键清单**（R11-A0）。改这里就是改盒子本体 —— 别在别的模块里再抄一份
#: （`context.py` 过去自己声明过一串同名键，那是「同一件事两处真相」：加一个键很容易只加一处，
#: 而漏掉的那一份会**静默新建一个不属于盒子的容器**，症状与「世代裂脑」一模一样）。
#: 断言：`tests/test_units.py` 的 ㉙ 要求 `set(真实盒子) == set(_SHARED_BOX_FACTORY)`。
_SHARED_BOX_FACTORY: Dict[str, Any] = {
    # —— 面板数据层（panel.py 自己用）
    "panel_state": {}, "panel_chat_session": {}, "panel_last_active": [""],
    # ⚠️ **锁必须与它守的容器同源**（R11-A0）：容器进程级共享、锁模块级 ⇒ 两份模块对象
    # 各持一把锁 ⇒ 互斥失效（实测 `dictionary changed size during iteration`）。
    "panel_lock": threading.Lock(),
    # R11-A7：正文累积的**独立小仓库**（``session_id -> {turn, parts, ...}``）与它自己的
    # 「最近活跃」盒子。为什么不塞进 `panel_state` 的会话桶、也不借 `panel_last_active`：
    # 正文累积只有一个用途（当**前缀对照物**），而面板那个盒子会被 `_purge_locked` 按
    # 「必须指向一个真面板桶」清空 —— 借来的话，「纯正文回合」（压根没有面板桶）第一帧就丢归属。
    "answers": {}, "answers_last": [""],
    # v0.7.0 P1：正文桶的**世代号**（换回合/换会话重建时递增）。让卡片能用
    # 「建卡时的桶世代 == 当前桶世代」判断归属漂移，避免 hook turn 与 consumer turn
    # 两套命名空间不可比的问题。
    "answer_gen": [0],
    # v0.7.0 P1：`on_stream_end` 对账快照（每次 API 调用一次；只观察、不决定正文）。
    # `session:turn -> {iteration, finished, error, len, text, sha, updated}`，只留最近若干条。
    "stream_ends": {},
    # —— 指标与账本（context.py 用）
    "status": None,
    "context_lock": threading.Lock(), "ctx_inflight": set(),
    "ctx_latest": {}, "ctx_max_cache": {}, "ctx_retry_after": {},
    "ctx_max_override": [None], "ctx_aliases": {},
    # —— 诊断：本模块在这个进程里被加载了几次（1 = 第一世代，≥2 = 发生过世代更替）
    "load_seq": 0,
    # —— 注册结论（adapter.py 用）：两世代各记一份的话，`/larkdeck status` 与启动自检
    # 会按「读到哪一代」给两个不同的答案（钩子 7/7 还是 0/7、命令注册还是没注册）。
    # ⚠️ 适配器的**类缓存**（`_BASE_CLASSES`/`_MERGED_CLASSES`）**故意不在**这里 ——
    # 它们缓存的是代码而不是结论，共享会把活适配器冻在上一世代的代码上（见 adapter.py 的说明）。
    "adapter_hooks": {}, "adapter_command": {},
    # P1a：能力探测快照。`build_adapter()` 在 **register/build 时**算一次并存进盒子；
    # `/larkdeck status` 只读这份快照，绝不在渲染卡片的线程里重新探测（基类可能已经不是
    # 当初接管的那一个，而且探测不应在命令路径上做 IO）。缺失 ⇒ 状态卡写「未探测」。
    "adapter_probe_report": {},
    # P2：官方插件上下文 `ctx.get_config` 的**只读**句柄。聊天侧写入路径已在安全审计后移除，
    # 这里不保存 `set_config`；命令可能来自旧世代模块对象，所以句柄与 `PROBE_REPORT` 同等共享。
    "adapter_plugin_ctx": {},
    # ⚠️ **明确不共享**的一项：各 `_log_*_once` 的限流戳挂在**函数对象**上（`fn._at`），
    # 而函数对象是每个世代的模块自己的 ⇒ 换世代后限流窗口会重置一次（最多多打一条同样的
    # 限流日志）。它**不影响任何行为与数据**，而为它搬家要把 ~10 处调用点改成查表 ——
    # 收益与风险不成比例，所以这里显式记成「已决定：不共享」，不是「忘了」。
}


def load_seq() -> int:
    """**本模块对象**在进程里的加载序号（1 = 第一世代；≥2 = 这一份是后来加载的）。

    用途只有一个：让「哪一份模块对象是活的」变成可以**读出来**的事实（R11-A1）。
    ⚠️ 它读的是 import 时定格的常量，**不是**盒子里的当前值 —— 后者会被后一个世代改写，
    于是两份模块对象报同一个数、世代标记等于没有（实测过）。
    ⚠️ 它**只读诊断**，绝不许参与任何业务判断 —— 拿它当「谁是新代码」的判据会在热重载时把
    行为改掉（那就是在猜，而本项目对「猜」的代价有前科）。
    """
    return _LOAD_SEQ


def latest_load_seq() -> int:
    """进程内**最新那一份**模块对象的加载序号（盒子里的当前值）。

    与 :func:`load_seq` 的差是这个诊断的第二个维度：本模块 `load_seq()=1` 而
    `latest_load_seq()=2` ⇒ **这一份是旧的，进程里已经存在第二世代**。
    """
    return int(_SHARED.get("load_seq") or 0)


def shared_box() -> Dict[str, Any]:
    """进程级共享盒子的**唯一入口**（`context.py` / `adapter.py` 都从这里取，不各自声明键）。"""
    import builtins
    box = getattr(builtins, "_larkdeck_shared_state", None)
    if not isinstance(box, dict):
        box = {}
        setattr(builtins, "_larkdeck_shared_state", box)
    for key, default in _SHARED_BOX_FACTORY.items():
        if key not in box:
            # 容器按**类型**造一个新的（不能共用 `_SHARED_BOX_FACTORY` 里那个原对象：
            # 它是模块级原型，共用会让「测试里 reset 一下」把原型本体也清掉）。
            box[key] = dict(default) if isinstance(default, dict) else (
                list(default) if isinstance(default, list) else (
                    set(default) if isinstance(default, set) else default))
    return box


def _shared_state() -> Dict[str, Any]:
    """（历史名字，保留给老调用点）等价于 :func:`shared_box`。"""
    return shared_box()


_SHARED = shared_box()

#: **本模块被加载了几次**（R11-A1 的决定性凭据）。同一进程里插件被加载两遍时，
#: 第一世代的模块看到 1、第二世代看到 2 —— 于是「哪一份是活的」不再靠日志时序去猜：
#: 启动自检、启动快照里都带上这个数字，一眼就能把两行日志分开。
#: 它同时是「世代更替真的发生了」的**机器可读**证据（`Plugin discovery complete` 出现两次
#: 只是间接迹象，而且极易被别的插件淹没）。
_SHARED["load_seq"] = int(_SHARED.get("load_seq") or 0) + 1
#: ⚠️ **必须在 import 时定格成本模块对象自己的那个号**：`load_seq()` 若去读盒子里的当前值，
#: 两份模块对象会报**同一个数**（盒子是共享的），世代标记就等于没有 —— 这个坑是写门禁时
#: 实测出来的（`3 → 3`），不是推演出来的。
_LOAD_SEQ: int = int(_SHARED["load_seq"])

#: 面板的互斥锁 —— **从共享盒子取**（见 `_SHARED_BOX_FACTORY` 里的长注释）。
_LOCK: threading.Lock = _SHARED["panel_lock"]

#: ``session_id -> {"turn", "parts", "len", "complete", "tool_since_text", "updated"}``
_ANSWERS: Dict[str, Any] = _SHARED.setdefault("answers", {})
#: 正文仓库自己的「最近活跃会话」（长度 1 的 list，跨世代共享，理由同上）
_ANSWERS_LAST: list = _SHARED.setdefault("answers_last", [""])
#: 正文桶世代计数器（共享盒子，跨模块世代一致；只在 `_answer_bucket_locked` 重建时 +1）
_ANSWER_GEN: list = _SHARED.setdefault("answer_gen", [0])
#: 正文累积仓库的上限/存活期（与面板桶同一量级；它是**可选优化**的数据，淘汰了只是少剥一次）
_ANSWER_MAX_SESSIONS = 32
_ANSWER_TTL_SECONDS = 1800.0

#: v0.7.0 P1：`on_stream_end` 只读对账存储（共享盒子，跨模块世代一致）。
#: 键 = ``session_id:turn_id``，值只保留最近一次 API 调用的 final_text 前缀关系诊断。
_STREAM_ENDS: Dict[str, Any] = _SHARED.setdefault("stream_ends", {})
_STREAM_END_MAX = 16

_CHAT_SESSION: Dict[str, Any] = _SHARED["panel_chat_session"]
_CHAT_SESSION_MAX = 256
_CHAT_SESSION_TTL = 86400.0

#: ``session_id -> state``；state = turn_id / rounds / current_round / tools / status / ...
_STATE: Dict[str, Dict[str, Any]] = _SHARED["panel_state"]

# --------------------------------------------------------------------------- #
# 回合状态（卡片颜色由它决定）
# --------------------------------------------------------------------------- #
#: 回合结局。``None`` = 还没有结论（进行中 / 没有信号），此时面板保持中性灰边。
STATUS_OK = "ok"            # 正常完成 → 绿
STATUS_ERROR = "error"      # 报错 → 红
STATUS_STOPPED = "stopped"  # 用户中止 → 黄


#: 最近有活动的会话 id —— 适配器没有 session_id，只能靠它关联。
#: 「最近活跃会话」的盒子（长度 1 的 list）：与上面同一个理由，必须是**进程内共享**的
#: 可变对象。用盒子而不是字符串全局，是因为字符串全局没法跨模块对象共享（赋值只会改一份）。
_LAST_ACTIVE_BOX: list = _SHARED["panel_last_active"]


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
    expired = [sid for sid, st in _STATE.items()
               if now - st.get("updated", 0.0) > _TTL_SECONDS]
    for sid in expired:
        _STATE.pop(sid, None)
    if len(_STATE) > _MAX_SESSIONS:
        oldest = sorted(_STATE.items(), key=lambda kv: kv[1].get("updated", 0.0))
        for sid, _ in oldest[: len(_STATE) - _MAX_SESSIONS]:
            _STATE.pop(sid, None)
    if _LAST_ACTIVE_BOX[0] not in _STATE:
        _LAST_ACTIVE_BOX[0] = ""


def _new_state_locked(sid: str, now: float) -> Dict[str, Any]:
    """建一个空的会话桶（锁内调用）。"""
    state: Dict[str, Any] = {
        "turn_id": "", "rounds": [], "current_round": None, "reasoning_len": 0,
        "tools": [], "started": now, "updated": now, "status": None,
        "closed": deque(maxlen=_MAX_CLOSED_TURNS),
    }
    _STATE[sid] = state
    return state


def _touch_locked(session_id: str, turn_id: str, now: float) -> Optional[Dict[str, Any]]:
    """取（或新建）会话状态；``turn_id`` 变化视为新回合，清空过程数据。

    **返回 ``None`` 表示这次事件属于一个已作废的旧回合，调用方必须原样丢弃** ——
    且在**任何状态写入之前**丢弃（``updated`` / ``_LAST_ACTIVE_BOX[0]`` 都不能被碰，否则迟到
    事件仍会刷新 TTL 与「最近活跃」路由）。

    为什么需要它：Hermes 给每个 ``(钩子名, 回调)`` 配一对独立的有界队列 + 独立守护线程
    （见 ``agent/plugin_stream_hooks.py``），**跨钩子没有顺序保证**。于是「t1 的推理增量
    还堵在队列里、t2 的 ``on_stream_start`` 先派发」是结构性的：新回合清空面板并把
    turn_id 改成 t2，随后迟到的 t1 事件又被判成「又换回合」，把 t2 的面板清掉、turn_id
    倒回 t1。把「被替换掉的那个 turn_id」立刻记进作废集，迟到事件就再也进不来。

    注意一处刻意的设计：登记**必须在替换分支里做**，不能只放在 :func:`begin_turn`。
    若 t2 的首个事件先于 t2 的 ``on_stream_start`` 到达（两条队列，属常态），
    替换是这里自己完成的，t1 从未经过 ``begin_turn``，集合会恒空、保护失效。
    """
    sid = str(session_id or "")
    if not sid:
        # 归属不明的数据不许进桶：空 session_id 会建成匿名桶，而 snapshot() 会把它
        # 当成一个正常会话选中 —— 于是别的卡片上会冒出无主的面板数据。
        return None
    state = _STATE.get(sid)
    if state is None:
        state = _new_state_locked(sid, now)
    closed = state.get("closed")
    if not isinstance(closed, deque):
        closed = state["closed"] = deque(maxlen=_MAX_CLOSED_TURNS)
    tid = str(turn_id or "")
    if tid:
        if tid in closed:
            # 迟到的旧回合事件（或旧回合的重复 start）：**一律丢弃**，且不写任何状态。
            #
            # ⚠️ 2026-09-12 踩过：曾经给 `begin_turn` 开了一条 `reopen=True` 的例外
            # （本意是「万一把 turn_id 复用，允许重新收养」）。但那个例外**绕过了本检查**
            # 并落进下面的替换分支 —— 于是被记进作废集的是**当前正在跑的回合**，
            # 一个迟到的 on_stream_start 就能让整回合面板永久黑屏（比原来的瞬态清空更糟）。
            #
            # `tid in closed` 只有两种可能：迟到的旧事件，或 id 被复用。两者无法区分，
            # 而前者是真实的生产故障、后者只在「给稳定 id 的测试替身」里出现。
            # 所以一律按迟到处理；作废集本身有上限（_MAX_CLOSED_TURNS），
            # 很久以前的 id 会被挤出去，复用场景能自愈。
            return None
        current = state.get("turn_id") or ""
        if current and tid != current:
            # 新回合：先把被替换的那个 turn_id 记为作废，再清空过程数据
            closed.append(current)
            state["rounds"] = []
            state["current_round"] = None
            state["reasoning_len"] = 0
            state["tools"] = []
            state["started"] = now
            # 新回合必须把上一回合的结局清掉 —— 否则新卡片会带着上一回合的颜色
            # （尤其「上一回合报错、这一回合正常」时，一个红边会一直挂着）。
            state["status"] = None
        state["turn_id"] = tid
    state["updated"] = now
    _LAST_ACTIVE_BOX[0] = sid
    return state


# --------------------------------------------------------------------------- #
# 推理轮（rounds）——「一轮」的定义
# --------------------------------------------------------------------------- #
#: aiduPOP 的「轮次」定义被我们沿用：**一轮 = 一段连续的推理文本**，被
#: 「正文开始」或「工具调用」打断即结束。它**不是** API 调用次数（`iteration`），
#: 也**不是**工具轮次 —— 用错了标签会误导用户，所以这里按打断切段自算。
def _rounds_locked(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    rounds = state.get("rounds")
    if not isinstance(rounds, list):
        rounds = state["rounds"] = []
    return rounds


def _open_round_locked(state: Dict[str, Any], now: float) -> Dict[str, Any]:
    """取（或开一个）当前正在进行的推理轮。"""
    current = state.get("current_round")
    if not isinstance(current, dict):
        current = {"parts": [], "started": now, "elapsed_ms": None}
        state["current_round"] = current
        _rounds_locked(state).append(current)
    return current


def _finalize_round_locked(state: Dict[str, Any], now: float) -> None:
    """结束当前推理轮（正文开始 / 工具开始 / 换回合时调用）。

    纯空白的轮直接丢掉 —— 否则面板里会多出一个没有内容的「第 N 轮」标题。

    ⚠️ **丢轮必须把它的长度从 ``reasoning_len`` 里扣掉**。``record_reasoning`` 是逐片段
    加长度的，空轮（例如只有 ``"\\n"``、``"  "`` 的增量）也一样加过账；不扣就留下**幻影
    长度**，而 ``_compact_locked`` 唯一的判据就是这个账本 —— 后果是把**有内容的真实轮**
    比配置更早地整轮回收掉（面板过程信息凭空少一段，且不报错）。

    两处**已知的固有偏差**（不是 bug，别当 bug 修）：
      * 由工具打断的轮，耗时包含模型生成 tool-call 参数的那段时间（``pre_tool_call``
        是在 API 响应结束后才触发的）；由正文打断的轮则是精确的。
      * 跨钩子**没有顺序保证**（``pre_tool_call`` 与 ``on_stream_delta`` 是两套派发）：
        若工具回调被饿死整整一个工具执行时长，第 N、N+1 轮会被并成一整轮。概率很低，
        而且公开契约里没有任何跨钩时序可依赖 —— 无法缓解，只能知道。
    """
    current = state.get("current_round")
    if not isinstance(current, dict):
        return
    state["current_round"] = None
    rounds = _rounds_locked(state)
    parts = current.get("parts") or []
    if not "".join(parts).strip():
        state["reasoning_len"] = max(0, state.get("reasoning_len", 0) - len("".join(parts)))
        try:
            rounds.remove(current)
        except ValueError:
            # 当前不变量下不可达（``current_round`` 恒等于 ``rounds[-1]``）。留 debug 而不是
            # 静默 pass：将来不变量若破了，这是唯一的线索。
            logger.debug("larkdeck: 待丢弃的空轮不在 rounds 中（不变量可能已破）")
        return
    current["elapsed_ms"] = max(0, int((now - float(current.get("started") or now)) * 1000))
    # 只保留最近的若干轮
    while len(rounds) > _MAX_ROUNDS:
        dropped = rounds.pop(0)
        state["reasoning_len"] = max(
            0, state.get("reasoning_len", 0) - len("".join(dropped.get("parts") or [])))


def _compact_locked(state: Dict[str, Any]) -> None:
    """总长超上限时**从最早的轮开始回收**（保留最近的过程信息）。

    热路径必须是 O(1)：长度靠 ``reasoning_len`` 增量累加，只有超上限才做
    O(轮数) 的回收 —— 这个函数在 ``on_stream_delta`` 上**每个 token** 都会被调用。
    """
    if state.get("reasoning_len", 0) <= _MAX_REASONING_CHARS:
        return
    rounds = _rounds_locked(state)
    # ① 从**最早的轮**开始整轮丢弃，直到总量落回上限内（保留最近的过程信息）
    while len(rounds) > 1 and state["reasoning_len"] > _MAX_REASONING_CHARS:
        dropped = rounds.pop(0)
        state["reasoning_len"] -= len("".join(dropped.get("parts") or []))
    # ② 只剩一轮仍超长 → 截断这一轮自己（保留最早部分，与旧语义一致）
    if state["reasoning_len"] > _MAX_REASONING_CHARS and rounds:
        text = "".join(rounds[0].get("parts") or "")
        keep = text[: _MAX_REASONING_CHARS]
        rounds[0]["parts"] = [keep]
        state["reasoning_len"] = len(keep)


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
        # `default=` 也必须**有界**：`_shrink` 对非 JSON 类型原样返回，`default=str` 会把
        # 一个 5MB 的自定义对象整段字符串化（审计 A7 实测：这条路径 3053ms，且它**绕过**
        # `_shrink` 的封顶）⇒ 截到 200 字符，展示用绰绰有余。
        text = json.dumps(_shrink(args), ensure_ascii=False,
                          default=lambda _o: str(_o)[:200])
    except Exception:
        return ""
    # ⚠️ **先按 `_REDACT_SCAN_CHARS` 截一刀再脱敏**（审计 A7）：`_args_preview` 最终只展示
    #    `_ARGS_PREVIEW_CHARS`（80）个字符 ⇒ 对第 4096 个字符之后的文本做脱敏是**纯成本**。
    #    这不是优化洁癖：这是 **fail-closed** 钩子（回调慢会拖住整批工具执行），而实测
    #    1MB 文本上四条规则合计 436ms、其中 ENV 一条占 407ms ——「有界」这条纪律的判据是
    #    **常量级**，不能依赖「参数总是很小」这个没人写下的假设。
    #    安全性：脱敏后的前 80 字符才是会被展示的那一段，被砍掉的尾巴**从不展示**。
    text = redact_inline_secrets(text[: _REDACT_SCAN_CHARS])
    if len(text) > _ARGS_PREVIEW_CHARS:
        return text[: _ARGS_PREVIEW_CHARS] + "…"
    return text


#: 预览里的凭据脱敏（R11-C1）。**纯函数、无 I/O、有界、幂等** —— 这条纪律与
#: :func:`_args_preview` 同源：钩子是 **fail-closed** 的，回调慢会拖住整批工具执行。
#:
#: 为什么必须有：钩子拿到的是**原始**工具参数，卡片（群聊里人人可见）会原样印出来 ——
#: ``export TOKEN=…``、``Authorization: Bearer …``、``{"api_key": "…"}`` 都是真实出现过的形状。
#: 判据是「**键名以凭据词结尾**」而不是「值长得像随机串」：
#: 猜值会把正常内容涂掉，那比不脱敏更难查（本项目对「猜」的纪律见 ``docs/lessons.md``）。
#: ⚠️ **有意的保守**：键名里凭据词出现在**中间**的（如 ``password_hash``、``secret_sauce``）
#: **不脱敏**；好处是不会误伤 ``max_tokens`` / ``input_tokens`` / ``token_count`` 这类
#: 正常字段（它们的凭据词后面还跟着字母，被前瞻挡住了）。
#: 另把家目录前缀折叠成 ``~``：``/Users/<名字>/…`` 会泄露用户名与目录结构。
#: ⚠️ 脱敏**只作用于预览这一份展示文本**，不改动任何写回核心的数据。
_REDACT_VALUE = "***"
#: 键名**以凭据词结尾**才算 —— 这个性质由紧跟的 ``"`` 保证（不是靠前瞻：
#: 初版写过一个 ``(?![A-Za-z0-9_.\-])``，实测它是**死代码** —— 后面的 ``"`` 已经
#: 要求凭据词在键名末尾，前瞻一点作用都没有。所以 ``max_tokens`` / ``input_tokens`` /
#: ``token_count`` 不被误伤靠的是「结尾」这个约束本身）
#: 一次最多**扫多少个字符**（见 :func:`redact_inline_secrets` 的「成本有界」一段）。
#: 4096 是「够覆盖被展示的那 80 字符」与「fail-closed 钩子必须廉价」之间的折中。
_REDACT_SCAN_CHARS = 4096

_REDACT_JSON_RE = re.compile(
    r'(?i)"([A-Za-z0-9_.\-]*?(?:token|secret|password|passwd|pwd|apikey|api[_-]?key|'
    r'access[_-]?key|client[_-]?secret|private[_-]?key|credential|cookie|authorization))'
    r'"\s*:\s*"((?:[^"\\]|\\.)*)"')
#: ``Bearer <token>``（含 ``Authorization: Bearer …``）
_REDACT_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-+/=]{8,}")
#: **头部形态**的凭据：``Cookie: …`` / ``Authorization: …`` / ``X-Api-Key: …``。
#: ⚠️ 这条是审计补的（**A2**）：`cookie` 原先只出现在**JSON 键名**规则里，
#: 而 `curl -H "Cookie: session=…"` 是第三种形状（引号里包着 `Key: value`），
#: 当时四条规则一条都不覆盖它 ⇒ **零脱敏**（这条形状是审计 A2 补的第五条规则）。
#: 同一批里 `Authorization: Bearer …` 是被涂掉的，
#: 所以格外容易误以为「头都覆盖了」。
_REDACT_HEADER_RE = re.compile(
    r"(?i)((?:^|[\s\"'(,])(?:cookie|set-cookie|authorization|x-api-key|x-auth-token)"
    r"\s*:\s*)([^\"\\\n,;]+)")
#: shell 风格 ``TOKEN=abc``（同一个「键名结尾」判据；这里靠紧跟的 ``=``）。
#: ⚠️ 两处都是审计逼出来的（**A1**/**A7**）：
#:   ① 键前缀用 ``{0,32}`` **限长**并套原子组 —— 定长前缀不会在长词上逐位置回溯
#:      （原先 `[A-Za-z0-9_]*` 无界，1MB 文本上这条规则独占 **407ms**，占四条总成本的 93%）；
#:   ② 值类必须能吃**转义的引号/反斜杠** —— `_args_preview` 喂进来的是 `json.dumps` 的产物，
#:      命令里的 `KEY="值"` 到正则眼里是 `KEY=\"值\"`，而旧值类 `[^\s"',;]+` 撞上第一个 `"` 就收尾
#:      ⇒ 只涂掉那个反斜杠、**凭据原文完整露出**（A1 实测：`export OPENAI_API_KEY="sk-…"` 漏脱）。
_REDACT_ENV_RE = re.compile(
#: ⚠️ 前缀用 `{0,32}?`（**限长 + 非贪婪**）而**不是**原子组：原子组会让引擎无法回溯，
#: 于是 `GITHUB_TOKEN=` 这种「前缀把敏感词整个吃掉」的形状**一次都匹配不上**
#: （实测：改成原子组后连无引号的 `KEY=value` 都不脱敏 —— 修 bug 反而制造了更大的洞）。
#: 定长上界已经把回溯钳在 32 次以内，不需要原子性。
    r"(?i)(?<![A-Za-z0-9_])([A-Za-z0-9_]{0,32}?(?:token|secret|password|passwd|pwd|apikey|"
    r"api[_-]?key|access[_-]?key|client[_-]?secret|private[_-]?key|credential|cookie))"
    r"(?![A-Za-z0-9_])=((?:[^\\\s,;'\"]|\\.)*)")
#: 家目录前缀 → ``~``。
#: ⚠️ **必须有左边界**（A5）：旧写法 `/(?:Users|home)/…` 没有边界，会把 URL 里的
#: `/home/…` 当路径 ⇒ `https://example.com/home/dashboard?tab=1` 被**截断成**
#: `https://example.com~`（用户看到一个**不存在的 URL**，比不脱敏更难查）。
#: 同时**不再吃掉**用户名的下一级：只把 `/<用户>` 这一段换成 `~`，后面的路径原样保留。
_REDACT_HOME_RE = re.compile(r"(?:^|(?<=[\s\"'=(:,]))/(?:Users|home)/[^/\s\"']+")


def redact_inline_secrets(text: str) -> str:
    """把预览里**明显是凭据**的片段换成 ``***``（纯函数、有界、幂等）。

    **五条**规则各对应一类真实形状：JSON 键值 / **头部形态**（``Cookie:`` ``Authorization:`` …）/
    裸 ``Bearer`` / ``KEY=value`` / 家目录前缀。
    ⚠️ 这里写的是**五条**：`_REDACT_HEADER_RE` 是审计 A2 补的，而 docstring 一度还写着「四条」
    （README 的功能对照表也照抄了那个数）—— 「同一件事两处真相」的又一个小形态，数一遍就知道了。
    """
    if not text:
        return text
    text = _REDACT_JSON_RE.sub(
        lambda m: '"%s": "%s"' % (m.group(1), _REDACT_VALUE), text)
    text = _REDACT_HEADER_RE.sub(lambda m: m.group(1) + _REDACT_VALUE, text)
    text = _REDACT_BEARER_RE.sub(lambda m: m.group(1) + _REDACT_VALUE, text)
    text = _REDACT_ENV_RE.sub(lambda m: m.group(1) + "=" + _REDACT_VALUE, text)
    return _REDACT_HOME_RE.sub("~", text)


#: ``_shrink`` 的预算：每层最多看几个条目、最多下钻几层、单个字符串留多长。
_SHRINK_ITEMS = 8
_SHRINK_DEPTH = 3
_SHRINK_STR_CHARS = 120


def _shrink(value: Any, depth: int = 0) -> Any:
    """生成参数的**有界**副本，供预览序列化使用（有损，仅用于展示）。

    ⚠️ **标量封顶必须排在深度判断之前**。原实现先判深度、深度超限时对 ``str``/``bytes``
    原样返回，于是「深度刚好等于上限」的长字符串完全不封顶 ——
    ``{"edits":[{"old_str": 5MB, "new_str": 5MB}]}`` 实测 18.9ms，等于没修。

    同理切片用 ``islice``：``list(value)[:8]`` 会**先全量拷贝**再切片
    （2M 元素 list 实测 14.4ms），而 islice 是惰性的。
    """
    # ① 标量封顶与深度无关，永远先做
    if isinstance(value, str):
        return value[:_SHRINK_STR_CHARS] + ("…" if len(value) > _SHRINK_STR_CHARS else "")
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    # ② 只有容器才受深度限制
    if depth >= _SHRINK_DEPTH and isinstance(value, (dict, list, tuple, set, frozenset)):
        return "…"
    if isinstance(value, dict):
        out: Dict[Any, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _SHRINK_ITEMS:
                out["…"] = f"+{len(value) - _SHRINK_ITEMS}"
                break
            out[str(key)[:64]] = _shrink(item, depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(islice(value, _SHRINK_ITEMS))
        try:
            total = len(value)
        except TypeError:  # pragma: no cover - 无 len 的可迭代
            total = len(items)
        out_list = [_shrink(item, depth + 1) for item in items]
        if total > _SHRINK_ITEMS:
            out_list.append(f"+{total - _SHRINK_ITEMS}")
        return out_list
    return value


# --------------------------------------------------------------------------- #
# 采集（钩子回调 → 本模块）
# --------------------------------------------------------------------------- #
def _reset_answers_for_new_turn_locked(sid: str, tid: str) -> None:
    """换回合时丢掉旧正文桶（锁内调用）：新回合第一个 text delta 前不得回上一回合答案。"""
    if not sid or not tid:
        return
    item = _ANSWERS.get(sid)
    if isinstance(item, dict) and str(item.get("turn") or "") != tid:
        _ANSWERS.pop(sid, None)


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
        _reset_answers_for_new_turn_locked(sid, tid)
        _purge_locked(now)


def note_turn(session_id: str, turn_id: str) -> None:
    """``post_api_request`` 钩子回调的**轻量**用途：给会话记一次「这个回合在动」。

    为什么需要：非流式模式下 ``on_stream_start`` 完全不触发（它由流式 emitter 发），
    于是「新回合开始了」这个信号没有别的来源 —— 上一回合的``status``就永远清不掉，
    新卡片会挂着旧颜色。``post_api_request`` 每回合至少发一次，且**带 turn_id**，
    正好补上这个缺口（``turn_id`` 与流式/回合结束钩子同源，见 :func:`snapshot`）。

    它只做 :func:`_touch_locked`（换回合即清空 + 刷新活跃度），不写任何业务数据。
    """
    sid = str(session_id or "")
    if not sid:
        return
    now = _now()
    with _LOCK:
        _touch_locked(sid, str(turn_id or ""), now)
        _reset_answers_for_new_turn_locked(sid, str(turn_id or ""))
        _purge_locked(now)


def record_turn_end(session_id: str, turn_id: str, *, completed: bool = False,
                    failed: bool = False, interrupted: bool = False) -> None:
    """``on_session_end`` 钩子回调：记下本回合的结局（卡片状态色由它决定）。

    名字叫 session，实际**每回合触发一次**（``agent/turn_finalizer.py`` 的 ``finalize_turn``），
    载荷正好是官方对「完成 / 报错 / 中止」的权威判定。

    ⚠️ **判定优先级必须 ``interrupted > failed > completed``**：官方 ``completed`` 的表达式
    里**没有** ``interrupted``，所以「被中止但已产出部分正文」的回合会同时
    ``completed=True, interrupted=True``。先看 ``completed`` 就会把中止显示成完成（绿色）。
    官方源码：``finalize_turn`` 里 ``completed = final_response is not None and not failed and ...``。

    ⚠️ **绝不对 ``error`` 之类的字符串做分类** —— 载荷里没有 ``reason`` / ``cancelled`` 字段，
    中止与报错在字符串上不可区分（``docs/lessons.md`` 明令禁止）。

    三个标志全为 False 时不改状态（例如 ``max_iterations`` 之外的一些收尾路径、
    以及 ``/new`` 这类会话级收尾）—— 没有结论就保持中性，不猜。
    """
    if interrupted:
        status = STATUS_STOPPED
    elif failed:
        status = STATUS_ERROR
    elif completed:
        status = STATUS_OK
    else:
        return
    sid = str(session_id or "")
    if not sid:
        return
    now = _now()
    tid = str(turn_id or "")
    with _LOCK:
        state = _STATE.get(sid)
        if state is not None:
            current = str(state.get("turn_id") or "")
            if current and tid and tid != current:
                # 这次收尾属于**另一个回合**（更早的迟到收尾，或与我们无关的回合）。
                # ⚠️ **绝不能走 ``_touch_locked``**：它会把 tid 当成「新回合」，于是
                # 清空当前回合的面板数据并把 turn_id 倒回去 —— 一个迟到的旧收尾就能
                # 把正在跑的回合打空（2026-09-13 加测试时实测到这条）。
                # 丢弃时**不写任何状态**（``updated`` / ``_LAST_ACTIVE_BOX[0]`` 都不碰）。
                logger.debug("larkdeck: 丢弃属于另一个回合的收尾（当前 %s / 载荷 %s）",
                             current[:16], tid[:16])
                return
            state["status"] = status
            state["updated"] = now
        else:
            # 还没有这个会话的桶（非流式 / 纯文本回合：面板里没有任何过程数据）——
            # 建一个，这样卡片上仍能带上状态色。
            state = _new_state_locked(sid, now)
            state["turn_id"] = tid
            state["status"] = status
        _LAST_ACTIVE_BOX[0] = sid
        _purge_locked(now)


def mark_stopped(chat_id: str = "") -> str:
    """把某个 chat 的会话标成「已中止」；返回命中的 ``session_id``（没命中返回空串）。

    这条路径与 :func:`record_turn_end` 不重复，而且**必须由插件自己走**：
    ``/stop`` 会让 stream consumer 直接 return（"abandon rather than deliver stale deltas"），
    而 native 模式下 ``_abandon_native_stream`` 是空操作 —— **永远不会有收尾帧**。
    所以状态改完还得由适配器**主动重绘那张卡**，否则状态在内存里变了、卡片纹丝不动，
    而且不报任何错。归属用 :func:`_select_locked`，与渲染完全同一套。
    """
    now = _now()
    chat = str(chat_id or "").strip()
    with _LOCK:
        sid = ""
        if chat:
            bound = _CHAT_SESSION.get(chat)
            if bound and now - bound[1] <= _CHAT_SESSION_TTL:
                sid = str(bound[0])
        if sid:
            # ⚠️ 这里**不能**调 ``bound_session_id()``：它用的是同一把非重入
            # ``threading.Lock``，锁内调用直接死锁（审计实测 120s 超时）。
            # 绑定的会话桶还没建就**建一个** —— 中止必须落在正确的会话上，
            # 不能因为「这个会话暂时没数据」就把状态写到别的会话（那是错色）。
            state = _STATE.get(sid) or _new_state_locked(sid, now)
        else:
            # 没有绑定（新会话首回合等）→ 退回旧的「最近活跃」行为，与渲染同一套判据
            sid, state = _select_locked("", now)
        if state is None:
            return ""
        state["status"] = STATUS_STOPPED
        state["updated"] = now
        # 唯一的写入口里也要顺手淘汰：`mark_stopped` 会为「绑定但还没建桶」的会话建桶，
        # 不淘汰的话这些刚建的空桶会挤掉真实会话的槽位（审计实测：`_MAX_SESSIONS`
        # 按 `updated` 淘汰，刚建的空桶更新、掉的是有内容的老会话）。
        _purge_locked(now)
        return sid


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
        current = _open_round_locked(state, now)
        current["parts"].append(text)
        state["reasoning_len"] = state.get("reasoning_len", 0) + len(text)
        _compact_locked(state)
        _purge_locked(now)


def record_answer_delta(session_id: str, turn_id: str, delta: str = "") -> None:
    """``on_stream_delta``（**正文**增量）钩子回调 —— 两件事：结束推理轮 + 累积正文。

    ① 「正文开始」是推理轮的**结束信号**（轮次定义 = 被正文或工具打断）；
    ② R11-A7 起**还累积正文本身**（``delta``）：核心会把工具进度行合成进流式帧文本，
       要证明「帧尾那一段是核心加的」就必须有同一份正文可以比对（见 :func:`answer_state`
       与 ``core/adapter.py`` 顶部的「正文净化」说明）。面板本身仍然不渲染正文。

    ⚠️ 这里**刻意不调** ``_touch_locked``（所以不刷新 ``updated`` / ``_LAST_ACTIVE_BOX[0]``）：
    上面那条早退已经盖住了绝大多数正文 token，而剩下的每一次都要多写两个状态字段。
    代价只是「纯正文长回合不会续上 TTL 与最近活跃」—— 归属改成
    ``chat_id -> session_id`` 确定性绑定（:func:`bind_chat_session`）之后，这条代价
    已经不再影响渲染正确性。哪天真要改，先想清楚它对 fail-closed 热路径的影响。

    ⚠️ **正文累积只认「桶里当前那个回合」**（对称比较，同 ①）：比不上一律不入账 ——
    宁可少剥一次（那次进度行短暂可见），也不能拿别的回合的正文去证明这一帧。
    热路径代价是「一次 dict 取 + 一次 list append + 一次整数加」，量级低于已有的
    :func:`record_reasoning`（那条还要建轮、做压缩），可以接受。
    """
    now = _now()
    sid = str(session_id or "")
    if not sid:
        return
    # R11-A7：正文**要连文本一起入账**（单独的正文仓库，见 `note_answer_delta`）。
    # 放在面板桶的早退**之前**：面板桶是「有没有过程数据」的概念，而正文累积的用途是
    # 「证明帧尾那一段是核心加的」，与面板有没有内容无关（纯正文 + 工具回合的面板桶
    # 由工具事件建，但**工具之前的正文**不能丢，否则前缀判据一开始就断）。
    note_answer_delta(sid, turn_id, delta)
    with _LOCK:
        state = _STATE.get(sid)
        if not isinstance(state, dict) or not isinstance(state.get("current_round"), dict):
            return  # 没有正在进行的轮：这是热路径，立刻返回，不写任何状态
        tid = str(turn_id or "")
        # 只有**载荷回合与会话当前回合一致**时才切轮。
        # 旧写法 ``if tid and state["turn_id"] not in ("", tid)`` 只挡住了「载荷是旧 tid」，
        # 漏了「载荷有 tid、会话侧为空」这一支（两者不一致却是新回合的正文）—— 实测会拿
        # 新回合的正文去 finalize 上一回合残留的轮，耗时按上一回合的 started 算，
        # 面板上冒出「第 1 轮 · 600.0s」这种跨越回合的假轮。
        # 对称比较把「一边有一边没有」都判成无法归属：宁可少切一轮，也不要造假数据。
        if tid != str(state.get("turn_id") or ""):
            return
        _finalize_round_locked(state, now)
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
        # 工具调用也是推理轮的**结束信号**（轮次定义 = 被正文或工具打断）
        _finalize_round_locked(state, now)
        # R11-A7：打开「工具窗口」—— 核心从这一刻起**可能**把工具进度行叠进流式帧的尾部，
        # 而它只会在下一个正文增量到达时清掉那些行（公开语义推导，不读私有变量）。
        # 放在幂等闸门**之前**：窗口问的是「核心有没有可能叠了进度行」，重复投递答的也是「有」。
        note_tool_event_locked(str(session_id or ""), str(turn_id or ""))
        tools: List[Dict[str, Any]] = state["tools"]
        # ⚠️ **同一 `tool_call_id` 只记一次**（2026-09-14 真机根因的另一面）：同一进程里插件会被
        # 发现两次 ⇒ 钩子被订阅两遍 ⇒ 每个工具事件会被回调两次；状态共享之后就会在面板里
        # 出现**两行同样的工具**。判据用非空 `tool_call_id`（它是网关给这次调用的稳定标识），
        # 已经在跑的同名步骤直接跳过 —— 幂等，而不是「猜哪个是重复的」。
        _tcid = str(tool_call_id or "")
        if _tcid and any(str(item.get("id") or "") == _tcid for item in tools):
            _purge_locked(now)
            return
        tools.append({
            "id": _tcid,
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
def bind_chat_session(chat_id: str, session_id: str) -> None:
    """记下「这个 chat 属于哪个会话」（由 ``pre_gateway_dispatch`` 观察得到）。

    有了它，适配器渲染卡片时就能按自己的 ``chat_id`` **确定地**取到对应会话，
    而不是猜「最近活跃」—— 这是消掉多会话串台的关键。观察方只做一次只读查找，无副作用。
    """
    chat = str(chat_id or "").strip()
    sid = str(session_id or "").strip()
    if not chat or not sid:
        return
    now = _now()
    with _LOCK:
        _CHAT_SESSION[chat] = (sid, now)
        if len(_CHAT_SESSION) > _CHAT_SESSION_MAX:
            stale = sorted(_CHAT_SESSION.items(), key=lambda kv: kv[1][1])
            for key, _ in stale[: len(_CHAT_SESSION) - _CHAT_SESSION_MAX]:
                _CHAT_SESSION.pop(key, None)


def bound_session_id(chat_id: str) -> str:
    """这个 chat 已知属于哪个会话；没有绑定（或已过期）返回空串。"""
    chat = str(chat_id or "").strip()
    if not chat:
        return ""
    now = _now()
    with _LOCK:
        bound = _CHAT_SESSION.get(chat)
        if not bound:
            return ""
        if now - bound[1] > _CHAT_SESSION_TTL:
            _CHAT_SESSION.pop(chat, None)
            return ""
        return str(bound[0])


def snapshot(chat_id: str = "") -> Optional[Dict[str, Any]]:
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

    **注意区分**：钩子**之间**的 ``turn_id`` 是同一个值（``on_stream_start`` /
    ``on_stream_delta`` / ``on_session_end`` / ``post_api_request`` 读的都是
    ``agent._current_turn_id``，由 ``agent/turn_context.py`` 的 ``_bind_turn_identity``
    每回合设一次），所以**回合状态可以按 turn_id 与面板数据精确对齐** ——
    对不上的只有上面那个 consumer 自己生成的版本。

    卡片与会话的精确对应走 ``chat_id -> session_id`` 反查（:func:`bind_chat_session`，
    由 ``pre_gateway_dispatch`` 观察得到）。
    """
    now = _now()
    with _LOCK:
        _purge_locked(now)
        sid, state = _select_locked(chat_id, now)
        if state is None:
            return None
        rounds_raw = [dict(item) for item in (state.get("rounds") or [])]
        tools = [dict(item) for item in (state.get("tools") or [])]
        turn_id = state.get("turn_id", "")
        status = state.get("status")
        updated = state.get("updated", now)
    # 拼接放到锁外 —— 推理最长可达 _MAX_REASONING_CHARS，锁里做会拖慢
    # fail-closed 的 pre_tool_call 回调（见模块 docstring 线程模型）。
    rounds: List[Dict[str, Any]] = []
    for item in rounds_raw:
        text = "".join(item.get("parts") or [])
        # 判空口径必须与 ``_finalize_round_locked`` 丢空轮的口径**一致**（都用 ``strip()``）。
        # 用真值判断时，一个尚未结束的纯空白轮会被渲染成空着身子的「第 1 轮」标题。
        if not text.strip():
            continue
        elapsed = item.get("elapsed_ms")
        if elapsed is None:
            # 还在进行中的轮：按「到现在为止」算，让用户看到实时耗时
            elapsed = max(0, int((now - float(item.get("started") or now)) * 1000))
        rounds.append({"text": text, "elapsed_ms": elapsed})
    reasoning = "".join(item["text"] for item in rounds)
    # 有结局（哪怕没有任何过程数据）也要出一份快照：状态色的**唯一载体**是面板，
    # 没有面板就没有颜色 —— 一个「无推理无工具但结束了」的回合也该是绿的。
    if not reasoning and not tools and not status:
        return None
    return {
        "session_id": sid,
        "turn_id": turn_id,
        "reasoning": reasoning,
        "rounds": rounds,
        "tools": tools,
        "status": status,
        "age": max(0.0, now - updated),
    }


def _select_locked(chat_id: str, now: float) -> "tuple[str, Optional[Dict[str, Any]]]":
    """挑出这次渲染该用哪个会话桶（锁内调用）。归属策略见 :func:`snapshot`。

    单独抽出来是因为 :func:`mark_stopped` 也要用**同一套**归属 —— 中止时若按别的方式
    挑会话，就会出现「状态改在了 A 会话、重绘的是 B 那条卡」这种静默错位。
    """
    # ① 优先用**确定性归属**：这个 chat_id 已知属于哪个会话（由 pre_gateway_dispatch
    #    观察得到）。**只要有绑定就认账**（哪怕那个桶暂时是空的）——
    #    这条 2026-09-13 被审计判为阻断项：原来要求「桶里有内容或有状态」才认，
    #    于是「绑定会话刚被换回合清空」时会跳到②/③，把**别的会话**的面板/颜色画到这张卡上，
    #    并把中止状态写到那个别的会话上。「这个会话暂时没东西」必须表现为
    #    「这张卡没有面板」，**不能**表现成「换一个会话」。
    if chat_id:
        bound = _CHAT_SESSION.get(str(chat_id).strip())
        if bound and now - bound[1] <= _CHAT_SESSION_TTL:
            # 绑定即认账：桶还没建（``None``）也认 —— 调用方据此**不渲染面板**。
            return str(bound[0]), _STATE.get(bound[0])
    # ② 没有绑定（新会话第一回合 / 老版本 Hermes / 查找失败）→ 退回旧行为：
    #    取「最近活跃会话」。**必须保住这条退路**，不能因为归属失败就不渲染面板。
    #    注意②③**只认有内容的桶**：这里没有归属信息可依，只有状态（没有过程数据）
    #    的桶是「另一个会话刚结束」的痕迹，选中它就会把别人的颜色画到这张卡上。
    sid = _LAST_ACTIVE_BOX[0]
    state = _STATE.get(sid) if sid else None
    if state is not None and _has_content(state):
        return sid, state
    # ③ 回退：找「最近更新且真的有内容」的会话
    candidates = [(key, st) for key, st in _STATE.items() if _has_content(st)]
    if not candidates:
        return "", None
    sid, state = max(candidates, key=lambda kv: kv[1].get("updated", 0.0))
    return sid, state


# --------------------------------------------------------------------------- #
# R11-A7：正文累积 —— 正文净化的**对照物**
# --------------------------------------------------------------------------- #
#: 为什么不用面板的会话桶：正文累积的生命周期与用途都不同（只服务「证明帧尾那一段是核心
#: 叠加的工具进度块」），塞进面板桶会连带影响回合切换、归属回退与「有内容」判据 ——
#: 那三样任何一处被带偏，症状都是**卡片渲染错**，而不是「少剥一次」。
def _purge_answers_locked(now: float) -> None:
    """淘汰过期/超量的正文累积（锁内调用）。全是 O(≤32)，可以随便调。"""
    expired = [sid for sid, item in _ANSWERS.items()
               if now - float(item.get("updated") or 0.0) > _ANSWER_TTL_SECONDS]
    for sid in expired:
        _ANSWERS.pop(sid, None)
    if len(_ANSWERS) > _ANSWER_MAX_SESSIONS:
        oldest = sorted(_ANSWERS.items(), key=lambda kv: kv[1].get("updated", 0.0))
        for sid, _ in oldest[: len(_ANSWERS) - _ANSWER_MAX_SESSIONS]:
            _ANSWERS.pop(sid, None)


def _answer_bucket_locked(sid: str, tid: str, now: float) -> Dict[str, Any]:
    """取（或按回合重建）某个会话的正文累积桶（锁内调用）。

    **换回合就重建，绝不沿用上一回合的正文**：一份「属于上一回合」的正文仍然可能是
    这一帧的前缀（两个回合开头一样时），而判据只比对前缀、看不出回合错位。
    """
    item = _ANSWERS.get(sid)
    if not isinstance(item, dict) or str(item.get("turn") or "") != tid:
        _ANSWER_GEN[0] = int(_ANSWER_GEN[0] or 0) + 1
        item = {"turn": tid, "gen": int(_ANSWER_GEN[0]), "parts": [], "len": 0,
                "complete": True, "tool_since_text": 0, "updated": now}
        _ANSWERS[sid] = item
        # 顺带把正文仓库自己的「最近活跃」指到它：没有绑定 `chat_id -> session_id` 的路径
        # （网关自己发的卡、探针卡）只能靠它。**不借面板那个盒子** —— 那个会被
        # `_purge_locked` 按「必须指向一个真面板桶」清空，而纯正文回合没有面板桶。
        _ANSWERS_LAST[0] = sid
    return item


def note_answer_delta(session_id: str, turn_id: str, delta: str) -> None:
    """``on_stream_delta(kind="text")``：把正文增量攒进本回合的对照物。

    三条纪律：
      * **归属 / 回合不明一律不入账**（空 ``session_id`` 或空 ``turn_id``）：错记一份正文的
        后果是**可能吞掉正文**（前缀判据看不出回合错位），而少记一次只是少剥一次
        （那一次进度行短暂可见，核心下一个正文增量到达时自己也会清掉）；
      * 正文增量同时**关闭工具窗口**（核心也在这一刻清掉它的进度行）；
      * 超过 ``_MAX_ANSWER_CHARS`` 就**冻结**并标记不完整（不是丢掉早期片段）——
        不完整就不许剥，理由见 :func:`answer_state`。
    """
    sid = str(session_id or "")
    tid = str(turn_id or "")
    if not sid or not tid:
        return
    text = str(delta) if delta else ""
    now = _now()
    with _LOCK:
        item = _answer_bucket_locked(sid, tid, now)
        if text and bool(item.get("complete", True)):
            total = int(item.get("len") or 0) + len(text)
            if total > _MAX_ANSWER_CHARS:
                item["complete"] = False       # 从这一刻起不再是完整正文 ⇒ 判据整体退回「不剥」
            else:
                item["parts"].append(text)
                item["len"] = total
        item["tool_since_text"] = 0
        item["updated"] = now
        _purge_answers_locked(now)


def note_tool_event_locked(sid: str, tid: str) -> None:
    """``pre_tool_call``：打开工具窗口（**锁内**调用 —— 调用方 :func:`record_tool_started`
    已经持有 ``_LOCK``，这里再取一次会死锁）。

    这一回合还没有正文入账时，窗口照样要开——核心可能已经在叠进度行了；但那第①帧是
    「没有正文的进度块」（核心合成式在 ``accumulated`` 为空时不留分隔符），证明不了，
    按原样渲染。**不留上一回合的正文**：换回合由 :func:`_answer_bucket_locked` 负责重建。
    """
    if not sid or not tid:
        return
    item = _answer_bucket_locked(sid, tid, _now())
    item["tool_since_text"] = int(item.get("tool_since_text") or 0) + 1
    _purge_answers_locked(_now())


def note_tool_event(session_id: str, turn_id: str) -> None:
    """``pre_tool_call`` 的公开入口（自己取锁）。"""
    with _LOCK:
        note_tool_event_locked(str(session_id or ""), str(turn_id or ""))


def _answer_session_for(chat_id: str) -> str:
    """正文累积用哪套归属：**确定性绑定优先**，拿不到退回「最近活跃」。

    为什么可以比面板的归属松：正文累积只当**前缀对照物**，归属错了只会比对不上、
    退化成「不剥」（fail-open）—— 不会画错东西，更不会吞正文。
    """
    chat = str(chat_id or "").strip()
    if chat:
        bound = _CHAT_SESSION.get(chat)
        if bound:
            return str(bound[0])
    return str(_ANSWERS_LAST[0] or "")


def answer_state(chat_id: str = "", *,
                 require_binding: bool = False) -> "tuple[str, bool, bool]":
    """**只读**给正文净化用的三件事实（R11-A7）：``(累积正文, 有工具窗口, 累积是否完整)``。

    ``require_binding=True`` 是 v0.7.0 own 模式的**严格归属**：只有
    ``chat_id -> session_id`` 存在且 TTL 未过期才返回累积；拿不到确定性绑定时
    返回 ``("", False, False)``，**绝不**退回 ``_ANSWERS_LAST``（那会跨会话串答案，
    2026-09-18 审计 B/C 均确认）。legacy 路径仍用默认 ``False`` 保持旧语义。

    为什么必须由面板层提供：钩子只知道 ``session_id``、卡片只知道 ``chat_id``，
    而把两者对上的那套归属正是本模块一直在维护的东西。**归属错了也不会剥错** ——
    比对不上前缀就退回「不剥」（见 ``core/adapter.py`` 的 :func:`_strip_core_progress`），
    所以这里可以放心复用，不必比面板的归属判据更严。

    三个返回值的用法（全部来自公开钩子语义，不读 Hermes 私有变量）：
      * 累积正文 = ``on_stream_delta(kind="text")`` 的逐条拼接（见 :func:`note_answer_delta`）；
      * 有工具窗口 = 「自上次正文增量以来有过 ``pre_tool_call``」（见 :func:`note_tool_event`）
        —— 与核心 ``_tool_progress_lines`` 的生命周期同形（有工具进度就 append、来正文就 clear）；
      * 是否完整 = 没到 ``_MAX_ANSWER_CHARS`` 而被冻结。**不完整就不许剥**：
        一份残缺的累积仍然可能是帧文本的前缀，拿它当证据就会把中间那段正文吞掉。
    """
    now = _now()
    with _LOCK:
        _purge_answers_locked(now)
        if require_binding:
            chat = str(chat_id or "").strip()
            sid = ""
            if chat:
                bound = _CHAT_SESSION.get(chat)
                if (isinstance(bound, (tuple, list)) and len(bound) >= 2
                        and now - float(bound[1]) <= _CHAT_SESSION_TTL):
                    sid = str(bound[0] or "")
        else:
            sid = _answer_session_for(chat_id)
        item = _ANSWERS.get(sid) if sid else None
        if not isinstance(item, dict):
            return "", False, False
        parts = list(item.get("parts") or [])
        armed = int(item.get("tool_since_text") or 0) > 0
        complete = bool(item.get("complete", True))
    # 拼接放到锁外：正文可以长到 _MAX_ANSWER_CHARS，锁里拼会拖慢 fail-closed 的
    # pre_tool_call 回调（同 snapshot 的推理拼接）。
    return "".join(parts), armed, complete


def answer_turn(chat_id: str = "", *, require_binding: bool = False) -> str:
    """严格绑定下该 chat 当前正文桶的 turn_id（无绑定/无桶返回空）。

    用途：own 模式校验「建卡时的 turn」是否仍与当前累积桶一致，防止同一会话
    切到新回合后旧卡消费新回合正文（审计 B 的绑定漂移补充场景）。
    """
    now = _now()
    with _LOCK:
        _purge_answers_locked(now)
        if require_binding:
            chat = str(chat_id or "").strip()
            bound = _CHAT_SESSION.get(chat) if chat else None
            sid = ""
            if (isinstance(bound, (tuple, list)) and len(bound) >= 2
                    and now - float(bound[1]) <= _CHAT_SESSION_TTL):
                sid = str(bound[0] or "")
        else:
            sid = _answer_session_for(chat_id)
        item = _ANSWERS.get(sid) if sid else None
        return str(item.get("turn") or "") if isinstance(item, dict) else ""


def answer_generation(chat_id: str = "", *, require_binding: bool = False) -> int:
    """严格绑定下该 chat 当前正文桶的世代号（无绑定/无桶返回 0）。

    世代号在每次「换 turn 或换会话重建正文桶」时递增，不依赖 hook turn 与
    consumer turn 的命名空间能否对齐 —— 这正是 own 模式主帧路径需要的漂移判据。
    """
    now = _now()
    with _LOCK:
        _purge_answers_locked(now)
        if require_binding:
            chat = str(chat_id or "").strip()
            bound = _CHAT_SESSION.get(chat) if chat else None
            sid = ""
            if (isinstance(bound, (tuple, list)) and len(bound) >= 2
                    and now - float(bound[1]) <= _CHAT_SESSION_TTL):
                sid = str(bound[0] or "")
        else:
            sid = _answer_session_for(chat_id)
        item = _ANSWERS.get(sid) if sid else None
        return int(item.get("gen") or 0) if isinstance(item, dict) else 0


def record_stream_end(session_id: str, turn_id: str = "", *, iteration: Any = 0,
                      final_text: Any = "", finished: bool = False,
                      error: Any = "") -> None:
    """``on_stream_end`` 观察回调：只保存对账快照，**绝不参与正文决策**。

    为什么只能做对账：Hermes 0.21.1 ``agent/chat_completion_helpers.py:2243-2259``
    每次 API 调用都发一次 ``on_stream_end``，``final_text`` 是**单次 response** 的
    content（多工具轮不是整回合文本），且仍走 1024 丢最旧队列。它比 core finalize
    更早/更晚都不可知，所以这里只记长度与前缀关系原料；渲染路径不读它。
    """
    sid = str(session_id or "")
    tid = str(turn_id or "")
    if not sid or not tid:
        return
    text = str(final_text) if final_text else ""
    if len(text) > _MAX_ANSWER_CHARS:
        text = text[:_MAX_ANSWER_CHARS]
        truncated = True
    else:
        truncated = False
    now = _now()
    item = {
        "iteration": _as_int(iteration) or 0,
        "finished": bool(finished),
        "error": str(error or ""),
        "len": len(text),
        "text": text,
        "truncated": truncated,
        "updated": now,
    }
    key = f"{sid}:{tid}"
    with _LOCK:
        _STREAM_ENDS[key] = item
        if len(_STREAM_ENDS) > _STREAM_END_MAX:
            oldest = sorted(_STREAM_ENDS.items(), key=lambda kv: kv[1].get("updated", 0.0))
            for stale, _ in oldest[: len(_STREAM_ENDS) - _STREAM_END_MAX]:
                _STREAM_ENDS.pop(stale, None)


def stream_end_snapshot(session_id: str, turn_id: str = "") -> Dict[str, Any]:
    """只读：取最近一次 ``on_stream_end`` 快照的浅拷贝（诊断/测试用）。"""
    key = f"{str(session_id or '')}:{str(turn_id or '')}"
    with _LOCK:
        item = _STREAM_ENDS.get(key)
        return dict(item) if isinstance(item, dict) else {}


def answer_stream_end_reconcile(chat_id: str = "") -> Dict[str, Any]:
    """只读对账：严格绑定会话的 own 累积与最近 ``on_stream_end.final_text`` 的前缀关系。

    返回字段只用于诊断/状态卡/测试：``has_end``、``finished``、``own_len``、
    ``final_len``、``own_prefix_of_final``、``final_prefix_of_own``、``missing_tail``。
    绝不返回或拼接成正文；不满足 ``own_prefix_of_final`` 时只告警。
    """
    result: Dict[str, Any] = {"has_end": False, "finished": False, "own_len": 0,
                              "final_len": 0, "own_prefix_of_final": False,
                              "final_prefix_of_own": False, "missing_tail": False}
    own, _armed, _complete = answer_state(chat_id, require_binding=True)
    result["own_len"] = len(own)
    with _LOCK:
        chat = str(chat_id or "").strip()
        bound = _CHAT_SESSION.get(chat) if chat else None
        sid = ""
        if (isinstance(bound, (tuple, list)) and len(bound) >= 2
                and _now() - float(bound[1]) <= _CHAT_SESSION_TTL):
            sid = str(bound[0] or "")
        if not sid:
            return result
        candidates = [item for key, item in _STREAM_ENDS.items()
                      if isinstance(item, dict) and key.startswith(sid + ":")]
        if not candidates:
            return result
        item = max(candidates, key=lambda value: value.get("updated", 0.0))
        final = str(item.get("text") or "")
        result.update(has_end=True, finished=bool(item.get("finished")),
                      final_len=int(item.get("len") or len(final)))
    if not final:
        return result
    result["own_prefix_of_final"] = final.startswith(own)
    result["final_prefix_of_own"] = own.startswith(final)
    result["missing_tail"] = bool(own and not result["own_prefix_of_final"]
                                  and not result["final_prefix_of_own"])
    return result


def answer_tools(chat_id: str = "") -> List[Dict[str, Any]]:
    """与 :func:`answer_state` **同一会话**的工具步骤快照（正文净化专用）。

    为什么不能直接用 :func:`snapshot`：`snapshot` 走面板的「最近活跃/绑定」选择，
    而正文净化拿累积正文走的是 :func:`answer_state` 的归属；两者在无绑定 / 绑定过期时
    可能选中不同会话。空累积的进度剥离需要「累积为空 + 有工具窗口 + 工具名单」三件事实
    来自同一会话 —— 用别的会话的工具名单去剥本会话的真实文本，会把模型正文当进度剥掉
    （2026-09-18 独立审计 A-P2）。

    取不到、会话为空或不一致一律返回 ``[]``（调用方据此 fail-open，绝不猜）。
    """
    now = _now()
    with _LOCK:
        _purge_answers_locked(now)
        sid = _answer_session_for(chat_id)
        state = _STATE.get(sid) if sid else None
        if not isinstance(state, dict):
            return []
        return [dict(item) for item in (state.get("tools") or []) if isinstance(item, dict)]


def diagnose(chat_id: str = "") -> Dict[str, Any]:
    """**只读诊断**：这次渲染会选中哪个会话桶、桶里到底有什么（不改任何状态）。

    为什么需要它：面板为空有**两种完全不同的原因**，而线上只看得到「空」这一个结果 ——
      * ① **绑定了另一个（空的）会话**（`_CHAT_SESSION[chat]` 指向的桶没数据）；
      * ② **桶被反复清空**（`_touch_locked` 把每次事件都判成「换了回合」⇒ rounds/tools 被清）。
    两种的修法完全不同，所以先分辨再动手（`docs/lessons.md`：先量再修）。
    """
    now = _now()
    with _LOCK:
        bound = _CHAT_SESSION.get(str(chat_id).strip()) if chat_id else None
        bound_sid = str(bound[0]) if bound else ""
        sid, state = _select_locked(chat_id, now)
        out = {
            "chat": str(chat_id),
            "bound_session": bound_sid,
            "bound_state_exists": bool(bound_sid and _STATE.get(bound_sid) is not None),
            "selected_session": str(sid or ""),
            "buckets": len(_STATE),
            "rounds": 0, "tools": 0, "reasoning_len": 0, "turn_id": "", "updated": 0.0,
        }
        if isinstance(state, dict):
            out["rounds"] = len(state.get("rounds") or [])
            out["tools"] = len(state.get("tools") or [])
            out["reasoning_len"] = int(state.get("reasoning_len") or 0)
            out["turn_id"] = str(state.get("turn_id") or "")
            out["updated"] = float(state.get("updated") or 0.0)
        return out


def _has_content(state: Dict[str, Any]) -> bool:
    """这个会话桶里有没有值得渲染的东西（工具步骤或非空推理轮）。

    与 :func:`snapshot` 的渲染口径保持一致（``strip()`` 判空），否则归属回退会选中一个
    「快照里其实什么都没有」的会话桶，把真正的面板挤掉。
    """
    if state.get("tools"):
        return True
    for item in state.get("rounds") or []:
        if isinstance(item, dict) and "".join(item.get("parts") or "").strip():
            return True
    return False


def reset() -> None:
    """清空全部面板数据（测试用）。"""
    with _LOCK:
        _STATE.clear()
        _CHAT_SESSION.clear()
        _ANSWERS.clear()
        _ANSWERS_LAST[0] = ""
        _ANSWER_GEN[0] = 0
        _STREAM_ENDS.clear()
        _LAST_ACTIVE_BOX[0] = ""


__all__ = [  # noqa: RUF022 - 按功能分组列出，便于对照文档
    "STATUS_OK", "STATUS_ERROR", "STATUS_STOPPED",
    "record_turn_end", "note_turn", "mark_stopped",
    "begin_turn",
    "bind_chat_session",
    "bound_session_id",
    "record_reasoning",
    "record_answer_delta",
    "record_stream_end",
    "stream_end_snapshot",
    "answer_turn",
    "answer_stream_end_reconcile",
    "record_tool_started",
    "record_tool_finished",
    "snapshot",
    "answer_tools",
    "reset",
]
