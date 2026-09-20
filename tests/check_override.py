"""对**真实 Hermes 安装**验证平台覆盖是否生效。

它做的事：
  1. 造一个临时 HERMES_HOME，把本仓库软链进 ``plugins/larkdeck``；
  2. 在临时 config.yaml 里启用 larkdeck；
  3. 用 Hermes 自己的插件加载器加载插件，然后问平台注册表：
     ``feishu`` 现在解析到谁的工厂？
  4. 真造一个适配器实例，检查它的类确实是 LarkDeck 子类。

跑法::

    python3 tests/check_override.py

为什么必须真机验证:「注册表最后写入者胜」是**运行时**行为，只有真跑一遍才能证明。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_INSTALL = Path.home() / ".hermes" / "hermes-agent"

_INNER = r'''
import sys
from pathlib import Path

INSTALL = Path(sys.argv[1])
sys.path.insert(0, str(INSTALL))

from hermes_cli.plugins import discover_plugins          # noqa: E402
from gateway.platform_registry import platform_registry  # noqa: E402
from gateway.config import PlatformConfig                # noqa: E402

discover_plugins()

entry = platform_registry.get("feishu")
if entry is None:
    print("FAIL: 注册表里没有 feishu 平台")
    raise SystemExit(2)

factory = entry.adapter_factory
module = getattr(factory, "__module__", "") or ""
qualname = getattr(factory, "__qualname__", "") or ""
print(f"resolved factory: {module}.{qualname}")

if "larkdeck" not in module:
    print(f"FAIL: feishu 平台未被 larkdeck 接管（factory 来自 {module!r}）")
    raise SystemExit(3)

adapter = factory(PlatformConfig(enabled=True, extra={}))
cls = type(adapter)
mro_names = [c.__name__ for c in cls.__mro__]
print(f"adapter class  : {cls.__module__}.{cls.__qualname__}")
print(f"MRO            : {' -> '.join(mro_names[:4])}")
print(f"REQUIRES_EDIT_FINALIZE = {getattr(adapter, 'REQUIRES_EDIT_FINALIZE', None)}")

# P3：standalone sender 必须在**无网关进程**里能初始化 SDK client；官方 __init__ 只把 `_client`
# 置 None，真机审计复现过「不补 client ⇒ adapter.send() 返回 Not connected ⇒ cron 文本投递整条丢」。
_standalone_problems: list = []
_compat_mod = (sys.modules.get("hermes_plugins.larkdeck.core.compat")
               or sys.modules.get("larkdeck.core.compat"))
if _compat_mod is None:
    _standalone_problems.append("找不到 larkdeck.core.compat，standalone client 初始化无法核对")
else:
    _standalone_client = _compat_mod.ensure_standalone_client(adapter)
    if _standalone_client is None or getattr(adapter, "_client", None) is None:
        _standalone_problems.append("standalone client 初始化失败（cron 文本投递会变成 "
                                    "SendResult(success=False, error='Not connected')）")
    else:
        print("standalone client init: ok")

problems = []
problems.extend(_standalone_problems)
if cls.__name__ != "LarkDeckFeishuAdapter":
    problems.append(f"适配器类名不是 LarkDeckFeishuAdapter，而是 {cls.__name__}")
if "LarkDeckMixin" not in mro_names:
    problems.append("MRO 里没有 LarkDeckMixin")
if mro_names.index("LarkDeckMixin") > mro_names.index("FeishuAdapter"):
    problems.append("LarkDeckMixin 的优先级低于 FeishuAdapter，覆盖不会生效")
if getattr(adapter, "REQUIRES_EDIT_FINALIZE", None) is not True:
    problems.append("REQUIRES_EDIT_FINALIZE 不是 True，末帧会另发新消息而不是原地封口")

# 覆盖面必须真的落到我们的实现上 —— 光看 MRO 顺序不够，绑定方法可能在构造期就被取走了。
#
# ⚠️ 名字**从 `compat` 的登记表派生**，不写死字面量：写死的话「把它从上表删掉」这种退化
# 没有任何门禁能看见（第七路审计实测：从硬编码元组里去掉 `_reactions_enabled`，四门禁全绿）。
# 现在唯一的单一事实来源是 `compat.REACTION_ADAPTER_ATTRS` / `SIGNAL_ADAPTER_ATTRS`，
# 动它会被 `test_units` 抓住，动这里的派生逻辑则会被下面「Mixin 必须实现登记的名字」抓住。
_compat_for_names = (sys.modules.get("hermes_plugins.larkdeck.core.compat")
                     or sys.modules.get("larkdeck.core.compat"))
ld_mixin = next((c for c in cls.__mro__ if c.__name__ == "LarkDeckMixin"), None)
if ld_mixin is None:
    problems.append("找不到 LarkDeckMixin")
else:
    _must_cover = ["send", "edit_message", "send_clarify", "_on_card_action_trigger"]
    if _compat_for_names is not None:
        _must_cover += list(_compat_for_names.SIGNAL_ADAPTER_ATTRS)
        _must_cover += list(_compat_for_names.REACTION_ADAPTER_ATTRS)
    else:
        problems.append("拿不到 compat 模块，无法派生覆盖清单")
    for name in _must_cover:
        own = ld_mixin.__dict__.get(name)
        if own is None:
            problems.append(f"LarkDeckMixin 没有实现 {name}（登记在契约表里，覆盖会静默失效）")
            continue
        resolved = getattr(adapter, name, None)
        if getattr(resolved, "__func__", resolved) is not own:
            problems.append(
                f"{name} 解析到的是 {getattr(resolved, '__qualname__', resolved)!r}，不是 larkdeck 的实现"
            )
    print(f"resolved overrides: {len(_must_cover)} 个（含 compat 派生的契约名）")
if not isinstance(getattr(adapter, "_ld_state", None), dict):
    problems.append("_ld_setup() 没跑，实例状态缺失")

# 配置桥接：plugins.entries.larkdeck.settings 里的键必须经 ctx.get_config 生效，
# 没写的键必须保持默认（这两条一起才能证明桥接"精确"而不是乱写一桶）。
if ld_mixin is None:
    problems.append("找不到 LarkDeckMixin，无法验证配置桥接")
else:
    ld_mod = sys.modules.get(getattr(ld_mixin, "__module__", ""))
    if ld_mod is None:
        problems.append("拿不到 larkdeck.core.adapter 模块对象，无法验证配置桥接")
    else:
        got_clarify = ld_mod._cfg("clarify_cards")
        got_style = ld_mod._cfg_raw("context_style")
        got_cards = ld_mod._cfg("cards")
        print(f"settings bridge: clarify_cards={got_clarify!r} context_style={got_style!r} cards={got_cards!r}")
        for _key, _expected in (("visual_engine", "legacy"), ("card_status_header", True),
                                ("show_reasoning", False)):
            if ld_mod._DEFAULTS.get(_key, object()) != _expected:
                problems.append(
                    f"v0.7.1 视觉键默认值错误：{_key}={ld_mod._DEFAULTS.get(_key)!r}（期望 {_expected!r}）")
        if got_clarify is not False:
            problems.append(f"ctx settings 未生效：clarify_cards={got_clarify!r}（期望 False）")
        if got_style != "bar":
            problems.append(f"ctx settings 未生效：context_style={got_style!r}（期望 'bar'）")
        if got_cards is not True:
            problems.append(f"未配置的键没有保持默认：cards={got_cards!r}（期望 True）")

# 中断信号（``/stop``、``/new``）的派发约定必须与核心一致 —— 这是用**核心自己的判据**
# 核对的，不是我们自己猜的：核心在 gateway/run_agent_cache.py 里
# ``getattr(type(adapter), "interrupt_session_activity", None)`` 取方法，再用
# ``agent.interrupt_compat._accepts_keyword(fn, "metadata")`` 决定带不带 metadata 调。
import inspect as _inspect                                  # noqa: E402

from agent.interrupt_compat import _accepts_keyword as _core_accepts_keyword  # noqa: E402

_own = getattr(ld_mixin, "interrupt_session_activity", None)
if _own is None:
    problems.append("LarkDeckMixin 没有覆盖 interrupt_session_activity，中止后卡片不会变色")
else:
    _resolved = getattr(cls, "interrupt_session_activity", None)
    if _resolved is not _own:
        problems.append(
            f"核心按 type(adapter) 取到的是 {getattr(_resolved, '__qualname__', _resolved)!r}，"
            "不是 larkdeck 的实现 —— 中止重绘永远不会被调用")
    if _inspect.iscoroutinefunction(_own) is False:
        problems.append("interrupt_session_activity 必须是 async（核心是 await 调用的）")
    _meta = _core_accepts_keyword(_own, "metadata")
    print(f"interrupt dispatch: ours={_resolved is _own} "
          f"async={_inspect.iscoroutinefunction(_own)} core_accepts_metadata={_meta}")
    if not _meta:
        problems.append("核心的 _accepts_keyword 认为我们的方法不接受 metadata（签名不对称）")

# P1b：核心是否仍按 `getattr(type(adapter), "interrupt_session_activity")` 查找。
# 这是**静态 best-effort** 证据；签名对拍是运行期证据，两者一起才能覆盖「改名」这一种失灵。
if ld_mod is None:
    problems.append("拿不到 larkdeck.core.adapter 模块，无法核对核心中断查找名")
else:
    _signal_lookup = ld_mod._compat.core_interrupt_lookup_ok()
    print(f"core interrupt lookup: {_signal_lookup!r}")
    if _signal_lookup is not True:
        problems.append(
            "核心源码里找不到 getattr(type(adapter), 'interrupt_session_activity') 的查找点"
            "（False=已改名/缺失，None=源码不可读）—— `/stop` 后卡片可能静默不变色")

# 订阅钩子清单的单一事实来源：compat.OBSERVED_HOOKS 必须与 hooks.SUBSCRIPTIONS 一一对应，
# 否则「文档说订阅了 N 个」与「真订阅了哪几个」会各说各话。
_hooks_mod = sys.modules.get("hermes_plugins.larkdeck.core.hooks") or sys.modules.get("larkdeck.core.hooks")
_compat_mod = sys.modules.get("hermes_plugins.larkdeck.core.compat") or sys.modules.get("larkdeck.core.compat")
if _hooks_mod is None or _compat_mod is None:
    problems.append("拿不到 hooks/compat 模块，无法核对钩子清单")
else:
    _subscribed = tuple(name for name, _ in _hooks_mod.SUBSCRIPTIONS)
    _declared = tuple(_compat_mod.OBSERVED_HOOKS)
    print(f"hooks: subscribed={list(_subscribed)}")
    if _subscribed != _declared:
        problems.append(f"hooks.SUBSCRIPTIONS 与 compat.OBSERVED_HOOKS 不一致："
                        f"{_subscribed!r} vs {_declared!r}")

# 能力探测报告的**形状**：上游改名时靠它报警（`_reactions_enabled` / `interrupt_session_activity`
# 缺了都是「静默失灵」——开关无声失效、/stop 后卡片不变色）。第七路审计实测：把
# `compat.probe_report()` 里的 `missing_reactions` 整个删掉、或把 adapter 里两条 WARNING 删掉，
# **四个门禁全绿** —— 也就是说「P3 修完之后再被改回去也没人管」。这里把形状钉住。
if _compat_mod is None:
    problems.append("拿不到 compat 模块，无法核对能力探测报告")
else:
    # 键清单**从 compat 派生**，这里不再手写第二份（第八路审计实测：手写那份被删一项，
    # 四个门禁全绿 —— 门禁自己的覆盖清单没人守）。
    _required_keys = tuple(_compat_mod.PROBE_REPORT_KEYS)
    _report = _compat_mod.probe_report(cls)
    _absent = [k for k in _required_keys if k not in _report]
    print(f"probe_report keys: {sorted(_report)}")
    if _absent:
        problems.append(f"probe_report 缺键 {_absent} —— 上游改名会在启动自检里静默漏报")
    if _report.get("missing_reactions") != []:
        problems.append(f"真适配器不该缺 reactions 契约，实得 {_report.get('missing_reactions')!r}")
    if _report.get("missing_signal") != []:
        problems.append(f"真适配器不该缺中断信号契约，实得 {_report.get('missing_signal')!r}")
    if _report.get("contract_violation"):
        problems.append(f"能力探测报告的契约没对齐：{_report['contract_violation']!r}")

    # ⚠️ 「真适配器返回空列表」这一条**没有判别力**：把 `probe_report` 改成
    # `report["missing_reactions"] = []`（探测彻底失效）它照样绿 —— 第七路审计实测到了。
    # 所以再拿一个**什么都没有**的类探一次：缺什么就必须如实报出什么。
    class _Bare:                                   # noqa: D401 - 故意什么都不实现
        pass

    _bare_report = _compat_mod.probe_report(_Bare)
    for _key, _contracts, _what in (
            ("missing_reactions", _compat_mod.REACTION_ADAPTER_ATTRS, "reactions 契约"),
            ("missing_signal", _compat_mod.SIGNAL_ADAPTER_ATTRS, "中断信号契约"),
            ("missing_callback", _compat_mod.CALLBACK_ADAPTER_ATTRS, "点击回调契约")):
        if not _contracts:
            problems.append(f"{_key} 对应的登记表是空的，探测等于没做")
        elif list(_bare_report.get(_key) or []) != list(_contracts):
            problems.append(f"空类应当缺全部{_what}：期望 {list(_contracts)}，"
                            f"实得 {_bare_report.get(_key)!r}（探测没真读登记表）")
    print(f"probe_report(bare) missing_reactions={_bare_report.get('missing_reactions')!r} "
          f"missing_signal={_bare_report.get('missing_signal')!r}")

# ⚠️ **平台 entry 必须整条保真**：`register_platform` 是**整条替换** entry，不合并 ⇒
# 少传一个字段不是「退回默认」而是**关掉一个能力**。第十二路审计实测漏过三个有害字段
# （`standalone_sender_fn` ⇒ cron/send_message 进程外投递报错；`max_message_length=8000`
# ⇒ 长回复不再分块；`apply_yaml_config_fn` ⇒ `feishu.allow_bots` YAML 静默失效）。
# 判据 = **内置注册函数真给的字段** vs **我们注册后 entry 上的字段**，逐字段比对。
# ⚠️ 不要用 `snapshot_registration` 的第二个返回值当「被替换的内置 entry」——
# 它是**延迟加载器**（`_Loader`），不是 entry（这条我第一版就写错了，门禁当场自曝）。
_CLS_MODULE = type(adapter).__mro__[1].__module__ if len(type(adapter).__mro__) > 1 else ""
_builtin_mod = (sys.modules.get("hermes_plugins.feishu_platform.adapter")
                or sys.modules.get(_CLS_MODULE))
if _builtin_mod is None or not hasattr(_builtin_mod, "register"):
    problems.append("找不到内置 feishu 的 register()（entry 保真检查跑不起来）")
else:
    _captured: dict = {}

    class _RecordingCtx:                      # 只记录，不动真注册表
        def register_platform(self, **kw):
            _captured.update(kw)
            return object()

    try:
        _builtin_mod.register(_RecordingCtx())
    except Exception as exc:
        problems.append(f"内置 register() 调不通，entry 保真检查失效：{exc!r}")
    _IDENTITY = {"name", "label", "adapter_factory", "check_fn", "source", "plugin_name"}
    #: P3 的有意覆盖：内置 standalone sender 只能发纯文本；我们换成经合并适配器的卡片 sender。
    #: 这个字段的判据不是「同一个函数」，而是「可调用 + 不是内置那一枚」。
    _OVERRIDABLE = {"standalone_sender_fn"}
    _live = platform_registry.get("feishu")
    if _live is None:
        problems.append("注册后拿不到 feishu entry")
    else:
        _missing, _checked = [], 0
        for _key, _want in _captured.items():
            if _key in _IDENTITY:
                continue
            _checked += 1
            _got = getattr(_live, _key, None)
            if _key in _OVERRIDABLE:
                if not callable(_got):
                    _missing.append(f"{_key}: 被覆盖成不可调用对象 {_got!r}")
                elif _got is _want:
                    _missing.append(f"{_key}: 应覆盖为卡片 sender，实际仍是内置对象")
                print(f"entry 字段覆盖：{_key} 已换成卡片 sender（可调用）")
                continue
            # ⚠️ 判据要分两类：**可调用对象按身份比**（必须是同一个函数），
            # 其余（列表/字符串/数字/布尔）按值比 —— 一律用 `is` 会把
            # `required_env=["FEISHU_APP_ID", …]` 这种等值列表误报成「丢了」。
            _same = (_got is _want) if (callable(_want) or callable(_got)) else (_got == _want)
            if not _same:
                _missing.append(f"{_key}: 内置={_want!r} → 我们={_got!r}")
        print(f"entry 字段保真：内置给了 {_checked} 个非身份字段，丢失 {len(_missing)} 个")
        if _missing:
            problems.append("平台 entry 丢字段（等于静默关掉能力）：" + "；".join(_missing))

# ⚠️ 启动自检那行日志要**自报传输**（`native 传输 cardkit|patch`）。这条断言放在这儿而不是单测里，
# 因为它要看的是**真加载器 + 真配置桥接**跑完之后的 `SELFCHECK` —— 那正是运维在日志里读到的那句
# 话。没有它，「默认翻了但没重启」「进程还在跑旧传输」这两件事都只能靠猜。
_admin_mod = sys.modules.get("hermes_plugins.larkdeck.core.adapter")
if _admin_mod is None:
    problems.append("拿不到 hermes_plugins.larkdeck.core.adapter（加载器命名空间变了？）")
else:
    _selfcheck = getattr(_admin_mod, "SELFCHECK", None) or {}
    _detail = str(_selfcheck.get("detail") or "")
    _declared = str((getattr(_admin_mod, "_DEFAULTS", None) or {}).get("native_transport"))
    print(f"启动自检 detail: {_detail}")
    if _selfcheck.get("ok") is not True:
        problems.append(f"启动自检没通过：ok={_selfcheck.get('ok')!r} detail={_detail!r}")
    # ⚠️ 断言「日志里的传输 == 这个进程**实际生效**的传输」，而**不是**与 `_DEFAULTS` 比：
    # 子进程会继承开发者的环境变量（`LARKDECK_NATIVE_TRANSPORT=patch` 之类），拿声明值比会
    # 得到一次**假红**（第十二路审计指出）。而「默认值到底是什么」由 `test_units.py` 的
    # `test_declared_defaults_are_an_explicit_decision` 集中钉住，这里管的是「自报是否如实」。
    _effective = _admin_mod.LarkDeckMixin._ld_transport()
    if f"native 传输 {_effective}" not in _detail:
        problems.append(f"启动自检没有如实自报传输：这个进程实际生效 {_effective!r}，"
                        f"但日志是 {_detail!r}")

# ⚠️ **工具行是否进正文**必须在真加载器里验：那条判据（`format_tool_event` → `None`）只有在
# 有真 Hermes 类型（`ToolCallChunk`）时才有意义，而单测是零 Hermes 依赖的。
# 没有这条断言，把覆盖删掉 / 无视配置一律转发父类，四门禁全绿 —— 而用户看到的正是
# 「正文区又滚起了 ⚙️ 工具行」（默认行为被静默改掉）。
try:
    from gateway.stream_events import ToolCallChunk
    _probe_event = ToolCallChunk(tool_name="terminal", preview="date", args={"command": "date"})
except Exception as _exc:      # noqa: BLE001
    problems.append(f"拿不到 ToolCallChunk，工具行开关这条断言失效：{_exc!r}")
else:
    # ⚠️ 要**实例**：`format_tool_event` 是普通方法（不是 classmethod），在类上调用会
    # 「missing 1 required positional argument: 'event'」（这一版就是这么红的）。
    try:
        from gateway.config import PlatformConfig
        _factory = getattr(platform_registry.get("feishu"), "adapter_factory", None)
        _inst = _factory(PlatformConfig(enabled=True, extra={})) if _factory else None
    except Exception as _exc:      # noqa: BLE001
        _inst = None
        problems.append(f"造不出适配器实例，工具行开关这条断言失效：{_exc!r}")
    _mix = _inst
    _saved_cfg = dict(getattr(_admin_mod, "_CONFIG", {}) or {})
    try:
        if _mix is None:
            raise SystemExit(5)
        _admin_mod.configure(progress_lines_in_body=False)
        _eaten = _mix.format_tool_event(_probe_event, mode="all", preview_max_len=40)
        if _eaten is not None:
            problems.append(f"默认必须**吃掉**核心的工具行（正文才干净），实得 {_eaten!r}")
        _admin_mod.configure(progress_lines_in_body=True)
        _shown = _mix.format_tool_event(_probe_event, mode="all", preview_max_len=40)
        if not _shown:
            problems.append("`progress_lines_in_body: true` 时必须原样转发父类（能看到工具行），"
                            f"实得 {_shown!r}")
    finally:
        _admin_mod._CONFIG.clear()
        _admin_mod._CONFIG.update(_saved_cfg)
# CardKit 的**请求构造器必须齐**（R7 审计的「仍未收口」第 3 条的另一面）：会话预览那两个模型
# 曾经与其它 CardKit 模型在**同一个 import 块**里 —— 老 SDK 缺它们会让**整条 CardKit 传输**
# 一起关掉（fail-open 回 patch，用户失去打字机），而他根本没开过预览。现在拆开单独 import，
# 这里用**真 SDK** 证明六个构造器都在（缺任何一个都会让这条红）。
# 变异：把 `settings_card=_settings_card` 从返回里删掉 / 把两个模型并回同一个 try。
_caretaker = sys.modules.get("hermes_plugins.larkdeck.core.adapter")
if _caretaker is None:
    problems.append("拿不到 hermes_plugins.larkdeck.core.adapter，CardKit 构造器这条断言失效")
else:
    _reqs = None
    try:
        _reqs = _caretaker.LarkDeckMixin._ld_ck_requests()
    except Exception as _exc:      # noqa: BLE001
        problems.append(f"`_ld_ck_requests()` 在真 SDK 上抛了：{_exc!r}")
    if _reqs is None:
        problems.append("真 SDK 上 `_ld_ck_requests()` 返回 None —— CardKit 传输整条不可用")
    else:
        _need = ("create_card", "send_entity", "reply_entity", "write_element",
                 "batch_update", "settings_card")
        _have = sorted(k for k in vars(_reqs))
        print(f"cardkit request builders: {_have}")
        _miss = [k for k in _need if k not in vars(_reqs)]
        if _miss:
            problems.append(f"CardKit 请求构造器缺 {_miss}（缺一个就少一种能力）：{_have}")
        else:
            # 真造一次 `settings` 请求：那两个模型没 import 进来时这里会拿到 None / AttributeError
            try:
                _req = _reqs.settings_card("ck_probe", '{"config":{}}', 7, "ld-ck_probe-s7")
            except Exception as _exc:      # noqa: BLE001
                problems.append(f"`settings_card` 造不出请求（预览会静默失效）：{_exc!r}")
            else:
                _name = type(_req).__name__
                print(f"settings request: {_name}")
                if "Settings" not in _name:
                    problems.append(f"`settings_card` 造出来的不是 SettingsCardRequest：{_name}")
# `/larkdeck` 插件命令（R9）—— 必须用**真加载器 + 真命令注册表**核对：单测只能证明处理器
# 本身写得出文本，证明不了「它真的注册进了核心、核心取得到」。判据是核心自己的公开 getter
# `get_plugin_command_handler`，不是我们自己记的标志位。
try:
    from hermes_cli.plugins import get_plugin_command_handler       # noqa: E402
except Exception as _exc:      # noqa: BLE001
    problems.append(f"拿不到插件命令注册表，`/larkdeck` 这条断言失效：{_exc!r}")
else:
    _cmd_handler = get_plugin_command_handler("larkdeck")
    if _cmd_handler is None:
        problems.append("`/larkdeck` 没注册进核心（get_plugin_command_handler 取到 None）"
                        "—— 用户敲了没反应，而且是静默的")
    else:
        def _run_cmd(_args):
            _out = _cmd_handler(_args)
            if _inspect.isawaitable(_out):
                import asyncio
                _out = asyncio.run(_out)
            return _out

        _cmd_text = _run_cmd("status")
        _first = str(_cmd_text).splitlines()[0] if _cmd_text else "<empty>"
        print(f"plugin command : {_first}")
        if not isinstance(_cmd_text, str) or not _cmd_text.strip():
            problems.append(f"`/larkdeck status` 没返回文本：{_cmd_text!r}")
        else:
            # 关键词取自**三条记录 + 传输自报**：少了任何一段，这张自检卡就答不了
            # 「插件在不在动」这个问题（而它存在的唯一理由就是回答这个）。
            for _need in ("传输", "入站心跳", "写卡", "聚合", "能力探测"):
                if _need not in _cmd_text:
                    problems.append(f"`/larkdeck status` 少了「{_need}」这一段：{_cmd_text!r}")
            if _effective not in _cmd_text:
                problems.append(f"`/larkdeck status` 没报出生效传输 {_effective!r}：{_cmd_text!r}")
            # ⚠️ **口径说明必须在卡上**（R9 审计中-4）：账本与页脚指标一样是**进程级全局**，
            # 多会话并发时那些数字里含**别的会话**。没有这句话，用户会拿别人的失败原因去查自己的卡
            # —— 而这一整条纪律（页脚）早就写在 README/AGENTS 里了，账本这条此前一个字都没有。
            if "进程级" not in _cmd_text:
                problems.append(f"`/larkdeck status` 没说清数字是进程级累计（含全部会话）："
                                f"{_cmd_text!r}")
        # P2 配置刷新：`config` 必须真能在核心派发的处理器里读到官方设置（不是单测替身）。
        # 这里只跑**只读**路径；聊天侧写入命令已按安全审计 B1 移除，不会有写动作。
        _cfg_text = str(_run_cmd("config"))
        for _need in ("生效配置", "context_style", "clarify_cards"):
            if _need not in _cfg_text:
                problems.append(f"`/larkdeck config` 少了「{_need}」：{_cfg_text!r}")
        _reload_text = str(_run_cmd("config reload"))
        if not any(_w in _reload_text for _w in ("已刷新", "无法刷新", "取消")):
            problems.append(f"`/larkdeck config reload` 没有给出可判定的结果：{_reload_text!r}")
        # 真加载器必须证明官方只读句柄真的被 register() 采集到了；并且不得再采集写句柄。
        _ctx_box = getattr(_caretaker, "PLUGIN_CTX", None) or {}
        if not callable(_ctx_box.get("get_config")):
            problems.append(f"register() 没有把官方 ctx.get_config 放进共享盒：{_ctx_box!r}")
        if "set_config" in _ctx_box:
            problems.append(f"共享盒里仍有 set_config（聊天侧写入路径没删干净）：{_ctx_box!r}")

        _help_text = _run_cmd("help")
        # ⚠️ 判据是「**排队这条提醒还在**」，不是某个字面短语（R9 审计低-1 之后改的）：
        # 旧文案「仅空闲态可用」在 **CLI / TUI 里不成立**（那边由 `cli.py::_run_plugin_slash_command`
        # / `tui_gateway/methods_tools.py` **直接调处理器**，没有忙碌概念），所以措辞改成
        # 「网关里生成期间会被排队 + CLI/TUI 可直接执行」。判据跟着**改语义、不改强弱**：
        # 它守的仍然是「用户必须知道生成中敲命令不会立刻回卡」这条事实。
        _queue_words = ("排队", "queued")
        if not any(_w in str(_help_text) for _w in _queue_words):
            problems.append(f"`/larkdeck help` 没写明「生成期间会被排队」：{_help_text!r}")
        if "CLI" not in str(_help_text):
            problems.append(f"`/larkdeck help` 没写明 CLI / TUI 里可直接执行"
                            f"（旧措辞「仅空闲态可用」会误导）：{_help_text!r}")
        # 不认识的参数必须**说清楚 + 给用法**，不许静默当成 status（那会让用户以为参数生效了）
        _unknown_text = str(_run_cmd("wat"))
        if "wat" not in _unknown_text or not any(_w in _unknown_text for _w in _queue_words):
            problems.append(f"`/larkdeck wat` 没有明确拒绝并给出用法：{_unknown_text!r}")


# ⚠️ **启动自检那句「命令已注册」必须与核心注册表一致**（R9 审计中-6）。
# 它读的是我们自己的标志位 `COMMAND["registered"]`，而「我们记的」与「核心注册表里真的有没有」
# 是**两件事**。审计指出：把它硬编码成 `True`，四个门禁全绿。
#
# 这条**分两半**，因为它们抓的是两种不同的病（第一版只做了前半，实测抓不住「硬编码」）：
#   ① **一致性**：日志说的与实际的核心注册表一致 —— 抓「注册真的失败了、日志却说成功」
#      （注册被拒 / 名字撞内置命令 / `register_command` 不存在）；
#   ② **判据的来源**：那句自报**必须读 `COMMAND`**（`COMMAND.get("registered")`），
#      不许写死一个字面量 —— 抓「把判据换成常量」。为什么非要做这半步：一致性断言是
#      **相对的**（我既不知道、也不该规定「这个环境里命令一定会注册成功」），
#      所以 `COMMAND` 一旦恒为真，它就没有任何可观测量了。来源断言把「自报必须来自
#      真实注册结果」这件事**直接钉在源码上** —— 硬编码形态当场红（变异 `R9-21`）。
#      这是本项目里少见的「断言源码形状」的场合，理由写在这里，别当成泛泛的洁癖。
_selfcheck_mod = sys.modules.get("hermes_plugins.larkdeck.core.adapter")
if _selfcheck_mod is None:
    problems.append("拿不到 adapter 模块，启动自检「命令已注册」这条对拍失效")
else:
    try:
        from hermes_cli.plugins import get_plugin_command_handler as _get_cmd   # noqa: E402
    except Exception as _exc:      # noqa: BLE001
        problems.append(f"拿不到插件命令注册表，启动自检「命令已注册」这条对拍失效：{_exc!r}")
    else:
        _detail_now = str((getattr(_selfcheck_mod, "SELFCHECK", None) or {}).get("detail") or "")
        _says_ok = "命令已注册" in _detail_now
        _says_no = "命令未注册" in _detail_now
        _really = _get_cmd("larkdeck") is not None
        print(f"启动自检的命令自报：detail 说 "
              f"{'已注册' if _says_ok else ('未注册' if _says_no else '？')}"
              f" · 核心注册表实际 {'有' if _really else '没有'}处理器")
        # ② 判据来源：自报必须读 `COMMAND.get("registered")`（源码里必须出现这个表达式）
        try:
            import inspect as _inspect                                     # noqa: E402
            _reg_src = _inspect.getsource(_selfcheck_mod.register)
        except Exception as _exc:      # noqa: BLE001
            problems.append(f"读不到 register() 的源码，自报判据来源这条断言失效：{_exc!r}")
        else:
            if 'COMMAND.get("registered")' not in _reg_src:
                problems.append("启动自检的命令注册状态**不是**从 `COMMAND.get(\"registered\")` "
                                "读出来的（被换成了字面量？）—— 那句自报会变成永远成立")
        if _says_ok == _says_no:

            problems.append(f"启动自检没有明确自报命令注册状态：{_detail_now!r}")
        elif _says_ok != _really:
            problems.append(f"启动自检的自报与核心注册表**不一致**：日志说「命令已注册」"
                            f"（= {_says_ok}），而 get_plugin_command_handler('larkdeck') "
                            f"{'有' if _really else '没有'}处理器 —— 运维会信错这句话")


if problems:
    for p in problems:
        print("FAIL:", p)
    raise SystemExit(4)

print("OVERRIDE OK")
'''


def main() -> int:
    install = Path(os.environ.get("HERMES_INSTALL_DIR") or DEFAULT_INSTALL)
    if not (install / "gateway" / "platform_registry.py").is_file():
        print(f"找不到 Hermes 安装目录：{install}")
        print("用 HERMES_INSTALL_DIR=<路径> 指定。")
        return 1

    python = install / "venv" / "bin" / "python3"
    if not python.is_file():
        python = Path(sys.executable)

    with tempfile.TemporaryDirectory(prefix="larkdeck-check-") as tmp:
        home = Path(tmp) / "home"
        (home / "plugins").mkdir(parents=True)
        # 软链仓库本身 —— 改代码立即生效，不需要重装。
        (home / "plugins" / "larkdeck").symlink_to(REPO, target_is_directory=True)
        (home / "config.yaml").write_text(
            "plugins:\n"
            "  enabled:\n"
            "    - larkdeck\n"
            "  entries:\n"
            "    larkdeck:\n"
            "      settings:\n"
            "        clarify_cards: false\n"
            "        context_style: bar\n",
            encoding="utf-8",
        )
        # 让软链里的包能被 import（父目录上 sys.path）。
        env = dict(os.environ)
        env["HERMES_HOME"] = str(home)
        env["PYTHONPATH"] = str(REPO.parent) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [str(python), "-c", _INNER, str(install)],
            env=env, cwd=str(install), capture_output=True, text=True,
        )
        print(proc.stdout.strip())
        if proc.stderr.strip():
            print("--- stderr ---")
            print(proc.stderr.strip()[-3000:])
        if proc.returncode != 0:
            print(f"\n退出码 {proc.returncode}")
            return proc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
