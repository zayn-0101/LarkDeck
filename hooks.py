"""官方钩子订阅 —— 只订阅、不改写，仍然零源码改写。

larkdeck 是「平台插件 + 钩子订阅者」：``ctx.register_platform()`` 抢下 ``feishu``
平台名，``ctx.register_hook()`` 拿到运行时数据。两者挂在同一个注册上下文中，
都不需要动 Hermes 一个字节。

订阅分两类，纪律相同 —— **只写内存、异常自吞、返回值一律 None**：

* 观察型：``post_api_request``（页脚指标）、``on_stream_start``（回合边界）、
  ``on_stream_delta``（推理增量）；
* 工具生命周期：``pre_tool_call`` / ``post_tool_call``（面板里的工具步骤）。

``pre_tool_call`` 是 **fail-closed** 钩子 —— 核心会等它返回指令，回调卡住
会阻止工具执行。所以这个回调只做一次 dict 写入（:mod:`larkdeck.panel` 内
是微秒级的加锁写），绝不返回 directive、绝不 IO：哪怕 larkdeck 自己有 bug，
最坏情况也只是面板少一行，不可能拦住任何工具。

推理增量有个 Hermes 侧前置条件：``plugins.stream_reasoning_deltas`` 为 true
时核心才发 ``kind="reasoning"`` 的增量（默认 false，见 docs/metrics-and-hooks
与 README）；不开时订阅照常，只是面板里没有思考文本。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Tuple

from . import context as _context
from . import panel as _panel

logger = logging.getLogger("larkdeck.hooks")


def _on_stream_start(**payload: Any) -> None:
    """新回合开始：清掉该会话上一回合的面板残留。

    首帧卡片可能先于本回合的第一个推理 / 工具事件发出，没有这一步会把
    上一回合的推理带给新回复（本回合无面板事件时甚至永久残留）。
    ``on_stream_start`` 每次 API 尝试（含重试）都触发，靠 ``turn_id``
    比较保持幂等，见 :func:`larkdeck.panel.begin_turn`。
    """
    try:
        _panel.begin_turn(payload.get("session_id", ""), payload.get("turn_id", ""))
    except Exception:  # pragma: no cover - 防御性：钩子绝不能抛
        logger.debug("[larkdeck] on_stream_start 采集忽略了一次异常", exc_info=True)


def _on_stream_delta(**payload: Any) -> None:
    """流式增量：只有推理（``kind="reasoning"``）进面板，正文增量丢弃。

    走 Hermes 的专用队列派发（每回调 1024 深度、满了丢最旧），回调必须
    吃 ``**kwargs`` 全量载荷；核心热路径，禁一切阻塞操作。
    """
    try:
        if str(payload.get("kind") or "") != "reasoning":
            return
        _panel.record_reasoning(payload.get("session_id", ""),
                                payload.get("turn_id", ""),
                                payload.get("delta", ""))
    except Exception:  # pragma: no cover - 防御性：钩子绝不能抛
        logger.debug("[larkdeck] on_stream_delta 采集忽略了一次异常", exc_info=True)


def _on_pre_tool_call(**payload: Any) -> None:
    """工具开始（fail-closed 钩子）：登记一步 ``running``。

    **必须返回 None** —— 返回 ``{"action": ...}`` 会被核心当作拦截指令；
    本函数没有任何 return 值，异常也吞掉，确保只观察不干预。
    """
    try:
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
                                    tool_call_id=payload.get("tool_call_id", ""))
    except Exception:  # pragma: no cover - 防御性：钩子绝不能抛
        logger.debug("[larkdeck] post_tool_call 采集忽略了一次异常", exc_info=True)


#: 订阅清单：钩子名 -> 回调。全部只观察，不返回指令。
SUBSCRIPTIONS: Tuple[Tuple[str, Callable[..., Any]], ...] = (
    # 每次 API 调用后触发：model / provider / base_url / usage{in,out}_tokens
    ("post_api_request", _context.record_api_call),
    # 回合边界（清掉上一回合的面板残留；含重试，回调内幂等）
    ("on_stream_start", _on_stream_start),
    # 流式增量（仅取 kind="reasoning" 做面板）
    ("on_stream_delta", _on_stream_delta),
    # 工具生命周期（面板里的工具步骤；pre 为 fail-closed，回调极小）
    ("pre_tool_call", _on_pre_tool_call),
    ("post_tool_call", _on_post_tool_call),
)


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
        return {name: False for name, _ in SUBSCRIPTIONS}
    for name, callback in SUBSCRIPTIONS:
        try:
            register_hook(name, callback)
            result[name] = True
        except Exception as exc:
            result[name] = False
            logger.warning("[larkdeck] 订阅钩子 %s 失败: %s", name, exc)
    if result.get("post_api_request"):
        logger.debug("[larkdeck] 已订阅 post_api_request（模型 / 上下文用量）")
    return result


__all__ = ["register", "SUBSCRIPTIONS"]
