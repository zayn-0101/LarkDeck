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

import inspect
import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

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

#: **信号型**适配器契约：核心主动调我们（不是我们调核心），且是在 ``/stop``、``/new``
#: 这些「必须立刻把卡片改成中止态」的路径上。核心的取法是
#: ``getattr(type(adapter), name, None)``（见 ``gateway/run_agent_cache.py``），
#: 所以混入层定义它就能被调到。
#:
#: 缺了不致命（卡片照发），但**中止后卡片永远不变色** —— 因为 native 模式下中止会让
#: consumer 直接 return、**永远不会再有 finalize 帧**，没有任何别的地方会重绘那张卡。
SIGNAL_ADAPTER_ATTRS: Tuple[str, ...] = (
    "interrupt_session_activity",
)

#: **处理生命周期**用到的适配器私有方法。缺了不致命（`reactions` 开关会静默失灵），
#: 但必须探测上报 —— 2026-09-13 审计指出的问题：我们覆盖了它却**没登记**，
#: 上游哪天改名，启动自检不会报警、用户设的 `reactions: false` 也就静默失效
#: （违反不变量 3「私有名只允许出现在 compat.py」与不变量 4「不假设版本」）。
REACTION_ADAPTER_ATTRS: Tuple[str, ...] = (
    "_reactions_enabled",
)

#: **显示 chrome** 扩展点：父类把工具调用渲染成一行行 chrome 并交给我们，
#: `gateway/platforms/base.py` 的 docstring 明写「adapters without editing/rich text
#: override to None」⇒ 覆盖成 ``None`` 是**官方允许**的做法（不是 monkeypatch）。
#: 我们用它来**吃掉**核心的工具行：那些行会被并进**流式正文**，而同一份信息我们已经在
#: 卡片的「执行详情」面板里结构化地给了一遍（来自官方钩子）⇒ 两份重复、正文被污染。
#: 登记在这里是为了不变量 3：覆盖父类方法也算「用到上游的名字」，缺了要能探测上报。
DISPLAY_CHROME_ATTRS: Tuple[str, ...] = (
    "format_tool_event",
)

#: 本插件订阅的**观察型**钩子清单（与 ``core/hooks.py`` 一一对应）。
#: 这个元组存在的意义是让「订阅了几个」有单一事实来源：文档、门禁、自检都读它。
OBSERVED_HOOKS: Tuple[str, ...] = (
    "post_api_request",
    "on_stream_start",
    "on_stream_delta",
    "on_stream_end",
    "pre_tool_call",
    "post_tool_call",
    "pre_gateway_dispatch",
    "on_session_end",
)


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


#: 核心在 ``/stop``、``/new`` 路径上取我们覆盖方法的**查找名**。
#: 核心的取法（`gateway/run_agent_cache.py`）是
#: ``getattr(type(adapter), "interrupt_session_activity", None)`` ——
#: 「合并类有没有这个方法」探测不出**核心把查找名改了**，所以 P1b 用静态源码
#: best-effort 探测这个字面量；核心源码不可读时返回 ``None``（**未取证**），不猜。
CORE_INTERRUPT_LOOKUP = "interrupt_session_activity"
_CORE_INTERRUPT_LOOKUP_RE = re.compile(
    r"getattr\s*\(\s*type\s*\(\s*adapter\s*\)\s*,\s*['\"]interrupt_session_activity['\"]"
)


def _core_source_text() -> Optional[str]:
    """只读地取核心 ``gateway/run_agent_cache.py`` 的源码文本；取不到返回 ``None``。

    **不 import** 核心模块（遵守本文件头「只读探测，不导入 Hermes 私有模块」的原则）：
    从已经在 ``sys.modules`` 里的 ``gateway`` 包路径拼出文件位置再读文本。上游把模块
    搬走/改路径时返回 ``None``（未取证），由调用方决定怎么显示。
    """
    gateway_mod = sys.modules.get("gateway")
    paths = getattr(gateway_mod, "__path__", None)
    if not paths:
        return None
    try:
        path = os.path.join(next(iter(paths)), "run_agent_cache.py")
    except Exception:
        return None
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return None


