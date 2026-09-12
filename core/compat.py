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
    # 会话归属是「卡片能否确定属于哪个会话」的前提，缺了只是退回旧行为（不阻断卡片）
    report["session_attribution_ok"] = session_attribution_available()
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


# --------------------------------------------------------------------------- #
# 会话归属（``gateway/session.py``）—— 把「哪张卡」对上「哪个会话」所需的公开面。
#
# 为什么需要：钩子载荷只有 ``session_id``，而适配器渲染卡片时只有 ``chat_id``；
# 两侧的 ``turn_id`` 还是两套不相干的命名空间（见 panel.snapshot 的警告块）。
# ``pre_gateway_dispatch`` 恰好同时给出 ``event.source``（含 chat_id）与 ``session_store``，
# 于是可以算出 ``chat_id -> session_id`` 的确定性映射。
#
# 这些名字都是**公开名**（无下划线），但仍属 Hermes 内部耦合，按不变量 3 集中登记 + 探测上报。
# --------------------------------------------------------------------------- #
#: 归属契约用到的公开面（点分名，仅供文档与探测报告使用）。
SESSION_ATTRIBUTION_API: Tuple[str, ...] = (
    "gateway.session.build_session_key",
    "gateway.session.SessionStore.lookup_by_session_key",
    "gateway.session.SessionStore.peek_session_id",
    "gateway.platforms.event.MessageEvent.source",
    "gateway.session.SessionSource.chat_id",
)


def session_attribution_available() -> bool:
    """归属所需的公开面是否齐备；缺了就退回「最近活跃会话」的旧行为。"""
    try:
        from gateway.session import SessionStore, build_session_key

        return (callable(getattr(SessionStore, "lookup_by_session_key", None))
                and callable(getattr(SessionStore, "peek_session_id", None))
                and callable(build_session_key))
    except Exception:
        return False


def session_key_for_source(source: Any, store: Any) -> str:
    """按 store **自己的配置**算出 session_key（镜像它的私有实现，不调用私有方法）。

    ``SessionStore`` 内部是 ``build_session_key(source, group_sessions_per_user=...,
    thread_sessions_per_user=..., profile=...)``；这里读同样的**公开**配置属性再算一遍。
    默认配置下两者结果一致；算不出或算不中时返回空串，调用方据此放弃归属。
    """
    try:
        from gateway.session import build_session_key

        config = getattr(store, "config", None)
        return str(build_session_key(
            source,
            group_sessions_per_user=bool(getattr(config, "group_sessions_per_user", True)),
            thread_sessions_per_user=bool(getattr(config, "thread_sessions_per_user", False)),
        ) or "")
    except Exception:
        return ""


def lookup_session_id(store: Any, session_key: str) -> str:
    """``session_key -> session_id``；查不到返回空串。

    **只读**：优先用 ``peek_session_id``（拿锁读映射），退回 ``lookup_by_session_key``。
    **刻意不用 ``get_or_create_session``** —— 那个会在 auth 之前给未授权发送者创建会话，
    属于改变核心行为。
    """
    if not session_key:
        return ""
    for name in ("peek_session_id", "lookup_by_session_key"):
        method = getattr(store, name, None)
        if not callable(method):
            continue
        try:
            got = method(session_key)
        except Exception:
            continue
        if isinstance(got, str):
            return got
        sid = str(getattr(got, "session_id", "") or "")
        if sid:
            return sid
    return ""


def _has(cls: type, name: str) -> bool:
    try:
        return hasattr(cls, name)
    except Exception:  # pragma: no cover
        return False
