"""官方钩子订阅 —— 只订阅、不改写，仍然零源码改写。

larkdeck 是「平台插件 + 钩子订阅者」：``ctx.register_platform()`` 抢下 ``feishu``
平台名，``ctx.register_hook()`` 拿到运行时数据。两者挂在同一个注册上下文中，
都不需要动 Hermes 一个字节。

订阅分两类，纪律相同 —— **只写内存、异常自吞、返回值一律 None**：

* 观察型：``post_api_request``（页脚指标 + 回合活跃）、``on_stream_start``（回合边界）、
  ``on_stream_delta``（推理增量）、``on_session_end``（回合结局 → 卡片状态色）；
* 分发型观察：``pre_gateway_dispatch``（只读地观察 ``chat_id -> session_id`` 归属）；
* 工具生命周期：``pre_tool_call`` / ``post_tool_call``（面板里的工具步骤）。

``pre_tool_call`` 是 **fail-closed** 钩子 —— 核心会等它返回指令，回调卡住
**不只是面板少一行，而是工具被判 skip 并按 block 处理**（超时 30s + 60s 抑制窗口，
见 ``hermes_cli/plugins_dispatch.py``）。所以这个回调只做一次 dict 写入
（:mod:`larkdeck.core.panel` 内是微秒级的加锁写），绝不返回 directive、绝不 IO。
往这个回调里加任何阻塞物都是把后果从「少一行」升级成「拦工具」。

推理增量有个 Hermes 侧前置条件：``plugins.stream_reasoning_deltas`` 为 true
时核心才发 ``kind="reasoning"`` 的增量（默认 false，见 docs/metrics-and-hooks
与 README）；不开时订阅照常，只是面板里没有思考文本。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Tuple

from . import compat as _compat
from . import context as _context
from . import panel as _panel

logger = logging.getLogger("larkdeck.hooks")


def _on_stream_start(**payload: Any) -> None:
    """新回合开始：清掉该会话上一回合的面板残留。

    首帧卡片可能先于本回合的第一个推理 / 工具事件发出，没有这一步会把
    上一回合的推理带给新回复（本回合无面板事件时甚至永久残留）。
    ``on_stream_start`` 每次 API 尝试（含重试）都触发，靠 ``turn_id``
    比较保持幂等，见 :func:`larkdeck.core.panel.begin_turn`。
    """
    try:
        _panel.begin_turn(payload.get("session_id", ""), payload.get("turn_id", ""))
    except Exception:  # pragma: no cover - 防御性：钩子绝不能抛
        logger.debug("[larkdeck] on_stream_start 采集忽略了一次异常", exc_info=True)


def _on_stream_delta(**payload: Any) -> None:
    """流式增量：推理（``kind="reasoning"``）进面板，**正文只用来切轮**。

    正文本身不进面板（面板只收过程信息），但「正文开始」是推理轮的结束信号 ——
    轮次定义就是「一段连续推理，被正文或工具打断」。不接这个信号的话，整回合的
    推理会连成一个巨大的「第 1 轮」，轮次显示就没意义了。

    走 Hermes 的专用队列派发（每回调 1024 深度、满了丢最旧），回调必须
    吃 ``**kwargs`` 全量载荷；核心热路径，禁一切阻塞操作。
    """
    try:
        kind = str(payload.get("kind") or "")
        session_id = payload.get("session_id", "")
        turn_id = payload.get("turn_id", "")
        if kind == "reasoning":
            _panel.record_reasoning(session_id, turn_id, payload.get("delta", ""))
        elif kind == "text":
            # R11-A7：**正文增量要连文本一起入账** —— 正文净化靠「我们这份正文是不是帧文本的
            # 前缀」来**证明**帧尾那一段是核心叠加的工具进度块，所以这里必须传 `delta`。
            # 只报「来了正文」而不报内容，判据就退化成猜（8f81b4d 那个 P0 就是这么来的）。
            _panel.record_answer_delta(session_id, turn_id, payload.get("delta", ""))
    except Exception:  # pragma: no cover - 防御性：钩子绝不能抛
        logger.debug("[larkdeck] on_stream_delta 采集忽略了一次异常", exc_info=True)


def _on_stream_end(**payload: Any) -> None:
    """``on_stream_end``：每次 API 调用结束时的**只读对账**，绝不决定正文。

    载荷（v0.7.0 静态核实）：``final_text`` / ``finished`` / ``error`` +
    ``session_id`` / ``turn_id`` / ``iteration``。它不是整回合权威文本
    （``agent/chat_completion_helpers.py:2243-2259`` 每次调用都会发），
    且与 ``on_stream_delta`` 不在同一个 1024 队列上、先后不可知。
    因此只把前缀关系原料写进 :func:`panel.record_stream_end`；渲染路径不读它。
    """
    try:
        _panel.record_stream_end(
            payload.get("session_id", ""),
            payload.get("turn_id", ""),
            iteration=payload.get("iteration", 0),
            final_text=payload.get("final_text", ""),
            finished=bool(payload.get("finished")),
            error=payload.get("error", ""),
        )
    except Exception:  # pragma: no cover - 防御性：钩子绝不能抛
        logger.debug("[larkdeck] on_stream_end 对账忽略了一次异常", exc_info=True)


def _on_api_request(**payload: Any) -> None:
    """``post_api_request``：页脚指标 + **面板的「回合在动」信号**。

    后半句为什么必要：非流式模式下 ``on_stream_start`` 完全不触发（它由流式 emitter 发），
    「新回合开始」就没有别的来源 —— 上一回合的状态色会一直挂着（见 ``panel.note_turn``）。
    这个钩子每回合至少发一次，且**带 turn_id**，正好补上这个缺口。
    """
    try:
        _context.record_api_call(
            model=payload.get("model", ""),
            provider=payload.get("provider", ""),
            # ⚠️ base_url 必须透传：页脚的上下文上限按 `model@base_url` 解析
            # （`context.context_max()` → `get_model_context_length(model, base_url=...)`），
            # 缺了它探测就退化成「无 base_url、无 provider」的裸查表 —— 对不在硬编码表里、
            # 靠 provider 元数据解析窗口的模型（如 opencode-go 的 `deepseek-flash`）会落到
            # 家族兜底（`deepseek`: 128K），而真实窗口是 1M。2026-09-14 线上页脚即此症状。
            base_url=payload.get("base_url", ""),
            usage=payload.get("usage"),
            response_model=payload.get("response_model"),
        )
    except Exception:  # pragma: no cover - 防御性：钩子绝不能抛
        logger.debug("[larkdeck] post_api_request 指标采集忽略了一次异常", exc_info=True)
    try:
        _panel.note_turn(payload.get("session_id", ""), payload.get("turn_id", ""))
    except Exception:  # pragma: no cover - 防御性：钩子绝不能抛
        logger.debug("[larkdeck] post_api_request 回合信号忽略了一次异常", exc_info=True)


def _on_session_end(**payload: Any) -> None:
    """``on_session_end``：**每回合一次**的权威结局 → 卡片状态色。

    名字里的 session 是历史包袱：它由 ``agent/turn_finalizer.py`` 的 ``finalize_turn``
    在**每次** ``run_conversation()`` 结尾同步发出，载荷是
    ``completed`` / ``failed`` / ``interrupted`` / ``turn_exit_reason`` —— 这正是官方
    对「完成 / 报错 / 中止」的权威判定，比任何推断都可靠。

    判定优先级的坑（``interrupted > failed > completed``）与「不许按 error 字符串分类」
    都写在 :func:`larkdeck.core.panel.record_turn_end` 里 —— 那里是唯一的判据处。
    """
    try:
        _panel.record_turn_end(
            payload.get("session_id", ""),
            payload.get("turn_id", ""),
            completed=bool(payload.get("completed")),
            failed=bool(payload.get("failed")),
            interrupted=bool(payload.get("interrupted")),
        )
    except Exception:  # pragma: no cover - 防御性：钩子绝不能抛
        logger.debug("[larkdeck] on_session_end 采集忽略了一次异常", exc_info=True)


def _on_pre_gateway_dispatch(**payload: Any) -> None:
    """观察入站消息的**会话归属**（``chat_id -> session_id``）。

    为什么需要：钩子载荷只有 ``session_id``，而适配器渲染卡片时只有 ``chat_id``；
    两侧的 ``turn_id`` 还是两套不相干的命名空间（见 ``panel.snapshot`` 的警告块）。
    这个钩子恰好同时给出 ``event.source``（含 chat_id）与 ``session_store``，
    于是能把两者对上 —— 面板归属从此是**确定性**的，不再靠「最近活跃」猜。

    ⚠️ **必须恒返回 None**：这是个能干预分发的钩子（返回 ``{"action": "skip"}`` 会丢消息、
    ``"rewrite"`` 会改文本），我们只观察。它在 **auth 之前**、每条入站消息都会跑，
    所以只做「只读查一次 store + 写一次内存」，不做任何 I/O、不改任何东西。

    ⚠️ **心跳必须是回调的第一条语句**（R9 审计中-3，2026-09-14 修正）：以前它写在归属绑定
    **之后**、而且还在**同一个 try 块的最后一行**上。于是 `_compat.lookup_session_id` 或
    `_panel.bind_chat_session`（**另外两个模块**的代码）抛一次异常，就被同一个
    `except Exception` 连心跳一起吞掉 —— 审计 `work/xhb6.py`（真加载器 + 真钩子派发器）实测：
    「绑定正常 ⇒ `inbound_count=1`；绑定抛 ⇒ **0**」。而心跳存在的理由就是回答「消息到底到没到
    插件」，那一次归因会**指错方向**（README 的旧措辞正是「心跳不动 ⇒ 消息根本没到插件」）。
    `note_inbound()` 自己异常自吞、只加锁写一次内存，放第一行就不可能被兄弟调用的异常挡住。
    代价（**有意接受**）：`source` / `chat_id` 缺失的载荷现在也记一笔心跳 —— 那类消息**确实到了
    插件的钩子回调**，这正是心跳要回答的问题；「归属有没有绑上」是另一件事，它有自己更精确的
    信号（面板/状态色的归属，见 `panel.py`），不该混进这一条。
    """
    # 放在**第一行**（在 try 里、但在任何可能抛的逻辑之前）：位置本身就是这条纪律的实现，
    # 靠的不是「记得把归属绑定放在后面」这种会在下一次重构里失守的约定。
    try:
        _context.note_inbound()
        source = getattr(payload.get("event"), "source", None)
        store = payload.get("session_store")
        if source is None or store is None:
            return
        chat_id = str(getattr(source, "chat_id", "") or "")
        if not chat_id:
            return
        # 只读查找：绝不调 get_or_create_session（那会在 auth 之前给未授权发送者建会话）
        session_id = _compat.lookup_session_id(
            store, _compat.session_key_for_source(source, store))
        if session_id:
            _panel.bind_chat_session(chat_id, session_id)
    except Exception:  # pragma: no cover - 防御性：钩子绝不能抛
        logger.debug("[larkdeck] 会话归属观察忽略了一次异常", exc_info=True)


def _log_pre_tool_once(tool_name: str, session_id: str) -> None:
    """诊断限流：60 秒内只记一条，证明 pre_tool_call 真的触达了本插件。"""
    now = time.monotonic()
    if now - getattr(_log_pre_tool_once, "_at", 0.0) < 60.0:
        return
    _log_pre_tool_once._at = now  # type: ignore[attr-defined]
    logger.info("[larkdeck] pre_tool_call 钩子触达（%s · session=%s）",
                tool_name, session_id[:8])


def _on_pre_tool_call(**payload: Any) -> None:
    """工具开始（fail-closed 钩子）：登记一步 ``running``。

    **必须返回 None** —— 返回 ``{"action": ...}`` 会被核心当作拦截指令；
    本函数没有任何 return 值，异常也吞掉，确保只观察不干预。
    """
    try:
        _log_pre_tool_once(str(payload.get("tool_name") or ""),
                            str(payload.get("session_id") or ""))
        _panel.record_tool_started(payload.get("session_id", ""),
                                   payload.get("turn_id", ""),
                                   payload.get("tool_name", ""),
                                   payload.get("args"),
                                   payload.get("tool_call_id", ""))
    except Exception:  # pragma: no cover - 防御性：钩子绝不能抛
        logger.debug("[larkdeck] pre_tool_call 采集忽略了一次异常", exc_info=True)


def _on_post_tool_call(**payload: Any) -> None:
    """工具结束：把 ``running`` 那步更新成完成态（带耗时 / 状态）。"""
    try:
        _panel.record_tool_finished(payload.get("session_id", ""),
                                    payload.get("turn_id", ""),
                                    payload.get("tool_name", ""),
                                    status=str(payload.get("status") or "ok"),
                                    duration_ms=payload.get("duration_ms"),
                                    tool_call_id=payload.get("tool_call_id", ""),
                                    result=payload.get("result"),
                                    error_type=str(payload.get("error_type") or ""),
                                    error_message=str(payload.get("error_message") or ""))
    except Exception:  # pragma: no cover - 防御性：钩子绝不能抛
        logger.debug("[larkdeck] post_tool_call 采集忽略了一次异常", exc_info=True)


#: 订阅清单：钩子名 -> 回调。全部只观察，不返回指令。
#: ⚠️ 与 ``compat.OBSERVED_HOOKS`` 一一对应；P2a 之前不要在这里加钩子。
SUBSCRIPTIONS: Tuple[Tuple[str, Callable[..., Any]], ...] = (
    # 每次 API 调用后触发：model / provider / usage{in,out}_tokens + 回合活跃信号
    ("post_api_request", _on_api_request),
    # 回合边界（清掉上一回合的面板残留；含重试，回调内幂等）
    ("on_stream_start", _on_stream_start),
    # 流式增量（仅取 kind="reasoning" 做面板）
    ("on_stream_delta", _on_stream_delta),
    # 每次 API 调用结束时的只读对账观察（P2a：已并入 OBSERVED_HOOKS 契约）
    ("on_stream_end", _on_stream_end),
    # 工具生命周期（面板里的工具步骤；pre 为 fail-closed，回调极小）
    ("pre_tool_call", _on_pre_tool_call),
    ("post_tool_call", _on_post_tool_call),
    # 入站消息的会话归属（chat_id -> session_id）—— 让卡片能确定地找到自己的会话。
    # 这个钩子能干预分发，我们**只观察、恒返回 None**。
    ("pre_gateway_dispatch", _on_pre_gateway_dispatch),
    # 每回合一次的结局（完成 / 报错 / 中止）—— 卡片状态色的唯一来源
    ("on_session_end", _on_session_end),
)

#: v0.7.0 P2a：`on_stream_end` 已并入 `SUBSCRIPTIONS` / `compat.OBSERVED_HOOKS`；
#: 保留空元组只是避免旧调用点报 AttributeError，下一轮清理可删。
EXTRA_SUBSCRIPTIONS: Tuple[Tuple[str, Callable[..., Any]], ...] = ()


def register(ctx: Any) -> Dict[str, bool]:
    """把采集器挂到钩子上；返回 ``{钩子名: 是否成功}``。

    单个钩子注册失败不影响平台覆盖 —— 卡片照常工作，只是页脚少显示一两个字段，
    所以这里只记日志，不改变自检结论。
    """
    result: Dict[str, bool] = {}
    register_hook = getattr(ctx, "register_hook", None)
    if not callable(register_hook):
        logger.warning("[larkdeck] 当前 Hermes 未提供 ctx.register_hook()，"
                       "页脚的模型 / 上下文用量不可用（卡片功能不受影响）")
        return {name: False for name, _ in SUBSCRIPTIONS + EXTRA_SUBSCRIPTIONS}
    for name, callback in SUBSCRIPTIONS + EXTRA_SUBSCRIPTIONS:
        try:
            register_hook(name, callback)
        except Exception as exc:
            result[name] = False
            logger.warning("[larkdeck] 订阅钩子 %s 失败: %s", name, exc)
            continue
        # 「没抛异常」不等于挂上了：官方对未知钩子名只 warning（见 compat.hook_is_wired）。
        # 不复核的话，官方哪天改名，自检仍会打印「已订阅」，而回调永不派发，
        # 页脚 / 面板静默变空 —— 正是本项目最怕的静默失败。
        wired = _compat.hook_is_wired(name)
        result[name] = wired
        if not wired:
            logger.warning("[larkdeck] 钩子 %s 注册后仍未被核心认作已订阅"
                           "（官方可能改名了），相关页脚 / 面板数据会缺失", name)
    if result.get("post_api_request"):
        logger.debug("[larkdeck] 已订阅 post_api_request（模型 / 上下文用量）")
    return result


__all__ = ["register", "SUBSCRIPTIONS", "EXTRA_SUBSCRIPTIONS"]