def core_interrupt_lookup_ok() -> Optional[bool]:
    """核心源码里是否仍按 ``getattr(type(adapter), "interrupt_session_activity")`` 查找。

    返回 ``True`` / ``False`` / ``None``（不可读，未取证）。
    这是 **best-effort 静态探测**，不是运行期契约验证：它只能发现「核心改了查找名/换了
    文件写法」，不能证明核心真的会调到我们；真正的运行期证据由 `check_override.py` 的
    interrupt 派发核对提供。核心做等价重构（例如先把方法取到局部变量）时可能误报 False，
    因此 status 文案也必须标明这是静态证据。
    """
    source = _core_source_text()
    if source is None:
        return None
    return bool(_CORE_INTERRUPT_LOOKUP_RE.search(source))


#: :func:`probe_report` **必须**产出的键（契约本身）。这是唯一事实来源 ——
#: 门禁、启动自检的告警都从它派生，**不再各自抄一份字面量**。
#: 第八路审计实测：`check_override.py` 里那份手写的键清单被删掉一项，四个门禁全绿
#: （门禁自己的覆盖清单没人守）；`core/adapter.py` 的 `_log_probe_report` 里还有第三份。
PROBE_REPORT_KEYS: Tuple[str, ...] = (
    "hermes_version", "adapter_class", "ok", "missing_required", "missing_optional",
    "missing_callback", "missing_signal", "missing_reactions", "missing_display_chrome",
    "session_attribution_ok", "core_interrupt_lookup",
)


def probe_report(cls: Optional[type]) -> Dict[str, Any]:
    """可读的能力快照，供启动自检与 ``doctor`` 输出使用。

    返回前**自检契约**：少一个 :data:`PROBE_REPORT_KEYS` 里的键就在报告里加一条
    ``contract_violation``（而不是静默少一个键 —— 「探测自己失效」与「契约齐全」
    必须能区分开，那正是这些键存在的意义）。刻意**不抛**：这个函数跑在插件注册路径上，
    抛出去就是「插件整体不生效」，比少上报一个键糟得多（不变量 2）。"""
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
    # 信号型契约：缺了只是「中止后卡片不变色」，但那是**静默**失灵，所以要上报
    report["missing_signal"] = [n for n in SIGNAL_ADAPTER_ATTRS if not _has(cls, n)]
    # 处理生命周期：缺了也只是「reactions 开关静默失灵」，同样要上报（不能只靠名字不变）
    report["missing_reactions"] = [n for n in REACTION_ADAPTER_ATTRS if not _has(cls, n)]
    # 显示 chrome：缺了不致命（工具行照旧并进正文），但用户要的「干净卡片」就静默失效了
    report["missing_display_chrome"] = [n for n in DISPLAY_CHROME_ATTRS if not _has(cls, n)]
    # 会话归属是「卡片能否确定属于哪个会话」的前提，缺了只是退回旧行为（不阻断卡片）
    report["session_attribution_ok"] = session_attribution_available()
    # P1b：核心中断查找名的静态 best-effort 探测；None = 源码不可读，未取证。
    report["core_interrupt_lookup"] = core_interrupt_lookup_ok()
    # 自检：契约里的键一个都不能少（改这个函数时忘同步 PROBE_REPORT_KEYS 就会被抓）
    absent = [key for key in PROBE_REPORT_KEYS if key not in report]
    if absent:
        report["contract_violation"] = absent
    return report


def accepts_keyword(fn: Any, name: str) -> bool:
    """``fn`` 是否接受关键字参数 ``name``（含 ``**kwargs``）。

    核心用同一套判据（``agent/interrupt_compat.py`` 的 ``_accepts_keyword``）决定
    怎么调用我们的覆盖方法：不认 ``metadata`` 就退回两参数形式。我们的覆盖方法要
    **原样转发**给 ``super()``，所以也得按同样规则问一次父类，否则老版本上会 TypeError。
    """
    try:
        parameters = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        p.kind is inspect.Parameter.VAR_KEYWORD
        or (p.name == name and p.kind is not inspect.Parameter.POSITIONAL_ONLY)
        for p in parameters
    )


def accepts_positional(fn: Any, count: int = 1) -> bool:
    """``fn`` 能否接受 ``count`` 个**位置**实参（跨版本调用核心私有方法的判据）。

    为什么不能只看关键字名：内置 ``_card_response(card_data=None)`` 的形参叫 `card_data`，
    别的版本完全可能叫 `card` —— 我们**位置传参**，形参叫什么无关紧要，真契约只有
    「能收几个位置实参」。签名取不到（C 函数 / 装饰器）时返回 True（乐观），
    调用点再兜一层 ``TypeError``（两处都有，见 ``_ld_card_response_safe``）。
    """
    try:
        parameters = list(inspect.signature(fn).parameters.values())
    except (TypeError, ValueError):
        return True
    if any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in parameters):
        return True
    slots = sum(1 for p in parameters
                if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                              inspect.Parameter.POSITIONAL_OR_KEYWORD))
    return slots >= int(count)


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


