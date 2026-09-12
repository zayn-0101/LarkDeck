"""官方钩子订阅 —— 只订阅、不改写，仍然零源码改写。

larkdeck 是「平台插件 + 钩子订阅者」：``ctx.register_platform()`` 抢下 ``feishu``
平台名，``ctx.register_hook()`` 拿到运行时指标。两者挂在同一个注册上下文中，
都不需要动 Hermes 一个字节。

这里只订阅**只读观察型**钩子（返回值被忽略），不碰 ``pre_tool_call`` 这类
能改写行为的策略钩子 —— 卡片插件没有任何理由去干预 agent 的决策。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Tuple

from . import context as _context

logger = logging.getLogger("larkdeck.hooks")

#: 订阅清单：钩子名 -> 回调。全部是观察型，返回值被核心忽略。
SUBSCRIPTIONS: Tuple[Tuple[str, Callable[..., Any]], ...] = (
    # 每次 API 调用后触发：model / provider / base_url / usage{in,out}_tokens
    ("post_api_request", _context.record_api_call),
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
