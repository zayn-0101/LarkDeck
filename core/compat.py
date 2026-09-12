"""Hermes 版本 / 适配器能力探测 —— 所有兼容性判断集中在本文件。

设计原则：
  * 只做**只读**探测（getattr / importlib.metadata），绝不导入 Hermes 私有模块；
  * 探测失败一律「降级但继续」，并把结论交给调用方决定怎么告警；
  * 任何 Hermes 私有属性名只在本文件出现，方便升级时一处修。

为什么需要它：Mac 与 NAS 两处 Hermes 版本可能不同（NAS 是镜像内固定版本），
加上 Hermes 主体升级会移动内部实现，所以「内置适配器还认不认这些钩子」必须在
运行时问，而不是在代码里假定。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("larkdeck.compat")

# 内置 FeishuAdapter 上、LarkDeck 直接复用的私有/受保护接口。
# 任一缺失 = 覆盖层无法安全接管，必须显式告警而不是静默降级。
REQUIRED_ADAPTER_ATTRS: Tuple[str, ...] = (
    "_feishu_send_with_retry",       # 发送原语（带重试 / 限流处理）
    "_finalize_send_result",         # response -> SendResult
    "_run_blocking",                 # 把阻塞 SDK 调用丢到适配器线程池
)

# 缺少也不致命：有则用、无则退回内置实现。
OPTIONAL_ADAPTER_ATTRS: Tuple[str, ...] = (
    "edit_message",
)

# 卡片点击路径复用的内置适配器私有接口。缺了不致命（点击回落给内置实现），
# 但澄清按钮会静默失灵 —— 探测上报，不阻断卡片加载。
# 注意 ``_on_card_action_trigger`` 在列表内：它是本插件**覆盖并 super() 调用**的那个方法，
# 内置哪天改名/移除，点击链路就整条失效（且 super() 会抛），必须在同一处登记。
CALLBACK_ADAPTER_ATTRS: Tuple[str, ...] = (
    "_on_card_action_trigger",              # 点击入口（本插件覆盖它，并把非自己的点击交回）
    "_is_interactive_operator_authorized",  # 点击人是否获授权
    "_loop_accepts_callbacks",              # loop 是否已就绪
    "_get_cached_sender_name",              # open_id -> 显示名
    "_card_response",                       # 卡片回调响应构造
)

#: 实例属性（不是类属性），无法在类上静态探测：运行时缺失会抛 ``AttributeError``，
#: 由 ``_on_card_action_trigger`` 捕获并回落。登记在此只为把私有名集中到本文件，
#: 并把 probe_report 的输出修准 —— ``_client`` 是实例属性，早先误登记在类属性组里，
#: 导致 ``_has(cls, "_client")`` 恒为 False、探测结果长期误报。
CALLBACK_INSTANCE_ATTRS: Tuple[str, ...] = ("_loop", "_client")


def hermes_version() -> str:
    """尽力拿到 Hermes 版本号；拿不到返回 ``"unknown"``。"""
    for getter in (_version_metadata, _version_constants, _version_module):
        try:
            v = getter()
            if v:
                return str(v)
        except Exception:  # pragma: no cover - 纯兜底
            continue
    return "unknown"


def _version_metadata() -> Optional[str]:
    from importlib.metadata import version

    for dist in ("hermes-agent", "hermes_agent", "hermes"):
        try:
            return version(dist)
        except Exception:
            continue
    return None


def _version_constants() -> Optional[str]:
    import hermes_constants as hc  # type: ignore

    for attr in ("VERSION", "__version__", "HERMES_VERSION"):
        v = getattr(hc, attr, None)
        if v:
            return str(v)
    return None


def _version_module() -> Optional[str]:
    import importlib

    for mod_name in ("hermes_version", "version"):
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        for attr in ("VERSION", "__version__"):
            v = getattr(mod, attr, None)
            if v:
                return str(v)
    return None


def probe_adapter_class(cls: type) -> Tuple[bool, List[str]]:
    """检查 ``cls``（内置 FeishuAdapter）是否具备 LarkDeck 依赖的接口。

    返回 ``(ok, missing)``：``ok`` 为 False 表示必须放弃覆盖、让内置适配器原样工作。
    """
    missing = [name for name in REQUIRED_ADAPTER_ATTRS if not _has(cls, name)]
    return (not missing), missing


def probe_report(cls: Optional[type]) -> Dict[str, Any]:
    """可读的能力快照，供启动自检与 ``doctor`` 输出使用。"""
    report: Dict[str, Any] = {"hermes_version": hermes_version(), "adapter_class": None}
    if cls is None:
        report["ok"] = False
        report["error"] = "built-in FeishuAdapter class not reachable"
        return report
    ok, missing = probe_adapter_class(cls)
    report["adapter_class"] = f"{cls.__module__}.{cls.__qualname__}"
    report["ok"] = ok
    report["missing_required"] = missing
    report["missing_optional"] = [n for n in OPTIONAL_ADAPTER_ATTRS if not _has(cls, n)]
    report["missing_callback"] = [n for n in CALLBACK_ADAPTER_ATTRS if not _has(cls, n)]
    return report


# --------------------------------------------------------------------------- #
# 澄清网关（``tools.clarify_gateway``）—— 卡片点击与「其他」待答状态直接依赖。
# 私有名（_lock / _entries / entry.multi_select）只出现在本文件，升级时一处修。
# --------------------------------------------------------------------------- #
def clarify_multi_select(clarify_id: str) -> bool:
    """该澄清是否允许多选；网关不可用时保守返回 False（单选用卡片）。"""
    try:
        from tools import clarify_gateway as _cg

        with _cg._lock:
            entry = _cg._entries.get(str(clarify_id))
            return bool(getattr(entry, "multi_select", False))
    except Exception:
        return False


def clarify_mark_awaiting_text(clarify_id: str) -> bool:
    """把该澄清切成「等待文字输入」（点「其他」时用）；返回是否真的切成功。

    **「没抛异常」不等于成功** —— 该澄清未知时 Hermes 返回 False。
    """
    from tools.clarify_gateway import mark_awaiting_text

    return bool(mark_awaiting_text(str(clarify_id)))


def clarify_resolve_gateway_clarify(clarify_id: str, answer: str) -> bool:
    """提交澄清答案、解除网关阻塞；返回是否真的提交成功。

    ⚠️ 返回 False 表示这次点击**没有生效**（该澄清已被超时或文字回答消费掉，或重复点击）。
    调用方若把「没抛异常」当成功、把卡片换成「已答复」，就会出现
    「卡片说已收到、agent 仍阻塞在网关」—— 答案永久丢失，而且卡片已变成已解状态，
    用户连重试的机会都没有。

    这两处实现都是「锁内 dict 取写 + ``threading.Event.set()``」，不碰事件循环，
    所以可以同步调用并当场拿结果。
    """
    from tools.clarify_gateway import resolve_gateway_clarify

    return bool(resolve_gateway_clarify(str(clarify_id), str(answer)))


def hook_is_wired(name: str) -> bool:
    """问核心：这个钩子名当前真的有订阅者吗？

    为什么需要它：``ctx.register_hook()`` 对**未知钩子名只 warning 不抛**
    （Hermes ``hermes_cli/plugins.py`` 会拿名字对 ``VALID_HOOKS`` 校验），返回值也不是
    bool。所以「注册时没抛异常」根本不能证明钩子挂上了 —— 官方哪天改名，
    启动自检照样打印「钩子 xxx 已订阅」，而回调永不派发，页脚与面板静默变空。
    这里用核心自己的判定兜一层，让自检说的是实话。
    """
    try:
        from hermes_cli.lifecycle import has_hook

        return bool(has_hook(name))
    except Exception:
        return False


def _has(cls: type, name: str) -> bool:
    try:
        return hasattr(cls, name)
    except Exception:  # pragma: no cover
        return False