#: 澄清「文字回答」的判定结果（对应核心 ``tools.clarify_gateway`` 的 TEXT_* 常量）。
CLARIFY_TEXT_RESOLVED = "resolved"
CLARIFY_TEXT_NO_PENDING = "no_pending"
CLARIFY_TEXT_REJECTED_SELECTION = "rejected_selection"
CLARIFY_TEXT_REJECTED_PROSE = "rejected_prose"


def clarify_text_answer(clarify_id: str, text: str) -> str:
    """把**这一张卡自己的**澄清上的自由文本变成答案（用核心自己的解析规则）。

    为什么不让调用方退回到 ``attempt_text_response_for_session``（核心给「用户直接
    打字回复」用的那个入口）：它取的是该 session **最旧的**待答澄清，**完全不看卡片上的
    ``clarify_id``**。2026-09-13 审计实测：同一个 session 有两条待答时，在**新**卡的
    输入框里输入会被解到**旧**问题上，而回填卡显示的是新问题 —— 错误答案 + 假确认，
    两个方向都不报错。

    这里只针对 ``clarify_id`` 那一条：拿它的 entry 走核心的
    ``_coerce_text_response_detailed``（编号 / 标签匹配 / 多选解析 / 无效选择 vs 散文），
    **解析成功才提交**。核心没有这个内部函数（老版本）时返回 ``no_pending`` ——
    不猜、不提交（宁可让用户重试，也不要把答案写到别的问题上）。

    返回值与核心的 ``TEXT_*`` 同名，调用方据此决定是否回填卡片。
    """
    cid = str(clarify_id or "")
    body = str(text or "").strip()
    if not cid or not body:
        return CLARIFY_TEXT_NO_PENDING
    try:
        from tools import clarify_gateway as _cg

        with _cg._lock:
            entry = _cg._entries.get(cid)
            if entry is None or entry.event.is_set():
                return CLARIFY_TEXT_NO_PENDING
        coerce = getattr(_cg, "_coerce_text_response_detailed", None)
        if not callable(coerce):
            return CLARIFY_TEXT_NO_PENDING
        value, reason = coerce(entry, body)
        if value is None:
            return (CLARIFY_TEXT_REJECTED_SELECTION
                    if reason == "invalid_selection" else CLARIFY_TEXT_REJECTED_PROSE)
        return (CLARIFY_TEXT_RESOLVED
                if _cg.resolve_gateway_clarify(cid, value) else CLARIFY_TEXT_NO_PENDING)
    except Exception:
        return CLARIFY_TEXT_NO_PENDING


def manager_snapshot() -> Dict[str, Any]:
    """插件管理器的**只读快照**（R11-A1 的决定性实测）：身份 / home / 各钩子回调数。

    为什么需要它：真机实测「同一进程里插件被加载两遍」，而**哪一份是活的**、两遍是不是
    同一个 manager，只能在运行时量 —— 日志里的 `Plugin discovery complete` 出现两次只是
    间接迹象（别的插件也会打，而且极易淹没）。这个函数把三件事一次性变成数字：
      * ``manager``：管理器对象 id（**两个 id = 真的有两个 manager**，那就不是「重复加载」
        而是「按 home 分身」—— 修法完全不同）；
      * ``home``：管理器的 home 路径（审计给的判别信号之一：只差大小写的两种写法会分成两个 manager）；
      * ``hooks``：每个钩子名下的回调数（同一个 manager 里我们那 7 个钩子只该各有一份；
        若是两份，说明**真的重复订阅** —— 那与世代更替是两回事，别混为一谈）。

    纪律：**只读**（不注册、不注销、不触发发现），取不到就返回 ``{}`` —— 它是诊断，
    缺了不许影响任何行为（调用方只把它打进日志）。
    """
    out: Dict[str, Any] = {}
    try:
        from hermes_cli.plugins import get_plugin_manager

        manager = get_plugin_manager()
    except Exception:
        return out
    out["manager"] = id(manager)
    try:
        home = getattr(manager, "home_path", None)
        out["home"] = str(home) if home else ""
    except Exception:
        out["home"] = ""
    counts: Dict[str, int] = {}
    try:
        raw = getattr(manager, "_hooks", None)
        if isinstance(raw, dict):
            for hook_name, callbacks in raw.items():
                try:
                    counts[str(hook_name)] = len(callbacks)
                except TypeError:
                    continue
    except Exception:
        counts = {}
    out["hooks"] = counts
    return out


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

    ⚠️ **``profile`` 必须一起传**（2026-09-13 审计发现）：开了 ``multiplex_profiles``
    时 store 会用 ``_resolve_profile_for_key`` 给键加 profile 段，我们少传一个参数就会
    **永远算不中** ⇒ 绑定写不进去 ⇒ 面板整体静默退回「最近活跃」（不报错）。
    本机没开这个开关，所以是潜在坑而非现症。

    算不出或算不中时返回空串，调用方据此放弃归属。
    """
    try:
        from gateway.session import build_session_key

        config = getattr(store, "config", None)
        kwargs: Dict[str, Any] = {
            "group_sessions_per_user": bool(getattr(config, "group_sessions_per_user", True)),
            "thread_sessions_per_user": bool(getattr(config, "thread_sessions_per_user", False)),
        }
        profile = profile_for_source(store, source)
        if profile:
            kwargs["profile"] = profile
        return str(build_session_key(source, **kwargs) or "")
    except Exception:
        return ""


def profile_for_source(store: Any, source: Any) -> str:
    """多 profile（``multiplex_profiles``）时该 source 落在哪个 profile；否则空串。

    镜像核心 ``gateway/session_recovery.py`` 的 ``_resolve_profile_for_key``：读同样的
    公开配置属性自己算，不调用私有函数。任何异常都当作「没有 profile」。
    """
    try:
        config = getattr(store, "config", None)
        if not bool(getattr(config, "multiplex_profiles", False)):
            return ""
        resolver = getattr(store, "_resolve_profile_for_key", None)
        if callable(resolver):
            return str(resolver(source) or "")
        return ""
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


def ensure_standalone_client(adapter: Any) -> Any:
    """给 `standalone_sender_fn` 的合并适配器补上官方同款 SDK client。

    为什么必须在这里做：官方 `FeishuAdapter.__init__` 只把 ``_client`` 置 ``None``，
    真正建 client 的是它的私有 `_build_lark_client` / `_domain_name`（内置 standalone sender
    也自己走这两步）。这些名字按不变量 3 集中在本文件；拿不到 SDK / 建不出来就返回
    ``None``，由调用方回落内置 sender，绝不猜一个能用的 client。
    """
    try:
        existing = getattr(adapter, "_client", None)
    except Exception:
        existing = None
    if existing is not None:
        return existing
    # 官方 Feishu 模块把 SDK 绑定惰性加载进模块全局；不先跑它的 loader，
    # `_build_lark_client` 会在 `lark.Client` 上拿 None 抛 AttributeError（真机复现）。
    # `_load_lark_oapi` 是官方同步函数，名字按不变量 3 集中在这里。
    loader = None
    for cls in type(adapter).__mro__:
        module = sys.modules.get(getattr(cls, "__module__", ""))
        candidate = getattr(module, "_load_lark_oapi", None)
        if callable(candidate):
            loader = candidate
            break
    if loader is not None:
        try:
            if not loader():
                return None
        except Exception:
            logger.debug("[larkdeck] 加载 lark SDK 失败，将回落内置 sender", exc_info=True)
            return None
    builder = getattr(adapter, "_build_lark_client", None)
    if not callable(builder):
        return None
    try:
        from lark_oapi.core.const import FEISHU_DOMAIN, LARK_DOMAIN
    except Exception:
        return None
    domain = (LARK_DOMAIN if str(getattr(adapter, "_domain_name", "feishu")
                                 or "feishu").strip().lower() == "lark"
              else FEISHU_DOMAIN)
    try:
        client = builder(domain)
    except Exception:
        logger.debug("[larkdeck] 构造 standalone SDK client 失败，将回落内置 sender", exc_info=True)
        return None
    if client is None:
        return None
    try:
        setattr(adapter, "_client", client)
    except Exception:
        return None
    return client


def _has(cls: type, name: str) -> bool:
    try:
        return hasattr(cls, name)
    except Exception:  # pragma: no cover
        return False
