"""LarkDeck 单元测试 —— 零网络、零 Hermes 依赖。

跑法::

    python3 tests/test_units.py

覆盖五件事：
  1. ``build_adapter()`` 的「换 class」把戏真的成立（MRO 顺序、幂等、能力探测）；
  2. 卡片 JSON 结构合法、双语字段齐全、统一面板空则不渲染；
  3. 覆盖层的四条主路径在**失败时都回落**内置实现 —— 卡片是增强，不能弄丢消息；
  4. 指标层（上下文用量 / 页脚 / 模型别名）与溢出保护的每个边界；
  5. 面板数据层（推理累积 / 回合重置 / 工具配对 / TTL 与容量淘汰）。
"""

from __future__ import annotations

import asyncio
import itertools
from collections import deque
import json
import logging
import pathlib as _pathlib
import os
import random
import re
import sys
import threading
import time
import types
from typing import Any, Dict

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_PARENT = os.path.dirname(os.path.dirname(_HERE))  # .../code —— 使 `import larkdeck` 成立
if _REPO_PARENT not in sys.path:
    sys.path.insert(0, _REPO_PARENT)

from larkdeck.core import adapter, cards, compat, context, hooks, i18n, panel  # noqa: E402


#: 本仓库根目录（`.../larkdeck`，测试文件在它下面的 `tests/` 里）。
_REPO_ROOT = _pathlib.Path(__file__).resolve().parent.parent


def _gate_import_problems(loaded: Any, repo_root: _pathlib.Path) -> list:
    """门禁**先自证被测的就是这份代码**（R11-B2 审计高-1）：返回问题清单（空 = 合格）。

    为什么这条必要（不是洁癖）：这个脚本靠 `sys.path` 找 `larkdeck` —— 只要搜索路径上更靠前
    的地方有**另一份** `larkdeck`（`/tmp` 下前一次导出的镜像、别的拷贝、陈旧的
    `__pycache__`），门禁就会去测那份代码，而**四门禁照常全绿**。这不是推演：R11-B2 的
    审计实测过「把 `capacity_exceeded()` 整条改坏、在镜像目录里 184/184 全绿」；
    我自己在起一个临时拷贝做实验时也当场踩到（`import larkdeck` 解析到了 `/tmp` 下
    前一次审计的导出）。与 `mutate_check.py` 那条「快照目录必须叫 `larkdeck`」同源：
    都是同一种**二类假绿**（门禁跑了，但跑的不是这份代码）的入口。
    """
    got = _pathlib.Path(str(loaded)).resolve()
    if repo_root.resolve() in got.parents:
        return []
    return [f"`import larkdeck` 解析到 {got}，而本仓库是 {repo_root.resolve()}"
            " —— 先清掉搜索路径上的旧拷贝/镜像（同目录名的那份最容易被漏掉）"]


_problems = _gate_import_problems(adapter.__file__, _REPO_ROOT)
if _problems:
    raise SystemExit("❌ 门禁测的不是这份代码：" + "；".join(_problems))


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #
class _StubResult:
    def __init__(self, ok: bool = True, message_id: str = "om_1", error: Any = None) -> None:
        self.success = ok
        self.message_id = message_id
        self.error = error


class _FakeUpdate:
    def __call__(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return {"code": 0, "data": {"message_id": request["message_id"]}}


class _FakeClient:
    def __init__(self) -> None:
        message = types.SimpleNamespace(update=_FakeUpdate())
        self.im = types.SimpleNamespace(v1=types.SimpleNamespace(message=message))


class _StubConfig:
    """模拟 gateway.config.PlatformConfig 的最小面。"""

    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class StubAdapter:
    """模拟内置 FeishuAdapter 的最小可用面。"""

    REQUIRES_EDIT_FINALIZE = False

    def __init__(self, config: Any = None, *, fail_cards: bool = False) -> None:
        self.config = config
        self._client = _FakeClient()
        self._fail_cards = bool(getattr(config, "fail_cards", fail_cards))
        self.calls: list = []
        # ⚠️ 这里**有意不再预置**一个 `submitted` 列表：它原来只被初始化、没有任何写入点，
        # 于是「未授权用户的点击不得触发澄清解析」那条断言**恒真**（R8 审计中-4 实测：
        # 把鉴权拦截删掉四门禁全绿）。观测点现在由 `_fake_clarify_gateway(..., record=[...])`
        # 在**提交真的被发起**时写入 —— 恒真的空列表不算证据（`docs/lessons.md` 推论 8）。
        self.card_updates: list = []
        self._loop = object()

    # --- compat.REQUIRED_ADAPTER_ATTRS ---
    async def _feishu_send_with_retry(self, *, chat_id, msg_type, payload, reply_to, metadata):
        self.calls.append(("send", msg_type, payload))
        if self._fail_cards:
            return {"code": 99999, "msg": "card rejected"}
        return {"code": 0, "data": {"message_id": "om_card_1"}}

    def _finalize_send_result(self, response, default_message):
        mid = ((response or {}).get("data") or {}).get("message_id")
        if mid:
            return _StubResult(True, mid)
        return _StubResult(False, "", (response or {}).get("msg", default_message))

    async def _run_blocking(self, func, *args):
        return func(*args)

    # --- 被继承的行为（用于验证「回落」） ---
    async def send(self, chat_id, content, reply_to=None, metadata=None, **kw):
        self.calls.append(("SUPER.send", content))
        return _StubResult(True, "om_text_1")

    async def edit_message(self, chat_id, message_id, content, *, finalize=False):
        self.calls.append(("SUPER.edit", content, finalize))
        return _StubResult(True, message_id)

    async def send_clarify(self, chat_id, question, choices, clarify_id, session_key, metadata=None):
        self.calls.append(("SUPER.clarify",))
        return _StubResult(True, "om_text_clarify")

    def _on_card_action_trigger(self, data):
        self.calls.append(("SUPER.trigger",))
        return "SUPER_RESULT"

    # --- compat.SIGNAL_ADAPTER_ATTRS（核心在 /stop 路径调我们，我们再回落它） ---
    async def interrupt_session_activity(self, session_key, chat_id, metadata=None):
        self.calls.append(("SUPER.interrupt", session_key, chat_id, metadata))

    # --- 卡片点击路径依赖的辅助（内置适配器上有真实实现） ---
    def _is_interactive_operator_authorized(self, open_id):
        return open_id == "ou_ok"

    def _loop_accepts_callbacks(self, loop):
        return loop is not None

    def _get_cached_sender_name(self, open_id):
        return "汪老师"

    def _card_response(self, card=None):
        self.card_updates.append(card)
        return {"card": card} if card else {"toast": "ok"}


def _fake_clarify_gateway(resolved: list, *, commit: bool = True,
                          record: Any = None):
    """装一个假的 ``tools.clarify_gateway``。

    ``commit=False`` 模拟网关**拒绝**这次提交（重复点击 / 该澄清已被超时或文字回答
    消费掉）—— 真实实现就是返回 bool 的这个语义，见 Hermes
    ``tools/clarify_gateway.py`` 的 ``resolve_gateway_clarify``。

    ``record``（可选）是一个 list：**每一次「提交被发起」都往里记一条**。
    ⚠️ 这个参数是 R8 审计中-4 的直接产物：原来「未授权用户的点击不得触发澄清解析」
    那条断言盯的是 ``StubAdapter.submitted``，而全仓**只有初始化、没有任何写入点**
    ⇒ 那条断言**恒真**，把鉴权拦截删掉四门禁全绿（`docs/lessons.md` 推论 8 的
    「空集 = 一切正常」型）。现在观测点真的会记录，断言才有判别力。
    """
    fake_tools = types.ModuleType("tools")
    fake_cg = types.ModuleType("tools.clarify_gateway")
    fake_cg._lock = threading.Lock()
    fake_cg._entries = {}

    def _note(cid, what):
        if isinstance(record, list):
            record.append((cid, what))

    def _resolve(cid, resp):
        resolved.append((cid, resp))
        _note(cid, resp)
        return commit

    def _awaiting(cid):
        resolved.append((cid, "__await_text__"))
        _note(cid, "__await_text__")
        return commit

    fake_cg.resolve_gateway_clarify = _resolve
    fake_cg.mark_awaiting_text = _awaiting
    fake_tools.clarify_gateway = fake_cg
    sys.modules["tools"] = fake_tools
    sys.modules["tools.clarify_gateway"] = fake_cg
    return fake_tools, fake_cg


def _drop_fake_clarify_gateway():
    sys.modules.pop("tools", None)
    sys.modules.pop("tools.clarify_gateway", None)


def _run(coro):
    return asyncio.run(coro)


def _make(**cfg: Any):
    """按与真实路径完全相同的方式造一个「内置适配器 + 卡片层」实例。

    ⚠️ 这里**显式**把传输钉成 ``patch``：native 帧的行为是**随传输而变**的
    （``cardkit`` 走实体卡 + 元素写入，``patch`` 走整卡替换），所以涉及帧的测试必须
    **声明自己在测哪条**，不能隐式继承默认值 —— 否则以后翻默认时，一堆断言会以
    「突然红了但没人知道为什么」的形式一起爆掉（阶段 9 翻默认时实测过一次）。
    要测 cardkit：先 `_make()` 再 `adapter.configure(native_transport="cardkit")`。
    """
    cfg.setdefault("native_transport", "patch")
    obj = adapter.build_adapter(StubAdapter, _StubConfig(**cfg))
    # ⚠️ 光把 cfg 传给 `_StubConfig` 是**不生效**的（`build_adapter` 不读它来落配置）——
    # 这正是翻默认时那 10 条断言会红的原因之一。测试要走配置，只能经 `configure()`。
    if cfg:
        adapter.configure(**cfg)
    return obj


def _wire_patch(raw):
    """把卡片更新的 SDK 边界换成记录器（跳过真实 lark_oapi），返回请求列表。"""
    requests = []
    raw._ld_build_patch_request = lambda *, message_id, content: {
        "message_id": message_id, "content": content}
    raw._client.im.v1.message.patch = lambda request: (
        requests.append(request)
        or {"code": 0, "data": {"message_id": request["message_id"]}})
    return requests


# --------------------------------------------------------------------------- #
# 1. enrich / compat
# --------------------------------------------------------------------------- #
def test_merged_class_mro_and_flags():
    obj = _make()
    mro = type(obj).__mro__
    assert mro[0].__name__ == adapter.ADAPTER_CLASS_NAME, mro
    assert mro[1] is adapter.LarkDeckMixin, f"卡片层必须优先于内置类：{mro}"
    assert mro[2] is StubAdapter, mro
    assert obj.REQUIRES_EDIT_FINALIZE is True, "必须开启末帧编辑，否则会另发新消息"
    assert obj._ld_state == {}, "build_adapter 应初始化卡片追踪状态"


def test_merged_class_is_cached():
    assert adapter.merged_class(StubAdapter) is adapter.merged_class(StubAdapter), \
        "同一个内置类只应建一次混合子类"


def test_build_adapter_falls_back_when_interface_missing():
    class Half:
        def __init__(self, config=None):
            self.config = config

    obj = adapter.build_adapter(Half, _StubConfig())
    assert isinstance(obj, Half), "接口不全时必须原样返回内置适配器"
    assert not isinstance(obj, adapter.LarkDeckMixin), "接口不全时不得叠卡片层"


def compat_has_bilingual(key: str) -> bool:
    """i18n 表里两门语言都必须有该键，且**互不相同**（相同说明有人只写了一种语言）。"""
    entry = cards._i18n._STRINGS.get(key) or {}
    zh, en = entry.get(cards._i18n.ZH), entry.get(cards._i18n.EN)
    return bool(zh) and bool(en) and zh != en


def test_probe_adapter_class_reports_missing():
    ok, missing = compat.probe_adapter_class(StubAdapter)
    assert ok and missing == [], missing

    report = compat.probe_report(StubAdapter)
    assert report["missing_callback"] == [], "点击路径接口齐全时不应有回调缺口"
    assert report["adapter_class"].endswith("StubAdapter")

    class Missing:
        """只有一半接口的假适配器。"""

        async def _feishu_send_with_retry(self, **kwargs):  # pragma: no cover
            return None

    ok2, missing2 = compat.probe_adapter_class(Missing)
    assert not ok2 and "_run_blocking" in missing2, missing2
    assert "_card_response" in compat.probe_report(Missing)["missing_callback"]

    # ⚠️ 「真适配器返回空列表」这一条**没有判别力**（第七路审计实测：把 `probe_report` 里
    # `missing_reactions` 改成恒 `[]`，四个门禁全绿）。要证明探测**真的读了登记表**，
    # 就得拿一个什么都没有的类去探：缺什么就必须如实报出什么。
    class Bare:
        pass

    bare = compat.probe_report(Bare)
    assert compat.REACTION_ADAPTER_ATTRS, "登记表是空的，探测等于没做"
    assert compat.SIGNAL_ADAPTER_ATTRS, "登记表是空的，探测等于没做"
    try:
        assert list(bare["missing_reactions"]) == list(compat.REACTION_ADAPTER_ATTRS), bare
        assert list(bare["missing_signal"]) == list(compat.SIGNAL_ADAPTER_ATTRS), bare
        assert list(bare["missing_callback"]) == list(compat.CALLBACK_ADAPTER_ATTRS), bare
    except KeyError as exc:  # P5a2 变异：删掉报告键会让探测结果无法上卡
        raise AssertionError(f"探测报告必须包含完整契约键：{exc!r}; bare={bare!r}") from exc


def test_probe_report_warnings_name_the_right_contract():
    """`_log_probe_report` 的两条 WARNING 必须**逐条对得上**（第七路审计 P5）。

    实测旧覆盖：删掉 `report["missing_reactions"]`、删掉任一条 WARNING、甚至把 WARNING 的
    列表参数换成另一个（于是日志把「缺信号 ⇒ /stop 不变色」说成「reactions 失效」，
    **把排查方向带反**），四个门禁**全绿**。这两条 WARNING 修的是「上游改名 → 静默失灵」，
    所以它们自己绝不能是静默的。
    """
    def _report(**over):
        """造一份**完整**的报告（键从 `compat.PROBE_REPORT_KEYS` 派生，不手写第二份）。"""
        base = {key: [] for key in compat.PROBE_REPORT_KEYS}
        base["adapter_class"] = "x.Y"
        base["hermes_version"] = "test"
        base["ok"] = True
        base["session_attribution_ok"] = True
        base.update(over)
        return base

    # ① 两条都缺：必须两条 WARNING，且各自的列表**一一对应**（换参数就会错位）
    with _LogCapture("larkdeck") as records:
        adapter._log_probe_report(_report(missing_signal=["interrupt_session_activity"],
                                         missing_reactions=["_reactions_enabled"]))
    warnings = [r.getMessage() for r in records if r.levelno >= 30]
    signal_warns = [w for w in warnings if "中止色" in w]
    reaction_warns = [w for w in warnings if "reactions: false" in w]
    assert len(signal_warns) == 1 and len(reaction_warns) == 1, warnings
    assert "interrupt_session_activity" in signal_warns[0], signal_warns[0]
    assert "_reactions_enabled" in reaction_warns[0], reaction_warns[0]
    assert "interrupt_session_activity" not in reaction_warns[0], "两条的列表串了（误诊）"
    assert "_reactions_enabled" not in signal_warns[0], "两条的列表串了（误诊）"

    # ② 只缺 reactions：不许冒出「中止色」那条
    with _LogCapture("larkdeck") as records:
        adapter._log_probe_report(_report(missing_reactions=["_reactions_enabled"]))
    warnings = [r.getMessage() for r in records if r.levelno >= 30]
    assert len(warnings) == 1 and "reactions: false" in warnings[0], warnings

    # ③ 都不缺：一条 WARNING 都不能有（否则用户会去查一个不存在的缺口）
    with _LogCapture("larkdeck") as records:
        adapter._log_probe_report(_report())
    assert [r.getMessage() for r in records if r.levelno >= 30] == []

    # ④ 报告里**没有** `missing_reactions` 这个键时不许静默（上游改名/被误删）：
    #    缺键要当成「探测失效」报出来，而不是当成「一切正常」。
    # 契约键本身必须**包含**这六个（删掉任何一个都意味着某条静默失灵失去上报）。
    # 注意用 `pop(..., None)`：否则「契约少了这个键」会以 KeyError 的形式**崩**在测试里，
    # 而崩溃不算判别力证据（第九路审计 F3 指出过这种归因）。
    for required in ("hermes_version", "adapter_class", "ok", "missing_required",
                     "missing_optional", "missing_callback", "missing_signal",
                     "missing_reactions", "missing_display_chrome",
                     "session_attribution_ok"):
        assert required in compat.PROBE_REPORT_KEYS, (
            f"探测契约少了 {required} —— 上游改名后不会有任何上报")

    broken = _report()
    assert "missing_reactions" in broken, "契约键没进报告"
    broken.pop("missing_reactions")
    with _LogCapture("larkdeck") as records:
        adapter._log_probe_report(broken)
    warnings = [r.getMessage() for r in records if r.levelno >= 30]
    assert any("missing_reactions" in w for w in warnings), (
        f"报告缺键时必须点名报警，否则和「契约齐全」无法区分：{warnings}")

    # ⑤ `probe_report()` 自己要能发现「实现漏了一个契约键」：把某个赋值去掉时它会加
    #    `contract_violation`（而不是静默少一个键）。这里用真实函数验证这条自检存在。
    full = compat.probe_report(StubAdapter)
    assert "contract_violation" not in full, full
    assert set(compat.PROBE_REPORT_KEYS) <= set(full), (
        "probe_report 的产出必须覆盖契约键；缺了会由 contract_violation 报告出来")


def test_clarify_gateway_bridge_degrades_safely():
    # 无 Hermes 环境（没有 tools 模块）时保守返回 False，而不是抛异常。
    assert compat.clarify_multi_select("cid-x") is False
    # _client 是**实例**属性，登记在实例组里；早先误放进类属性组，导致探测恒报缺失。
    assert compat.CALLBACK_INSTANCE_ATTRS == ("_loop", "_client")
    # 覆盖并 super() 调用的点击入口必须在册，否则内置改名后整条点击链路静默失效
    assert "_on_card_action_trigger" in compat.CALLBACK_ADAPTER_ATTRS


# --------------------------------------------------------------------------- #
# 2. 卡片 JSON
# --------------------------------------------------------------------------- #
def _all_tags(node) -> list:
    found = []
    if isinstance(node, dict):
        if "tag" in node:
            found.append(node["tag"])
        for value in node.values():
            found.extend(_all_tags(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_all_tags(item))
    return found


def _find_collapsible(node):
    """找出卡片里的 ``collapsible_panel`` 节点（没有则 None）。"""
    if isinstance(node, dict):
        if node.get("tag") == "collapsible_panel":
            return node
        for value in node.values():
            found = _find_collapsible(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_collapsible(item)
            if found is not None:
                return found
    return None


def test_reply_card_shape():
    card = cards.reply_card("正文 **加粗**", streaming=True, footer="⏱ 3.2s")
    assert card["schema"] == "2.0"
    assert card["config"]["streaming_mode"] is True
    assert card["config"]["update_multi"] is True
    json.dumps(card, ensure_ascii=False)  # 必须可序列化
    tags = _all_tags(card)
    assert "markdown" in tags, tags
    assert "note" not in tags, f"2.0 卡不能用 note（飞书已废弃），应用 footnote: {tags}"


def test_streaming_flag_off_on_finalize():
    final = cards.reply_card("done", streaming=False)
    assert final["config"]["streaming_mode"] is False


def test_unified_panel_empty_is_none():
    assert cards.unified_panel() is None, "无过程信息时不应渲染空面板"
    panel = cards.unified_panel(reasoning="先看文件", tools=["read_file(a.py)"])
    assert panel is not None
    tags = _all_tags(panel)
    assert "collapsible_panel" in tags, tags
    assert panel["expanded"] is False, "默认收起，正文区保持干净"


def test_panel_header_title_must_be_plain_text():
    """面板头的 title 只能是 ``plain_text``。

    塞 ``lark_md`` / ``markdown`` 飞书**不报错**，只在客户端把面板降级成普通行：
    标题文字还在，三角箭头没了、内容全部铺开。真机实测（2026-09-12）才抓到，
    静态校验看不见，所以在这里钉死。
    """
    panel = cards.unified_panel(reasoning="想一下", tools=["read_file(a.py)"])
    header = panel["header"]
    assert header["title"]["tag"] == "plain_text", header
    assert header["vertical_align"] == "center", header
    # 箭头必须显式给 icon —— 不给就没有任何展开/收起控件（点不动，且不报错）
    assert header["icon"]["token"] == "down-small-ccm_outlined", header
    assert header["icon_position"] == "right", header
    assert header["icon_expanded_angle"] == -180, header
    assert panel["border"]["color"] == "grey", panel
    assert panel["element_id"], "面板要给 element_id，方便后续定位与更新"
    # 收起态下面板标题是用户唯一看得到的信息，必须带工具计数
    assert "1" in header["title"]["content"], header
    # 折叠面板是 2.0 专属，不许混进 1.0 卡
    legacy = cards.clarify_card("Q?", ["A"], clarify_id="c", session_key="s")
    assert "collapsible_panel" not in _all_tags(legacy)


def test_clarify_card_buttons_and_i18n():
    card = cards.clarify_card("选哪个？", ["A 方案", "B 方案"],
                              clarify_id="cid-1", session_key="sk-1")
    buttons = _buttons_of(card)
    assert len(buttons) == 3, "两个选项 + 一个『其他』"
    assert buttons[0]["value"]["answer"] == "A 方案"
    assert buttons[0]["value"]["clarify_id"] == "cid-1"
    assert buttons[0]["value"]["question"] == "选哪个？", "回填卡要用到问题原文"
    assert buttons[-1]["value"]["answer"] == cards.OTHER_VALUE
    # 双语：脚注元素同时带 content 与 i18n_content
    notes = [e for e in card["elements"] if e.get("tag") == "note"]
    assert notes and "i18n_content" in notes[0]["elements"][0], notes
    # 双语：header 与「其他」按钮都走 i18n_text —— 1.0 的标题和按钮 text
    # 接受 i18n_content，已由真机探针（probe_render 双语实验卡）确证。
    resolved = cards.clarify_resolved_card(question="选哪个？", answer="A", user_name="汪老师")
    for c in (card, resolved):
        title = c["header"]["title"]
        assert set(title["i18n_content"]) == {i18n.ZH, i18n.EN}, title
    other = buttons[-1]["text"]
    assert set(other["i18n_content"]) == {i18n.ZH, i18n.EN}, other


def _buttons_of(card):
    """从 1.0 卡的 ``action`` 按钮行里取出所有按钮。"""
    row = card.get("elements") or []
    for el in row:
        if el.get("tag") == "action":
            return el["actions"]
    raise AssertionError(f"卡片里没有 action 按钮行: {card}")


def test_clarify_card_must_be_legacy_dialect():
    """**1.0 的构造器**输出必须仍然是纯 1.0（顶层 elements + action 行）。

    2026-09-13：`clarify_dialect` 的**默认值已翻成 2.0**（真机点击到达 +
    2.0 e2e 全绿，两条前提都满足并留了证据，见 `_DEFAULTS` 的注释与 AGENTS.md 不变量 5）。
    但 1.0 这条路径**仍然必须可用且不许被改坏**（用户可能把它配回去），所以这条测试
    继续锁 1.0 构造器的形状 —— 真正的红线是**方言混用**：1.0 的 action 行放进 2.0 卡
    会被飞书拒（``230099``）。
    """
    card = cards.clarify_card("选哪个？", ["A", "B"], clarify_id="c", session_key="s")
    assert "schema" not in card, "澄清卡不能带 schema —— 带了她就是 2.0 卡，action 行会被拒"
    assert "body" not in card, "澄清卡必须用顶层 elements，不能包在 body 里"
    assert isinstance(card.get("elements"), list), card
    assert _buttons_of(card), "按钮必须在 action 容器里"
    assert card["config"]["wide_screen_mode"] is True
    assert card["header"]["template"] == "orange"


def test_clarify_resolved_card_shares_the_same_dialect():
    """回调里回填的卡必须与待答卡同方言，否则飞书会静默丢弃这一帧。"""
    pending = cards.clarify_card("选哪个？", ["A"], clarify_id="c", session_key="s")
    resolved = cards.clarify_resolved_card(question="选哪个？", answer="A", user_name="汪老师")
    for card in (pending, resolved):
        assert "schema" not in card and "body" not in card, card
        assert isinstance(card.get("elements"), list), card
    assert resolved["header"]["template"] == "green"


def test_reply_card_is_20_with_summary():
    """流式回复卡走 2.0，且必须带 config.summary（官方 SDK 与 HFC 都强制带）。"""
    card = cards.reply_card("一段很长的回答" * 30, streaming=True)
    assert card["schema"] == "2.0"
    assert "elements" not in card, "2.0 卡的正文在 body.elements 里"
    assert card["body"]["elements"]
    summary = card["config"].get("summary")
    assert isinstance(summary, dict) and summary.get("content"), "流式卡漏了 summary"
    assert len(summary["content"]) <= cards.SUMMARY_MAX


def test_card_byte_budget_degrades_decorations_never_body():
    """超预算时分级丢装饰（面板 → 页脚 → 全摘），**绝不截断正文**。

    官方把 native 流式下的长度责任明确推给适配器，而官方 send() 本身分块 ——
    正文过大时正确做法是让发送失败、由 fail-open 链回落官方分块（退化成多条纯文本，
    但答案完整）；静默截断会让用户以为模型就说了这么多。
    """
    panel = cards.unified_panel(reasoning="推" * 2000, tools=["bash"])
    footer = "🤖 m · ctx 1k/2k · ⏱ 1.0s"
    body = "答案" * 500

    node, tier = cards.fit_reply_card(body, streaming=True, panel=panel, footer=footer,
                                      budget=10 ** 9)
    assert tier == "ok"
    assert "collapsible_panel" in _all_tags(node), "预算充足时装饰该在"

    node2, tier2 = cards.fit_reply_card(body, streaming=True, panel=panel, footer=footer,
                                        budget=100)
    assert tier2 == "over-budget"
    assert body in json.dumps(node2, ensure_ascii=False), "正文被截断了 —— 绝不能静默截断答案"

    # 量纲是字节：中文 3 字节/字，按字符估会低估 3 倍
    assert cards.card_bytes({"a": "汉"}) == len('{"a": "汉"}'.encode("utf-8"))


def test_empty_streaming_body_shows_a_pending_placeholder():
    """建卡时正文还是空的 ⇒ 必须显示占位文案；**收尾帧不许带**。

    为什么值得一条门禁（aiduPOP 的效果图 1 = 「即时响应」）：光「卡出现得早」不够，
    还要**一眼看出它在干活**。我们此前建卡后正文是一个空格（`md()` 为了不让元素为空
    补的）⇒ 用户看到的是一张近乎空白的卡，直到第一个 token 到达 —— 效果 1a 的观感
    在真机上打了折扣。反过来更危险：占位若跟着**收尾帧**走，一个没有正文的回合
    （被中止 / 只输出思考）会永远停在「正在生成…」，那是**错误信息**而不是观感问题。
    """
    pending = cards._i18n.t(cards._PENDING_TEXT_KEY)
    assert pending.strip(), "占位文案不能是空的"

    # ① 流式 + 空正文 ⇒ 占位
    for blank in ("", "   ", "\n", None):
        node = cards.reply_card(blank, streaming=True)
        blob = json.dumps(node, ensure_ascii=False)
        assert pending in blob, (blank, blob)

    # ② 收尾帧（streaming=False）+ 空正文 ⇒ **不许**占位
    for blank in ("", "   ", None):
        node = cards.reply_card(blank, streaming=False)
        blob = json.dumps(node, ensure_ascii=False)
        assert pending not in blob, f"收尾帧带了占位，空答案的回合会永远停在「正在生成…」：{blob}"

    # ③ 有正文时，任何一帧都不该出现占位（它是「还没到」，不是「附言」）
    node = cards.reply_card("真实答案", streaming=True)
    blob = json.dumps(node, ensure_ascii=False)
    assert "真实答案" in blob and pending not in blob, blob

    # ④ 走完整降载阶梯也一样（超预算档也不能把占位吃掉或留下）
    for kwargs in ({}, {"budget": 10 ** 9}, {"panel": cards.unified_panel(status="ok")}):
        streaming_empty, tier = cards.fit_reply_card("", streaming=True, **kwargs)
        assert pending in json.dumps(streaming_empty, ensure_ascii=False), (tier, kwargs)
        final_empty, tier2 = cards.fit_reply_card("", streaming=False, **kwargs)
        assert pending not in json.dumps(final_empty, ensure_ascii=False), (tier2, kwargs)

    # ⑤ 占位文案必须有双语条目（界面文案一律走 i18n，不硬编码）
    assert compat_has_bilingual(cards._PENDING_TEXT_KEY), cards._PENDING_TEXT_KEY


def test_streaming_summary_is_never_empty():
    """空文本也要给兜底 summary。

    真实路径上会两次送出空文本：native 流式的 seed 帧（空文本建卡），以及归档点落在
    末尾时的 finalize 帧。``{"content": ""}`` 等于通知栏空白 —— 正是这个字段要防的事。
    """
    for empty in ("", " ", "   \n  "):
        for streaming in (True, False):
            node = cards.reply_card(empty, streaming=streaming)["config"].get("summary")
            if node is None:
                continue
            assert node.get("content"), f"空文本（{empty!r}, streaming={streaming}）下 summary 不该为空"


def test_dialect_element_exclusivity():
    """元素级方言互斥 —— 本地就能拦，不必等飞书拒。

    实测飞书裁决：
      * ``note``   在 2.0 卡里被废弃（im/v1/messages 返回 230099 / ErrCode 200861）
      * ``action`` 按钮容器在 2.0 卡里被拒（按钮点击永远到不了服务端）
      * ``collapsible_panel`` 是 2.0 专属，1.0 里没有
    """
    panel = cards.unified_panel(reasoning="想一下", tools=["read_file(a.py)"])
    samples = {
        "clarify_card": cards.clarify_card("Q?", ["A", "B"], clarify_id="c", session_key="s"),
        "clarify_resolved_card": cards.clarify_resolved_card(
            question="Q?", answer="A", user_name="汪老师"),
        "reply_card": cards.reply_card("答案", streaming=True, panel=panel, footer="脚注"),
    }
    for name, card in samples.items():
        tags = set(_all_tags(card))
        if card.get("schema") == cards.SCHEMA:
            assert "note" not in tags, f"{name}: 2.0 卡不能用 note（飞书已废弃该元素）"
            assert "action" not in tags, f"{name}: 2.0 卡不能用 action 容器（按钮会静默失效）"
        else:
            assert "schema" not in card, f"{name}: 1.0 卡不能带 schema 字段"
            assert "collapsible_panel" not in tags, f"{name}: collapsible_panel 是 2.0 专属"


def test_reply_card_footer_uses_20_footnote():
    """2.0 卡的脚注必须是 markdown + text_size=notation，不能是 note。"""
    tail = cards.reply_card("答案", streaming=True, footer="脚注")["body"]["elements"][-1]
    assert tail["tag"] == "markdown", tail
    assert tail["text_size"] == "notation", tail
    assert "脚注" in tail["content"]
    legacy = cards.clarify_card("Q?", ["A"], clarify_id="c", session_key="s")
    assert "note" in _all_tags(legacy["elements"]), "1.0 卡仍应能用 note 做脚注"


def test_i18n_text_is_bilingual():
    node = i18n.i18n_text("panel.title")
    assert node["content"] == i18n.t("panel.title", i18n.ZH)
    assert set(node["i18n_content"]) == set(i18n.DEFAULT_LOCALES)
    assert node["i18n_content"][i18n.EN] != ""
    assert i18n.t("no.such.key") == "no.such.key"


# --------------------------------------------------------------------------- #
# 3. 覆盖层行为 + 回落
# --------------------------------------------------------------------------- #
def test_send_renders_card_and_tracks():
    raw = _make()
    result = _run(raw.send("oc_1", "你好"))
    assert result.message_id == "om_card_1"
    kind = raw.calls[0]
    assert kind[0] == "send" and kind[1] == "interactive", raw.calls
    payload = json.loads(kind[2])
    assert payload["schema"] == "2.0"
    assert "om_card_1" in raw._ld_state, "发出的卡片必须纳入追踪，否则后续 edit 走内置"


def test_send_falls_back_to_text_on_card_failure():
    raw = _make(fail_cards=True)
    context.reset()
    result = _run(raw.send("oc_1", "你好"))
    assert result.message_id == "om_text_1", "卡片失败必须回落纯文本"
    assert any(c[0] == "SUPER.send" for c in raw.calls), raw.calls
    # ⚠️ **接线判据**（审计 X14）：`send()` 是「用户看到纯文本」的**主路径之一**，
    #    而它那两处 `note_plaintext_fallback` 原先**一条断言都没有** —— 上面这句已经
    #    把 `send()` 逼到了失败，却完全不看账本 ⇒ 把那两行整条删掉，四门禁照样全绿。
    #    同时钉住口径：这一格**该**计（我们真的发起过一次写、而它失败了），
    #    与 `test_send_empty_content_or_missing_client_falls_back` 那格（一次写都没发）相反。
    _snap = context.status_snapshot()
    assert _snap["fallback_count"] == 1 and "send 未成功" in _snap["fallback_reason"], _snap
    # 异常支路（另一处调用点）：`_ld_send_card` 抛 ⇒ 同样要落实计数，
    # 而且原因里要带**异常类型**（排障时「抛了什么」比「失败了」有用得多）。
    raw2 = _make()
    context.reset()

    async def _boom(*_a, **_kw):
        raise RuntimeError("卡片构造炸了")

    raw2._ld_send_card = _boom
    _run(raw2.send("oc_2", "你好"))
    _snap2 = context.status_snapshot()
    assert _snap2["fallback_count"] == 1 and \
        "send 异常：RuntimeError" in _snap2["fallback_reason"], _snap2


def test_send_empty_content_or_missing_client_falls_back():
    empty = _make()
    _run(empty.send("oc_1", ""))
    assert empty.calls[0][0] == "SUPER.send", "空内容不该发卡片"
    naked = _make()
    naked._client = None
    _run(naked.send("oc_1", "你好"))
    assert naked.calls[0][0] == "SUPER.send", "没有 SDK 客户端时只能走内置"


def test_send_cards_off_falls_back():
    defaults = dict(adapter._DEFAULTS)
    try:
        adapter.configure(cards=False)
        raw = _make()
        _run(raw.send("oc_1", "你好"))
        assert raw.calls[0][0] == "SUPER.send", "cards=False 必须完全退回纯文本"
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_edit_message_untracked_goes_to_builtin():
    raw = _make()
    _run(raw.edit_message("oc_1", "om_unknown", "x", finalize=False))
    assert raw.calls[0][0] == "SUPER.edit", raw.calls


def test_edit_message_updates_tracked_card_and_finalizes():
    raw = _make()
    _run(raw.send("oc_1", "流式中"))
    raw.calls.clear()
    _wire_patch(raw)
    _run(raw.edit_message("oc_1", "om_card_1", "答案", finalize=True))
    assert raw.calls == [], "已追踪的卡片应走卡片更新，不碰内置编辑"
    assert "om_card_1" not in raw._ld_state, "finalize 后应停止追踪"


def test_edit_message_falls_back_when_card_update_fails():
    raw = _make()
    _run(raw.send("oc_1", "流式中"))
    raw.calls.clear()
    _wire_patch(raw)
    raw._client.im.v1.message.patch = lambda request: {"code": 99999, "msg": "patch rejected"}
    result = _run(raw.edit_message("oc_1", "om_card_1", "答案", finalize=True))
    assert ("SUPER.edit", "答案", True) in raw.calls, "卡片更新失败必须回落内置编辑"
    assert result.message_id == "om_card_1"


def test_send_and_edit_include_panel():
    """面板数据（panel 快照）确实接进 send 首帧与 edit_message 流式更新。"""
    defaults = dict(adapter._DEFAULTS)
    try:
        panel.reset()
        adapter.configure(unified_panel=True)
        panel.record_reasoning("s1", "t1", "推理中……")
        raw = _make()
        _run(raw.send("oc_1", "你好"))
        payload = json.loads(raw.calls[0][2])
        assert "collapsible_panel" in _all_tags(payload), "首帧就该带上面板"

        raw.calls.clear()
        captured = _wire_patch(raw)
        panel.record_tool_started("s1", "t1", "bash", {"cmd": "ls"}, "c9")
        _run(raw.edit_message("oc_1", "om_card_1", "更新", finalize=False))
        body = json.loads(captured[0]["content"])
        assert "collapsible_panel" in _all_tags(body)
        joined = json.dumps(body, ensure_ascii=False)
        assert "推理中……" in joined and "Run command" in joined, joined
        assert "bash" not in joined, \
            f"默认 ap_lite 不该再把原始英文工具名倒进卡片：{joined}"
    finally:
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_send_first_frame_after_turn_switch_has_no_stale_panel():
    """新回合首帧不能带上一个回合的面板（on_stream_start 一到就先清）。"""
    defaults = dict(adapter._DEFAULTS)
    try:
        panel.reset()
        adapter.configure(unified_panel=True)
        panel.record_reasoning("s1", "t1", "上一回合的推理")
        raw = _make()
        _run(raw.send("oc_1", "你好"))
        first = json.loads(raw.calls[0][2])
        assert "collapsible_panel" in _all_tags(first), "有数据时首帧应带面板"

        # 模型开始了新回合：on_stream_start 先清旧数据（真实派发在 check_hooks 验）。
        panel.begin_turn("s1", "t2")
        raw.calls.clear()
        _run(raw.send("oc_1", "新回合第一句话"))
        second = json.loads(raw.calls[0][2])
        assert "collapsible_panel" not in _all_tags(second), "新回合首帧不该带上回合的面板"
    finally:
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_native_streaming_probe_and_seed():
    """native 流式探测 + seed 建卡：text 为空时建卡（流式态），并纳入追踪。"""
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0
    try:
        panel.reset()
        assert adapter.LarkDeckMixin.SUPPORTS_NATIVE_STREAMING is True
        raw = _make()
        assert raw.supports_native_streaming() is True
        assert raw.supports_native_streaming("p2p", None) is True
        assert raw.supports_native_streaming(chat_type="p2p", metadata={"x": 1}) is True

        assert _run(raw.send_stream_frame("", finalize=False, chat_id="oc_1",
                                          reply_to="om_orig", turn_id="t1")) is True
        kind, msg_type, payload = raw.calls[0]
        assert kind == "send" and msg_type == "interactive"
        card = json.loads(payload)
        assert card["config"].get("streaming_mode") is True, "seed 建卡必须是流式态"
        assert raw._ld_known("om_card_1") is not None, "native 卡必须被追踪"
        assert "oc_1:t1" in raw._ld_streams

        naked = _make()
        naked._client = None
        assert naked.supports_native_streaming() is False
        adapter.configure(cards=False)
        assert raw.supports_native_streaming() is False
        adapter.configure(cards=True, native_streaming=False)
        assert raw.supports_native_streaming() is False, "native_streaming=False 时不得启用"
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_declared_defaults_are_an_explicit_decision():
    """几个「用户看得见」的默认值必须有断言钉着 —— 翻它们必须是**显式决定**。

    教训（阶段 9）：我试着把 `native_transport` 默认翻成 `cardkit`，结果 8 条既有断言
    一起红 —— 因为那些测试隐式继承了默认值。现在测试自己声明传输（见 `_make`），
    而「默认到底是什么」由这一条集中声明：改默认就会红这一条，且**只红这一条**。
    """
    declared = dict(adapter._DEFAULTS)
    # cardkit 是 2026-09-13 翻的默认：两条前提都满足 —— ① 第十一路对抗审计无阻断
    # （序号 / 失败语义 / 孤儿实体都过了一遍，它指出的三条门禁缺口已补）；
    # ② 真机 `probe_render.py --cardkit-prod` 走生产路径全绿（建实体 1 + 发卡 1 + 写元素 6 + patch 1）。
    # 想翻回去（或再翻过来）改这一条 + `_DEFAULTS` + `plugin.yaml` + README 四处，
    # 然后**重跑变异套件**（CK6 专门钉这件事）。
    assert declared["native_transport"] == "cardkit", (
        "翻这个默认要先确认：cardkit 传输的边界（序号/失败语义/孤儿卡）已过审计，"
        "且 --cardkit-prod 真机生产路径全绿")
    assert declared["clarify_dialect"] == "2.0"       # 真机点击到达 + 2.0 e2e 全绿后才翻的
    assert declared["cards"] is True
    assert declared["native_streaming"] is True
    # 声明归声明：**什么都不配**时真正生效的传输必须是它 —— `_ld_transport()` 经 `_cfg_raw`
    # 回退到 `_DEFAULTS`，所以这两处一旦分叉（例如有人在 `_ld_transport` 里写死一条），
    # 「默认是逐字打字机」就只是文档里的一句话。四门禁此前只验显式配置过的值。
    saved = dict(adapter._CONFIG)
    adapter._CONFIG.clear()
    try:
        assert adapter.LarkDeckMixin._ld_transport() == declared["native_transport"], (
            "没配置任何东西时真正跑的传输与 `_DEFAULTS` 声明的不一致")
    finally:
        adapter._CONFIG.update(saved)


def _golden_trace() -> dict:
    """跑一遍**固定场景**，把「真正会发到飞书的东西」逐字记下来（golden trace）。

    为什么需要它（第十二路审计的要求）：R1 要重构 CardKit 的写入路径，声称「行为逐字节不变」——
    而「事后拍个快照跟自己对」是**自证循环**。所以先在重构**之前**把行为冻成夹具，
    重构后必须逐字节相等；夹具本身有变更时，diff 就是「这次改动到底改了什么行为」的显式声明。

    记录四类事实：建实体卡的 JSON、每次元素写入的 (元素, 内容, 序号, uuid)、
    收尾整卡 patch 的 JSON、每一帧的返回值。
    """
    calls = {"content": [], "patch": [], "entity": [], "batch": [], "settings": []}

    class _Resp:
        def __init__(self, code=0, **data):
            self.code = code
            self.msg = "success" if code == 0 else "boom"
            self.data = types.SimpleNamespace(**data) if data else None

        def success(self):
            return self.code == 0

    class _Card:
        def create(self, request):
            calls["entity"].append(request.request_body["card_json"])
            return _Resp(0, card_id="ck_golden")

        def batch_update(self, request):
            calls["batch"].append([json.loads(request.request_body.actions),
                                   request.request_body.sequence])
            return _Resp(0)

        def settings(self, request):
            # ⚠️ **必须有这一臂**（R7 审计的「仍未收口」第 3 条）：替身缺 `settings_card` 时，
            # `_CK_SUMMARY_INTERVAL=0` 让夹具变红的机制是 **AttributeError 崩帧**，而不是
            # 「预览写进了夹具的定义域」这条语义 —— 以后有人为扩展定义域补上这个替身，
            # 就会**静默失去**那条敏感度。补上之后，窗口一改，`settings` 这一列会如实出现在
            # 冻结的 trace 里（默认窗口下它是空的，所以冻结值不变）。
            calls["settings"].append([json.loads(request.request_body.settings),
                                      request.request_body.sequence,
                                      request.request_body.uuid])
            return _Resp(0)

    class _Elem:
        def content(self, request):
            calls["content"].append([request.element_id, request.request_body.content,
                                     request.request_body.sequence, request.request_body.uuid])
            return _Resp(0)

    class _Msg:
        def create(self, request):
            return {"code": 0, "data": {"message_id": "om_golden"}}

        def reply(self, request):
            return {"code": 0, "data": {"message_id": "om_golden"}}

        def patch(self, request):
            calls["patch"].append(request.request_body.content)
            return _Resp(0)

    client = types.SimpleNamespace(
        cardkit=types.SimpleNamespace(v1=types.SimpleNamespace(card=_Card(), card_element=_Elem())),
        im=types.SimpleNamespace(v1=types.SimpleNamespace(message=_Msg())))

    def _reqs():
        return types.SimpleNamespace(
            create_card=lambda card_json: types.SimpleNamespace(
                request_body={"card_json": card_json}),
            send_entity=lambda receive_id, card_id: types.SimpleNamespace(
                receive_id=receive_id, card_id=card_id,
                request_body=types.SimpleNamespace(content="{}", msg_type="interactive",
                                                   uuid=f"ld-msg-{card_id}")),
            reply_entity=lambda reply_to, card_id: types.SimpleNamespace(
                reply_to=reply_to, card_id=card_id,
                request_body=types.SimpleNamespace(content="{}", msg_type="interactive",
                                                   uuid=f"ld-msg-{card_id}",
                                                   reply_in_thread=False)),
            write_element=lambda cid, eid, content, seq, uuid_value: types.SimpleNamespace(
                card_id=cid, element_id=eid,
                request_body=types.SimpleNamespace(content=content, sequence=seq, uuid=uuid_value)),
            batch_update=lambda cid, actions, seq, uuid_value: types.SimpleNamespace(
                card_id=cid,
                request_body=types.SimpleNamespace(actions=json.dumps(actions, ensure_ascii=False),
                                                   sequence=seq, uuid=uuid_value)),
            settings_card=lambda cid, payload, seq, uuid_value: types.SimpleNamespace(
                card_id=cid,
                request_body=types.SimpleNamespace(settings=payload, sequence=seq,
                                                   uuid=uuid_value)),
        )

    raw = _make()
    raw._client = client
    old_reqs = adapter.LarkDeckMixin._ld_ck_requests
    old_interval = adapter._STREAM_MIN_INTERVAL
    # ⚠️ 必须**保存并恢复整份 `_CONFIG`**：夹具对环境极其敏感（`footer` / `panel_expanded` /
    # `show_model` 都会进收尾那张卡的 JSON），而全套件跑起来时前面的用例会改配置 ——
    # 那会让夹具在「单独跑」时绿、「全套件跑」时红（我第一版就是这样，白白怀疑了一阵）。
    saved_config = dict(adapter._CONFIG)
    # ⚠️ **冻结时钟**：面板标题的 `· 0.1s` 与收尾卡的 `⏱ 12.3s` 都是墙钟算出来的 ——
    # 不冻结的话，夹具会在「单独跑」与「全套件跑」之间随机不一致（第一版就是这样：
    # 我一度以为夹具坏了，其实是它在记录时间）。时间派生值**不在夹具覆盖范围内**。
    saved_monotonic, saved_time = time.monotonic, time.time
    time.monotonic = lambda: 1_700_000_000.0        # type: ignore[assignment]
    time.time = lambda: 1_700_000_000.0             # type: ignore[assignment]

    # ⚠️ 还要清掉**全局指标快照**（`context`）：面板标题里的模型名/轮数来自它，
    # 而前面的用例会往里灌数据 —— 不清就会出现「同一份代码、两次运行标题不同」。
    context.reset()
    adapter.LarkDeckMixin._ld_ck_requests = staticmethod(_reqs)
    adapter._STREAM_MIN_INTERVAL = 0.0
    returns = []
    try:
        adapter.configure(native_transport="cardkit", unified_panel=True,
                          panel_expanded=False, footer=True, show_model=True)
        # ⚠️ 必须**灌入非空面板数据**：否则夹具里 `panel_body` 的两次内容都是 `" "`，
        # 而「面板内容整条丢失」这种改动就抓不到（R1 审计 W6：把面板内容换成常量，全绿）。
        panel.reset()
        panel.record_reasoning("oc_golden", "s-golden", "先想一下这个问题该怎么拆。")
        # ⚠️ **seed 之前先有一个工具**：否则种子那一帧的 `panel_tools` 只是空占位，
        #    「seed 漏传 `panel_tools_text`」这种改动在夹具里**看不出来**（R3 代码审计中-3
        #    实测：掉参数四门禁全绿）。工具事件会 finalize 当前推理轮，所以下面还会再补一轮推理。
        panel.record_tool_started("oc_golden", "s-golden", "terminal",
                                  {"command": "ls"}, "tc-golden-0")
        returns.append(_run(raw.send_stream_frame("", chat_id="oc_golden", turn_id="t-golden")))
        # ⚠️ **必须有工具事件**（R3 收窄版的夹具定义域）：面板拆成 `panel_body` + `panel_tools`
        # 两块之后，只灌推理的场景里 `panel_tools` 恒为空 ⇒ `entity_card` 与装饰 batch 在
        # 「工具块整条丢失/写错元素」这类改动下**逐字不变**，夹具对它们**零判别力**
        # （方案审计中-4④ 实测：那份场景一个工具步都没有）。工具事件本身也会 finalize 一轮推理，
        # 所以它同时把「轮次标题」这一路也带进了定义域。
        # ⚠️ 两个位置参数是 `(session_id, turn_id)`，本场景上面写的是
        # `record_reasoning("oc_golden", "s-golden", …)` ⇒ 这里必须是**同一对**
        # （session=`oc_golden`、turn=`s-golden`）。我第一版把第二个参数写成 `"t-golden"`，
        # 于是 `_touch_locked` 判成「换了回合」⇒ **清空 rounds**，夹具里 `panel_body` 写成空、
        # 只有 `panel_tools` 有内容（门禁当时全绿 —— 是靠**读夹具 diff** 抓到的，
        # 这也是为什么夹具改完必须逐字段看一眼再接受）。
        panel.record_tool_started("oc_golden", "s-golden", "read_file",
                                  {"path": "/tmp/golden.txt"}, "tc-golden-1")
        returns.append(_run(raw.send_stream_frame("第一段", chat_id="oc_golden", turn_id="t-golden")))
        panel.record_tool_finished("oc_golden", "s-golden", "read_file", status="ok",
                                   duration_ms=2300, tool_call_id="tc-golden-1")
        returns.append(_run(raw.send_stream_frame("第一段，第二段。", chat_id="oc_golden",
                                                  turn_id="t-golden")))
        # 用户 2026-09-17 的观感要求：结局状态在最前、模型名在页脚（不再进面板标题）。
        # 把这两样塞进夹具的**定义域**，否则「状态/模型整段丢掉」在 golden trace 里逐字不变。
        context.record_api_call(model="test-model", provider="test",
                                usage={"input_tokens": 21000, "prompt_tokens": 21000,
                                       "output_tokens": 5})
        panel.record_turn_end("oc_golden", "s-golden", completed=True)
        finalized = []

        async def _record_update(chat_id, mid, card):
            finalized.append((chat_id, mid, json.dumps(card, ensure_ascii=False)))
            return _StubResult(True, mid)

        raw._ld_update_card = _record_update
        returns.append(_run(raw.send_stream_frame("第一段，第二段。", finalize=True,
                                                  chat_id="oc_golden", turn_id="t-golden")))
    finally:
        time.monotonic, time.time = saved_monotonic, saved_time
        context.reset()
        adapter.LarkDeckMixin._ld_ck_requests = old_reqs
        adapter._STREAM_MIN_INTERVAL = old_interval
        adapter._CONFIG.clear()
        adapter._CONFIG.update(saved_config)
        panel.reset()
    return {"entity_card": calls["entity"], "element_writes": calls["content"],
            "decor_batches": calls["batch"],
            # R7：会话预览也在夹具的**定义域**里（默认窗口下是空表；窗口一旦被改小，
            # 这一列会如实变化 ⇒ 夹具红在语义上，而不是崩在 AttributeError 上）。
            "summary_writes": calls["settings"],
            "final_patch": [c for _, _, c in finalized], "returns": returns}


def test_context_snapshot_exposes_r7_footer_fields():
    """R7 页脚扩展的数据层：新字段必须**如实派生**，缺数据就是 `None`（不许编 0）。

    语义来自 Hermes 的 `_fire_post_api_request_hook`：
      * `first_chunk_at` 是首个流式分块的 epoch 秒，官方注释写明 TTFB = `first_chunk_at - started_at`；
      * `api_duration` 是**本次请求**耗时（不是回合耗时 —— 那个在面板标题里）；
      * `cache_read_tokens` / `cache_write_tokens` / `reasoning_tokens` / `total_tokens` 在 `usage` 里。
    """
    context.reset()
    # ① 完整载荷：每个派生值都要对得上
    context.record_api_call(
        model="m-1", provider="p-1", base_url="", api_call_count=7, finish_reason="stop",
        message_count=12, api_duration=1.5, started_at=1000.0, first_chunk_at=1000.42,
        session_id="s-1", platform="feishu",
        usage={"input_tokens": 100, "output_tokens": 50, "prompt_tokens": 400,
               "cache_read_tokens": 300, "cache_write_tokens": 0,
               "reasoning_tokens": 20, "total_tokens": 450})
    snap = context.snapshot()
    assert snap["input_tokens"] == 400, snap["input_tokens"]      # prompt 优先（含缓存）
    assert snap["cache_write_tokens"] == 0 and snap["reasoning_tokens"] == 20
    assert snap["total_tokens"] == 450 and snap["api_call_count"] == 7
    assert snap["cache_pct"] == 75.0, f"300/400 = 75%：{snap['cache_pct']}"
    assert snap["ttfb_ms"] == 420, f"1.00042-1.0 = 0.42s ⇒ 420ms：{snap['ttfb_ms']}"
    assert snap["api_duration_ms"] == 1500, snap["api_duration_ms"]
    assert snap["finish_reason"] == "stop" and snap["message_count"] == 12

    # ② 缺字段：一律 None，**不是 0**（编 0 会让页脚显示「缓存命中 0%」这种假信息）
    context.reset()
    context.record_api_call(model="m-2", usage={"input_tokens": 10, "prompt_tokens": 10})
    snap = context.snapshot()
    assert snap["cache_pct"] is None, f"没有 cache_read ⇒ 必须 None：{snap['cache_pct']}"
    assert snap["ttfb_ms"] is None and snap["api_duration_ms"] is None
    assert snap["cache_write_tokens"] is None and snap["message_count"] is None
    assert snap["finish_reason"] == ""

    # ③ 坏值不抛：inf / NaN / 负数差 / 字符串
    context.reset()
    context.record_api_call(model="m-3", api_duration=float("inf"), started_at=2000.0,
                            first_chunk_at=1000.0,          # 负差 ⇒ None
                            usage={"prompt_tokens": 0, "cache_read_tokens": 5})
    try:
        snap = context.snapshot()
    except Exception as exc:  # 撤掉 _as_float 的 inf 守卫时 round(inf) 会炸到这里
        raise AssertionError(f"病态 duration/时间戳不能让 snapshot 崩溃：{exc!r}") from exc
    assert snap["api_duration_ms"] is None, "inf 秒不能变成一个巨大的毫秒数"
    assert snap["ttfb_ms"] is None, "负的首字延迟必须 None，不能是负数毫秒"
    assert snap["cache_pct"] is None, "分母为 0 ⇒ None"
    context.reset()


def test_ck_plan_content_and_role_failures_are_pinned():
    """`_ck_plan` / `fail_reason` 的**纯函数**断言（R1 审计 U4 + W6）。

    为什么单独锁这两件事：
      * W6：夹具里 `panel_body` 的内容只出现过 `" "` ⇒ 「面板内容整条丢失」抓不到
        （审计实测：把面板内容换成常量，四门禁全绿）。纯函数层锁内容最便宜。
      * U4：`fail_reason()` 换成一个常量、或把角色判据从「面板」放宽到「任意失败」，
        四门禁都全绿 ⇒ 「哪个元素死了」在真机上就无从查起（而 `_ld_stream_fail` 的告警
        是进程级 30 秒限流，一帧里第二个失败的元素终生不留痕）。
    """
    elems = [cards.CARDKIT_ANSWER_ID, cards.CARDKIT_PANEL_BODY_ID]
    ops = adapter._ck_plan("正文内容", "面板内容", elems)
    # 顺序：装饰先、**正文最后**（提交点在后）
    assert [op.element_id for op in ops] == ["panel_body", "answer"], [o.element_id for o in ops]
    assert ops[0].content == "面板内容" and ops[0].role == adapter._CK_ROLE_PANEL
    assert ops[1].content == "正文内容" and ops[1].role == adapter._CK_ROLE_ANSWER
    # 空内容兜底：面板/正文都不许把元素刷成空串（飞书对空内容有历史坑）
    assert adapter._ck_plan("", "", elems)[0].content == " "
    assert adapter._ck_plan("", "", elems)[1].content == " "
    # 卡里没有的元素一个都不写（面板关掉时只剩正文）
    only_answer = adapter._ck_plan("正文内容", "面板内容", [cards.CARDKIT_ANSWER_ID])
    assert [op.element_id for op in only_answer] == ["answer"], only_answer
    # 未知 id 被忽略（不写不存在的元素 ⇒ 真机 300313）
    assert adapter._ck_plan("x", "y", ["ghost"]) == []
    # 角色文案：三条互不相同、各自点名自己的元素 id（真机上唯一的线索）
    reasons = {}
    for role in (adapter._CK_ROLE_ANSWER, adapter._CK_ROLE_PANEL, adapter._CK_ROLE_DECOR):
        eid = {"answer": "answer", "panel": "panel_body", "decor": "footer"}[role]
        reasons[role] = adapter._CkOp(eid, "c", role).fail_reason()
    assert len(set(reasons.values())) == 3, f"三个角色的失败文案必须互不相同：{reasons}"
    assert "answer" in reasons[adapter._CK_ROLE_ANSWER]
    assert "panel_body" in reasons[adapter._CK_ROLE_PANEL]
    assert "footer" in reasons[adapter._CK_ROLE_DECOR]
    # 写入预算（`docs/plan-v1.md` 附录 B）：数字**写死**在这里，因为它们是「照着官方口径
    # 留余量」的**显式决定**，不是从别的常量顺手推出来的。算式那条断言单独也留着 ——
    # 它保证「改了帧窗口却没同步预算」会发生在这里，而不是发生在真机的限流上。
    assert adapter._CK_WRITES_PER_SECOND == 10, \
        f"卡级写入上限的官方口径是 10 次/秒：{adapter._CK_WRITES_PER_SECOND}"
    assert adapter._CK_WRITES_PER_FRAME == 2, \
        f"0.25s 的帧窗口 ⇒ 每帧最多 2 次写：{adapter._CK_WRITES_PER_FRAME}"
    assert adapter._CK_WRITES_PER_FRAME == max(
        1, int(adapter._CK_WRITES_PER_SECOND * adapter._STREAM_MIN_INTERVAL)), \
        "每帧预算必须等于「卡级上限 × 帧窗口」（改了窗口就要一起改预算）"


def test_ck_create_wall_counts_elements_recursively():
    """建实体前的**两道墙**（R2 补的审计缺口）：字节是硬上限，元素是**递归** 200。

    为什么单独拿纯函数验：这道墙在今天的实体卡上**永远不会响**（结构定死 **5** 个元素：
    正文 + 面板 + 面板里两个 markdown + 页脚）—— 而 R3 **完整版**（每工具一行，运行时
    `card_element.create`）会把元素数变成动态的，那时它必须已经在位。用**合成的长卡**
    直接喂它，不依赖「今天会不会响」。
    ⚠️ 判别力来自**变异 `R2-12`**（把递归计数换成只数顶层）：嵌套那一条（1 个面板里塞 210 个
    子元素）在**只数顶层**的错实现下会放行 ⇒ 断言变红。审计提醒：别把这条夸成「两种实现在
    扁平卡上一样、只有嵌套能区分」——那是**变异**的判别力来源，不是这组断言的强度来源；
    断言本身是在「递归总数必须 ≤200」这条真机口径上钉死的。
    """
    limit = cards.FEISHU_ELEMENT_LIMIT
    assert limit == 200, f"元素硬上限是实测出来的 200：{limit}"

    def _flat(n: int) -> dict:
        return {"schema": "2.0",
                "body": {"elements": [{"tag": "markdown", "element_id": f"e{i}", "content": "x"}
                                      for i in range(n)]}}

    def _nested(children: int) -> dict:
        return {"schema": "2.0", "body": {"elements": [
            {"tag": "collapsible_panel", "element_id": "panel",
             "elements": [{"tag": "markdown", "element_id": f"c{i}", "content": "x"}
                          for i in range(children)]}]}}

    assert adapter._ck_create_wall(_flat(3)) is None
    assert adapter._ck_create_wall(_flat(limit)) is None, "正好 200 个必须放行（真机 204 才拒）"
    assert adapter._ck_create_wall(_flat(limit + 1)) == "元素"
    assert adapter._ck_create_wall(_nested(3)) is None
    assert adapter._ck_create_wall(_nested(limit + 10)) == "元素", \
        "嵌套元素必须计入（只数顶层会让「面板里塞 200 个子元素」从闸门底下溜过去）"
    # 字节墙（口径 = JSON 转义后的字节，同 `card_bytes`）
    fat = {"schema": "2.0", "body": {"elements": [
        {"tag": "markdown", "element_id": "answer",
         "content": "汉" * (cards.FEISHU_CARD_BYTE_LIMIT // 3 + 100)}]}}
    assert adapter._ck_create_wall(fat) == "字节"
    assert adapter._ck_create_wall(
        {"schema": "2.0", "body": {"elements": [
            {"tag": "markdown", "element_id": "answer", "content": "小卡"}]}}) is None


def test_cardkit_golden_trace_is_frozen():
    """R1 的地基：**重构前**把 CardKit 写入路径的行为冻成夹具，重构后必须逐字节不变。

    夹具在 `tests/golden_cardkit_trace.json`；**有意**改行为后用
    `python3 tests/write_golden_trace.py` 重新生成（那份 diff 就是行为变更声明）。
    夹具的**定义域**：cardkit 传输的成功路径 + 建实体 JSON + 每次写入的
    (元素, 内容, 序号) + 收尾整卡 JSON + 每帧返回值。
    （装饰 batch 那一列记的是 (actions, sequence) —— **没有 uuid**：batch 的 uuid 由
    (卡, 序号) 推出、正文的由 (卡, 元素, 序号) 推出，两者不同源，夹具只记序号。审计提醒过
    这句措辞别写成「每次写入都含 uuid」。）
    其中「装饰**只在内容变了时**才写」这条 R2 规则也在域内：夹具里第二帧的装饰没变 ⇒
    只有一次 batch（序号 1），两帧的正文是 2、3 号。
    **P2 起默认主题是 `ap_lite`**（面板标题 🌊/🧰、工具行带 📖/⌨️ 图标），所以主题层的符号
    与图标也在夹具的定义域里；换默认主题就是换用户可见行为，必须重跑夹具并在提交信息里声明。
    **它覆盖不到的**（这些地方「逐字节不变」不成立，别假装成立）：所有失败路径、
    `patch` 传输的分支、帧节流跳过、字节闸门、`settings`(summary)，
    以及**一切时间派生值**（时钟在采集期间被冻结 ⇒ 耗时/首字延迟这类字段不进夹具）。
    ⚠️ 夹具**故意**是字面量文件而不是「跑一遍再跟自己对」：后者是自证循环，
    它永远绿，也就永远抓不到「重构悄悄改了行为」。
    """
    trace = _golden_trace()
    path = _pathlib.Path(__file__).with_name("golden_cardkit_trace.json")
    assert path.exists(), f"缺少 golden trace 夹具：{path}（跑 `tests/write_golden_trace.py` 生成）"
    want = json.loads(path.read_text(encoding="utf-8"))
    got = json.loads(json.dumps(trace, ensure_ascii=False))
    assert got == want, (
        "CardKit 写路径的行为变了（golden trace 不相等）。"
        "若这是**有意**的行为变更，请在同一提交里更新夹具并说明改了什么；"
        "若不是，说明重构破坏了既有行为。\n"
        f"  期望：{json.dumps(want, ensure_ascii=False)[:400]}\n"
        f"  实得：{json.dumps(got, ensure_ascii=False)[:400]}")


def _mk_cardkit_fake(*, fail_write_after=10 ** 9, create_ok=True, fail_answer_only=False,
             fail_panel_only=False, create_empty_id=False,
             create_codes=None, answer_codes=None, fail_first_only=False,
             fail_at=None, fail_at_code=300309, fail_batch=False,
             fail_batch_code=300309, fail_batch_msg=None, patch_code=0,
             settings_code=0, settings_msg=None, settings_raises=False):
    calls = {"create": 0, "send": 0, "reply": 0, "content": [], "patch": 0,
             "patch_mids": [], "settings": [],
             "entity": [], "send_req": [], "reply_req": [], "batch": [],
             "writes": 0}

    class _Resp:
        def __init__(self, code=0, msg=None, **data):
            self.code = code
            # msg 可注入：R5 的「服务端点名坏元素」那条路要求 msg 长得跟真机一样
            # （真机实测 `ErrMsg: not find elementID : ghost_missing_element; `），
            # 默认的 "boom" 解析不出 id ⇒ 走「整批标死」那条保守分支。
            self.msg = msg if msg is not None else ("success" if code == 0 else "boom")
            self.data = types.SimpleNamespace(**data) if data else None

        def success(self):
            return self.code == 0

    class _CardRes:
        def batch_update(self, request):
            calls["writes"] += 1
            calls["batch"].append((json.loads(request.request_body.actions),
                                   request.request_body.sequence))
            if fail_batch:
                # 码可配：`300309`（结构性死法）与 `99991400`（限流类 ⇒ 会退避重试）
                # 在 R2 之后的处置**不一样**，用例要能分别构造；msg 也可配（R5 的
                # 「服务端点名坏元素」需要一个真机形状的 msg）
                return _Resp(fail_batch_code, msg=fail_batch_msg)
            if fail_at is not None and calls["writes"] == fail_at:
                return _Resp(fail_at_code)
            return _Resp(0)

        def settings(self, request):
            """`card.settings`（R7 的会话列表预览）——记下 payload 与序号。

            ⚠️ 这里**也必须计入 `calls["writes"]`**（R7 审计中-2）：这本账是
            「写入预算」的**观测量**（附录 B 断言②：真跑 N 帧、数一数调用次数）。
            不计的话，「预览每帧都写」这种实现从这本账上**看不见** —— 实测把
            「成功但不记账」那一行删掉，四门禁全绿，而限频被彻底废掉。
            """
            calls["writes"] += 1
            calls["settings"].append((json.loads(request.request_body.settings),
                                      request.request_body.sequence,
                                      request.request_body.uuid))
            if settings_raises:
                # H-1：真机上这条路是 `run_in_executor` + `requests.request`（**一行
                # try 都没有**）⇒ 连接重置/超时就是这个形状。
                raise ConnectionError("settings boom")
            if settings_code:
                return _Resp(settings_code, msg=settings_msg)
            return _Resp(0)

        def create(self, request):
            calls["create"] += 1
            calls["entity"].append(request.request_body["card_json"])
            # ⚠️ card_id **按次数递增**（R4 卡链要能分辨第一张/第二张；原来恒为 `ck_1`
            # 会让「切卡后还在写旧卡」这种缺陷从账本上看不出来）
            _cid = f"ck_{calls['create']}"
            calls.setdefault("entity_ids", []).append(_cid)
            if create_codes:
                # 按脚本发码：列表用完后重复最后一个（模拟「一直限流」）
                code = create_codes[min(calls["create"] - 1, len(create_codes) - 1)]
                return _Resp(code, card_id=_cid) if code == 0 else _Resp(code)
            if create_empty_id:
                # code=0 但 data.card_id 是空串：飞书没给实体 id
                return _Resp(0, card_id="")
            return _Resp(0, card_id=_cid) if create_ok else _Resp(300305)

    class _ElemRes:
        def content(self, request):
            calls["writes"] += 1
            calls["content"].append((request.element_id, request.request_body.content,
                                     request.request_body.sequence))
            # ⚠️ 计数必须**跨 batch 与 content 合计**：两者共用同一个序号账本
            # （R2 起每帧 = 一次 batch + 一次 content），只看 content 的话
            # 「第 3 次写入」永远不会发生（R2 落地时这条前提当场红了）。
            if fail_first_only and calls["writes"] == 1:
                return _Resp(300309)
            if fail_at is not None and calls["writes"] == fail_at:
                # ⚠️ `fail_at_code` 必须可配：`300309`（会话已关）在 R5 起是**可降级的
                # 卡级死法**，用它当「这一帧真的失败了」的前提已经不成立（用例当场红）。
                # 测序号账本要用**确定性拒收**类码（附录 A 的 FATAL 那一行）。
                return _Resp(fail_at_code)
            if answer_codes and request.element_id == cards.CARDKIT_ANSWER_ID:
                idx = sum(1 for c in calls["content"]
                          if c[0] == cards.CARDKIT_ANSWER_ID) - 1
                return _Resp(answer_codes[min(idx, len(answer_codes) - 1)])
            if fail_answer_only and request.element_id == cards.CARDKIT_ANSWER_ID:
                # ⚠️ 故意用**确定性拒收**码（230099）而不是 `300309`：R5 起 `300309`
                # 是「可降级的卡级死法」（会走 DEGRADE 车道并让帧返回 True），
                # 拿它当「正文写失败 ⇒ 必须 fail-open」的前提已经不成立。
                return _Resp(230099, msg="deterministic rejection")
            if fail_panel_only and request.element_id == cards.CARDKIT_PANEL_BODY_ID:
                return _Resp(300309)
            if len(calls["content"]) > fail_write_after:
                return _Resp(300309)
            return _Resp(0)

    class _MsgRes:
        def create(self, request):
            calls["send"] += 1
            calls["send_req"].append(request)
            # ⚠️ 形状必须与替身适配器的 `_finalize_send_result` 一致（它读 dict）
            return {"code": 0, "data": {"message_id": "om_ck_1"}}

        def reply(self, request):
            calls["reply"] += 1
            calls["reply_req"].append(request)
            return {"code": 0, "data": {"message_id": "om_ck_r1"}}

        def patch(self, request):
            calls["patch"] += 1
            # ⚠️ **必须记下目标 message_id**（R5 审计高-2：把它换成别的 id，四门禁曾全绿）
            calls.setdefault("patch_mids", []).append(
                getattr(request, "message_id", None))
            # R4：封旧卡走的是整卡 patch ⇒ 内容要能断言（封的是哪一段、有没有关流式态）
            calls.setdefault("patch_cards", []).append(
                json.loads(request.request_body.content))
            # ⚠️ 形状必须是 **dict**：替身适配器的 `_finalize_send_result` 读
            # `response["data"]["message_id"]`（与真 SDK 响应的取值路径一致）。
            # 返回 `_Resp` 对象会让它 AttributeError ⇒ 帧路径吞掉异常 ⇒
            # 「降级车道」看起来像是没生效（这条断言当场红了一次）。
            # 码可配：R5 的撤回守卫要构造 `230011 The message was withdrawn.`
            if patch_code:
                return {"code": patch_code, "msg": "The message was withdrawn."}
            return {"code": 0, "data": {"message_id": "om_ck_1"}}

    client = types.SimpleNamespace(
        cardkit=types.SimpleNamespace(v1=types.SimpleNamespace(
            card=_CardRes(), card_element=_ElemRes())),
        im=types.SimpleNamespace(v1=types.SimpleNamespace(message=_MsgRes())))
    return calls, client


# ⚠️ `test_units.py` 是**零 Hermes 依赖**的，所以 SDK 的请求构造必须可注入：
# 真环境用 `_ld_ck_requests()` 里的 SDK builder，这里换成等价的哑对象。
def _fake_ck_requests():
    return types.SimpleNamespace(
        create_card=lambda card_json: types.SimpleNamespace(
            request_body={"card_json": card_json}),
        write_element=lambda cid, eid, content, seq, uuid_value: types.SimpleNamespace(
            card_id=cid, element_id=eid,
            request_body=types.SimpleNamespace(content=content, sequence=seq,
                                               uuid=uuid_value)),
        batch_update=lambda cid, actions, seq, uuid_value: types.SimpleNamespace(
            card_id=cid,
            request_body=types.SimpleNamespace(actions=json.dumps(actions, ensure_ascii=False),
                                               sequence=seq, uuid=uuid_value)),
        # ⚠️ R7：帧路径会调 `card.settings`（会话列表预览）——替身少了这一个属性会
        # AttributeError，被帧路径吞掉后表现为「这一帧失败」，与真实原因无关。
        settings_card=lambda cid, payload, seq, uuid_value: types.SimpleNamespace(
            card_id=cid,
            request_body=types.SimpleNamespace(settings=payload, sequence=seq,
                                               uuid=uuid_value)),
        # ⚠️ 锚点/去重键必须能被断言：替身以前**不记录** `reply_to`，于是
        # 「cardkit 把回复锚点整个丢掉」（第十二路审计实测）四门禁全绿 ——
        # 翻默认之后这意味着**每次回答都不再挂在提问下面**。
        send_entity=lambda receive_id, card_id: types.SimpleNamespace(
            receive_id=receive_id, card_id=card_id,
            request_body=types.SimpleNamespace(
                content=json.dumps({"type": "card", "data": {"card_id": card_id}}),
                msg_type="interactive", uuid=f"ld-msg-{card_id}")),
        reply_entity=lambda reply_to, card_id: types.SimpleNamespace(
            reply_to=reply_to, card_id=card_id,
            request_body=types.SimpleNamespace(
                content=json.dumps({"type": "card", "data": {"card_id": card_id}}),
                msg_type="interactive", uuid=f"ld-msg-{card_id}",
                reply_in_thread=False)),
    )


def test_cardkit_transport_writes_elements_and_falls_open():
    """阶段 9：`native_transport: "cardkit"` 的帧序列 + **任何一步失败都回落**。

    真机实测确立的两条硬约束（`docs/plan-6-effects.md` 阶段 9）：
      * **只能按 `element_id` 写内容**（`card_element.content`），结构在建实体时定死 ——
        任何结构性写入（patch / card.update）都会**关闭流式会话**，之后再写元素得 `300309`；
      * 序号必须**单调递增**（用 settings 重开会话后序号没对齐会拿到 `300317`）。
    另外这是**新增的一条传输**，所以「失败必须回落」这条不变量（宁可退回纯文本也不丢消息）
    必须在这里也成立 —— 这里逐条打桩验证。
    """
    defaults = dict(adapter._DEFAULTS)
    try:
        _mk_fake = _mk_cardkit_fake

        _fake_requests = _fake_ck_requests

        # ① 全链路成功：建实体 → 四帧（装饰**只在变了的那一帧**发一次 batch，正文每帧一次）
        #    → 收尾走 patch
        calls, client = _mk_fake()
        raw = _make()
        raw._client = client
        raw._ld_send_card = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("cardkit 模式不该走 _ld_send_card"))
        adapter.configure(native_transport="cardkit", theme="neutral")
        old_reqs = adapter.LarkDeckMixin._ld_ck_requests
        old_interval = adapter._STREAM_MIN_INTERVAL
        adapter._STREAM_MIN_INTERVAL = 0.0        # 帧节流窗口：测试里连发两帧要都能过
        adapter.LarkDeckMixin._ld_ck_requests = staticmethod(_fake_requests)
        # ── R9 自检账本在这条**默认传输（cardkit）**的真机主路径上必须自证（审计高-1）。
        #    审计实测：把 seed 处或元素写成功分支的 `note_frame_ok()` 换成 `pass`，**四门禁全绿** ——
        #    而代价是 `/larkdeck status` 永远说「最近写卡：无记录 · 累计 0 次」，用户眼前却躺着
        #    一张逐字往外冒的卡。下面这几条**硬编码**的数字就是为此钉的。
        #    ⚠️ 数字全部**写死**（不许用 `len(calls[...]) + 2` 这类由被测代码推出的期望值 ——
        #    那就是自证循环：变异把记账撤掉，期望值跟着掉，断言照样绿）。推导过程：
        #      seed 建实体 = 1（`card.create` + 发实体卡两次网络调用算**一帧**，见 context 的口径）
        #      四帧正文   = 4（每帧只写元素，不经 `_ld_update_card`）
        #      收尾 patch = 1（走 `_ld_update_card`）
        #    合计 6。
        #    ⚠️ 断言位置：**必须放在本用例中途那次 `context.reset()` 之前**（它在 ⑱ 段的
        #    「量 footer 之前先把采集数据清掉」那几行）。选择「提前断言」而不是「在 reset 后重新
        #    灌一遍计数」：后者要手工调 `note_frame_ok()` 造数，那验的就是我自己造的账、
        #    不是这次跑出来的账（lessons 推论 19 的第三形态）。所以这里逐段钉，顺带给出
        #    「哪一段漏记」的直接证据。
        context.reset()
        assert _run(raw.send_stream_frame("", chat_id="oc_ck", turn_id="t-ck"))
        assert context.status_snapshot()["frame_ok_count"] == 1, \
            f"cardkit 的 seed 建实体没记账（默认传输下这一帧等于不存在）：" \
            f"{context.status_snapshot()}"
        assert calls["create"] == 1 and calls["send"] == 1, calls
        assert _run(raw.send_stream_frame("正文一", chat_id="oc_ck", turn_id="t-ck"))
        assert _run(raw.send_stream_frame("正文一，正文二", chat_id="oc_ck", turn_id="t-ck"))
        assert context.status_snapshot()["frame_ok_count"] == 3, \
            f"cardkit 的元素写帧没记账（真机主路径）：{context.status_snapshot()}"
        # ── 下面两帧专门验 R2 的「**未变化不重写**」：装饰只在内容真的变了时才发那一次 batch。
        #    驱动用的是**真的数据层**（页脚走 `context`、面板走 `panel`），不是替身 ——
        #    这条规则唯一的失败形态是「装饰静默冻结」，只有在真数据层上才验得出来。
        footer_before = raw._ld_footer() or ""
        context.record_api_call(model="test-model",
                                usage={"input_tokens": 4242, "output_tokens": 8})
        context.set_context_override(10000)
        footer_after = raw._ld_footer() or ""
        assert footer_after and footer_after != footer_before, \
            f"⑱ 前提：这一帧的页脚必须真的变了，否则证不了「变了就重写」：{footer_before!r} → {footer_after!r}"
        assert _run(raw.send_stream_frame("正文一，正文二，正文三",
                                          chat_id="oc_ck", turn_id="t-ck"))
        panel.reset()
        panel.record_reasoning("oc_ck", "s-ck-1", "面板到这一帧才第一次有内容")
        assert raw._ld_panel_markdown("oc_ck"), "⑱ 前提：面板到这一帧必须有内容"
        assert _run(raw.send_stream_frame("正文一，正文二，正文三，正文四",
                                          chat_id="oc_ck", turn_id="t-ck"))
        assert context.status_snapshot()["frame_ok_count"] == 5, \
            f"四帧正文后应是 1(seed) + 4(元素写) = 5 帧：{context.status_snapshot()}"
        ids = [c[0] for c in calls["content"]]
        seqs = [c[2] for c in calls["content"]]
        batch_ids = [[a["params"]["element_id"] for a in b[0]] for b in calls["batch"]]
        batch_seqs = [b[1] for b in calls["batch"]]
        # 页脚那一路真的把**当前**页脚写进去了吗（只比对 id 的话，「写了一版过期页脚」全绿）
        footer_written = [[a["params"]["partial_element"]["content"] for a in b[0]
                           if a["params"]["element_id"] == "footer"] for b in calls["batch"]]
        panel.reset()
        context.reset()
        context.set_context_override(None)
        # ⚠️ 必须写**字面量**：两边都用 `cards.CARDKIT_*` 是**自比较** ——
        # 第十一路审计实测「两个元素 id 都叫 answer」「面板 id 抄错字面量」两种变异四门禁全绿，
        # 而真机 `cardkit.v1.card.create` 对这两种形状都回 `300301`
        # （`Code 1001: Duplicate ID` / `Code 1002: elementID format error`）。
        # ⚠️ 顺序是**有意**的：装饰先写、**正文最后写**（提交点在后）—— 上游按「最后一次
        # **成功**发出的帧文本」记账，正文最后落盘才能让「这一帧失败」等价于「正文没更新」，
        # 否则回落补发的尾部会把同一段话再说一遍（R1 审计第③条）。
        # R2 之后的写入形态：**装饰一次 batch、正文一次 content** = 每帧 ≤2 次写（写入预算），
        # 而**装饰没变就不写** ⇒ 稳态下每帧 1 次。
        assert ids == ["answer"] * 4, f"正文只该走 content 通道：{ids}"
        # R3 收窄版：面板是 `panel_body`（推理）+ `panel_tools`（工具）两块，**都在首帧建出来**
        # ⇒ 首帧的装饰 batch 是三元素；之后只有内容变了的那一块才会再写（工具块在这个场景里
        # 一直没有工具事件，所以只出现首帧那一次 —— 「工具块变化只重写工具块」由
        # 本函数末尾的**场景 ㉕** 单独钉（原先这里写的是一个**并不存在**的函数名 ——
        # 2026-09-14 R3 代码审计按「文档说有、代码没有」的规矩抓出来的）。
        assert batch_ids == [["panel_body", "panel_tools", "footer"], ["footer"], ["panel_body"]], \
            f"装饰只在**内容变了**的元素上重写（顺序：变化的那一帧才发）：{batch_ids}"
        assert batch_seqs == [1, 4, 6], f"三次装饰 batch 的序号：{batch_seqs}"
        assert seqs == [2, 3, 5, 7], f"正文序号（装饰先、正文最后）：{seqs}"
        # R7 的会话列表预览：**建卡时就是对的**（`⏳ 正在生成…`），限频窗口内一次都不该多发
        # ——这条断言把「不许每帧刷 settings」钉在**四帧连续**的场景上（域见用例 ㉒）。
        assert calls["settings"] == [], \
            f"限频窗口内不该写预览（建卡时已经有了）：{calls['settings']}"
        # ⚠️ 这里**不许**再写「最后一帧页脚没变，所以 footer_written 最后一项是空列表」这种断言：
        #    同一件事已经由 `batch_ids` 精确钉住（最后一批只含 panel_body），那条断言是**空真**的
        #    —— R2 审计实测：删掉它 132/132 照样全绿，它只会给人一种「页脚去重被多条断言守住」的错觉。
        # 帧路径写出去的页脚 = 基数页脚 + **本卡短码**（R11-C2）。期望值在这里**显式拼**
        # 出来，不再调 `_ld_frame_footer` —— 那样等于「函数等于它自己」，零判别力。
        assert footer_written[1] == [f"{footer_after} · \U0001f516 m_ck_1"], \
            f"页脚变化的那一帧必须写**当前**页脚内容（含本卡短码）：{footer_written}"
        assert sorted(batch_seqs + seqs) == [1, 2, 3, 4, 5, 6, 7], \
            f"每次写入共用同一个严格递增序号（既不跳号也不撞号）：{sorted(batch_seqs + seqs)}"
        # 写入预算的**观测量**（附录 B 的第二条断言）：预算里的数字是算出来的，而这里是
        # 「真跑四帧、数一数 API 调用」——只锁算式的话，「每帧多写一次」这种实现照样全绿。
        assert calls["writes"] == 7, f"四帧一共 7 次写：{calls['writes']}"
        # ⚠️ 这个 7 是**跨源等式的一半**（R9 审计中-5 的「自证」教训）：上面那条账本断言
        # （四帧后 = 5）用的是「seed 1 + 元素帧 4」这个**写死**的推导，而这里独立地数
        # 「SDK 边界上到底发生了几次写」。两者一个来自我们的账面、一个来自调用账本 ——
        # 只有两个来源**都**成立，那条等式才不是「拿账本核账本」。
        assert calls["writes"] == 1 + 3 + 3, \
            f"四帧的写入构成（首帧 batch 2 元素 + content，中间帧各一次 content）：{calls['writes']}"
        assert calls["writes"] <= adapter._CK_WRITES_PER_FRAME * 4, \
            f"每帧写入次数不得超过预算 {adapter._CK_WRITES_PER_FRAME}：{calls['writes']}/4 帧"
        # 收尾帧走的是 `_ld_update_card`（= 普通 patch）—— 这里打桩记录，因为单测环境没有 SDK，
        # 而「收尾必须走 patch」正是 CardKit 设计的一部分（那一刻流式本来就结束）。
        finalized = []

        async def _record_update(chat_id, mid, card):
            finalized.append((chat_id, mid, card))
            return _StubResult(True, mid)

        raw._ld_update_card = _record_update
        assert _run(raw.send_stream_frame("正文一，正文二", finalize=True,
                                          chat_id="oc_ck", turn_id="t-ck"))
        assert len(finalized) == 1 and finalized[0][1] == "om_ck_1", finalized
        assert calls["patch"] == 0, "收尾不该写元素（应整卡替换）"
        # 收尾这一帧也要记账 —— 但这里把 `_ld_update_card` 换成了**录音替身**（见上），
        # 所以底层的收口点没跑：这一条**不能**在这里断言（会验成「替身记没记账」）。
        # 收尾帧的记账由 `test_ledger_counts_send_edit_stop_and_never_double_counts`
        # 用**真原语** + 真 patch 边界单独钉住（lessons 推论 19 的第三形态：别断言替身）。

        # ② 写**正文**元素失败（面板那条仍成功）⇒ 这一帧必须返回 False
        #    （核心据此停用 native 并回落 edit/send）。这一条专门堵「吞掉正文失败」那种变异：
        #    如果两个元素都失败，面板那一层的失败会把它掩盖过去。
        calls, client = _mk_fake(fail_answer_only=True)
        raw2 = _make()
        raw2._client = client
        adapter.configure(native_transport="cardkit")
        assert _run(raw2.send_stream_frame("", chat_id="oc_ck2", turn_id="t-2"))
        # ⚠️ 对偶断言（R1 审计 U4）：**正文失败时不该出现「面板元素写入失败」那条 WARNING**。
        #    只测「面板失败要留痕」是不够的 —— 把角色判据放宽成「任意失败」时，那条断言照样绿，
        #    而真机上「哪个元素死了」就再也分不清了。
        adapter._log_ck_panel_write_failed_once._at = 0.0
        with _LogCapture("larkdeck") as records:
            assert not _run(raw2.send_stream_frame("正文", chat_id="oc_ck2", turn_id="t-2")), \
                "写元素失败必须返回 False 让核心回落，绝不能吞掉"
        assert not any("面板元素写入失败" in r.getMessage() for r in records), \
            "正文失败不该被误诊成面板失败（角色判据被放宽了？）"

        # ②b **装饰失败 ⇒ 帧仍然成功**（R2 的失败分档，**有意改掉**旧行为）：
        #    旧行为是「面板失败 ⇒ 整帧 fail-open」，而 fail-open 的代价是上游会补一帧 finalize
        #    再落到 `_first_send` ⇒ **DM 里第二张卡**。装饰（面板/页脚）坏了不该买下这条路：
        #    处置改成 `DEAD`（标死 + 限流 WARNING + 本帧继续），正文照常落盘。
        #    ⚠️ 用例码的选择（R5 审计高-1 落地后调整）：`300309` 现在是**卡级死法** ⇒ 会走降级
        #    车道（见 ②g），所以这条「装饰失败 ⇒ 帧仍成功、坏元素不再重试」的用例改用
        #    **确定性拒收**码 `230099`（附录 A 里装饰那一格是 `DEAD`）。
        calls, client = _mk_fake(fail_batch=True, fail_batch_code=230099)
        raw2b = _make()
        raw2b._client = client
        adapter.configure(native_transport="cardkit", unified_panel=True)
        assert _run(raw2b.send_stream_frame("", chat_id="oc_ck2b", turn_id="t-2b"))
        adapter._log_ck_decor_write_failed_once._at = 0.0
        with _LogCapture("larkdeck") as records:
            assert _run(raw2b.send_stream_frame("正文", chat_id="oc_ck2b", turn_id="t-2b")), \
                "装饰失败不能让整帧失败（否则买下「DM 两张卡」那条链）"
        assert any("装饰写入失败" in r.getMessage() for r in records), \
            "装饰失败必须留下那条 WARNING（「正文在长、装饰冻结」的唯一线索）"
        assert any("整批标死" in r.getMessage() for r in records), \
            "msg 解析不出坏元素时必须如实说「整批标死」（别让人以为只死了一个）"
        # ⚠️ 告警必须**点名受影响的元素**（R5 审计低-10：把 ids 清空成 "" ⇒ 133/133 曾全绿，
        # 而那段清单正是这条告警存在的理由 —— 排查时要知道「冻了哪几个」）。
        # R3 收窄版：面板是**两块**（`panel_body` + `panel_tools`），所以整批标死时
        # 三个装饰元素都在名单里 —— 这条断言的字面量**必须跟着结构更新**
        # （而不是改成 `>= 2` 那种「随便几个都行」的写法：那样「少标死一个」就抓不住了）。
        assert all(x in r.getMessage() for r in records
                   for x in ("panel_body", "panel_tools", "footer")), \
            f"装饰失败的告警必须列出受影响的元素 id：{[r.getMessage() for r in records]}"
        state2b = raw2b._ld_stream_get("oc_ck2b:t-2b") or {}
        assert state2b.get("ck_dead") == {"panel_body", "panel_tools", "footer"}, \
            f"失败过的装饰元素必须被标死（后续不再尝试）：{state2b.get('ck_dead')}"
        # ⚠️ **装饰失败那一帧不许有任何「已写成功」记账**（R2 审计第 4 条）：把记账挪到 batch
        #    调用**之前**时，`ck_dead` 会把它掩蔽住 ⇒ 四门禁全绿；可一旦装饰能被复活（R3 起）
        #    那一行就变成真静默冻结（内容变了却因为「记过了」永不重写、无日志、不回落）。
        #    所以这里直接断言「失败帧之后 `ck_decor` 是空的」，让记错位置当场变红（变异 `R2-13`）。
        assert not state2b.get("ck_decor"), \
            f"写失败的装饰绝不能被记成「已写成功」：{state2b.get('ck_decor')}"
        # ⚠️ 正文**没有**因为是「装饰先写」而被拖累：这一帧的正文确实落盘了
        assert [c[0] for c in calls["content"]] == ["answer"], calls["content"]
        # 下一帧：装饰已标死 ⇒ **不再发 batch**，只写正文（省配额、也不再刷日志）
        assert _run(raw2b.send_stream_frame("正文二", chat_id="oc_ck2b", turn_id="t-2b"))
        assert len(calls["batch"]) == 1, f"标死的装饰不该再试：{calls['batch']}"
        assert [c[0] for c in calls["content"]] == ["answer", "answer"], calls["content"]

        # ②c **退避重试的调用放大效应**（R2 审计第 1 条）：`_CK_WRITES_PER_FRAME` 是**逻辑写**
        #    预算，不是「每帧最多 2 次 API 调用」—— 撞限流时每次逻辑写最多重发
        #    `len(_TRANSIENT_BACKOFF) + 1` 次。这个最坏值是**承诺的一部分**（文档口径写的就是它），
        #    所以钉成一个数：改退避表长度、或哪天有人把「每帧 2 次」当成 HTTP 上限来用，这里会红。
        #    ⚠️ 退避表在测试里换成全 0（否则本条用例要真的睡 2 秒 ×120 条变异）。
        saved_backoff = adapter._TRANSIENT_BACKOFF
        adapter._TRANSIENT_BACKOFF = (0.0, 0.0, 0.0)
        try:
            calls, client = _mk_fake(fail_batch=True, fail_batch_code=99991400,
                                     answer_codes=[99991400])
            raw2c = _make()
            raw2c._client = client
            adapter.configure(native_transport="cardkit", unified_panel=True)
            assert _run(raw2c.send_stream_frame("", chat_id="oc_ck2c", turn_id="t-2c")), "seed 帧"
            before = calls["writes"]
            assert not _run(raw2c.send_stream_frame("正文", chat_id="oc_ck2c", turn_id="t-2c")), \
                "限流耗尽后正文写仍然失败 ⇒ 这一帧必须 fail-open"
            attempts = adapter._TRANSIENT_BACKOFF + (0.0,)          # 每次逻辑写的尝试次数
            assert calls["writes"] - before == len(attempts) * 2, \
                (f"最坏情况：2 次逻辑写 × 每次 {len(attempts)} 次尝试 = {len(attempts) * 2} 次调用，"
                 f"实得 {calls['writes'] - before}")
            assert adapter._CK_WRITES_PER_SECOND == 10, "官方口径不能顺手改"
        finally:
            adapter._TRANSIENT_BACKOFF = saved_backoff

        # ③ 建实体失败 ⇒ seed 帧就返回 False（整回合回落，消息不会丢）
        calls, client = _mk_fake(create_ok=False)
        raw3 = _make()
        raw3._client = client
        adapter.configure(native_transport="cardkit")
        assert not _run(raw3.send_stream_frame("", chat_id="oc_ck3", turn_id="t-3"))

        # ④ 默认传输仍是 patch（这一条保证「翻默认」是个**显式决定**，不是顺手改坏）
        adapter.configure(native_transport="patch")
        assert adapter.LarkDeckMixin._ld_transport() == "patch"
        adapter.configure(native_transport="cardkit")
        assert adapter.LarkDeckMixin._ld_transport() == "cardkit"
        adapter.configure(native_transport="???")
        assert adapter.LarkDeckMixin._ld_transport() == "patch", "认不出的值按 patch（不猜）"

        # ②e **`DEGRADE` 车道**（R5）：元素通道拿到**卡级死法**（`300309` 会话已关 / `300313` /
        #    `300317`）时，**不许** fail-open —— 那会让内核停用本回合 native、用户看到纯文本。
        #    正确处置：用 `message.patch` 把**同一张卡**换成普通卡继续写（真机实测 patch 能覆盖
        #    CardKit 实体卡消息：`probe_ck_stream_ops.py --lanes`，`code=0` 且随后写元素得
        #    `300309` ⇒ 会话确实被替换掉了）。四条一起钉：本帧 True、patch 真的发了、
        #    **没有第二张卡**（create 仍为 1）、后续帧不再碰元素通道。
        #    ⚠️ **三个码都要跑**（R5 审计中-3：只测过 300309 ⇒ 把另外两个从集合里删掉四门禁全绿）。
        #    ⚠️ 必须写**字面量**：用 `sorted(adapter._CARD_DEATH_CODES)` 就是自比较 ——
        #    把表里的码删掉，循环也跟着少跑一圈，门禁照样全绿（R5 审计中-3，实测）。
        assert adapter._CARD_DEATH_CODES == frozenset({300309, 300313, 300317}), \
            f"卡级死法码表是实测出来的三个：{adapter._CARD_DEATH_CODES}"
        for _death in (300309, 300313, 300317):
            calls, client = _mk_fake(fail_at=2, fail_at_code=_death)
            raw2e = _make()
            raw2e._client = client
            adapter.configure(native_transport="cardkit", unified_panel=True)
            assert _run(raw2e.send_stream_frame("", chat_id="oc_ck2e", turn_id="t-2e")), "seed 帧"
            mid2e = (raw2e._ld_stream_get("oc_ck2e:t-2e") or {}).get("message_id")
            adapter._log_ck_degrade_once._at = 0.0
            with _LogCapture("larkdeck") as records:
                assert _run(raw2e.send_stream_frame("正文", chat_id="oc_ck2e", turn_id="t-2e")), \
                    f"卡级死法 {_death} 必须降级续写同一张卡，**不许** fail-open 掉卡片"
            assert any("降级为整卡 patch" in r.getMessage() for r in records), \
                f"降级必须留痕（「看起来正常、其实换了实现」的唯一线索）：{[r.getMessage() for r in records]}"
            assert calls["patch"] == 1, f"降级那一帧必须真的 patch 出去：{calls['patch']}"
            # ⚠️ **必须打在同一张卡上**（R5 审计高-2：只看次数的话，把目标换成别的 id 四门禁全绿）
            assert calls.get("patch_mids") == [mid2e], \
                f"降级 patch 必须打在原来那条 message_id 上（{mid2e}）：{calls.get('patch_mids')}"
            assert calls["create"] == 1, f"降级**绝不另建卡**（DM 里只能有一张卡）：{calls['create']}"
            state2e = raw2e._ld_stream_get("oc_ck2e:t-2e") or {}
            assert state2e.get("ck_degrade") == _death, \
                f"降级决定必须记进回合状态（要的是 {_death}）：{state2e}"
            assert not state2e.get("card_id"), f"降级后不许再走元素通道：{state2e.get('card_id')}"
            element_writes_after = len(calls["content"])
            assert _run(raw2e.send_stream_frame("正文二", chat_id="oc_ck2e", turn_id="t-2e"))
            assert len(calls["content"]) == element_writes_after, \
                "降级之后的帧不许再写元素（会话已关，写了就是每帧失败）"
            assert calls["patch"] == 2, f"降级之后每帧都该走 patch：{calls['patch']}"
            assert calls.get("patch_mids") == [mid2e, mid2e], calls.get("patch_mids")

        # ②g **装饰批量拿到卡级死法（会话已关）也必须降级**（R5 审计的高-1：旧代码只在**正文**
        #    写入失败时查 `_CARD_DEATH_CODES`）⇒ 否则后续每帧都往一个关掉的会话写正文，
        #    卡片静默冻在流式态。构造：`fail_batch` 用 `300309`（走 `_CARD_DEATH_DECOR_CODES`）。
        for _decor_death in sorted(adapter._CARD_DEATH_DECOR_CODES):
            calls, client = _mk_fake(fail_batch=True, fail_batch_code=_decor_death)
            raw2g = _make()
            raw2g._client = client
            adapter.configure(native_transport="cardkit", unified_panel=True)
            assert _run(raw2g.send_stream_frame("", chat_id="oc_ck2g", turn_id="t-2g"))
            mid2g = (raw2g._ld_stream_get("oc_ck2g:t-2g") or {}).get("message_id")
            assert _run(raw2g.send_stream_frame("正文", chat_id="oc_ck2g", turn_id="t-2g")), \
                "装饰撞上卡级死法时这一帧仍算成功（正文已落盘）"
            state2g = raw2g._ld_stream_get("oc_ck2g:t-2g") or {}
            assert state2g.get("ck_degrade") == _decor_death, \
                (f"装饰批量拿到卡级死法 {_decor_death} 时**必须**把降级决定写回状态："
                 f"{state2g.get('ck_degrade')}")
            assert not state2g.get("card_id"), "降级必须同时清掉 card_id"
            # ⚠️ 本帧算出来的标死也要落进状态（从 `live_state` 出发，不是旧 `state`）——
            #    否则「同一件事两处真相」（审计低-5），而这条断言是它唯一的可观测量。
            # R3 收窄版：面板两块（`panel_body` + `panel_tools`）都在装饰名单里
            assert state2g.get("ck_dead") == {"panel_body", "panel_tools", "footer"}, \
                f"降级那一帧标死的装饰不能被丢掉：{state2g.get('ck_dead')}"
            element_writes = len(calls["content"])
            assert _run(raw2g.send_stream_frame("正文二", chat_id="oc_ck2g", turn_id="t-2g"))
            assert len(calls["content"]) == element_writes, \
                "降级之后不许再写元素（否则每帧都在往关掉的会话写）"
            assert calls.get("patch_mids") == [mid2g] or calls["patch"] >= 1, calls
        # ②h **别的码的 msg 里出现 `elementID :` 时不许按名字标死**（R5 审计中-4 的误伤样本）：
        #    平台会拿这条 msg 去描述**上下文**（真凶写在后面），照解析就会标死一个好元素、
        #    放走真正坏的那个，而且日志会把人带到错方向。
        calls, client = _mk_fake(
            fail_batch=True, fail_batch_code=230099,
            fail_batch_msg="[230099] invalid params: elementID : panel_body content is too long,"
                           " (the offending field is footer)")
        raw2h = _make()
        raw2h._client = client
        adapter.configure(native_transport="cardkit", unified_panel=True)
        assert _run(raw2h.send_stream_frame("", chat_id="oc_ck2h", turn_id="t-2h"))
        adapter._log_ck_decor_write_failed_once._at = 0.0
        with _LogCapture("larkdeck") as records:
            assert _run(raw2h.send_stream_frame("正文", chat_id="oc_ck2h", turn_id="t-2h"))
        state2h = raw2h._ld_stream_get("oc_ck2h:t-2h") or {}
        assert state2h.get("ck_dead") == {"panel_body", "panel_tools", "footer"}, \
            (f"只有 `300313` 的 msg 才允许按名字标死；这条 230099 必须整批标死（保守）："
             f"{state2h.get('ck_dead')}")
        assert any("整批标死" in r.getMessage() for r in records), \
            f"没按名字标死时必须如实说「整批标死」：{[r.getMessage() for r in records]}"

        # 对照：**`300313`（元素不存在）不该让装饰把整条通道拖死** —— 那只是某一个装饰元素没了
        # （msg 点名它），正文元素是另一个 id ⇒ 只标死它、保住打字机（`_CARD_DEATH_DECOR_CODES`
        # 故意比 `_CARD_DEATH_CODES` 少一个码，用例 ②d 覆盖）。

        # ②f **撤回守卫**（R5）：整卡写入拿到 `230011 The message was withdrawn.`（真机实测，
        #    R0 的 P4）⇒ 标死这条 message_id + 清追踪 + 留痕，**绝不自己补发**（补发 = DM 两张卡）。
        #    这条守卫只可能在整卡路径上生效：元素写入对撤回**无感**（P4 实测 code=0）。
        calls, client = _mk_fake(patch_code=230011)
        raw2f = _make()
        raw2f._client = client
        adapter.configure(native_transport="cardkit", unified_panel=True)
        assert _run(raw2f.send_stream_frame("", chat_id="oc_ck2f", turn_id="t-2f"))
        assert _run(raw2f.send_stream_frame("正文", chat_id="oc_ck2f", turn_id="t-2f"))
        mid2f = (raw2f._ld_stream_get("oc_ck2f:t-2f") or {}).get("message_id")
        assert mid2f and raw2f._ld_known(mid2f), "前提：收尾之前这张卡是被追踪的"
        adapter._log_withdrawn_once._at = 0.0
        with _LogCapture("larkdeck") as records:
            assert not _run(raw2f.send_stream_frame("正文", finalize=True,
                                                     chat_id="oc_ck2f", turn_id="t-2f")), \
                "消息已撤回 ⇒ 收尾必须返回失败（让核心按 fail-open 决定怎么送达）"
        assert any("已被撤回/删除" in r.getMessage() for r in records), \
            "撤回必须留痕（否则「卡片怎么没了」完全无迹可寻）"
        assert not raw2f._ld_known(mid2f), "撤回之后必须忘掉这条 message_id（不再往它写）"
        assert raw2f._ld_stream_get("oc_ck2f:t-2f") is None, "撤回之后回合状态也必须清掉"
        assert calls["create"] == 1 and calls["send"] == 1, \
            f"我们自己绝不补发（那会变成 DM 两张卡）：{calls}"
        assert calls["patch"] == 1, \
            f"撤回之后**不许再往这条 message_id 写**（一次 patch 都不该多）：{calls['patch']}"

        # ㉒ **R7 的会话列表预览**（`card.settings`）：限频 + i18n 对象 + 共用序号 + 失败不致命。
        #    这条路的纪律和元素写入**不一样**，所以必须单独钉：
        #      * 它是**额外**的一次逻辑写 ⇒ 只能靠限频摊薄（我们直接把窗口设成 0 来逼它每帧发，
        #        再用大窗口验「真的会跳过」）；
        #      * `summary` 必须是 **i18n 对象**（传裸字符串真机回 `300122`）；
        #      * 它**吃序号**（与元素写入共用账本），跳过时不许占号；
        #      * 失败只标死 + 限流 WARNING，**绝不让这一帧失败**（fail-open 会让整回合掉纯文本）。
        _saved_interval = adapter._CK_SUMMARY_INTERVAL
        adapter._CK_SUMMARY_INTERVAL = 0.0
        try:
            calls, client = _mk_fake()
            raw2i = _make()
            raw2i._client = client
            adapter.configure(native_transport="cardkit", unified_panel=True)
            assert _run(raw2i.send_stream_frame("", chat_id="oc_ck2i", turn_id="t-2i"))
            assert calls["settings"] == [], "建卡时已有 summary ⇒ 第一帧不该再写一次"
            assert _run(raw2i.send_stream_frame("第一段正文", chat_id="oc_ck2i", turn_id="t-2i"))
            assert len(calls["settings"]) == 1, f"窗口到了就该写一次预览：{calls['settings']}"
            payload, seq, uuid_value = calls["settings"][0]
            assert isinstance(payload.get("config", {}).get("summary"), dict), \
                f"summary 必须是 i18n **对象**（裸字符串真机回 300122）：{payload}"
            assert isinstance(payload["config"]["summary"].get("content"), str), payload
            assert "第一段正文" in payload["config"]["summary"]["content"], payload
            # ⚠️ **必须断言 uuid 的确切形状**（R7 审计中-3 的第 ⑧ 条）：原来这里是
            # `assert uuid_value` —— 而它来自 f-string，card_id 非空 ⇒ **近似恒真**，
            # 判不出「每次写的去重键必须不同」。去重键退化（例如写成常量）会让服务端把
            # 每一次新预览都当成重发 ⇒ 列表那行永远停在第一条。
            _cid2i = (raw2i._ld_stream_get("oc_ck2i:t-2i") or {}).get("card_id")
            assert uuid_value == f"ld-{_cid2i}-s{seq}", \
                f"预览的去重键必须是 (卡, 序号) 的确定性函数：{uuid_value!r}"
            # 序号共用：这一帧的元素写入占 1、2；预览占 3
            assert seq == 3, f"预览必须与元素写入共用账本（这里该是 3）：{seq}"
            ledger = ([b[1] for b in calls["batch"]] + [c[2] for c in calls["content"]]
                      + [c[1] for c in calls["settings"]])
            assert sorted(ledger) == [1, 2, 3], f"共用同一个严格递增账本：{sorted(ledger)}"
            # 正文没变 ⇒ 不留痕、不重发（这一帧被文本去重挡在前面，属正常路径）
            # 正文变了 ⇒ 再写一次，序号继续往上
            assert _run(raw2i.send_stream_frame("第一段正文，第二段", chat_id="oc_ck2i", turn_id="t-2i"))
            assert len(calls["settings"]) == 2, f"正文变了就该更新预览：{calls['settings']}"
            assert calls["settings"][-1][1] > seq, "序号必须继续递增"

            # 大窗口 ⇒ 一次都不写（限频真的生效；这条是 R7-5 变异的判据）
            calls, client = _mk_fake()
            raw2j = _make()
            raw2j._client = client
            adapter._CK_SUMMARY_INTERVAL = 3600.0
            adapter.configure(native_transport="cardkit", unified_panel=True)
            assert _run(raw2j.send_stream_frame("", chat_id="oc_ck2j", turn_id="t-2j"))
            for _t in ("一", "一，二", "一，二，三"):
                assert _run(raw2j.send_stream_frame(_t, chat_id="oc_ck2j", turn_id="t-2j"))
            assert calls["settings"] == [], \
                f"限频窗口没到就绝不许写预览（每帧写会超写入预算）：{calls['settings']}"

            # 失败：只标死 + WARNING，**帧仍然成功**、后续不再重试
            adapter._CK_SUMMARY_INTERVAL = 0.0
            calls, client = _mk_fake(settings_code=300313)
            raw2k = _make()
            raw2k._client = client
            adapter.configure(native_transport="cardkit", unified_panel=True)
            assert _run(raw2k.send_stream_frame("", chat_id="oc_ck2k", turn_id="t-2k"))
            adapter._log_ck_summary_failed_once._at = 0.0
            with _LogCapture("larkdeck") as records:
                assert _run(raw2k.send_stream_frame("正文", chat_id="oc_ck2k", turn_id="t-2k")), \
                    "预览写失败**不许**让这一帧失败（fail-open 会让整回合掉成纯文本）"
            assert any("会话预览" in r.getMessage() for r in records), \
                f"预览失败必须留痕：{[r.getMessage() for r in records]}"
            state2k = raw2k._ld_stream_get("oc_ck2k:t-2k") or {}
            assert state2k.get("ck_summary_dead") is True, f"失败后必须标死：{state2k}"
            _before = len(calls["settings"])
            assert _run(raw2k.send_stream_frame("正文二", chat_id="oc_ck2k", turn_id="t-2k"))
            assert len(calls["settings"]) == _before, "标死之后不许再试（省配额、也不再刷日志）"
        finally:
            adapter._CK_SUMMARY_INTERVAL = _saved_interval

        # ⚠️ **窗口的取值本身也要钉住**（R7 审计的「仍未收口」第 2 条）：它是**预算决策**
        # （5 秒 ⇒ 平均 ≈0.2 次/秒，见附录 B），而 5.0 → 1.0 这种改动四门禁原本全绿。
        # 与 `test_declared_defaults_are_an_explicit_decision` 同一个理由：显式决定要显式钉住。
        assert adapter._CK_SUMMARY_INTERVAL == 5.0, \
            f"会话预览的限频窗口是显式预算决策（≈0.2 次/秒），改它要同时改附录 B：" \
            f"{adapter._CK_SUMMARY_INTERVAL}"

        # ㉓ **预览的三条硬纪律**（R7 审计的 H-1 / M-2 / L-7）—— 每一条都有一个「撤掉修复
        #    四门禁全绿」的实测反例，所以逐条钉：
        #      * 限频靠**记账**（`ck_summary_at`）生效；不记账 ⇒ 之后每帧都写（配额被吃掉）；
        #      * 写调用**抛异常**时，这一帧**仍须成功**（否则核心停用本回合 native ⇒ 掉纯文本）；
        #      * 限流码**不重试**（它排在提交点之后，失败只标死，重试只是白等 ≤1s）。
        adapter._CK_SUMMARY_INTERVAL = 1.0
        calls, client = _mk_fake()
        raw2n = _make()
        raw2n._client = client
        adapter.configure(native_transport="cardkit", unified_panel=True)
        assert _run(raw2n.send_stream_frame("", chat_id="oc_ck2n", turn_id="t-2n"))
        # 建卡时 `ck_summary_at` 记成「刚刚」⇒ 第一帧不写（窗口没到）
        assert calls["settings"] == [], "建卡那一刻刚写过 summary ⇒ 第一帧不该再写"
        # 手动把窗口拨到过去（真机上是「过了 1 秒」），此后**只靠那个戳**决定要不要写
        raw2n._ld_streams["oc_ck2n:t-2n"]["ck_summary_at"] = time.monotonic() - 2.0
        for _t in ("一", "一，二", "一，二，三", "一，二，三，四", "一，二，三，四，五",
                   "一，二，三，四，五，六", "一，二，三，四，五，六，七",
                   "一，二，三，四，五，六，七，八"):
            assert _run(raw2n.send_stream_frame(_t, chat_id="oc_ck2n", turn_id="t-2n")), _t
        assert len(calls["settings"]) == 1, \
            f"拨一次窗口 ⇒ 只能写一次预览（限频靠 ck_summary_at 记账）：{calls['settings']}"
        assert calls["writes"] <= adapter._CK_WRITES_PER_FRAME * 8 + 1, \
            f"八帧的写入总量不得超过「每帧预算 × 帧数 + 一次预览」：{calls['writes']}"

        # 异常：这一帧必须成功 + 只标死 + **序号照样落账**（那次写真实发生过了）
        calls, client = _mk_fake(settings_raises=True)
        raw2o = _make()
        raw2o._client = client
        adapter.configure(native_transport="cardkit", unified_panel=True)
        assert _run(raw2o.send_stream_frame("", chat_id="oc_ck2o", turn_id="t-2o"))
        raw2o._ld_streams["oc_ck2o:t-2o"]["ck_summary_at"] = time.monotonic() - 2.0
        assert _run(raw2o.send_stream_frame("正文", chat_id="oc_ck2o", turn_id="t-2o")), \
            "预览写**抛异常**时这一帧也必须成功（否则核心停用 native ⇒ 用户掉成纯文本）"
        state2o = raw2o._ld_stream_get("oc_ck2o:t-2o") or {}
        assert state2o.get("ck_summary_dead") is True, f"异常同样只标死：{state2o}"
        assert state2o.get("ck_seq") == 3, \
            f"异常路径也要把序号落账（元素写 1、2 + 预览 3）：{state2o.get('ck_seq')}"

        # 不重试：限流码只试一次（`_TRANSIENT_BACKOFF` 那是给元素写入的，不是给预览的）
        calls, client = _mk_fake(settings_code=99991400)
        raw2p = _make()
        raw2p._client = client
        adapter.configure(native_transport="cardkit", unified_panel=True)
        assert _run(raw2p.send_stream_frame("", chat_id="oc_ck2p", turn_id="t-2p"))
        raw2p._ld_streams["oc_ck2p:t-2p"]["ck_summary_at"] = time.monotonic() - 2.0
        assert _run(raw2p.send_stream_frame("正文", chat_id="oc_ck2p", turn_id="t-2p")), \
            "限流码同样不许让这一帧失败"
        assert len(calls["settings"]) == 1, \
            f"预览不重试（重试只是白等 ≤1s）：写了 {len(calls['settings'])} 次"
        state2p = raw2p._ld_stream_get("oc_ck2p:t-2p") or {}
        assert state2p.get("ck_summary_dead") is True, state2p

        # ㉔ **R4 卡链**：正文涨到单卡容量 ⇒ 封旧卡 + 开新卡，新卡只显示**剩下的那一段**。
        #    五件事一起钉：① 超过阈值才切；② 封旧卡是**整卡 patch + streaming=False** 且带
        #    「（续下一条）」；③ 新卡正文 == `text[cut:]`（**不重放**前半段）；④ 之后的帧继续写
        #    **新卡**；⑤ 收尾与降级车道同样只写本卡那一段。
        #    ⚠️ 正文里必须放**可辨认的标记**：整段同一个字时，「不许重放」这类断言会被子串匹配
        #    顺带满足（⑭ 的旧版就是这么假绿的 —— 恒真断言的又一个形态）。
        _saved_split_at = adapter._CK_SPLIT_SEAL_AT
        adapter._CK_SPLIT_SEAL_AT = 3000      # 压小阈值，用几千字就能触发（真机是 64000 字节）
        try:
            adapter.LarkDeckMixin._ld_ck_requests = staticmethod(_fake_requests)
            calls, client = _mk_fake()
            raw2r = _make()
            raw2r._client = client
            adapter.configure(native_transport="cardkit", unified_panel=True)
            adapter._STREAM_MIN_INTERVAL = 0.0
            assert _run(raw2r.send_stream_frame("", chat_id="oc_ck2r", turn_id="t-2r"))
            assert calls["create"] == 1, "seed 建第一张卡"
            head = "前段标记" + "甲" * 950
            assert _run(raw2r.send_stream_frame(head, chat_id="oc_ck2r", turn_id="t-2r"))
            assert calls["create"] == 1, "没超过阈值就不该切卡"
            # 再涨一点、跨过（被压小的）阈值：切完之后**尾巴也在阈值以内** ⇒ 下一帧不该再切
            # （尾巴比阈值还大时再切一次是**合法**的：卡链一段段收窄，见 `_ld_ck_split` 的注释）
            # ⚠️ 两段之间**必须有换行**：切点会优先落在换行上，只有这样「封卡里不含后半段标记」
            # 才是一条有判别力的断言（没有换行时切点会落在后半段标记中间，断言就变成假红）
            full = head + "\n\n" + "后段标记" + "乙" * 400
            assert _run(raw2r.send_stream_frame(full, chat_id="oc_ck2r", turn_id="t-2r")), \
                "跨过阈值那一帧必须成功（切卡是它的职责）"
            assert calls["create"] == 2, f"应当建出第二张卡：{calls['create']}"
            state2r = raw2r._ld_stream_get("oc_ck2r:t-2r") or {}
            cut = int(state2r.get("ck_offset") or 0)
            assert 0 < cut < len(full), f"切点必须在正文中间：{cut} / {len(full)}"
            assert state2r.get("ck_cards"), f"封掉的卡要记进 ck_cards：{state2r}"
            assert state2r.get("card_id") == "ck_2", f"状态要指向新卡：{state2r.get('card_id')}"
            # ① 封旧卡：整卡 patch、内容 = 前半段 + 「续下一条」、**并且关掉了流式态**
            sealed = calls["patch_cards"][-1]
            assert calls["patch_mids"][-1] == "om_ck_1", f"封的是第一张卡：{calls['patch_mids']}"
            sealed_body = json.dumps(sealed, ensure_ascii=False)
            assert not sealed.get("config", {}).get("streaming_mode"), \
                f"封掉的卡必须关掉流式态（否则永远停在「正在生成」）：{sealed_body[:200]}"
            assert "前段标记" in sealed_body and i18n.t("stream.continued") in sealed_body, \
                "封卡内容必须是**前半段** + 续下一条提示"
            assert "后段标记" not in sealed_body, "封卡里不许出现后半段（那是新卡的内容）"
            # ② 新卡正文 == text[cut:]（**不重放**前半段）
            new_answers = [c[1] for c in calls["content"] if c[0] == cards.CARDKIT_ANSWER_ID]
            assert new_answers[-1] == full[cut:], \
                f"新卡只显示剩下的那一段：{new_answers[-1][:40]!r} vs {full[cut:][:40]!r}"
            assert "前段标记" not in new_answers[-1], "新卡不许重放前半段（R4 最大的观感坑）"
            # ③ 切完之后的帧继续写**新卡**、只写增量
            grown = full + "丙" * 100
            assert _run(raw2r.send_stream_frame(grown, chat_id="oc_ck2r", turn_id="t-2r"))
            assert calls["create"] == 2, "没再超阈值就不该再切"
            new_answers = [c[1] for c in calls["content"] if c[0] == cards.CARDKIT_ANSWER_ID]
            assert new_answers[-1] == grown[cut:], new_answers[-1][:40]
            # ④ patch 车道（`card_id` 被清空 ⇒ 之后每帧走整卡 patch）同样只写本卡那一段
            raw2r._ld_streams["oc_ck2r:t-2r"]["card_id"] = ""
            grown2 = grown + "丁" * 50
            assert _run(raw2r.send_stream_frame(grown2, chat_id="oc_ck2r", turn_id="t-2r"))
            lane_body = json.dumps(calls["patch_cards"][-1], ensure_ascii=False)
            assert grown2[cut:] in lane_body and "前段标记" not in lane_body, \
                "降级车道也不许重放前半段"
            # ⑤ **元素通道拿到卡级死法**（300309）那一帧走的是降级分支（整卡 patch 续写同一张卡）：
            #    它也**只写本卡那一段**（写整段会把封掉的前半段重放一遍）
            _orig_write = raw2r._ld_ck_write

            async def _die_on_answer(card_id, element_id, content, sequence):
                if element_id == cards.CARDKIT_ANSWER_ID:
                    return adapter._CkResult(False, 300309, "session closed")
                return await _orig_write(card_id, element_id, content, sequence)

            raw2r._ld_streams["oc_ck2r:t-2r"]["card_id"] = "ck_2"
            raw2r._ld_ck_write = _die_on_answer
            grown3 = grown2 + "戊" * 30
            try:
                assert _run(raw2r.send_stream_frame(grown3, chat_id="oc_ck2r", turn_id="t-2r")), \
                    "卡级死法要降级续写（不是 fail-open）"
            finally:
                raw2r._ld_ck_write = _orig_write
            degrade_body = json.dumps(calls["patch_cards"][-1], ensure_ascii=False)
            assert grown3[cut:] in degrade_body and "前段标记" not in degrade_body, \
                "降级分支同样不许重放前半段"
            # R3 收窄版：降级走的是**普通卡**（`unified_panel`，面板 id 是 `auxiliary_timeline`）
            # ⇒ 这句话只该是普通卡的面板，绝不许把实体卡那两块（`panel_body`/`panel_tools`）
            # 塞进来。这是**反向断言**（AGENTS/CHANGELOG 里说过「三条车道有反向断言」——
            # 2026-09-14 审计实测只有收尾那一条真有，这条就是补上的另一半）。
            assert cards.CARDKIT_PANEL_TOOLS_ID not in degrade_body \
                and cards.CARDKIT_PANEL_BODY_ID not in degrade_body, \
                "降级车道用的是普通卡，不该带实体卡的面板元素"

            # ⑥ 收尾：封的是**最新那张**，内容同样是本卡那一段
            raw2r._ld_streams["oc_ck2r:t-2r"]["card_id"] = "ck_2"
            raw2r._ld_streams["oc_ck2r:t-2r"]["card_id"] = "ck_2"
            patched_before = len(calls["patch_cards"])
            assert _run(raw2r.send_stream_frame(grown2, finalize=True, chat_id="oc_ck2r",
                                                turn_id="t-2r"))
            assert len(calls["patch_cards"]) == patched_before + 1
            final_body = json.dumps(calls["patch_cards"][-1], ensure_ascii=False)
            assert grown2[cut:] in final_body and "前段标记" not in final_body, \
                "收尾帧只写本卡那一段"
        finally:
            adapter._CK_SPLIT_SEAL_AT = _saved_split_at

        # ⑤ SDK 取不到（单测/裁剪环境）⇒ 建实体返回 None ⇒ **fail-open**，绝不抛
        adapter.LarkDeckMixin._ld_ck_requests = staticmethod(lambda: None)
        calls, client = _mk_fake()
        raw4 = _make()
        raw4._client = client
        adapter.configure(native_transport="cardkit")
        assert not _run(raw4.send_stream_frame("", chat_id="oc_ck4", turn_id="t-4")), \
            "没有 SDK 时必须 fail-open 返回 False（让核心回落），不能抛"

        # ⑥ `unified_panel: false` ⇒ **面板元素压根不进卡**，帧里也只写正文那一个元素。
        #    这一条同时钉两件事：① README 承诺「两条传输下都关得掉面板」是真的；
        #    ② 面板关掉后**绝不能**再去写 `panel_body` 的 id —— 卡片里没有那个元素，
        #    真机会回 `300313`，于是每一帧都失败、整个回合被打回纯文本（比面板丑严重得多）。
        adapter.LarkDeckMixin._ld_ck_requests = staticmethod(_fake_requests)
        calls, client = _mk_fake()
        raw5 = _make()
        raw5._client = client
        adapter.configure(native_transport="cardkit", unified_panel=False)
        assert _run(raw5.send_stream_frame("", chat_id="oc_ck5", turn_id="t-5"))
        # 建实体时发的 JSON 才是**结构**的唯一证据（之后只能按 id 写内容，改不了结构）
        assert calls["entity"], "⑥ 前提：建实体的请求必须被记录下来"
        entity = json.loads(calls["entity"][0])
        # ⚠️ `streaming_mode` 是**打字机的总开关**，也是这次翻默认的**唯一理由** ——
        # 而它此前零门禁（第十二路审计：改成 False / 删掉这个键，四门禁全绿）。
        assert entity["config"]["streaming_mode"] is True, \
            f"实体卡必须开流式会话，否则逐字打字机整个失效：{entity['config']!r}"
        assert entity["config"].get("update_multi") is True, entity["config"]
        elem_ids = [e.get("element_id") for e in entity["body"]["elements"]]
        # R2 起：`footer: true`（默认）⇒ 卡里多一个页脚元素（流式期间就能更新它）。
        # 面板关掉时它也不该出现 —— 判据是**配置**，不是「这一刻有没有数据」。
        assert elem_ids == ["answer", "footer"], \
            f"unified_panel: false 时面板元素不该进卡：{elem_ids}"
        assert _run(raw5.send_stream_frame("正文一", chat_id="oc_ck5", turn_id="t-5"))
        assert _run(raw5.send_stream_frame("正文一，正文二", chat_id="oc_ck5", turn_id="t-5"))
        sent_ids = [c[0] for c in calls["content"]]
        assert sent_ids == ["answer", "answer"], sent_ids
        # 面板关掉后装饰只剩页脚，而页脚内容两帧都没变 ⇒ **只写第一次**（未变化不重写）
        assert [b[1] for b in calls["batch"]] == [1], calls["batch"]

        # ⑥b **`footer: false` ⇒ 页脚元素同样不进卡**，而且一次都不写它的 id
        #    （写卡里不存在的元素 ⇒ 真机 300313 ⇒ 每帧失败 ⇒ 整回合打回纯文本）。
        #    ⚠️ 必须用**自己的一套替身**：第一版复用了 ⑥ 的 `calls`，于是 ⑥ 后面的断言
        #    看到的是 ⑥b 的请求（顺序与隔离都错了，用例当场红）。
        calls5b, client5b = _mk_fake()
        raw5b = _make()
        raw5b._client = client5b
        adapter.configure(native_transport="cardkit", unified_panel=False, footer=False)
        assert _run(raw5b.send_stream_frame("", chat_id="oc_ck5b", turn_id="t-5b"))
        entity5b = json.loads(calls5b["entity"][0])
        ids5b = [e.get("element_id") for e in entity5b["body"]["elements"]]
        assert ids5b == ["answer"], f"footer: false 时页脚不该进卡：{ids5b}"
        assert _run(raw5b.send_stream_frame("正文一", chat_id="oc_ck5b", turn_id="t-5b"))
        written = ([c[0] for c in calls5b["content"]]
                   + [a["params"]["element_id"] for b in calls5b["batch"] for a in b[0]])
        assert written == ["answer"], f"关掉面板与页脚时只该写正文：{written}"
        adapter.configure(footer=True)
        sent_seqs = [c[2] for c in calls["content"]]
        # R2 起：装饰（这里只剩页脚）走 batch 占 1 号、且**没变就不重写** ⇒ 正文是 2、3 号
        assert sent_seqs == [2, 3], sent_seqs
        # 面板关掉后**任何通道**都不许碰 panel_body（写了不存在的元素 ⇒ 真机 300313
        # ⇒ 每帧失败 ⇒ 整回合打回纯文本）。注意现在要连 batch 的 action 一起查 ——
        # 只查 content 那条通道的话，面板改走 batch 之后这条断言会变成**空断言**。
        touched = ([c[0] for c in calls["content"]]
                   + [a["params"]["element_id"] for b in calls["batch"] for a in b[0]])
        assert cards.CARDKIT_PANEL_BODY_ID not in touched, \
            f"面板关掉后不该出现 panel_body（任何通道）：{touched}"

        # ②d **装饰批量失败时，标死范围要尽量收窄**（R5，真机实测支撑）：批级返回码能反映坏 id，
        #    而且 **msg 会点名它**（`--lanes` 实测 `code=300313`、
        #    `msg='ErrMsg: not find elementID : <id>; '`）⇒ 解析得出就只标那一个，
        #    其余装饰下一帧照常写；解析不出才退回「整批标死」（R2 审计第 3 条的保守处置）。
        calls, client = _mk_fake(fail_batch=True, fail_batch_code=300313,
                                 fail_batch_msg="ErrMsg: not find elementID : footer; ")
        raw2d = _make()
        raw2d._client = client
        adapter.configure(native_transport="cardkit", unified_panel=True)
        assert _run(raw2d.send_stream_frame("", chat_id="oc_ck2d", turn_id="t-2d"))
        adapter._log_ck_decor_write_failed_once._at = 0.0
        with _LogCapture("larkdeck") as records:
            assert _run(raw2d.send_stream_frame("正文", chat_id="oc_ck2d", turn_id="t-2d")), \
                "装饰失败不能让整帧失败"
        assert any("只标死它" in r.getMessage() for r in records), \
            f"msg 点名了坏元素时必须如实说「只标死它」：{[r.getMessage() for r in records]}"
        state2d = raw2d._ld_stream_get("oc_ck2d:t-2d") or {}
        assert state2d.get("ck_dead") == {"footer"}, \
            f"只该标死被点名的 footer，panel_body 必须留着：{state2d.get('ck_dead')}"
        # ⚠️ 光看 `ck_dead` 不够：还要证明**面板下一帧真的又被写了**（否则「只标一个」只是状态好看）
        calls["batch"].clear()
        assert _run(raw2d.send_stream_frame("正文二", chat_id="oc_ck2d", turn_id="t-2d"))
        # R3 收窄版：面板两块（`panel_body` + `panel_tools`）。⚠️ 上一次 batch **失败**过 ⇒
        # 两块都**没有**进 `ck_decor` 记账（记账只在成功分支）⇒ 这一帧两块都还在 fresh 集合里，
        # 于是字面量是两块 —— 这不是「footer 没死」：footer 已从计划里被 `live_elems` 过滤掉。
        assert [[a["params"]["element_id"] for a in b[0]] for b in calls["batch"]] \
            == [["panel_body", "panel_tools"]], \
            f"被点名的 footer 死了，但面板两块必须继续写：{calls['batch']}"

        # ⑫ **回复锚点必须透传** + 发实体卡必须带**确定性 uuid**（第十二轮审计实测：
        #    锚点被整个丢掉、且两条 uuid 一个都没填）。
        #    核心每次调 `send_stream_frame` 都带 `reply_to`（生产上恒有值）——丢了它，
        #    patch 传输会「引用回复你的消息」而 cardkit 变成一条顶层新消息：用户可见的
        #    行为差异，且话题群里可能自成一条新话题。uuid 则是退避重试的去重键：
        #    缺了它，「响应丢了但实际发成功」会让 DM 里多一张永远停在「正在生成…」的卡。
        calls, client = _mk_fake()
        raw12 = _make()
        raw12._client = client
        adapter.configure(native_transport="cardkit")
        assert _run(raw12.send_stream_frame("", chat_id="oc_ck13", turn_id="t-13",
                                            reply_to="om_user_msg")), "带锚点的 seed 帧必须成功"
        assert calls["reply"] == 1 and calls["send"] == 0, \
            f"有 reply_to 时必须走 message.reply（不能发顶层新消息）：{calls}"
        assert calls["reply_req"][0].reply_to == "om_user_msg", calls["reply_req"][0]
        _uuid = calls["reply_req"][0].request_body.uuid
        assert _uuid, "发实体卡必须带 uuid（重试去重键）"
        assert "ck_1" in _uuid, f"uuid 必须由 card_id 推出（同一次发送重试时不变）：{_uuid}"

        # ⑫b 没有锚点时仍走顶层发送（不能为了锚点把「第一次回答」变成回复一条不存在的消息）
        calls, client = _mk_fake()
        raw13 = _make()
        raw13._client = client
        adapter.configure(native_transport="cardkit")
        assert _run(raw13.send_stream_frame("", chat_id="oc_ck14", turn_id="t-14"))
        assert calls["send"] == 1 and calls["reply"] == 0, calls

        # ⑮ **序号只增不减**（R1 的规矩，第十二路审计给的）：一帧失败之后，**同一条流**上的
        #    下一帧必须用**更大**的序号。回退序号会让下一帧撞上同一个 uuid（uuid 由
        #    (卡,元素,序号) 推出）⇒ 服务端按去重键处理 ⇒ 可能返回 0 但内容没变（静默半更新）；
        #    写死或跳号则撞 R0 实测的「严格递增」⇒ `300317` ⇒ 整回合掉 native。
        #    R0 真机探针：跳号与撞号都得 `300317`。
        #    ⚠️ 必须**在同一条流上**验：第一版后半段换了个新实例从 0 重新开始，
        #    验的其实是「首帧写 1、2 号」，与失败路径无关（R1 审计 W5 实测两条绕过改法全绿）。
        # ⚠️ 失败点必须落在**序号 > 1** 的地方：第一版让它失败在第 1 号，于是「把序号写死成 1」
        #    这条变异**行为等价**（W5 全绿）。改成第 3 号（前两号已经成功）才逼出判别力。
        # 第 4 次写入 = 第二帧的**正文**（第 3 次是装饰 batch；装饰失败在新语义下不让帧失败）
        calls, client = _mk_fake(fail_at=4, fail_at_code=230099)
        raw15 = _make()
        raw15._client = client
        # ⚠️ 必须**显式**写 `unified_panel=True`：这一段跑在前面那些用例之后，而 ⑥ 把
        # `unified_panel` 设成了 False（元素表里就没有面板），不写清楚期望会随执行顺序漂移。
        adapter.configure(native_transport="cardkit", unified_panel=True)
        assert _run(raw15.send_stream_frame("", chat_id="oc_ck16", turn_id="t-16")), "seed 帧"
        # ⚠️ R2 起「**未变化不重写**」：装饰没变的那一帧只有 1 次写（正文），失败点会整体前移，
        #    这条用例的前提（第 4 次写 = 第二帧的正文）就不成立了。所以每帧之间要让装饰
        #    **真的变一次** —— 用真页脚数据层驱动（`context`），不是替身。
        def _move_footer(tokens: int, why: str) -> str:
            before = raw15._ld_footer() or ""
            context.record_api_call(model="test-model",
                                    usage={"input_tokens": tokens, "output_tokens": 4})
            context.set_context_override(10000)
            after = raw15._ld_footer() or ""
            assert after and after != before, f"{why}：页脚必须真的变了（{before!r} → {after!r}）"
            return after

        _move_footer(1500, "⑮ 前提")
        assert _run(raw15.send_stream_frame("第一帧", chat_id="oc_ck16", turn_id="t-16")), \
            "前提：第一帧要成功（用掉 1、2 号）"
        _move_footer(2500, "⑮ 前提")
        assert not _run(raw15.send_stream_frame("第二帧", chat_id="oc_ck16", turn_id="t-16")), \
            "前提：第二帧的正文写入确实失败了"
        state_after_fail = raw15._ld_stream_get("oc_ck16:t-16") or {}
        assert state_after_fail.get("ck_seq") == 4, \
            f"失败的那一帧也必须把序号推进到 4（写死/回退都会让下一帧撞号）：{state_after_fail}"
        # 失败帧之后**在同一条流上**继续：必须从 4 号之后接着来
        _move_footer(3500, "⑮ 前提")
        assert _run(raw15.send_stream_frame("第三帧", chat_id="oc_ck16", turn_id="t-16")), \
            "失败之后下一帧应当还能正常写"
        ledger = sorted([b[1] for b in calls["batch"]] + [c[2] for c in calls["content"]])
        assert ledger == [1, 2, 3, 4, 5, 6], \
            f"装饰与正文共用同一个严格递增序号，失败也不回退：实得 {ledger}"
        context.reset()
        context.set_context_override(None)

        # ⑯ **空元素表是契约违反**（R1 审计 U1）：元素表为空（或表里全是卡里没有的 id）时，
        #    返回成功会让核心以为一切正常 ⇒ 卡片静默冻死、一行日志都没有、也不回落。
        #    必须按失败处理（核心据此回落 edit/send）。
        calls, client = _mk_fake()
        raw17 = _make()
        raw17._client = client
        adapter.configure(native_transport="cardkit", unified_panel=True)
        assert _run(raw17.send_stream_frame("", chat_id="oc_ck18", turn_id="t-18"))
        raw17._ld_stream_put("oc_ck18:t-18", {
            **(raw17._ld_stream_get("oc_ck18:t-18") or {}), "ck_elems": []})
        assert not _run(raw17.send_stream_frame("正文", chat_id="oc_ck18", turn_id="t-18")), \
            "元素表为空时必须 fail-open（不能报成功让卡片静默冻住）"
        assert calls["content"] == [], "空元素表时不该发出任何写入请求"

        # ⑰ **坏序号不许把这一帧炸掉**（R1 审计 U2）：`int("abc")` 会抛 ⇒ 被帧路径外层
        #    except 兜住 ⇒ 帧失败、整回合掉 native，而用户只看到「卡片怎么不变了」。
        #    收口到 `_ck_seq()`：坏值归零 + 限流告警（真机上这是唯一线索）。
        calls, client = _mk_fake()
        raw18 = _make()
        raw18._client = client
        adapter.configure(native_transport="cardkit", unified_panel=True)
        assert _run(raw18.send_stream_frame("", chat_id="oc_ck19", turn_id="t-19"))
        raw18._ld_stream_put("oc_ck19:t-19", {
            **(raw18._ld_stream_get("oc_ck19:t-19") or {}), "ck_seq": "坏了"})
        adapter._log_ck_seq_invalid_once._at = 0.0
        with _LogCapture("larkdeck") as records:
            assert _run(raw18.send_stream_frame("正文", chat_id="oc_ck19", turn_id="t-19")), \
                "坏序号应当被归零后继续（绝不能抛 ⇒ 整回合掉 native）"
        assert any("ck_seq" in r.getMessage() for r in records), \
            "坏序号必须留痕（真机上这是「为什么掉成纯文本」的唯一线索）"
        # 坏序号按 0 处理 ⇒ 这一帧从 1 号开始（装饰 batch 拿 1、正文拿 2）
        assert [b[1] for b in calls["batch"]] == [1], calls["batch"]
        assert [c[2] for c in calls["content"]] == [2], \
            f"坏序号按 0 处理 ⇒ 这一帧从 1 号开始：{[c[2] for c in calls['content']]}"

        # ⑱ **装饰的去重记账必须留历史**（R2）：去重的判据是「这个元素**上一次真的写成功了**
        #    什么内容」，而一帧里只有**变了的那几个**元素会进 batch ⇒ 如果记账写成「只记本帧
        #    发出去的那几个」，上一帧没写过的元素记录就被抹掉，下一帧会把它（内容没变）
        #    白写一遍。**这属于「省配额」的规则，坏了不致命 —— 所以它只有一条断言能抓**：
        #    逐帧数 batch 里到底带了哪几个元素。真实形态（同一张卡、同一串帧）：
        #      帧1 面板+页脚都第一次有内容 → 帧2 什么都没变 → 帧3 只有页脚变了
        #      → 帧4 页脚又变了（**这一帧抓「记账被抹掉」**：抹掉的话面板会被捎上）
        #      → 帧5 只有面板变了（**这一帧抓「只看 id 不比内容」**：那样面板永远不再写）
        calls, client = _mk_fake()
        raw19 = _make()
        raw19._client = client
        adapter.configure(native_transport="cardkit", unified_panel=True, footer=True)
        panel.reset()
        context.reset()
        try:
            assert _run(raw19.send_stream_frame("", chat_id="oc_ck20", turn_id="t-20"))

            def _footer(tokens: int, why: str) -> str:
                before = raw19._ld_footer() or ""
                context.record_api_call(model="test-model",
                                        usage={"input_tokens": tokens, "output_tokens": 4})
                context.set_context_override(10000)
                after = raw19._ld_footer() or ""
                assert after and after != before, f"{why}：页脚必须真的变了（{before!r}→{after!r}）"
                return after

            _footer(1000, "⑱ 前提")
            assert _run(raw19.send_stream_frame("正文一", chat_id="oc_ck20", turn_id="t-20"))
            assert _run(raw19.send_stream_frame("正文二", chat_id="oc_ck20", turn_id="t-20"))
            _footer(2000, "⑱ 前提")
            assert _run(raw19.send_stream_frame("正文三", chat_id="oc_ck20", turn_id="t-20"))
            _footer(3000, "⑱ 前提")
            assert _run(raw19.send_stream_frame("正文四", chat_id="oc_ck20", turn_id="t-20"))
            panel.record_reasoning("oc_ck20", "s-ck-20", "面板第二段")
            assert raw19._ld_panel_markdown("oc_ck20"), "⑱ 前提：面板到这一帧必须有内容"
            assert _run(raw19.send_stream_frame("正文五", chat_id="oc_ck20", turn_id="t-20"))
            got_ids = [[a["params"]["element_id"] for a in b[0]] for b in calls["batch"]]
            got_batch_seqs = [b[1] for b in calls["batch"]]
            got_answer_seqs = [c[2] for c in calls["content"]]
            # R3 收窄版：首帧建出来的面板是**两块**（`panel_body` + `panel_tools`），
            # 两块都在首帧写一次占位；本场景**没有工具事件** ⇒ 工具块此后一次都不再写
            # （这正是拆开的目的：工具块不跟着推理逐帧重发）。
            assert got_ids == [["panel_body", "panel_tools", "footer"], ["footer"], ["footer"],
                               ["panel_body"]], \
                f"每帧只该写**这一帧真的变了**的装饰：{got_ids}"
            assert got_batch_seqs == [1, 4, 6, 8], got_batch_seqs
            assert got_answer_seqs == [2, 3, 5, 7, 9], got_answer_seqs
            assert sorted(got_batch_seqs + got_answer_seqs) == list(range(1, 10)), \
                f"装饰与正文共用同一个严格递增序号：{sorted(got_batch_seqs + got_answer_seqs)}"

        finally:
            panel.reset()
            context.reset()
            context.set_context_override(None)

        # ㉕ **R3 收窄版的核心收益**：面板拆成 `panel_body`（推理）+ `panel_tools`（工具）之后，
        #    **工具事件只重写工具块、推理增长只重写推理块**（推理逐字在长时不再把那几十行
        #    工具摘要一起每帧重发）。判据全部**独立**于被测实现：字面量 id 列表、`⏳`/`✅` 字面量、
        #    以及对另一块的**反向搜索**（工具名一次都不许出现在推理块里 —— 否则「两块其实是
        #    同一块」也全绿）。
        #    ⚠️ **时钟必须冻结**：推理轮收尾时标题会补上耗时（`**第 1 轮**` → `**第 1 轮 · 0.0s**`），
        #    而耗时是墙钟算出来的 —— 不冻结时「工具开始那一帧会不会连带重写推理块」**取决于跑得多快**
        #    （我实测到两种结果：快时只写工具块、慢时两块都写，门禁在两台机器上结论不同）。
        #    这与 `_golden_trace()` 冻结时钟是同一条理由：**时间派生值不在断言的覆盖范围内**。
        calls, client = _mk_fake()
        raw25 = _make()
        raw25._client = client
        adapter.configure(native_transport="cardkit", unified_panel=True, footer=True)
        panel.reset()
        context.reset()
        _saved_monotonic, _saved_time = time.monotonic, time.time
        _saved_interval = adapter._STREAM_MIN_INTERVAL
        time.monotonic = lambda: 1_700_000_000.0        # type: ignore[assignment]
        time.time = lambda: 1_700_000_000.0             # type: ignore[assignment]
        # 冻结时钟下 `now - last_at` 恒为 0 ⇒ 不把节流窗口置 0 会被跳过（帧根本走不到写路径）
        adapter._STREAM_MIN_INTERVAL = 0.0
        try:
            panel.record_reasoning("oc_r3", "s-r3", "先看目录结构。")
            assert _run(raw25.send_stream_frame("", chat_id="oc_r3", turn_id="t-r3"))
            # 帧①：第一次写元素 —— 两块都还没有记账 ⇒ 都写一遍（预期，不是浪费）
            assert _run(raw25.send_stream_frame("正文一", chat_id="oc_r3", turn_id="t-r3"))
            # 帧②：**工具开始**（推理文本没变）⇒ 只该写工具块
            panel.record_tool_started("oc_r3", "s-r3", "read_file", {"path": "/tmp/r3.txt"}, "tc-r3")
            assert _run(raw25.send_stream_frame("正文二", chat_id="oc_r3", turn_id="t-r3")), \
                "工具开始那一帧必须成功"
            # 帧③：**工具结束**（推理仍没变）⇒ 还是只该写工具块
            panel.record_tool_finished("oc_r3", "s-r3", "read_file", status="ok",
                                       duration_ms=2300, tool_call_id="tc-r3")
            assert _run(raw25.send_stream_frame("正文三", chat_id="oc_r3", turn_id="t-r3")), \
                "工具结束那一帧必须成功"
            # 帧④：**第二个工具开始** ⇒ 仍旧只该写工具块
            panel.record_tool_started("oc_r3", "s-r3", "bash", {"cmd": "ls"}, "tc-r3b")
            assert _run(raw25.send_stream_frame("正文四", chat_id="oc_r3", turn_id="t-r3"))
            # 帧⑤：**推理增长**（工具没变）⇒ 只该写推理块
            panel.record_reasoning("oc_r3", "s-r3", "再看一眼测试目录。")
            assert _run(raw25.send_stream_frame("正文五", chat_id="oc_r3", turn_id="t-r3"))
            frames = [[a["params"]["element_id"] for a in b[0]] for b in calls["batch"]]
            assert frames == [["panel_body", "panel_tools", "footer"], ["panel_tools"],
                              ["panel_tools"], ["panel_tools"], ["panel_body"]], \
                f"工具事件只该重写工具块、推理增长只该重写推理块：{frames}"
            written = [{a["params"]["element_id"]: a["params"]["partial_element"]["content"]
                        for a in b[0]} for b in calls["batch"]]
            assert "read_file" not in written[0]["panel_body"], \
                f"推理块里不许出现工具行（两块其实是同一块时这条会红）：{written[0]['panel_body']!r}"
            assert "先看目录结构。" in written[0]["panel_body"], \
                f"推理块必须带推理文本：{written[0]['panel_body']!r}"
            assert "⏳ read_file" in written[1]["panel_tools"], \
                f"工具开始后工具块要写**运行中**那一行：{written[1]['panel_tools']!r}"
            assert "✅ read_file · 2.3s" in written[2]["panel_tools"], \
                f"工具结束后工具块要写**完成**那一行：{written[2]['panel_tools']!r}"
            assert "⏳ bash" in written[3]["panel_tools"], \
                f"第二个工具开始后工具块要带它：{written[3]['panel_tools']!r}"
            assert "再看一眼测试目录。" in written[4]["panel_body"] and \
                "先看目录结构。" in written[4]["panel_body"], \
                f"推理块写的是**整块**（累积轮次），不是增量：{written[4]['panel_body']!r}"
            # 反向搜索要覆盖**每一帧**（不是只看第一帧）：工具名一次都不许出现在推理块里
            assert all("read_file" not in w.get("panel_body", "")
                       and "bash" not in w.get("panel_body", "") for w in written), \
                f"任何一帧的推理块里都不许出现工具行：{[w.get('panel_body') for w in written]}"
            # ㉖ **「普通卡」车道不含面板两块**（收尾 / 降级 / `/stop` 重绘走 `unified_panel`）：
            #    面板两块是**实体卡专属**结构。这是**反向断言**：谁把实体卡的面板塞进普通卡就会红。
            final_cards = []

            async def _record_final(chat_id, mid, card):
                final_cards.append(card)
                return _StubResult(True, mid)

            raw25._ld_update_card = _record_final
            assert _run(raw25.send_stream_frame("正文一，正文二", finalize=True,
                                                chat_id="oc_r3", turn_id="t-r3"))
            assert final_cards, "收尾必须走整卡替换（patch）"
            blob = json.dumps(final_cards[0], ensure_ascii=False)
            assert cards.CARDKIT_PANEL_TOOLS_ID not in blob and \
                cards.CARDKIT_PANEL_BODY_ID not in blob, \
                "收尾整卡替换走的是普通卡（unified_panel），不该带实体卡的面板元素"
        finally:
            time.monotonic, time.time = _saved_monotonic, _saved_time
            adapter._STREAM_MIN_INTERVAL = _saved_interval
            panel.reset()
            context.reset()


        # ㉗ **A3：每回合一条自检汇总**（面板/页脚/账本 → 机器可读）。
        #    为什么要有它：面板为空、页脚不显示、账本「累计 0 帧」这三个症状**用户看得见、
        #    日志里却一个字都没有** ⇒ 判定只能靠「请用户再发一条消息再截图」—— 那是在消耗用户时间。
        #    ⚠️ 判据必须**能分辨是哪条路径打的**：失败路径打 `本回合帧=-1`，正常收尾打 `本回合帧=N(≥1)`。
        #    第一版只断言「有 `面板=有`」，结果**失败路径那条日志把它顺带满足了**
        #    （变异 `R10-4` 撤掉正常收尾那行日志、门禁照样全绿）—— 所以这里另起一个干净回合。
        calls27, client27 = _mk_fake()
        raw27 = _make()
        raw27._client = client27
        adapter.configure(native_transport="cardkit", unified_panel=True, footer=True)
        panel.reset()
        context.reset()
        panel.record_reasoning("oc_r3sc", "s-r3sc", "面板有内容。")
        panel.record_tool_started("oc_r3sc", "s-r3sc", "bash", {"cmd": "ls"}, "c-r3sc")
        adapter._log_turn_selfcheck._at = 0.0
        with _LogCapture("larkdeck") as records27:
            assert _run(raw27.send_stream_frame("", chat_id="oc_r3sc", turn_id="t-r3sc"))
            assert _run(raw27.send_stream_frame("正文", chat_id="oc_r3sc", turn_id="t-r3sc"))
            adapter._log_turn_selfcheck._at = 0.0
            assert _run(raw27.send_stream_frame("正文完", chat_id="oc_r3sc", turn_id="t-r3sc",
                                               finalize=True))
        _sc = [r.getMessage() for r in records27 if "回合自检" in r.getMessage()]
        def _frames_of(line: str) -> int:
            """从自检行里取「本回合帧」的数字（失败路径是 -1，正常收尾 ≥1）。"""
            found = re.search(r"本回合帧=(-?\d+)", line)
            return int(found.group(1)) if found else 0

        assert any(_frames_of(l) >= 1 and "面板=有" in l for l in _sc), \
            f"正常收尾必须打一条「本回合帧≥1 + 面板=有」的自检汇总：{_sc}"
        assert any("写卡帧数=" in l and "传输=cardkit" in l and "页脚=" in l for l in _sc), \
            f"自检汇总必须带上页脚 / 写卡帧数 / 传输三项：{_sc}"
        # 对偶：**没有面板数据**的回合必须如实说「面板=无」（否则这行日志只有一个取值）
        calls28, client28 = _mk_fake()
        raw28 = _make()
        raw28._client = client28
        panel.reset()
        adapter._log_turn_selfcheck._at = 0.0
        with _LogCapture("larkdeck") as records28:
            assert _run(raw28.send_stream_frame("", chat_id="oc_r3no", turn_id="t-r3no"))
            assert _run(raw28.send_stream_frame("正文", chat_id="oc_r3no", turn_id="t-r3no"))
            adapter._log_turn_selfcheck._at = 0.0
            assert _run(raw28.send_stream_frame("正文完", chat_id="oc_r3no", turn_id="t-r3no",
                                               finalize=True))
        _sc2 = [r.getMessage() for r in records28 if "回合自检" in r.getMessage()]
        assert any(_frames_of(l) >= 1 and "面板=无" in l for l in _sc2), \
            f"没有面板数据的回合必须如实说「面板=无」：{_sc2}"

        # ㉚ **R11-B3：失败分支不许丢掉 `live_state` 里新写的键**。
        #    病因：这一支原来写的是 `{**state, ...}`（旧状态）+ 手动抄三个字段 —— 于是
        #    `_ld_ck_apply` 里**任何新写的键**都会在这一支静默消失，而没有任何门禁看得见。
        #    判据必须往 `live_state` 塞一个**合成键**再让正文失败（否则撤掉修复不会红）：
        #    合成键只有从 `live_state` 出发才会留在回合状态里。
        _callsB, _clientB = _mk_fake(fail_answer_only=True)
        _rawB = _make()
        _rawB._client = _clientB
        adapter.configure(native_transport="cardkit")
        panel.reset()
        context.reset()
        assert _run(_rawB.send_stream_frame("", chat_id="oc_b3", turn_id="t-b3"))
        _apply_saved = adapter.LarkDeckMixin._ld_ck_apply

        async def _apply_probe(self, card_id, ops, seq, live_state):
            live_state["ck_synth_probe"] = "probe"        # ← 合成键（R11-B3 的判据）
            return await _apply_saved(self, card_id, ops, seq, live_state)

        adapter.LarkDeckMixin._ld_ck_apply = _apply_probe
        try:
            assert not _run(_rawB.send_stream_frame("正文", chat_id="oc_b3", turn_id="t-b3")), \
                "正文写失败必须 fail-open 返回 False（交核心回落 edit/send）"
        finally:
            adapter.LarkDeckMixin._ld_ck_apply = _apply_saved
        _stB = _rawB._ld_stream_get("oc_b3:t-b3") or {}
        assert _stB.get("ck_synth_probe") == "probe", \
            (f"失败分支必须从 `live_state` 出发 —— 只写进 live_state 的键不许被丢掉："
             f"{sorted(_stB)}")
        # 对偶：失败帧**不许动** `last` / `last_at` / `frames`（下一帧要重试同一段文本）
        assert _stB.get("last") == "" and int(_stB.get("frames") or 0) == 0, \
            (f"失败帧不许推进 last/frames（否则这段正文永远不会重试）："
             f"{_stB.get('last')!r} / {_stB.get('frames')}")

        # ㉘ **R11-A7：正文净化 —— 有证据地剥掉核心叠加的工具进度块**。
        #    用户明确要求「一个核心配置都不动」⇒ 必须在插件侧解决（不能靠
        #    `display.tool_progress`）。判据的要点是**证明**而不是**猜**：
        #        帧文本 == 我们的正文累积 + "\n\n---\n" + 尾巴   ⇒ 尾巴只可能是核心加的
        #    下面五格各钉一个「缺一不可」的条件，撤掉任何一个都会让某一格变红：
        #      ① 有证据 ⇒ 剥；② 没有工具窗口 ⇒ 不剥；③ 模型自己写的分隔线不许被吃掉；
        #      ④ 累积被上限冻结（不完整）⇒ 不剥；⑤ 工具**已结束**但还没有新正文 ⇒ 仍要剥。
        _SEP = adapter._CORE_PROGRESS_SEP
        # ⚠️ **判据与实现不许同源**（审计中-4）：上面这行是**自比较** —— 合成帧与剥离判据
        #    用的是同一个常量，于是把常量改成 `"\n---\n"` 之类，四门禁**全绿**而真机上
        #    从此静默不再剥（工具行又进正文）。这里用**字面量**把它钉死。
        assert _SEP == "\n\n---\n", \
            f"分隔符常量必须与核心的合成式一致（改它=真机静默失效）：{_SEP!r}"
        _PROG = "⚙️ 探针工具行（核心叠加的进度）"

        def _a7_run(chat, hook_turn, frame, final_text, prep=None):
            """跑一个 cardkit 回合；返回 (中间帧写进正文元素的内容, 收尾卡 JSON, 自检行)。

            ⚠️ **``frame`` 与 ``final_text`` 必须分开给，这是核心的真实行为**：核心只有
            ``tick.is_interim`` 那一帧才走 ``_compose_frame_content()`` 把工具进度块合成进去
            （``gateway/stream_consumer.py:791-798`` 的 ``display_text = self._accumulated``
            之后那个 ``if tick.is_interim``），**收尾帧发的是纯 ``self._accumulated``**
            （finalize 发送点**除一处例外**都是纯累积：``:486``/``:747``/``:781``/``:834``/``:856``/
            ``:862``/``:912``；例外是 ``stream_consumer_transport.py:391`` 的「失败后重发」——
            那条路会带进度行+光标，本测试**不模拟**它，已知缺口记在 ``docs/plan-v1.md`` 附录 F）。
            旧版拿**同一个 frame** 发两次 —— 那是按**错误的核心行为**写的测试，它让
            「收尾帧也剥进度」看起来是对的（R11-A7 尾巴的更正，变异 ``R11-9``）。

            ⚠️ **第二个返回值是收尾卡，不是「元素被写成了什么」**：cardkit 的收尾帧走
            ``_ld_update_card`` **整卡替换**（``core/adapter.py:2579``），那一刻**不写正文元素**
            ⇒ 「用户最终看到的正文」只能从收尾卡里读。写成「收尾帧后元素内容」会得到
            「元素没变」这种**恒真**断言（第一版就是这么写的，当场被 ⑦ 抓出来）。
            """
            _calls2, _client2 = _mk_fake()
            _raw2 = _make()
            _raw2._client = _client2
            adapter.configure(native_transport="cardkit", unified_panel=True)
            panel.reset()
            context.reset()
            if prep is not None:
                prep()
            assert _run(_raw2.send_stream_frame("", chat_id=chat, turn_id="t-" + hook_turn))
            with _LogCapture("larkdeck") as _recs:
                _n0 = len(_calls2["content"])
                assert _run(_raw2.send_stream_frame(frame, chat_id=chat,
                                                    turn_id="t-" + hook_turn))
                _mid2 = [c[1] for c in _calls2["content"][_n0:]
                         if c[0] == cards.CARDKIT_ANSWER_ID]
                adapter._log_turn_selfcheck._at = 0.0
                assert _run(_raw2.send_stream_frame(final_text, chat_id=chat,
                                                    turn_id="t-" + hook_turn, finalize=True))
            assert _mid2, "中间帧必须真的写了一次正文元素（否则这一格什么都没验）"
            _fin2 = json.dumps(_calls2["patch_cards"][-1], ensure_ascii=False)
            return _mid2[-1], _fin2, [r.getMessage() for r in _recs if "回合自检" in r.getMessage()]

        def _arm(body_text, turn, *, tool=True, finish_tool=False, session=None):
            """正文入账 + 工具窗口（顺序照真机：**正文在前、工具在后**）。"""
            sid = session or ("s-" + turn)
            def _go():
                panel.note_answer_delta(sid, "t-" + turn, body_text)
                if tool:
                    if finish_tool:
                        # 走真钩子入口（含幂等闸门与回合创建）
                        panel.record_tool_started(sid, "t-" + turn, "terminal", {"cmd": "ls"}, "c1")
                        panel.record_tool_finished(sid, "t-" + turn, "terminal", "ok", 12, "c1")
                    else:
                        panel.note_tool_event(sid, "t-" + turn)
            return _go

        # ① 有证据 ⇒ 剥（**中间帧**只渲染正文；自检要报出「剥了几帧」）
        _body_a = "工具之前就写好的第一段正文。"
        _mid_a, _fin_a, _sc_a = _a7_run("oc_a7a", "a7a", _body_a + _SEP + _PROG, _body_a,
                                       prep=_arm(_body_a, "a7a"))
        assert _mid_a == _body_a, f"有证据时必须只渲染正文（中间帧）：{_mid_a!r}"
        assert "探针工具行" not in _fin_a, \
            f"收尾帧也不许把核心的进度行留在卡里（用户最终看到的就是这一帧）：{_fin_a[-300:]}"
        # ⚠️ **必须精确等于 1**（审计中-7）：原来写的是 `>= 1`，于是把自检改成「每帧都记一笔
        #    剥」（`if True:`）也照样全绿 —— 而这个数是真机上**验证正文净化唯一生效的凭据**，
        #    虚高就等于凭据失效。本回合只有**中间帧**那一帧真的剥了（收尾帧永不剥）。
        _strip_counts = [int(re.search(r"正文剥进度=(\d+)", l).group(1)) for l in _sc_a
                        if "正文剥进度=" in l]
        assert _strip_counts == [1], \
            f"自检必须精确报出本回合剥了 1 帧（中间帧那一帧）：{_sc_a}"

        # ② 没有工具窗口 ⇒ **不剥**（核心没叠过进度块；这一格守住 `tool_pending` 条件）
        #    ⚠️ 这一格是**合成探针**：`frame` 里那段「像核心进度块」的文字是**模型自己写的**
        #    （所以 `final_text` 与 `frame` 相同 —— 模型写的就是全部）。它验的是「没有工具
        #    事件就不许剥」，不是「真机长这样」。
        _mid_b, _fin_b, _ = _a7_run("oc_a7b", "a7b", _body_a + _SEP + _PROG,
                                    _body_a + _SEP + _PROG,
                                    prep=_arm(_body_a, "a7b", tool=False))
        assert _mid_b == _body_a + _SEP + _PROG, \
            f"没有工具事件就不该剥（那是模型自己写的内容）：{_mid_b!r}"
        assert "探针工具行" in _fin_b, \
            f"没有工具窗口时收尾卡里就该保留模型自己写的那一整段：{_fin_b[-300:]}"

        # ③ 模型自己写的分隔线：只能剥掉**核心那一截**，模型写的一字不许少
        #    ⚠️ 这一格专治「按最后一个分隔符切」那种猜法 —— 猜法会把 `B段` 整段吞掉，
        #    而 `B段` 是**真答案**（8f81b4d 那个 P0 就是同一个病因的变体）。
        _body_c = "A段。" + _SEP + "B段（模型自己写的分隔线之后的正文）。"
        _mid_c, _fin_c, _ = _a7_run("oc_a7c", "a7c", _body_c + _SEP + _PROG, _body_c,
                                    prep=_arm(_body_c, "a7c"))
        assert _mid_c == _body_c, f"只能剥核心叠加的那一截：{_mid_c!r}"
        assert "B段" in _mid_c, "模型自己写的分隔线之后的正文被吃掉了"
        assert "B段" in _fin_c, f"收尾卡里也必须留着模型自己写的 B 段：{_fin_c[-300:]}"

        # ④ 累积被上限**冻结**（不完整）⇒ 不剥：残缺的累积仍可能是帧前缀，
        #    拿它当证据会把「冻结点之后、核心分隔符之前」的正文吞掉（那一段是真答案）。
        _cap_saved = panel._MAX_ANSWER_CHARS
        try:
            panel._MAX_ANSWER_CHARS = 8

            def _frozen_prep():
                panel.note_answer_delta("s-a7d", "t-a7d", "12345")       # 入账（5 ≤ 8）
                panel.note_answer_delta("s-a7d", "t-a7d", "67890")       # 超上限 ⇒ 冻结
                panel.note_tool_event("s-a7d", "t-a7d")

            _mid_d, _fin_d, _ = _a7_run(
                "oc_a7d", "a7d",
                "12345" + _SEP + "冻结之后模型继续写的正文" + _SEP + _PROG,
                "12345" + _SEP + "冻结之后模型继续写的正文",
                prep=_frozen_prep)
        finally:
            panel._MAX_ANSWER_CHARS = _cap_saved
        assert "冻结之后模型继续写的正文" in _mid_d, \
            f"累积不完整时绝不能剥（会吞掉冻结点之后的真答案）：{_mid_d!r}"
        assert "冻结之后模型继续写的正文" in _fin_d, \
            f"收尾卡里也必须有冻结点之后的真答案（那是用户最终看到的）：{_fin_d[-300:]}"

        # ⑤ 工具**已经结束**、但还没有新的正文增量 ⇒ 仍然要剥。
        #    核心的进度行是在**下一个正文增量**到达时才被清掉的，不是工具一结束就清 ——
        #    所以「post_tool_call 关窗口」是错的（那一帧恰好就是收尾帧）。
        _body_e = "工具跑完之后、下一个正文增量之前的那一帧。"
        _mid_e, _fin_e, _ = _a7_run("oc_a7e", "a7e", _body_e + _SEP + _PROG, _body_e,
                                    prep=_arm(_body_e, "a7e", finish_tool=True))
        assert _mid_e == _body_e, f"工具结束后（无新正文）的帧同样要剥：{_mid_e!r}"

        # ⑥ 配置项 `progress_lines_in_body: true` ⇒ **原样渲染**（用户明确要核心那套）。
        #    这一格的存在理由：这个键**早就写在 `_DEFAULTS`/`plugin.yaml`/README 里**，
        #    而它原先的唯一实现（`format_tool_event` 返回 None）在 Hermes 0.21.1 的生产路径上
        #    **根本没有调用点** ⇒ 那句文档一直是空转的（真机上工具行照样进正文）。
        #    现在它接到真正的帧文本上，所以必须有一条断言钉住「这个键真的有用」。
        _calls6, _client6 = _mk_fake()
        _raw6 = _make()
        _raw6._client = _client6
        adapter.configure(native_transport="cardkit", unified_panel=True,
                          progress_lines_in_body=True)
        panel.reset()
        context.reset()
        panel.note_answer_delta("s-a7f", "t-a7f", _body_a)
        panel.note_tool_event("s-a7f", "t-a7f")
        assert _run(_raw6.send_stream_frame("", chat_id="oc_a7f", turn_id="t-a7f"))
        assert _run(_raw6.send_stream_frame(_body_a + _SEP + _PROG,
                                            chat_id="oc_a7f", turn_id="t-a7f"))
        _ans_f = [c[1] for c in _calls6["content"] if c[0] == cards.CARDKIT_ANSWER_ID][-1]
        assert _ans_f == _body_a + _SEP + _PROG, \
            f"`progress_lines_in_body: true` 必须原样渲染（这个键不许是空转的）：{_ans_f!r}"
        adapter.configure(progress_lines_in_body=False)

        # ⑦ **收尾帧永远不剥**（R11-A7 尾巴；变异 `R11-9`）—— 本段最要紧的一格。
        #    证据：核心的收尾帧发的是**纯** `self._accumulated`（只有 `tick.is_interim`
        #    那一帧才合成进度块，`stream_consumer.py:791-798`），所以「累积之后还有内容」
        #    只可能是**模型自己写的**。
        #    为什么真机上真会发生：我们的累积来自**钩子队列**（异步投递），收尾帧完全可能
        #    **早于**最后一个正文增量到达我们这里 ⇒ 累积是一个**陈旧的完整前缀**
        #    （`complete` 仍为真、是帧文本的前缀、尾巴以分隔符开头且后面有内容）
        #    ⇒ 旧版的四个条件**全部成立**，它会在**唯一不可逆**的那一帧上把模型的续写
        #    当成核心进度剥掉；而核心对 finalize 是**乐观记账**
        #    （`_record_turn_final_payload` / `delivered_final_matches`）⇒ 它认为送达成功、
        #    **不会再补发** ⇒ 用户看到的正文被静默砍掉一截，任何一层都不报错。
        _body_g = "前半段。"
        _stale_full = _body_g + _SEP + "后半段（模型自己写的分隔线之后继续写的正文）。"
        _mid_g, _fin_g, _ = _a7_run(
            "oc_a7g", "a7g", _stale_full + _SEP + _PROG, _stale_full,
            prep=_arm(_body_g, "a7g"))   # 累积只到「前半段。」：完整、非冻结、有工具窗口
        assert _mid_g == _body_g, \
            f"中间帧的判据不该被护栏一起关掉（那会变成「这个功能没了」）：{_mid_g!r}"
        assert "后半段" in _fin_g, \
            f"⚠️ 收尾帧吃掉了正文 —— 而核心不会再补发（不可逆）：{_fin_g[-300:]}"
        assert _body_g in _fin_g, \
            f"前半段也必须还在（不许整段被换成别的）：{_fin_g[-300:]}"

        # ⑧ **「收尾帧不剥」必须与传输无关**（审计实测的判别力缺口，必须单独钉）。
        #    审计构造过一个「弱化形态」：把通用护栏从 `_strip_core_progress` 里删掉、
        #    改成 `_ld_body_text` 里一句「只有 cardkit 才跳过收尾帧」。那个形态**对 cardkit
        #    而言语义等价于修好了**，于是上面 ①–⑦ 这些**全在 cardkit 上跑**的断言
        #    全都发现不了它（它一度被误当作「195/195 全绿」的证据）。
        #    ⇒ 这一格**直接在函数层**钉住护栏本身：不经任何传输、也不依赖状态查找。
        #      两句缺一不可：头一句钉「收尾帧不剥」，后一句钉「中间帧照剥」
        #      （否则「把剥离功能整个关掉」也能让前一句通过）。
        assert adapter._strip_core_progress(_body_a + _SEP + _PROG, _body_a, True, True,
                                            finalize=True) == _body_a + _SEP + _PROG, \
            "收尾帧在**函数层**就必须不剥（与传输无关）——别实现成某个传输的特例"
        assert adapter._strip_core_progress(_body_a + _SEP + _PROG, _body_a, True, True,
                                            finalize=False) == _body_a, \
            "中间帧的剥离判据不变（这一句防「把功能整个关掉」也能满足上一句）"


        # ⑨ **条件③与条件④各自也要有守卫**（审计中-6 实测：这两条原先撤掉四门禁全绿）。
        #    ③ 「前缀」不是「子串」：帧文本**包含**累积但**不以它开头**时，尾巴根本不是核心叠加的
        #       ——拿子串当证据会剥掉**前面那段模型正文**。
        #
        #    ⚠️⚠️ **这一格的第一版是恒真的**（2026-09-15 全量跑实测：变异 `G1-22` 把它从
        #    `startswith` 放宽成 `not in`，四门禁**照样全绿**）。原因不是「断言写松了」，
        #    而是**探针构造本身**走不到剥离分支：旧探针是 `"宋" + _body_a + _SEP + _PROG`，
        #    而条件④那一行切片用的是**长度**（`text[len(accumulated):]`），累积前面多出来的
        #    `"宋"` 让切片**错位一个字符** ⇒ 尾巴以 `"。"` 开头而不是分隔符 ⇒ 连错误实现
        #    也在条件④上原样返回 ⇒ 断言两边都成立。
        #    ⇒ 所以探针必须**自己带一条「这条用例真的有判别力」的自证**：帧首长度与累积
        #      等长，错的切片才会**正对着**分隔符（下面第二句 assert 钉住这件事）。
        #      这是本项目「恒真断言」的又一个新形态：**输入构造**让错误分支不可达，
        #      而不是断言写错 —— 只有把这一点也断言下来，下一轮改代码才不会静默退化。
        _p9_head = "帧首正文。"          # 与下面累积**必须等长**（见下）
        _p9_acc = "错配累积。"
        assert len(_p9_head) == len(_p9_acc), \
            "构造前提：帧首与累积必须等长，否则这一格会退化成恒真（理由见上面的注释）"
        _p9 = _p9_head + _SEP + _p9_acc + _PROG
        assert _p9_acc in _p9 and not _p9.startswith(_p9_acc), "构造前提：累积必须是「子串但非前缀」"
        assert _p9[len(_p9_acc):].startswith(_SEP), \
            "构造无效：错位切片必须以分隔符开头，否则「子串判据」那条错误实现也走不到剥离分支"
        assert adapter._strip_core_progress(_p9, _p9_acc, True, True,
                                            finalize=False) == _p9, \
            "条件③：累积必须真的是**前缀**（只是子串不算证据，否则会吞掉前面的正文）"

        #    ④ 尾巴**只有裸分隔符、后面没内容** ⇒ 不许剥：核心的合成式在 `progress` 为空时
        #      **不会**留下裸分隔符（`"\n\n---\n".join(p for p in (accumulated, progress) if p)`），
        #      所以裸分隔符只能是**模型自己写的**（那时它已经在累积里了）。
        assert adapter._strip_core_progress(_body_a + _SEP, _body_a, True, True,
                                            finalize=False) == _body_a + _SEP, \
            "条件④：尾巴必须是「分隔符 + 还有内容」；裸分隔符不许剥（那是模型写的）"

        # ⑭ **正文长大之后也要守硬上限**（第十二路审计第 5 条）：建实体那道闸门守的是
        #    **空正文**的 seed 帧（核心传 `""`），真正会长大的是后面每一帧的累积全文。
        #    ⚠️ R4 起这一格分**两半**（行为**有意**变了，别再当成回归）：
        #      ① 超硬上限但**切得开** ⇒ 走卡链（帧成功、内容一个字节都不丢）；
        #      ② 一帧的增量太大、连新卡都装不下 ⇒ 仍然 fail-open（不白花一次往返）。
        calls, client = _mk_fake()
        raw14 = _make()
        raw14._client = client
        adapter.configure(native_transport="cardkit")
        assert _run(raw14.send_stream_frame("", chat_id="oc_ck15", turn_id="t-15"))
        # ⚠️ 正文要有**可辨认的开头标记**：整段都是同一个汉字时，「新卡不许重放前半段」这类
        # 断言会被子串匹配**顺带满足**（第一版就是这么假绿的 —— 恒真断言的又一个形态）。
        _huge = "开头标记" + "汉" * ((cards.FEISHU_CARD_BYTE_LIMIT // 3) + 1000)   # 略超硬上限
        assert _run(raw14.send_stream_frame(_huge, chat_id="oc_ck15", turn_id="t-15")), \
            "R4：超过单卡容量但**切得开**时必须切卡（不是 fail-open）"
        assert calls["create"] == 2, f"应当封一张、开一张：{calls['create']}"
        _sealed_body = json.dumps(calls["patch_cards"][-1], ensure_ascii=False)
        _tail_answer = [c[1] for c in calls["content"] if c[0] == cards.CARDKIT_ANSWER_ID][-1]
        # 内容不许丢：封卡里是**前半段**、新卡里是**后半段**（两段拼起来才是原文）
        assert "开头标记" in _sealed_body, "封卡里应当有正文开头"
        assert "开头标记" not in _tail_answer, "新卡不许重放前半段（R4 最大的观感坑）"
        assert len(_tail_answer) > 1000, f"新卡里应当有后半段：{len(_tail_answer)} 字"

        # ② 一帧的增量**太大**（切完的尾巴仍超新卡容量）⇒ 只能 fail-open 交核心回落
        calls, client = _mk_fake()
        raw14b = _make()
        raw14b._client = client
        adapter.configure(native_transport="cardkit")
        assert _run(raw14b.send_stream_frame("", chat_id="oc_ck15b", turn_id="t-15b"))
        # 阈值：封卡预算 + 尾巴预算 ≈ 0.5 + 0.9 = 1.4 倍硬上限 ⇒ 取 2 倍必然切不开
        _too_big = "汉" * (cards.FEISHU_CARD_BYTE_LIMIT * 2 // 3)
        adapter._log_ck_over_budget_once._at = 0.0
        with _LogCapture("larkdeck") as records:
            assert not _run(raw14b.send_stream_frame(_too_big, chat_id="oc_ck15b",
                                                     turn_id="t-15b")), \
                "一帧塞不下的增量必须 fail-open（否则发一张必被飞书拒的卡）"
        assert calls["content"] == [], \
            f"超上限的正文不该再往元素里写（白花一次 API 往返）：{calls['content']}"
        assert any("硬上限" in r.getMessage() for r in records), "超上限必须留痕"

        # ⑬ **真 SDK 请求构造**要单独验（⑫ 断言的是**替身**给的 uuid —— 那是自比较：
        #    把生产代码里的 `.uuid(...)` 删掉，替身照样给出 uuid，变异 CK21 曾经全绿）。
        #    所以这里直接调生产的 `_ld_ck_requests()`（SDK 缺了就跳过，并**打印**跳过原因，
        #    免得「没跑」被读成「通过」）。
        # ⚠️ 必须用**开头存下的原函数**（`old_reqs`）：本用例中途把 `_ld_ck_requests`
        #    换成了替身，直接取 `LarkDeckMixin._ld_ck_requests()` 拿到的是替身 ——
        #    第一版就踩了这个坑（AttributeError，且如果替身恰好也带这些属性就会变成自比较）。
        _real_reqs = old_reqs()
        if _real_reqs is None:
            print("   ⚠️ 跳过真 SDK 请求构造检查：这个解释器里没有 lark_oapi")
        else:
            _send = _real_reqs.send_entity("oc_real", "ck_9")
            assert _send.request_body.uuid == "ld-msg-ck_9", \
                f"发实体卡的去重键必须由 card_id 推出，实得 {_send.request_body.uuid!r}"
            assert _send.request_body.msg_type == "interactive", _send.request_body
            _reply = _real_reqs.reply_entity("om_real", "ck_9")
            assert _reply.message_id == "om_real", _reply
            assert _reply.request_body.uuid == "ld-msg-ck_9", _reply.request_body
            assert _reply.request_body.reply_in_thread is False, _reply.request_body
            assert _reply.request_body.msg_type == "interactive", _reply.request_body

        # ⑦ 建实体回 `code=0` 但 **card_id 是空串** ⇒ 必须当场 fail-open，
        #    **绝不能**拿这个空 id 去发一张实体卡（那会是一条指向不存在实体的消息，
        #    而且失败在更下游、更难查）。审计把它列为「低」，但它是「静默发出坏卡」的形态。
        # ⚠️ 位置很关键：必须放在 ⑥ 之后 —— ⑤ 把 `_ld_ck_requests` 换成了「没有 SDK」，
        #    把 ⑦ 放在它前面时，两个断言会被**更早的一个原因**顺带满足（帧因为没 SDK 就返回
        #    False 了），于是这条用例**恒真**、变异 CK11 全绿。所以这里额外断言
        #    「建实体这一步真的被调用到了」——它是这条用例的判别力来源。
        calls, client = _mk_fake(create_empty_id=True)
        raw6 = _make()
        raw6._client = client
        adapter.configure(native_transport="cardkit")
        assert _run(raw6.send_stream_frame("", chat_id="oc_ck6", turn_id="t-6")) is False, \
            "card_id 为空时必须 fail-open 返回 False"
        assert calls["create"] == 1, "前提：建实体真的被调用了（否则下面的断言是被别的失败顺带满足的）"
        assert calls["send"] == 0, "card_id 为空时不该去发实体卡（飞书会拒，且查起来更远）"

        # ⑧ **限流码要退避重试**（第十二路审计）：CardKit 的元素写入/建实体此前**一次都不重试**，
        #    撞上一次限流整回合的 native 就被停用 —— 而 patch 路径的 `_ld_update_card` 早就有这层
        #    退避，同一个错误码在两条传输上后果不同，是纯粹的不对称。
        #    退避间隔在测试里压成 0（只验「重试了几次」，不验真实等待）。
        old_backoff = adapter._TRANSIENT_BACKOFF
        adapter._TRANSIENT_BACKOFF = (0.0, 0.0, 0.0)
        try:
            # ⑧a 写正文先撞一次限流（230020）、第二次成功 ⇒ 整帧必须成功
            calls, client = _mk_fake(answer_codes=[230020, 0])
            raw7 = _make()
            raw7._client = client
            adapter.configure(native_transport="cardkit")
            assert _run(raw7.send_stream_frame("", chat_id="oc_ck7", turn_id="t-7"))
            assert _run(raw7.send_stream_frame("正文", chat_id="oc_ck7", turn_id="t-7")), \
                "限流后重试成功 ⇒ 这一帧必须成功（原来会当作定义性失败、整回合掉 native）"
            answers = [c for c in calls["content"] if c[0] == "answer"]
            assert len(answers) == 2, f"正文元素应被写两次（一次限流 + 一次重试成功）：{len(answers)}"

            # ⑧b **结构性码不重试**：300309（流式会话已关闭）原样重发不可能成功，
            #     立刻放弃重试、不许白等三轮退避。
            #     ⚠️ R5 起「这一帧返回什么」**有意**变了：300309 是**卡级死法** ⇒ 走 `DEGRADE`
            #     车道用整卡 patch 续写同一张卡 ⇒ 帧返回 **True**。所以这条用例钉的是**重试次数**
            #     （恰好 1 次），帧的返回值与「不另建卡」由 ②e 专门钉。
            calls, client = _mk_fake(answer_codes=[300309])
            raw8 = _make()
            raw8._client = client
            adapter.configure(native_transport="cardkit")
            assert _run(raw8.send_stream_frame("", chat_id="oc_ck8", turn_id="t-8"))
            adapter._log_ck_degrade_once._at = 0.0
            assert _run(raw8.send_stream_frame("正文", chat_id="oc_ck8", turn_id="t-8")), \
                "卡级死法应当降级续写同一张卡（不是 fail-open）"
            answers = [c for c in calls["content"] if c[0] == "answer"]
            assert len(answers) == 1, f"300309 是结构性状态，不该重试（实际写了 {len(answers)} 次）"
            assert calls["patch"] == 1, f"降级那一帧必须 patch 出去：{calls['patch']}"

            # ⑧c 一直限流 ⇒ 试满 `len(_TRANSIENT_BACKOFF)+1` 次后 fail-open（不吞、不无限重试）
            calls, client = _mk_fake(answer_codes=[230020])
            raw9 = _make()
            raw9._client = client
            adapter.configure(native_transport="cardkit")
            assert _run(raw9.send_stream_frame("", chat_id="oc_ck9", turn_id="t-9"))
            assert not _run(raw9.send_stream_frame("正文", chat_id="oc_ck9", turn_id="t-9"))
            answers = [c for c in calls["content"] if c[0] == "answer"]
            assert len(answers) == len(adapter._TRANSIENT_BACKOFF) + 1, \
                f"限流应重试到退避表用完为止：期望 {len(adapter._TRANSIENT_BACKOFF) + 1} 次，实得 {len(answers)}"

            # ⑧d 建实体撞限流 ⇒ 同样要重试（一次限流 + 一次成功 = 建实体 2 次、发实体卡 1 次）
            calls, client = _mk_fake(create_codes=[99991400, 0])
            raw10 = _make()
            raw10._client = client
            adapter.configure(native_transport="cardkit")
            assert _run(raw10.send_stream_frame("", chat_id="oc_ck10", turn_id="t-10")), \
                "建实体撞限流后重试成功 ⇒ seed 帧必须成功"
            assert calls["create"] == 2 and calls["send"] == 1, calls
        finally:
            adapter._TRANSIENT_BACKOFF = old_backoff

        # ⑨ **硬上限闸门**（第十二路审计缺口 1）：cardkit 的结构在建实体时定死，
        #    超硬上限就是整卡被飞书拒（`230099`）⇒ 这一帧什么都没有、还白建一个实体。
        #    ⚠️ 造这条数据**不能用默认面板**：面板只有 ~4.4KB，永远进不了这个分支
        #    （审计第一版探针就撞了这个坑：两态都是绿的）。正文直接顶到硬上限之上。
        big = "汉" * ((cards.FEISHU_CARD_BYTE_LIMIT // 3) + 2000)   # 中文 3 字节/字
        assert len(big.encode("utf-8")) > cards.FEISHU_CARD_BYTE_LIMIT, "前提：正文本身超硬上限"
        calls, client = _mk_fake()
        raw11 = _make()
        raw11._client = client
        adapter.configure(native_transport="cardkit")
        # ⚠️ 告警是 60 秒限流的（进程全局），同一用例里前面调过一次就会把这次压掉 ——
        #    所以断言「必须留痕」之前要把限流时间戳清零（`_at` 挂在函数对象上）。
        adapter._log_ck_over_budget_once._at = 0.0
        with _LogCapture("larkdeck") as records:
            assert not _run(raw11.send_stream_frame(big, chat_id="oc_ck11", turn_id="t-11")), \
                "超硬上限时建实体必然被拒 ⇒ 必须当场 fail-open，而不是把卡发出去"
        assert calls["create"] == 0 and calls["send"] == 0, \
            f"超预算时不该去建实体/发实体卡（会白留一张孤儿实体卡）：{calls}"
        assert any("硬上限" in r.getMessage() for r in records), \
            "超预算必须留痕（这是「这一帧怎么什么都没有」的唯一线索）"

        # ⑩ **面板内容必须过用户配的三个上限**（第十二路审计缺口 2）：实体卡里的面板内容是
        #    一个 markdown 字符串，走 `_cards.panel_markdown` 同一套截断规则。删掉那几个
        #    kwarg 时四门禁曾经全绿 —— 而 README 把 `max_reasoning_chars` 当用户可配上限卖、
        #    还承诺「两条传输下观感一致」。所以这里把「配了 100 字」与「真的 ≤ 一个量级」钉住。
        _panel_mod = panel          # 顶部已 `from larkdeck.core import … panel`
        _panel_mod.reset()
        adapter.configure(native_transport="cardkit", unified_panel=True,
                          max_reasoning_chars=100)
        _panel_mod.record_reasoning("oc_ck12", "s-ck12", "推理" * 400)
        # ⚠️ 这里**必须**钉在真正写入实体卡的那条路径上（`_ld_panel_parts`）。
        #    R3 收窄版把实体卡的面板从「一个 markdown」拆成两块之后，
        #    `_ld_panel_markdown` 在生产里**一个调用方都没有**了 —— 而这条用例原本调的就是它
        #    （2026-09-14 R3 代码审计实测：把 `_ld_panel_parts` 的三个上限换成写死的值，
        #    四门禁全绿 ⇒ 实体卡面板的上限**完全没人守**，长工具行/200 步都不会被截断，
        #    最后撞建实体的字节墙 ⇒ 那一帧 fail-open）。所以两块各自断言一次。
        _body, _tools = raw11._ld_panel_parts("oc_ck12")
        assert _body, "前提：推理块得有内容（否则这条断言恒真）"
        assert len(_body) <= 200, \
            f"配了 max_reasoning_chars=100，cardkit 的推理块却有 {len(_body)} 字符（上限被忽略）"
        # 工具块的两个上限同样要守（用户可配 `max_tool_result_chars` / `max_panel_steps`）
        adapter.configure(max_tool_result_chars=60, max_panel_steps=3)
        for _i in range(8):
            _panel_mod.record_tool_started("oc_ck12", "s-ck12", f"tool{_i}", {"i": _i}, f"c{_i}")
            _panel_mod.record_tool_finished("oc_ck12", "s-ck12", f"tool{_i}", status="ok",
                                            duration_ms=10, tool_call_id=f"c{_i}")
        _body2, _tools2 = raw11._ld_panel_parts("oc_ck12")
        assert _tools2, "前提：工具块得有内容"
        _rows = [r for r in _tools2.split("\n\n") if r.startswith(("✅", "⏳"))]
        assert len(_rows) == 3, \
            f"配了 max_panel_steps=3 ⇒ 只该留 3 行工具，实得 {len(_rows)}：{_tools2!r}"
        assert "tool7" in _tools2 and "tool0" not in _tools2, \
            f"要留**最近**的几步：{_tools2!r}"
        _panel_mod.reset()
        adapter.configure(max_reasoning_chars=adapter._DEFAULTS["max_reasoning_chars"],
                          max_tool_result_chars=adapter._DEFAULTS["max_tool_result_chars"],
                          max_panel_steps=adapter._DEFAULTS["max_panel_steps"])

        # ⑪ 两条 CardKit 告警的**限流**本身要有门禁（第十二路审计缺口 3）：
        #    `M07`/`CK4`/`CK8` 只打「失败被吞掉」的调用点，从没打过「60 秒一条」这件事。
        #    限流失效的后果是每帧一条 WARNING 把日志刷爆（并掩盖真正的线索）；
        #    反向（一条都不打）则回到「静默、无迹可寻」。
        for _once, _args in ((adapter._log_ck_panel_write_failed_once, ()),
                             (adapter._log_ck_over_budget_once, (200000,))):
            _once._at = 0.0                                   # 清掉上一次调用的时间戳
            with _LogCapture("larkdeck") as records:
                _once(*_args)
                _once(*_args)
                _once(*_args)
            hits = [r for r in records if r.levelno >= logging.WARNING]
            assert len(hits) == 1, \
                f"{_once.__name__} 应当 60 秒只打一条，实际打了 {len(hits)} 条"
    finally:
        try:
            adapter.LarkDeckMixin._ld_ck_requests = old_reqs
            adapter._STREAM_MIN_INTERVAL = old_interval
        except NameError:
            pass
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_card_tracking_cache_has_a_hard_bound():
    """追踪表**必须有容量上界**（R5 的第 ④ 项）：网关是长驻进程，无界缓存 = 慢性内存泄漏。

    上界的**淘汰策略**也要对：按「最近活动」而不是「创建时刻」淘汰 —— 长回合的卡创建得早
    但仍在被编辑，按 t0 淘汰会把它们踢出去，之后 `edit_message` 找不到追踪项就回落内置实现，
    而内置走 `message.update`、对 interactive 卡会被飞书拒（卡片永久冻结，见 `_ld_track` 注释）。
    """
    assert adapter._MAX_TRACKED == 512, f"上界是显式决定，别顺手改：{adapter._MAX_TRACKED}"
    raw = _make()
    # 灌到超过上界：最新的必须还在，最早的必须已被淘汰
    for i in range(adapter._MAX_TRACKED + 20):
        raw._ld_track(f"om_{i}", "oc_bound")
    tracked = raw._ld_state
    assert len(tracked) <= adapter._MAX_TRACKED, f"追踪表没有上界：{len(tracked)}"
    assert f"om_{adapter._MAX_TRACKED + 19}" in tracked, "最新的那张卡不能被淘汰"
    assert "om_0" not in tracked, "最早的（且不再活动的）卡应当被淘汰掉"
    # 淘汰策略必须是**最近活动**、不是**创建时刻**（长回合的卡创建得早但一直在被编辑）：
    # 填满到上界 → 把**最早那张**标成「刚刚还在编辑」→ 再塞一张触发淘汰 ⇒
    # 被刷过的老卡必须活下来，而它旁边**没被刷过**的老卡必须被淘汰。
    raw2 = _make()
    for i in range(adapter._MAX_TRACKED):
        raw2._ld_track(f"om_lru_{i}", "oc_lru")
    assert raw2._ld_known("om_lru_0") is not None, "前提：填满之后最早那张还在"
    raw2._ld_known("om_lru_0")                   # 刷成「刚刚活动过」
    raw2._ld_track("om_trigger", "oc_lru")       # 触发一次淘汰
    assert raw2._ld_known("om_lru_0") is not None, \
        "正在被编辑的老卡不能被淘汰（按创建时刻淘汰会让长回合的卡永久冻结）"
    evicted = [i for i in range(1, adapter._MAX_TRACKED) if f"om_lru_{i}" not in raw2._ld_state]
    assert evicted, "触发淘汰后应当有卡被清掉（否则这条断言恒真、证明不了淘汰真的发生）"


def test_native_streaming_frame_lifecycle():
    """普通帧原地更新 + 幂等去重；finalize 收尾并清状态。"""
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0
    try:
        panel.reset()
        panel.record_reasoning("s1", "t1", "先想一下")
        raw = _make()
        updates = _wire_patch(raw)
        assert _run(raw.send_stream_frame("", finalize=False, chat_id="oc_1",
                                          turn_id="t1")) is True
        assert _run(raw.send_stream_frame("你好", finalize=False, chat_id="oc_1",
                                          turn_id="t1")) is True
        assert len(updates) == 1, "普通帧应更新同一张卡"
        body = json.loads(updates[0]["content"])
        assert body["config"].get("streaming_mode") is True
        joined = json.dumps(body, ensure_ascii=False)
        assert "你好" in joined and "先想一下" in joined, "面板推理应随帧更新"

        assert _run(raw.send_stream_frame("你好", finalize=False, chat_id="oc_1",
                                          turn_id="t1")) is True
        assert len(updates) == 1, "文本没变不该重复更新（幂等）"

        assert _run(raw.send_stream_frame("最终答案", finalize=True, chat_id="oc_1",
                                          turn_id="t1")) is True
        assert len(updates) == 2, "finalize 要落到同一张卡"
        final = json.loads(updates[1]["content"])
        assert not final["config"].get("streaming_mode"), "finalize 后不再是流式态"
        assert "oc_1:t1" not in raw._ld_streams, "finalize 后回合状态必须清掉"
        assert raw._ld_known("om_card_1") is None, "finalize 后卡片停止追踪"
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_native_streaming_failures_fall_back():
    """任何一步失败都返回 False（核心回落 send/edit）；finalize 失败保住状态等重试。"""
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0
    try:
        panel.reset()
        refused = _make()
        refused._fail_cards = True
        assert _run(refused.send_stream_frame("", finalize=False, chat_id="oc_9",
                                              turn_id="t9")) is False
        assert not refused._ld_streams, "建卡失败不该留状态"

        raw = _make()
        assert _run(raw.send_stream_frame("", finalize=False, chat_id="oc_2",
                                          turn_id="t2")) is True
        _wire_patch(raw)
        raw._client.im.v1.message.patch = lambda request: {"code": 99999, "msg": "patch rejected"}
        assert _run(raw.send_stream_frame("收尾", finalize=True, chat_id="oc_2",
                                          turn_id="t2")) is False
        assert "oc_2:t2" in raw._ld_streams, "finalize 失败要保住状态，让回落路径再试"

        # 模拟核心回落：edit_message(finalize=True) 成功 → _ld_forget 应把流状态一并清掉
        raw._client.im.v1.message.patch = lambda request: (
            {"code": 0, "data": {"message_id": request["message_id"]}})
        result = _run(raw.edit_message("oc_2", "om_card_1", "收尾", finalize=True))
        assert getattr(result, "success", False) is True
        assert "oc_2:t2" not in raw._ld_streams, "回落 edit 成功后流状态不该残留"

        assert _run(raw.send_stream_frame("x", finalize=True, chat_id="oc_none",
                                          turn_id="tz")) is False, "没有活跃流的 finalize 返回 False"
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_native_streaming_throttle_skips_midframes_but_not_first():
    """首帧不被节流（首字即时）；窗口内的后续中间帧跳过；finalize 不受节流。"""
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    try:
        panel.reset()
        raw = _make()
        updates = _wire_patch(raw)
        assert _run(raw.send_stream_frame("", finalize=False, chat_id="oc_t",
                                          turn_id="tt")) is True
        adapter._STREAM_MIN_INTERVAL = 10.0
        assert _run(raw.send_stream_frame("第一帧", finalize=False, chat_id="oc_t",
                                          turn_id="tt")) is True
        assert len(updates) == 1, "首帧（last 为空）不该被节流"
        assert _run(raw.send_stream_frame("第二帧", finalize=False, chat_id="oc_t",
                                          turn_id="tt")) is True
        assert len(updates) == 1, "窗口内的中间帧应被跳过"
        assert _run(raw.send_stream_frame("最终", finalize=True, chat_id="oc_t",
                                          turn_id="tt")) is True
        assert len(updates) == 2, "finalize 不受节流影响"
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_native_streaming_cap_reclaims_only_leaked_streams():
    """容量满时只回收**真正泄漏**的流（静默超过泄漏阈值），不是「最旧的那批」。

    泄漏 = 核心始终没发 finalize，状态挂在那里没人管。这类回收是安全的。
    """
    raw = _make()
    now = time.monotonic()
    leaked = now - adapter._STREAM_LEAK_SECONDS - 10
    for i in range(adapter._MAX_STREAMS):
        raw._ld_stream_put(f"leaked-{i}", {"t0": leaked, "last_at": leaked,
                                          "message_id": f"om_l{i}"})
    # ⚠️ 「泄漏」= **插进来之后就再没人碰过**。存活判据是 `alive_at`（每次 get/put 都盖新戳，
    # 见 R5 审计低-6），所以这里必须**把戳回拨到过去**才能造出真的泄漏流 —— 只在入参里写
    # 旧的 `t0`/`last_at` 是不够的：插入那一刻就会被盖上「刚刚活动」。
    for i in range(adapter._MAX_STREAMS):
        raw._ld_streams[f"leaked-{i}"]["alive_at"] = leaked
    assert len(raw._ld_streams) == adapter._MAX_STREAMS
    raw._ld_stream_put("newcomer", {"t0": now, "last_at": now, "message_id": "om_new"})
    assert len(raw._ld_streams) <= adapter._MAX_STREAMS
    assert "newcomer" in raw._ld_streams, "新流必须能进来"


def test_stream_liveness_survives_a_long_silent_turn():
    """**长时间没写帧但一直在被读的回合，绝不能被当成泄漏流回收**（R5 审计的低-6）。

    场景：跑了 >1 小时工具的回合 —— 期间**正文没变**（帧被节流跳过或早返回），
    `last_at` 一动不动，`_ld_stream_put` 也不一定被调用；但每一帧都会 `_ld_stream_get`。
    存活判据必须是独立字段 `alive_at`（每次 get/put 刷新），否则 64 个流满的时候会把这
    个**真活跃**的回合当泄漏踢掉 ⇒ 下一帧查不到状态 ⇒ **另发一张新卡**（重复卡）。
    """
    raw = _make()
    now = time.monotonic()
    old = now - adapter._STREAM_LEAK_SECONDS - 10
    for i in range(adapter._MAX_STREAMS):
        raw._ld_stream_put(f"silent-{i}", {"t0": old, "last_at": old, "message_id": f"om_s{i}"})
        raw._ld_streams[f"silent-{i}"]["alive_at"] = old
    # 两条刷新路径都要隔离测（审计低-6）：
    # ① 0 号一直在被**读**（帧节流跳过 / 文本没变早返回的回合就是这个形状）
    assert raw._ld_stream_get("silent-0"), "前提：这个回合在状态表里"
    # ② 2 号这一帧**写了状态**（`_ld_stream_put`）⇒ 也必须算活跃
    raw._ld_stream_put("silent-2", {"t0": old, "last_at": old, "message_id": "om_s2"})
    raw._ld_stream_put("newcomer", {"t0": now, "last_at": now, "message_id": "om_new"})
    assert "silent-0" in raw._ld_streams, \
        "「没写帧但一直被读」的活跃回合被当泄漏回收了 —— 它的下一帧会另发一张新卡"
    assert "silent-2" in raw._ld_streams, \
        "刚写过状态的流被当泄漏回收了 —— `_ld_stream_put` 也必须刷新活跃度"
    assert "newcomer" in raw._ld_streams, "新流仍要能进来"
    assert "silent-1" not in raw._ld_streams, \
        "对照组：真的没人碰过的流必须被回收（否则这条断言恒真、证明不了回收会发生）"


def test_native_streaming_cap_never_evicts_active_streams():
    """**活跃流绝不淘汰** —— 这是防重复卡的关键不变量。

    为什么不能按「最近活动」淘汰：`last_at` 只在有正文帧时推进，而一个跑十分钟工具的
    回合期间只有 panel 在变、`_ld_stream_put` 根本不被调用 —— 那种流看起来「很陈旧」，
    实际正活跃。踢掉它，下一帧会因为查不到状态而**另发一张新卡**（重复卡 + 老卡永久
    停在流式态），正是淘汰逻辑本来要防的事。

    ⚠️ 断言必须**有判别力**：先前只断言 `<= _MAX_STREAMS` + 「新流进来了」，
    而旧实现（按 t0 淘汰）同样满足 —— 于是这个测试在旧实现下也是绿的（假测试）。
    能区分新旧的可观测量是「全部活跃流一个都不许掉」+「软超限时总数恰好是 N+1」。
    """
    raw = _make()
    now = time.monotonic()
    # 全部是「创建很久、但最近刚活动」的流：按 t0 淘汰的实现会把它们踢掉
    for i in range(adapter._MAX_STREAMS):
        raw._ld_stream_put(f"active-{i}", {"t0": now - 99999.0, "last_at": now,
                                          "message_id": f"om_a{i}"})
        raw._ld_streams[f"active-{i}"]["alive_at"] = now        # 「刚刚活动过」
    assert len(raw._ld_streams) == adapter._MAX_STREAMS
    raw._ld_stream_put("active-new", {"t0": now, "last_at": now, "message_id": "om_an"})
    assert all(f"active-{i}" in raw._ld_streams for i in range(adapter._MAX_STREAMS)), \
        "活跃流被淘汰了 —— 这会导致同一回合另发一张新卡（重复卡）"
    assert len(raw._ld_streams) == adapter._MAX_STREAMS + 1, \
        "软超限必须**保留全部活跃流**，只是多留一个（旧实现会掉到 49 个）"
    assert "active-new" in raw._ld_streams


def test_stream_state_rejects_stale_turn_events():
    """迟到的旧回合事件必须被丢弃，不能清掉新回合的面板。

    结构性成因：Hermes 给每个 (钩子名, 回调) 配独立队列 + 独立守护线程，**跨钩子无顺序
    保证** —— t1 的推理增量可能排在队列里，等 t2 的 on_stream_start 先派发之后才到达。
    """
    panel.reset()
    panel.record_reasoning("s1", "t1", "第一回合的推理")
    # 新回合开始（权威信号）
    panel.begin_turn("s1", "t2")
    panel.record_reasoning("s1", "t2", "第二回合的推理")
    assert "第二回合的推理" in panel.snapshot()["reasoning"]

    # t1 的迟到增量到达 —— 必须被丢弃
    panel.record_reasoning("s1", "t1", "迟到的第一回合增量")
    snap = panel.snapshot()
    assert "迟到的第一回合增量" not in snap["reasoning"], "迟到的旧回合事件污染了新回合"
    assert "第二回合的推理" in snap["reasoning"], "新回合的数据被清掉了"
    assert snap["turn_id"] == "t2", "turn_id 被倒回旧回合了"
    panel.reset()


def test_stream_state_drops_unattributed_events():
    """空 session_id 不许进桶 —— 否则会生成匿名桶被 snapshot 选中，串到别的卡片上。"""
    panel.reset()
    panel.record_reasoning("", "t1", "无主的推理")
    panel.record_tool_started("", "t1", "bash", {}, "c1")
    assert panel.snapshot() is None, "无归属的数据不该被任何卡片取到"
    assert "" not in panel._STATE
    panel.reset()


def test_native_streaming_never_blanks_body_on_markdown_rule():
    """P0 回归：模型自己写 markdown 分隔线，**绝不能**把正文弄没。

    曾经的「叙述归档」用裸分隔符 ``"\\n\\n---\\n"`` 判断核心有没有拼工具进度块，
    但核心在没有工具进度时帧文本就是累积正文本身（``gateway/stream_consumer.py``
    的 ``_compose_frame_content``）。于是模型写一条 ``---`` 就被误判成进度块：
    归档点推到分隔线处 → 之后每帧算出的正文都是空串 → finalize 的
    ``display or " "`` 让整条回答只剩一个空格；而核心按完整帧文本乐观记账、
    判定「已送达」，不会再补发 —— 回答彻底消失且不报任何错。
    """
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0
    try:
        panel.reset()
        raw = _make()
        updates = _wire_patch(raw)
        assert _run(raw.send_stream_frame("", finalize=False, chat_id="oc_r",
                                          turn_id="tr")) is True
        # 模型写了一条标准的 markdown 分隔线，整回合没有调用任何工具
        frames = ["第一章要点",
                  "第一章要点\n\n---\n第二章要点",
                  "第一章要点\n\n---\n第二章要点\n\n结论就这些。"]
        for text in frames:
            assert _run(raw.send_stream_frame(text, finalize=False, chat_id="oc_r",
                                              turn_id="tr")) is True
        assert _run(raw.send_stream_frame(frames[-1], finalize=True, chat_id="oc_r",
                                          turn_id="tr")) is True
        final = json.dumps(json.loads(updates[-1]["content"]), ensure_ascii=False)
        assert "第一章要点" in final, "分隔线**之前**的内容不能丢"
        assert "第二章要点" in final, "分隔线**之后**的内容更不能丢"
        assert "结论就这些。" in final, "末帧正文不能只剩一个空格"
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_send_clarify_without_choices_falls_back():
    raw = _make()
    _run(raw.send_clarify("oc_1", "开放式问题？", None, "cid", "sk"))
    assert raw.calls[0][0] == "SUPER.clarify", raw.calls


def test_send_clarify_cards_off_falls_back():
    defaults = dict(adapter._DEFAULTS)
    try:
        adapter.configure(clarify_cards=False)
        raw = _make()
        _run(raw.send_clarify("oc_1", "选哪个？", ["A", "B"], "cid", "sk"))
        assert raw.calls[0][0] == "SUPER.clarify", raw.calls
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_card_action_unrelated_value_falls_through():
    raw = _make()
    data = types.SimpleNamespace(event=types.SimpleNamespace(
        action=types.SimpleNamespace(value={"hermes_action": "approve"}),
        operator=types.SimpleNamespace(open_id="ou_ok")))
    assert raw._on_card_action_trigger(data) == "SUPER_RESULT"
    assert raw.calls == [("SUPER.trigger",)], raw.calls


def test_probe_click_leaves_the_only_evidence_we_get():
    """探针卡的那一击**必须真的落进日志** —— 那是「2.0 `button` 行不行」唯一能拿到的凭据。

    为什么要有一条断言守着这条链（2026-09-16 加）：探针 ⑮ 是「**请真人点一次**」的一格，
    而那一击**只能点一次**。用户点下去之后我们手上只有网关日志里那一行 —— 如果派发链在中途断了
    （判据键改名 / 提前被澄清分支截走 / 降级成 debug），用户点完**什么都看不到**，
    而我们只会得出「点击没到服务端」的结论 ⇒ **把一次成功的真机实验误判成失败**，
    再据此决定「澄清卡不加按钮形态」。这正是本项目最怕的形态：**误诊比没有结论更糟**。
    ⇒ 本地能证的部分先证掉：`event.action.value` 一进适配器，就**一定**会打出那行。

    ⚠️ 刻意**不 import** `probe_render.py`：真机探针不被任何门禁 import 是一条**有意的隔离**
    （它一被 import，以后动探针就得跟着重跑一次 65 分钟的全量变异）。这里只造**同形状的值**；
    卡片那一侧的形状由 `probe_render.probe_card_problems()` 在**发出去之前**自检（发之前就拦）。
    """
    raw = _make()
    with _LogCapture("larkdeck") as recs:
        raw._on_card_action_trigger(types.SimpleNamespace(
            event=types.SimpleNamespace(
                action=types.SimpleNamespace(tag="button", option=None,
                                             input_value=None,
                                             value={cards.PROBE_VALUE_KEY: True, "kind": "button"}),
                operator=types.SimpleNamespace(open_id="ou_probe"),
                context=types.SimpleNamespace(open_chat_id="oc_probe"))))
    lines = [r.getMessage() for r in recs]
    hit = [l for l in lines if "探针点击到达" in l]
    assert hit, f"探针点击没留下凭据 —— 用户点完将无法判定（这一格就白点了）：{lines}"
    # `tag=button` 必须**就在那一行里**：那是「2.0 的 button 回调能到服务端」的原话凭据，
    # 只写「探针点击到达」而不带 tag，事后分不清是 button 还是 select_static 那一格。
    assert "tag=button" in hit[0], hit[0]
    # 反向：不许被当成澄清点击（那会去解一个不存在的澄清，用户看到「提交未生效」而困惑）
    assert raw.calls == [], f"探针点击不该交给内置处理：{raw.calls}"
    # ⚠️⚠️ **堵两个「因错误原因通过」的洞**（2026-09-16 对抗审计实测，两条都真的全绿过）：
    #  ① 上面那些断言只认 logger 的**名字子树** ⇒ 把留痕改到
    #     `logging.getLogger("larkdeck.probe")` 上，四门禁**照样全绿**（实测）——
    #     而生产里去找日志的人是按 `larkdeck` 找的，凭据等于凭空消失；
    #  ② `logger.propagate = False` 也**照样全绿**（实测）—— 而 Hermes 的文件 handler 挂在
    #     **root** 上（`hermes_logging.py` 的注释：「Always on the root logger so records from
    #     any logger reach the queue」）⇒ 那行**永远到不了 `agent.log`**。
    #  ⇒ 判据必须钉在**对象身份 + 可达性**上，不是钉在名字上。
    assert adapter.logger is logging.getLogger("larkdeck"), \
        "凭据必须打在 `larkdeck` 这个 logger **对象**上（换个名字照样能过测试，但没人会去那儿找）"
    assert logging.getLogger("larkdeck").propagate is True, \
        ("这个 logger 必须向 root 传播：Hermes 的文件 handler 挂在 root 上，"
         "关掉传播 = 那行永远到不了 agent.log（而门禁察觉不到）")

    # ⚠️ **判据的语义本身也要钉住**（这是「探针自检与适配器分叉」那个真缺陷的根因）：
    # `cards.is_probe_value()` 是**适配器与探针共用**的唯一一处判据，判的是 `value.get(...)`
    # 的**真值性**，不是「键存在」—— 两者在 `{"larkdeck_probe": False}` 上分叉，而那一格
    # 唯一的凭据就是这行日志 ⇒ 「假值算不算探针」必须是被断言钉住的**行为**。
    assert cards.is_probe_value({cards.PROBE_VALUE_KEY: True}) is True
    assert cards.is_probe_value({cards.PROBE_VALUE_KEY: False}) is False, \
        "判据必须是**真值性**：假值不算探针（探针自检与适配器共用这一条，分叉会让点击静默无凭据）"
    assert cards.is_probe_value(cards.PROBE_VALUE_KEY) is False, "裸字符串不算探针（适配器 json.loads 会失败）"
    assert cards.is_probe_value(None) is False


def test_clarify_click_resolves_gateway():
    raw = _make()
    resolved: list = []
    fake_tools, fake_cg = _fake_clarify_gateway(resolved, commit=True)
    try:
        data = types.SimpleNamespace(
            event=types.SimpleNamespace(
                action=types.SimpleNamespace(value={
                    "larkdeck_action": "clarify", "clarify_id": "cid-9",
                    "answer": "B 方案", "question": "选哪个？"}),
                operator=types.SimpleNamespace(open_id="ou_ok")),
        )
        out = raw._on_card_action_trigger(data)
        assert out is not None and raw.calls == [], "自己人的点击不应交给内置处理"
        # 提交是**同步**的：网关那两处只做锁内 dict 取写 + threading.Event.set()，
        # 不碰事件循环（已对 Hermes 源码核实），所以当场就能断言，无需再驱动 loop。
        assert resolved == [("cid-9", "B 方案")], resolved
        assert raw.card_updates and raw.card_updates[-1] is not None, \
            "提交成功后应把卡片回填成「已答复」"
    finally:
        _drop_fake_clarify_gateway()


def test_clarify_click_other_marks_awaiting_text():
    raw = _make()
    resolved: list = []
    fake_tools, fake_cg = _fake_clarify_gateway(resolved, commit=True)
    try:
        data = types.SimpleNamespace(
            event=types.SimpleNamespace(
                action=types.SimpleNamespace(value={
                    "larkdeck_action": "clarify", "clarify_id": "cid-3",
                    "answer": cards.OTHER_VALUE}),
                operator=types.SimpleNamespace(open_id="ou_ok")),
        )
        raw._on_card_action_trigger(data)
        # 提交是同步的（网关只做锁内 dict 写 + Event.set，不碰事件循环），无需再驱动 loop
        assert resolved == [("cid-3", "__await_text__")], resolved
    finally:
        _drop_fake_clarify_gateway()


def test_clarify_click_that_did_not_commit_must_not_refill_card():
    """提交未生效时**绝不能**把卡片换成「已答复」。

    触发：重复点击同一条澄清，或点击一个已被超时 / 文字回答消费掉的澄清 ——
    网关此时返回 False。若仍回填卡片，就会出现「卡片说已收到、agent 仍阻塞在网关」：
    答案永久丢失，而且卡片已变成已解状态，用户连重试的机会都没有。
    """
    raw = _make()
    resolved: list = []
    fake_tools, fake_cg = _fake_clarify_gateway(resolved, commit=False)
    try:
        data = types.SimpleNamespace(
            event=types.SimpleNamespace(
                action=types.SimpleNamespace(value={
                    "larkdeck_action": "clarify", "clarify_id": "cid-dup",
                    "answer": "B 方案", "question": "选哪个？"}),
                operator=types.SimpleNamespace(open_id="ou_ok")),
        )
        out = raw._on_card_action_trigger(data)
        assert resolved == [("cid-dup", "B 方案")], "还是要尝试提交"
        assert out is not None, "但必须给回调一个响应"
        # ⚠️ R8 起失败态改走 **toast 响应**（不再经过 `_card_response`），所以判据从
        # 「记录里恰好一个 None」换成更强的一句：**任何一次响应里都不许出现卡片**。
        assert all(card is None for card in raw.card_updates), \
            f"提交没成功就不能回填「已答复」卡片：{raw.card_updates!r}"
        assert getattr(out, "card", None) is None, \
            f"响应里不许带卡（带卡就等于回填「已答复」）：{out!r}"
    finally:
        _drop_fake_clarify_gateway()


def test_clarify_click_from_unauthorized_user_is_ignored():
    """未授权用户的点击**不得**走到澄清解析（这是「群里谁能替你答」的唯一一道门）。

    判别力（R8 审计中-4）：这条原来是 `assert raw.submitted == []`，而
    `StubAdapter.submitted` 全仓**只有初始化、没有任何写入点** ⇒ 断言恒真、把鉴权拦截
    那四行删掉四门禁全绿。现在两处都补成**真的会记录**的观测点：
      * `_fake_clarify_gateway(..., record=submitted)` —— 网关只要被**发起过**提交就记一条；
      * `raw.card_updates` / `raw.calls` —— 换卡与「交回内置」的痕迹。
    为什么这道门值钱：默认配置下官方的 `_is_interactive_operator_authorized` 在
    `_admins | _allowed_group_users` **为空时恒返回 True**（Hermes
    `plugins/platforms/feishu/adapter.py:2103-2111`）⇒ 没有白名单时「谁能点澄清卡」
    **全靠这一句**；一旦被重构挪走，群里任何人都能替用户把澄清答掉。
    """
    raw = _make()
    resolved: list = []
    submitted: list = []
    _fake_clarify_gateway(resolved, record=submitted)
    try:
        data = types.SimpleNamespace(
            event=types.SimpleNamespace(
                action=types.SimpleNamespace(value={
                    "larkdeck_action": "clarify", "clarify_id": "cid-4", "answer": "A"}),
                operator=types.SimpleNamespace(open_id="ou_intruder")),
        )
        raw._on_card_action_trigger(data)
    finally:
        _drop_fake_clarify_gateway()
    assert submitted == [], f"未授权用户的点击不得触发澄清解析：{submitted!r}"
    assert resolved == [], f"网关上不许有任何提交痕迹：{resolved!r}"
    assert all(card is None for card in raw.card_updates), \
        f"未授权的点击不许换卡：{raw.card_updates!r}"
    assert raw.calls == [], f"更不许交回内置实现：{raw.calls!r}"


# --------------------------------------------------------------------------- #
# 指标渲染：上下文用量 / 页脚（纯函数边界）
# --------------------------------------------------------------------------- #
def test_compact_tokens() -> None:
    assert cards.compact_tokens(0) == "0"
    assert cards.compact_tokens(999) == "999"
    assert cards.compact_tokens(1000) == "1k"          # 不留 "1.0k"
    assert cards.compact_tokens(45200) == "45.2k"
    assert cards.compact_tokens(200000) == "200k"      # 不留 "200.0k"
    assert cards.compact_tokens(1000000) == "1m"
    assert cards.compact_tokens(1500000) == "1.5m"
    assert cards.compact_tokens(2000000) == "2m"
    # 脏输入一律空串，绝不吐出 "None" 这种字符串拼进卡片
    assert cards.compact_tokens(None) == ""
    assert cards.compact_tokens("x") == ""
    assert cards.compact_tokens(-5) == ""
    assert cards.compact_tokens(True) == "", "bool 是 int 的子类，必须挡掉"


def test_progress_cells_boundaries() -> None:
    assert cards.progress_cells(0) == "░" * 8
    assert cards.progress_cells(100) == "█" * 8
    assert cards.progress_cells(50) == "████░░░░"
    assert cards.progress_cells(150) == "█" * 8, "超过 100% 必须夹住，不能画出越界条"
    assert cards.progress_cells(-10) == "░" * 8
    assert len(cards.progress_cells(23)) == 8, "格子数恒定，否则页脚会跳宽"
    # 单调性：占用只增不减时，每格密度不允许回退
    ranks = {"░": 0, "▒": 1, "▓": 2, "█": 3}
    prev = [-1] * 8
    for pct in range(0, 101, 5):
        now = [ranks[c] for c in cards.progress_cells(pct)]
        assert all(n >= p for n, p in zip(now, prev)), f"{pct}% 处进度条回退了"
        prev = now


def test_context_indicator_styles() -> None:
    assert cards.context_indicator(45200, 200000) == "ctx 45.2k/200k · 23%"
    assert cards.context_indicator(45200, 200000, style="bar") == "ctx [██░░░░░░] 23%"
    both = cards.context_indicator(45200, 200000, style="both")
    assert both.startswith("ctx 45.2k/200k [") and both.endswith("23%")
    # 未知样式回落 text，不抛也不空白
    assert cards.context_indicator(45200, 200000, style="瞎写") == "ctx 45.2k/200k · 23%"
    # 数据不全 → 空串（调用方据此不渲染这一段）
    assert cards.context_indicator(None, 200000) == ""
    assert cards.context_indicator(100, None) == ""
    assert cards.context_indicator(100, 0) == ""
    assert cards.context_indicator(0, 200000) == ""
    # 超过上限夹在 100%，不显示 250%
    assert "100%" in cards.context_indicator(500000, 200000)


def test_format_elapsed_and_footer_line() -> None:
    assert cards.format_elapsed(12.34) == "12.3s"
    assert cards.format_elapsed(60) == "1m00s"
    assert cards.format_elapsed(125.7) == "2m05s"
    assert cards.format_elapsed(-1) == "0.0s"
    line = cards.footer_line(status="✅ 已完成", model="Sonnet 4.6",
                             context="ctx 45.2k/200k · 23%", duration=12.3)
    # 用户 2026-09-17 指定的页脚阅读顺序：状态 → 时长 → 模型 → 其余指标。
    assert line == "✅ 已完成 · ⏱ 12.3s · 🤖 Sonnet 4.6 · ctx 45.2k/200k · 23%"
    assert cards.footer_line() is None
    assert cards.footer_line(model="m") == "🤖 m"
    assert cards.footer_line(model="m", duration=1.2) == "⏱ 1.2s · 🤖 m",         "模型前必须有耗时；顺序不能倒（对齐 AP 的 `已完成 · 1m 4s · ✳ model`）"
    assert cards.footer_line(status="S", duration=1.2, model="m") == "S · ⏱ 1.2s · 🤖 m"
    assert cards.footer_line(duration=0.0) is None, "0 秒不算耗时（首帧就是这个情况）"
    assert cards.footer_line(duration=0.05) is None, "不足 0.1s 不显示（native seed 帧）"
    assert cards.footer_line(tools=3) == "🔧 3"
    assert cards.footer_line(tools=0) is None


# --------------------------------------------------------------------------- #
# 溢出保护（统一面板上限）
# --------------------------------------------------------------------------- #
def test_truncate_marks_omission() -> None:
    assert cards.truncate("hello", 100) == "hello"
    assert cards.truncate("hello", 0) == "hello", "limit<=0 表示不限制"
    long_text = "word " * 400
    cut = cards.truncate(long_text, 100)
    assert len(cut) < len(long_text)
    assert "已省略" in cut, "截断必须留痕，不能静默切掉"
    assert cut.count(">") == 1, "只补一行说明"
    # 截断点回退到词边界：不会把词劈成两半
    assert cut.split("\n")[0].endswith("word")


def test_tool_step_formatting() -> None:
    assert cards.tool_step("read_file", status="ok", duration_ms=2300,
                           preview='{"path": "/tmp/a.txt"}') \
        == '✅ read_file · 2.3s · `{"path": "/tmp/a.txt"}`'
    assert cards.tool_step("bash", status="running") == "⏳ bash"
    assert cards.tool_step("bash", status="error", duration_ms=50).startswith("❌ bash · 0.1s")
    assert cards.tool_step("bash", status="blocked") == "⛔ bash"
    # 未知状态兜底；耗时缺失 / 0 / 非数字都不渲染时长
    assert cards.tool_step("x", status="weird") == "• x"
    assert cards.tool_step("x", duration_ms=None) == "✅ x"
    assert cards.tool_step("x", duration_ms=0) == "✅ x"
    assert cards.tool_step("x", duration_ms=True) == "✅ x", "bool 不是数字"
    assert cards.tool_step("", status="ok") == "✅ tool"
    assert cards.tool_step("x", preview="a`b") == "✅ x · `a'b`", "预览里的反引号不能破坏行内代码"



def test_tool_step_unknown_and_cancelled_statuses_never_crash():
    """Hermes 会 emit ``status="cancelled"``（中断/跳过的工具）——未知状态不能把面板带走。

    2026-09-17 审计 H1：旧写法 ``_TOOL_STATUS_STYLES.get(...)`` 后直接解包，未命中时先抛
    ``TypeError``，``_ld_panel`` 的列表推导整块失败 ⇒ 一个被取消的工具就能让面板、推理和
    状态色全部消失。这里钉「未知状态有兜底词」与「中性主题仍走旧 ``•`` 兜底」两条。
    """
    cases = {
        "cancelled": ("Cancelled", "grey"),
        "canceled": ("Cancelled", "grey"),
        "skipped": ("Skipped", "grey"),
        "success": ("Succeeded", "green"),
        "": ("Unknown", "grey"),
        "weird": ("Weird", "grey"),
        "timeout": ("Timed out", "red"),
    }
    for status, (label, color) in cases.items():
        try:
            got = cards.tool_step("read_file", status=status, theme="ap_lite")
        except TypeError as exc:
            raise AssertionError(
                f"未知工具状态 {status!r} 让 tool_step 解包崩溃：{exc}") from exc
        assert got.startswith(
            f"📖 **Read file** · <font color='{color}'>{label}</font>"), got
    # neutral 主题仍保持旧行为（cancelled 现在有 ⛔ 兜底，不再是未知 •）
    assert cards.tool_step("read_file", status="cancelled") == "⛔ read_file"
    assert cards.tool_step("read_file", status="skipped") == "⏭ read_file"


def test_tool_duration_and_section_heading_helpers_are_dirty_data_safe():
    """坏 hook 数据（NaN / Inf / 超大 int / 非 int）不能让面板整块消失。"""
    assert cards._format_tool_duration(25) == "25 ms"
    assert cards._format_tool_duration(1200) == "1.2 s"
    assert cards._format_tool_duration(float("nan")) == ""
    assert cards._format_tool_duration(float("inf")) == ""
    assert cards._format_tool_duration(10 ** 400) == ""
    assert cards._format_tool_duration(86_400_000) == ">24h"
    huge = [{"elapsed_ms": 10 ** 400}]
    assert cards._rounds_elapsed_ms(huge) == 86_400_000
    heading = cards._thinking_heading(huge)
    assert heading.startswith("<font color='grey'>💭 思考"), heading
    assert cards._tools_heading(None) == ""
    assert cards._tools_heading(True) == ""
    assert cards._tools_heading(0) == ""
    assert cards._tools_heading(2) == "<font color='grey'>🛠️ 工具执行 · 2 步</font>"
    # neutral 与每轮标题也必须走同一套坏值防护（Phase 1 审计 H-1/M-1）
    for bad in (10 ** 400, float("inf"), float("nan")):
        try:
            neutral = cards.tool_step("x", status="ok", duration_ms=bad)
        except Exception as exc:
            raise AssertionError(f"neutral 坏耗时崩溃：{exc}") from exc
        assert neutral == "✅ x", neutral
        assert cards.tool_step("x", status="ok", duration_ms=bad, theme="ap_lite") == \
            "🧰 **x** · <font color='green'>Succeeded</font>"
    try:
        round_title = cards._round_title(1, 10 ** 400)
    except Exception as exc:
        raise AssertionError(f"坏 elapsed 让轮标题崩溃：{exc}") from exc
    assert round_title == "第 1 轮 · 1440m00s"
    huge_round = cards.unified_panel(rounds=[{"text": "a", "elapsed_ms": 10 ** 400}])
    assert huge_round is not None
    assert "1440m00s" in " ".join(e.get("content", "") for e in huge_round["elements"])


def test_json_fragment_unescape_and_preview_value_are_not_fooled():
    """截断预览的转义还原不能被 ``\\\\n`` 顺序改坏，也不能把嵌套字符串当参数。"""
    assert cards._unescape_json_fragment(r"C:\\new") == r"C:\new"
    assert cards._unescape_json_fragment(r"a\nb") == "a b"
    assert cards._preview_value(
        '{"note": "\\"command\\": \\"rm -rf /\\""}', "terminal") is None
    assert cards._preview_value('{"command": "ls -la', "terminal") == "ls -la"
    assert cards._unescape_json_fragment(r"\u4e2d") == "中"
    try:
        illegal = cards._unescape_json_fragment(r"\u-123")
    except Exception as exc:
        raise AssertionError(f"非法 \\u 片段让解析崩溃：{exc}") from exc
    assert illegal == r"\u-123", illegal
    assert cards._unescape_json_fragment(r"\u12") == r"\u12"
    assert cards._unescape_json_fragment(r"\ud83d") == "\ufffd"
    # 合法 JSON 但已知类别没有本类别的键 ⇒ 不显示细节（不得把 terminal_id/note 当命令/路径）
    assert cards._tool_detail("read_file", '{"terminal_id": "abc", "offset": 3}') == ""
    assert cards._tool_detail("run_command", '{"note": "do not show me"}') == ""
    assert cards._tool_detail("read_file", '{"path": {"a": 1}, "file": "/tmp/a"}') == "/tmp/a"


def test_small_tool_char_limit_never_cuts_font_tag():
    """小 max_tool_result_chars 下先剥标签再截断，不能留下半个 `<font>`。"""
    item = cards.tool_step("read_file", status="ok", duration_ms=120,
                           preview='{"path": "/tmp/a.txt"}', theme="ap_lite")
    out = cards.panel_tools_markdown(tools=[item], max_tool_chars=40)
    # 分区标题本身也是 `<font>`，这里只看工具行那一块；工具行必须先剥标签再截断
    tool_part = out.split("\n\n", 1)[-1]
    assert "<font" not in tool_part, tool_part
    assert "已省略" in out, out


def test_color_tag_fallback_covers_status_heading_and_detail():
    """D3：真机若不认 `<font color>`，三类消费者必须有无色降级路径。"""
    try:
        adapter.configure(panel_color_tags=False)
        assert cards.color_tags_enabled() is False, "配置没有把降级开关推给 cards"
        read = cards.tool_step("read_file", status="ok", duration_ms=100,
                               preview='{"path": "/tmp/a.txt"}', theme="ap_lite")
        assert "<font" not in read and "Succeeded" in read and "↳ /tmp/a.txt" in read, read
        body = cards.panel_rounds_markdown(
            rounds=[{"text": "想一下", "elapsed_ms": 1000}])
        assert "<font" not in body and "💭 思考" in body, body
        tools = cards.panel_tools_markdown(
            tools=[cards.tool_step("read_file", status="ok", theme="ap_lite")])
        assert "<font" not in tools and "🛠️ 工具执行 · 1 步" in tools, tools
    finally:
        adapter.configure(panel_color_tags=True)
    assert cards.color_tags_enabled() is True, "恢复默认后颜色标签必须回来"


def test_theme_symbols_and_tool_icons_are_pinned():
    """P2 主题层：符号/图标是**默认观感**的一部分，必须逐字冻结。

    这条同时钉三件事：
      * ``footer_line`` / ``tool_step`` 的**缺省仍是 neutral**（直接调用者一个字节不变）；
      * ``ap_lite`` 只换符号与工具图标，不改状态符号、数字格式、预览转义；
      * 工具分类是 **token 精确匹配**，不是子串包含（`false` / `catalog` 不许被 read 抢走，
        `create_image` 必须归 image、`run_skill` 必须归 skill）。
    """
    assert cards.THEME_NEUTRAL == "neutral"
    assert cards.THEME_AP_LITE == "ap_lite"
    assert cards.THEME_AP_BUBBLE == "ap_bubble"
    # 缺省 = neutral：既有调用点（探针、单测、任何未传 theme 的直接调用）零变化；
    # 页脚顺序本身对两档主题一致：状态 → 时长 → 模型 → 轮次 → 工具。
    assert cards.footer_line(model="m", rounds=3, tools=2, duration=1.2) == \
        "⏱ 1.2s · 🤖 m · 🧠 3 · 🔧 2"
    assert cards.tool_step("read_file", status="running") == "⏳ read_file"
    # ap_lite：只动模型/轮次/工具数/耗时这几格
    assert cards.footer_line(model="m", rounds=3, tools=2, duration=1.2,
                             theme="ap_lite") == "⏱ 1.2s · 🤖 m · 🌊 3 · 🧰 2"
    assert cards.footer_line(rounds=1, tools=1, cache=75.0, api=7, ttfb=0.4,
                             theme="ap_lite") == "🌊 1 · 🧰 1 · ⚡ 75% · 🔁 7 · 🐢 0.4s"
    # ap_bubble：可选档，人物 emoji 只在显式选择时出现
    assert cards.footer_line(model="m", rounds=1, tools=1, duration=1.2,
                             theme="ap_bubble") == "✨ 1.2s · 👸🏻 m · 🌊 1 · 🫧 1"
    assert cards.tool_step("read_file", status="running",
                           theme="ap_bubble") == \
        "👩🏻‍🏫 **Read file** · <font color='turquoise'>Running</font>"
    # CLS 风格契约：图标 + 加粗动作名 + 耗时 + **带颜色的状态词**，细节灰色小字另起一行；
    # 不再用 ✅/⏳ 前缀（状态词本身就是可读的状态），也不把 JSON 原文倒回卡上。
    read = cards.tool_step("read_file", status="ok", duration_ms=100,
                           preview='{"path": "/tmp/a.txt"}', theme="ap_lite")
    assert read == ("📖 **Read file** (100 ms) · <font color='green'>Succeeded</font>\n"
                    "<font color='grey'>↳ /tmp/a.txt</font>"), read
    cmd = cards.tool_step("run_command", status="ok",
                          preview='{"command": "ls -la && pwd"}', theme="ap_lite")
    assert cmd == ("⌨️ **Run command** · <font color='green'>Succeeded</font>\n"
                   "<font color='grey'>↳ ls -la 等 2 条</font>"), cmd
    assert "{" not in cmd and '"' not in cmd, f"细节行不许露出 JSON：{cmd!r}"
    # 失败 / 阻塞也必须有一眼可读的状态词（颜色 + 文字，不靠 emoji 单独承载）
    assert "<font color='red'>Failed</font>" in cards.tool_step(
        "run_command", status="error", theme="ap_lite")
    assert "<font color='red'>Blocked</font>" in cards.tool_step(
        "run_command", status="blocked", theme="ap_lite")
    # 预览被上游截断（不是合法 JSON）时做**有界** key 提取：既保留命令细节，又不倾倒原文
    truncated = cards.tool_step("run_command", status="ok", duration_ms=1200,
                                preview='{"command": "sed -n \'1,200p\' /very/long/path/to/file',
                                theme="ap_lite")
    assert truncated == (
        "⌨️ **Run command** (1.2 s) · <font color='green'>Succeeded</font>\n"
        "<font color='grey'>↳ sed -n '1,200p' /very/long/path/to/file</font>"), truncated
    # 细节里的 `<` 必须被中和，否则参数里的 `</font>` 会把灰色小字变成正文
    safe = cards.tool_step("run_command", status="ok",
                           preview='{"command": "echo </font> hi"}', theme="ap_lite")
    assert "</font> hi" not in safe and "‹/font> hi" in safe, safe
    # 工具图标：每个类别至少一条；标签必须是人话，图标与类别配对。
    icon_cases = {
        "read_file": ("📖", "Read file"), "write_file": ("✍️", "Write file"),
        "run_command": ("⌨️", "Run command"),
        "web_search": ("🌐", "Web search"), "grep": ("🔍", "Search"),
        "skill_view": ("🧩", "Load skill"),
        "delegate_task": ("🤝", "Run sub-agent"), "create_image": ("🖼️", "Image"),
        # 审计 C1：读取类必须优先于 terminal，`read_terminal` 归 📖；`close_terminal`
        # 没有 read token，仍归 ⌨️（关闭终端是终端动作）。
        "read_terminal": ("📖", "Read file"), "close_terminal": ("⌨️", "Run command"),
    }
    for name, (icon, label) in icon_cases.items():
        got = cards.tool_step(name, status="ok", duration_ms=100,
                              theme="ap_lite")
        assert got.startswith(
            f"{icon} **{label}** (100 ms) · <font color='green'>Succeeded</font>"), \
            f"{name}: {got!r}"
    # token 精确匹配：短键不许在更长单词里误命中；未知工具退回默认工具图标
    for name in ("false", "catalog", "results", "tool"):
        got = cards.tool_step(name, status="ok", theme="ap_lite")
        assert got == f"🧰 **{name}** · <font color='green'>Succeeded</font>", \
            f"{name} 被误分类：{got!r}"
    assert cards.tool_step("ls", status="ok", theme="ap_lite") == \
        "📖 **Read file** · <font color='green'>Succeeded</font>"
    # 认不出的主题退回 neutral（不猜、不放大）
    assert cards.theme_name("nonsense") == "neutral"
    assert cards.theme_name(None) == "neutral"
    assert cards.tool_step("read_file", status="ok", theme="nonsense") == "✅ read_file"


def test_plugin_manifest_declares_theme_default_ap_lite():
    """默认主题必须同时写进代码默认值与 `plugin.yaml`；两处分叉时测试必须红。"""
    assert adapter._DEFAULTS["theme"] == "ap_lite"
    manifest = (_pathlib.Path(_REPO_PARENT) / "larkdeck" / "plugin.yaml").read_text(
        encoding="utf-8")
    match = re.search(r"^  theme:\n(?:    .*\n)+", manifest, re.M)
    assert match, "plugin.yaml 缺少 theme 配置块"
    block = match.group(0)
    assert 'type: string' in block, block
    assert 'default: "ap_lite"' in block, block


def test_adapter_theme_wiring_uses_default_ap_lite_and_neutral_fallback():
    """P2 主题必须真的接进三条渲染路径，不能只让纯函数自己绿。

    三条路径 = `_ld_panel`（普通卡 / patch 收尾）、`_ld_panel_parts`（CardKit 两块）、
    `_ld_panel_markdown`（对拍基准 / 降级车道）。判据是**渲染出来的内容**，
    不是「调用了 theme=」这句源码声明。
    """
    defaults = dict(adapter._DEFAULTS)
    panel.reset()
    context.reset()
    try:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(adapter._DEFAULTS)
        panel.record_reasoning("s1", "t1", "先看文件。")
        panel.record_tool_started("s1", "t1", "read_file", {"path": "/tmp/a.txt"}, "c1")
        node = adapter.LarkDeckMixin._ld_panel()
        assert node is not None
        title = node["header"]["title"]["content"]
        # CLS 观感：思考与工具两个不同的 emoji；思考显示耗时、工具显示步数。
        assert "💭 思考" in title and "🛠️ 工具执行" in title, title
        assert "🌊" not in title and "🧰" not in title, title
        body, tools = adapter.LarkDeckMixin._ld_panel_parts()
        assert "先看文件。" in body and "💭 思考" in body, (body, tools)
        assert "🛠️ 工具执行 · 1 步" in tools, (body, tools)
        assert "📖 **Read file**" in tools and "Running" in tools, tools
        assert "/tmp/a.txt" in tools, tools
        assert "📖 **Read file**" in adapter.LarkDeckMixin._ld_panel_markdown(), \
            adapter.LarkDeckMixin._ld_panel_markdown()

        adapter.configure(theme="neutral")
        title = adapter.LarkDeckMixin._ld_panel()["header"]["title"]["content"]
        # 主题只改符号/图标；「💭/🛠️ + 耗时/步数」是 2026-09-17 的布局，与主题无关。
        assert "💭 思考" in title and "🛠️ 工具执行 · 1 步" in title, title
        _, tools = adapter.LarkDeckMixin._ld_panel_parts()
        assert "read_file" in tools and "📖" not in tools, tools
        assert "Running" not in tools, "neutral 保留旧的 emoji 前缀状态，不掺 CLS 状态词"

        adapter.configure(theme="nonsense")
        title = adapter.LarkDeckMixin._ld_panel()["header"]["title"]["content"]
        assert "💭 思考" in title and "🛠️ 工具执行 · 1 步" in title, \
            f"未知主题必须退回 neutral 的符号，但布局摘要不变：{title!r}"

        adapter.configure(theme="ap_bubble")
        _, tools = adapter.LarkDeckMixin._ld_panel_parts()
        assert "👩🏻‍🏫 **Read file**" in tools, tools
    finally:
        panel.reset()
        context.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_panel_summary_uses_cls_section_emojis_and_i18n():
    """折叠面板标题：💭 思考（耗时）+ 🛠️ 工具执行（步数），且必须是双语 i18n 节点。

    CLS 的这两个面板标题分别带不同 emoji；我们收在一个折叠面板里，所以合并成一行。
    判据分三种数据组合 + 空数据，防止「只有工具时把思考段也写出来」这种半成品。
    """
    snap = {"rounds": [{"text": "想一下", "elapsed_ms": 1600}],
            "tools": [{"name": "read_file"}]}
    node = adapter.LarkDeckMixin._ld_panel_summary(snap)
    assert node is not None and node["tag"] == "plain_text", node
    assert node["content"] == "💭 思考 1.6s · 🛠️ 工具执行 · 1 步", node
    assert node["i18n_content"]["en_us"] == "💭 Thought 1.6s · 🛠️ Tools · 1 step", node
    assert node["i18n_content"]["zh_cn"] == node["content"]

    many = adapter.LarkDeckMixin._ld_panel_summary(
        {"rounds": [{"text": "想一下", "elapsed_ms": 1600}],
         "tools": [{"name": "a"}, {"name": "b"}]})
    assert many["content"] == "💭 思考 1.6s · 🛠️ 工具执行 · 2 步", many
    assert many["i18n_content"]["en_us"] == "💭 Thought 1.6s · 🛠️ Tools · 2 steps", many

    only_tools = adapter.LarkDeckMixin._ld_panel_summary(
        {"rounds": [], "tools": [{"name": "a"}, {"name": "b"}]})
    assert only_tools["content"] == "🛠️ 工具执行 · 2 步", only_tools
    assert only_tools["i18n_content"]["en_us"] == "🛠️ Tools · 2 steps", only_tools

    only_reasoning = adapter.LarkDeckMixin._ld_panel_summary(
        {"rounds": [{"text": "想", "elapsed_ms": 0}], "tools": []})
    assert only_reasoning["content"] == "💭 思考", only_reasoning
    assert adapter.LarkDeckMixin._ld_panel_summary({"rounds": [], "tools": []}) is None



def test_plugin_ctx_handle_is_recorded_in_the_shared_box():
    """官方 ctx 句柄必须与探测快照同源共享：命令可能来自旧世代模块对象。"""
    saved = dict(adapter.PLUGIN_CTX)

    class _Ctx:
        def get_config(self, key, default=None):
            return default

        def set_config(self, key, value):
            return None

    try:
        adapter._remember_plugin_ctx(_Ctx())
        assert callable(adapter.PLUGIN_CTX.get("get_config")), adapter.PLUGIN_CTX
        # 审计 B1：聊天侧写入口整体移除，set_config 不再被采集（哪怕 ctx 提供了它）。
        assert "set_config" not in adapter.PLUGIN_CTX, adapter.PLUGIN_CTX
        # 盒子里是**同一份**容器（两代模块对象任一从盒子里取，都是同源）
        assert panel.shared_box()["adapter_plugin_ctx"] is adapter.PLUGIN_CTX
        adapter._remember_plugin_ctx(object())
        assert adapter.PLUGIN_CTX == {"get_config": None}, adapter.PLUGIN_CTX
    finally:
        adapter.PLUGIN_CTX.clear()
        adapter.PLUGIN_CTX.update(saved)


def test_panel_tools_block_truncates_and_keeps_the_most_recent() -> None:
    """R3 收窄版：**工具块**的截断与裁减方向。

    这两条**曾经零门禁**：R3 的两条变异（`R3-5` 工具行不再过 `truncate`、`R3-6` 裁减方向写反）
    实测**四门禁全绿** —— 而后果分别是「一条超长工具输出把面板撑爆」与
    「对用户说反话（丢掉最近的、留下最早的）」。期望值**全部写死**：**不同的**两步文本 +
    字面量 `已省略` 提示 + 字面量的首尾 id —— 拿被测函数自己算期望值就是自证循环。
    """
    # ① 截断：一条 2000 字的工具行必须被截到上限附近，且**带省略标记**（不能静默截断）
    long_row = "✅ bash · " + "甲" * 2000
    got = cards.panel_tools_markdown(tools=[long_row], max_tool_chars=40)
    assert "🛠️ 工具执行 · 1 步" in got, got[:80]
    assert len(got) < len(long_row), f"超长工具行必须被截断：{len(got)} vs {len(long_row)}"
    assert "✅ bash · 甲" in got, got[:80]
    assert got != long_row and "…" in got, f"截断必须带省略标记（静默截断会让用户以为就这么多）：{got[-20:]!r}"

    # ② 裁减方向：33 步 / 上限 30 ⇒ **保留最近的 30 步**、丢掉最早的 3 步，
    #    并且提示行在**最前面**、数字是**被丢掉的那 3 步**。
    steps = [f"✅ step{i:02d}" for i in range(33)]
    got = cards.panel_tools_markdown(tools=steps, max_steps=30)
    assert "step00" not in got and "step01" not in got and "step02" not in got, \
        f"裁减必须丢掉**最早**的几步：{got[:60]!r}"
    assert "step03" in got and "step32" in got, "最近的几步必须都在"
    lines = got.split("\n\n")
    assert "🛠️ 工具执行 · 33 步" in lines[0], f"分区标题必须最前：{lines[0]!r}"
    assert lines[1] not in steps and "3" in lines[1], \
        f"提示行必须在步骤前、且说的是被丢掉的那 3 步：{lines[1]!r}"
    assert len([l for l in lines if l.startswith("✅")]) == 30, \
        f"上限是元素行数（保留 30 步）：{[l for l in lines if l.startswith('✅')][:3]}"


def test_panel_and_context_state_survive_the_module_being_loaded_twice() -> None:
    """**进程内状态必须真的进程内** —— 真机根因（2026-09-14）。

    实测：同一个网关进程里 **插件发现跑了两遍**（日志里 `Plugin discovery complete` 出现两次、
    启动自检打印两遍）⇒ `core.panel` / `core.context` 各被加载成**两份模块对象**。状态绑在模块
    变量上就是两份，于是：**钩子写 A 份、卡片读 B 份** ⇒ 面板恒空（诊断实测 `buckets=0`，
    而同时 `pre_tool_call 钩子触达` 在打日志）；账本同理 ⇒ 卡片写「最近写卡：无记录 · 累计 0 帧」
    而 DM 里明明有卡。

    判据**不是**「`_STATE` 是个 dict」这种恒真话，而是**真的造出第二份模块对象**（把同一份源码
    `exec` 到另一个命名空间），然后：① 两份的容器是**同一个对象**；② 在第二份里记一个工具，
    **第一份**的 `snapshot` 就能看见它。撤掉共享（`_STATE` 改回模块局部 dict）⇒ ② 必红。
    """
    src = (_pathlib.Path(_REPO_PARENT) / "larkdeck" / "core" / "panel.py").read_text(
        encoding="utf-8")
    copy1: dict = {"__name__": "larkdeck_panel_copy_1"}
    copy2: dict = {"__name__": "larkdeck_panel_copy_2"}
    exec(compile(src, "panel_copy_1", "exec"), copy1)      # noqa: S102 —— 故意的：模拟第二份
    exec(compile(src, "panel_copy_2", "exec"), copy2)      # noqa: S102
    assert copy1["_STATE"] is copy2["_STATE"], \
        "两份模块对象必须共享同一个会话状态容器（否则钩子写一份、卡片读另一份 ⇒ 面板恒空）"
    assert copy1["_CHAT_SESSION"] is copy2["_CHAT_SESSION"]
    copy1["reset"]()
    try:
        copy2["record_tool_started"]("s-share", "t-share", "bash", {"cmd": "ls"}, "c-share")
        snap = copy1["snapshot"]("") or {}
        assert [t.get("name") for t in (snap.get("tools") or [])] == ["bash"], \
            f"在第二份模块对象里记录的工具，第一份必须看得见（真机面板为空的根因）：{snap}"
        # 同一 `tool_call_id` 再记一次 ⇒ **不许出现第二行**（插件被加载两次时钩子会回调两遍）
        copy2["record_tool_started"]("s-share", "t-share", "bash", {"cmd": "ls"}, "c-share")
        snap2 = copy1["snapshot"]("") or {}
        assert len(snap2.get("tools") or []) == 1, \
            f"同一个 tool_call_id 只该有一行（重复订阅不许在面板里变成两行）：{snap2.get('tools')}"
    finally:
        copy1["reset"]()
    # 账本（`context._STATUS`）与**页脚指标**（`context._LATEST` 等）同一条纪律：
    # 钩子写、卡片读；分成两份 ⇒ 账本永远「累计 0 帧」、**页脚永远不显示**
    # （用户实测：卡片上没有 `ctx 4.3k/20k · 22%` 那行，而接口层一切正常）。
    import builtins
    box = getattr(builtins, "_larkdeck_shared_state")
    assert context._STATUS is context._shared_status(), \
        "写卡账本必须挂在进程级共享容器上（否则「累计 0 帧」那个症状会回来）"
    # ⚠️⚠️ **上面那一句是恒真的**（审计 Y1b 实测）：它拿「模块绑定的对象」与「同模块函数
    #    返回的对象」比 —— 只要有人把 `_STATUS` 的初始化（`context.py:146`）**和**
    #    `_shared_status` 的返回（`:80`）**一起**改成本地对象，这句永远成立（实测两处一起改
    #    ⇒ 198/198 全绿）。**单点**改坏时它确实能抓（实测 197/198），所以它补的是另一半。
    #    判据必须跨出模块自己：账本**就是共享盒子里那一格**。
    assert context._STATUS is box["status"], \
        "账本必须是**共享盒子里那一格**，不是模块自己的一个 dict（两处一起搬走 = 静默裂脑）"
    # 更强的判据：照 panel 那条的手法，把 `context.py` **真的 exec 成两份模块对象**，
    # 再验证「第二份里记的码，第一份必须看得见」。⚠️ `context.py` 有 `from . import i18n`
    # ⇒ 命名空间**必须**给 `__package__`，否则 exec 直接炸（那就退化成崩溃、不是断言）。
    _csrc = (_pathlib.Path(_REPO_PARENT) / "larkdeck" / "core" / "context.py").read_text(
        encoding="utf-8")
    _c1: dict = {"__name__": "larkdeck_context_copy_1", "__package__": "larkdeck.core"}
    _c2: dict = {"__name__": "larkdeck_context_copy_2", "__package__": "larkdeck.core"}
    exec(compile(_csrc, "context_copy_1", "exec"), _c1)     # noqa: S102 —— 故意的：模拟第二份
    exec(compile(_csrc, "context_copy_2", "exec"), _c2)     # noqa: S102
    assert _c1["_STATUS"] is _c2["_STATUS"], \
        "两份模块对象必须共享同一个账本（钩子写 A、卡片读 B ⇒ 卡片恒报「累计 0 帧」）"
    assert _c1["_LOCK"] is _c2["_LOCK"], "账本的锁同样要跨世代同源（否则互斥从构造上失效）"
    _c1["reset"]()
    try:
        _c2["note_response_code"](300309)
        assert _c1["status_snapshot"]()["codes"].get("300309") == 1, \
            "在第二份模块对象里记的错误码，第一份必须看得见"
    finally:
        _c1["reset"]()
    for name in ("_LATEST", "_MAX_CACHE", "_RETRY_AFTER", "_ALIASES"):
        assert getattr(context, name) is box["ctx_" + name.lstrip("_").lower()], \
            f"{name} 必须与进程级容器是同一个对象（否则页脚/缓存会被分成两份）"
    assert context._MAX_OVERRIDE_BOX is box["ctx_max_override"], \
        "标量配置（context_max_override）也要用共享盒子，否则两份模块各记一个值"

    # ㉙ **R11-A0：容器共享了，锁也必须共享；键清单只有一处。**
    #    为什么锁是独立的缺陷：容器进程级共享、而 `_LOCK` 是模块级 ⇒ **两份模块对象各持一把锁**，
    #    互斥从构造上失效 —— 一份在 `_purge_locked` 里迭代 `_STATE.items()`、另一份同时插新桶，
    #    实测 `RuntimeError: dictionary changed size during iteration`（真机上是「偶发丢面板」，
    #    不可复现的那一类）。判据是**对象同一性**（互斥性由它推出），不靠复现竞态。
    assert copy1["_LOCK"] is copy2["_LOCK"], \
        "面板锁必须与它守的容器同源（两份模块对象各持一把锁 = 互斥失效）"
    assert copy1["_LOCK"] is box["panel_lock"]
    assert context._LOCK is box["context_lock"], \
        "指标层的锁也必须共享（`_LATEST`/`_STATUS` 的读改写跨世代才真正互斥）"
    assert context._INFLIGHT is box["ctx_inflight"], \
        "「正在探测」集合必须共享：各持一份时，另一份 discard 不掉 ⇒ 那个模型永远不再被探测"
    assert adapter.HOOKS is box["adapter_hooks"] and adapter.COMMAND is box["adapter_command"], \
        "注册结论（钩子挂载数 / 命令注册）必须共享，否则自检按世代给出两个不同的答案"
    assert set(box) == set(panel._SHARED_BOX_FACTORY), \
        (f"共享盒子的键清单必须只有一处真相（`panel._SHARED_BOX_FACTORY`）："
         f"多出 {sorted(set(box) - set(panel._SHARED_BOX_FACTORY))}、"
         f"缺少 {sorted(set(panel._SHARED_BOX_FACTORY) - set(box))}")
    # 压力冒烟：两个线程各拿一份模块对象反复写 + 淘汰。**这不是判别力来源**（两把锁时它
    # 也可能侥幸通过），只是「共享之后真的没炸」的旁证 —— 判别力在上面那组同一性断言上。
    import threading as _threading

    def _hammer(mod: dict) -> None:
        for i in range(120):
            mod["record_tool_started"]("s-hammer", "t-hammer", "bash",
                                       {"cmd": "ls"}, f"c-{i % 7}")
            mod["snapshot"]("")

    _threads = [_threading.Thread(target=_hammer, args=(m,)) for m in (copy1, copy2)]
    for _t in _threads:
        _t.start()
    for _t in _threads:
        _t.join()
    copy1["reset"]()


def test_generation_snapshot_is_machine_readable_and_never_fakes_it() -> None:
    """R11-A1：世代快照 —— 「哪一份模块对象是活的」必须是**读得出来的数字**。

    为什么值得一条门禁：真机上「插件被加载两遍」这件事，原先只能靠日志时序推断
    （`Plugin discovery complete` 出现两次 + 启动自检打印两遍），而那条线索既容易被别的插件
    淹没、又分不清「同一个 manager 里重复加载」与「两个 manager 按 home 分身」——两者修法不同。
    快照把四件事变成数字：**加载序号**（1 = 第一世代，≥2 = 世代更替真的发生了）、模块 id、
    manager id、home。

    两半判据：① 快照里必须有 `加载序号=` 且值 ≥1；② **不在 Hermes 环境里时必须如实说
    「读不到」，绝不编一个数字**（本项目的头号失败模式是「静默」，第二号就是「谎报」）。
    """
    line = adapter.generation_snapshot_line()
    found = re.search(r"加载序号=(\d+)", line)
    assert found and int(found.group(1)) >= 1, f"快照必须带可读的加载序号：{line}"
    assert f"模块={adapter.__name__}#" in line, f"快照必须带模块身份：{line}"
    # ⚠️ 光有「加载序号=N」不够：**写死成 1 也能过**（那就是个装饰）。所以再真加载一次
    # `panel.py`（同一份源码 exec 到另一个命名空间 = 第二世代），断言序号**真的**跟着涨。
    _src = (_pathlib.Path(_REPO_PARENT) / "larkdeck" / "core" / "panel.py").read_text(
        encoding="utf-8")
    _base = panel.latest_load_seq()
    _gen1: dict = {"__name__": "larkdeck_panel_gen1"}
    _gen2: dict = {"__name__": "larkdeck_panel_gen2"}
    exec(compile(_src, "panel_gen1", "exec"), _gen1)      # noqa: S102 —— 故意的
    exec(compile(_src, "panel_gen2", "exec"), _gen2)      # noqa: S102
    # ⚠️ 判据是「**每个模块对象自己的**序号」按加载顺序递增。第一版写的是「读盒子里的当前值」，
    # 结果两份模块对象报**同一个数**（盒子共享）⇒ 世代标记等于没有 —— 测试当场抓出来了。
    assert (_gen1["load_seq"](), _gen2["load_seq"]()) == (_base + 1, _base + 2), \
        (f"加载序号必须是「本模块对象自己的」那个号、且随加载顺序递增："
         f"{_gen1['load_seq']()} / {_gen2['load_seq']()}（基线 {_base}）")
    assert _gen1["latest_load_seq"]() == _base + 2, \
        "`latest_load_seq()` 要能说出「进程内最新那一份是第几代」（旧的那份靠它发现新世代）"
    if not compat.manager_snapshot():
        assert "读不到" in line, f"拿不到 manager 时要如实说「读不到」，不许编：{line}"
    else:
        assert "manager=" in line and "home=" in line, f"拿到就要报出来：{line}"
    # ⚠️ 「编一个数字」这条路**本地环境里走不到**（这个 venv 里 `hermes_cli` 可导入，
    # 快照总是拿得到）—— 所以必须**把「拿不到」的情形造出来**再断言，否则那条纪律毫无判别力
    # （变异 R13-2 实测：不造它就是 🟢）。判据：不许出现 `manager=<数字>`。
    _saved_snapshot = compat.manager_snapshot
    try:
        compat.manager_snapshot = lambda: {}          # type: ignore[assignment]
        _no_env = adapter.generation_snapshot_line()
    finally:
        compat.manager_snapshot = _saved_snapshot     # type: ignore[assignment]
    assert "读不到" in _no_env, f"拿不到管理器时必须如实说「读不到」：{_no_env}"
    assert not re.search(r"manager=\d", _no_env), \
        f"拿不到管理器时**不许编一个 id**（谎报比缺失更糟）：{_no_env}"


def test_merged_class_never_stacks_our_mixin_twice() -> None:
    """R11-A6：**自套娃**检测 —— 第二世代拿到的基类是**第一世代的合并类**。

    真机时序（日志实测）：两遍扫描各注册一次平台 ⇒ 第二世代的 `_discover_base_class()`
    问注册表要「上一个工厂造出来的类」时，拿回来的是第一世代的合并类。照旧写法再叠一层，
    MRO 就是 ``[新合并, 新混入, 旧合并, 旧混入, 官方…]`` —— 混入层里 `super().xxx()` 的
    回退路径会**执行两遍**，而我们的回退路径都是「真的去写一次卡 / 真的去交还控制权」
    ⇒ 同一帧写两次。

    ⚠️ 关键细节：**跨世代的 `LarkDeckMixin` 是两个不同的类对象**（模块被重新 exec 过），
    所以「剥旧层」不能只按对象同一性判 —— 下面第二格就是用**同名不同对象**的假旧层钉这条。
    """
    class _Official:
        def ping(self) -> str:
            return "official"

    first = adapter.merged_class(_Official)
    assert first.__mro__.count(adapter.LarkDeckMixin) == 1, "正常叠一层就够"
    assert _Official in first.__mro__

    try:
        with _LogCapture("larkdeck") as records:
            second = adapter.merged_class(first)          # ← 第二世代拿到的基类
    except TypeError as exc:  # R12-6 变异：不剥旧层会造出矛盾 MRO
        raise AssertionError(f"自套娃必须剥掉旧层，不能造出矛盾 MRO：{exc}") from exc
    assert second.__mro__.count(adapter.LarkDeckMixin) == 1, \
        "自套娃：混入层被叠了两层（`super()` 回退路径会执行两遍）"
    assert second.__bases__ == (adapter.LarkDeckMixin, _Official), \
        f"合并类必须直接继承**官方类**，而不是上一世代的合并类：{second.__bases__}"
    assert second.__mro__.index(_Official) == second.__mro__.index(adapter.LarkDeckMixin) + 1, \
        "官方类必须紧跟在我们这一层之后（中间多一层 = 那段代码会被执行两遍）"
    assert any("已经是本插件叠过的" in r.getMessage() for r in records), \
        "自套娃必须在日志里说出来（它是「插件被加载了两遍」最直接的信号）"

    # 跨世代：旧层是**同名但不同对象**的类（模拟上一世代那份模块）
    class LarkDeckMixin:                              # noqa: N801 - 故意同名，模拟旧世代
        pass

    _gen1_merged = type(adapter.ADAPTER_CLASS_NAME, (LarkDeckMixin, _Official), {})
    assert adapter._official_base_class(_gen1_merged) is _Official, \
        "按对象同一性判会漏掉旧世代的层（同名不同对象）—— 必须连名字一起判"


def test_unified_panel_applies_caps() -> None:
    panel = cards.unified_panel(reasoning="x" * 5000, tools=["a"])
    assert panel is not None
    joined = " ".join(e.get("content", "") for e in panel["elements"])
    assert "已省略" in joined and len(joined) < 5000
    assert "💭 思考" in joined and "🛠️ 工具执行 · 1 步" in joined
    trimmed = cards.unified_panel(tools=[f"step{i}" for i in range(40)])
    joined = " ".join(e.get("content", "") for e in trimmed["elements"])
    assert "更早的 10 步已折叠" in joined
    assert "step0" not in joined and "step39" in joined, "保留最近的步骤"
    # 每条工具结果也各自受限
    big = cards.unified_panel(tools=["y" * 5000])
    assert "已省略" in " ".join(e.get("content", "") for e in big["elements"])
    # 上限可调（配置进来就是这个口子）
    assert cards.unified_panel(reasoning="z" * 50, max_reasoning_chars=10) is not None
    assert cards.unified_panel() is None


def test_unified_panel_reserves_room_for_section_headings():
    """分区标题本身也占面板子元素额度 —— 不能把元素墙的缝重新打开。

    2026-09-17 审计 M5：``keep`` 原先没有为 ``💭 思考`` / ``🛠️ 工具执行`` 两个标题扣额度，
    极端配置下会让整卡元素数越过 ``fit_reply_card`` 的硬墙（面板被整块摘掉）。
    """
    rounds = [{"text": "x" * 120, "elapsed_ms": 1} for _ in range(187)]
    node = cards.unified_panel(rounds=rounds, tools=["t1", "t2"],
                               max_reasoning_chars=22320)
    assert node is not None
    rendered = "".join(str(e.get("content", "")) for e in node["elements"])
    expected = max(1, min(187, 22320 // cards._MIN_ROUND_CHARS,
                          max(1, cards._PANEL_CHILDREN_ROOM - 1 - 1 - 1)))
    assert rendered.count("**第") == expected, (
        f"渲染轮数应把 thinking/tools 两个分区标题的额度一起扣掉："
        f"expected {expected}, got {rendered.count('**第')}")
    assert "更早的 187" not in rendered and "轮已折叠" in rendered



def test_panel_caps_treat_zero_as_default_not_unlimited():
    """配置写 0 / 负数必须退回默认上限，**不是**取消上限。

    取消上限会把整段推理（panel 缓冲可达 262144 字符）塞进卡片，飞书直接拒收，
    那个回合的卡片功能整块丢掉。README 把这三个键描述成「上限」，行为得对得上。
    """
    for zeroish in (0, -1):
        long_panel = cards.unified_panel(reasoning="x" * 5000, max_reasoning_chars=zeroish)
        assert "已省略" in " ".join(e.get("content", "") for e in long_panel["elements"]), \
            f"max_reasoning_chars={zeroish} 不能变成「不截断」"

        many = cards.unified_panel(tools=[f"step{i}" for i in range(40)], max_steps=zeroish)
        joined = " ".join(e.get("content", "") for e in many["elements"])
        assert "更早的 10 步已折叠" in joined, f"max_steps={zeroish} 不能变成「全量保留」"

        big = cards.unified_panel(tools=["y" * 5000], max_tool_chars=zeroish)
        assert "已省略" in " ".join(e.get("content", "") for e in big["elements"]), \
            f"max_tool_chars={zeroish} 不能变成「不截断」"


def test_panel_title_english_plural():
    """英文单复数：1 tool call / N tool calls（单次工具调用很常见）。"""
    one = cards.unified_panel(tools=["read_file"])
    assert one["header"]["title"]["i18n_content"]["en_us"] == "Thinking & tools · 1 tool call"
    many = cards.unified_panel(tools=["read_file", "bash"])
    assert many["header"]["title"]["i18n_content"]["en_us"] == "Thinking & tools · 2 tool calls"
    assert one["header"]["title"]["i18n_content"]["zh_cn"] == "思考与工具 · 1 次工具调用"


# --------------------------------------------------------------------------- #
# 指标采集层：钩子喂数据 → 快照 → 别名
# --------------------------------------------------------------------------- #
def test_context_store_snapshot_and_aliases() -> None:
    context.reset()
    context.set_context_override(None)
    context.set_aliases({"claude-sonnet-4-6": "Sonnet 4.6"}, spec="c=d, e=f")
    assert context.known_aliases() == {"claude-sonnet-4-6": "Sonnet 4.6", "c": "d", "e": "f"}
    assert context.display_model("claude-sonnet-4-6") == "Sonnet 4.6"
    # 没有别名时做保守瘦身：去 vendor 前缀 / 去 :free 之类后缀
    assert context.display_model("openrouter/anthropic/claude-x:free") == "claude-x"
    assert context.display_model("") == ""

    context.record_api_call(
        model="claude-sonnet-4-6", provider="anthropic", platform="feishu",
        session_id="s1", api_call_count=3,
        usage={"input_tokens": 45200, "output_tokens": 900},
    )
    context.set_context_override(200000)
    snap = context.snapshot()
    assert snap["input_tokens"] == 45200
    assert snap["output_tokens"] == 900
    assert snap["context_max"] == 200000
    assert snap["model_display"] == "Sonnet 4.6"
    assert abs(snap["context_pct"] - 22.6) < 0.05

    # 钩子里出现空 payload 时，不得把上一帧的好数据冲掉
    context.record_api_call(usage=None, model="")
    assert context.snapshot()["input_tokens"] == 45200

    # 真实钩子的 usage 是 Hermes CanonicalUsage 的 asdict：prompt_tokens 把缓存命中
    # 也算进来了，必须优先用它 —— 否则开提示缓存的会话会把上下文占用严重低报。
    context.record_api_call(
        model="m", usage={"input_tokens": 800, "prompt_tokens": 45000,
                          "output_tokens": 120, "cache_read_tokens": 44000},
    )
    snap2 = context.snapshot()
    assert snap2["input_tokens"] == 45000, "必须用 prompt_tokens 而不是裸 input_tokens"
    assert snap2["prompt_tokens"] == 45000
    assert snap2["cache_read_tokens"] == 44000

    # 脏 usage 也不该炸（字符串数字会被宽容转换）
    context.record_api_call(usage={"input_tokens": "123", "output_tokens": None})
    assert context.snapshot()["input_tokens"] == 123

    context.set_context_override(None)
    context.reset()
    context.set_aliases({}, spec="")
    assert context.snapshot()["input_tokens"] is None


def test_markdown_hygiene_applies_to_finalize_only() -> None:
    """卫生**只允许出现在收尾帧**：流式帧的文本必须原样送出去（前缀链纪律）。

    判据：中间帧写进元素的内容 == 原始 text；收尾那一帧的整卡 patch 载荷里
    出现**降级后**的文本（`# 标题` ⇒ `**标题**`）。两条一起才说明「该改的改了、不该改的没动」。
    """
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0
    try:
        raw = _make()
        updates = _wire_patch(raw)
        adapter.configure(native_transport="patch")
        assert _run(raw.send_stream_frame("", chat_id="oc_md", turn_id="t-md"))
        assert _run(raw.send_stream_frame("# 标题\n正文：**A、B", chat_id="oc_md", turn_id="t-md"))
        _mid = json.loads(updates[-1]["content"])
        _mid_text = [e.get("content") for e in _mid["body"]["elements"]
                     if e.get("tag") == "markdown"]
        assert any("# 标题" in (t or "") for t in _mid_text), \
            f"流式帧的文本必须原样（前缀链纪律）：{_mid_text}"
        assert _run(raw.send_stream_frame("# 标题\n正文：**A、B", finalize=True,
                                          chat_id="oc_md", turn_id="t-md"))
        _fin = json.loads(updates[-1]["content"])
        _fin_joined = json.dumps(_fin, ensure_ascii=False)
        assert "**标题**" in _fin_joined, f"收尾帧必须做卫生（降级标题）：{_fin_joined[:200]}"
        assert "# 标题" not in _fin_joined, f"收尾帧不该留 H1：{_fin_joined[:200]}"
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_sanitize_markdown_is_idempotent_and_never_touches_code() -> None:
    """R6a 的 markdown 卫生：**纯函数 + 幂等**，而且**代码区逐字不动**、**只删不补**。

    为什么这几条是硬要求（原型实测踩过两次）：
      * **不能「补一个 `**`」** —— 补出来的收尾落在整段最后，会把它后面的全部内容吞进加粗
        （`# 标题\n正文：**A、B\n## 建议` ⇒ `**建议****`）；所以只允许**删**那个游离标记；
      * **顺序**必须先删再降级 —— 降级会给标题行凭空加一对 `**`，把奇偶性搅乱；
      * **代码区不是 markdown** —— 围栏/行内代码里的 `**` 与 `#` 改了就是数据损坏。
    """
    cases = [
        "# 标题\n正文", "## 小标题\n\n正文", "### 三级\n正文", "#### 四级不动\n正文",
        "正文 **加粗", "正文 **加粗** 正常", "无标记的正文",
        "**未闭合\n\n# 标题", "", "   ",
        "```\ncode with ** inside\n```", "正文\n```\ncode ** here\n```",
        "```\n# not a heading\n**x\n```", "# 标题\n```\n# code\n```",
        "行内 `**not bold` 与正文 **bold**", "行内 `a**b` 单独一个",
        "# 磁盘报告\n占用前三：**A、B\n## 建议", "**跨行\n加粗**合法", "**a** 与 **b**",
    ]
    for case in cases:
        once = cards.sanitize_markdown(case)
        assert cards.sanitize_markdown(once) == once, f"不幂等：{case!r} ⇒ {once!r}"
        # 代码区逐字不变（最硬的一条：宁可格式少一点，也不能改坏代码块内容）
        assert ([case[a:b] for a, b in cards._code_spans(case)]
                == [once[a:b] for a, b in cards._code_spans(once)]), \
            f"代码区被改了：{case!r} ⇒ {once!r}"
    # 具体形状（逐条钉住，别只断言性质）
    assert cards.sanitize_markdown("# 磁盘报告") == "**磁盘报告**"
    assert cards.sanitize_markdown("正文 **加粗") == "正文 加粗"
    assert cards.sanitize_markdown("**跨行\n加粗**合法") == "**跨行\n加粗**合法"   # 成对的不许动
    assert cards.sanitize_markdown("```\n# code\n**x\n```") == "```\n# code\n**x\n```"
    assert cards.sanitize_markdown("#### 四级不动") == "#### 四级不动"
    # ⚠️ 这一条是**顺序**的判据（先删游离 `**`、再降级标题）：反过来的话，降级会给标题行凭空
    #    加一对 `**`，把奇偶性搅成偶数 ⇒ 那个游离标记**不会被删**，结果就长成下面「不许」的样子。
    assert cards.sanitize_markdown("# 磁盘报告\n占用前三：**A、B\n## 建议") == \
        "**磁盘报告**\n占用前三：A、B\n**建议**", "顺序反了（必须先删游离 **、再降级标题）"
    assert cards.sanitize_markdown(None) is None and cards.sanitize_markdown("") == ""


# --------------------------------------------------------------------------- #
# R6a 审计收口（2026-09-14，第十X路审计：高-1 / 中-1..中-4 / 低-1..低-6）
#
# 这一批断言的共同点：**期望值一律写在测试里**（字面量或测试自己写的独立正则），
# 绝不从被测实现反推。高-1 就是对「两边都用同一个 `cards._code_spans`」的报应 ——
# 那个函数一旦认不出行内代码，两边算出 `[] == []`，断言**恒真**（审计自造变异 MY-5
# 四门禁全绿，而它真的把行内代码内容删掉了）。
# --------------------------------------------------------------------------- #

#: 测试自己的**独立**代码区提取器（只认「闭合的三反引号对 + 行内单反引号」）。
#: 刻意**不复用** `cards._code_spans`：它就是要被检验的那个实现，拿它算期望值等于自证循环
#: （`docs/lessons.md` 推论 2/6/8）。它也**故意**不认未闭合围栏 / 四反引号 / `~~~` ——
#: 正好用来证明「被测实现比这条独立正则更保守」：产出里凡是它认出来的代码区，
#: 都必须逐字不变。
_INDEPENDENT_CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)


def _independent_code_parts(text: str):
    """独立提取器给出的代码区文本（以及它们的起点，供定位用）。"""
    return [(m.start(), m.group(0)) for m in _INDEPENDENT_CODE_RE.finditer(text)]


def _markdown_of(card):
    """从卡片 JSON（或 patch 载荷字符串）里取出所有 markdown 文本。

    ⚠️ 断言**不要**直接拿 `json.dumps` 出来的串做子串判断：卡里正文的换行在 JSON 里是
    两字符的 `\\n`，`"# 标题\n正文" in blob` 会**假红**（实测踩过），而 `"**标题**"` 这类
    不含换行的子串又会因为别的元素凑巧包含它而假绿。解析成结构再比才是判据。
    """
    node = json.loads(card) if isinstance(card, str) else card
    found = []
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if item.get("tag") == "markdown" and isinstance(item.get("content"), str):
                found.append(item["content"])
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


def test_sanitize_never_rewrites_what_an_independent_extractor_calls_code() -> None:
    """**代码区一个字节都不许动** —— 判据来自测试自己的正则，不来自 `cards._code_spans`。

    为什么必须换判据（R6a 审计高-1）：原来那条断言两边都用 `cards._code_spans` 算 span，
    所以「`_code_spans` 认不出行内代码」时两边都是 `[]` ⇒ `[] == []` **恒真**。
    审计自造的变异 `MY-5`（把行内代码扫描整段删掉）因此**四门禁全绿**，
    而它真的把 `` `a**b` `` 改成了 `` `ab` ``（行内代码内容被删字）。

    这条断言的判别力来自两处：
      ① 期望值是字面量（`_CODE_REGION_CASES` 的第三个字段），错一个字就红；
      ② 独立正则算出的每个代码区片段必须**逐字出现**在产出里 —— 覆盖那些「产出被改写」
         的用例（未闭合围栏、`~~~`、降级标题），它们没法用整串相等来断言。
    """
    # 两个「被测实现必须比独立正则更保守」的样本（④ 用；`~~~` 那条独立正则完全不认）
    fenced_src = "~~~\n# not a heading\ncode ** here\n~~~"
    unclosed_src = "先看这段：\n```python\n# 磁盘检查\ndf -h ** 2>/dev/null\n"
    for raw, expected in _CODE_REGION_CASES:
        out = cards.sanitize_markdown(raw)
        assert out == expected, f"产出与钉住的期望不符：{raw!r} ⇒ {out!r}（期望 {expected!r}）"
        # ② 独立正则认出来的每一段代码内容，必须原样出现在产出里
        for at, part in _independent_code_parts(raw):
            assert part in out, (
                f"第 {at} 位起的代码区内容在产出里不见了/被改了：{part!r}\n"
                f"  in : {raw!r}\n  out: {out!r}")
    # ③ 反向对照：独立正则**认得出**行内代码（否则 ② 就是空集恒真 —— 又一个 MY-5 形态）
    assert len(_independent_code_parts("行内 `a**b` 单独一个")) == 1
    assert len(_independent_code_parts("# 标题\n```\n# c\n```")) == 1

    # ④ **被测实现必须比独立正则更保守**（逐条 `⊇`）：独立正则找出的每个区间，都要被
    #    `_code_spans` 的某个区间**包含**。这一条防的是「`_code_spans` 退化成恒 `[]`」
    #    ——那时 ② 会变成**空集恒真**（没有任何片段可查），而 ④ 的 `observed` 立刻变空 ⇒ 红。
    #    判据里不含任何字面量，所以它跟 ① 的字面量表是**两种**判别力（变异 `R6a-20` 钉它）。
    observed = 0
    for source in [_CODE_REGION_CASES[i][0] for i in range(len(_CODE_REGION_CASES))] + [
            unclosed_src, fenced_src]:
        expected_spans = _independent_code_parts(source)
        observed += len(expected_spans)
        got = cards._code_spans(source)
        for at, part in expected_spans:
            assert any(start <= at and at + len(part) <= end for start, end in got), (
                f"被测实现比独立正则**更不保守**：第 {at} 位起的 {part!r} 没被任何 span 包含\n"
                f"  source: {source!r}\n  spans: {got}")
    assert observed >= 10, f"独立正则一共只找到 {observed} 个代码区间 —— 这条断言快变成空集了"
    # 反过来也要有：被测实现必须**多认出**那三种（独立正则故意不认）
    assert cards._code_spans(unclosed_src), "未闭合围栏这段的 span 不该是空的"
    assert len(cards._code_spans(fenced_src)) == 1


#: `(输入, 钉住的产出)`。**两个字段都是测试里手写的字面量**（关键：不是算出来的）。
#: 覆盖：围栏里的 `#`/`**`、行内代码里的 `**`、未闭合围栏、四反引号嵌套、`~~~` 围栏，
#: 以及各条真实转换（降级标题 / 删游离 `**` / 不做任何事）。
_CODE_REGION_CASES = [
    # —— 代码区内容必须逐字不动（下面这五条 `out == raw`，即「一个字节都不许动」）——
    ("```\n# not a heading\n**x\n```", "```\n# not a heading\n**x\n```"),
    ("正文\n```\ncode ** here\n```", "正文\n```\ncode ** here\n```"),
    ("```python\ndf -h ** 2>/dev/null\n# 注释\n```\n正文一段。",
     "```python\ndf -h ** 2>/dev/null\n# 注释\n```\n正文一段。"),
    ("行内 `a**b` 单独一个", "行内 `a**b` 单独一个"),
    ("行内 `**not bold` 与正文 **bold**", "行内 `**not bold` 与正文 **bold**"),
    ("示例 `x ** y`，正文 **重点**", "示例 `x ** y`，正文 **重点**"),
    ("说明：`git status` 的结果在图里。", "说明：`git status` 的结果在图里。"),
    # ② 四反引号嵌套围栏（CommonMark 合法：块里可以有 ```）
    ("````\n```\n# comment about code\nx = a ** b\n```\n````",
     "````\n```\n# comment about code\nx = a ** b\n```\n````"),
    # ③ `~~~` 围栏
    ("~~~\n# not a heading\ncode ** here\n~~~", "~~~\n# not a heading\ncode ** here\n~~~"),
    # ① 未闭合围栏延伸到文末（核心的「边界收尾」会把这种文本直接送出来）
    ("先看这段：\n```python\n# 磁盘检查\ndf -h ** 2>/dev/null\n",
     "先看这段：\n```python\n# 磁盘检查\ndf -h ** 2>/dev/null\n"),
    # —— 围栏之外的规则照常生效（代码区被排除，正文照改）——
    ("# 标题\n```\n# code\n```", "**标题**\n```\n# code\n```"),
    ("~~~\n# comment\n~~~\n正文 **A", "~~~\n# comment\n~~~\n正文 A"),
    ("#### 四级不动", "#### 四级不动"),
    # 单行内代码 + **下一行**的标题：行内代码正则若允许跨行，`# B` 会被吞进「代码区」⇒ 不降级
    # （变异 `R6a-8` 钉这条；上面那些用例都是「成对反引号在同一行」，抓不到跨行那种改法）
    ("甲 `a` 乙\n# B", "甲 `a` 乙\n**B**"),
    # 标题体首尾的**全角空格**与零宽空格是正文内容，不许被 `str.strip()` 吞掉
    # （变异 `R6a-14` 钉这条）
    ("\u3000\n# \u3000全角\n\n# 零宽 \u200b", "\u3000\n**\u3000全角**\n\n**零宽 \u200b**"),
    # CommonMark 的「闭合法标题」尾随 `#` 本来**不显示**，降级时不许把它露成加粗里的可见字符
    # （变异 `R6a-13` 钉这条；`# 标题 ####` 在 CommonMark 里只显示「标题」）
    ("# 标题 ####", "**标题**"),
    ("## 小标题 ##", "**小标题**"),
    ("# 标题 #", "**标题**"),
    ("# 磁盘报告\n占用前三：**A、B\n## 建议",
     "**磁盘报告**\n占用前三：A、B\n**建议**"),
    ("**跨行\n加粗**合法", "**跨行\n加粗**合法"),
]

#: **三条候选**的形状（R6a 审计中-1）：游离的开头 `**` + 后面一对合法加粗。
#: 判据是「删**第一个**」——删最后一个会把合法对的闭合标记拆掉，中间整段被吞进加粗。
_THREE_CANDIDATE_MARKERS = "**磁盘占用前三：A、B\n重点关注 **/var** 这个目录"


def test_unpaired_bold_drops_the_first_candidate_not_the_last() -> None:
    """删「最后一个」游离 `**` 会把**后文合法的加粗对**拆掉（审计中-1：比不改更糟）。

    判别力：变异 `R6a-9`（把删除点改成从末尾往前找）必须让这条红。
    现有用例抓不住它，是因为它们在删除之前**只有一个候选** —— 删哪个都一样。
    """
    out = cards.sanitize_markdown(_THREE_CANDIDATE_MARKERS)
    assert out == "磁盘占用前三：A、B\n重点关注 **/var** 这个目录", \
        f"删错标记了（后文那对合法加粗必须留着）：{out!r}"
    # 对照：删**最后一个**会长成这样（把合法对的闭合标记拆掉 ⇒ 中间整段被吞进加粗）
    wrong = "**磁盘占用前三：A、B\n重点关注 **/var 这个目录"
    assert out != wrong, "这正是「删最后一个」的病态结果，绝不许出现"
    # 另外两条同族形状（一条游离在段中、一条游离在前且后文有两对合法加粗）
    assert cards.sanitize_markdown("正文 **开头未闭合\n后面 **强调** 结尾") == \
        "正文 开头未闭合\n后面 **强调** 结尾"
    assert cards.sanitize_markdown("**a\nb **c** d **e** f") == "a\nb **c** d **e** f"


def test_code_spans_sees_unclosed_and_non_backtick_fences() -> None:
    """围栏识别必须包含**未闭合**、**四反引号及以上**、**`~~~`**（审计中-2）。

    判别力：变异 `R6a-6`（未闭合围栏不延伸到文末）、`R6a-7`（只认三反引号）、
    `R6a-8`（`~~~` 不算围栏）各钉一条。三条都是「代码内容被改坏」的入口，
    而原来的 `_FENCE_RE = "```.*?```"` 一个都不认。
    """
    unclosed = "先看这段：\n```python\n# 磁盘检查\ndf -h ** 2>/dev/null\n"
    assert cards._code_spans(unclosed) == [(6, len(unclosed))], \
        f"未闭合围栏必须延伸到文末：{cards._code_spans(unclosed)}"
    assert cards.sanitize_markdown(unclosed) == unclosed, "未闭合围栏里的代码内容被改了"

    nested = "````\n```\n# comment about code\nx = a ** b\n```\n````"
    assert cards._code_spans(nested) == [(0, len(nested))], \
        f"四反引号围栏要整块算代码区（里层的 ``` 不该被当成闭合）：{cards._code_spans(nested)}"

    tilde = "~~~\n# not a heading\ncode ** here\n~~~"
    assert cards._code_spans(tilde) == [(0, len(tilde))], \
        f"`~~~` 围栏与反引号围栏同义：{cards._code_spans(tilde)}"
    assert cards.sanitize_markdown(tilde) == tilde


def test_sanitize_markdown_is_idempotent_on_every_realistic_shape() -> None:
    """幂等：**真实形状上必须成立**，已知边界**显式列出来**（审计中-4）。

    原来 `AGENTS.md` 把幂等写成不变量，而它是**假的**：`'## 用 ``` 开围栏\\n## 建议\\n
    ```\\ncode\\n```\\n'` 第二遍会把第一遍插进去的 `**` 删掉（现状修法：标题体里有反引号
    就不做加粗降级）。修完之后实测（审计自己的两条语料脚本，`sys.path` 指向本仓库）：

      * 块拼接穷举 2379 条 **0 条**非幂等（修之前 4 条）；
      * 现实语料 6 万篇 **0 条**（含「标题体里带 ``` 」的两种片段：修之前 19.29%）；
      * 随机 20 万条畸形输入 **1 条** ⇒ 就是 `_KNOWN_NONIDEMPOTENT_SHAPES` 那一族。

    这条断言钉的是**前两类**（穷举 + 现实语料按固定种子重建），边界那一族单独列出来
    ——「已知边界」必须有测试跟着，否则它就是下一句空话。
    """
    alphabet = ["**", "*", "`", "```", "#", "# ", " ", "\n", "a", "*a*", "```\n", "\n```",
                "\r\n"]
    checked = 0
    for size in (1, 2, 3):
        for combo in itertools.product(alphabet, repeat=size):
            case = "".join(combo)
            once = cards.sanitize_markdown(case)
            assert cards.sanitize_markdown(once) == once, f"不幂等：{case!r} ⇒ {once!r}"
            checked += 1
    assert checked == 2379, f"穷举规模变了（{checked}）—— 这条断言的强度也跟着变了"

    # 现实语料（审计 probe_nonidem.py 的 15 + 2 种片段，固定种子重建）
    parts = [
        "# 磁盘报告\n", "## 建议\n", "### 细节\n", "#### 四级标题\n",
        "正文一段，含 **加粗** 与 *斜体*。\n", "**这里漏了一个标记\n",
        "还有 **/var** 这个目录。\n", "```\ncode line\n```\n",
        "```python\nx = 1\nprint(x)\n```\n", "行内 `code` 与 `**粗**`。\n",
        "| 列 | 值 |\n|---|---|\n| a | b |\n", "> 引用一行\n", "\n", "- 列表项\n",
        "说明：`git status` 的用法\n",
        "## 用 ``` 开一个代码块\n", "# ```\n",
    ]
    rng = random.Random(11)
    bad = 0
    for _ in range(20000):
        doc = "".join(rng.choice(parts) for _ in range(rng.randint(2, 8)))
        once = cards.sanitize_markdown(doc)
        if cards.sanitize_markdown(once) != once:
            bad += 1
    assert bad == 0, f"现实语料里出现 {bad} 篇非幂等（修之前这类语料是 19.29%）"

    # ⚠️ 这条清单**本来是有内容的**：写这版修复的中间态里，随机 20 万条畸形输入还剩
    # 3 条非幂等（形状都是「一个 `**` 夹在两对行内代码之间」，如 `'aA \u200b``**``~'`），
    # 我一度准备把它当成「已知边界」写进文档。最后一次对齐时发现那 3 条的**真因是围栏识别
    # 的一个错**（开标记行的判定把 `` ```python `` 读成 `ython`）—— 修掉它之后
    # **20 万条随机输入 0 条非幂等**、2379 条穷举 0 条、现实语料 6 万篇 0 条。
    # 所以这里不留「已知边界」清单：**没有边界可留**，全域成立。（这条注释本身就是
    # 「别把没查清的东西写成边界」的提醒 —— 边界清单会让人停止追查。）
    assert cards.sanitize_markdown("aA \u200b``**``~") == "aA \u200b````~", \
        "这一族（两对空行内代码之间夹一个 `**`）现在也是幂等的，且用户看到的字没变"


def test_sanitize_reverts_when_it_would_push_the_card_over_the_limit() -> None:
    """卫生**之后**的文本要过一道**同口径**的字节闸门（审计低-1/低-2）。

    为什么必须有：`sanitize_markdown` 对标题密集的正文**只会变长**（每个降级标题 +2 字节），
    于是存在「卫生前过闸、卫生后超限」的窄带（审计构造过 `128000 → 128080`）。
    卫生前那道闸门（CardKit 元素帧）量的是**卫生前**的文本，收尾帧原本一道都没有 ⇒
    超限的收尾卡被拒 ⇒ fail-open ⇒ 用户从「一张卡」掉成「若干条纯文本」。
    口径病见 `docs/lessons.md` 推论 13：闸门必须量**要发出去的那一份**。
    """
    saved = cards.FEISHU_CARD_BYTE_LIMIT
    try:
        raw = "# 标题一\n# 标题二\n正文"
        cards.FEISHU_CARD_BYTE_LIMIT = adapter._card_body_bytes(raw)   # 原文刚好放行
        assert adapter._sanitize_for_send(raw) == raw, \
            "卫生后会超限 ⇒ 必须退回原文（少一点格式，也不能让卡发不出去）"
        # 门槛抬高一点：卫生后的那份也放行 ⇒ 必须做卫生（否则这条闸门成了「永远不做卫生」）
        cards.FEISHU_CARD_BYTE_LIMIT = len(raw) + 500
        assert adapter._sanitize_for_send(raw) == cards.sanitize_markdown(raw) \
            != raw, "放得下的时候必须照常做卫生"
    finally:
        cards.FEISHU_CARD_BYTE_LIMIT = saved


def test_code_spans_is_linear_not_quadratic_on_fence_heavy_input() -> None:
    """`_code_spans` 必须是**线性**的：围栏/行内代码数量翻几倍，耗时不许翻平方（审计低-5）。

    旧实现是 O(围栏数 × 行内代码数)：行内代码那一步对**每个**行内代码都遍历一遍全部围栏
    区间。实测（本机，取 5 次最小值；形状 = `N` 条行级围栏 × 每条围栏里 40 个行内代码）：

        N     规模      旧实现       新实现
        50    8250     18.2ms       0.36ms
        400   66000    **1173ms**   3.30ms        ← 规模 8 倍，旧实现耗时 64 倍
        （审计在它的机器上量到 56KB 病态输入 5.29s）

    这段耗时落在**收尾帧**上（会拖慢最后一帧的发送），所以要一条门禁。写法照
    `test_args_preview_is_bounded_for_nested_and_long_inputs`：**多次取最小值**
    （最小值最不受机器负载影响），判**两条**判据：

      ① 绝对上界 0.5s —— 直接拦「回到秒级」；
      ② **比值**（大输入与小输入各取最小值）—— 这一条与机器快慢无关，专拦「复杂度回去了」：
         新实现 9.2×，旧实现 64×（实测），所以阈值取 20×（新实现留一倍余量、旧实现必红）。

    ⚠️ 形状很关键：`'```a```' * k` 那种**行内**围栏在两边都是线性的（新实现的 `_fence_scan`
    只认行首围栏、整段只算一个围栏），拿它当判据**抓不到**这条变异（实测：加了这个变异
    仍然全绿）。必须是「行级围栏 × 每栏内部的行内代码」才算得到那个乘积。

    判别力：变异 `R6a-17`（把行内代码的排除改回「对每个围栏做一次线性查找」）必须让这条红。
    """
    def _best(text: str, rounds: int = 5) -> float:
        best = float("inf")
        for _ in range(rounds):
            started = time.perf_counter()
            cards._code_spans(text)
            best = min(best, time.perf_counter() - started)
        return best

    def _fence_heavy(fences: int, per_fence: int = 40) -> str:
        # 每条围栏占 3 行：``` / （40 个行内代码 + 空格）/ 换行
        return ("```\n" + "`a` " * per_fence + "\n") * fences

    small, big = _fence_heavy(50), _fence_heavy(400)
    assert (len(small), len(big)) == (8250, 66000), (len(small), len(big))
    small_s, big_s = _best(small), _best(big)
    assert big_s < 0.5, f"_code_spans 在 66KB 病态输入上用了 {big_s:.3f}s（绝对上界 0.5s）"
    ratio = big_s / max(small_s, 1e-6)
    assert ratio < 20.0, (
        f"输入大 8 倍、耗时大了 {ratio:.1f} 倍 —— 复杂度回到 O(围栏数 × 行内代码数) 了？"
        f"（小 {small_s * 1000:.2f}ms / 大 {big_s * 1000:.2f}ms）")


def test_markdown_hygiene_covers_every_whole_text_writer_not_just_native_finalize() -> None:
    """卫生覆盖**每一条写完整文本**的路径，而中间帧一个字节都不许动（审计中-3）。

    审计实测的不一致：同一段模型输出，**正常收尾**写成 `**磁盘报告**…`，而 `/stop` 重绘
    写原文 `# 磁盘报告…`。根本原因是 `sanitize_markdown` 全仓库只有一个调用点。

    判据（写在代码注释里的那条）：**卫生只能作用于「完整文本」的写入**
      * 可以做：`send()`、`edit_message(finalize=True)`、`/stop` 重绘、native 收尾帧；
      * 不许做：`edit_message(finalize=False)` 与 CardKit 的**元素帧**（它们是累积帧的中间态）。

    所以这条用例**两半都要断**：该做的四处做了（正断言），不该动的两处没动（反向断言）。
    判别力：变异 `R6a-10`（edit 两条路径一起做卫生）、`R6a-11`（send 不做）、
    `R6a-12`（/stop 重绘不做）、`R6a-1`（流式帧也做，前缀链断掉）各钉一条。
    """
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0
    raw = "# 磁盘报告\n占用前三：**A、B"
    clean = cards.sanitize_markdown(raw)
    assert clean != raw               # 前提：这段文本真的会被卫生改动（否则断言恒真）
    try:
        # ① send()：非 native 首帧也要卫生
        raw_a = _make()
        sent = []
        raw_a._ld_send_card = lambda chat_id, card, **kw: (
            sent.append(card) or _StubResult(True, "om_send"))
        assert _run(raw_a.send("oc_md_send", raw)) is not None
        texts = _markdown_of(sent[-1])
        assert clean in texts, f"send() 走的是完整消息，必须做卫生：{texts}"
        assert raw not in texts, "send() 不该再露着原文的 H1 与字面 `**`"

        # ② edit_message(finalize=True) 做 / (finalize=False) **不做**（前缀链纪律）
        raw_b = _make()
        updates = _wire_patch(raw_b)
        raw_b._ld_track("om_edit", "oc_md_edit")
        assert _run(raw_b.edit_message("oc_md_edit", "om_edit", raw, finalize=False))
        mid_texts = _markdown_of(updates[-1]["content"])
        assert mid_texts == [raw], f"中间帧必须**原样**（前缀链）：{mid_texts}"
        assert _run(raw_b.edit_message("oc_md_edit", "om_edit", raw, finalize=True))
        fin_texts = _markdown_of(updates[-1]["content"])
        assert fin_texts == [clean], f"收尾整卡必须做卫生：{fin_texts}"

        # ③ /stop 重绘：正文来自我们自己的追踪表，必须与收尾帧一致
        panel.reset()
        raw_c = _make()
        ck_updates = _wire_patch(raw_c)
        assert _run(raw_c.send_stream_frame("", chat_id="oc_md_stop", turn_id="t-md"))
        assert _run(raw_c.send_stream_frame(raw, chat_id="oc_md_stop", turn_id="t-md"))
        assert _markdown_of(ck_updates[-1]["content"]) == [raw], "流式中间帧必须原样（同上）"
        _run(raw_c.interrupt_session_activity("sk-md", "oc_md_stop"))
        stopped_texts = _markdown_of(ck_updates[-1]["content"])
        # ⚠️ 不能断言「列表里只有正文」：中止态面板自己也是一段 markdown（`⛔ 已中止`）。
        assert clean in stopped_texts, \
            f"/stop 重绘写的是同一段原文，必须卫生后与收尾帧一致：{stopped_texts}"
        assert raw not in stopped_texts, "中止重绘不该露出原文的 H1 与字面 `**`"
        panel.reset()

        # ④ CardKit：元素帧原样、收尾整卡卫生
        raw_d = _make()
        elem_writes, patch_cards = [], []

        class _Resp:
            """SDK 响应的最小面。⚠️ `data` 必须**逐次构造**：建实体读 `data.card_id`、
            发实体卡读 `data.message_id`，写死一个实例会让某一步静默拿到错的 id。"""

            def __init__(self, **data):
                self.code = 0
                self.msg = "success"
                self.data = types.SimpleNamespace(**data) if data else None

            def success(self):
                return True

        class _CardRes:
            def batch_update(self, request):
                return _Resp()

            def create(self, request):
                return _Resp(card_id="ck_md")

        class _ElemRes:
            def content(self, request):
                elem_writes.append(types.SimpleNamespace(
                    element_id=request.element_id,
                    request_body=types.SimpleNamespace(content=request.request_body.content)))
                return _Resp()

        class _MsgRes:
            def create(self, request):
                # 发实体卡走的是 `im.v1.message.create`（替身漏了它 ⇒ 建实体那一步 AttributeError
                # 被帧路径吞掉，表现成「这一帧失败」，与要验的卫生毫无关系）。
                # ⚠️ 返回值必须是**裸响应**（`{"code":0,"data":{"message_id":…}}`）：
                # 帧路径用 `_finalize_send_result` 读它，而那个函数按**字典**取 message_id
                # （内置适配器的原语就是这么写的，别用 `_Resp` 替身 —— 会 AttributeError）。
                return {"code": 0, "data": {"message_id": "om_ck_md"}}

            def patch(self, request):
                # ⚠️ 请求对象有两种形状：测试用 `_wire_patch` 换成的是**字典**（便于断言），
                # 而这一节为了 cardkit 换了整个 `_client`，于是又落回生产的
                # `_ld_build_patch_request`（真 SDK 的 `PatchMessageRequest`，只能按属性取）。
                # 两种都要接住 —— 只接字典会在收尾那一步 `TypeError`，被帧路径吞成
                # 「这一帧失败」，与要验的卫生毫无关系（实测踩过一次）。
                if isinstance(request, dict):
                    mid, content = request["message_id"], request["content"]
                else:
                    mid = request.message_id
                    content = request.request_body.content
                patch_cards.append(json.loads(content))
                return {"code": 0, "data": {"message_id": mid}}

        raw_d._client.im = types.SimpleNamespace(v1=types.SimpleNamespace(message=_MsgRes()))
        raw_d._client.cardkit = types.SimpleNamespace(
            v1=types.SimpleNamespace(card=_CardRes(), card_element=_ElemRes()))
        original_requests = type(raw_d)._ld_ck_requests
        try:
            type(raw_d)._ld_ck_requests = staticmethod(lambda: types.SimpleNamespace(
                create_card=lambda card_json: types.SimpleNamespace(
                    request_body={"card_json": card_json}),
                write_element=lambda cid, eid, content, seq, uuid_value: types.SimpleNamespace(
                    card_id=cid, element_id=eid,
                    request_body=types.SimpleNamespace(content=content, sequence=seq,
                                                       uuid=uuid_value, )),
                batch_update=lambda cid, actions, seq, uuid_value: types.SimpleNamespace(
                    card_id=cid,
                    request_body=types.SimpleNamespace(
                        actions=json.dumps(actions, ensure_ascii=False), sequence=seq,
                        uuid=uuid_value)),
                settings_card=lambda cid, payload, seq, uuid_value: types.SimpleNamespace(
                    card_id=cid, request_body=types.SimpleNamespace(settings=payload,
                                                                    sequence=seq)),
                send_entity=lambda receive_id, card_id: types.SimpleNamespace(
                    receive_id=receive_id, card_id=card_id,
                    request_body=types.SimpleNamespace(
                        content=json.dumps({"type": "card",
                                            "data": {"card_id": card_id}}),
                        msg_type="interactive", uuid=f"ld-msg-{card_id}")),
                reply_entity=lambda reply_to, card_id: types.SimpleNamespace(
                    reply_to=reply_to, card_id=card_id,
                    request_body=types.SimpleNamespace(
                        content=json.dumps({"type": "card",
                                            "data": {"card_id": card_id}}),
                        msg_type="interactive", uuid=f"ld-msg-{card_id}",
                        reply_in_thread=False)),
            ))
            adapter.configure(native_transport="cardkit")
            assert _run(raw_d.send_stream_frame("", chat_id="oc_md_ck", turn_id="t-ck"))
            assert _run(raw_d.send_stream_frame(raw, chat_id="oc_md_ck", turn_id="t-ck"))
            bodies = [w.request_body.content for w in elem_writes
                      if w.element_id == cards.CARDKIT_ANSWER_ID]
            assert bodies and bodies[-1] == raw, \
                f"CardKit 元素帧写的是累积中间态，必须原样：{bodies}"
            assert _run(raw_d.send_stream_frame(raw, finalize=True, chat_id="oc_md_ck",
                                                turn_id="t-ck"))
            assert patch_cards, "收尾必须走一次整卡 patch"
            tail_texts = _markdown_of(patch_cards[-1])
            assert tail_texts == [clean], f"CardKit 的收尾整卡必须做卫生：{tail_texts}"
        finally:
            type(raw_d)._ld_ck_requests = original_requests
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_footer_metrics_are_opt_in_and_never_fake_zero() -> None:
    """R7 页脚扩展：三段新指标**默认不出现**，开了才出现，而且**缺数据就少一段**。

    这条断言的判别力在两个方向上：
      * 关着（默认）⇒ 一个新段都不许有（否则等于偷偷改了所有人的观感）；
      * 开着但数据缺 ⇒ 那一段**不许**以 0 的形式出现（`cache=0.0` 是「真的 0% 命中」、
        `None` 是「不知道」—— 编 0 就是在骗人，见 `docs/lessons.md` 的口径病）。
    """
    defaults = dict(adapter._DEFAULTS)
    try:
        adapter.configure(footer=True, context_style="text", footer_metrics="off")
        context.reset()
        context.record_api_call(model="m", api_call_count=7,
                                usage={"input_tokens": 1000, "output_tokens": 5,
                                       "prompt_tokens": 4000, "cache_read_tokens": 3000})
        context.set_context_override(20000)
        _off = adapter.LarkDeckMixin._ld_footer()
        assert "ctx" in (_off or ""), f"上下文用量照旧必须在：{_off!r}"
        for symbol in ("⚡", "🔁", "🐢"):
            assert symbol not in (_off or ""), f"默认必须一个字都不加：{_off!r}"

        adapter.configure(footer_metrics="basic")
        _basic = adapter.LarkDeckMixin._ld_footer() or ""
        assert "⚡ 75%" in _basic, f"缓存命中率 = 3000/4000：{_basic!r}"
        assert "🔁 7" in _basic, f"API 次数：{_basic!r}"
        assert "🐢" not in _basic, f"basic 不含 TTFB：{_basic!r}"

        adapter.configure(footer_metrics="full")
        context.record_api_call(model="m", api_call_count=8, api_duration=1.5,
                                started_at=1000.0, first_chunk_at=1000.42,
                                usage={"input_tokens": 1000, "output_tokens": 5,
                                       "prompt_tokens": 4000, "cache_read_tokens": 3000})
        _full = adapter.LarkDeckMixin._ld_footer() or ""
        assert "🐢 0.4s" in _full, f"TTFB = 0.42s ⇒ 0.4s：{_full!r}"

        # **真的 0% 命中**必须显示出来（`cache_read=0` 是「一次都没命中」，与「不知道」是
        # 两件事）。判别力：变异 `R7-12`（把 0.0 当成缺失）⇒ 红。
        context.reset()
        context.record_api_call(model="m", api_call_count=3,
                                usage={"input_tokens": 4000, "output_tokens": 5,
                                       "prompt_tokens": 4000, "cache_read_tokens": 0})
        context.set_context_override(20000)
        _zero = adapter.LarkDeckMixin._ld_footer() or ""
        assert "⚡ 0%" in _zero, f"真的 0% 命中必须显示（不许当成「缺数据」）：{_zero!r}"

        # 反向：`api_call_count=0` / `ttfb=0.0` **不显示** —— 这是**有意的不对称**
        # （L-2 的结论）：缓存命中率 0% 是真实读数，而「本回合 0 次 API 调用」和
        # 「0.0 秒首字节」在渲染卡片的回合里不可能发生，出现 0 只会是脏数据。
        context.reset()
        context.record_api_call(model="m", api_call_count=0, api_duration=1.0,
                               started_at=1000.0, first_chunk_at=1000.0,
                               usage={"input_tokens": 1000, "output_tokens": 5,
                                      "prompt_tokens": 4000, "cache_read_tokens": 3000})
        context.set_context_override(20000)
        _zeros = adapter.LarkDeckMixin._ld_footer() or ""
        assert "🔁" not in _zeros and "🐢" not in _zeros, \
            f"0 次调用 / 0.0s 首字节视为「无意义」，不许画成读数：{_zeros!r}"
        assert "⚡ 75%" in _zeros, f"同一帧里真实读数照常显示：{_zeros!r}"

        # 缺数据：一段都不许编出来（尤其不许出现「⚡ 0%」这种假读数）
        context.reset()
        context.record_api_call(model="m", usage={"input_tokens": 1000, "output_tokens": 5})
        context.set_context_override(20000)
        _bare = adapter.LarkDeckMixin._ld_footer() or ""
        assert "⚡" not in _bare and "🔁" not in _bare and "🐢" not in _bare, \
            f"缺数据必须少一段，绝不编 0：{_bare!r}"

        # 认不出的取值按 off（不猜、不放大）。⚠️ 这一格必须**带着完整数据**测：数据缺的时候
        # 「按 off」与「按 full」渲染出来的东西**一模一样**（都是没有新段）⇒ 那种写法没有判别力
        # （变异实测全绿）。
        context.reset()
        context.record_api_call(model="m", api_call_count=7, api_duration=1.5,
                                started_at=1000.0, first_chunk_at=1000.42,
                                usage={"input_tokens": 1000, "output_tokens": 5,
                                       "prompt_tokens": 4000, "cache_read_tokens": 3000})
        context.set_context_override(20000)
        adapter.configure(footer_metrics="???")
        _unknown = adapter.LarkDeckMixin._ld_footer() or ""
        assert "⚡" not in _unknown and "🔁" not in _unknown and "🐢" not in _unknown, \
            f"认不出的取值必须按 off（不猜、不放大）：{_unknown!r}"
    finally:
        context.set_context_override(None)
        context.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_adapter_footer_wiring() -> None:
    """页脚顺序钉死：``状态 → 耗时 → 模型 → ctx``；模型不再出现在面板标题（用户 2026-09-17 指定）。"""
    defaults = dict(adapter._DEFAULTS)
    try:
        context.reset()
        panel.reset()
        adapter.configure(footer=True, show_model=True, context_style="text",
                          model_aliases="test-model=Test Model")
        # 钩子还没触发 → 没有任何一段可显示 → 不渲染脚注
        assert adapter.LarkDeckMixin._ld_footer(chat_id="oc_fw") is None

        panel.bind_chat_session("oc_fw", "s-fw")
        context.record_api_call(model="test-model",
                                usage={"input_tokens": 1000, "output_tokens": 5})
        # ⚠️ 用配置设置上限（而不是直接戳 `context.set_context_override`）：`configure()`
        # 会调用 `_apply_metrics_config()`，现在它无条件把配置值推给 context（审计 A1），
        # 直接戳的临时值会被正确覆盖掉。
        adapter.configure(context_max_override=10000)
        assert adapter.LarkDeckMixin._ld_footer(chat_id="oc_fw") == \
            "🤖 Test Model · ctx 1k/10k · 10%"

        # 结局一到，状态必须出现在**最前面**，且排在耗时/模型之前（AP 的 `已完成 · 1m 4s · ✳ model`）
        panel.record_turn_end("s-fw", "t-fw", completed=True)
        line = adapter.LarkDeckMixin._ld_footer(
            chat_id="oc_fw", started=time.monotonic() - 12.3)
        assert line is not None
        assert re.search(r"✅ 已完成 · ⏱ 12\.[0-9]s · 🤖 Test Model · ctx ", line), line
        assert line.index("✅") < line.index("⏱") < line.index("🤖") < line.index("ctx")
        # 显式传入的 status 也要能覆盖面板快照（`/stop` 重绘没有等待快照那一帧）
        assert adapter.LarkDeckMixin._ld_footer(
            chat_id="oc_fw", status="stopped").startswith("⛔ 已中止 · ")
        # started=0/False 不是合法回合起点（monotonic 不会为 0）：不许算出机器 uptime 级假耗时
        assert "⏱" not in (adapter.LarkDeckMixin._ld_footer(
            chat_id="oc_fw", started=0) or ""), "started=0 被当成了合法起点"
        # ❌ 执行出错 三态里必须有独立覆盖（Phase 1 审计 HIGH-1）
        assert adapter.LarkDeckMixin._ld_footer(
            chat_id="oc_fw", status="error").startswith("❌ 执行出错 · ")
        assert adapter._ld_status_text(panel.STATUS_ERROR) == "❌ 执行出错"

        adapter.configure(context_style="bar")
        assert "[█░░░░░░░]" in adapter.LarkDeckMixin._ld_footer(chat_id="oc_fw")
        adapter.configure(show_model=False)
        assert "🤖" not in adapter.LarkDeckMixin._ld_footer(chat_id="oc_fw")
        adapter.configure(footer=False)
        assert adapter.LarkDeckMixin._ld_footer(chat_id="oc_fw") is None
    finally:
        panel.reset()
        context.set_context_override(None)
        context.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_adapter_panel_wiring() -> None:
    """面板确实由「配置 + panel 快照」拼出来，且开关立刻生效。"""
    defaults = dict(adapter._DEFAULTS)
    try:
        panel.reset()
        adapter.configure(unified_panel=True, theme="neutral")
        assert adapter.LarkDeckMixin._ld_panel() is None, "没有数据不该渲染面板"

        panel.record_reasoning("s1", "t1", "先想一下。")
        panel.record_tool_started("s1", "t1", "read_file", {"path": "/tmp/a.txt"}, "c1")
        panel.record_tool_finished("s1", "t1", tool_name="read_file", status="ok",
                                   duration_ms=42, tool_call_id="c1")
        node = adapter.LarkDeckMixin._ld_panel()
        assert node is not None and node["tag"] == "collapsible_panel"
        title = node["header"]["title"]["content"]
        # 2026-09-17 CLS 观感：思考（💭 + 耗时）/ 工具（🛠️ + 步数）两个不同的 emoji。
        assert "💭 思考" in title and "🛠️ 工具执行 · 1 步" in title, title
        assert node["border"]["color"] == "grey", "还没有结局 → 中性灰边"
        texts = " ".join(e.get("content", "") for e in node["elements"])
        assert "先想一下。" in texts and "read_file" in texts and "✅" in texts

        # 用户 2026-09-17 指定：模型名与耗时**不再进面板标题**，改到页脚。
        adapter.configure(show_model=True, model_aliases="test-model=Test Model")
        context.record_api_call(model="test-model", usage={"input_tokens": 1})
        with_time = adapter.LarkDeckMixin._ld_panel("")
        header = with_time["header"]["title"]["content"]
        assert "🤖" not in header and "⏱" not in header, header
        assert "💭 思考" in header and "🛠️ 工具执行 · 1 步" in header, header
        footer = adapter.LarkDeckMixin._ld_footer(started=time.monotonic() - 12.3) or ""
        assert re.search(r"⏱ 12\.[0-9]s", footer), footer
        assert "🤖 Test Model" in footer, footer
        adapter.configure(show_model=False)
        assert "🤖" not in adapter.LarkDeckMixin._ld_panel()["header"]["title"]["content"]
        assert "🤖" not in (adapter.LarkDeckMixin._ld_footer(started=time.monotonic() - 12.3) or "")

        # 只有推理（没有工具）也给面板：标题只带思考段，不出现空的工具段
        panel.reset()
        panel.record_reasoning("s1", "t1", "只有推理。")
        only = adapter.LarkDeckMixin._ld_panel()
        assert only is not None
        only_title = only["header"]["title"]["content"]
        assert "💭 思考" in only_title and "🛠️" not in only_title, only_title

        adapter.configure(unified_panel=False)
        assert adapter.LarkDeckMixin._ld_panel() is None, "开关关掉必须立刻生效"
    finally:
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_ctx_settings_bridge() -> None:
    """官方插件配置（ctx.get_config）是 YAML 配置进 _CONFIG 的唯一通道。"""
    defaults = dict(adapter._DEFAULTS)

    class _Ctx:
        def __init__(self, values: Dict[str, Any]) -> None:
            self.values = values

        def get_config(self, key: str, default: Any = None) -> Any:
            return self.values.get(key, default)

    try:
        adapter._apply_ctx_settings(_Ctx({"footer": False, "context_style": "bar",
                                          "no_such_key": 1}))
        assert adapter._cfg("footer") is False
        assert adapter._cfg_raw("context_style") == "bar"
        # 老版本 / 测试替身没有 get_config：静默跳过，不抛不炸
        adapter._apply_ctx_settings(object())
        adapter._apply_ctx_settings(_Ctx({}))
        assert adapter._cfg("footer") is False, "空 settings 不应回写默认值"
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


# --------------------------------------------------------------------------- #
# 5. 面板数据层
# --------------------------------------------------------------------------- #
def test_panel_reasoning_accumulates_and_snapshots():
    panel.reset()
    panel.record_reasoning("s1", "t1", "第一段")
    panel.record_reasoning("s1", "t1", "，第二段。")
    snap = panel.snapshot()
    assert snap is not None
    assert snap["session_id"] == "s1" and snap["turn_id"] == "t1"
    assert snap["reasoning"] == "第一段，第二段。", snap
    assert snap["tools"] == [], snap
    assert snap["age"] >= 0.0


def test_panel_reasoning_tolerates_non_str_delta():
    # 坏载荷（数字 / None）不能留下「片段已加、长度未加」的半更新状态。
    panel.reset()
    panel.record_reasoning("s1", "t1", 123)
    panel.record_reasoning("s1", "t1", None)
    panel.record_reasoning("s1", "t1", "尾巴")
    snap = panel.snapshot()
    assert snap is not None and snap["reasoning"] == "123尾巴", snap


def test_panel_turn_change_resets_process_data():
    panel.reset()
    panel.record_reasoning("s1", "t1", "旧回合的思考")
    panel.record_tool_started("s1", "t1", "read_file", {"path": "x"}, "c1")
    panel.record_tool_finished("s1", "t1", "read_file", status="ok",
                               duration_ms=5, tool_call_id="c1")
    panel.record_reasoning("s1", "t2", "新回合的思考")
    snap = panel.snapshot()
    assert snap["turn_id"] == "t2"
    assert snap["reasoning"] == "新回合的思考", snap
    assert snap["tools"] == [], "换回合后不能残留上一回合的工具"


def test_panel_turn_id_absent_keeps_accumulating():
    # 老版本 Hermes 载荷没有 turn_id：保守地继续累积，不误清。
    panel.reset()
    panel.record_reasoning("s1", "", "甲")
    panel.record_reasoning("s1", "", "乙")
    assert panel.snapshot()["reasoning"] == "甲乙"


def test_panel_begin_turn_clears_before_new_events():
    # 新回合首帧可能早于本回合第一个面板事件：on_stream_start 一到就先清；
    # 同回合的重复通知（重试 / 工具轮的多次 API 调用）必须幂等、不误清。
    panel.reset()
    panel.record_reasoning("s1", "t1", "旧回合")
    panel.record_tool_started("s1", "t1", "bash", {"cmd": "ls"}, "c1")
    panel.begin_turn("s1", "t2")
    assert panel.snapshot() is None, "新回合还没数据，不该看到旧面板"
    panel.record_reasoning("s1", "t2", "新回合")
    panel.begin_turn("s1", "t2")  # 同回合再来一次：不清
    assert panel.snapshot()["reasoning"] == "新回合"
    # 拿不准的载荷（空 session / 空 turn）不动状态
    panel.begin_turn("", "t9")
    panel.begin_turn("s1", "")
    assert panel.snapshot()["reasoning"] == "新回合"


def test_panel_tool_pairing_by_call_id():
    panel.reset()
    panel.record_tool_started("s1", "t1", "read_file", {"path": "cards.py"}, "call_1")
    snap = panel.snapshot()
    assert snap["tools"][0]["status"] == "running"
    assert "cards.py" in snap["tools"][0]["preview"]
    panel.record_tool_finished("s1", "t1", "read_file", status="ok",
                               duration_ms=1234, tool_call_id="call_1")
    tool = panel.snapshot()["tools"][0]
    assert tool["status"] == "ok" and tool["duration_ms"] == 1234, tool
    assert tool["name"] == "read_file"


def test_panel_tool_finish_without_start_appends_fallback():
    # pre 丢包 / 顺序颠倒：post 兜底补一条，不让调用凭空消失。
    panel.reset()
    panel.record_tool_finished("s2", "t1", "web_search", status="error",
                               duration_ms=9, tool_call_id="call_9")
    tools = panel.snapshot()["tools"]
    assert len(tools) == 1 and tools[0]["status"] == "error"
    assert tools[0]["name"] == "web_search" and tools[0]["duration_ms"] == 9


def test_panel_duration_falls_back_to_clock():
    # 钩子没给 duration_ms 时按墙钟差兜底（数据层自己的 t0 差值）。
    panel.reset()
    panel.record_tool_started("s1", "t1", "grep", {}, "c2")
    panel.record_tool_finished("s1", "t1", "grep", status="ok", tool_call_id="c2")
    ms = panel.snapshot()["tools"][0]["duration_ms"]
    assert isinstance(ms, int) and ms >= 0, ms


def test_panel_unknown_status_passthrough():
    # status 原样保留（ok / error / blocked 与未来新值都不丢），渲染层再映射。
    panel.reset()
    panel.record_tool_finished("s1", "t1", "terminal", status="blocked", tool_call_id="c3")
    assert panel.snapshot()["tools"][0]["status"] == "blocked"


def test_panel_snapshot_empty_returns_none():
    panel.reset()
    assert panel.snapshot() is None
    # 有会话但没有任何内容：同样返回 None（调用方据此不渲染空面板）。
    panel.record_tool_started("s1", "t1", "noop", {}, "c0")
    panel._STATE["s1"]["tools"] = []
    assert panel.snapshot() is None


def test_panel_ttl_expires_sessions():
    panel.reset()
    ttl_before = panel._TTL_SECONDS
    try:
        panel._TTL_SECONDS = 0.01
        panel.record_reasoning("s1", "t1", "会过期的思考")
        time.sleep(0.03)
        assert panel.snapshot() is None, "过期会话不应再被看到"
        assert "s1" not in panel._STATE
    finally:
        panel._TTL_SECONDS = ttl_before
        panel.reset()


def test_panel_session_cap_evicts_oldest():
    panel.reset()
    cap_before = panel._MAX_SESSIONS
    try:
        panel._MAX_SESSIONS = 2
        for sid in ("s1", "s2", "s3"):
            panel.record_reasoning(sid, "t1", f"来自{sid}")
        assert "s1" not in panel._STATE, "超出容量应淘汰最旧会话"
        assert set(panel._STATE) == {"s2", "s3"}
        assert panel.snapshot()["session_id"] == "s3"
    finally:
        panel._MAX_SESSIONS = cap_before
        panel.reset()


def test_panel_reasoning_buffer_compacts_and_caps():
    """总量超上限时**从最早的轮开始回收**（保留最近的过程信息）。"""
    panel.reset()
    limit_before = panel._MAX_REASONING_CHARS
    try:
        # 大量小片段：总内容不丢
        for _ in range(200):
            panel.record_reasoning("s1", "t1", "x")
        assert panel.snapshot()["reasoning"] == "x" * 200

        # 超上限：单轮仍超长时截断它自己（保留最早部分）
        panel.reset()
        panel._MAX_REASONING_CHARS = 10
        panel.record_reasoning("s1", "t1", "0123456789ABCDEF")
        assert panel.snapshot()["reasoning"] == "0123456789"

        # 超上限：多轮时把**最早的轮**整轮丢掉，最近那轮留着
        panel.reset()
        panel._MAX_REASONING_CHARS = 10
        panel.record_reasoning("s1", "t1", "AAAAAAAAAA")      # 第 1 轮，10 字
        panel.record_answer_delta("s1", "t1")                  # 正文开始 → 切轮
        panel.record_reasoning("s1", "t1", "BBBBBBBBBB")      # 第 2 轮，10 字 → 总量 20 > 上限
        snap = panel.snapshot()
        assert "BBBBBBBBBB" in snap["reasoning"], "最近一轮被丢掉了"
        assert "AAAAAAAAAA" not in snap["reasoning"], "最早的轮没有被回收"
    finally:
        panel._MAX_REASONING_CHARS = limit_before
        panel.reset()


def test_panel_renders_reasoning_rounds_with_durations():
    """面板按轮渲染：每轮一个标题行 + 耗时；轮额度是总上限的均分。"""
    rounds = [{"text": "第一段推理", "elapsed_ms": 6200},
              {"text": "第二段推理", "elapsed_ms": 1500}]
    node = cards.unified_panel(rounds=rounds, tools=["✅ bash · 0.1s"])
    assert node is not None
    texts = [e.get("content", "") for e in node["elements"]]
    joined = " ".join(texts)
    assert "第 1 轮" in joined and "6.2s" in joined, joined
    assert "第 2 轮" in joined and "1.5s" in joined, joined
    assert "第一段推理" in joined and "第二段推理" in joined
    # 英文侧同步
    assert "Round 1" in i18n.t("panel.round_n", loc := i18n.EN, n=1)

    # 没给 rounds 时退回把 reasoning 当一整段（向后兼容）；标题仍走「💭 思考」分区
    flat = cards.unified_panel(reasoning="一整段推理")
    flat_text = " ".join(e.get("content", "") for e in flat["elements"])
    assert "一整段推理" in flat_text
    assert "💭 思考" in flat_text and "🛠️" not in flat_text
    assert "第 1 轮" not in flat_text

    # 每轮额度是总量的均分，而且**渲染轮数收在预算 // _MIN_ROUND_CHARS 以内**。
    # 旧断言是 `len(...) < 1000` —— 近乎恒真：实测「均分」290 字符、「一轮吃满上限」
    # 450 字符，两种实现都过。这里改成按预算算出来的确切边界。
    capped = cards.unified_panel(rounds=[{"text": "x" * 500}, {"text": "y" * 500}],
                                 max_reasoning_chars=200)
    capped_text = "".join(e.get("content", "") for e in capped["elements"])
    assert "y" * 200 in capped_text, "最近那轮没吃满预算（0.5 倍预算都没用上）"
    assert "x" * 200 not in capped_text, f"两轮吃进了同一份预算：{capped_text!r}"
    assert "轮已折叠" in capped_text, f"被折叠掉的轮必须说出来：{capped_text!r}"

    # 预算够时两轮都在，各拿一半
    roomy = cards.unified_panel(rounds=[{"text": "x" * 500}, {"text": "y" * 500}],
                                max_reasoning_chars=1200)
    roomy_text = "".join(e.get("content", "") for e in roomy["elements"])
    assert "轮已折叠" not in roomy_text, "预算充足时不该折叠任何轮"
    assert "x" * 500 in roomy_text and "y" * 500 in roomy_text, roomy_text


def test_panel_round_bytes_never_inflate_with_round_count():
    """**轮数不能把面板总量撑开** —— 否则面板会顶穿卡片字节预算、整块消失。

    旧写法 ``share = max(120, 预算 // N)``：N > 预算/120 时渲染总量恒等于 ``120·N``，
    与 ``max_reasoning_chars`` 无关（默认预算 1200 → 第 11 轮起线性膨胀）。
    这里的场景是修复前真会踩到的：51 轮 + 9000 字正文，面板把总字节顶过
    ``CARD_BYTE_BUDGET`` → ``fit_reply_card`` 降载到 ``no-panel``，**推理面板凭空消失**，
    日志里只有一行 INFO（正是 docs/lessons.md 里最怕的静默降级）。
    """
    rounds = [{"text": "推理" * 1000, "elapsed_ms": 1000} for _ in range(51)]
    node = cards.unified_panel(rounds=rounds, max_reasoning_chars=1200)
    rendered = "".join(str(e.get("content", "")) for e in node["elements"])
    assert len(rendered) <= 1200 * 2, \
        f"轮数把面板总量撑开了：{len(rendered)} 字符（预算 1200，旧实现是 7845）"
    assert "轮已折叠" in rendered, "折叠掉多少轮必须写出来，不能静默丢内容"
    assert rendered.count("**第") <= 1200 // cards._MIN_ROUND_CHARS, \
        f"渲染轮数没有收在预算以内：{rendered.count('**第')} 轮"

    tip = cards.tool_step("bash", status="ok", duration_ms=100, preview="ls")
    card, tier = cards.fit_reply_card("答" * 9000, streaming=True, panel=node, footer="🤖 m · ⏱ 1s")
    assert tier == "ok", f"面板把卡片顶穿字节预算（降载到 {tier}）—— 用户看到的是面板凭空消失"
    assert "collapsible_panel" in _all_tags(card), "降载把面板整块摘掉了"


def test_panel_empty_round_drop_must_not_leave_phantom_length():
    """空轮被丢弃时**必须同时扣掉它的长度**，否则幻影长度会提前回收真实轮。

    ``record_reasoning`` 是逐片段给 ``reasoning_len`` 加账的，纯空白轮（``"\\n"``、``"  "``）
    也一样加过；而 ``_compact_locked`` 唯一的判据就是这个账本。不扣 → 有内容的真实轮
    被**比配置更早地整轮回收**（面板过程信息凭空少一段，不报错、不告警）。
    """
    limit_before = panel._MAX_REASONING_CHARS
    panel.reset()
    try:
        # 尺寸刻意选得让「空白轮还在进行中」的那一刻**不**触发回收
        # （那一刻的账本等于各轮内容之和，是自洽的；真正错的是**丢弃后不扣账**）
        panel._MAX_REASONING_CHARS = 100
        panel.record_reasoning("s1", "t1", "A" * 50)   # 第 1 轮：50 字，有内容
        panel.record_answer_delta("s1", "t1")          # 正文 → 切轮
        panel.record_reasoning("s1", "t1", " " * 20)   # 第 2 轮：纯空白（会被丢掉）
        panel.record_answer_delta("s1", "t1")          # 切轮 → 空轮摘除
        panel.record_reasoning("s1", "t1", "B" * 40)   # 第 3 轮：40 字
        snap = panel.snapshot()
        assert "A" * 50 in snap["reasoning"], (
            "空轮留下了幻影长度（reasoning_len 虚高 20），把有内容的真实轮提前回收了")
        assert len(snap["rounds"]) == 2, snap["rounds"]
    finally:
        panel._MAX_REASONING_CHARS = limit_before
        panel.reset()


def test_panel_round_cut_ignores_unattributable_delta():
    """正文增量**归属不明时不许切轮** —— 否则会造出一个跨越两回合的假轮。

    旧 guard ``if tid and state["turn_id"] not in ("", tid)`` 在 ``state["turn_id"]`` 为空
    （该会话此前的事件都没带 turn_id，即老版本 Hermes 路径）时对**带 tid 的**载荷放行，
    于是拿新回合的正文去 finalize 上一回合残留的轮，耗时按上一回合的 started 起算 ——
    实测能渲染出「第 1 轮 · 600.0s」这种离谱数字。
    """
    panel.reset()
    try:
        panel.record_reasoning("s1", "", "没有 turn_id 的推理")
        panel.record_answer_delta("s1", "t2")  # 载荷带 tid，本会话此前是空 tid → 无法归属
        # 观测点：轮有没有被结束。被结束了下一条推理会**另开一轮**（2 轮），
        # 没被结束则接着写进同一轮（1 轮）。
        panel.record_reasoning("s1", "t2", "再补一句")
        snap = panel.snapshot()
        assert len(snap["rounds"]) == 1, \
            f"归属不明的正文把上一回合残留的轮切掉了（假轮）：{snap['rounds']!r}"
        assert snap["rounds"][0]["text"] == "没有 turn_id 的推理再补一句"

        # 同一个回合的正文照常切轮（别把正常路径也堵死）
        panel.reset()
        panel.record_reasoning("s1", "t1", "第一段")
        panel.record_answer_delta("s1", "t1")
        panel.record_reasoning("s1", "t1", "第二段")
        assert len(panel.snapshot()["rounds"]) == 2, "正常路径的切轮被堵死了"

        # 老版本 Hermes 路径（两边都没 turn_id）也必须照常切轮
        panel.reset()
        panel.record_reasoning("s1", "", "第一段")
        panel.record_answer_delta("s1", "")
        panel.record_reasoning("s1", "", "第二段")
        assert len(panel.snapshot()["rounds"]) == 2, "无 turn_id 的老路径被堵死了"
    finally:
        panel.reset()


def test_panel_card_renders_rounds_and_honours_panel_expanded():
    """卡片必须**真的**把 ``rounds`` 与 ``panel_expanded`` 用起来。

    变异测试的结论（2026-09-12）：把 adapter 里 ``rounds=snap.get("rounds")`` 删掉、
    或把 ``expanded=_cfg("panel_expanded")`` 写成死值 ``False``，四个门禁**全部照绿**。
    也就是说「整回合推理连成一个巨大的第 1 轮」「展开配置成摆设」这两种静默退化
    没有任何哨兵。这里补端到端断言：真走 ``send()``、真看卡片 JSON。
    """
    defaults = dict(adapter._DEFAULTS)
    panel.reset()
    try:
        panel.bind_chat_session("oc_1", "s1")
        panel.record_reasoning("s1", "t1", "第一段推理")
        panel.record_answer_delta("s1", "t1")
        panel.record_reasoning("s1", "t1", "第二段推理")

        raw = _make()
        _run(raw.send("oc_1", "你好"))
        card = json.loads(raw.calls[0][2])
        blob = json.dumps(card, ensure_ascii=False)
        assert "第 1 轮" in blob and "第 2 轮" in blob, \
            f"卡片没按轮渲染（rounds 没传到卡片层）：{blob}"
        collapsed = _find_collapsible(card)
        assert collapsed is not None, "面板元素不见了"
        assert collapsed["expanded"] is False, "默认应当收起（panel_expanded 默认 False）"

        adapter.configure(panel_expanded=True)
        raw2 = _make()
        _run(raw2.send("oc_1", "你好"))
        assert _find_collapsible(json.loads(raw2.calls[0][2]))["expanded"] is True, \
            "panel_expanded=True 没生效 —— 配置成了摆设"
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()
        panel.reset()


def test_panel_splits_reasoning_into_rounds():
    """轮次定义：一轮 = 一段连续推理，被**正文或工具**打断即结束。

    这是 aiduPOP 的定义（它的面板显示「第 N 波 · X.Xs」）。注意它**不是** API
    调用次数、也**不是**工具轮次 —— 用错标签会误导用户，所以按打断切段自算。
    """
    panel.reset()
    panel.record_reasoning("s1", "t1", "第一段推理")
    panel.record_reasoning("s1", "t1", "接着写")
    snap1 = panel.snapshot()
    assert len(snap1["rounds"]) == 1, "同一段推理不该被切成多轮"
    assert snap1["rounds"][0]["text"] == "第一段推理接着写"

    # 正文开始 → 结束第 1 轮
    panel.record_answer_delta("s1", "t1")
    panel.record_reasoning("s1", "t1", "第二段推理")
    snap2 = panel.snapshot()
    assert len(snap2["rounds"]) == 2, "正文开始应切断推理轮"
    assert snap2["rounds"][0]["text"] == "第一段推理接着写"
    assert snap2["rounds"][1]["text"] == "第二段推理"
    assert snap2["rounds"][0]["elapsed_ms"] >= 0
    # 进行中的那一轮也给出「到现在为止」的耗时
    assert snap2["rounds"][1]["elapsed_ms"] >= 0

    # 工具调用也切断推理轮
    panel.record_tool_started("s1", "t1", "bash", {}, "c1")
    panel.record_reasoning("s1", "t1", "第三段推理")
    snap3 = panel.snapshot()
    assert len(snap3["rounds"]) == 3, "工具调用应切断推理轮"
    # 向后兼容：reasoning 仍是所有轮的拼接
    assert snap3["reasoning"] == "第一段推理接着写第二段推理第三段推理"

    # 空白轮不进面板（否则会多出一个没有内容的「第 N 轮」标题）
    panel.reset()
    panel.record_reasoning("s1", "t1", "   ")
    panel.record_answer_delta("s1", "t1")
    assert panel.snapshot() is None
    panel.reset()



def test_status_colors_match_panel_constants():
    """状态字面量必须两边一致：``cards.STATUS_BORDERS`` 的键 = ``panel.STATUS_*``。

    这两个模块刻意互不 import（cards 是纯渲染、零依赖），代价是键名可能各改各的。
    一旦对不上，``border_for_status`` 会对每个状态都返回中性灰 —— **状态色整条静默失效**，
    单看任何一侧都「没问题」。所以在这里把两边钉在一起。
    """
    assert set(cards.STATUS_BORDERS) == {panel.STATUS_OK, panel.STATUS_ERROR,
                                        panel.STATUS_STOPPED}, cards.STATUS_BORDERS
    assert cards.border_for_status(panel.STATUS_OK) == "green"
    assert cards.border_for_status(panel.STATUS_ERROR) == "red"
    assert cards.border_for_status(panel.STATUS_STOPPED) == "yellow"
    # 未知 / 未给一律中性（不猜）
    assert cards.border_for_status(None) == "grey"
    assert cards.border_for_status("weird") == "grey"


def test_panel_status_priority_and_turn_reset():
    """回合结局的优先级与生命周期。

    优先级必须是 ``interrupted > failed > completed``：官方 ``completed`` 的表达式里
    **没有** ``interrupted``（``turn_finalizer.py``），所以「被中止但已产出部分正文」
    的回合会同时 ``completed=True, interrupted=True`` —— 先看 completed 就会把中止
    显示成绿色「已完成」。
    """
    panel.reset()
    try:
        # 正常完成
        panel.record_reasoning("s1", "t1", "想一下")
        panel.record_turn_end("s1", "t1", completed=True)
        assert panel.snapshot()["status"] == panel.STATUS_OK

        # 报错覆盖完成态
        panel.record_turn_end("s1", "t1", completed=True, failed=True)
        assert panel.snapshot()["status"] == panel.STATUS_ERROR

        # 中止覆盖报错与完成（这一条就是上面那个坑）
        panel.record_turn_end("s1", "t1", completed=True, failed=True, interrupted=True)
        assert panel.snapshot()["status"] == panel.STATUS_STOPPED

        # 三个都是 False 的收尾（会话级收尾等）不改状态 —— 没有结论就不猜
        panel.record_turn_end("s1", "t1")
        assert panel.snapshot()["status"] == panel.STATUS_STOPPED

        # 新回合必须把上一回合的结局清掉，否则新卡片会挂着旧颜色
        panel.begin_turn("s1", "t2")
        snap = panel.snapshot()
        assert snap is None or snap.get("status") is None, f"新回合没清状态：{snap!r}"
    finally:
        panel.reset()


def test_panel_mark_stopped_targets_the_bound_session():
    """``mark_stopped`` 必须改在**该 chat 绑定的那个会话**上（与渲染同一套归属）。

    归属用错方向的后果是「状态改在 A 会话、重绘的是 B 那条卡」—— 卡片永远不变色，
    而且两边都不报错。
    """
    panel.reset()
    try:
        panel.bind_chat_session("oc_one", "s1")
        panel.bind_chat_session("oc_two", "s2")
        panel.record_reasoning("s1", "t1", "会话一的推理")
        panel.record_reasoning("s2", "t2", "会话二的推理")
        assert panel.mark_stopped("oc_one") == "s1", "中止状态打到了别的会话上"
        assert panel.snapshot("oc_one")["status"] == panel.STATUS_STOPPED
        assert panel.snapshot("oc_two")["status"] is None, "别的会话被一起改色了"
        # 完全没有面板数据时也不能抛，只是返回空串
        panel.reset()
        assert panel.mark_stopped("oc_none") == ""
    finally:
        panel.reset()


def test_status_only_turn_still_renders_a_coloured_panel():
    """没有任何过程数据的回合也要有状态色 —— 面板是状态色**唯一**的载体。

    否则「简单问答」这种最常见的情形永远看不到完成色（效果 2 就等于没做）。
    """
    panel.reset()
    try:
        panel.bind_chat_session("oc_1", "s1")
        panel.record_reasoning("s1", "t1", "想一下")
        panel.record_turn_end("s1", "t1", interrupted=True)
        adapter.configure(unified_panel=True)
        node = adapter.LarkDeckMixin._ld_panel("oc_1")
        assert node is not None, "有结局却没有面板 = 状态色无处可放"
        assert node["border"]["color"] == "yellow", node["border"]
        assert node["elements"], "空面板飞书会拒，且用户看不到任何东西"

        # 连推理都没有的回合：快照仍要出，并且带状态
        panel.reset()
        panel.bind_chat_session("oc_1", "s1")
        panel.record_turn_end("s1", "t9", completed=True)
        snap = panel.snapshot("oc_1")
        assert snap is not None and snap["status"] == "ok", snap
        only = adapter.LarkDeckMixin._ld_panel("oc_1")
        assert only is not None and only["border"]["color"] == "green"
        assert "✅" in json.dumps(only, ensure_ascii=False), "状态文字没渲染"
    finally:
        panel.reset()


def test_reply_card_has_no_card_level_header():
    """决策 D2：回复卡没有卡片级 header（模型名/统计全在面板头）。

    1.0 的澄清卡**必须保留** header —— 那是它的标题，与 D2 无关；
    一起改掉会让澄清卡失去「需要你确认」这个唯一提示。
    """
    card = cards.reply_card("你好", streaming=True, footer="ctx 1k/2k")
    assert "header" not in card, f"回复卡不该有 header：{card.get('header')!r}"
    assert card["schema"] == "2.0" and card["body"]["elements"]
    assert card["config"]["streaming_mode"] is True

    legacy = cards.clarify_card("选一个", ["A", "B"], clarify_id="c1", session_key="sk")
    assert "header" in legacy, "1.0 澄清卡必须保留 header"
    assert "schema" not in legacy, "澄清卡不能混进 2.0"


def test_interrupt_redraws_the_stream_card_in_stopped_color():
    """``/stop`` 路径：**必须自己把卡重绘成中止态**，同时照常放行内核的中止。

    为什么不能等下一帧：``/stop`` 让 stream consumer 直接 return，而 native 模式下
    ``_abandon_native_stream`` 是空操作 —— **永远不会有收尾帧**。状态改在内存里、
    卡片纹丝不动，而且不报任何错。
    """
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0
    try:
        panel.reset()
        panel.bind_chat_session("oc_1", "s1")
        panel.record_reasoning("s1", "t1", "先想一下")
        raw = _make()
        updates = _wire_patch(raw)
        _run(raw.send_stream_frame("正在写的答案", finalize=False, chat_id="oc_1",
                                   turn_id="t1"))
        assert not updates, "首帧是新建卡，不该有 patch"
        assert _run(raw.send_stream_frame("正在写的答案，还没写完", finalize=False,
                                          chat_id="oc_1", turn_id="t1")) is True
        assert len(updates) == 1

        # 另开一个有内容的会话并让它成为「最近活跃」：适配器若把 chat_id 丢了，
        # 中止状态就会打到它身上（审计的 B1 变异，原来这条没有任何断言守着）
        panel.record_reasoning("s_other", "t_other", "别的会话的推理")

        _run(raw.interrupt_session_activity("sk1", "oc_1", metadata={"k": "v"}))
        assert ("SUPER.interrupt", "sk1", "oc_1", {"k": "v"}) in raw.calls, \
            "中止必须原样交给核心（那是这个方法的本质职责，不能被卡片挡住）"
        # 状态必须落在**这个 chat 绑定的会话**上，不能落到别的会话
        assert panel._STATE["s1"]["status"] == panel.STATUS_STOPPED, \
            "中止状态没写到绑定的会话上"
        assert not panel._STATE["s_other"].get("status"), "中止状态串到别的会话了"
        assert len(updates) == 2, "中止后必须自己重绘那张卡"
        body = json.loads(updates[1]["content"])
        try:
            assert _find_collapsible(body)["border"]["color"] == "yellow", \
                "中止态不是黄边 —— 状态色整条没落地"
        except (TypeError, KeyError) as exc:  # SEQ4 变异：重绘不带可折叠面板/状态色
            raise AssertionError(
                f"中止重绘必须带可折叠面板与黄边：{exc!r}; body={body!r}") from exc
        assert not body["config"].get("streaming_mode")
        joined = json.dumps(body, ensure_ascii=False)
        assert "正在写的答案，还没写完" in joined, "重绘必须沿用已经打出来的正文"
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_interrupt_never_blocks_stop_when_redraw_fails():
    """重绘怎么炸都不能影响中止本身 —— 那是内核最关键的路径之一。"""
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0
    try:
        panel.reset()
        panel.record_reasoning("s1", "t1", "先想一下")
        raw = _make()
        _wire_patch(raw)
        _run(raw.send_stream_frame("", finalize=False, chat_id="oc_1", turn_id="t1"))
        raw._client.im.v1.message.patch = lambda request: {"code": 99999, "msg": "nope"}
        _run(raw.interrupt_session_activity("sk1", "oc_1"))  # 不许抛
        assert ("SUPER.interrupt", "sk1", "oc_1", None) in raw.calls

        # 连 SDK 客户端都没有时也要放行
        naked = _make()
        naked._client = None
        _run(naked.interrupt_session_activity("sk2", "oc_2"))
        assert ("SUPER.interrupt", "sk2", "oc_2", None) in naked.calls
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def _log_text(records: list) -> str:
    """把日志记录拼成一段文本（断言「必须留痕」这类性质用）。

    用 ``getMessage()``（它已经把 ``%s`` 参数代进去），**不要**再 ``% r.args``
    —— 那是二次格式化，会抛 ``TypeError: not all arguments converted``。
    """
    return "\n".join(str(r.getMessage()) for r in records)


class _LogCapture:
    """把某个 logger 的日志收进列表（用来断言「必须留痕」这类性质）。"""

    def __init__(self, name: str) -> None:
        self.records: list = []
        self._logger = logging.getLogger(name)
        self._handler = logging.Handler()
        self._handler.emit = self.records.append  # type: ignore[method-assign]

    def __enter__(self):
        self._logger.addHandler(self._handler)
        self._old_level = self._logger.level
        self._logger.setLevel(logging.INFO)
        return self.records

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._old_level)
        return False

    def text(self) -> str:
        return "\n".join(r.getMessage() % r.args if r.args else r.getMessage()
                          for r in self.records)


def test_transient_error_table_keeps_the_documented_code_only():
    """瞬态码表要有门禁：官方写明的限流码必须在，**确定性拒收**的码不许在。

    第十路审计实测两条全绿变异：删掉 `230020`（`im.v1.message.patch` 官方错误码表里明写的
    频率限制）⇒ 限流时不再退避、一帧失败就掉 native；加入 `230099`（内容创建失败的**确定性**
    错误）⇒ 每次必然失败还白等 ~1.0s 退避。
    """
    assert 230020 in adapter._TRANSIENT_CODES, "官方 patch 限流码必须在（否则一帧失败就掉 native）"
    assert 99991400 in adapter._TRANSIENT_CODES
    for deterministic in (230099, 230025, 300305, 300314):
        assert deterministic not in adapter._TRANSIENT_CODES, (
            f"{deterministic} 是确定性拒收：重试必然同样失败，只会白等 1 秒")


def test_transient_feishu_errors_are_retried_not_fatal():
    """瞬态错误码要退避重试；非瞬态错误立刻返回（让内核按 fail-open 链回落）。

    为什么这条重要：一帧失败会被内核判成「本回合 native 不可用」，之后输出改走
    ``send()``、可能变成多条纯文本消息 —— 而飞书的限流错误**不匹配**内核的 flood
    启发式，走的是硬失败分支、连自适应退避都不启动。所以退避只能我们自己在这一层做。
    """
    defaults = dict(adapter._DEFAULTS)
    old_backoff = adapter._TRANSIENT_BACKOFF
    adapter._TRANSIENT_BACKOFF = (0.0, 0.0, 0.0)  # 测试里不等真实退避
    try:
        raw = _make()
        raw._ld_build_patch_request = lambda *, message_id, content: {
            "message_id": message_id, "content": content}
        calls: list = []

        def flaky(request):
            calls.append(request)
            if len(calls) == 1:
                return {"code": 99991400, "msg": "triggered rate limit"}
            return {"code": 0, "data": {"message_id": request["message_id"]}}

        raw._client.im.v1.message.patch = flaky
        with _LogCapture("larkdeck") as records:
            result = _run(raw._ld_update_card("oc_1", "om_1", {"schema": "2.0"}))
        assert getattr(result, "success", False), "瞬态错误重试后应当成功"
        assert len(calls) == 2, f"应当重试一次：{len(calls)}"
        assert "瞬态错误码" in _log_text(records), "重试必须留痕"

        # 非瞬态错误码：不重试，立刻把失败交回去
        raw2 = _make()
        raw2._ld_build_patch_request = lambda *, message_id, content: {
            "message_id": message_id, "content": content}
        calls2: list = []
        raw2._client.im.v1.message.patch = lambda request: (
            calls2.append(request) or {"code": 230001, "msg": "invalid msg_type"})
        result2 = _run(raw2._ld_update_card("oc_1", "om_1", {"schema": "2.0"}))
        assert not getattr(result2, "success", True)
        assert len(calls2) == 1, f"非瞬态错误不该重试：{len(calls2)}"

        # 一直瞬态失败：重试用尽后如实返回失败（不无限重试）
        raw3 = _make()
        raw3._ld_build_patch_request = lambda *, message_id, content: {
            "message_id": message_id, "content": content}
        calls3: list = []
        raw3._client.im.v1.message.patch = lambda request: (
            calls3.append(request) or {"code": 300317, "msg": "sequence conflict"})
        result3 = _run(raw3._ld_update_card("oc_1", "om_1", {"schema": "2.0"}))
        assert not getattr(result3, "success", True)
        assert len(calls3) == 1 + len(old_backoff), f"重试次数应受上限约束：{len(calls3)}"
    finally:
        adapter._TRANSIENT_BACKOFF = old_backoff
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_native_frame_failure_and_success_are_both_visible_in_logs():
    """native 流式必须**自证**：收尾时证明它跑完了，失败时留下线索。

    帧失败会让内核静默关掉本回合的 native（输出退化成多条纯文本），用户在飞书那侧
    只看到「卡片怎么变成一条条消息了」。所以两个方向都要有日志：成功 = 帧数，
    失败 = 明确的 WARNING（docs/lessons.md 推论 1）。
    """
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0
    try:
        panel.reset()
        raw = _make()
        _wire_patch(raw)
        with _LogCapture("larkdeck") as records:
            _run(raw.send_stream_frame("", finalize=False, chat_id="oc_1", turn_id="t1"))
            _run(raw.send_stream_frame("写完了一半", finalize=False,
                                       chat_id="oc_1", turn_id="t1"))
            _run(raw.send_stream_frame("写完了", finalize=True, chat_id="oc_1", turn_id="t1"))
        text = _log_text(records)
        assert "native 流式收尾" in text, f"收尾没有自证日志：{text!r}"
        assert "更新 2 帧" in text, f"帧数不对（首帧 + 一次更新）：{text!r}"

        # 失败方向：必须有明确 WARNING（且返回 False 让内核回落）
        adapter.LarkDeckMixin._ld_stream_fail._at = 0.0  # 清掉限流窗口
        raw2 = _make()
        _wire_patch(raw2)
        raw2._client.im.v1.message.patch = lambda request: {"code": 230001, "msg": "nope"}
        with _LogCapture("larkdeck") as records2:
            seed_ok = _run(raw2.send_stream_frame("", finalize=False,
                                                  chat_id="oc_2", turn_id="t1"))
            frame_ok = _run(raw2.send_stream_frame("第一帧", finalize=False,
                                                   chat_id="oc_2", turn_id="t1"))
        text2 = _log_text(records2)
        assert seed_ok is True, "建卡那一步应当成功（失败的是后面的更新帧）"
        assert frame_ok is False, "帧失败必须如实返回 False —— 内核据此回落"
        assert "native 流式帧失败" in text2, f"帧失败没有留痕：{text2!r}"
        assert "回落 send/edit" in text2, "日志要说清后果（用户会看到什么）"
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_clarify_dialect_switch_and_no_dialect_mixing():
    """``clarify_dialect`` 选方言；两种方言**内部都不许混用**。

    混用的后果是静默失灵：1.0 的 ``action`` 行放进 2.0 卡 → 飞书拒收（230099）；
    而 2.0 组件不带 ``behaviors`` → 点击永远到不了服务端（点了没反应）。
    """
    defaults = dict(adapter._DEFAULTS)
    try:
        adapter.configure(clarify_cards=True)
        # 默认现在是 **2.0**（2026-09-13 翻的，前提见 `_DEFAULTS` 的注释）
        two = adapter.LarkDeckMixin._ld_build_clarify_card(
            "选哪个？", ["A", "B"], clarify_id="c1", session_key="sk", multi=False)
        assert two.get("schema") == "2.0", two

        # 显式配回 1.0：顶层 elements + action 行，没有 schema/body（这条路必须一直可用）
        adapter.configure(clarify_dialect="1.0")
        one = adapter.LarkDeckMixin._ld_build_clarify_card(
            "选哪个？", ["A", "B"], clarify_id="c1", session_key="sk", multi=False)
        assert "schema" not in one and isinstance(one.get("elements"), list), one
        assert _buttons_of(one), "1.0 卡的点击载体是 action 行"

        adapter.configure(clarify_dialect="2.0")
        two = adapter.LarkDeckMixin._ld_build_clarify_card(
            "选哪个？", ["A", "B"], clarify_id="c1", session_key="sk", multi=False)
        assert two.get("schema") == "2.0" and "body" in two, two
        tags = _all_tags(two)
        assert "select_static" in tags and "input" in tags, tags
        assert "action" not in tags, "2.0 卡里出现 1.0 的 action 行会被飞书拒收"
        blob = json.dumps(two, ensure_ascii=False)
        assert "larkdeck_action" in blob, "拦截键必须原样带在 behaviors.value 里"

        # 多选：multi_select_static，且**不给**自由输入框（与编号/标签解析打架）
        multi = adapter.LarkDeckMixin._ld_build_clarify_card(
            "选哪些？", ["A", "B", "C"], clarify_id="c1", session_key="sk", multi=True)
        mtags = _all_tags(multi)
        assert "multi_select_static" in mtags and "select_static" not in mtags, mtags
        assert "input" not in mtags

        # 回填卡必须与待答卡同方言
        assert adapter.LarkDeckMixin._ld_build_resolved_card(
            question="Q?", answer="A", user_name="u").get("schema") == "2.0"
        adapter.configure(clarify_dialect="1.0")
        assert "schema" not in adapter.LarkDeckMixin._ld_build_resolved_card(
            question="Q?", answer="A", user_name="u")

        # 认不出的值 → 按 1.0 处理（不猜、不抛）
        adapter.configure(clarify_dialect="3.0")
        weird = adapter.LarkDeckMixin._ld_build_clarify_card(
            "Q?", ["A"], clarify_id="c1", session_key="sk", multi=False)
        assert "schema" not in weird, "认不出的方言值不许猜成 2.0"

        # 选项 value 必须唯一（官方：重复会让交互异常）
        dup = cards.clarify_card_2("Q?", ["A", "A", "B"], clarify_id="c", session_key="s")
        select = [e for e in dup["body"]["elements"] if e["tag"] == "select_static"][0]
        values = [o["value"] for o in select["options"]]
        assert values == ["A", "B"], values
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_clarify_card_2_shows_the_choices_and_never_says_tap_a_button():
    """第一组 · G1：**2.0 澄清卡必须让人看见有哪几个选项**，且脚注不许说「点按钮」。

    起因（用户对着 aiduPOP 的截图追问过这件事）：默认方言翻成 2.0 之后，选项文本
    **只活在下拉里** —— 不点开就看不出有哪几个选项；而脚注还写着「点按钮，或直接回复
    文字都行」，可这张卡上**根本没有按钮**。两件事都是「计划里有、实现漏了」，
    不是权衡（`docs/plan-6-effects.md` 的路径 A 本就写了选项要外显）。

    三条判据缺一条就会退化成「看起来有列表、点下去对不上」：
      ① 卡面列表**逐条等于**下拉的显示标签（同源）；② 提交值仍是**原始选项文本**
      （答案要的是规范标签，不是「1. A 方案」）；③ 脚注按方言分流。
    """
    choices = ["A 方案", "B 方案", "A 方案"]            # 故意带重复：验同源也验去重
    card2 = cards.clarify_card_2("选哪个？", choices, clarify_id="c", session_key="s")
    els = card2["body"]["elements"]
    # ⚠️ 脚注**也是** markdown（只是带 text_size=notation）⇒ 判据必须把它排除掉，
    # 否则「数够两个 markdown」会被脚注顺带满足（恒真断言的又一个形态）
    mds = [e for e in els if e["tag"] == "markdown" and e.get("text_size") != "notation"]
    assert len(mds) == 2, f"2.0 卡应当是「问题 + 可见选项列表」两个正文 markdown：{[e['tag'] for e in els]}"
    assert mds[0]["content"].endswith("选哪个？"), mds[0]["content"]
    sel = [e for e in els if e["tag"] == "select_static"][0]
    labels = [o["text"]["content"] for o in sel["options"]]
    values = [o["value"] for o in sel["options"]]
    assert mds[1]["content"].split("\n") == labels, (
        f"卡面列表必须与下拉标签**逐条相同**（同源）：{mds[1]['content']!r} vs {labels}")
    assert values == ["A 方案", "B 方案"], f"去重后提交值必须是原始选项文本：{values}"
    foot2 = [e["content"] for e in els if e.get("text_size") == "notation"]
    assert foot2 and "按钮" not in foot2[0], \
        f"2.0 卡上没有按钮，脚注就不许说「按钮」：{foot2}"
    # ⚠️ **编号必须从 1 开始、且与原选项顺序一致**（这条断言是补上的，之前确实没有）：
    #    文字回答那条路是**核心按 1 基下标**解析的（用户回「2」= 第 2 个选项），所以卡面编号
    #    一旦错位（0 基 / 跳号 / 按去重后重排），用户**照着卡面回数字就会答错** ——
    #    而卡面与下拉**仍然「同源」**，四门禁全绿。这是「同源」这个判据**覆盖不到**的一面。
    _plain = cards.clarify_card_2("Q?", ["甲", "乙", "丙"], clarify_id="c", session_key="s")
    _md_plain = [e for e in _plain["body"]["elements"]
                 if e["tag"] == "markdown" and e.get("text_size") != "notation"]
    assert _md_plain[1]["content"].split("\n") == ["1. 甲", "2. 乙", "3. 丙"], \
        f"卡面编号必须是 1 基且与原顺序一致（文字回答按 1 基下标解析）：{_md_plain[1]['content']!r}"
    # ⚠️ **1.0 卡那一侧是另一处 `enumerate`** —— 同一个隐患两处真相：按钮文字里的编号也必须是
    #    1 基（用户回数字由核心按 1 基解析）。只钉 2.0 那侧等于漏掉一半。
    _legacy = cards.clarify_card("Q?", ["甲", "乙", "丙"], clarify_id="c", session_key="s")
    _btns = [b["text"]["content"]
             for el in _legacy["elements"] if el.get("tag") == "action"
             for b in el["actions"]]
    assert _btns[:3] == ["1. 甲", "2. 乙", "3. 丙"], \
        f"1.0 按钮的编号也必须是 1 基：{_btns}"
    # ⚠️ **去重会让可见编号出现「空洞」，但编号必须始终指向它旁边那一项**（2026-09-16 第四轮审计）：
    #    `_clarify_choice_pairs` 去掉重复项，编号沿用**原始列表的 1 基下标** —— 这是**对的**，
    #    因为核心按 `choices[n-1]` 解析文字回答（用户回「2」= 第 2 个选项）。
    #    代价：`["甲","乙","甲","丙"]` 显示成 `1. 甲 / 2. 乙 / 4. 丙`（**没有 3**）。
    #    ⇒ **绝不许为了让编号连续而重排**：那会让卡面**说谎**（卡面写「3. 丙」而核心给「甲」），
    #    比跳号严重得多（跳号只是让人困惑，说谎是给出**错误的答案**）。
    #    这一格钉的就是「卡面数字 → 那一项」这条映射。
    _dup = ["甲", "乙", "甲", "丙"]
    for _label, _value in cards._clarify_choice_pairs(_dup):
        _n = int(_label.split(".")[0])
        assert _dup[_n - 1] == _value, (
            f"卡面编号必须指向**同一项**（核心按 `choices[n-1]` 解析文字回答）："
            f"卡面写 {_label!r}，而 choices[{_n - 1}] = {_dup[_n - 1]!r}")
    # 反面：不许「去重后从 1 重排」把空洞填上（那会满足「编号连续」却让卡面说谎）
    assert [lbl.split(".")[0] for lbl, _ in cards._clarify_choice_pairs(_dup)] == ["1", "2", "4"], \
        "编号必须沿用**原始下标**（去掉重复项后留下的空洞是**已知的观感代价**，见交接单登记）"
    # ⚠️ **选项里含换行**（模型完全可以给出多行选项）会同时坏两件事（审计 B1）：
    #    ① 「卡面列表与下拉标签**逐条相同**」这条同源判据**不成立**（列表凭空多出一行）；
    #    ② `# ` / `- ` / `> ` 落到**行首** ⇒ 直接变成 markdown 语法 ——
    #       而 `escape_inline_md` 不转义 `#`/`-`/`>`，正是以「插入点永远在行首标记**之后**」
    #       为前提的（那个前提被内嵌换行推翻）。
    #    ⇒ 展示标签必须**先折叠空白**；提交值仍是**原文**（答案要能对上）。
    _nl = ["ok", "第一行\n# 我是标题", "第三行"]
    _nl_card = cards.clarify_card_2("q", _nl, clarify_id="c", session_key="s")
    _nl_els = _nl_card["body"]["elements"]
    _nl_mds = [e for e in _nl_els
               if e["tag"] == "markdown" and e.get("text_size") != "notation"]
    _nl_sel = [e for e in _nl_els if e["tag"] == "select_static"][0]
    assert _nl_mds[1]["content"].split("\n") == [o["text"]["content"] for o in _nl_sel["options"]], \
        f"含换行的选项也必须保持同源（列表行数 == 下拉条数）：{_nl_mds[1]['content']!r}"
    assert not any(l.lstrip().startswith(("#", "-", ">"))
                   for l in _nl_mds[1]["content"].split("\n")), \
        f"折叠之后不许残留行首 markdown 标记（那会被当语法解释）：{_nl_mds[1]['content']!r}"
    assert [o["value"] for o in _nl_sel["options"]] == _nl, \
        "提交值必须仍是**原文**（含换行）—— 折叠只作用于展示"
    # 审计 B2：**已答复卡**的问题行也得转义，否则用户点一下就从「按字面显示」
    # 变成「露出 markdown 语法」（`*` 变斜体、`[x]` 变链接）
    _q2 = "选 A*B 还是 C_D？"          # 本地定义：这一格在**本**测试函数里，别借别的函数的变量
    _res2 = cards.clarify_resolved_card_2(question=_q2, answer="A*B 方案", user_name="u")
    _res2q = _res2["body"]["elements"][0]["content"]
    assert "A\\*B" in _res2q, f"2.0 已答复卡的问题同样要转义：{_res2q!r}"
    _res1 = cards.clarify_resolved_card(question=_q2, answer="A", user_name="u")
    _res1q = str(_res1["elements"][0].get("content", ""))
    assert "A\\*B" in _res1q, f"1.0 已答复卡同理：{_res1q!r}"
    # 1.0 卡**有**真按钮 ⇒ 它的脚注就该说「点按钮」（反向对照，防「两处都改掉」）
    card1 = cards.clarify_card("选哪个？", choices, clarify_id="c", session_key="s")
    assert "按钮" in json.dumps(card1, ensure_ascii=False), "1.0 卡的脚注被误改了"
    # 多选：不给自由输入框（与编号/标签解析打架），但可见列表照旧
    card_m = cards.clarify_card_2("选哪个？", choices, clarify_id="c", session_key="s",
                                  multi=True)
    tags_m = [e["tag"] for e in card_m["body"]["elements"]]
    assert "multi_select_static" in tags_m and "input" not in tags_m, tags_m
    body_md_m = [e for e in card_m["body"]["elements"]
                 if e["tag"] == "markdown" and e.get("text_size") != "notation"]
    assert len(body_md_m) == 2, f"多选卡同样要有可见列表（问题 + 列表）：{tags_m}"
    # 新文案必须真的双语（t() 对未知键**原样返回**，所以拿它自己当「键存在」的判据）
    zh, en = i18n.t("clarify.hint_2", i18n.ZH), i18n.t("clarify.hint_2", i18n.EN)
    assert zh != "clarify.hint_2" and en != "clarify.hint_2", "clarify.hint_2 这个键不存在"
    assert zh and en and zh != en, f"clarify.hint_2 必须双语且不同：{zh!r}/{en!r}"


def test_clarify_answer_extraction_covers_all_three_shapes():
    """三种载荷各把答案放在不同字段：``value.answer`` / ``action.option`` / ``action.input_value``。

    读错字段的后果是**点了没反应**（或提交一个空答案）—— 静默失灵。
    多选尤其容易错：网关要的是 **JSON 数组字符串**，自己拼字符串会让它把
    「A,B」当成一个选项。
    """
    cls = adapter.LarkDeckMixin

    class _Action:
        def __init__(self, **kw):
            for key in ("tag", "option", "options", "input_value", "value", "form_value"):
                setattr(self, key, kw.get(key))

    # 1.0：答案在我们自己的 value 里
    assert cls._ld_clarify_answer(_Action(value={"answer": "A"}), {"answer": "A"}) == ("A", "choice")
    # 2.0 单选下拉
    assert cls._ld_clarify_answer(_Action(tag="select_static", option="B"), {}) == ("B", "choice")
    # 2.0 多选下拉：值在 action.options（**复数**），答案是 **JSON 数组字符串**
    # （核心 `_coerce_multi_select_text` 的规范形式；下游 `_parse_multi_select_response`
    #  对它走 json.loads，所以选项文本里含逗号也不会被打散）
    answer, mode = cls._ld_clarify_answer(
        _Action(tag="multi_select_static", options=["A 方案", "C 方案"]), {})
    assert mode == "multi" and json.loads(answer) == ["A 方案", "C 方案"], (answer, mode)
    # 线格式给的是逗号串时等价处理
    answer2, mode2 = cls._ld_clarify_answer(
        _Action(tag="multi_select_static", options="A 方案, C 方案"), {})
    assert mode2 == "multi" and json.loads(answer2) == ["A 方案", "C 方案"], (answer2, mode2)
    # ⚠️ 反面：**不能再只认 `option` 是列表** —— 官方形状用的是 `options`，
    # 只读 `option` 会让用户「选完没反应」（2026-09-13 审计实测）。
    assert cls._ld_clarify_answer(
        _Action(tag="multi_select_static", option=["A"]), {})[1] == "none", \
        "`option` 是列表不是官方形状，不该被当成多选答案"
    # 2.0 输入框
    assert cls._ld_clarify_answer(
        _Action(tag="input", input_value="  自己写的  "), {}) == ("自己写的", "text")
    # 什么都没有 → none（调用方保持安静，不提交空答案）
    assert cls._ld_clarify_answer(_Action(), {}) == (None, "none")
    assert cls._ld_clarify_answer(_Action(input_value="   "), {}) == (None, "none")
    assert cls._ld_clarify_answer(_Action(options=[]), {}) == (None, "none")

    # `action.value` 是 JSON 字符串（同类项目实测见过的形态）也要能读出来 ——
    # 不归一的话 `.get()` 会抛，被外层吞掉后点击就是「没反应」。
    assert cls._ld_normalize_value(
        _Action(value='{"larkdeck_action": "clarify", "clarify_id": "c1"}')) == {
            "larkdeck_action": "clarify", "clarify_id": "c1"}
    # 表单提交按钮：value 为空、数据在 form_value 里
    assert cls._ld_normalize_value(
        _Action(value={}, form_value={"larkdeck_action": "clarify", "clarify_id": "c9"})) == {
            "larkdeck_action": "clarify", "clarify_id": "c9"}
    # 坏 JSON 不能让点击炸掉（交回内置实现）
    assert cls._ld_normalize_value(_Action(value="{not json")) == {}


def test_clarify_free_text_only_commits_when_the_core_accepts_it():
    """2.0 输入框的自由文本：**只有核心判据说「已解析」才回填卡片**。

    自己拼答案会废掉多选（「1,3」会被当成一个叫「1,3」的选项）；而「没抛异常就当
    成功」会让卡片显示「已答复」、agent 却仍阻塞在网关 —— 答案永久丢失且用户失去
    重试机会（这条纪律 1.0 路径上已经有，2.0 的输入框走的是另一条判据）。
    """
    defaults = dict(adapter._DEFAULTS)
    original = compat.clarify_text_answer
    # 这两处是**类属性/模块属性**的替换，必须在 finally 里还原 —— 忘了还原会让
    # 后面的测试拿着假实现跑（自证循环的一种，且症状与「测试顺序」耦合）。
    #
    # ⚠️⚠️ **必须从 `__dict__` 取 staticmethod 对象本身**（2026-09-14 实测的既存污染）：
    # 写成 `adapter.LarkDeckMixin._ld_build_resolved_card` 拿到的是**解包后的函数**，
    # 原样赋回去就变成了普通实例方法 ⇒ 之后所有 `self._ld_build_resolved_card(...)`
    # 都会把 self 当第一个位置实参 ⇒ `TypeError: takes 0 positional arguments but 1 given`。
    # 而那个异常被点击入口的兜底吞掉、表现成「点击走内置回落」，**与真实原因毫无关系**
    # （R8 的新用例排到它后面，正是被这条咬住才发现的）。
    try:
        original_builder = adapter.LarkDeckMixin.__dict__["_ld_build_resolved_card"]
    except AttributeError as exc:  # R8-6 变异：误取 __func__ 会污染后续用例
        raise AssertionError(f"必须从 __dict__ 取 staticmethod 对象本身：{exc!r}") from exc
    try:
        panel.reset()
        adapter.configure(clarify_cards=True, clarify_dialect="2.0")
        raw = _make()
        calls: list = []

        def fake_attempt(clarify_id, text):
            calls.append((clarify_id, text))
            return outcome[0]

        outcome = ["resolved"]
        compat.clarify_text_answer = fake_attempt
        try:
            event = types.SimpleNamespace(
                operator=types.SimpleNamespace(open_id="ou_ok"),
                action=types.SimpleNamespace(tag="input", value={"larkdeck_action": "clarify",
                                                                "clarify_id": "c1",
                                                                "session_key": "sk-1",
                                                                "question": "选哪个？"},
                                             option=None, input_value="1,3"))
            # 观测点：回填卡的构造被调用过（把构造换成记录器，别去窥探 SDK 响应对象内部）
            filled: list = []
            adapter.LarkDeckMixin._ld_build_resolved_card = staticmethod(
                lambda *, question, answer, user_name: filled.append((question, answer, user_name))
                or {"filled": True})
            result = raw._on_card_action_trigger(types.SimpleNamespace(event=event))
            # 必须带上**这张卡自己的** clarify_id —— 不带就会答错问题（见 A2）
            assert calls == [("c1", "1,3")], calls
            assert filled == [("选哪个？", "1,3", "汪老师")], filled
            assert result is not None, "解析成功时应当回填卡片"

            # 核心判据拒绝（无效选择 / 散文）→ **不**回填卡片（否则卡片谎报已答复、
            # agent 却仍阻塞在网关，答案永久丢失）
            outcome[0] = "rejected_selection"
            filled.clear()
            result2 = raw._on_card_action_trigger(types.SimpleNamespace(event=event))
            assert filled == [], f"被拒绝的输入不该回填卡片：{filled!r}"
            assert result2 is not None, "至少要有「无卡片变更」的响应"
        finally:
            compat.clarify_text_answer = original
            adapter.LarkDeckMixin._ld_build_resolved_card = original_builder
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()
        panel.reset()


def test_tool_args_preview_redacts_credentials_and_keeps_normal_fields():
    """第一组 · G1：工具参数预览**必须脱敏** —— 卡片会出现在群里，人人可见。

    我们是 raw JSON 预览（只做了有界化），而钩子拿到的是**原始**参数：
    ``export TOKEN=…`` / ``Authorization: Bearer …`` / ``{"api_key": "…"}`` 都会原样进卡。
    判据要**两头都钉**：凭据必须被涂掉，**正常字段一个字节都不许动** ——
    猜值式的脱敏会把正常内容涂掉，那比不脱敏更难查（`docs/lessons.md` 对「猜」的纪律）。
    另：脱敏必须**幂等**（预览可能被重复处理），否则第二次会把 `***` 再改写一遍。
    """
    cases = {
        json.dumps({"api_key": "sk-live-1234567890"}): '"api_key": "***"',
        # ⚠️ 期望值在 20:52 变了（**更安全**，不是放松）：新增的「头部形态」规则
        #    （审计 A2：`Cookie: …` 原先零脱敏）会把 `Authorization: <值>` **整个值**涂掉，
        #    所以输出是 `Authorization: ***` 而不是旧的 `Authorization: Bearer ***`
        #    —— 连 scheme 一起藏，比只藏 token 更好。
        "curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc'": "Authorization: ***",
        "export GITHUB_TOKEN=ghp_abcdefghijklmn": "GITHUB_TOKEN=***",
        "cat /Users/example/Code/secret/x.py": "cat ~/Code/secret/x.py",
    }
    for raw, want in cases.items():
        got = panel.redact_inline_secrets(raw)
        assert want in got, f"这类凭据必须被脱敏：{raw!r} → {got!r}"
        assert got == panel.redact_inline_secrets(got), f"脱敏必须幂等：{got!r}"
    # ⚠️ **接线判据**（这一条是补上的：初版只直接调 `redact_inline_secrets`，
    # 于是把 `_args_preview` 里那一行调用整个撤掉，测试**照样全绿** ——
    # 验的是函数，不是接线。必须有一条走真实入口的断言）。
    _seen = panel._args_preview({"api_key": "sk-live-1234567890", "path": "a.py"})
    assert "sk-live-1234567890" not in _seen, f"真实入口必须脱敏：{_seen!r}"
    assert "***" in _seen and "a.py" in _seen, _seen
    # ⚠️ 值里带**转义**的凭据（`json.dumps` 会把值里的 `"` 写成 `\"`、`\` 写成 `\\`）——
    #    这一格是被**审计的自制变异台**逼出来的（它的 `X2`：把值类收紧就全绿 ⇒ 说明没断言守着）。
    #    顺着它查下去发现的是**比「断言没牙」更严重的真缺陷**：旧的值类 `[^"]*` 会在转义引号处
    #    **提前收尾** ⇒ 只涂掉前半截、后半截照旧露在卡上。**这比完全不脱敏更危险**：
    #    看起来像处理过了（半截凭据同样是凭据）。
    for _raw in (json.dumps({"password": 'zqx"jwt'}),        # 值里有转义引号
                 json.dumps({"password": "zqx\\jwt"})):      # 值里有反斜杠
        _out = panel.redact_inline_secrets(_raw)
        assert "***" in _out and "zqx" not in _out and "jwt" not in _out, \
            f"带转义的凭据必须**整段**涂掉（半截也是凭据）：{_raw!r} → {_out!r}"
    # ⚠️ 下面这一组是**审计逼出来的真泄漏**（不是「断言没牙」，是功能本身漏了）：
    #    ① `KEY="值"` —— `_args_preview` 喂进来的是 `json.dumps` 的产物，命令里的引号会变成
    #       `\"`，而旧值类撞上第一个 `"` 就收尾 ⇒ **凭据原文完整露出**（生产里最常见的写法）；
    #    ② `Cookie:` 头 —— 四条规则一条都不覆盖它（`cookie` 原先只出现在 JSON **键名**规则里），
    #       而同批里 `Authorization: Bearer …` 是被涂掉的 ⇒ 格外容易误以为「头都覆盖了」；
    #    ③ 家目录规则**把 URL 截断**：`https://x.com/home/dashboard` → `https://x.com~`
    #       —— 用户看到一个**不存在的 URL**，比不脱敏更难查。
    for _raw, _must_hide in (
            (json.dumps({"command": 'export OPENAI_API_KEY="sk-abcdef0123456789"'}),
             ("sk-abcdef0123456789",)),
            (json.dumps({"command": 'curl -H "Cookie: session=abcdef1234567890" https://x'}),
             ("abcdef1234567890",)),
            (json.dumps({"h": "Set-Cookie: sid=zzz123456789"}), ("zzz123456789",))):
        _out = panel._args_preview(_raw)
        assert all(s not in _out for s in _must_hide), \
            f"这条形状必须被脱敏（审计实测漏过）：{_raw!r} → {_out!r}"
    # 反向：URL 里的 `/home/`、`/Users/` **不是家目录**，一个字节都不许动
    for _keep in (json.dumps({"url": "https://example.com/home/dashboard?tab=1"}),
                  json.dumps({"msg": "see https://github.com/Users/guide for details"})):
        assert panel.redact_inline_secrets(_keep) == _keep, \
            f"URL 不许被当成家目录截断（那会造出一个不存在的 URL）：{_keep!r}"
    # 反向对照：凭据词出现在键名**中间**的正常字段不许被动。
    # ⚠️ 用例必须用**字符串值**（`4096` 这种数字 JSON 规则本来就匹配不到 ——
    # 初版就是这么写的，于是「放宽判据」那条变异全绿，用例等于没写）。
    for raw in (json.dumps({"max_tokens": "4096"}),
                json.dumps({"token_count": "5", "input_tokens": "900"}),
                "MAX_TOKENS=4096 python run.py",
                "echo hello world"):
        assert panel.redact_inline_secrets(raw) == raw, f"不许误伤正常内容：{raw!r}"
    # ⚠️ **两个窗口的大小关系是「可见的未脱敏凭据 = 0」的**充要前提**（2026-09-16 审计实测）：
    #    `_args_preview` 的顺序是 `_shrink → json.dumps → text[:_REDACT_SCAN_CHARS] → 脱敏 →
    #    text[:_ARGS_PREVIEW_CHARS]`。**先截断再脱敏**之所以安全，全靠「扫描窗口 ≥ 展示窗口」
    #    —— 被展示的那一段必然是脱敏过的。若把 `_REDACT_SCAN_CHARS` 调到 ≤ `_ARGS_PREVIEW_CHARS`，
    #    同一个形状立刻变成**可见的未脱敏凭据**（审计做了穷举：今天可见未脱敏 0 例，
    #    而那个「0」是这条不等式换来的，不是设计上的巧合）。⇒ 把不等式本身钉住。
    assert panel._REDACT_SCAN_CHARS > panel._ARGS_PREVIEW_CHARS, (
        f"脱敏扫描窗口（{panel._REDACT_SCAN_CHARS}）必须**大于**展示窗口"
        f"（{panel._ARGS_PREVIEW_CHARS}）：先截断再脱敏的安全性正是由这条不等式保证的")
    # ⚠️ **Bearer 规则的独有贡献**（审计 C3）：上面那句 `Authorization: Bearer …` 断言
    #    其实**没有**守住 Bearer 规则 —— 「头部形态」规则会把 `Authorization:` 的**整个值**
    #    涂掉，所以把 Bearer 规则**整条删掉**，那一格照样通过（变异 `G1-6` 实测：四门禁全绿）。
    #    Bearer 规则真正**独占**的形状是「没有头部名、只有一个裸的 `Bearer <token>`」：
    #    那一刻它既不是 JSON 键值对、也没有 `Key:` 前缀、更没有 `=`，另外三条规则一条都不覆盖。
    for _raw, _must_hide in (
            (json.dumps({"note": "Bearer sk-live-abcdef123456"}), ("sk-live-abcdef123456",)),
            ("调用方式：Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig 收尾",
             ("eyJhbGciOiJIUzI1NiJ9.payload.sig",))):
        _out = panel.redact_inline_secrets(_raw)
        assert all(s not in _out for s in _must_hide), \
            f"裸 Bearer 也必须被涂掉（这是 Bearer 规则唯一独占的形状）：{_raw!r} → {_out!r}"
    # 反面：`Bearer` 后面没有 token（或短于 8 字符）时**一个字节都不许动** —— 那多半是正常英文
    for _raw in ("the bearer of good news", "Bearer 短", "Bearer ab"):
        assert panel.redact_inline_secrets(_raw) == _raw, f"不许误伤正常内容：{_raw!r}"


def test_clarify_question_and_choices_are_escaped_but_answers_stay_raw():
    """第二组 · G2：澄清卡的问题与选项标签要按**字面**显示，而提交值必须是**原文**。

    隐患：问题与选项都是模型/用户给的自由文本，``*`` / ``[`` / ``_`` 会被 markdown 当语法，
    轻则渲染错乱、重则整段吞掉。两个方言**各写了一遍** ``md(f"❓ {question}")``，
    现在收敛成一个构造点（同一个隐患，两处真相）。

    判据两头都要钉：① 展示的那一份被转义；② 回给核心的 ``value`` / ``answer`` **一个字节
    都不许改** —— 转义过的答案回给网关就对不上了（那是最难查的形态：卡上看着对、提交无效）。
    """
    q = "选 A*B 还是 C_D？"
    ch = ["A*B 方案", "带[链接]的选项"]
    card2 = cards.clarify_card_2(q, ch, clarify_id="c", session_key="s")
    els = card2["body"]["elements"]
    body_md = [e for e in els if e["tag"] == "markdown" and e.get("text_size") != "notation"]

    def _body_md(i):  # G1-1 变异：撤掉可见选项列表后，这段必须红成断言而不是 IndexError
        try:
            return body_md[i]
        except IndexError as exc:
            raise AssertionError(
                f"2.0 澄清卡缺少第 {i} 段正文 markdown（可见选项列表被撤掉）："
                f"{[e['tag'] for e in els]!r}") from exc

    assert "A\\*B" in _body_md(0)["content"] and "C\\_D" in _body_md(0)["content"], _body_md(0)
    sel = [e for e in els if e["tag"] == "select_static"][0]
    assert [o["value"] for o in sel["options"]] == ch, \
        f"⚠️ 提交值必须是**原文**（转义过的答案对不上）：{[o['value'] for o in sel['options']]}"
    assert all("\\" not in o["text"]["content"] for o in sel["options"]), \
        "下拉标签是 plain_text ⇒ 不需要转义（转了用户就看见反斜杠）"
    list_md = _body_md(1)["content"]
    assert "A\\*B" in list_md and "\\[链接\\]" in list_md, list_md
    # 剥掉转义反斜杠后必须与提交值逐条对上（**同源不受转义影响**）
    assert [ln.replace("\\", "") for ln in list_md.split("\n")] == \
        [f"{i}. {c}" for i, c in enumerate(ch, start=1)], list_md
    # 1.0 卡的问题走**同一个**构造点（收敛的意义就在这里）
    c1 = cards.clarify_card(q, ch, clarify_id="c", session_key="s")
    # ⚠️ 看**元素内容**而不是 `json.dumps` 的字符串：JSON 会把反斜杠再转义一层，
    #    在 dump 上找 `A\*B` 永远找不到（第一版就是这么写的，白红了一次）
    q1 = c1["elements"][0]["content"]
    assert "A\\*B" in q1, f"1.0 卡的问题也必须转义：{q1!r}"
    # 空/None 不许炸（渲染是装饰，不能因为它把整张卡搞坏）
    assert cards.escape_inline_md("") == "" and cards.escape_inline_md("abc") == "abc"


def test_frame_footer_carries_the_card_trace_id():
    """第二组 · G2：帧页脚带**本卡短码**，让「用户截图 → 日志」有确定的对齐方式。

    起因：用户截图里的卡片与日志**对不上号** —— 日志有 chat/round/turn，卡片上什么都没有，
    于是只能靠时间戳猜（多会话并发时根本猜不出来）。
    两条纪律同样重要：**基数页脚为空时不许因为短码就渲染出页脚**（`页脚=无` 是排查
    「钩子没喂数据」的入口），短码也**只能取本帧的卡**（不能是进程级快照，否则串台）。
    """
    assert adapter._ld_trace_id("om_abcdef123456") == "123456"
    assert adapter._ld_trace_id("m_ck_1") == "m_ck_1"        # 短于 6 位就原样
    assert adapter._ld_trace_id("") == "" and adapter._ld_trace_id(None) == ""
    raw = _make()
    orig = adapter.LarkDeckMixin._ld_footer
    try:
        seen: dict = {}
        def _fake_footer(cls, **kwargs):
            seen.update(kwargs)
            return "ctx 1k/2k"
        adapter.LarkDeckMixin._ld_footer = classmethod(_fake_footer)
        assert raw._ld_frame_footer({"message_id": "om_abcdef123456"}) == \
            "ctx 1k/2k · \U0001f516 123456", "帧页脚必须接上本卡短码"
        # 还没建卡（没有 message_id / card_id）⇒ 不加短码，只有基数页脚
        assert raw._ld_frame_footer({}) == "ctx 1k/2k"
        assert raw._ld_frame_footer({"card_id": "card_zzz999"}) == "ctx 1k/2k · \U0001f516 zzz999"
        assert seen == {"chat_id": "", "started": None, "status": None}, \
            "三次都没有 chat_id / t0 / status 时，往下传的必须是中性缺省（不猜）"
        raw._ld_frame_footer({"message_id": "om_abcdef123456", "chat_id": "oc_t",
                              "t0": 123.0, "status": "ok"})
        assert seen == {"chat_id": "oc_t", "started": 123.0, "status": "ok"}, seen
        # ⚠️ 基数页脚为空 ⇒ 仍然是 None（短码不许把「页脚=无」这个诊断信号抹掉）
        adapter.LarkDeckMixin._ld_footer = classmethod(lambda cls, **kwargs: None)
        assert raw._ld_frame_footer({"message_id": "om_abcdef123456"}) is None
    finally:
        adapter.LarkDeckMixin._ld_footer = orig
    # 每回合自检行也要带同一串（人眼从卡上抄 6 位，去日志里 grep）
    adapter._log_turn_selfcheck._at = 0.0
    with _LogCapture("larkdeck") as recs:
        adapter._log_turn_selfcheck("oc_trace", "cardkit", 7, 2, trace="abc123")
    line = [r.getMessage() for r in recs if "回合自检" in r.getMessage()]
    assert line and "卡片=abc123" in line[0], f"自检行必须报出卡短码：{line}"


def test_status_reports_uptime_fallback_and_error_code_top_n():
    """第二组 · G2：`/larkdeck status` 报 **uptime / 掉回纯文本次数 / 错误码 top-N**。

    三条的口径各不相同，所以三条都要各自钉住：
      * **uptime** 是**时钟读数**：`reset()` **不许**清它（清了就不是「进程已运行多久」，
        而会永远显示「刚重启」—— 那正好是排障时最容易误判的一个数）；
      * **掉回纯文本**记的是「**本回合我们把卡片车道让给了核心**」⇒ 用户**肉眼能看出**「这次没卡片」
        的那一类；它与 `frame_fail_count`（真的发起过一次写而失败）**口径不同**，两个都留；
      * **错误码**只数**非零**（`0` 是成功、`None` 是没拿到响应），且不同码有**上限**
        —— 坏上游不能把这张表撑到无限大。
    """
    context.reset()
    started = context.status_snapshot().get("started_at")
    assert started, "建盒子时就必须有进程起点（否则 uptime 永远是「无记录」）"
    context.note_plaintext_fallback("帧失败：测试")
    for code in (300309, 300309, 300313, 0, None, ""):
        context.note_response_code(code)
    snap = context.status_snapshot()
    assert snap["fallback_count"] == 1 and "测试" in snap["fallback_reason"], snap
    assert snap["codes"] == {"300309": 2, "300313": 1}, snap["codes"]
    assert snap["code_total"] == 3, snap["code_total"]
    text_report = "\n".join(context.status_lines())
    assert "掉回纯文本：1 次" in text_report, text_report
    assert "300309\u00d72" in text_report and "300313\u00d71" in text_report, text_report
    # 上限：不同的码再多也不会让表无限长大（超出的一律并进 other）。
    # ⚠️ **上限必须用字面量钉**（审计 X18）：原来这里是
    #    `range(context._MAX_CODE_KEYS + 5)` / `<= context._MAX_CODE_KEYS + 1` ——
    #    **拿被测常量自己算上界**，于是任何 ≤24 的值都自洽通过（把 24 改成 2 也全绿，
    #    top-N 静默退化成 top-2）。与 `_CK_WRITE_*` 那套「字面量钉住」是同一条纪律。
    assert context._MAX_CODE_KEYS == 24, "上限是被有意选定的常量，改了必须当场可见"
    context.reset()
    for i in range(24 + 3):
        context.note_response_code(100000 + i)
    codes = context.status_snapshot()["codes"]
    assert len(codes) == 25 and codes["other"] == 3, \
        f"键数上界 = 24 个不同码 + 一个 other 桶，多出来的 3 个并进 other：{codes}"
    # ⚠️ **快照必须深到 `codes`**（审计 X19）：它是账本里**唯一可变**的那一格，
    #    只做 `dict(_STATUS)` 时读者改快照 = 改共享账本。判据必须是**反向**的
    #    （「改一下看不看得见」）—— 正向读一次抓不住它。
    _snap = context.status_snapshot()
    _snap["codes"]["999999"] = 42
    assert "999999" not in context.status_snapshot()["codes"], \
        "快照必须深到 codes —— 读者改快照不许污染共享账本"
    # ⚠️ **`codes` 不许与 `_STATUS_DEFAULTS` 同源**（审计 X20）：`dict(_STATUS_DEFAULTS)` 是
    #    浅拷贝，一旦共享那一格，`reset()` 之外的任何清空都会波及所有副本（清一次等于清全部）。
    #    那行代码本来就是为此写的，但**一个判据都没有** —— 纪律没判据就等于没有。
    #
    #    ⚠️⚠️ **判据必须逼初始化路径跑一次**（第一版就是这样写漏的，变异 `X20` 照旧 🟢）：
    #    `_shared_status()` 只在**盒子还没建**时才走初始化分支，而进程启动时早建好了 ⇒
    #    直接断言「当前那一格的 codes 不是原型」**恒真** —— 撤掉那行初始化代码也照样成立。
    #    这正是「输入构造让错误分支不可达」那一型（与 `G1-22` 同病），所以先把那一格删掉。
    import builtins as _builtins
    _box = getattr(_builtins, "_larkdeck_shared_state")
    assert context._shared_status()["codes"] is not context._STATUS_DEFAULTS["codes"], \
        "账本的 codes 不许与 _STATUS_DEFAULTS 的默认 {} 同源（清一次等于清全部）"
    _saved_status = _box["status"]
    try:
        del _box["status"]                       # 逼 `_shared_status()` 走初始化分支
        _fresh = context._shared_status()
        assert _fresh is not context._STATUS_DEFAULTS, \
            "初始化必须给一份**新容器**，不是把那份模块级原型直接挂上去"
        assert _fresh["codes"] is not context._STATUS_DEFAULTS["codes"], \
            ("初始化时 `codes` 必须换成**新对象** —— `dict(_STATUS_DEFAULTS)` 是浅拷贝，"
             "共享那一格就是「清一次等于清全部」（那行 `box[\"status\"][\"codes\"] = {}` 的整条理由）")
        # 行为面：动这个账本的 codes **不许**波及原型（原型被污染 ⇒ 之后每一份新账本都带着它）
        _fresh["codes"]["123456"] = 1
        assert "123456" not in context._STATUS_DEFAULTS["codes"], \
            "账本与 `_STATUS_DEFAULTS` 同源 ⇒ 记一个码就污染了后续所有副本的原型"
    finally:
        _box["status"] = _saved_status
    # ⚠️ **失败原因必须先单行归一**（审计 X21）：换行会把状态卡撑成多行，行首的 `#`
    #    还会被解释成 markdown 标题。两处口径（本函数 / `note_frame_fail`）都要钉。
    context.reset()
    context.note_plaintext_fallback("帧失败（boom）\n# 我是标题")
    context.note_frame_fail("帧失败（boom）\n# 我是标题")
    for _label in ("掉回纯文本", "最近写卡失败"):
        _rows = [l for l in context.status_lines() if _label in l]
        assert len(_rows) == 1, f"{_label} 那一行应当正好一行：{_rows}"
        # ⚠️ 判据只能是「**这一行里不许有换行**」，不能顺手断言「不许出现 `#`」——
        #    归一之后 `#` 落在**行中间**（`… · 帧失败（boom） # 我是标题）`），那只是个普通字符；
        #    真正的病是它被换行顶到**行首**、于是被解释成 markdown 标题。
        #    （第一版就是多写了半句 `"# " not in …`，当场被自己的用例抓出来。）
        assert "\n" not in _rows[0], \
            f"原因串必须先单行归一，否则换行会把 `#` 顶到行首变成 markdown 语法：{_rows[0]!r}"
        assert "我是标题" in _rows[0], f"归一不许把原因整段丢掉：{_rows[0]!r}"
    # ⚠️ **`_dur` 的「读不到」那一半也要有判据**（审计 Y5）：原来只验了「有值」那半边
    #    （第 4 行含「已运行」），而 `started_at` 建账本时必写 ⇒ 错误分支**恒不可达**。
    #    四类脏值实测过的旧行为（编数字 / 直接抛）见 `_dur` 的 docstring。
    for _dirty in (None, "", "不是数", float("nan"), float("inf"), float("-inf"),
                   0, -1, 1e18, True):
        assert context._dur(_dirty) == i18n.t("status.none"), \
            f"脏值必须写「无记录」，不许编一个数字：_dur({_dirty!r}) = {context._dur(_dirty)!r}"
    _now = time.time()
    assert context._dur(_now - 5) == "5s", context._dur(_now - 5)
    assert context._dur(_now - 90) == "1m", context._dur(_now - 90)
    assert context._dur(_now - (3600 + 13 * 60)) == "1h13m", context._dur(_now - 4380)
    # ⚠️ `reset()` 清运行时计数，但**不清**进程起点
    assert context.status_snapshot().get("started_at") == started, \
        "`reset()` 不许清 started_at（清了 uptime 就永远是「刚重启」）"
    # 接线：帧失败的**唯一收口点**必须同时记这两条（不是各调用点各记一遍）
    raw = _make()
    context.reset()
    assert raw._ld_stream_fail("测试：元素写失败", code=300309) is False
    snap = context.status_snapshot()
    assert snap["fallback_count"] == 1 and snap["codes"].get("300309") == 1, snap
    assert snap["frame_fail_count"] == 1, snap


def test_entity_card_always_gets_a_footer_element_when_the_footer_is_enabled():
    """**建实体那一刻**页脚元素「要不要」必须按**配置**决定，不是按「这一刻有没有数据」。

    ⚠️⚠️ **这一格的历史是一段误诊，值得留着当教材**（2026-09-16 审计实测推翻）：
    2026-09-15 有一条「修复」声称 —— `_ld_footer()` 在进程第一次 API 调用前返回 `None`，
    建实体时把 `None` 传下去 ⇒ 页脚元素不进卡 ⇒ 重启后第一回合**永远没有页脚与短码**。
    **那个缺陷不存在**：`_ld_ck_create` 建卡时用的是函数体里**写死**的
    `footer_text=("" if _cfg("footer") else None)`（本来就是按配置判的），而调用方传进去的
    `footer_text=` **形参从来没被读过**（AST 实测：形参未被使用）⇒ 那条「修复」是一次**空转**，
    而守着它的旧用例恰好是本批次自己点名要消灭的两种无牙形态：
    **只直接调 helper（验函数不验接线）+ 手工照生产那一行再建一次卡（验表达式不验调用点）**。
    ⇒ 现在：① 形参 `footer_text` 与它唯一的生产者 `_ld_seed_footer_text()` 都已删除，
    判据只剩 `_ld_ck_create` 体内那一行（一处真相）；② 这一格**真驱动 `_ld_ck_create`**
    并断言**建出来的那张卡**里有没有 `CARDKIT_FOOTER_ID`（元素表的来源就是这张卡）。
    """
    defaults = dict(adapter._DEFAULTS)
    old_reqs = adapter.LarkDeckMixin._ld_ck_requests
    try:
        # 真环境里 `_ld_ck_requests()` 会 import 真的 `lark_oapi` 来造请求对象（本仓库的
        # venv 里**装得有**）⇒ 换成哑对象，这样 `calls["entity"]` 收到的才是能解析的卡 JSON
        # （与 `test_cardkit_transport_writes_elements_and_falls_open` 同一套做法）。
        adapter.LarkDeckMixin._ld_ck_requests = staticmethod(_fake_ck_requests)
        for enabled, should_have in ((True, True), (False, False)):
            adapter.configure(footer=enabled)
            calls, client = _mk_cardkit_fake()
            raw = _make()
            raw._client = client
            made = _run(raw._ld_ck_create("oc_footer", answer="正文", panel_text="面板",
                                          panel_tools_text=""))
            assert made is not None, f"前提：footer={enabled} 这一格必须真的建出实体卡"
            assert calls["entity"], "前提：建实体的请求必须被记录下来（否则这一格什么都没验）"
            elems = adapter._ck_elems_from_card(json.loads(calls["entity"][-1]))
            assert (cards.CARDKIT_FOOTER_ID in elems) is should_have, (
                f"footer={enabled} 时页脚元素的有无与配置不一致：{sorted(elems)}"
                "（判据是**配置**，不是这一刻有没有数据 —— 元素表在建实体时定死，"
                "漏了它这一回合就永远写不上页脚与短码）")
    finally:
        adapter.LarkDeckMixin._ld_ck_requests = old_reqs
        adapter.configure(**defaults)


def test_strip_core_progress_only_ever_returns_a_prefix_of_the_frame():
    """**凡真的剥了，剥出来的必须是帧文本的「前缀」** —— 这条性质是 R4 卡链的地基。

    ⚠️ 为什么值得**独立一条**用例（2026-09-16 第四轮审计）：
    ① 这段性质原先**只在 `_strip_core_progress` 的 docstring 里被论证过，没有任何判据**，
       而且那段论证的**理由是错的** —— 它写「因为我们只做后缀剥离」，那是**同义反复**
       （`return accumulated` 只有在 `accumulated` 真是前缀时才叫「剥后缀」）。真正的保证
       来自条件 ③+④：`tail = text[len(acc):]` 且 `tail.startswith(SEP)`
       ⇒ `acc == text[:text.index(SEP)]` ⇒ **必然是前缀**。而这一条又依赖一个**外部前提**：
       我们的累积与核心的累积**逐字节同源**（`agent/stream_delivery.py:314` 把同一个 `text`
       既投给钩子又累加；`gateway/stream_consumer.py:243` 的 `_accumulated` 从不含进度行）。
       前提一旦变了，`display` 就可能不再是帧文本的前缀 ⇒ **在切卡接缝上静默吞正文**。
    ② 放在大用例（㉘）里时它会被**先触发的具体断言遮住** —— 我实测过：施加「返回非前缀」
       的实现变异后，红的始终是前面那些具体断言，这条从不触发 ⇒ **它的判别力无法被证明**。
       独立成条之后，同一个变异会让**这一条也变红**（实测 `198/199` → 现在 `198/200`）。

    ⚠️ 自证（本项目对「判据不许恒真」的要求）：下面先断言**这一组输入真的走到了剥离分支**，
    否则「结果是不是前缀」对全都没剥的输入是**恒真**的。
    """
    SEP = "\n\n---\n"
    PROG = "⚙️ 工具行"
    acc_a = "A段正文。"
    acc_c = "A段。" + SEP + "B段（模型自己写的分隔线）。"
    cases = [
        (acc_a, acc_a + SEP + PROG),                          # 基本形状
        (acc_a, acc_a + SEP + PROG + SEP + "尾巴"),            # 尾巴里还有分隔符
        (acc_c, acc_c + SEP + PROG),                          # 累积里**含模型自己写的分隔符**
        (acc_a, acc_a + SEP + "x"),                           # 尾巴极短
        (acc_a, "前缀" + acc_a + SEP + PROG),                  # 累积只是子串（条件③应拦下）
        (acc_a, acc_a + SEP),                                 # 裸分隔符（条件④应拦下）
        (acc_a, acc_a),                                       # 没有尾巴
    ]
    stripped_any = False
    for acc, frame in cases:
        out = adapter._strip_core_progress(frame, acc, True, True, finalize=False)
        if out != frame:
            stripped_any = True
            assert frame.startswith(out), (
                f"剥完的结果必须是帧文本的**前缀**（否则 `ck_offset` 失效、切卡接缝静默吞正文）："
                f"{out!r} 不是 {frame!r} 的前缀")
        else:
            assert out == frame, "没剥就必须**原样返回**（不许改写）"
    assert stripped_any, (
        "构造无效：这一组输入里**至少有一条必须真的走到剥离分支** —— "
        "否则上面的「是不是前缀」对全都没剥的输入恒真，这一格等于没验")


def test_stop_redraw_and_edit_message_keep_the_trace_id():
    """**短码在哪几帧出现过**要有断言（审计 C1）：`/stop` 重绘与 `edit_message` 手上都有
    `message_id`，用基数页脚就把短码漏掉了 —— 而 `/stop` 那一帧恰恰是**最可能被截图**的。

    顺带钉住 D1 的口径边界：那条「**没有活跃流可收尾 ⇒ 按契约交还核心**」的**正常路径**
    **不许**进「掉回纯文本」账本（一个写请求都没发，消息照常发出去）。
    """
    # ① `/stop` 重绘：面板被强制成 stopped，页脚必须带短码。
    #    ⚠️⚠️ **必须走真路径**（审计 C4）：初版是**手工照生产那一行再建一次卡**
    #    （`raw._ld_build_card(..., footer=raw._ld_frame_footer({"message_id": "om_…"}))`），
    #    那验的是「这个表达式会加短码」，**不是**「生产真的用了它」—— 把
    #    `_ld_redraw_one_stopped` 里那一行换回 `_ld_footer()`，四门禁**照样全绿**
    #    （变异 `G2-10` 实测）。所以这里改成真驱动 `/stop`，读**真的发出去的那份载荷**。
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0
    try:
        raw = _make()
        adapter.configure(unified_panel=True)
        panel.reset()
        context.reset()
        # ⚠️ 先喂出**真实**的基数页脚：`_ld_frame_footer` 的纪律是「没有基数页脚就不加短码」
        #    （保住「页脚=无」这个诊断信号）⇒ 不喂数据时它本来就不该有短码，那不是 C1 那个缺陷。
        context.record_api_call(model="test-model",
                               usage={"input_tokens": 4242, "output_tokens": 8})
        context.set_context_override(10000)
        assert raw._ld_footer(), "前提：这一刻必须真的有基数页脚，否则这一格什么都没验"
        _ups = _wire_patch(raw)
        assert _run(raw.send_stream_frame("", finalize=False, chat_id="oc_stop", turn_id="c1"))
        assert _run(raw.send_stream_frame("正文", finalize=False, chat_id="oc_stop",
                                          turn_id="c1"))
        assert _ups, "前提：这一帧必须真的写出去过（否则 /stop 无从重绘）"
        _n_before = len(_ups)
        _run(raw.interrupt_session_activity("sk-stop", "oc_stop"))
        assert len(_ups) == _n_before + 1, "中止后必须**真的**重绘了一次（否则这一格什么都没验）"
        _mid = _ups[-1]["message_id"]
        _stop_blob = _ups[-1]["content"]
        assert f"🔖 {_mid[-6:]}" in _stop_blob, \
            f"`/stop` 重绘那一帧丢了短码（用户最可能截图的就是它）：mid={_mid!r} 尾部={_stop_blob[-300:]}"
        # 同一次重绘的另一半：面板必须真的变成中止色（两个断言各钉一个症状）
        _pn = _find_collapsible(json.loads(_stop_blob))
        assert _pn is not None and _pn["border"]["color"] == "yellow", _pn
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()
    # ①b **收尾整卡替换也要带短码**（审计 Y20）。短码一共三个调用点，原先只钉住两个
    #     （CardKit 元素帧的字面量、上面的 `/stop` 重绘）—— **收尾那一帧没人看**，
    #     而它恰恰是**用户最后看到的那张卡**（收尾之后卡片不再变，除非再 /stop）。
    #     ⇒ 同一型缺陷（「接线没验」）的第三处，与 `G1-4`／`G2-10` 同源。
    defaults_b = dict(adapter._DEFAULTS)
    old_interval_b = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0
    try:
        raw_b = _make()
        adapter.configure(unified_panel=True)
        panel.reset()
        context.reset()
        context.record_api_call(model="test-model",
                               usage={"input_tokens": 4242, "output_tokens": 8})
        context.set_context_override(10000)
        assert raw_b._ld_footer(), "前提：必须有基数页脚，否则短码本来就不该出现"
        _ups_b = _wire_patch(raw_b)
        assert _run(raw_b.send_stream_frame("", finalize=False, chat_id="oc_fin", turn_id="tf"))
        assert _run(raw_b.send_stream_frame("正文一，正文二", finalize=False,
                                            chat_id="oc_fin", turn_id="tf"))
        _n_b = len(_ups_b)
        assert _run(raw_b.send_stream_frame("正文一，正文二", finalize=True,
                                            chat_id="oc_fin", turn_id="tf")) is True, \
            "收尾帧必须成功（否则这一格验的是失败路径）"
        assert len(_ups_b) == _n_b + 1, "收尾必须**真的**整卡替换一次（否则这一格什么都没验）"
        _mid_b = _ups_b[-1]["message_id"]
        assert f"🔖 {_mid_b[-6:]}" in _ups_b[-1]["content"], \
            (f"收尾整卡替换是用户**最后看到**的那张卡，短码不许丢："
             f"mid={_mid_b!r} 尾部={_ups_b[-1]['content'][-300:]}")
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval_b
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults_b)
        adapter._apply_metrics_config()
    # ② D1：正常路径（没有活跃流可收尾）**不计**掉回纯文本
    context.reset()
    _calls, _client = _mk_cardkit_fake()
    raw2 = _make()
    raw2._client = _client
    assert _run(raw2.send_stream_frame("正文", chat_id="oc_none", turn_id="t-none",
                                       finalize=True)) is False, \
        "没有活跃流时 finalize 帧按契约返回 False（交还核心）"
    assert context.status_snapshot()["fallback_count"] == 0, \
        "⚠️ 那条**正常路径**不许进「掉回纯文本」账本（口径见 note_plaintext_fallback）"


def test_help_text_record_count_matches_the_status_card():
    """`/larkdeck help` 说的记录条数必须与状态卡**真的一样多**（审计 E1）。

    病（已修）：卡上早已是六条（uptime / 掉回纯文本 / 错误码），而帮助文案还写「三条心跳记录」
    —— 用户按帮助去数卡上的行，会以为自己看错了。判据是**两面**：文案里的数字与
    `status_lines()` 的真实行数必须一致。
    """
    assert len(context.status_lines()) == 6, "状态卡的行数变了？"
    zh, en = i18n.t("cmd.help", i18n.ZH), i18n.t("cmd.help", i18n.EN)
    assert "六条" in zh and "six records" in en, \
        f"帮助文案的记录条数必须与状态卡一致（当前 {len(context.status_lines())} 行）：{zh[:120]!r}"


def test_every_python_file_compiles():
    """**每一个 .py 都要能编译** —— 包括那些**没有任何门禁导入**的真机探针。

    为什么必须有这条（2026-09-15 实测踩出来的洞）：`tests/probe_render.py` 这类真机探针
    **不被任何门禁 import**（门禁测的是插件本体），所以一个**语法错**能一路穿过五道门禁，
    直到**真机上要用户配合的那一刻**才炸 —— 而那正是最不该失败的时刻（用户的时间是本项目
    唯一真正稀缺的资源）。当时那个错是 `f"{'\u2705' if code == 0 else ...}"`：
    Python 3.11 的 f-string **表达式部分不许含反斜杠** ⇒ SyntaxError，而四门禁全绿。

    判据是「**能编译**」，不是「能 import」—— 后者会执行模块顶层代码
    （探针会去读凭据、连飞书，门禁里绝不能那么干）。
    ⚠️ 用内置 `compile()` 而**不是** `py_compile.compile(..., cfile=os.devnull)`：
    后者在 macOS 上直接抛 `FileExistsError: /dev/null is a non-regular file`（实测），
    而去掉 `cfile` 又会在树里**落一堆 .pyc**（门禁不该有写副作用）。`compile()` 只编译不落盘。
    """
    bad = []
    # ⚠️ **必须排除 VCS / 缓存目录**：这道门禁在**带 `.git` 的真仓库**里跑，而金标快照里没有 `.git`
    #    —— 不排除的话，「快照里绿、移植后突然红」这种最讨厌的形态就会出现（本项目栽过同类的坑：
    #    被测对象与验证对象必须一致，见 `docs/lessons.md` 的二类假绿）。
    _SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache"}
    for path in sorted(_REPO_ROOT.rglob("*.py")):
        if _SKIP_DIRS & set(path.parts):
            continue
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except SyntaxError as exc:
            bad.append(f"{path.relative_to(_REPO_ROOT)}:{exc.lineno}: {exc.msg}")
        except Exception as exc:                       # 读不出来（编码坏了）也算不过
            bad.append(f"{path.relative_to(_REPO_ROOT)}: {type(exc).__name__}: {exc}"[:200])
    assert not bad, ("有 .py 编译不过（门禁不导入它们，所以只能在这里抓 —— "
                     "探针类脚本的语法错否则只会等到真机运行时才暴露）：\n  " + "\n  ".join(bad))


def test_element_limit_is_enforced_recursively():
    """飞书的**元素数硬上限**必须真的挡住 —— 撞上不是「卡片变小」而是整张卡不渲染。

    2026-09-13 真机实测（`probe_render.py --elements`）：递归 198 个元素收下、
    202 个拒收（`code=230099` · `ext=ErrCode: 11310 element exceeds the limit`）。
    所以墙在 200 附近，而**计数必须递归**：折叠面板里每个子元素都算一个，
    只数顶层 ``body.elements`` 会低估几倍。
    """
    # 计数口径：含嵌套
    inner = cards.collapsible({"tag": "plain_text", "content": "t"},
                             [cards.md(f"行 {i}") for i in range(5)])
    card = cards.reply_card("正文", panel=inner, footer="脚注")
    # 正文 1 + 面板 1 + 面板标题 1 + **面板标题的 icon 1** + 5 个子元素 + 脚注 1 = 10
    # （`header.icon` 也是带 tag 的对象 —— 只数「看得见的元素」就会漏掉它）
    assert cards.count_elements(card) == 10, cards.count_elements(card)

    # 面板自己就要收住：步数配置得再大也不能把整卡顶过墙
    panel = cards.unified_panel(tools=[f"✅ step {i}" for i in range(500)],
                               max_steps=500, max_tool_chars=20)
    assert cards.count_elements(panel) < cards.FEISHU_ELEMENT_LIMIT, \
        f"面板自己的元素数没收住：{cards.count_elements(panel)}"

    # 兜底闸门：`unified_panel` 的预算已经拦住了上面那种情况，所以这里用**底层原语**
    # 手工造一个超限的面板，模拟「上游硬塞进来一张注定被拒的卡」。
    fat = cards.collapsible({"tag": "plain_text", "content": "胖面板"},
                            [cards.md(f"行 {i}") for i in range(250)])
    assert cards.count_elements(fat) > cards.FEISHU_ELEMENT_LIMIT, \
        "（构造前提）这个面板本身应当超限，否则这条测试没意义"
    node, tier = cards.fit_reply_card("答案", streaming=True, panel=fat, footer="脚注")
    assert tier != "ok", "超限的面板必须被降载掉"
    assert cards.count_elements(node) <= cards.FEISHU_ELEMENT_LIMIT, \
        f"降载后仍然超限：{cards.count_elements(node)} 个元素"

    # 字节那道墙不受影响（两道德独立判定）
    _small, tier2 = cards.fit_reply_card("答案" * 100, streaming=True, panel=fat,
                                         footer="脚注", budget=100)
    assert tier2 == "over-budget", tier2


def test_stop_marks_the_bound_session_even_when_its_bucket_is_empty():
    """**阻断项回归**：`/stop` 的中止状态不许落到别的会话上。

    2026-09-13 审计实测的原缺陷：`mark_stopped` 复用了「渲染用」的归属判据，而那条判据
    要求「绑定的会话桶有内容或有状态」才认它 —— 于是回合刚被 `on_stream_start` 清空
    （或 30 分钟 TTL 淘汰）时，它会跳到「最近活跃会话」，把 `stopped` 写到**另一个会话**
    上：那个 chat 的下一张卡变成黄色「⛔ 已中止」（它其实正常完成了），而本次的卡片
    背景里显示的是别人的推理。
    """
    panel.reset()
    try:
        panel.bind_chat_session("oc_A", "s_A")
        # s_A 是一个**空桶**（换回合会清空过程数据），另一个会话有内容且是最近活跃
        panel.record_reasoning("s_B", "t_B", "别的会话的推理")
        assert panel.snapshot("oc_A") is None, "（前提）空桶的绑定会话不该渲染面板"

        sid = panel.mark_stopped("oc_A")
        assert sid == "s_A", f"中止状态打到了别的会话上：{sid!r}"
        state_A = panel._STATE["s_A"]
        assert state_A["status"] == panel.STATUS_STOPPED
        assert not panel._STATE["s_B"].get("status"), "别的会话被一起改色了"

        # 没有绑定时才退回「最近活跃」（旧的保底行为不能被弄丢）
        assert panel.mark_stopped("oc_unknown") == "s_B"
    finally:
        panel.reset()


def test_stop_redraw_paints_an_empty_turn_yellow():
    """回合里还没有任何过程数据时 `/stop`，卡片也必须能画上黄边。

    这是上面那条的另一半：状态改对了、但**渲染侧**拿不到面板 ⇒ 「状态改了、卡片没变、
    还不报错」。触发条件在默认配置下很常见 —— `stream_reasoning_deltas` 官方默认是关的，
    所以「模型还在思考、还没调工具」的回合整回合都没有过程数据。
    """
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0
    try:
        panel.reset()
        panel.bind_chat_session("oc_1", "s1")
        raw = _make()
        updates = _wire_patch(raw)
        # 建卡（seed 帧）后**不写任何面板数据** —— 这就是「还没思考出东西」的回合
        assert _run(raw.send_stream_frame("", finalize=False, chat_id="oc_1",
                                          turn_id="t1")) is True
        assert _run(raw.send_stream_frame("半个答案", finalize=False, chat_id="oc_1",
                                          turn_id="t1")) is True
        assert len(updates) == 1

        _run(raw.interrupt_session_activity("sk1", "oc_1"))
        assert len(updates) == 2, "中止后必须重绘"
        panel_node = _find_collapsible(json.loads(updates[1]["content"]))
        assert panel_node is not None, "重绘出来的卡上没有面板 —— 状态色无处安放"
        assert panel_node["border"]["color"] == "yellow", panel_node["border"]
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_stop_redraw_cleans_up_and_reaches_non_native_cards():
    """两个实测过的边界：重绘后要清流状态；非 native 路径也要能重绘。"""
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0
    try:
        # ① 重绘成功后必须清 `_ld_streams`，否则每次 /stop 会把该 chat 的
        #    **全部历史中止卡**重画一遍（串行 patch，且都在 super() 之前 → /stop 变慢）
        panel.reset()
        raw = _make()
        _wire_patch(raw)
        _run(raw.send_stream_frame("", finalize=False, chat_id="oc_1", turn_id="c1"))
        _run(raw.send_stream_frame("a", finalize=False, chat_id="oc_1", turn_id="c1"))
        _run(raw.interrupt_session_activity("sk", "oc_1"))
        assert not [k for k, v in raw._ld_streams.items() if v.get("chat_id") == "oc_1"], \
            "中止重绘后流状态没清 —— 下次 /stop 会把历史卡全部重画一遍"

        # ② 非 native 路径（edit）：没有存活流，但那张卡在 `_ld_state` 里有 message_id
        #    与最后渲染过的正文 → 同样要能原地重绘成中止态
        panel.reset()
        raw2 = _make()
        updates2 = _wire_patch(raw2)
        _run(raw2.send("oc_9", "答案正文"))
        assert raw2._ld_state, "（前提）send 过的卡应当被追踪"
        _run(raw2.interrupt_session_activity("sk9", "oc_9"))
        assert updates2, "非 native 路径的中止完全没变色（静默失效）"
        joined = json.dumps(json.loads(updates2[-1]["content"]), ensure_ascii=False)
        assert "答案正文" in joined, "重绘把正文弄丢了"
        assert "yellow" in joined, "非 native 路径的中止色没画上"
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_stop_forwards_to_core_before_redrawing():
    """中止必须**先转发给内核**再重绘卡片；且父类**只能被调用一次**。

    审计实测的两件事：
      * 原来 super() 排在重绘之后，而重绘里有 await（可能多次串行 patch、`_run_blocking`
        没有超时），一旦这段被取消或挂住，`except Exception` 接不住 `CancelledError`
        （BaseException），内核的「置停止事件 + 停打字」就不会发生；
      * 原来的运行时会兜 `except TypeError`，于是**父类内部**抛 TypeError 时会被跑第二遍
        （重复置停止事件 / 停打字）。M06 变异证明「不再双跑」这条断言此前**结构上不可能失败**
        ——这里让假父类在第一次调用时就抛，才真的验证到。
    父类抛出的异常**照原样往上抛**（与「核心直接调父类」等价，不隐瞒），但此时不重绘 ——
    中止是内核的职责，重绘是装饰，职责失败了就不该假装成功。
    """
    defaults = dict(adapter._DEFAULTS)
    try:
        for parent_raises in (False, True):
            panel.reset()
            raw = _make()
            order: list = []
            calls: list = []

            async def boom(chat_id):
                order.append("redraw")
                raise RuntimeError("重绘失败")

            async def fake_super(self, session_key, chat_id, metadata=None):
                order.append("super")
                calls.append((session_key, chat_id, metadata))
                if parent_raises:
                    raise TypeError("父类内部抛的 TypeError（不是签名不匹配）")

            raw._ld_redraw_stopped = boom
            original = StubAdapter.interrupt_session_activity
            StubAdapter.interrupt_session_activity = fake_super
            try:
                if parent_raises:
                    try:
                        _run(raw.interrupt_session_activity("sk", "oc_1", metadata={"k": 1}))
                    except TypeError:
                        pass  # 与「核心直接调父类」等价，允许上抛
                else:
                    _run(raw.interrupt_session_activity("sk", "oc_1", metadata={"k": 1}))
            finally:
                StubAdapter.interrupt_session_activity = original

            assert len(calls) == 1, f"父类被调用了两次：{calls!r}"
            assert calls[0][:2] == ("sk", "oc_1"), f"内核的中止没被正确转发：{calls!r}"
            assert calls[0][2] == {"k": 1}, f"metadata 被丢了：{calls!r}"
            assert order[0] == "super", f"转发必须排在重绘之前：{order!r}"
            if parent_raises:
                assert "redraw" not in order, "父类抛异常后不该继续假装成功"
            else:
                assert "redraw" in order, "父类正常时重绘必须发生"
            # 两种情况下状态都已经先写好了（写状态是最廉价、不会失败的一步）
            assert order[0] == "super"
    finally:
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_turn_end_ignores_stale_turn_and_note_turn_clears_status():
    """两条实测过的门禁盲区：`record_turn_end` 要认 turn_id；`note_turn` 要清旧状态。"""
    panel.reset()
    try:
        panel.bind_chat_session("oc_1", "s1")   # 真机路径一定带 chat_id（有绑定）
        panel.record_reasoning("s1", "t2", "第二回合的推理")

        # ① 属于**另一个回合**的收尾（迟到 / 无关）必须整条丢弃：既不写状态，
        #    更不能把当前回合的面板清空、把 turn_id 倒回去（加这条测试时实测到的真 bug）
        panel.record_turn_end("s1", "t1", completed=True)
        snap = panel.snapshot("oc_1")
        assert snap is not None and snap["turn_id"] == "t2", "旧回合的收尾把 turn_id 倒回去了"
        assert "第二回合的推理" in snap["reasoning"], "旧回合的收尾清空了当前回合的面板"
        assert snap["status"] is None, "旧回合的收尾改掉了当前回合的颜色"

        # ② 本回合的收尾正常落状态
        panel.record_turn_end("s1", "t2", completed=True)
        assert panel.snapshot("oc_1")["status"] == panel.STATUS_OK

        # ③ note_turn（post_api_request）要在新回合把旧状态清掉 —— 非流式模式下
        #    `on_stream_start` 完全不触发，这是唯一的新回合信号
        panel.note_turn("s1", "t3")
        snap = panel.snapshot("oc_1")
        assert snap is None or snap.get("status") is None, \
            f"新回合没有清掉上一回合的颜色（note_turn 失效）：{snap!r}"

        # ④ 同一回合内重复调用是幂等的，不能把状态清掉
        panel.record_turn_end("s1", "t3", completed=True)
        panel.note_turn("s1", "t3")
        assert panel.snapshot("oc_1")["status"] == panel.STATUS_OK, \
            "同回合的 note_turn 不该清状态"

        # ⑤ 遗留路径（**没有绑定**时退回「最近活跃」）**不认**只有状态的桶：
        #    那条路没有归属信息可依，选中「另一个会话刚结束」的桶就会把别人的颜色
        #    画到这张卡上 —— 宁可这张卡暂时没有颜色（有绑定就一定有色，见 ②）。
        fresh = panel._STATE["s1"]
        assert not fresh.get("rounds") and not fresh.get("tools")  # 前提：只剩状态
        assert panel.snapshot("oc_unknown") is None, \
            "无绑定的回退把「只有状态」的桶当成了本卡的面板（跨会话错色）"
    finally:
        panel.reset()


def _iter_text_nodes(node):
    """递归找出所有文本节点（plain_text / markdown / lark_md）。"""
    if isinstance(node, dict):
        if node.get("tag") in ("plain_text", "markdown", "lark_md"):
            yield node
        for value in node.values():
            yield from _iter_text_nodes(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _iter_text_nodes(item)


def test_every_text_node_carries_a_string_not_a_nested_node():
    """每个文本节点的 ``content`` 必须是**字符串**，``i18n_content`` 的值也是。

    ⚠️ 这条是**真机探针抓到的真 bug 的本地哨兵**（2026-09-13）：``card(title=...)`` 曾把
    ``i18n_text()`` 返回的**节点**直接塞进 ``plain_text.content``，产出
    ``{"content": {"tag": "plain_text", ...}}`` —— 飞书**拒收**整张卡
    （``230099 / ErrCode 200621 parse card json err``）。
    ``legacy_card`` 一直处理正确，但 2.0 的 ``card()`` 没有；本地单测只核「有没有 header」
    完全看不出来，两个真实的 2.0 澄清卡在真机上是**发不出去**的。

    结构检查放在本地，是因为「跑一次真机探针」不能每改一行都做；
    凡是**能被本地规则判定**的卡片合法性，就该在本地钉住。
    """
    cards_under_test = {
        "reply_card(streaming)": cards.reply_card("正文", streaming=True, footer="ctx 1k/2k"),
        "reply_card(final)": cards.reply_card("正文", streaming=False),
        "clarify_card(1.0)": cards.clarify_card("Q?", ["A", "B"], clarify_id="c",
                                                session_key="s"),
        "clarify_resolved_card(1.0)": cards.clarify_resolved_card(
            question="Q?", answer="A", user_name="u"),
        "clarify_card_2(单选)": cards.clarify_card_2("Q?", ["A", "B"], clarify_id="c",
                                                     session_key="s"),
        "clarify_card_2(多选)": cards.clarify_card_2("Q?", ["A", "B"], clarify_id="c",
                                                     session_key="s", multi=True),
        "clarify_resolved_card_2": cards.clarify_resolved_card_2(
            question="Q?", answer="A", user_name="u"),
    }
    problems = []
    for name, card in cards_under_test.items():
        for node in _iter_text_nodes(card):
            content = node.get("content")
            if not isinstance(content, str):
                problems.append(f"{name}: {node.get('tag')}.content 不是字符串而是 "
                                f"{type(content).__name__}（飞书会拒收整张卡）")
            i18n_node = node.get("i18n_content")
            if i18n_node is not None:
                if not isinstance(i18n_node, dict):
                    problems.append(f"{name}: i18n_content 不是 dict")
                else:
                    for lang, value in i18n_node.items():
                        if not isinstance(value, str):
                            problems.append(f"{name}: i18n_content[{lang}] 不是字符串")
    assert not problems, "；".join(problems)


def test_reactions_switch_overrides_only_itself():
    """「处理中」表情开关（aiduPOP 效果 1 的「无输入提示」）。

    做法是在**子类里覆盖判据**，而不是让用户改宿主的环境变量 `FEISHU_REACTIONS` ——
    覆盖自己的平台实现是这个插件的存在方式（不变量 1），而且只影响我们这个实例。
    默认必须与 Hermes 一致（开），关闭是显式选择。
    """
    defaults = dict(adapter._DEFAULTS)

    class _Base:
        def _reactions_enabled(self):
            return True

    try:
        merged = type("M", (adapter.LarkDeckMixin, _Base), {})
        assert merged()._reactions_enabled() is True, "默认必须保持 Hermes 的行为"
        adapter.configure(reactions=False)
        assert merged()._reactions_enabled() is False, "配置成 false 应当关掉"
        adapter.configure(reactions=True)
        assert merged()._reactions_enabled() is True

        # 内置没有这个方法（版本差异）时：我们的显式配置仍然算数，且**绝不抛**
        # （父类的反应代码根本不存在，返回什么都不会被用到）
        class _NoReactions:
            pass

        merged2 = type("M2", (adapter.LarkDeckMixin, _NoReactions), {})
        adapter.configure(reactions=False)
        assert merged2()._reactions_enabled() is False, \
            "显式关掉时，父类有没有这个方法都该是关"
        adapter.configure(reactions=True)
        assert merged2()._reactions_enabled() is True, \
            "父类没有这个方法时要如实说「开着」，而不是静默替它决定"
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_streaming_config_only_on_streaming_frames():
    """客户端打字机只挂在**流式帧**上，且能被配置关掉。

    为什么只挂流式帧：收尾帧是 `streaming_mode: false`，如果也带 `streaming_config`，
    客户端可能把整段答案**再逐字打一遍** —— 那是明确要避免的观感。
    为什么默认开：2026-09-13 真机实测 `message.patch` 往返 ≈0.5s/帧（16 次连打零拒绝），
    也就是**推帧侧最快约 2 帧/秒**，「顺滑」只能靠客户端动画；同类两个项目取值一致
    （15ms / 每步 1 字 / fast），且飞书在 create 与 patch 两条路径上都是 `code=0`。
    """
    stream = cards.reply_card("正文", streaming=True)
    final = cards.reply_card("正文", streaming=False)
    assert stream["config"].get("streaming_config") == {
        "print_frequency_ms": {"default": 15},
        "print_step": {"default": 1},
        "print_strategy": "fast"}, stream["config"].get("streaming_config")
    assert "streaming_config" not in final["config"], "收尾帧不能带打字机配置"

    # 配置 0 / 负数 / 垃圾值 = 不带这个字段（不是「用默认值」）
    for off in (0, -1, None, "abc"):
        node = cards.reply_card("正文", streaming=True, print_frequency_ms=off)
        assert "streaming_config" not in node["config"], f"{off!r} 应该关掉打字机"

    # 自定义间隔要真的生效（不是硬编码 15）
    custom = cards.reply_card("正文", streaming=True, print_frequency_ms=40)
    assert custom["config"]["streaming_config"]["print_frequency_ms"] == {"default": 40}

    # 适配器要把它接上（配置 → 卡片），且 0 时整条链路都不带
    defaults = dict(adapter._DEFAULTS)
    try:
        raw = _make()
        node = raw._ld_build_card("正文", streaming=True, panel=None, footer=None)
        assert node["config"].get("streaming_config"), "适配器没把打字机传下去"
        adapter.configure(streaming_print_ms=0)
        node2 = raw._ld_build_card("正文", streaming=True, panel=None, footer=None)
        assert "streaming_config" not in node2["config"], "配置 0 没有关掉打字机"
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_long_body_still_redraws_on_stop_and_never_degrades_silently():
    """审计 P1 的回归：正文很长时 `/stop` 也必须能重绘；实在存不下要**留痕**。

    原缺陷（本轮修掉）：`_ld_note_text` 用 **20000 字符**做闸门，超了就**静默**把正文清空
    ⇒ `_ld_redraw_stopped` 的非 native 回退分支一个候选都找不到 ⇒ `/stop` 之后卡片
    **一次 patch 都不发**、颜色不变、**零日志**。触发条件是真实可达的：20500 个**英文**
    字符（≈20.5KB，远在 40000 字节预算之内、卡片渲染得好好的）+ 本回合掉过 native。

    ⚠️ 第七路审计的阻断项（就在这条测试身上）：阈值曾被**同一文件里另一句赋值**
    `_MAX_TRACKED_TEXT = _cards.CARD_BYTE_BUDGET` 覆盖成 40000，而这第二条用例的 fixture
    恰好卡在 40000 上 —— 也就是说这条测试当时的绿，**正来自那句覆盖**，修复从未真正生效。
    现在 fixture 贴着**真阈值**写，并额外锁住「阈值 + 卡片固定开销 ≤ 飞书硬上限」这条关系。
    """
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0

    # ⓪ 形状断言：阈值必须贴着飞书实测硬上限，且**只有一个定义处**
    #    （这句就是那条阻断项的守卫：谁再把它改回 CARD_BYTE_BUDGET，这里立刻红）
    overhead = cards.card_bytes(cards.fit_reply_card("x" * 100)) - 100
    assert adapter._MAX_TRACKED_TEXT + overhead <= adapter._FEISHU_CARD_BYTE_LIMIT, (
        f"阈值 {adapter._MAX_TRACKED_TEXT} + 卡片固定开销 {overhead} 超过飞书实测硬上限 "
        f"{adapter._FEISHU_CARD_BYTE_LIMIT} —— 「发得出去就存得下」不再成立")
    assert adapter._MAX_TRACKED_TEXT > cards.CARD_BYTE_BUDGET, (
        "阈值不能退回卡片自己的降载预算：超预算的卡照样发得出去（真机实测 128000 仍 code=0）")

    try:
        # ① 20.5k 字符的英文正文：能上卡 ⇒ 必须保留 ⇒ `/stop` 必须重绘出黄边
        panel.reset()
        raw = _make()
        updates = _wire_patch(raw)
        _run(raw.send("oc_1", "x" * 20500))
        assert raw._ld_state, "（前提）卡应当被追踪"
        _run(raw.interrupt_session_activity("sk", "oc_1"))
        assert updates, "长正文的卡在 /stop 时没有重绘（原来的静默缺陷）"
        _stop_body = json.dumps(json.loads(updates[-1]["content"]), ensure_ascii=False)
        assert "yellow" in _stop_body
        # R3 收窄版：`/stop` 重绘也是**普通卡** ⇒ 同样不许出现实体卡那两块面板元素
        # （审计实测这条反向断言原本只覆盖收尾那一条车道）。
        assert cards.CARDKIT_PANEL_TOOLS_ID not in _stop_body \
            and cards.CARDKIT_PANEL_BODY_ID not in _stop_body, \
            "`/stop` 重绘用的是普通卡，不该带实体卡的面板元素"

        # ①' 阈值之内但远超卡片自己的降载预算（= 那条阻断项的实测区间）：**必须**保留正文
        panel.reset()
        raw1 = _make()
        updates1 = _wire_patch(raw1)
        big = "汉" * (60_000 // 3)                  # 60000 字节：飞书收得下，但 6 倍于降载预算
        _run(raw1.send("oc_big", big))
        assert adapter._MAX_TRACKED_TEXT >= 60_000, "阈值太低，这个区间的卡存不下正文"
        assert raw1._ld_state["om_card_1"]["last_text"] == big, "阈值内的大正文被丢掉了"
        _run(raw1.interrupt_session_activity("sk", "oc_big"))
        assert updates1, "超降载预算但发得出去的卡，/stop 时没有重绘"
        # 而且**载荷里必须有颜色** —— 只数 patch 次数会漏掉另一半缺陷
        # （真机实测过：patch 发了、code=0、载荷里没有状态色 ⇒ 用户还是看不到变色）。
        assert "yellow" in updates1[-1]["content"], \
            "超预算档的 /stop 重绘载荷里没有状态色（降载把承载颜色的面板摘掉了）"

        # ② 超过**真阈值**（连飞书都发不出去）：允许不存，但**必须留一条日志**（不许静默）
        panel.reset()
        adapter._log_note_text_skipped._at = 0.0   # 清限流窗口，否则这条断言会随机依赖前一个用例
        raw2 = _make()
        _wire_patch(raw2)
        huge = "汉" * (adapter._MAX_TRACKED_TEXT // 3 + 500)
        with _LogCapture("larkdeck") as records:
            _run(raw2.send("oc_2", huge))
        text = _log_text(records)
        assert "未为「中止重绘」保留副本" in text, f"存不下时必须留痕，实得：{text!r}"
        # 日志必须**同时**报两个口径（第七路审计：这里曾传字符数，读起来像阈值算错了；
        # 第九路审计：换口径后只打一个数会让「原始 126900 却报 127202」看着像 bug）
        assert "JSON 转义后" in text, f"日志要说清口径，实得：{text!r}"
        assert f"原始 {len(huge.encode('utf-8'))} 字节" in text, (
            f"日志必须报原始字节数，实得：{text!r}")
        assert f"正文 {adapter._card_body_bytes(huge)} 字节" in text, (
            f"日志必须报判据口径（JSON 转义后）的字节数，实得：{text!r}")
        # 超限时只允许两种结果：**完整保留**或**完全不留** —— 绝不许截断。
        # 截断的后果是 `/stop` 把屏上 6 万字的答案重绘成 1000 字（用户可见的数据丢失），
        # 而第八路审计实测：把这里改成 `body[:1000]` 四门禁全绿。
        stored = (raw2._ld_state.get("om_card_1") or {}).get("last_text")
        assert stored in ("", huge), f"存了个半截正文（{len(stored or '')} 字符）—— 不许截断"
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_tracked_text_keeps_a_bounded_number_of_copies():
    """`_MAX_TEXT_ENTRIES` 的**取值**也要有门禁（第八路审计：改成 1 四门禁全绿）。

    语义（`_ld_note_text` 的清理块）：最多留 `_MAX_TEXT_ENTRIES` 份正文，再多就清掉
    `last` 最小的那份。改成 1 之后，多会话里只有**最近那一个** chat 的卡能在 `/stop` 变色 ——
    而这正是「中止后不变色」这个静默缺陷的形态，没有任何别的门禁看得见。
    """
    defaults = dict(adapter._DEFAULTS)
    try:
        raw = _make()
        keep = adapter._MAX_TEXT_ENTRIES
        assert keep >= 8, f"保留份数太少（{keep}）：多会话下只有最近几个 chat 能变色"
        for index in range(keep):
            mid = f"om_keep_{index}"
            raw._ld_state[mid] = {"chat_id": f"oc_{index}", "t0": 0.0,
                                  "last": float(index), "last_text": ""}
            raw._ld_note_text(mid, f"正文{index}")
        for index in range(keep):
            assert raw._ld_state[f"om_keep_{index}"]["last_text"] == f"正文{index}"
        # 再来一份：挤掉 `last` 最小的那份（0 号），其余全部留下
        raw._ld_state["om_new"] = {"chat_id": "oc_new", "t0": 0.0,
                                   "last": float(keep), "last_text": ""}
        raw._ld_note_text("om_new", "新正文")
        survivors = [m for m in raw._ld_state if raw._ld_state[m]["last_text"]]
        assert len(survivors) == keep, f"保留份数不对：{len(survivors)} != {keep}"
        assert "om_keep_0" not in survivors, "该被淘汰的是最久没用的那份"
        assert "om_new" in survivors
    finally:
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_tracked_body_always_fits_the_status_shell_end_to_end():
    """**复合不变量**：被追踪的正文，`/stop` 那一次重绘的载荷里**一定有颜色**，且发得出去。

    由来（第九路审计 F1 + F2，实测可复现）：
      * 追踪判据原先用**原始 utf-8 字节**，而「装不装得下状态小面板」的守卫用
        **JSON 序列化后的整卡字节**；`\n` `"` `\\` `\t` 在 JSON 里会转义成 2 字节，
        于是存在一条缝隙：正文被留下（⇒ `/stop` 必发 patch），但那张卡装不下小面板
        ⇒ patch 发出去、`code=0`、**载荷里没有任何颜色**。实测窗口：正文 126900 字节
        + 300 个换行 ⇒ 载荷 127750 字节（会被飞书收下）、含 yellow **False**。
      * 而那两条各看一半的老断言（「阈值 + 开销 ≤ 硬上限」与「壳 ≤ 余量」）**拦不住它**：
        审计把壳从 543 加到 1015 字节（仍 ≤ 1024）就重新撕开一条 303 字节宽的窗口，
        四条门禁**全绿**。
    所以这里用**真实形状**（中文 + 换行）跑端到端：只要正文被留下，就必须画得上色。
    """
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0
    limit = adapter._MAX_TRACKED_TEXT
    try:
        # ⓪ 第**十**路审计的两条边（近似阈值只在两个方向各自安全，所以判据要两级）：
        #   * A1-a **我引入过的窄回归**：判「存不下」而那卡其实画得上色 ⇒ 正文白丢、
        #     `/stop` 一次 patch 都不发。反斜杠 / `\t` 形状最明显（JSON 转义把它们变成
        #     2 字节，正文的 JSON 口径远大于原始口径）。窗口 50~284 字节。
        #   * A1-b 反向：判「存得下」而那卡其实装不下状态小面板 ⇒ patch 发了、`code=0`、
        #     **载荷里没有颜色**（纯 CJK 形状下有 67 字节的带）。
        # 判据现在是「近似阈值 → 再问一次真判据」，所以这里**在边界两侧**各断言一次，
        # 而且形状覆盖「转义密集」与「纯 CJK」两类。
        def _boundary(unit: str) -> "tuple[str, str]":
            """粗扫 + 二分出「真判据说画得上色」的最大正文，以及再加一个字的那一份。

            粗扫步长先取 64，再在最后 64 个字里二分 —— 这条测试会构造十几张 ~127KB 的卡，
            原来的「从 1 开始倍增再二分」在满负载下太贵（实测会让整个变异矩阵跑崩一次）。
            """
            lo, hi = 1, 1
            while adapter._stop_redraw_would_paint(unit * hi) and hi < 200000:
                lo, hi = hi, hi * 2
            while lo + 1 < hi:
                mid = (lo + hi) // 2
                if adapter._stop_redraw_would_paint(unit * mid):
                    lo = mid
                else:
                    hi = mid
            return unit * lo, unit * hi

        for unit, label in (("汉", "纯 CJK"), ("\\", "反斜杠")):
            good, bad = _boundary(unit)
            assert adapter._stop_redraw_would_paint(good), (label, len(good))
            assert not adapter._stop_redraw_would_paint(bad), (label, len(bad))
            print(f"   边界（{label}）：画得上色的最大正文 = {len(good)} 字"
                  f"（JSON {adapter._card_body_bytes(good)} 字节）")

            panel.reset()
            raw_reg = _make()
            updates_reg = _wire_patch(raw_reg)
            _run(raw_reg.send("oc_reg", good))
            kept_reg = (raw_reg._ld_state.get("om_card_1") or {}).get("last_text") or ""
            assert kept_reg == good, (
                f"{label}：真判据说画得上色的正文（{len(good)} 字）被丢了 —— "
                "这正是白丢正文的那条回归（A1-a）")
            _run(raw_reg.interrupt_session_activity("sk", "oc_reg"))
            assert updates_reg, f"{label}：被保留的正文必须能重绘"
            payload_reg = updates_reg[-1]["content"]
            assert "yellow" in payload_reg, (
                f"{label}：/stop 载荷里没有颜色 —— 留下了一个画不上色的正文（A1-b 的那一侧）")
            assert len(payload_reg.encode("utf-8")) <= cards.FEISHU_CARD_BYTE_LIMIT

            # 反方向：真判据说画不上色的那一份必须**不留**，而且留一条告警（绝不静默）
            panel.reset()
            raw_bad = _make()
            updates_bad = _wire_patch(raw_bad)
            adapter._log_note_text_skipped._at = 0.0
            with _LogCapture("larkdeck") as records:
                _run(raw_bad.send("oc_bad", bad))
            assert (raw_bad._ld_state.get("om_card_1") or {}).get("last_text") == "", (
                f"{label}：真判据说画不上色的正文（{len(bad)} 字）被留下了 —— "
                "那条 patch 会发出去但载荷没有颜色，用户看不到任何变化")
            assert "未为「中止重绘」保留副本" in _log_text(records), _log_text(records)

        for per_line in (40, 80):
            line = "汉" * per_line + "\n"
            # 顶到**真正的边界**：JSON 引号只算一次，所以「unit 整除」只能当保守起点
            count = max(1, limit // adapter._card_body_bytes(line))
            while adapter._card_body_bytes(line * (count + 1)) <= limit:
                count += 1
            inside = line * count
            assert adapter._card_body_bytes(inside) <= limit, (
                adapter._card_body_bytes(inside), limit)
            panel.reset()
            raw = _make()
            updates = _wire_patch(raw)
            _run(raw.send("oc_esc", inside))
            kept = (raw._ld_state.get("om_card_1") or {}).get("last_text") or ""
            assert kept == inside, (
                f"判据口径 {adapter._card_body_bytes(inside)} ≤ 阈值 {limit} 的正文必须被保留"
                f"（原始 {len(inside.encode('utf-8'))} 字节）—— 否则 /stop 不会重绘")
            _run(raw.interrupt_session_activity("sk", "oc_esc"))
            assert updates, "被追踪的正文必须能重绘出中止态"
            payload = updates[-1]["content"]
            size = len(payload.encode("utf-8"))
            assert size <= cards.FEISHU_CARD_BYTE_LIMIT, (
                f"重绘载荷 {size} 字节超过飞书实测硬上限 {cards.FEISHU_CARD_BYTE_LIMIT}"
                " —— 那一次 patch 会被拒，用户什么都看不到")
            assert "yellow" in payload, (
                "被追踪的正文 /stop 后**载荷里没有颜色**（追踪判据与守卫口径不一致）"
                f"：判据口径 {adapter._card_body_bytes(inside)} 字节、载荷 {size} 字节")

            # 反方向：超出判据口径的正文必须**不被保留**，而且留一条告警（绝不静默）
            outer = line * (count + 1)
            assert adapter._card_body_bytes(outer) > limit
            raw2 = _make()
            _wire_patch(raw2)
            adapter._log_note_text_skipped._at = 0.0
            with _LogCapture("larkdeck") as records:
                _run(raw2.send("oc_esc2", outer))
            assert (raw2._ld_state.get("om_card_1") or {}).get("last_text") == "", \
                "超出判据口径的正文不许再留下（留下就落进「无颜色」窗口）"
            assert "未为「中止重绘」保留副本" in _log_text(records), _log_text(records)
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_stop_redraws_the_most_recently_used_card_and_keeps_one_copy_per_chat():
    """一个 chat 里有多张追踪卡时的两条性质（别把两条混成一条）。

    审计的 M11 变异（删掉 `_ld_track` 里「清同 chat 其它卡的 `last_text`」那行）四条门禁
    全绿。我第一版回归测试写错了断言 —— 它假设「一定挑最新**创建**的那张」，而代码实际是
    「挑最近**用过**的那张」（`_ld_known` 每次编辑都会刷新 `last`），所以那条变异照样绿。
    真实的两条性质是（第一条由 `_ld_track` 保证、第二条由 `_ld_redraw_stopped` 保证）：
      * **内存**：**新建卡**时把同 chat 其它卡的正文本清掉（所以「发了两张卡」之后只剩一张有正文）；
      * **选择**：重绘打给最近**用过**的那张卡（`last` 最新，编辑会刷新它），
        而不是「最新创建」的那张 —— 在屏上被更新的那张才是该重绘的。
    """
    defaults = dict(adapter._DEFAULTS)
    try:
        panel.reset()
        raw = _make()
        raw._ld_build_patch_request = lambda *, message_id, content: {
            "message_id": message_id, "content": content}
        results = iter([{"code": 0, "data": {"message_id": "om_old"}},
                        {"code": 0, "data": {"message_id": "om_new"}}])

        async def _send(*, chat_id, msg_type, payload, reply_to, metadata):
            return next(results)

        seen: list = []
        raw._feishu_send_with_retry = _send
        raw._client.im.v1.message.patch = lambda request: (
            seen.append(request["message_id"]) or {"code": 0, "data": {"message_id": "om_x"}})

        _run(raw.send("oc_1", "第一张卡"))
        _run(raw.send("oc_1", "第二张卡"))
        assert set(raw._ld_state) == {"om_old", "om_new"}, raw._ld_state

        # ① 内存：每个 chat 只留一张卡的正文本
        kept = [mid for mid, value in raw._ld_state.items()
                if value.get("chat_id") == "oc_1" and value.get("last_text")]
        assert kept == ["om_new"], f"每个 chat 只应保留最近一张卡的正文，实得 {kept!r}"

        # ② 行为：重绘打给**唯一还留着正文**的那张（也就是最近一次真正渲染过正文的卡）
        _run(raw.interrupt_session_activity("sk", "oc_1"))
        assert seen == ["om_new"], f"重绘应当打给留着正文的那张卡：{seen!r}"

        # ③ 选择：旧卡被重新编辑后，它就成了「最近渲染过正文的那张」——重绘要跟着它走
        #    （选择用的是 `last`，不是创建时刻；`_ld_known`/编辑都会刷新 `last`）
        # 注意：`edit_message` 自己也会 patch（那是它在更新卡片），所以清空要放在它**之后**
        _run(raw.edit_message("oc_1", "om_old", "旧卡被更新了"))
        seen.clear()
        _run(raw.interrupt_session_activity("sk", "oc_1"))
        assert seen == ["om_old"], f"重绘应当跟着「最近用过的那张卡」走：{seen!r}"
    finally:
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_stream_leak_threshold_stays_hour_scale():
    """`_STREAM_LEAK_SECONDS` 必须是**小时级** —— 这个常量本身就是那条纪律的证据。

    审计的 M29：把它降到 1 秒，四条门禁全绿（旧测试用常量自身构造数据，对取值不敏感）。
    而这个常量的意义恰恰是「只回收真正泄漏的流，绝不按最近活动淘汰」——
    一个跑十分钟工具的回合期间，`last_at` 根本不推进，看起来「很陈旧」但正活跃；
    阈值太小就会踢掉活跃流，下一帧查不到状态就**另发一张新卡**（重复卡 + 老卡停在流式态）。
    """
    assert adapter._STREAM_LEAK_SECONDS >= 600, \
        f"阈值太小会把活跃回合当成泄漏：{adapter._STREAM_LEAK_SECONDS}"


def _parse_config_schema(yaml_text: str) -> "Dict[str, Any]":
    """把 `plugin.yaml` 的 `config_schema` 段解析成 `{键: default}`（零依赖）。

    ⚠️ 这一段换过一版，因为第七路审计实测出旧版有**三条静默漏判**：
      (a) 键名正则 `^  [a-z_]+:$` 看不见含数字/连字符/大写的键 —— 往 yaml 里塞一个
          代码从不读的 `brand-new:` 键，四门禁**全绿**；
      (b) 不用块作用域、拿「上一个匹配到的键」当隐式状态 ⇒ 一个匹配不上的键如果排在
          某个匹配键**之后**，它的 `default:` 会被算到**上一个键**头上（值凑巧相等就静默通过）；
      (c) 根本不读 `type:` —— 把 `streaming_print_ms` 改成 `type: boolean`（配置界面会
          渲染成开关，`true` 经 `_cfg_int` 变 1ms）也全绿。
    新版按**缩进块**解析（键 = 2 空格、属性 = 4 空格），键名不设字符限制，认不出的行
    **直接失败**（宁可红，也不静默漏判），并把 `type:` 与代码默认值的类型一起核对。
    """
    import re
    import re as _re2

    lines = yaml_text.split("config_schema:", 1)[1].splitlines()
    declared: Dict[str, Any] = {}
    types: Dict[str, str] = {}
    current = None
    props: "set" = set()

    def _strip_comment(raw: str) -> str:
        """剥掉**引号外**的行内注释 —— ``default: 15  # 毫秒`` 是合法 YAML。

        第八路审计实测：旧版把它当成字符串 ``'15  # 毫秒'``，于是报「默认值不一致」
        （**误诊**：真正的问题是解析器不认识行内注释）。
        """
        out: "list[str]" = []
        quote = None
        for index, ch in enumerate(raw):
            if quote:
                out.append(ch)
                if ch == quote:
                    quote = None
                continue
            if ch in "\"'":
                quote = ch
                out.append(ch)
                continue
            if ch == "#" and (index == 0 or raw[index - 1] in " \t"):
                break
            out.append(ch)
        return "".join(out).rstrip()

    def _scalar(raw: str) -> Any:
        raw = _strip_comment(raw).strip()
        if raw in ("true", "false"):
            return raw == "true"
        if raw[:1] in ("\"", "'") and raw[-1:] == raw[:1] and len(raw) >= 2:
            return raw[1:-1]
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            return float(raw)
        except ValueError:
            return raw

    def _unsupported(line: str) -> AssertionError:
        return AssertionError(
            f"plugin.yaml config_schema 里有本解析器**读不了**的形状：{line!r}\n"
            "  它故意响亮失败（宁可红也不静默漏判）。两条路：把这段写成简单形状，"
            "或把本测试改成 `yaml.safe_load`（测试脚本可以依赖 Hermes venv 的 PyYAML）。")

    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            # 回到顶格 = 段结束 —— 但**只有真的顶格键**才算结束。
            # 第八路审计实测：段尾插一行 tab 缩进的垃圾会被旧版静默 `break` 掉
            # （116/116 全绿），于是「认不出的行直接失败」这条保证对这类行不成立。
            if re.match(r"^[A-Za-z_][A-Za-z0-9_.-]*:", line):
                break
            raise _unsupported(line)
        if re.match(r"^ {4,}- ", line):        # 列表项（enum / examples）
            raise _unsupported(line)
        m = re.match(r"^  ([^\s:][^:]*):$", line)
        if m:
            current = m.group(1).strip()
            assert current not in declared, f"plugin.yaml: 配置键 {current!r} 重复声明"
            declared[current] = None
            props = set()
            continue
        m = re.match(r"^    ([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$", line)
        if m and current is not None:
            prop, value = m.group(1), m.group(2)
            if _strip_comment(value).strip() in (">", "|", ">-", "|-", ">+", "|+"):
                raise _unsupported(line)      # 折叠 / 字面标量
            assert prop not in props, f"plugin.yaml: {current}.{prop} 重复"
            props.add(prop)
            if prop == "default":
                declared[current] = _scalar(value)
            elif prop == "type":
                types[current] = _strip_comment(value).strip()
            continue
        raise _unsupported(line)

    return declared, types


def test_config_schema_parser_fails_loudly_on_shapes_it_cannot_read():
    """解析器读不了的形状必须**响亮失败**，而不是静默漏判（第八路审计：这条保证此前零覆盖）。

    实测过两条旧行为：① 段尾插一行 tab 缩进的垃圾 → 被静默 `break` 掉，116/116 全绿；
    ② 把解析器里那个 `raise AssertionError` 退回 `continue` → 四门禁全绿。
    也就是说「宁可红也不静默漏判」这句话本身没人守。这条测试守它。
    """
    good = "config_schema:\n  a_1-x:\n    type: boolean\n    default: true\n"
    declared, types = _parse_config_schema(good)
    assert declared == {"a_1-x": True} and types == {"a_1-x": "boolean"}, (declared, types)
    # 行内注释是合法 YAML，必须被剥掉而不是当成字符串（旧版会误诊成「默认值不一致」）
    declared2, _ = _parse_config_schema(
        "config_schema:\n  a:\n    default: 15  # 毫秒\n    type: integer\n")
    assert declared2 == {"a": 15}, declared2

    broken_cases = {
        "段尾 tab 缩进垃圾": good + "\tgarbage: 1\n",
        "键内 5 空格缩进": "config_schema:\n  a:\n     default: 1\n",
        "折叠标量": "config_schema:\n  a:\n    default: >\n      x\n",
        "列表项（enum）": "config_schema:\n  a:\n    enum:\n      - 1\n",
        "键值同行": "config_schema:\n  a: true\n",
        "属性行没有键名": "config_schema:\n  a:\n    : 1\n",
        "顶格垃圾": good + "plain garbage\n",
    }
    for label, text in broken_cases.items():
        try:
            _parse_config_schema(text)
        except AssertionError as exc:
            assert "config_schema" in str(exc), (label, exc)
        else:
            raise AssertionError(f"{label} 没有被响亮拒绝 —— 解析器静默放过了一种读不了的形状")


def test_config_schema_matches_defaults_exactly():
    """`plugin.yaml` 的 `config_schema` 必须与 `_DEFAULTS` **逐键、逐默认值、逐类型**一致。

    这是「三处同步」里**唯一能机械核对**的一处（README 那份是文档，靠人读）。
    2026-09-13 审计实测：把 yaml 里 `streaming_print_ms` 的默认值改成 9999、`reactions`
    改成 false、甚至**整个键删掉**，四个门禁**全部照绿** —— 因为门禁只读过桥接
    （`check_override.py` 验的是 `ctx.get_config` 能不能生效），从来没读过 yaml 与代码是否一致。

    第七路审计又实测出旧解析器的三条漏判（见 `_parse_config_schema` 的 docstring），
    全部补上；顺带**不再**用 `str(got) == str(expected)` 这种宽松比较
    （它会把 `"15"`、`15.0`、`1`（对 `true`）都放过去）。
    """
    from pathlib import Path

    yaml_text = Path(__file__).resolve().parent.parent.joinpath(
        "plugin.yaml").read_text(encoding="utf-8")
    declared, types = _parse_config_schema(yaml_text)

    # `type:` **缺失**必须被看见（旧版 `if declared_type is None: continue` 是静默空洞 ——
    # 第八路审计实测：删掉 `type: integer` 四门禁全绿）。2026-09-13 起**每个键都必须声明
    # type**（`model_aliases` 当时是唯一的例外，已补上），所以这里是个**无例外的等式**：
    # 漏一个就红，不需要维护什么允许名单。
    without_type = sorted(set(declared) - set(types))
    assert without_type == [], (
        f"这些键没有声明 type：{without_type} —— 每个键都必须声明（配置界面按它渲染控件，"
        "`integer` 写成 `boolean` 会让输入框变成开关）")

    assert set(declared) == set(adapter._DEFAULTS), (
        f"plugin.yaml 与 _DEFAULTS 的键不一致："
        f"只在一处有 {sorted(set(declared) ^ set(adapter._DEFAULTS))}")
    # README 的样例配置块也列了全部键 —— 「三处同步」里的第三处，这里一并机械核对键集
    readme_path = Path(__file__).resolve().parent.parent / "README.md"
    if readme_path.is_file():
        block = readme_path.read_text(encoding="utf-8").split("settings:", 1)[-1].split("```", 1)[0]
        import re as _readme_re
        readme_keys = set(_readme_re.findall(r"^\s{8}([a-z_]+):", block, _readme_re.M))
        assert readme_keys == set(adapter._DEFAULTS), (
            f"README 的配置样例与 _DEFAULTS 键集不一致："
            f"README 缺 {sorted(set(adapter._DEFAULTS) - readme_keys)}、"
            f"README 多 {sorted(readme_keys - set(adapter._DEFAULTS))}")

    for key, expected in adapter._DEFAULTS.items():
        got = declared[key]
        assert got == expected and type(got) is type(expected), (
            f"{key} 的默认值不一致：yaml={got!r}（{type(got).__name__}）"
            f"代码={expected!r}（{type(expected).__name__}）")
        # `type:` 声明必须与代码默认值的 Python 类型对得上（配置界面按它渲染控件）
        declared_type = types.get(key)
        if declared_type is None:
            continue
        want = {bool: "boolean", int: "integer", str: "string",
                float: "number"}.get(type(expected), "?")
        assert declared_type == want, (
            f"{key} 的 type 与代码默认值不符：yaml={declared_type!r} "
            f"代码默认值 {expected!r} 应为 {want!r}")


def test_invariant_2_fallbacks_survive_exceptions_not_just_failures():
    """不变量 2 的**异常**分支：卡片层抛异常时也必须回落官方实现。

    审计实测：把 `send()` 里那个 `except` 整条删掉改成 `raise`、`edit_message` 同理，
    **四个门禁全绿** —— 现有测试只覆盖了「卡片返回失败」这条分支，没覆盖「抛异常」。
    而 AGENTS.md 里最重的一条性质就是它：宁可退回纯文本，也绝不因为卡片报错而丢消息。
    """
    defaults = dict(adapter._DEFAULTS)
    try:
        # ① send：面板渲染抛异常 → 必须回落 super()，且不把异常抛给核心
        raw = _make()
        raw._ld_build_card = classmethod(lambda cls, *a, **k: (_ for _ in ()).throw(
            RuntimeError("卡片层炸了")))
        result = _run(raw.send("oc_1", "你好"))     # 不许抛
        assert result.message_id == "om_text_1", result
        assert any(c[0] == "SUPER.send" for c in raw.calls), raw.calls

        # ② edit_message：卡片更新抛异常 → 必须回落 super()
        raw2 = _make()
        raw2._client.im.v1.message.patch = lambda request: (_ for _ in ()).throw(
            RuntimeError("patch 炸了"))
        result2 = _run(raw2.edit_message("oc_1", "om_card_1", "答案", finalize=True))
        assert ("SUPER.edit", "答案", True) in raw2.calls, raw2.calls
        assert result2.message_id == "om_card_1"
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_card_body_bytes_measure_is_the_json_escaping_one():
    """判据口径必须是 **JSON 转义后**的字节数 —— 用原始 utf-8 会静默砍掉一半容量。

    第十路审计实测：把 `_card_body_bytes` 里的 `ensure_ascii=False` 去掉，四门禁**全绿**；
    但那样每个汉字算 6 字节（`\u6c49`）而不是 3 字节 ⇒ 能被追踪的 CJK 正文上限从
    42324 字掉到 **21162 字**（窗口 4018 字节），全是「/stop 该有色而无色」那一类。
    这里把口径直接钉成数字：一个汉字 = 两个引号 + 3 字节 = 5。
    """
    assert adapter._card_body_bytes("汉") == 5, adapter._card_body_bytes("汉")
    assert adapter._card_body_bytes("a") == 3
    assert adapter._card_body_bytes("\n") == 4          # 换行转义成 \n（2 字节）+ 引号
    assert adapter._card_body_bytes("\\") == 4          # 反斜杠转义成 \\（2 字节）+ 引号
    assert adapter._card_body_bytes("汉" * 100) == 302


def test_cross_module_constants_are_derived_not_copied():
    """跨模块常量必须是**算出来的**，不能抄字面量（第九路审计 F9 的防漂移闸门）。

    实测过三条四门禁全绿的变异：`adapter` 里把 `_cards.FEISHU_CARD_BYTE_LIMIT` 抄成
    字面量 `128000`、`_print_frequency_ms` 里把上限抄成 `2000`、`_log_probe_report` 的
    缺键清单退回手写元组。今天数值相同 ⇒ **无可观测后果**，但下一次改一处忘一处就是
    真缺陷（本项目已经栽过一次：`_MAX_TRACKED_TEXT` 被同文件旧赋值覆盖成 40000）。
    机械门禁只能查「源码里引用了那个单一事实来源」，查不了「以后会不会漂」——
    这条就是那个最低成本的防漂移闸门。
    """
    import inspect

    adapter_src = inspect.getsource(adapter)
    cards_src = inspect.getsource(cards)
    for needle, why in (
            ("_FEISHU_CARD_BYTE_LIMIT = _cards.FEISHU_CARD_BYTE_LIMIT",
             "硬上限必须引用 cards.FEISHU_CARD_BYTE_LIMIT，不能抄字面量"),
            ("high = _cards.PRINT_FREQUENCY_MAX_MS",
             "打字机上限必须引用 cards.PRINT_FREQUENCY_MAX_MS，不能抄字面量"),
            ("_compat.PROBE_REPORT_KEYS",
             "缺键清单必须从 compat.PROBE_REPORT_KEYS 派生"),
            ("_MAX_TRACKED_TEXT = _FEISHU_CARD_BYTE_LIMIT - _CARD_BYTES_OVERHEAD",
             "阈值必须是算出来的")):
        assert needle in adapter_src, why
    assert "FEISHU_CARD_BYTE_LIMIT = 128000" in cards_src, \
        "硬上限的**唯一**字面量必须留在 cards.py（真机实测的出处就在它上面那段注释里）"
    # ⚠️ 只查**赋值形态**，不查散文：注释/docstring 里引用实测数字是对的（那是出处），
    # 直接给常量赋数字、或手写一份字符串列表，才是会漂的那种改法。
    import re as _re
    for pattern, why in (
            (r"_FEISHU_CARD_BYTE_LIMIT\s*=\s*\d", "硬上限不许直接赋数字（要引用 cards 的常量）"),
            (r"\bhigh\s*=\s*\d", "打字机上限不许直接赋数字（要引用 cards.PRINT_FREQUENCY_MAX_MS）"),
            (r"_MAX_TRACKED_TEXT\s*=\s*\d", "追踪阈值不许直接赋数字（要由硬上限减去余量算出来）"),
            (r"absent\s*=\s*\[\"", "缺键/契约清单不许手写字符串列表（要从 compat 派生）")):
        assert not _re.search(pattern, adapter_src), f"{why}（第九路审计 F9）"

    # ⚠️ 再补一条**全包**扫描（第十路审计指出原版只扫 adapter/cards 两个模块、且是纯字符串
    #   搜索 ⇒ 别的模块抄多少份数字都看不见；同时对 `getattr(...)` 这类等价重构会假红）。
    #   新判据只认「**赋值形态**的数字」：注释/docstring 里引用实测数字是允许的（那是出处），
    #   其它模块里出现 128000/126976 的赋值就是漂移。
    #   ⚠️ 判据是「**全包只能有一处**」，不是「别的文件里没有」—— 后者看不见
    #   `cards.py` 自己内部再复制一份这种写法（第十路审计实测的假绿之一）。
    core_dir = _pathlib.Path(adapter.__file__).resolve().parent
    hits = []
    for path in sorted(core_dir.glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue                      # 注释里引用实测数字是允许的（那是出处）
            if _re.search(r"=\s*(128000|126976)\b", stripped):
                hits.append(f"{path.name}:{lineno} {stripped}")
    assert len(hits) == 1 and hits[0].startswith("cards.py:"), (
        "卡片字节的硬数字（128000 / 126976）在全 `core/` 里只允许**定义一次**，"
        f"且必须在 cards.py：{hits}")


def test_card_byte_constants_are_pinned_to_the_measured_envelope():
    """卡片字节那三个常量**自身**必须有门禁，不能只锁它们之间的关系。

    第八路审计实测（43 条变异矩阵）：新加的形状断言只盯着「阈值 + 开销 ≤ 硬上限」这条
    **关系**，于是把三个输入分别改坏——`_FEISHU_CARD_BYTE_LIMIT` → 999999、
    `_CARD_BYTES_OVERHEAD` → 50000（追踪窗口缩到 78000，「/stop 无重绘」区间回归）、
    `_MAX_TRACKED_TEXT` 手写成 127000——**四门禁全绿**。而 `128000` 这个数字来自真机阶梯
    （`probe_render.py --bytes`：**128000 仍 code=0**、**160000 被拒**：
    `230025 The length of the message content reaches its limit`），是整个
    「发得出去就存得下」口径的地基。机械门禁证明不了那个数字（只有真机探针能），
    但至少要让改它的人**必须面对那份实测**，而不是随手改大。
    """
    assert adapter._FEISHU_CARD_BYTE_LIMIT == cards.FEISHU_CARD_BYTE_LIMIT, "两处常量必须同源"
    measured_accepted, measured_rejected = 128000, 160000
    assert measured_accepted <= cards.FEISHU_CARD_BYTE_LIMIT < measured_rejected, (
        f"硬上限常量 {cards.FEISHU_CARD_BYTE_LIMIT} 落在真机实测包络线 "
        f"[{measured_accepted}, {measured_rejected}) 之外 —— 要改它先重跑 "
        f"`probe_render.py --bytes` 并把新的实测值一起写进注释与断言")
    # 阈值必须是**算出来的**，不是手写数字（手写就等于把口径钉死在一处，改一处忘另一处）
    assert adapter._MAX_TRACKED_TEXT == (cards.FEISHU_CARD_BYTE_LIMIT
                                         - adapter._CARD_BYTES_OVERHEAD)
    # 余量必须是「小余量」：太大等于把「/stop 无重绘」的区间又请回来（审计实测 50000 就全绿）
    assert adapter._CARD_BYTES_OVERHEAD <= 8192, adapter._CARD_BYTES_OVERHEAD
    assert adapter._MAX_TRACKED_TEXT >= 100_000, adapter._MAX_TRACKED_TEXT
    # 而且必须**放得下**状态小面板（它是超预算档唯一还在卡上的装饰）
    shell_bytes = cards.card_bytes(cards.status_shell(
        cards.unified_panel(status="stopped")))
    assert 0 < shell_bytes <= adapter._CARD_BYTES_OVERHEAD, (
        f"状态小面板 {shell_bytes} 字节 > 余量 {adapter._CARD_BYTES_OVERHEAD} —— "
        "那「发得出去的卡都存得下正文」就不再成立")


def test_status_color_survives_the_byte_budget_degradation():
    """正文超过字节预算时，**状态色仍然必须留在卡上**（真机实测出来的缺陷）。

    由来（2026-09-13，新加的真机探针 `probe_render.py --stop-redraw` 抓到的，第七路审计
    那条阻断项的**另一半**）：状态色的载体只有 ``collapsible_panel.border.color``，而正文
    超过 ``CARD_BYTE_BUDGET`` 时降载阶梯会把面板**整块**摘掉 ⇒ ``/stop`` 的确发出了
    ``message.patch``（``code=0``），但载荷里**根本没有颜色** ⇒ 用户看到的还是「没变色」。
    修法是 :func:`cards.status_shell`：这一档只留「带颜色的框 + 一行状态文字」（约 545 字节），
    且**只受飞书实测硬上限约束**（不受软预算约束，否则等于没修）。

    这条断言的价值在于它盯的是**载荷里的颜色**，不是「有没有发 patch」——
    后者写在另一条测试里，两者合起来才等于「用户会看到变色」。
    """
    panel_of = lambda status: cards.unified_panel(status=status, reasoning="想一下")  # noqa: E731
    for status in ("ok", "error", "stopped"):
        color = cards.border_for_status(status)
        body = "汉" * 20000                     # 60000 字节：远超 40000 软预算
        node, tier = cards.fit_reply_card(body, panel=panel_of(status))
        blob = json.dumps(node, ensure_ascii=False)
        assert tier == "over-budget", tier
        assert body in blob, "正文被截断了 —— 绝不允许"
        assert color in blob, (
            f"{status} 色的卡在超预算档丢了颜色（载荷里连 {color} 都没有）—— "
            "这正是真机上「patch 发了但没变色」的成因")
        assert '"collapsible_panel"' in blob, "颜色载体必须是那个小面板"
        assert cards.count_elements(node) <= cards.FEISHU_ELEMENT_LIMIT, cards.count_elements(node)

    # 小面板**自己**不能是空的：空面板飞书会拒（`unified_panel` 里为同一件事补过状态文字），
    # 而「只留个彩色空框」等同于什么都没显示。也不能只留颜色、把状态文字丢掉。
    shells = {status: cards.status_shell(panel_of(status))
              for status in ("ok", "error", "stopped")}
    for status, shell in shells.items():
        assert shell and shell.get("elements"), f"{status} 的状态小面板是空的"
        assert cards.count_elements(shell) >= 3, (status, cards.count_elements(shell))
        child = (shell["elements"][0] or {}).get("content") or ""
        assert child.strip(), (status, shell)
    assert (shells["ok"]["elements"][0]["content"]
            != shells["stopped"]["elements"][0]["content"]), \
        "不同状态的小面板文字必须不同，否则状态色一丢就什么线索都没有了"

    # 没有状态色时不该凭空造一个面板（「不猜」）
    plain, _ = cards.fit_reply_card("汉" * 20000, panel=panel_of(None))
    assert '"collapsible_panel"' not in json.dumps(plain, ensure_ascii=False)
    assert cards.status_shell(None) is None
    assert cards.status_shell(panel_of(None)) is None

    # 硬上限守卫的**两侧**都要验（只验一侧等于没验）：
    #   贴边时宁可不要颜色，也不能把一张**本来发得出去**的卡顶成「拒收 → 回落纯文本」；
    #   有余量时必须把颜色加上，否则这个修复就等于没做。
    overhead = cards.card_bytes(cards.reply_card("x" * 100)) - 100   # 实测，不猜
    shell_bytes = cards.card_bytes(cards.status_shell(panel_of("stopped")))
    assert 0 < shell_bytes < 2000, shell_bytes
    tight = "x" * (cards.FEISHU_CARD_BYTE_LIMIT - overhead - 20)
    bare, _ = cards.fit_reply_card(tight)
    shelled, _ = cards.fit_reply_card(tight, panel=panel_of("stopped"))
    assert cards.card_bytes(bare) <= cards.FEISHU_CARD_BYTE_LIMIT, cards.card_bytes(bare)
    assert cards.card_bytes(shelled) <= cards.FEISHU_CARD_BYTE_LIMIT, cards.card_bytes(shelled)
    assert "yellow" not in json.dumps(shelled, ensure_ascii=False), \
        f"贴边时必须放弃颜色（这个正文 {len(tight)} 字节，加 {shell_bytes} 字节会超硬墙）"

    roomy = "x" * (cards.FEISHU_CARD_BYTE_LIMIT - overhead - shell_bytes - 50)
    fitted, _ = cards.fit_reply_card(roomy, panel=panel_of("stopped"))
    assert cards.card_bytes(fitted) <= cards.FEISHU_CARD_BYTE_LIMIT
    assert "yellow" in json.dumps(fitted, ensure_ascii=False), \
        "明确有余量时还不给颜色 ⇒ status_shell 的守卫条件反了"


def test_degrade_log_names_the_wall_and_the_numbers():
    """降载日志必须说清**撞的是哪堵墙**（档位 / 元素数 / 字节数），第九路审计 F8。

    实测过四条变异四门禁全绿：不打字节、元素数传 0、字节数传 0、**档位写死 "ok"**
    （最误导的一条：明明降载了却记「档位=ok」）。而这条日志是「卡片为什么少了个面板」
    的唯一线索 —— 光看档位名分不出是**字节**触发的还是**元素数**触发的，两者的处置
    完全不同（后者要收轮数/步数，不是收长度）。
    """
    defaults = dict(adapter._DEFAULTS)
    try:
        adapter._log_degrade_once._at = 0.0
        with _LogCapture("larkdeck") as records:
            adapter._log_degrade_once("over-budget", 5, 61000)
        text = _log_text(records)
        assert "over-budget" in text, text
        assert "元素 5/200" in text, text
        assert "字节 61000/40000" in text, text
        # 档位必须来自**实参**（写死 "ok" 的变异在这里变红）
        adapter._log_degrade_once._at = 0.0
        with _LogCapture("larkdeck") as records:
            adapter._log_degrade_once("no-panel", 7, 1234)
        text2 = _log_text(records)
        assert "no-panel" in text2 and "元素 7/200" in text2 and "字节 1234/40000" in text2, text2

        # ③ **调用点**传的数必须是真的（第十路审计 A15/A16：调用点传 (0,0)、或档位写死
        #    "ok"，四门禁全绿 —— 而这条日志是「卡片为什么少了个面板」的唯一线索）
        huge = "汉" * 20000
        adapter._log_degrade_once._at = 0.0
        with _LogCapture("larkdeck") as records:
            made = adapter.LarkDeckMixin._ld_build_card(huge, streaming=False,
                                                        panel=None, footer=None)
        text3 = _log_text(records)
        tier3 = cards.fit_reply_card(huge, panel=None, footer=None)[1]
        assert tier3 in text3, (tier3, text3)
        assert f"元素 {cards.count_elements(made)}/200" in text3, text3
        assert f"字节 {cards.card_bytes(made)}/40000" in text3, text3
        assert "字节 0/" not in text3 and "元素 0/" not in text3, text3
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()
        adapter._log_degrade_once._at = 0.0


def test_over_budget_tier_keeps_the_typewriter_and_never_truncates_body():
    """三种降载档位都要保住打字机配置，且正文永不被截断。

    审计的 M7：把 `fit_reply_card` 里 `over-budget` 分支的 `print_frequency_ms` 参数丢掉，
    四条门禁全绿 —— 于是「正文大到只能裸卡」时打字机静默消失。
    """
    body = "答" * 20000                      # 远超我们的字节预算，但飞书收得下
    # ⚠️ 必须传**自定义**值：`reply_card` 的默认参数就是 15，用默认值测的话
    # 「over-budget 分支忘了把参数传下去」这个变异照样绿（实测过）。
    node, tier = cards.fit_reply_card(body, streaming=True, print_frequency_ms=40)
    assert tier == "over-budget", tier
    assert body in json.dumps(node, ensure_ascii=False), "正文被截断了 —— 绝不允许"
    assert node["config"].get("streaming_config") == {
        "print_frequency_ms": {"default": 40}, "print_step": {"default": 1},
        "print_strategy": "fast"}, "裸卡也应当带打字机配置，且自定义值要传下去"

    # 中间两档同样要带（面板被摘掉、页脚被摘掉，但流式帧还是流式帧）
    panel = cards.unified_panel(reasoning="推理")
    for kwargs in ({"panel": panel}, {"footer": "ctx 1k/2k"}):
        n2, t2 = cards.fit_reply_card("正文", streaming=True, **kwargs)
        assert t2 == "ok" and n2["config"].get("streaming_config"), (t2, kwargs)
    n3, t3 = cards.fit_reply_card(body, streaming=True, footer="ctx", budget=10 ** 9,
                                  element_limit=1)
    assert t3 != "ok" and n3["config"].get("streaming_config"), t3

    # ⚠️ **带状态小面板的那半个超预算分支**（第九路审计 F7）：这一档的卡会带
    # `status_shell`，它与裸卡分支是两条不同的构造语句 —— 早先的断言只覆盖了裸卡，
    # 于是「壳分支忘传 print_frequency_ms」四门禁全绿（用户把 streaming_print_ms 设成 0
    # 关掉打字机时，这一档又会带上 15ms）。
    status_panel = cards.unified_panel(status="stopped")
    for want in (0, 40):
        node_shell, tier_shell = cards.fit_reply_card(
            body, streaming=True, panel=status_panel, print_frequency_ms=want)
        assert tier_shell == "over-budget", tier_shell
        blob_shell = json.dumps(node_shell, ensure_ascii=False)
        assert cards.border_for_status("stopped") in blob_shell, "壳丢了 —— 前提不成立"
        if want == 0:
            assert "streaming_config" not in blob_shell, (
                "0 = 关掉打字机，超预算档不许再带上它")
        else:
            assert node_shell["config"].get("streaming_config") == {
                "print_frequency_ms": {"default": 40}, "print_step": {"default": 1},
                "print_strategy": "fast"}, "超预算 + 小面板这一档也要把自定义值传下去"


def test_print_frequency_is_clamped_and_never_raises():
    """打字机间隔的坏值必须被夹住，且**绝不抛**（抛出去整张卡就回落到纯文本了）。

    ⚠️ 归因修正（第七路审计实测）：审计的 M22 变异（把 `_positive` 里 nan/inf 那半段
    保护删掉）**四条门禁仍然全绿** —— 真正的保护在 `streaming_config()` 自己的
    `try/except (OverflowError)`（`int(float("inf"))` 抛的是它）。下面 ③ 那条「遍历坏值」
    的断言**没有判别力**：`nan` 走 `_positive → False` ⇒ 卡片里压根没这个字段 ⇒ `continue`；
    `inf` 走 `_positive → True` ⇒ 由 `streaming_config` 接住。两条路都看不见 `_positive`。
    所以这里 ① **直接打被保护的那一层**（`streaming_config`），② 单独锁 `_positive` 的行为
    （它是有用的防御，只是不该被冒充成「这条断言守住了 M22」—— 那是 `lessons.md` 推论 6 的复发）。
    """
    # ① 被保护的那一层：越界/坏值一律退回默认，**绝不抛**
    for bad in (float("inf"), float("-inf"), float("nan"), 10 ** 9, 5000, 2001, 0, -5,
                None, "abc", [], {}, "1e999", 10 ** 400, int("9" * 400)):
        got = cards.streaming_config(bad)["print_frequency_ms"]["default"]
        assert got == cards.DEFAULT_PRINT_FREQUENCY_MS, (bad, got)
    # 边界内保留原值（夹取不该把合法值也吃掉）
    assert cards.streaming_config(1)["print_frequency_ms"]["default"] == 1
    assert cards.streaming_config(cards.PRINT_FREQUENCY_MAX_MS)[
        "print_frequency_ms"]["default"] == cards.PRINT_FREQUENCY_MAX_MS

    # ② `_positive` 自身的行为（第 ① 条的保护**不**来自它）
    assert cards._positive(float("inf")) is False
    assert cards._positive(float("nan")) is False
    assert cards._positive(0) is False and cards._positive(-1) is False
    assert cards._positive(1) is True and cards._positive("40") is True

    # ③ 经完整卡片路径：任何坏值都不许抛，落进卡里的值必须在合法区间内
    for bad in (float("nan"), float("inf"), float("-inf"), None, "abc", [], {},
                "1e999", 0, -5, 10 ** 9, 5000):
        node = cards.reply_card("正文", streaming=True, print_frequency_ms=bad)
        cfg = node["config"].get("streaming_config")
        if cfg is None:
            continue  # 0 / 负数 / 坏值 = 不带这个字段，也是允许的结果
        value = cfg["print_frequency_ms"]["default"]
        assert 1 <= value <= cards.PRINT_FREQUENCY_MAX_MS, (bad, value)


def test_out_of_range_print_frequency_is_logged_not_silent():
    """`streaming_print_ms` 越界时必须**留痕**（第七路审计 P2）。

    实测旧行为：写 `5000`（用户想要「最慢」）→ 卡片收到 15ms（视觉上等于没有动画），
    **零日志** —— 与本项目「诊断路径不许静默」的纪律直接冲突（其他同类路径
    `_log_note_text_skipped` / `_log_degrade_once` / `_log_empty_panel_once` 都有限流日志）。
    """
    from larkdeck.core import adapter as _adapter_module   # noqa: F401 - 只为 lint 友好

    def _value(cfg):
        adapter.configure(streaming_print_ms=cfg)
        adapter._log_print_ms_once._at = 0.0          # 清限流窗口
        try:
            with _LogCapture("larkdeck") as records:
                got = adapter._print_frequency_ms()
        except Exception as exc:
            # ⚠️ 必须**转成断言失败**而不是让异常飞出去：这条路径跑在插件注册里，
            # 抛出去就是「插件整体不生效、静默退回纯文本」（本项目最怕的失败模式）。
            # 而且断言失败才是门禁的判别力证据 —— 崩溃只说明代码坏了（第九路审计 F3/F6）。
            raise AssertionError(f"配置 {cfg!r} 让取值抛了异常：{exc!r}") from exc
        return got, _log_text(records)

    defaults = dict(adapter._DEFAULTS)
    try:
        # ① 越界：**边界上下都要打**。`PRINT_FREQUENCY_MAX_MS + 1` 这一条是有判别力的 ——
        #    第八路审计实测：把 `_print_frequency_ms` 里的上限改成字面量 2001，四门禁全绿
        #    （两个模块各持一份上限、分叉了没人管）。现在这条会把分叉抓住。
        for bad in ("5000", 5001, 10 ** 9, cards.PRINT_FREQUENCY_MAX_MS + 1,
                    10 ** 400, int("9" * 400)):
            got, text = _value(bad)
            assert got == cards.DEFAULT_PRINT_FREQUENCY_MS, (bad, got)
            assert "streaming_print_ms" in text and "退回" in text, (bad, text)

        # ② **转不动**的值同样不许静默（第八路审计指出的另一半：`_cfg_int` 在范围判断之前
        #    就把它们塌成默认，于是这条分支永远见不到它们 —— 写 "fast"/""/null/inf 全静默）
        for junk in ("abc", "", [], {}, float("inf"), float("nan"), "1e999",
                     10 ** 400, int("9" * 400)):
            got, text = _value(junk)
            assert got == cards.DEFAULT_PRINT_FREQUENCY_MS, (junk, got)
            assert "不是可用的数字" in text, (junk, text)
            # 告警要打**用户写的那个值**，不是 float 舍入后的天文数字
            assert repr(junk)[:40] in text, (junk, text)

        # ③ 合法值 / 0（= 关掉打字机）不许报警，也别改值
        for good in (1, 15, cards.PRINT_FREQUENCY_MAX_MS):
            got, text = _value(good)
            assert got == good and text == "", (good, got, text)
        for off in (0, -5, "0"):
            got, text = _value(off)
            assert got == int(off) and text == "", (off, got, text)
        # 数字串（config.yaml 里常见的引号写法）与 float 都要照常吃下去
        for same in ("15", 15.0):
            got, text = _value(same)
            assert got == 15 and text == "", (same, got, text)
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()
        adapter._log_print_ms_once._at = 0.0
        adapter._print_frequency_ms._at = 0.0


def test_reactions_override_respects_the_parent_and_is_registered():
    """`_reactions_enabled` 的覆盖必须**尊重父类语义**，并且已在 `compat.py` 登记。

    审计的两条：M10（让覆盖忽略父类、直接 `return True`）四门禁全绿；更重要的是
    P3 —— 这个私有名此前**没进 compat**，上游改名后启动自检不报警、开关静默失效。
    """
    assert "_reactions_enabled" in compat.REACTION_ADAPTER_ATTRS, "私有名没登记进 compat"
    defaults = dict(adapter._DEFAULTS)

    class _Base:
        def __init__(self, answer):
            self._answer = answer

        def _reactions_enabled(self):
            return self._answer

    try:
        merged = type("M", (adapter.LarkDeckMixin, _Base), {})
        adapter.configure(reactions=True)
        assert merged(True)._reactions_enabled() is True
        # 父类说「关」时，我们开着配置也必须跟着父类（尊重内置语义 / 宿主环境变量）
        assert merged(False)._reactions_enabled() is False, "覆盖忽略了父类的判断"
        adapter.configure(reactions=False)
        assert merged(True)._reactions_enabled() is False, "显式关掉时应当关"
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_panel_concurrent_writes_are_safe():
    panel.reset()
    errors: list = []

    def worker(n: int) -> None:
        try:
            for i in range(100):
                panel.record_reasoning(f"s{n}", "t1", "y")
                panel.record_tool_started(f"s{n}", "t1", "tool", {}, f"c{i}")
                panel.record_tool_finished(f"s{n}", "t1", "tool",
                                           tool_call_id=f"c{i}")
                panel.snapshot()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert panel.snapshot() is not None
    panel.reset()


# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 9. 阶段 0 修复的回归网（审计指出这批「零覆盖」，逐条补上）
#
# 每条都必须**能抓住对应缺陷**：撤掉修复会红。补这些是因为
# docs/lessons.md 推论 1：每加一层能力，都要配一个「能自证」的东西。
# --------------------------------------------------------------------------- #
def test_inf_and_nan_config_never_escape_as_exceptions():
    """`inf` / `1e999` / `.inf` 这类 YAML 合法值**不许**让插件注册炸掉。

    这是审计复现出的头号缺陷：`_as_int` / `_cfg_int` 只捕 TypeError/ValueError，
    而 `int(float("inf"))` 抛的是 **OverflowError**。于是配置里写 `inf` 会经
    `configure()` 一路穿到**没有任何 try 的 `register()`** → 注册整体失败 →
    **静默退回纯文本**（本项目最怕的失败模式）。
    """
    defaults = dict(adapter._DEFAULTS)
    bad_values = (float("inf"), float("-inf"), float("nan"), "inf", "1e999", object())
    try:
        for bad in bad_values:
            # 1) adapter._cfg_int：退回默认，不抛
            adapter._CONFIG["max_panel_steps"] = bad
            assert adapter._cfg_int("max_panel_steps", 30) == 30, bad
            # 2) configure 整条链不抛
            adapter.configure(context_max_override=bad, max_reasoning_chars=bad)
            # 3) cards._cap 同样兜住（这是同类缺陷的第三处）
            assert cards._cap(bad, 7) == 7, bad
            # 4) 两个 _as_int 兜住
            assert context._as_int(bad) is None, bad
            assert panel._as_int(bad) is None, bad
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_ld_known_never_raises_without_setup():
    """`_ld_setup()` 没跑成时，`_ld_known` 必须返回 None 而不是抛。

    `edit_message` 的**第一行**就调它，而那一行在 try 之外 —— 一旦抛出去就会穿进
    核心的每帧编辑路径，破坏「卡片失败必须回落官方实现」这条不变量。
    """
    class Bare:
        pass

    bare = Bare()
    assert adapter.LarkDeckMixin._ld_known(bare, "om_x") is None
    # 状态被塞成非 dict 也不许抛
    bare._ld_state = "不是 dict"
    bare._ld_lock = threading.Lock()
    assert adapter.LarkDeckMixin._ld_known(bare, "om_x") is None


def test_args_preview_is_bounded_for_nested_and_long_inputs():
    """有界序列化要对**嵌套**与**超长列表**同样成立（不只是最平的那种参数）。

    审计实测：修正前 `{"edits":[{"old_str":5MB}]}` = 18.9ms、2M 元素 list = 14.4ms，
    而扁平 `{"content":5MB}` 只有 0.022ms —— 也就是「有界」只对一种形状成立。
    """
    big = "x" * 5_000_000
    nested = {"edits": [{"old_str": big, "new_str": big}]}
    deep = {"a": {"b": {"c": big}}}
    huge_list = {"items": ["y"] * 2_000_000}

    def _cost_ms(payload) -> float:
        """取多次测量的**最小值**：这条断言要判的是「有没有 O(参数规模) 的工作」，
        而最小值最不受机器负载影响（2026-09-13 实测：并发跑别的重活时单次采样会
        从 0.02ms 飘到 10ms，把一条正确的实现判成红的 —— 测量方式本身要有抗噪设计）。"""
        best = float("inf")
        for _ in range(5):
            t0 = time.perf_counter()
            got = panel._args_preview(payload)
            best = min(best, (time.perf_counter() - t0) * 1000)
        return best, got

    for label, payload in (("嵌套", nested), ("深层", deep), ("超长列表", huge_list)):
        cost_ms, got = _cost_ms(payload)
        assert cost_ms < 5.0, f"{label}参数预览耗时 {cost_ms:.1f}ms —— 有界序列化失效了"

        assert len(got) <= panel._ARGS_PREVIEW_CHARS + 1, (label, len(got))
    # ⚠️ **契约判据**（审计逼出来的，因为它证明「撤掉扫描上限」原本能全绿）：
    #    送进 `redact_inline_secrets` 的文本**必须有硬上限**。这条判据**不用墙上时钟** ——
    #    时间断言在高负载下会飘（本文件上面那段 docstring 就为这件事设计过「取最小值」），
    #    而这一条要防的是**有人把上限拿掉**，判据必须是确定的。
    #    实测过的量级：1MB 文本上四条规则合计 436ms（ENV 一条占 407ms）⇒ 上限被拿掉之后，
    #    fail-closed 钩子会被一个大参数拖住，而钩子慢会拖住**整批工具执行**。
    _seen: list = []
    _orig_redact = panel.redact_inline_secrets

    def _spy(text):
        _seen.append(len(text))
        return _orig_redact(text)

    _worst = {f"k{i}": {f"m{j}": {f"n{k}": "v" * 120 for k in range(8)} for j in range(8)}
              for i in range(8)}
    try:
        panel.redact_inline_secrets = _spy
        panel._args_preview(_worst)
    finally:
        panel.redact_inline_secrets = _orig_redact
    assert _seen, "脱敏必须真的被调用（否则这一格什么都没验）"
    assert max(_seen) <= panel._REDACT_SCAN_CHARS, (
        f"送进脱敏的文本超过上限（{max(_seen)} > {panel._REDACT_SCAN_CHARS}）—— "
        "fail-closed 钩子会被大参数拖慢（实测 1MB ⇒ 436ms）")
    # 同一条上限也要能挡住「最坏嵌套形状」的耗时（这里放宽到 15ms：墙钟只做兜底）
    _t0 = time.perf_counter()
    panel._args_preview(_worst)
    _cost = (time.perf_counter() - _t0) * 1000.0
    assert _cost < 15.0, f"最坏嵌套形状耗时 {_cost:.1f}ms —— 扫描上限失效了"
    # 自引用结构不许无限递归
    cyc: dict = {}
    cyc["self"] = cyc
    assert len(panel._args_preview(cyc)) <= panel._ARGS_PREVIEW_CHARS + 1


def test_api_hook_passes_base_url_to_the_context_probe():
    """`post_api_request` 回调必须把 base_url 传给指标层 —— 页脚的窗口靠它解析。

    病（2026-09-14 线上）：`_on_api_request` 只透传 model/provider/usage/response_model，
    而 `record_api_call` 里读的是 `payload.get("base_url")` ⇒ 快照的 base_url 恒为 ""，
    探测退化成 `get_model_context_length(model, base_url="")`（无 base_url、无 provider）。
    对**不在硬编码表里、靠 provider 元数据解析窗口**的模型 —— opencode-go 的
    `deepseek-flash`（真实 1M）—— 这一步落到家族兜底 `deepseek`: 128K，
    于是页脚恒显示 `ctx x/128k`，用户按这个数字判断「该压缩了」会一直误判。

    `tests/check_hooks.py` 抓不到这条：它**自己**把 base_url 塞进派发载荷，
    而缺陷恰恰在「回调 → 指标层」这一跳。所以这里直接对真回调打桩上游 API，
    断言**到达 `get_model_context_length` 的 base_url** 与载荷一致。
    """
    assert hooks._context is context, \
        "钩子读的 context 与测试读的不是同一个模块对象 —— 下面的打桩会看不见"
    url = "https://opencode.ai/zen/go/v1"
    seen: list = []
    fake = types.ModuleType("agent.model_metadata")

    def _capture(model, base_url="", **_kw):
        # 记**每一次**探测：别的用例可能留下一枚晚跑的探测线程，它带着空的 base_url 进来。
        # 用「集合里出现过正确的一对」判定，而不是「最后一次」，否则测试顺序会变成判据。
        seen.append((model, base_url))
        return 1_000_000

    fake.get_model_context_length = _capture
    agent_mod = sys.modules.get("agent") or types.ModuleType("agent")
    old_meta = getattr(agent_mod, "model_metadata", None)
    sys.modules["agent"] = agent_mod
    agent_mod.model_metadata = fake
    sys.modules["agent.model_metadata"] = fake
    try:
        context.reset()
        hooks._on_api_request(
            model="deepseek-flash", provider="opencode-go", base_url=url,
            usage={"input_tokens": 20753, "prompt_tokens": 20753},
            response_model="deepseek-flash",
        )
        assert context._LATEST.get("base_url") == url, \
            f"① 回调没把 base_url 存进快照（存的是 {context._LATEST.get('base_url')!r}）"
        for _ in range(60):  # 探测在后台线程上，等它写回
            if context.context_max() == 1_000_000:
                break
            time.sleep(0.05)
        assert ("deepseek-flash", url) in seen, \
            f"② base_url 没传到上游 —— 页脚会按兜底窗口算百分比。上游实际收到：{seen}"
        assert context.context_max() == 1_000_000
    finally:
        sys.modules.pop("agent.model_metadata", None)
        if old_meta is None:
            sys.modules.pop("agent", None)
        else:
            agent_mod.model_metadata = old_meta
        context.reset()


def test_context_max_never_blocks_the_caller():
    """`context_max` 未命中缓存时**必须立即返回**，把探测交给后台线程。

    它在事件循环线程上被调用（send / edit_message / send_stream_frame 都是 async），
    而官方 `get_model_context_length` 会发 HTTP（官方为此专门提供 async 版本，
    注释写明会 stall the event loop）。同步探测会让首次渲染页脚时**所有会话的流式帧
    一起停等几秒**。
    """
    import types

    hold = threading.Event()
    fake = types.ModuleType("agent.model_metadata")

    def _slow(model, base_url=""):
        hold.wait(3.0)
        return 200_000

    fake.get_model_context_length = _slow
    agent_mod = sys.modules.get("agent") or types.ModuleType("agent")
    old_meta = getattr(agent_mod, "model_metadata", None)
    sys.modules["agent"] = agent_mod
    agent_mod.model_metadata = fake
    sys.modules["agent.model_metadata"] = fake
    try:
        context.reset()
        context.record_api_call(model="probe-model", usage={"input_tokens": 1000})
        t0 = time.perf_counter()
        first = context.context_max()
        cost_ms = (time.perf_counter() - t0) * 1000
        assert cost_ms < 500, f"首次调用阻塞了 {cost_ms:.0f}ms —— 又回到同步探测了"
        assert first is None, "未命中时应当返回 None（页脚退化成只显示已用量）"
        hold.set()
        for _ in range(60):  # 等后台线程写回
            if context.context_max() == 200_000:
                break
            time.sleep(0.05)
        assert context.context_max() == 200_000, "后台探测没有把结果写回缓存"
    finally:
        hold.set()
        sys.modules.pop("agent.model_metadata", None)
        if old_meta is None:
            sys.modules.pop("agent", None)
        else:
            agent_mod.model_metadata = old_meta
        context.reset()


def test_truncate_never_over_reports_omission():
    """「已省略 N 字符」必须按**实际切点**算。

    切点会被块级/字形回退拉到 limit 之前，而原实现用 `len(text) - limit` ——
    回退时少报（审计实测：实际丢 108、文案声称 68）。
    """
    text = "A" * 60 + "\n- item\n" + "B" * 100
    out = cards.truncate(text, 100)
    head, _, note = out.partition("\n> ")
    omitted = int("".join(ch for ch in note if ch.isdigit()))
    actual = len(text) - len(head)
    assert omitted >= actual, f"少报了：声称省略 {omitted}，实际丢 {actual}"


def test_panel_attribution_is_deterministic_by_chat_id():
    """有 chat→session 绑定时，面板归属必须是**确定性**的，不是「最近活跃」。

    这是消掉多会话串台的关键：钩子载荷只有 ``session_id``、适配器只有 ``chat_id``，
    靠 ``pre_gateway_dispatch`` 观察到的映射把两者对上。以前只能取「最近活跃」，
    并发会话时 A 的卡片会显示 B 的推理/工具。
    """
    panel.reset()
    panel.record_reasoning("s1", "t1", "会话一的推理")
    panel.record_reasoning("s2", "t2", "会话二的推理")
    assert panel.snapshot()["session_id"] == "s2", "无绑定时退回最近活跃（旧行为）"

    panel.bind_chat_session("oc_one", "s1")
    panel.bind_chat_session("oc_two", "s2")
    assert panel.snapshot("oc_one")["session_id"] == "s1", "按 chat 归属失败 —— 会串台"
    assert "会话一的推理" in panel.snapshot("oc_one")["reasoning"]
    assert panel.snapshot("oc_two")["session_id"] == "s2"

    # 未知 chat（**没有绑定**）→ 退回旧行为；**绝不能因为归属失败就不渲染面板**
    assert panel.snapshot("oc_unknown")["session_id"] == "s2"
    # ⚠️ 但**有绑定、只是那个会话暂时没内容**时，必须表现为「这张卡没有面板」，
    # **不能**换成别的会话 —— 那正是跨会话错色 / 中止状态写错会话的根因
    # （2026-09-13 审计判为阻断项的那条，原来这里断言的是「退回 s2」）。
    panel.bind_chat_session("oc_empty", "s-nonexistent")
    assert panel.snapshot("oc_empty") is None, \
        "绑定的会话没内容 ≠ 可以拿别人的面板来画"
    # 绑定的会话桶空着、但另一个会话有内容时，同样不许串台
    panel.bind_chat_session("oc_fresh", "s-fresh")
    panel.begin_turn("s-fresh", "t-fresh")  # 建一个空桶（换回合会清空过程数据）
    assert panel.snapshot("oc_fresh") is None, "空桶的绑定会话把别人的面板带出来了"

    # 空值不入表
    panel.bind_chat_session("", "s1")
    panel.bind_chat_session("oc_x", "")
    assert panel.bound_session_id("") == "" and panel.bound_session_id("oc_x") == ""
    panel.reset()


def test_session_key_includes_profile_when_multiplexed():
    """开了 ``multiplex_profiles`` 时算键必须带 profile —— 否则绑定永远写不进去。

    2026-09-13 审计发现的潜在坑：``build_session_key`` 在 multiplex 模式下会把 profile
    段算进键里，我们少传一个参数就会**永远匹配不上**，于是归属整体静默退回「最近活跃」
    （不报错、日志里也看不出来）。本机没开这个开关，所以是潜在坑而非现症 ——
    但正因如此才需要一条断言把它钉住。
    """
    class _Cfg:
        group_sessions_per_user = True
        thread_sessions_per_user = False
        multiplex_profiles = False

    class _Store:
        def __init__(self, multiplex: bool) -> None:
            self.config = _Cfg()
            self.config.multiplex_profiles = multiplex

        def _resolve_profile_for_key(self, source):
            return "work" if getattr(source, "chat_id", "") == "oc_work" else None

    source = types.SimpleNamespace(chat_id="oc_work", platform="feishu")
    # 没开 multiplex：不传 profile（与核心默认行为一致）
    assert compat.profile_for_source(_Store(False), source) == ""
    # 开了：要把 profile 算进键
    assert compat.profile_for_source(_Store(True), source) == "work"
    # 算不出 profiles / 老版本 store：退回空串，不抛
    assert compat.profile_for_source(object(), source) == ""
    assert compat.profile_for_source(None, source) == ""


def test_session_attribution_helpers_degrade_safely():
    """归属辅助函数拿不到东西时**必须返回空/False，绝不抛**；且**只读**。"""
    assert compat.lookup_session_id(None, "k") == ""
    assert compat.lookup_session_id(object(), "") == ""
    assert isinstance(compat.session_attribution_available(), bool)

    class Store:
        def __init__(self):
            self.created = False

        def get_or_create_session(self, *a, **k):  # pragma: no cover - 不该被调用
            self.created = True
            raise AssertionError("归属绝不能用 get_or_create_session（会改核心行为）")

        def peek_session_id(self, key):
            return "sess-1" if key else None

    store = Store()
    assert compat.lookup_session_id(store, "k") == "sess-1"
    assert store.created is False, "用了会建会话的接口 —— 违反只读纪律"


def test_late_begin_turn_must_not_poison_current_turn():
    """迟到的 `on_stream_start` **不许**把当前回合作废 —— 否则整回合面板永久黑屏。

    2026-09-12 踩过：曾给 `begin_turn` 开了一条 `reopen=True` 例外（本意是处理
    turn_id 复用），它**绕过了作废集检查**并落进替换分支，于是被记进作废集的
    是**当前正在跑的回合** —— 一个迟到的 start 就能毒死整回合的面板，
    比原来的瞬态清空更糟（父版下一个事件就把面板拉回来了）。
    """
    panel.reset()
    panel.begin_turn("s1", "t1")
    panel.record_reasoning("s1", "t1", "第一回合")
    panel.begin_turn("s1", "t2")
    panel.record_reasoning("s1", "t2", "第二回合")

    panel.begin_turn("s1", "t1")  # t1 的 start 迟到（它自己那条队列落后了）

    panel.record_reasoning("s1", "t2", "第二回合后续")
    snap = panel.snapshot()
    assert snap is not None, "迟到的旧 start 把面板毒死了（整回合黑屏）"
    assert "第二回合后续" in snap["reasoning"], "当前回合的数据被丢弃了"
    assert "t2" not in list(panel._STATE["s1"]["closed"]), "当前回合被记进了作废集"
    panel.reset()



#: 进度表里「已经实施」的阶段名（**事实**清单，不是空格形状）：缺一行就红。
_PLAN_DONE_PHASES = ("R0", "R1", "R1.5", "R2", "R5", "R6a", "R7", "R9", "R8①②")


def _plan_progress_problems(plan: str):
    """检查 `docs/plan-v1.md` 的进度表，返回**问题清单**（空 = 通过）。

    判据全部**从表格行派生**，不硬编码任何字面 token（R6a 审计低-6：旧版只查 `"| R3"`
    与 `"| R5 "`（含半角空格）之类的字面串，于是「掏空照样绿、合法改写假红」同时成立）。

    单独抽成函数是为了**能被合成输入测**：`test_plan_progress_table_is_checkable...` 会拿
    「把表格反过来」「把证据列清空」这些构造样本喂进来，证明这个门禁**真的会拒绝**它们
    —— 否则「门禁有效」这件事本身没有证据（`docs/lessons.md` 推论 11）。
    """
    problems = []
    if "## 实施进度" not in plan:
        return ["找不到「## 实施进度」这一节 —— 进度表被搬走或改名了"]
    region = plan[plan.index("## 实施进度"):]
    # 只取这一节的**第一个标题之前**的内容：进度表本身就贴在这个标题下面，
    # 后面接的是审计小节（那些小节里也有表格，混进来会解析出一堆假「阶段」行）。
    region = region[:region.index("\n#", 1)]
    rows = []
    for line in region.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or set(cells[0]) <= set("-: "):      # 表头分隔行
            continue
        rows.append(cells)
    if not rows:
        return ["进度表一行都没解析出来（表格被重排了？）"]
    if rows[0][0] != "阶段" or rows[0][1] != "状态":
        problems.append(f"表头变了：{rows[0]}")
    body = rows[1:]
    if len(body) < 8:
        problems.append(f"进度表只剩 {len(body)} 行 —— 有记录被抹掉了")
    for cells in body:
        phase, status, evidence = cells[0], cells[1], "|".join(cells[2:]).strip()
        if not (status.startswith("✅") or status.startswith("未开始")):
            problems.append(f"阶段「{phase}」的状态既不是 ✅ 也不是「未开始」：{status[:40]!r}")
        if not evidence:
            problems.append(
                f"阶段「{phase}」的**证据**列是空的 —— 这一列是「怎么证明它做完了」的唯一记录，"
                "空着等于没做（审计实测：掏空 R3 那行的证据列，旧门禁照旧全绿）")
    # 「未完成的下一阶段」必须存在，而且**要等什么**必须写出来。
    # ⚠️ 这里**不能**用「未开始的阶段号 > 已完成的最大阶段号」那种判据：本项目的阶段编号
    # 不是单调推进的（R3 被停在附录 C 的肉眼确认上，之后先做了 R5/R6a/R7/R8/R9）——
    # 上一版这么写就假红了（实测：'R3 起' 与 'R9' 比大小）。
    pending = [c for c in body if c[1].startswith("未开始")]
    if not pending:
        problems.append("没有任何「未开始」的行 —— 进度表看不出「下一步在等什么」")
    for cells in pending:
        if not re.search(r"[（(].+[）)]", cells[1]):
            problems.append(f"「未开始」的阶段「{cells[0]}」没说清在等什么：{cells[1]!r}")
    if "R3" not in "\n".join(c[0] for c in pending):
        problems.append(
            "R3（下一步）不在「未开始」的行里 —— 它要么被标成完成，要么整行被删了。"
            "2026-09-14 实测过一次静默丢失：给进度表追加一行时替换串漏带了被替换的那一行，"
            "于是「已完成的行」上去了、「未完成的行」没了，而四门禁照旧全绿。")
    # 已实施阶段的行**必须都在**（防止「整张表被谁重写」或「追加一行时抹掉另一行」）。
    # ⚠️ 判据是「**整行**里有没有这个名字」，**不是**「阶段列的第一个 token 是不是它」：
    # 后者会在「同一行的证据列被掏空」时也报「行不见了」（实测：变异 R6a-18 被误归因成
    # 这一条），而那种改动的真正病是**证据列空了**——误归因会把人带去改错地方。
    # 同时这条判据**不**依赖空格形状：`'| R5 健壮性'` ⇒ `'| R5（健壮性）'` 照样绿
    # （旧版写死 `"| R5 "`（带半角空格）就是这么假红的，审计实测）。
    # 判据只看**阶段列**（不能看整行：证据列里会出现 `R6a-1..R6a-4` 这种字样，
    # 拿整行做子串匹配会让「R6a 那一行被删掉」照样通过 —— 实测过）。
    joined = "\n".join(c[0] for c in body)
    for name in _PLAN_DONE_PHASES:
        if not re.search(r"^" + re.escape(name), joined, re.M):
            problems.append(f"已完成阶段 {name} 的行不见了")
    return problems


def _append_progress_row(plan: str, row: str) -> str:
    """把一行**进度表行**追加到 `## 实施进度` 那张表的末尾。

    为什么测试要能自己造行（R6a 审计低-6 / `docs/lessons.md` 推论 11）：
    门禁的判别力不能只用「改真文档」来证明 —— 挪动真文档的行会同时触发**别的**断言，
    于是「掏空证据列被抓住了」这句话就成了误归因（究竟是被证据判据抓的，还是被
    「行不见了」抓的？）。造一行来测，红的理由就是**唯一**的。
    """
    assert row.startswith("|") and row.endswith("|"), row
    lines = plan.splitlines(True)
    head = next(i for i, l in enumerate(lines) if l.startswith("## 实施进度"))
    index = head
    for i in range(head, len(lines)):
        if lines[i].lstrip().startswith("|"):
            index = i
        elif i > head and not lines[i].lstrip().startswith("|") and index > head:
            break
    assert index > head, "没找到进度表的行 —— 这份测试的前提不成立"
    return "".join(lines[:index + 1]) + row + "\n" + "".join(lines[index + 1:])


def test_plan_progress_table_is_checkable_from_its_own_rows() -> None:
    """`docs/plan-v1.md` 的进度表门禁：**真的会拒绝**坏表格，且**不**对合法改写假红。

    R6a 审计低-6 实测了旧版（只查 `"| R3"` 与几个 `"| R5 "` 之类的字面串）的三个毛病：
      * **掏空照样绿**：R3 那一行的「证据」列清空（`'| R3 起 | 未开始 | |'`）⇒ 154/154 全绿，
        而那一行正是「下一步在等什么」的唯一记录；
      * **合法改写假红**：把 `'| R5 健壮性'` 改成 `'| R5（健壮性）'`（**内容等价**）⇒ 变红，
        红的理由与被守的回归毫无关系；
      * **R8/R9/R6a 的行被删查不出来**（清单里只点名 R0/R1/R2/R5/R7）。

    这条用例**两半都要**：① 真文档必须通过；② 四种构造样本必须被拒绝 —— 只有 ① 的话，
    「门禁有没有判别力」就没有证据（`docs/lessons.md` 推论 11：给验证器本身写输入）。
    判别力：变异 `R6a-18`（在**真文档**上掏空 R6a 那一行的证据列）必须让这条红。
    """
    real = (_pathlib.Path(_REPO_PARENT) / "larkdeck" / "docs" / "plan-v1.md").read_text(
        encoding="utf-8")
    assert _plan_progress_problems(real) == [], _plan_progress_problems(real)

    # ① 掏空证据列（审计的 S2 场景）⇒ 必须被拒绝。
    #    用**同一张表里已有的一行**做模板造一行新记录，只把证据列留空：
    #    这样红的理由只可能是「证据列空」，不会顺带触发「行不见了」那条（误归因）。
    template = [l for l in real.splitlines() if l.startswith("| R6a ")][0]
    hole = _append_progress_row(real, template.rsplit("|", 2)[0] + "|   |")
    hole_problems = _plan_progress_problems(hole)
    assert any("证据" in p for p in hole_problems), hole_problems

    # ② 「未完成的下一个阶段」整行被删（2026-09-14 真实发生过的静默丢失）⇒ 必须被拒绝。
    #    ⚠️ 目标行必须取**状态列是「未开始」的那一行**，不能取「第一行以 `| R3` 开头的」：
    #    2026-09-14 R3 收窄版落地后表里同时有「R3 面板拆两块（收窄版）| ✅ 完成」与
    #    「R3 完整版 | 未开始」两行，按前缀取第一行会删掉**已完成**那一行 ——
    #    而判据「未开始的行里有 R3」仍被另一行满足 ⇒ 这条构造样本**自己失去判别力**（实测红了）。
    #    判据与要守的回归（「未开始那一行被静默抹掉」）必须同源，这就是同源的写法。
    pending_r3 = [l for l in real.splitlines()
                  if l.startswith("| R3") and "未开始" in l]
    assert len(pending_r3) == 1, f"表里应当只有一行「R3 …未开始」：{pending_r3}"
    dropped = real.replace(pending_r3[0] + "\n", "", 1)
    dropped_problems = _plan_progress_problems(dropped)
    assert any("R3" in p for p in dropped_problems), dropped_problems

    # ③ 状态列写成空话（既不是 ✅ 也不是「未开始」）⇒ 必须被拒绝
    vague = real.replace("| R6a markdown 卫生 | ✅ 完成", "| R6a markdown 卫生 | 大概做了", 1)
    assert any("状态" in p for p in _plan_progress_problems(vague)), _plan_progress_problems(vague)

    # ④ 合法改写**不许**假红（阶段名后面加全角括号；旧门禁就是在这里假红的）
    legit = real.replace("| R5 健壮性 |", "| R5（健壮性） |", 1)
    assert _plan_progress_problems(legit) == [], _plan_progress_problems(legit)


def test_status_lines_never_claim_healthy_without_records():
    """没有记录时，**每一条记录行都要说「无记录」**；一张永远说「正常」的自检，
    与一张坏掉的自检在用户眼里长得一模一样（「绿而无判别力」）。

    判别力：变异 `R9-2`（把「无记录」改成「正常」）必须让这条红。
    """
    context.reset()
    lines = context.status_lines()
    # R11-C2 起是 6 行：入站 / 写卡 / 写卡失败 + **运行时长 / 掉回纯文本 / 错误码**
    assert len(lines) == 6, lines
    for i, line in enumerate(lines):
        if i == 3:
            # ⚠️ **运行时长是唯一的例外，而且必须明说**：它是**时钟读数**（建盒子那一刻就有），
            #    不是「记录」⇒ 永远有值；它也不是健康声明（"已运行 3s" 什么都没断言）。
            #    把它也要求成「无记录」会让这个字段变成永远无用的装饰。
            assert "已运行" in line and "无记录" not in line, line
            continue
        assert "无记录" in line, f"没有记录却没说「无记录」：{line!r}"
    assert "正常" not in "".join(lines), "没有任何证据却给自己发了张健康证明"
    # ⚠️ **累计次数**必须一起显示：只有「最近一次是什么时候」的话，刚重启的进程与
    # 「跑了三天、一次没失败」看起来完全一样。
    # ⚠️ 写卡那行的尾巴在 R9 审计中-5 之后**故意**变长了（多一句口径说明）：
    # 它数的是**帧数**，不是 API 调用次数（cardkit 一帧最多 3 次逻辑写、seed 是 2 次网络调用、
    # 撞限流一次逻辑写最多 4 次 HTTP，全都只 +1）⇒ 断言跟着**改口径**，不是放松：
    # 仍然要求「字面量 `累计 0`」+ 仍然要求出现「帧」这个单位词（含糊写「次」会让用户以为
    # 它数的是写动作，而 patch 车道恰好一比一，纯属巧合 —— 审计中-5 指出的病）。
    assert lines[0].endswith("累计 0 条消息"), lines
    assert "累计 0 帧真的有写出" in lines[1], lines


def test_status_lines_report_real_records_with_time_and_count():
    """有记录时三条**记录行**各自带上**时刻 + 累计次数 + 失败原因**（心跳/写卡/写卡失败）。

    R11-C2 起 `status_lines()` 返回 6 行（后三行是 uptime / 掉回纯文本 / 错误码），
    所以这里**按下标取前三行**并显式断言行数 —— 行数变了必须当场红，不能悄悄滑过去。
    """
    context.reset()
    context.note_inbound()
    context.note_inbound()
    context.note_frame_ok()
    context.note_frame_fail("收尾帧失败（boom）")
    _all = context.status_lines()
    assert len(_all) == 6, _all
    inbound, written, failed = _all[:3]
    assert re.match(r"^入站心跳：\d\d-\d\d \d\d:\d\d:\d\d · 距上次 \d+[smh] · 累计 2 条消息$",
                    inbound), inbound
    # 写卡行：时刻 + **帧数**（口径见中-5）+ 单位说明。用「累计 1 帧」而不是「累计 1 次」，
    # 这样「把帧说成 API 调用次数」那种文案退化会当场红。
    assert re.match(r"^最近写卡：\d\d-\d\d \d\d:\d\d:\d\d · 累计 1 帧真的有写出"
                    r"（帧数，不是 API 调用次数）$", written), written
    assert "累计 1 次" in failed and "收尾帧失败（boom）" in failed, failed
    # 失败行也要写清口径（中-2）：它只算**我们发起且失败**的写，不含「没活跃流可收尾」
    # 那种按契约返回 False 的正常路径 —— 不写的话用户会拿它当「核心收到几个 False」的证据。
    assert "只算我们发出且失败的写" in failed, failed


def test_when_rejects_dirty_timestamps_below_the_epoch_floor():
    """`_when` 的判据是「在一个**真实运行时刻的合理范围内**」，不是「正数」（R9 审计低-3）。

    修之前只判 `ts > 0`，于是 `1e-6` 会渲染成 `01-01 08:00:00`（本地时区下的 1970-01-01）——
    一个**看起来像真时刻**的脏值。用户会以为插件刚刚写过卡 / 刚收到过消息，
    而正确处置是「无记录」（与「读不到就说读不到」同一条纪律）。
    ⚠️ 这条必须**直接打 `_when`**：账本里写入的时刻都来自 `time.time()`（≈1.7e9），
    脏值只可能从别处（手工写入 / 反序列化 / 未来某个新的写入点）进来 ——
    只经 `status_lines()` 是**永远碰不到**这条判据的（第一版断言就是这样，变异 R9-19 全绿）。
    """
    real = time.time()
    # 真实时刻附近必须**照常**格式化（别把闸门收得过紧）
    assert re.match(r"^\d\d-\d\d \d\d:\d\d:\d\d$", context._when(real)), context._when(real)
    assert re.match(r"^\d\d-\d\d \d\d:\d\d:\d\d$", context._when(real - 0.5))
    for dirty in (0, 0.0, 1e-6, -1, float("nan"), float("inf"), float("-inf"),
                  True, "1700000000", None):
        assert context._when(dirty) == "无记录", \
            f"脏时刻 {dirty!r} 被渲染成了「{context._when(dirty)}」—— 它看着像真时刻，必须写「无记录」"
    # 边界：正好在闸门上（`_EPOCH_FLOOR` = 1e9）**算有效**，早一秒就无效 —— 判据是 `<` 不是 `<=`。
    assert context._when(context._EPOCH_FLOOR) != "无记录", "闸门本身应当被接受（判据是 `<`）"
    assert context._when(context._EPOCH_FLOOR - 1) == "无记录"


def test_status_reason_is_collapsed_and_bounded():
    """失败原因来自异常字符串 / SDK 返回，长度与换行都不可控 ⇒ 入库前折叠 + 截断。

    不截断的话，一次 `300313` 的长 ErrMsg 能把状态卡撑成一段乱码（还会把三条记录挤没）。
    """
    context.reset()
    context.note_frame_fail("第一行\n第二行" + "x" * 500)
    snap = context.status_snapshot()
    assert snap["frame_fail_count"] == 1
    assert "\n" not in snap["frame_fail_reason"], "换行没被折叠（卡片会出现断行）"
    assert len(snap["frame_fail_reason"]) <= context._STATUS_REASON_MAX
    assert snap["frame_fail_reason"].startswith("第一行 第二行")


def test_inbound_heartbeat_is_recorded_by_the_real_hook_callback():
    """心跳必须由**真钩子回调**记下（不是只有函数本身能写）。

    这条还钉住一个语义：**归属查不到也要记心跳** —— `chat_id -> session_id` 查不到正是
    「消息到了但没归到会话」的场景，那时面板可以退化成「最近活跃」，但心跳不能跟着丢。
    """
    context.reset()
    hooks._on_pre_gateway_dispatch(
        event=types.SimpleNamespace(source=types.SimpleNamespace(chat_id="oc_probe")),
        session_store=object())          # 归属查不到（缺 peek_session_id）
    assert context.status_snapshot()["inbound_count"] == 1


def test_inbound_heartbeat_is_the_first_statement_of_the_callback():
    """R9 审计中-3 的正题：心跳必须**记在回调第一行**，不能被兄弟调用的异常吞掉。

    修之前的形态：`note_inbound()` 是同一个 `try` 块的**最后一条语句**，前面还有
    `_compat.lookup_session_id` 与 `_panel.bind_chat_session`（**另外两个模块**的代码）。
    审计 `work/xhb6.py` 用真加载器 + 真钩子派发器实测：绑定正常 ⇒ `inbound_count=1`、
    绑定抛异常 ⇒ **0**（心跳被同一个 `except Exception` 吞掉）。而心跳正是用来回答
    「消息到底到没到插件」的唯一凭据 —— 那一次归因会把用户带到**错误方向**
    （README 旧措辞直接写着「心跳不动 ⇒ 消息根本没到插件」）。

    这里用两半钉住它（都不依赖任何替身指标）：
      ① 归属绑定**真的抛** ⇒ 心跳仍然要记（这是审计的最小复现）；
      ② 载荷缺 `source` / `chat_id` 时**提前 return** ⇒ 心跳更要在 return 之前记下
         （否则那类消息会让用户以为插件整个没收到消息）。
    """
    context.reset()
    # ⚠️ **打桩要打在钩子真正读的那个模块对象上**（`hooks._context`），不是测试文件里那个
    # `context` 名字上：两者**通常是**同一个模块，但一旦不是（双模块对象 —— 见
    # `docs/lessons.md` 里「用加载器那份 context」那条），打在 `context` 上的桩就完全看不见，
    # 断言会读一个永远不变的计数而**假绿**（这条实测踩到过：变异 R9-17 因此四门禁全绿）。
    # 所以下面既断言身份、又直接在 `hooks._context` 上打桩。
    assert hooks._context is context, \
        "钩子读的 context 与测试读的不是同一个模块对象 —— 下面的打桩会看不见"
    real_bind = panel.bind_chat_session
    try:
        def _boom(chat_id, session_id):
            raise RuntimeError("bind boom（另一个模块的 bug，与心跳无关）")

        panel.bind_chat_session = _boom
        hooks._on_pre_gateway_dispatch(
            event=types.SimpleNamespace(source=types.SimpleNamespace(chat_id="oc_hb")),
            session_store=object())
        assert context.status_snapshot()["inbound_count"] == 1, \
            "归属绑定抛异常把心跳一起吞掉了（心跳必须记在它之前）"
    finally:
        panel.bind_chat_session = real_bind

    # ② 载荷不全（三个早退分支）时心跳更要在 return **之前**记下：那类消息**确实到了
    #    插件的钩子回调**，而「心跳动不动」正是判「消息到没到插件」的唯一凭据。
    #    ⚠️ 判据用的是「**归属绑定有没有被调用**」而不是心跳计数（第二轮实测教训）：
    #    计数断言会随「心跳记在哪个位置」变化 —— 把心跳挪到早退分支之前，计数照样对；
    #    而「拿不到 chat_id 就不该去绑归属」是这三个分支的**行为事实**，
    #    任何「在绑归属之前就 return」的改动都会让它变红（变异 `R9-17` 就是这么验的）。
    # ⚠️ **这条判据第一版写错了，留在这里免得下一个人重踩**：我先断言的是「拿不到
    #    `chat_id` 就不该去绑归属」（`panel.bind_chat_session` 不被调用）。实测把那三个
    #    早退分支整个删掉它**照样全绿** —— 因为 `chat_id` 为空时 `session_key_for_source()`
    #    算出的键是空串，`lookup_session_id("")` 直接返回 `""`，于是 bind **本来就不会被调用**。
    #    那条断言守的是一件**恒真**的事（lessons 推论 6/19 的形态）。
    #    正确观测点是**心跳本身**：它是这三个分支唯一的副作用。
    calls = []
    target = hooks._context                    # ⚠️ 必须打在钩子真正读的那个模块对象上
    assert target is context, \
        "钩子读的 context 与测试读的不是同一个模块对象 —— 打在 context 上的桩会看不见"
    real_note = target.note_inbound
    try:
        def _count():
            calls.append(1)
            return real_note()

        target.note_inbound = _count
        # 先自证桩**真的生效**（否则下面的「没被调用」是假的 —— 打错了对象就是这种形状）
        hooks._on_pre_gateway_dispatch(event=types.SimpleNamespace(
            source=types.SimpleNamespace(chat_id="oc_probe2")), session_store=object())
        assert len(calls) == 1, "打桩自证失败：桩没被钩子调用到（打错对象了？）"
        calls.clear()
        for bad in ({"event": None, "session_store": object()},
                    {"event": types.SimpleNamespace(source=None), "session_store": object()},
                    {"event": types.SimpleNamespace(source=types.SimpleNamespace(chat_id="")),
                     "session_store": object()}):
            hooks._on_pre_gateway_dispatch(**bad)
        assert len(calls) == 3, \
            f"载荷不全的入站消息没记心跳（{len(calls)}/3：它确实到了插件的钩子回调）"
    finally:
        target.note_inbound = real_note


def test_frame_ledger_counts_only_real_card_writes():
    """账本跟着**真的写出去的**动作走：建卡 / 正文 / 收尾算，**节流跳过与去重不算**。

    判据分两半：
      ① 一次正常回合（seed 建卡 → 正文 → 去重 → 收尾）应记 3 次；
      ② 窗口内的中间帧（`skipped`）**一次都不许记** —— 它一个字节都没写，
         记成成功会让长回合里的状态卡自信地说「一直在写」。
    """
    defaults = dict(adapter._DEFAULTS)
    old_interval = adapter._STREAM_MIN_INTERVAL
    context.reset()
    try:
        raw = _make()
        _wire_patch(raw)
        adapter._STREAM_MIN_INTERVAL = 0.0
        assert _run(raw.send_stream_frame("", finalize=False, chat_id="oc_1",
                                          turn_id="t9")) is True      # seed 建卡
        assert context.status_snapshot()["frame_ok_count"] == 1, "建卡没记账"
        assert _run(raw.send_stream_frame("你好", finalize=False, chat_id="oc_1",
                                          turn_id="t9")) is True
        assert context.status_snapshot()["frame_ok_count"] == 2, "正文帧没记账"
        assert _run(raw.send_stream_frame("你好", finalize=False, chat_id="oc_1",
                                          turn_id="t9")) is True      # 文本没变 ⇒ 去重
        assert context.status_snapshot()["frame_ok_count"] == 2, "去重帧（没写）被记成了写卡"
        # 节流窗口内的中间帧：把窗口开到很大，再写一段**新**文本 ⇒ 走 skipped 分支
        adapter._STREAM_MIN_INTERVAL = 3600.0
        assert _run(raw.send_stream_frame("你好，世界", finalize=False, chat_id="oc_1",
                                          turn_id="t9")) is True
        assert context.status_snapshot()["frame_ok_count"] == 2, "被节流跳过（没写）却记了账"
        adapter._STREAM_MIN_INTERVAL = 0.0
        assert _run(raw.send_stream_frame("最终答案", finalize=True, chat_id="oc_1",
                                          turn_id="t9")) is True      # 收尾替换
        assert context.status_snapshot()["frame_ok_count"] == 3, "收尾帧没记账"
    finally:
        adapter._STREAM_MIN_INTERVAL = old_interval
        context.reset()
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_ledger_counts_send_edit_stop_clarify_and_never_double_counts():
    """R9 审计中-1：**每一次真的写卡**都要记，而且**同一帧只能记一次**。

    背景（审计 `work/ledger_probe.py` A 段实测）：修之前账本的 7 个调用点全在
    ``_ld_stream_frame`` 里，于是非 native 的官方卡片路径 —— ``send()`` 首发、
    ``edit_message()`` 非流式帧、``send_clarify`` 澄清卡、``/stop`` 的中止重绘 ——
    **真的写了卡却一个数都不加**：`send()` 成功那一刻，状态卡照样说三行全「无记录」，
    而用户 DM 里明明躺着一张卡（README 当时还写着「建卡（首发 / 建实体）…都算」）。
    修法是**把记账下移到低层写卡收口点**（``_ld_send_card`` / ``_ld_update_card``）：
    由构造保证「一次写一笔」，而不是靠「记得在调用方也加一句」。

    判别力：突变 `R9-9`（`_ld_send_card` 成功却不记）/ `R9-10`（`_ld_update_card` 成功却不记）
    必须让这条红。**反向**也要钉住：同一帧不能记两笔（`R9-12`）。
    """
    defaults = dict(adapter._DEFAULTS)
    context.reset()
    try:
        # ── ① `send()` 首发：这是「非 native 或掉回非 native」时用户看到的那张卡
        raw = _make()
        _wire_patch(raw)
        assert _run(raw.send("oc_1", "回答正文")) is not None
        assert context.status_snapshot()["frame_ok_count"] == 1, \
            f"`send()` 真的发出了一张卡却没记账（审计的实测形态）：{context.status_snapshot()}"
        # 反面对偶：**发失败回落纯文本**不许记（那时一个字节都没写出去）—— 否则
        # 「写卡」这一行会在卡片全坏的情况下照样往上涨。
        refused = _make()
        refused._fail_cards = True
        assert _run(refused.send("oc_2", "回答正文")) is not None      # 回落 super() 的纯文本
        assert context.status_snapshot()["frame_ok_count"] == 1, \
            "卡没发出去（回落纯文本）却记成了「写卡」"

        # ── ② 同一次写不许记两笔：`_ld_send_card` 与调用方都在记就会变成 +2
        #    （这正是「把记账挪到低层」最容易引入的新病）
        assert context.status_snapshot()["frame_ok_count"] == 1, \
            f"同一次发送被记了不止一笔：{context.status_snapshot()}"

        # ── ③ 非流式 `edit_message()`：我们自己发的卡被改写（非 native 流式的每一帧都走它）
        _wire_patch(raw)
        assert _run(raw.edit_message("oc_1", "om_card_1", "改过的正文")) is not None
        assert context.status_snapshot()["frame_ok_count"] == 2, \
            f"非流式 `edit_message` 改写了卡却没记账：{context.status_snapshot()}"

        # ── ④ `/stop` 的中止重绘：真的 patch 出去了一张中止色的卡
        assert _run(raw.interrupt_session_activity("sess-1", "oc_1")) is None
        assert context.status_snapshot()["frame_ok_count"] == 3, \
            f"`/stop` 的中止重绘写了卡却没记账：{context.status_snapshot()}"

        # ── ⑤ 澄清卡 `send_clarify` 也是一张真的发出去了的卡
        _fake_clarify_gateway([])
        try:
            assert _run(raw.send_clarify("oc_3", "选一个？", ["A", "B"], "c1", "sess-1")) is not None
        finally:
            _drop_fake_clarify_gateway()
        assert context.status_snapshot()["frame_ok_count"] == 4, \
            f"澄清卡发出去了却没记账：{context.status_snapshot()}"

        # ── ⑥ **同一帧绝不记两次**（R9 审计中-1 最要紧的那半）：
        #    `DEGRADE` 车道那一帧**既写了元素、又整卡 patch 了一次**；
        #    以及 cardkit 的 seed 帧 = `card.create` + 发实体卡**两次网络调用**。
        #    两种都只能 +1（账本数的是「帧」，不是写动作次数 —— 口径见中-5）。
        def _degrade_requests():
            return types.SimpleNamespace(
                create_card=lambda card_json: types.SimpleNamespace(
                    request_body={"card_json": card_json}),
                write_element=lambda cid, eid, content, seq, uuid_value: types.SimpleNamespace(
                    card_id=cid, element_id=eid,
                    request_body=types.SimpleNamespace(content=content, sequence=seq,
                                                       uuid=uuid_value)),
                batch_update=lambda cid, actions, seq, uuid_value: types.SimpleNamespace(
                    card_id=cid,
                    request_body=types.SimpleNamespace(actions=json.dumps(actions),
                                                       sequence=seq, uuid=uuid_value)),
                settings_card=lambda cid, payload, seq, uuid_value: types.SimpleNamespace(
                    card_id=cid,
                    request_body=types.SimpleNamespace(settings=payload, sequence=seq,
                                                       uuid=uuid_value)),
                send_entity=lambda receive_id, card_id: types.SimpleNamespace(
                    receive_id=receive_id, card_id=card_id,
                    request_body=types.SimpleNamespace(
                        content=json.dumps({"type": "card", "data": {"card_id": card_id}}),
                        msg_type="interactive", uuid=f"ld-msg-{card_id}")),
                reply_entity=lambda reply_to, card_id: types.SimpleNamespace(
                    reply_to=reply_to, card_id=card_id,
                    request_body=types.SimpleNamespace(
                        content=json.dumps({"type": "card", "data": {"card_id": card_id}}),
                        msg_type="interactive", uuid=f"ld-msg-{card_id}",
                        reply_in_thread=False)),
            )

        class _Resp:
            def __init__(self, code=0, msg=None, **data):
                self.code = code
                self.msg = msg if msg is not None else ("success" if code == 0 else "boom")
                self.data = types.SimpleNamespace(**data) if data else None

            def success(self):
                return self.code == 0

        calls = {"create": 0, "send": 0, "content": 0, "batch": 0, "patch": 0}

        class _Card:
            def create(self, request):
                calls["create"] += 1
                return _Resp(0, card_id="ck_d1")

            def batch_update(self, request):
                calls["batch"] += 1
                # 装饰批量拿到**卡级死法**（会话已被关掉）⇒ 这一帧走 DEGRADE 车道
                return _Resp(300309)

            def settings(self, request):
                return _Resp(0)

        class _Elem:
            def content(self, request):
                calls["content"] += 1
                return _Resp(0)          # 正文照常写成功

        class _Msg:
            def create(self, request):
                calls["send"] += 1
                return {"code": 0, "data": {"message_id": "om_d1"}}

            def reply(self, request):
                calls["send"] += 1
                return {"code": 0, "data": {"message_id": "om_d1"}}

            def patch(self, request):
                calls["patch"] += 1
                return {"code": 0, "data": {"message_id": "om_d1"}}

        rawd = _make()
        rawd._client = types.SimpleNamespace(
            cardkit=types.SimpleNamespace(v1=types.SimpleNamespace(card=_Card(),
                                                                   card_element=_Elem())),
            im=types.SimpleNamespace(v1=types.SimpleNamespace(message=_Msg())))
        adapter.configure(native_transport="cardkit", unified_panel=True)
        old_reqs2 = adapter.LarkDeckMixin._ld_ck_requests
        old_interval2 = adapter._STREAM_MIN_INTERVAL
        # ⚠️ 帧节流窗口必须归零：默认 0.25s 下第二帧会被当成「窗口内的中间帧」跳过
        # （实测：不归零时下面的 patch 断言红，而失败原因与记账毫无关系 —— lessons 推论 19
        # 的第一形态：断言被另一个**更早的失败**顺带满足/挡住）。
        adapter._STREAM_MIN_INTERVAL = 0.0
        adapter.LarkDeckMixin._ld_ck_requests = staticmethod(_degrade_requests)
        try:
            context.reset()
            assert _run(rawd.send_stream_frame("", chat_id="oc_d", turn_id="t-d"))
            after_seed = context.status_snapshot()["frame_ok_count"]
            assert after_seed == 1, \
                (f"cardkit 的 seed 帧是两次网络调用（建实体 + 发实体卡）但只是**一帧**，"
                 f"应当 +1，实得 {after_seed}（`card.create`={calls['create']} "
                 f"`message.create`={calls['send']}）")
            # 装饰批量拿到**卡级死法**（`300309`：会话已被关掉）⇒ 这一帧走的是
            # `ok and degrade_code`（正文已经写成功、降级决定落进状态，**本帧不 patch**）。
            # 这一帧=帧路径里那句 `note_frame_ok()` 唯一守着的地方之一，而且它**只写元素**
            # （不经 `_ld_update_card`）—— 撤掉它，下面这个 2 就变成 1。
            assert _run(rawd.send_stream_frame("正文", chat_id="oc_d", turn_id="t-d")), \
                "装饰拿到卡级死法时这一帧仍算成功（正文已落盘）"
            assert calls["content"] == 1 and calls["patch"] == 0, calls
            assert (rawd._ld_stream_get("oc_d:t-d") or {}).get("ck_degrade") == 300309, \
                "前提：这一帧必须真的进了降级分支，否则下面的计数断言守的是别的路径"
            after_element = context.status_snapshot()["frame_ok_count"]
            assert after_element == 2, \
                (f"cardkit 的元素写帧（**不经** `_ld_update_card`）应当 +1（1 → 2），"
                 f"实得 {after_element}（patch {calls['patch']} 次）")
            # 下一帧才是 DEGRADE 的**真 patch**（后续帧走整卡替换）—— 那一帧也只能 +1
            assert _run(rawd.send_stream_frame("正文二", chat_id="oc_d", turn_id="t-d"))
            assert calls["patch"] == 1, calls
            after_degrade = context.status_snapshot()["frame_ok_count"]
            assert after_degrade == 3, \
                (f"降级之后那一帧只做了一次整卡 patch，应当 +1（2 → 3）—— 变成 4 说明"
                 f"**同一帧被记了两次**（帧路径与底层原语都在记，这正是把记账下移最容易引入的病）："
                 f"实得 {after_degrade}")
        finally:
            adapter.LarkDeckMixin._ld_ck_requests = old_reqs2
            adapter._STREAM_MIN_INTERVAL = old_interval2
    finally:
        context.reset()
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_finalize_without_active_stream_returns_false_but_is_not_a_write_failure():
    """**口径钉死**（R9 审计中-2）：`state is None and finalize` 返回 False **不进失败账本**。

    这一支是**正常路径**：没有活跃流可收尾 ⇒ 按官方契约把控制权交还核心，核心回落
    edit/send 把消息正常发出去（一个写请求都没发）。而 `_ld_stream_fail` 记的是
    「**我们真的发起过一次写、而它失败了**」。两者都返回 False，但语义不同：
      * 返回值是给**核心**的信号（False ⇒ 停用本回合 native）；
      * 账本里的「写卡失败」是给**用户排障**的事实。
    合并口径的代价是：一个健康回合会在排障卡上显示一条指向不存在写卡动作的失败原因
    （审计指出 `/stop` 重绘成功后内核若再送 finalize 帧正落这一支）。
    """
    defaults = dict(adapter._DEFAULTS)
    context.reset()
    try:
        raw = _make()
        _wire_patch(raw)
        assert _run(raw.send_stream_frame("正文", finalize=True, chat_id="oc_9",
                                          turn_id="没有这个回合")) is False, \
            "没有活跃流可收尾时必须返回 False（核心据此回落 edit/send）"
        snap = context.status_snapshot()
        assert snap["frame_fail_count"] == 0, \
            f"「没有活跃流可收尾」是正常返回，不该被记成写卡失败：{snap}"
        assert snap["frame_ok_count"] == 0, \
            f"它当然也不该被记成写卡成功（一个字节都没写）：{snap}"
    finally:
        context.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_frame_failure_ledger_keeps_the_reason():
    """帧失败必须**每一次都记账**（日志只有 30 秒一条、且用户看不到日志）。

    用户问「刚才那回合为什么掉成纯文本」，能回答的只有这张状态卡。
    """
    defaults = dict(adapter._DEFAULTS)
    context.reset()
    try:
        refused = _make()
        refused._fail_cards = True          # 建卡失败 ⇒ 走 _ld_stream_fail
        assert _run(refused.send_stream_frame("", finalize=False, chat_id="oc_9",
                                              turn_id="t9")) is False
        snap = context.status_snapshot()
        assert snap["frame_fail_count"] == 1, snap
        assert snap["frame_fail_reason"], "失败原因没留下（状态卡会变成一句废话）"
        assert "失败" in snap["frame_fail_reason"], snap
    finally:
        context.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_cardkit_answer_write_failure_keeps_specific_reason_and_code():
    """CardKit 正文写失败必须保留**具体元素 + 返回码**，不能被外层吞成通用异常。

    病因（P1a）：`_CkOp` 是 NamedTuple，只有 `fail_reason()`；`inner_code()` 只在
    `_CkResult` 上。帧路径把失败 op 当 `_CkResult` 调 ⇒ AttributeError 被
    `send_stream_frame` 外层接住，状态卡只剩「帧处理异常：...」，具体是哪个元素、
    什么码全丢。判据必须两边都钉：① reason 点名 `answer`；② 230099 进错误码账本。
    """
    defaults = dict(adapter._DEFAULTS)
    old_reqs = adapter.LarkDeckMixin._ld_ck_requests
    old_interval = adapter._STREAM_MIN_INTERVAL
    context.reset()
    panel.reset()
    try:
        _calls, client = _mk_cardkit_fake(fail_answer_only=True)
        raw = _make()
        raw._client = client
        adapter._STREAM_MIN_INTERVAL = 0.0
        adapter.LarkDeckMixin._ld_ck_requests = staticmethod(_fake_ck_requests)
        adapter.configure(native_transport="cardkit")
        assert _run(raw.send_stream_frame("", chat_id="oc_p1a", turn_id="t-p1a")), "seed 帧"
        assert not _run(raw.send_stream_frame("正文", chat_id="oc_p1a", turn_id="t-p1a")), \
            "正文元素写失败必须 fail-open 返回 False"
        snap = context.status_snapshot()
        reason = str(snap.get("frame_fail_reason") or "")
        assert "正文" in reason and "answer" in reason, (
            f"失败原因必须点名正文元素，而不是被外层吞成通用异常：{reason!r}")
        assert "帧处理异常" not in reason, reason
        assert int((snap.get("codes") or {}).get("230099", 0)) >= 1, snap
    finally:
        adapter.LarkDeckMixin._ld_ck_requests = old_reqs
        adapter._STREAM_MIN_INTERVAL = old_interval
        context.reset()
        panel.reset()
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()



def _manifest_version() -> str:
    """**独立**解析 plugin.yaml 的版本行（不用被测代码那个正则 —— 那等于自己证自己）。"""
    manifest = _pathlib.Path(_REPO_PARENT) / "larkdeck" / "plugin.yaml"
    text = manifest.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("plugin.yaml 里没有 version 行")



def test_aggregate_diagnosis_reports_facts_without_claiming_healthy():
    """P2 聚合诊断：把跨模块事实压成两行，但**不做「健康/正常」结论**。

    判据分三组（每组都必须有判别力）：
      * 能力/链路行：探测结论、钩子数、命令注册、模块世代；
      * 运行/账本行：入站心跳年龄、写卡帧数、写卡失败、掉回纯文本、错误码；
      * 异常时的 ``⚠️`` 前缀只由**明确事实**触发（未接管 / 钩子不全 / 命令未注册 /
        世代裂脑 / 有失败计数），不作为「一切正常」的反面承诺。
    """
    saved_report = dict(adapter.PROBE_REPORT)
    saved_hooks = dict(adapter.HOOKS)
    saved_command = dict(adapter.COMMAND)
    context.reset()
    try:
        def _full_report():
            adapter.PROBE_REPORT.clear()
            adapter.PROBE_REPORT.update({key: [] for key in compat.PROBE_REPORT_KEYS})
            adapter.PROBE_REPORT.update({
                "hermes_version": "0.21.1",
                "adapter_class": "example.FeishuAdapter",
                "ok": True,
                "adopted": True,
                "session_attribution_ok": True,
                "core_interrupt_lookup": True,
            })

        _full_report()
        adapter.HOOKS.clear()
        adapter.HOOKS.update({name: True for name in hooks.SUBSCRIPTIONS})
        adapter.COMMAND.clear()
        adapter.COMMAND.update({"registered": True, "why": ""})

        lines = adapter._ld_diagnosis_lines()
        assert len(lines) == 2, lines
        card_text = adapter._ld_command_card("status")
        assert lines[0] in card_text and lines[1] in card_text, card_text
        total = len(hooks.SUBSCRIPTIONS)
        assert lines[0].startswith("🩺"), lines[0]
        for token in ("探测=已接管", f"钩子={total}/{total}", "命令=已注册",
                      f"世代={panel.load_seq()}/{panel.latest_load_seq()}"):
            assert token in lines[0], f"聚合能力行少了 {token!r}：{lines[0]!r}"
        assert lines[1].startswith("📊"), lines[1]
        assert "入站=无记录" in lines[1], lines[1]
        assert "写卡=0 帧" in lines[1] and "写卡失败=0" in lines[1], lines[1]
        assert "正常" not in "".join(lines) and "健康" not in "".join(lines), lines

        # 入站心跳的年龄与 status.inbound 同源；这里把记录改成 90 秒前，必须显示「1m前」。
        context.note_inbound()
        context._shared_status()["inbound_at"] = time.time() - 90
        line = adapter._ld_diagnosis_lines()[1]
        assert "1m前" in line, f"入站年龄没有进入聚合行：{line!r}"

        # 运行异常：三种失败计数各自进账本，聚合行必须带 ⚠️ 且如实列数。
        context.note_frame_fail("写卡炸了")
        context.note_plaintext_fallback("fallback 了")
        context.note_response_code(300309)
        line = adapter._ld_diagnosis_lines()[1]
        assert line.startswith("⚠️ 📊"), line
        assert "写卡失败=1" in line and "掉回纯文本=1" in line and "错误码=1" in line, line

        # 能力异常：四种探测负结论 + 钩子不全 + 命令未注册 + 世代裂脑都必须是 ⚠️。
        adapter.PROBE_REPORT["adopted"] = False
        line = adapter._ld_diagnosis_lines()[0]
        assert line.startswith("⚠️ 🩺") and "未接管（覆盖层构造失败）" in line, line
        adapter.PROBE_REPORT["ok"] = False
        line = adapter._ld_diagnosis_lines()[0]
        assert "未接管（必需接口缺失）" in line, line
        adapter.PROBE_REPORT.pop("missing_signal", None)
        line = adapter._ld_diagnosis_lines()[0]
        assert "探测报告缺键" in line, line

        _full_report()
        first_hook = next(iter(adapter.HOOKS))
        adapter.HOOKS.pop(first_hook)
        line = adapter._ld_diagnosis_lines()[0]
        assert line.startswith("⚠️ 🩺") and f"钩子={total - 1}/{total}" in line, line
        adapter.HOOKS[first_hook] = True
        adapter.COMMAND["registered"] = False
        line = adapter._ld_diagnosis_lines()[0]
        assert line.startswith("⚠️ 🩺") and "命令=未注册" in line, line

        # 世代裂脑：本模块加载序号与进程内最新序号不一致时必须报出来。
        saved_load = panel.load_seq
        saved_latest = panel.latest_load_seq
        panel.load_seq = lambda: 1
        panel.latest_load_seq = lambda: 2
        try:
            line = adapter._ld_diagnosis_lines()[0]
            assert line.startswith("⚠️ 🩺") and "世代=1/2" in line, line
        finally:
            panel.load_seq = saved_load
            panel.latest_load_seq = saved_latest

        # 年龄函数的公开入口与 `_dur` 同源；脏值仍然是「无记录」。
        assert context.age_text(None) == i18n.t("status.none")
        assert context.age_text(context._EPOCH_FLOOR - 1) == i18n.t("status.none")
        assert context.age_text(time.time() - 5) in ("4s", "5s"), context.age_text(time.time() - 5)

        # 聚合自身失败时**不许静默少两行**：把失败原因放到卡上（R9 低-2 同源）。
        saved_snapshot = context.status_snapshot
        def _boom_snapshot():
            raise RuntimeError("snapshot boom")

        context.status_snapshot = _boom_snapshot
        try:
            failed = adapter._ld_diagnosis_lines()
            assert len(failed) == 1, failed
            assert "聚合诊断渲染失败" in failed[0] and "snapshot boom" in failed[0], failed
        finally:
            context.status_snapshot = saved_snapshot
    finally:
        adapter.PROBE_REPORT.clear()
        adapter.PROBE_REPORT.update(saved_report)
        adapter.HOOKS.clear()
        adapter.HOOKS.update(saved_hooks)
        adapter.COMMAND.clear()
        adapter.COMMAND.update(saved_command)
        context.reset()


def test_config_command_is_read_only_and_reload_is_all_or_nothing():
    """P2 配置刷新：`config` / `config reload` 只读，读失败时整次取消而不是半刷新。"""
    saved_cfg = dict(adapter._CONFIG)
    saved_ctx = dict(adapter.PLUGIN_CTX)
    try:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(adapter._DEFAULTS)
        official = {"theme": "neutral"}
        adapter.PLUGIN_CTX.clear()
        adapter.PLUGIN_CTX.update({
            "get_config": lambda key, default=None: official.get(key, default),
        })
        text = adapter._ld_command_card("config")
        assert "生效配置" in text, text
        assert "theme = `ap_lite`" in text, text
        # 官方文件是 neutral、本进程内存是 ap_lite ⇒ 必须提示需要 reload，而不是假装一致
        assert "官方文件已改" in text, text

        # 没有官方读取接口：如实说，不假装知道官方设置
        adapter.PLUGIN_CTX["get_config"] = None
        text = adapter._ld_command_card("config")
        assert "未提供 ctx.get_config" in text, text
        assert "无法刷新" in adapter._ld_command_card("config reload"), \
            adapter._ld_command_card("config reload")

        # 异常时全有全无：任一键读取失败 ⇒ 整次取消，内存保持原样（不是文件系统事务）
        def _partially_bad(key, default=None):
            if key == "footer":
                raise OSError("config read failed")
            return official.get(key, default)

        adapter.PLUGIN_CTX["get_config"] = _partially_bad
        before = dict(adapter._CONFIG)
        text = adapter._ld_command_card("config reload")
        assert "取消" in text and "footer" in text, text
        assert dict(adapter._CONFIG) == before, "读失败时不许留下半套新配置"

        # 成功路径：官方设置覆盖默认值；不存在的键回默认值
        adapter._CONFIG["cards"] = False      # 模拟「官方已删掉这个键」的陈旧内存
        adapter.PLUGIN_CTX["get_config"] = (
            lambda key, default=None: {"theme": "neutral", "footer": False}.get(key, default))
        text = adapter._ld_command_card("config reload")
        assert "已刷新" in text, text
        assert adapter._cfg_raw("theme") == "neutral", adapter._cfg_raw("theme")
        assert adapter._cfg("footer") is False, adapter._cfg("footer")
        assert adapter._cfg("cards") is True, "官方已删掉的键必须回默认值（否则陈旧值永远留在内存）"
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(saved_cfg)
        adapter.PLUGIN_CTX.clear()
        adapter.PLUGIN_CTX.update(saved_ctx)
        adapter._apply_metrics_config()


def test_config_set_has_no_chat_write_path():
    """P2 安全收口（审计 B1）：聊天侧不存在配置写入命令；`config set` 只得到解释，绝不写。"""
    saved_cfg = dict(adapter._CONFIG)
    saved_ctx = dict(adapter.PLUGIN_CTX)
    calls = []
    try:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(adapter._DEFAULTS)
        adapter.PLUGIN_CTX.clear()
        adapter.PLUGIN_CTX.update({
            "get_config": lambda key, default=None: default,
            # 故意放一个可调用的写接口：代码若偷偷调用，这里会记下来。
            "set_config": lambda key, value: calls.append((key, value)),
        })
        before = dict(adapter._CONFIG)
        text = adapter._ld_command_card("config set footer false")
        assert "没有配置写入命令" in text, text
        assert calls == [], calls
        assert dict(adapter._CONFIG) == before, "聊天侧 set 竟然改了内存配置"
        for arg in ("config SET footer false", "config write footer false"):
            text = adapter._ld_command_card(arg)
            assert "没有配置写入命令" in text, text
            assert calls == [], text
        help_text = adapter._ld_command_card("help")
        assert "config set" not in help_text, help_text
        assert "没有写入命令" in help_text or "只读" in help_text, help_text
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(saved_cfg)
        adapter.PLUGIN_CTX.clear()
        adapter.PLUGIN_CTX.update(saved_ctx)
        adapter._apply_metrics_config()


def test_reload_clears_context_override_and_discloses_env_shadow():
    """P2 审计 A1/A5/C3：reload 要清掉真实的运行时覆盖；空 env 不能吞提示；env 遮蔽要说清。"""
    saved_cfg = dict(adapter._CONFIG)
    saved_ctx = dict(adapter.PLUGIN_CTX)
    saved_footer = os.environ.get("LARKDECK_FOOTER")
    saved_theme = os.environ.get("LARKDECK_THEME")
    try:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(adapter._DEFAULTS)
        adapter._CONFIG["theme"] = "ap_lite"
        official = {"theme": "neutral", "footer": False}
        adapter.PLUGIN_CTX.clear()
        adapter.PLUGIN_CTX.update({
            "get_config": lambda key, default=None: official.get(key, default),
            "set_config": lambda key, value: (_ for _ in ()).throw(
                AssertionError("read-only reload 不许调用 set_config")),
        })
        # A5：空字符串 env 在 `_cfg_raw` 里等效「未设」，不能因此吞掉「官方已改」提示。
        # ⚠️ 让**只有 theme** 这一个键与官方不同：否则 footer 的差异也会让卡片出现同一句提示，
        # 这条判据就失去判别力（变异 P2-19 实测全绿）。
        adapter._CONFIG["footer"] = False
        os.environ["LARKDECK_THEME"] = ""
        text = adapter._ld_command_card("config")
        assert "官方文件已改" in text, f"空 env 抑制了 reload 提示（A5）：{text!r}"

        # A1：先制造一个真实的运行时覆盖，reload 必须把它清掉（而不是只改内存值）。
        adapter._CONFIG["context_max_override"] = 12345
        adapter._apply_metrics_config()
        assert context._MAX_OVERRIDE_BOX[0] == 12345, context._MAX_OVERRIDE_BOX
        # C3：footer 被非空 env 遮蔽时，reload 必须披露「本进程生效值不变」。
        os.environ["LARKDECK_FOOTER"] = "1"
        text = adapter._ld_command_card("config reload")
        assert context._MAX_OVERRIDE_BOX[0] is None, \
            f"reload 没有清除运行时 context override（A1）：{context._MAX_OVERRIDE_BOX!r}"
        assert adapter._CONFIG["context_max_override"] == 0, adapter._CONFIG
        assert "环境变量" in text, f"reload 没有披露 env 遮蔽（C3）：{text!r}"
        assert "footer" in text, text
    finally:
        for name, value in (("LARKDECK_FOOTER", saved_footer),
                            ("LARKDECK_THEME", saved_theme)):
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        context.set_context_override(None)
        adapter._CONFIG.clear()
        adapter._CONFIG.update(saved_cfg)
        adapter.PLUGIN_CTX.clear()
        adapter.PLUGIN_CTX.update(saved_ctx)
        adapter._apply_metrics_config()


def test_standalone_sender_uses_card_factory_and_falls_back_for_media():
    """P3 cron / 无网关投递：`standalone_sender_fn` 必须先经我们的工厂走卡片层。

    判据分三半（任何一半撤掉都要变红）：
      * 纯文本投递：`factory` 被调用一次、`adapter.send` 收到原 chat / 文本 / thread metadata；
      * 媒体附件：**回落内置 sender**（不静默丢附件），工厂不参与；
      * 任何异常 / 失败结果：按官方契约返回 `{"error": ...}`，绝不抛进 cron 调度器。
    """
    calls: list = []
    factory_calls: list = []
    media_calls: list = []

    class _Res:
        success = True
        message_id = "om_cron"
        error = None

    class _Adapter:
        # 官方 `__init__` 后 `_client` 是 None，standalone 必须先补 client；
        # 这里给一个非 None 值，模拟 `compat.ensure_standalone_client()` 已成功。
        _client = object()

        async def send(self, chat_id, message, metadata=None):
            calls.append((chat_id, message, metadata))
            return _Res()

    def factory(pconfig):
        factory_calls.append(pconfig)
        return _Adapter()

    async def fallback(pconfig, chat_id, message, *, thread_id=None,
                       media_files=None, force_document=False):
        media_calls.append((chat_id, message, thread_id, list(media_files or [])))
        return {"success": True, "channel": "fallback"}

    sender = adapter._make_standalone_sender(factory, fallback)
    out = _run(sender(object(), "oc_1", "hello", thread_id="th_1"))
    assert out.get("success") is True and out.get("message_id") == "om_cron", out
    assert out.get("platform") == "feishu", out
    assert calls == [("oc_1", "hello", {"thread_id": "th_1"})], calls
    assert len(factory_calls) == 1, factory_calls

    # 媒体附件：内置 sender 收完整载荷；工厂不再建第二个适配器
    out = _run(sender(object(), "oc_2", "cap", thread_id=None,
                      media_files=["/tmp/a.png"], force_document=True))
    assert out.get("channel") == "fallback", out
    assert media_calls == [("oc_2", "cap", None, ["/tmp/a.png"])], media_calls
    assert len(factory_calls) == 1, factory_calls

    # 工厂构造失败：error dict，异常正文不泄漏
    def bad_factory(pconfig):
        raise RuntimeError("boom-secret")

    out = _run(adapter._make_standalone_sender(bad_factory, None)(object(), "oc_3", "x"))
    assert "error" in out and "boom-secret" not in out["error"], out

    class _BadAdapter:
        _client = object()          # 过了 client 门，专门验「发送成功但返回失败」这一支

        async def send(self, chat_id, message, metadata=None):
            return types.SimpleNamespace(success=False, error="rejected by feishu",
                                         message_id="")

    out = _run(adapter._make_standalone_sender(lambda pconfig: _BadAdapter(), None)(
        object(), "oc_4", "y"))
    assert "error" in out and "rejected by feishu" in out["error"], out

    # 空文本且无媒体：与内置契约同形的 error dict
    out = _run(sender(object(), "oc_5", "   "))
    assert "error" in out, out



def test_standalone_client_init_is_required_and_falls_back():
    """P3 blocker 回归：standalone 必须补官方同款 SDK client；补不上就先回落内置 sender。

    审计真机复现：官方 `FeishuAdapter.__init__` 把 `_client` 置 None，直接 `adapter.send()`
    返回 `Not connected`。这条同时钉两件事：
      * `compat.ensure_standalone_client()` 真建 client 并写回实例（且幂等）；
      * wrapper 在 client 补不上时**回落内置 sender**，而不是把文本投递整条切断。
    """
    built = []

    class _NoClient:
        _client = None
        _domain_name = "lark"

        def _build_lark_client(self, domain):
            built.append(domain)
            return object()

    fake = _NoClient()
    first = compat.ensure_standalone_client(fake)
    assert first is not None and fake._client is first, fake.__dict__
    assert len(built) == 1, built
    # 幂等：已有 client 不再建第二个
    assert compat.ensure_standalone_client(fake) is first and len(built) == 1, built

    # wrapper：没有可建的 client ⇒ 回落内置；内置返回成功即算送达
    class _Unbuildable:
        _client = None          # 没有 `_build_lark_client`，helper 必然返回 None

    fallback_calls = []

    async def fallback(pconfig, chat_id, message, *, thread_id=None,
                       media_files=None, force_document=False):
        fallback_calls.append((chat_id, message, thread_id, list(media_files or [])))
        return {"success": True, "channel": "builtin"}

    out = _run(adapter._make_standalone_sender(lambda pconfig: _Unbuildable(), fallback)(
        object(), "oc_c", "hello", thread_id="th_c"))
    assert out.get("channel") == "builtin", out
    assert fallback_calls == [("oc_c", "hello", "th_c", [])], fallback_calls

    # media + 无内置 sender：必须有 error（不许静默丢附件，也不许走只发文本的路径）
    out = _run(adapter._make_standalone_sender(lambda pconfig: _Unbuildable(), None)(
        object(), "oc_m", "caption", media_files=["/tmp/x.png"]))
    assert "error" in out and "media" in out["error"], out

def test_command_card_reports_version_transport_and_three_records():
    """`/larkdeck status`：版本**现读清单** + 传输自报 + 钩子 + 三条记录。

    版本这条必须有判别力：把「读 plugin.yaml」换成写死常量（变异 `R9-6`）时，
    这里与清单**独立解析**出来的值一比就红 —— 卡片报错版本号会把排障带偏。
    """
    context.reset()
    saved_hooks = dict(adapter.HOOKS)
    adapter.HOOKS.clear()
    adapter.HOOKS.update({"post_api_request": True, "on_stream_start": False})
    try:
        text = adapter._ld_command_card("")
        version = _manifest_version()
        assert version and version in text, f"卡片没报出清单里的版本：{text!r}"
        assert f"larkdeck v{version}" in text, text
        assert f"传输 {adapter.LarkDeckMixin._ld_transport()}" in text, text
        assert f"钩子 1/{len(hooks.SUBSCRIPTIONS)} 已挂" in text, text
        # R9 审计中-4：**进程级口径必须在卡上**。账本与页脚指标一样是模块级全局
        # （`context._STATUS`），多会话并发时「累计 N 条消息 / N 帧写卡」里可能大部分
        # 来自**别的会话** —— 不写清楚，用户会拿别人的失败原因去查自己的卡。
        # 判据落在**卡片的文本**上（不是落在 i18n 表上）：写在表里但没拼进卡等于没说。
        assert "进程级" in text, f"卡片没写明这些数字是进程级累计（含全部会话）：{text!r}"
        # ⚠️⚠️ **不许整行比对**（审计 X19 实测出来的**假红源**）：`status_lines()` 里有一行是
        #    `⏱ 已运行：2s` 这种**墙钟读数** —— 卡片是上一次调用渲染的，跨过一秒它就与
        #    现在这一行不等 ⇒ 这条断言会**随机**变红。而 `mutate_check` 把「假红」当成
        #    **判别力证据**（结论正好写反），所以它比「断言太松」更危险。
        #    ⇒ 判据分两层：稳定的行**整行**不许少；uptime 那一行只钉**标签**（不钉秒数）。
        _lines = context.status_lines()
        _uptime = [l for l in _lines if "已运行" in l]
        assert len(_uptime) == 1, f"uptime 行应当正好一行：{_uptime}"
        for line in _lines:
            if "已运行" in line:
                continue
            assert line in text, f"少了自检行：{line!r}"
        assert "已运行" in text, f"卡片少了 uptime 那一行（只钉标签，不钉秒数）：{text!r}"
        # 没参数与显式 `status` 必须**同一张卡**（不然 `help` 里写的默认值就是假的）
        assert adapter._ld_command_card("status") == text
        assert adapter._ld_command_card(" STATUS ") == text
    finally:
        adapter.HOOKS.clear()
        adapter.HOOKS.update(saved_hooks)


def test_clarify_silent_paths_emit_toasts_instead_of_nothing():
    """P1b：澄清点击的三条失败路径必须给用户可见反馈，而不是只写 WARNING。

    三条路径：缺 clarify_id / 未授权 / 适配器 loop 未就绪。判据是「调用了 toast 且 key 正确」，
    不是「没抛异常」——本项目已经裁过「恒真空断言」的跟头（lessons 推论 8）。
    """
    raw = _make()
    recorded: list = []

    def _record_toast(*, kind, text_key):
        recorded.append((kind, text_key))
        return {"toast": text_key}

    raw._ld_toast_or_noop = _record_toast          # type: ignore[method-assign]
    action = types.SimpleNamespace(option=None, input_value=None)

    missing = raw._ld_handle_clarify_click(
        event=types.SimpleNamespace(operator=types.SimpleNamespace(open_id="ou_bad")),
        action=action, value={})
    assert missing == {"toast": "clarify.toast_missing_id"}, missing

    unauthorized = raw._ld_handle_clarify_click(
        event=types.SimpleNamespace(operator=types.SimpleNamespace(open_id="ou_bad")),
        action=action, value={"clarify_id": "cid", "answer": "A"})
    assert unauthorized == {"toast": "clarify.toast_unauthorized"}, unauthorized

    raw._loop = None
    unavailable = raw._ld_handle_clarify_click(
        event=types.SimpleNamespace(operator=types.SimpleNamespace(open_id="ou_ok")),
        action=action, value={"clarify_id": "cid", "answer": "A"})
    assert unavailable == {"toast": "clarify.toast_unavailable"}, unavailable
    assert recorded == [("error", "clarify.toast_missing_id"),
                        ("error", "clarify.toast_unauthorized"),
                        ("error", "clarify.toast_unavailable")], recorded


def test_core_interrupt_lookup_detects_renamed_lookup():
    """P1b：核心查找名的静态探测必须能区分在位 / 改名 / 源码不可读。

    这是「核心把 `interrupt_session_activity` 改名 ⇒ `/stop` 后卡片静默不变色」的
    唯一预警信号；不能只是永远 True（那等于没有探测）。
    """
    orig = compat._core_source_text
    try:
        compat._core_source_text = lambda: (
            'def f(adapter):\n'
            '    return getattr(type(adapter), "interrupt_session_activity", None)\n')
        assert compat.core_interrupt_lookup_ok() is True

        compat._core_source_text = lambda: (
            'def f(adapter):\n    return getattr(type(adapter), "renamed_hook", None)\n')
        assert compat.core_interrupt_lookup_ok() is False

        compat._core_source_text = lambda: None
        assert compat.core_interrupt_lookup_ok() is None
    finally:
        compat._core_source_text = orig


def test_text_profile_applies_device_tokens_to_entity_card():
    """P1b：设备字号档位在建卡期写入 config.style + 元素 text_size 引用；off 不动卡片。"""
    card = cards.cardkit_entity_card("正文", "面板推理", panel_tools_text="面板工具",
                                     panel=True, footer_text="ctx 1k/20k")
    original = json.dumps(card, ensure_ascii=False)
    assert cards.apply_text_profile(card, "off") == card
    assert json.dumps(card, ensure_ascii=False) == original, "off 档不许改一个字节"

    assert cards.apply_text_profile(card, "mobile_friendly") is card
    style = card["config"]["style"]["text_size"]
    assert style["ld_body"] == {"default": "normal", "pc": "small", "mobile": "large"}, style
    assert style["ld_panel"] == {"default": "notation", "pc": "notation", "mobile": "notation"}
    assert style["ld_notice"] == {"default": "notation", "pc": "notation", "mobile": "notation"}
    by_id = {}
    def _collect(node):
        if isinstance(node, dict):
            if node.get("element_id"):
                by_id[node["element_id"]] = node
            for value in node.values():
                _collect(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                _collect(item)
    _collect(card)
    def _node(eid):
        try:
            return by_id[eid]
        except KeyError as exc:  # R3-1 变异：面板只建一块 ⇒ 实体卡缺元素
            raise AssertionError(f"实体卡缺少元素 {eid!r}：{sorted(by_id)!r}") from exc

    assert _node(cards.CARDKIT_ANSWER_ID).get("text_size") == "ld_body"
    assert _node(cards.CARDKIT_PANEL_BODY_ID).get("text_size") == "ld_panel"
    assert _node(cards.CARDKIT_PANEL_TOOLS_ID).get("text_size") == "ld_panel"
    assert _node(cards.CARDKIT_FOOTER_ID).get("text_size") == "ld_notice"

    unknown = cards.cardkit_entity_card("正文", "面板", panel_tools_text="工具",
                                        panel=True, footer_text="f")
    before = json.dumps(unknown, ensure_ascii=False)
    assert cards.apply_text_profile(unknown, "no_such_profile") == unknown
    assert json.dumps(unknown, ensure_ascii=False) == before, "未知档位必须 fail-open 不动卡片"


def test_card_builders_apply_text_profile_not_just_the_helper():
    """P1b：字号档位必须真的接到两条建卡路径（普通卡 + CardKit 实体卡），不能只有 helper 自证。"""
    defaults = dict(adapter._DEFAULTS)
    saved_reqs = adapter.LarkDeckMixin._ld_ck_requests
    try:
        adapter.configure(text_profile="mobile_friendly")
        card = adapter.LarkDeckMixin._ld_build_card(
            "正文", streaming=False, panel=None, footer=None)
        try:
            assert card["config"]["style"]["text_size"]["ld_body"]["mobile"] == "large", card["config"]
        except (KeyError, TypeError, IndexError) as exc:  # P1b-8 变异：建卡不接字号档位
            raise AssertionError(f"普通卡没有应用字号档位：{exc!r}; card={card!r}") from exc

        calls, client = _mk_cardkit_fake()
        raw = _make()
        raw._client = client
        adapter.LarkDeckMixin._ld_ck_requests = staticmethod(_fake_ck_requests)
        adapter.configure(native_transport="cardkit")
        assert _run(raw.send_stream_frame("", chat_id="oc_tp", turn_id="t-tp"))
        try:
            entity = json.loads(calls["entity"][0])
        except IndexError as exc:  # CK9 变异：传输写死后根本不建实体卡
            raise AssertionError(f"CardKit 实体卡没有建出来（配置传输未生效）：{exc!r}; calls={calls!r}") from exc
        try:
            assert entity["config"]["style"]["text_size"]["ld_body"]["pc"] == "small", entity["config"]
            assert entity["body"]["elements"][0]["text_size"] == "ld_body", entity["body"]["elements"][0]
        except (KeyError, TypeError, IndexError) as exc:  # P1b-9 变异：实体卡不接字号档位
            raise AssertionError(f"CardKit 实体卡没有应用字号档位：{exc!r}; entity={entity!r}") from exc
    finally:
        adapter.LarkDeckMixin._ld_ck_requests = saved_reqs
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


def test_command_card_says_so_when_the_version_is_unreadable():
    """版本读不到时必须**说出来**（R9 审计低-2）。

    修之前的形态：`_ld_plugin_version()` 读不到清单就返回 `""`，而卡片里
    `f" v{version}" if version else ""` 会让版本段**整段消失** —— 卡片看起来跟一切正常
    一模一样（审计 `work/version_probe.py` 实测：挪走 `plugin.yaml` 后首行只剩
    `🃏 larkdeck · 传输 cardkit …`）。这与本卡片的头号纪律（**没记录就写「无记录」**）
    是同一条：「读不到」和「没有这一段」在用户眼里必须能分辨。
    """
    saved = adapter._PLUGIN_MANIFEST
    try:
        adapter._PLUGIN_MANIFEST = "/nonexistent-dir/plugin.yaml"      # 读不到清单
        assert adapter._ld_plugin_version() == "", "读不到清单时不许编一个版本号"
        text = adapter._ld_command_card("")
        assert "版本读不到" in text, f"版本读不到却什么都没说（静默消失）：{text!r}"
    finally:
        adapter._PLUGIN_MANIFEST = saved
    # 对偶：清单读得到时**不许**出现「版本读不到」（否则这句话会变成一句噪音）
    assert "版本读不到" not in adapter._ld_command_card("")


def test_command_card_surfaces_probe_report_and_never_calls_it_healthy():
    """`/larkdeck status` 必须把能力探测结论上到用户可见渠道。

    P1a 的判据（对应 `plugins-compare.md` §7.6/§7.8 的 S1–S7 可见性）：
      * 未探测 ⇒ 写「未探测」，绝不写「正常」；
      * 已探测且接管成功 ⇒ 覆盖全部 `compat.PROBE_REPORT_KEYS` 的可读摘要；
      * 必需接口缺失 / 覆盖层构造失败 / 报告缺键 ⇒ 分别明说，不能静默。

    ⚠️ 键集合从 `compat.PROBE_REPORT_KEYS` **派生**，不再手写第二份清单 —— 项目裁过
    「同一份键清单抄两处」的跟头（compat.py 的注释就是为此写的）。
    """
    saved = dict(adapter.PROBE_REPORT)
    context.reset()
    try:
        adapter.PROBE_REPORT.clear()
        text = adapter._ld_command_card("status")
        assert "能力探测：未探测" in text, f"未探测时必须说出来：{text!r}"
        assert "正常" not in text, f"没有探测结论时绝不能说「正常」：{text!r}"

        # 从唯一事实来源派生 fixture；新增第 11 个契约键会立刻在这里显现。
        adapter.PROBE_REPORT.update({key: [] for key in compat.PROBE_REPORT_KEYS})
        adapter.PROBE_REPORT.update({
            "hermes_version": "0.21.1",
            "adapter_class": "example.FeishuAdapter",
            "ok": True,
            "adopted": True,
            "missing_optional": ["edit_message"],
            "session_attribution_ok": True,
            "core_interrupt_lookup": True,
        })
        text = adapter._ld_command_card("status")
        for token in ("能力探测", "已接管", "Hermes 0.21.1", "example.FeishuAdapter",
                      "edit_message", "会话归属", "探测契约", "完整", "信号契约",
                      "静态查到核心查找名字面量"):
            assert token in text, f"探测摘要少了 {token!r}：{text!r}"
        assert "正常" not in text, f"探测摘要不许写「正常」：{text!r}"

        # 覆盖层构造失败：探测跑了，但没接管 —— 不能说「已接管」。
        adapter.PROBE_REPORT["adopted"] = False
        text = adapter._ld_command_card("status")
        assert "未接管（覆盖层构造失败）" in text, text
        assert "已接管" not in text, text

        # 必需接口缺失：headline 必须是「未接管（必需接口缺失）」，并列出缺失项。
        adapter.PROBE_REPORT.update({"ok": False,
                                     "missing_required": ["_feishu_send_with_retry"]})
        text = adapter._ld_command_card("status")
        assert "未接管（必需接口缺失）" in text and "_feishu_send_with_retry" in text, text

        # 契约缺键：从键集合删一个，显示层必须自己求差集，而不是只依赖 contract_violation。
        adapter.PROBE_REPORT.pop("missing_signal", None)
        text = adapter._ld_command_card("status")
        assert "缺键" in text and "missing_signal" in text, \
            f"探测契约缺键必须明说：{text!r}"
    finally:
        adapter.PROBE_REPORT.clear()
        adapter.PROBE_REPORT.update(saved)
        context.reset()


def test_build_adapter_missing_required_stores_probe_snapshot_without_adopting():
    """必需接口缺失的**负结论**也必须进快照，status 才能显示「未接管」。

    Phase 1a 审计 A 的 P2：`build_adapter()` 原先在 `if not ok` 早退，快照没写，
    于是 `/larkdeck status` 只能说「未探测（无记录）」——最关键的负面结论反而上不了卡。
    这条用例真的驱动 `build_adapter()`，不复用 `_probe_status_lines` 的手工快照。
    """
    saved_report = dict(adapter.PROBE_REPORT)
    saved_probe = adapter._compat.probe_adapter_class
    saved_bases = dict(adapter._BASE_CLASSES)
    saved_merged = dict(adapter._MERGED_CLASSES)

    def _fake_probe_adapter_class(_cls):
        return False, ["_feishu_send_with_retry"]

    adapter._compat.probe_adapter_class = _fake_probe_adapter_class
    try:
        instance = adapter.build_adapter(StubAdapter, _StubConfig())
        assert type(instance) is StubAdapter, "必需接口缺失时必须原样退回内置适配器"
        report = dict(adapter.PROBE_REPORT)
        assert report.get("ok") is False, report
        assert report.get("adopted") is False, report
        assert "_feishu_send_with_retry" in (report.get("missing_required") or []), report
        text = adapter._ld_command_card("status")
        assert "未接管（必需接口缺失）" in text, text
        assert "_feishu_send_with_retry" in text, text
        assert "未探测（无记录）" not in text, text
    finally:
        adapter._compat.probe_adapter_class = saved_probe
        adapter.PROBE_REPORT.clear()
        adapter.PROBE_REPORT.update(saved_report)
        adapter._BASE_CLASSES.clear()
        adapter._BASE_CLASSES.update(saved_bases)
        adapter._MERGED_CLASSES.clear()
        adapter._MERGED_CLASSES.update(saved_merged)
        context.reset()


def test_build_adapter_success_stores_probe_snapshot():
    """成功接管后快照必须标记 `adopted=True`，否则 status 会把「没接管」说成「未探测」。

    这条用例真的跑 `build_adapter(StubAdapter, ...)`；如果成功路径的
    `PROBE_REPORT.clear()/update(report)` 被撤掉，状态卡会永远显示「未探测」。
    """
    saved_report = dict(adapter.PROBE_REPORT)
    saved_bases = dict(adapter._BASE_CLASSES)
    saved_merged = dict(adapter._MERGED_CLASSES)
    try:
        instance = adapter.build_adapter(StubAdapter, _StubConfig())
        assert type(instance) is not StubAdapter, "覆盖层必须真的接管成功"
        report = dict(adapter.PROBE_REPORT)
        assert report.get("ok") is True, report
        assert report.get("adopted") is True, report
        assert set(compat.PROBE_REPORT_KEYS) <= set(report), \
            f"成功快照必须包含全部契约键：{sorted(set(compat.PROBE_REPORT_KEYS) - set(report))}"
        text = adapter._ld_command_card("status")
        assert "已接管" in text, text
    finally:
        adapter.PROBE_REPORT.clear()
        adapter.PROBE_REPORT.update(saved_report)
        adapter._BASE_CLASSES.clear()
        adapter._BASE_CLASSES.update(saved_bases)
        adapter._MERGED_CLASSES.clear()
        adapter._MERGED_CLASSES.update(saved_merged)
        context.reset()



def test_command_card_help_states_the_queue_caveat():
    """help 必须写明**飞书网关里生成期间命令会被排队**（命令派发只挂核心的 idle 路径）。

    ⚠️ R9 审计低-1 修正了措辞：旧文案是一句绝对断言「仅空闲态可用」，而它在 **CLI / TUI 里
    不成立** —— 那两条路径（`cli.py::_run_plugin_slash_command`、
    `tui_gateway/methods_tools.py`）**直接调处理器**，没有忙碌概念。正确的说法要同时说清两半：
    网关里会被排队、CLI/TUI 里可直接执行。断言也照着两半写（只留「排队」会放过「CLI 那半
    被删掉」，用户又会被误导一次）。
    """
    help_text = adapter._ld_command_card("help")
    assert "排队" in help_text and "CLI" in help_text, help_text
    assert "仅空闲态" not in help_text, f"旧的绝对措辞还在（CLI/TUI 里不成立）：{help_text!r}"
    unknown = adapter._ld_command_card("wat")
    assert "wat" in unknown and "排队" in unknown, unknown


def test_command_card_never_raises_even_when_state_read_fails():
    """处理器**绝不能抛**：抛出去会把「查一次状态」变成用户侧的错误提示。

    用一个 `__str__` 会炸的对象造出真实的读取失败（第一条语句就挂）。
    """
    class _Boom:
        def __str__(self):
            raise RuntimeError("boom")

    out = adapter._ld_command_card(_Boom())
    assert isinstance(out, str) and out, "处理器没返回任何文本"
    assert "状态读取失败" in out and "boom" in out, out


def test_command_card_never_raises_even_when_the_error_itself_is_unprintable():
    """兜底里的兜底（R9 审计低-4）：**连 `str(异常)` 都抛**时也不许穿透。

    修之前的形态：`except Exception as exc` 里直接 `_i18n.t("cmd.failed", error=str(exc))`，
    而 `str(raw_args)` 抛出的那个异常对象如果自己的 `__str__` 也抛，兜底就**再抛一次** ——
    穿透到核心，用户看到的是「什么反应都没有」（核心只记一条 WARNING，又是静默）。
    这条断言直接打在「穿透」这件事上：任何返回值都必须存在，而且要是给人看的话。
    """
    class _Unprintable(Exception):
        def __str__(self):
            raise RuntimeError("连 __str__ 都炸")

    class _Arg:
        def __str__(self):
            raise _Unprintable()

    try:
        out = adapter._ld_command_card(_Arg())
    except Exception as exc:  # R9-22 变异：连 str(异常) 都抛时会穿透处理器
        raise AssertionError(f"处理器抛穿到调用方了（用户什么也看不到）：{exc!r}") from exc
    assert isinstance(out, str) and out, "处理器抛穿到调用方了（用户什么也看不到）"
    assert "状态读取失败" in out, out
    assert "读不出失败原因" in out, f"读不出原因时必须如实说读不出，不许装成别的：{out!r}"



# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 14. R8①② 点击路径：内联换卡（带能力探测）+ 失败态只弹 toast
#
# 这一层跑在 **SDK 回调线程**上：抛一次就是把「别人的卡」（审批卡等）的点击整条炸掉。
# 所以每条都必须能在「核心那侧不支持 / SDK 缺东西」时**退回正确但朴素的行为**。
# --------------------------------------------------------------------------- #
def _clarify_click_data(**value: Any):
    """造一次澄清点击载荷（默认是**已授权**的按钮点击）。"""
    payload = {"larkdeck_action": "clarify", "clarify_id": "cid-r8", "answer": "B 方案",
               "question": "选哪个？"}
    payload.update(value)
    return types.SimpleNamespace(
        event=types.SimpleNamespace(
            action=types.SimpleNamespace(value=payload, option=None, options=None,
                                         input_value=None),
            operator=types.SimpleNamespace(open_id="ou_ok")),
    )


def _fake_toast_classes(box: list):
    """哑的 ``(P2CardActionTriggerResponse, CallBackToast)`` 替身。

    ⚠️ 用它而不是真 SDK：单测**零 Hermes 依赖**（系统解释器上没有 ``lark_oapi``），
    而这里要验的只是「我们把字段填对了吗」—— 不是 SDK 能不能 import。
    """
    class _Toast:
        def __init__(self) -> None:
            self.type: Any = None
            self.content: Any = None
            self.i18n: Any = None

    class _Resp:
        def __init__(self) -> None:
            self.toast: Any = None
            self.card: Any = None
            box.append(self)

    return _Resp, _Toast


def _inject_toast_classes(raw: Any, box: list) -> None:
    """把哑 toast 类**挂在实例上**（不是类上）。

    ⚠️ 类级打补丁在别处会失效：适配器类是 ``merged_class()`` 造出来的**另一个类**
    （基类是 ``LarkDeckMixin``），进程里一旦建过、就按 base_cls 缓存；打在 Mixin 上虽然
    经 MRO 仍能被查到，但这条链上还有「实例属性 → 类属性」的其它覆盖点，曾经在整份套件里
    排到后面时**静默失效**（点击走到内置回落、断言说「没有提示」）。挂实例上不可能歧义。
    """
    raw._ld_toast_classes = lambda: _fake_toast_classes(box)


def test_clarify_failed_submit_toasts_but_never_swaps_the_card():
    """提交未生效 ⇒ **只弹 toast、绝不换卡**。

    为什么「绝不换卡」是硬要求：这一条最常见的触发者是**一次迟到的重复点击**
    （用户点了两下 / 网络重放）。那一瞬间卡片可能已经被前一次点击换成了「已确认」——
    若这里回一张「待答 + 失败」，用户就会看到自己确认过的卡被退回待答，而答案其实
    早已送达 agent。toast 能告知失败，又不可能覆盖卡片状态。
    """
    raw = _make()
    box: list = []
    _inject_toast_classes(raw, box)
    _fake_clarify_gateway([], commit=False)
    try:
        out = raw._on_card_action_trigger(_clarify_click_data())
        assert all(card is None for card in raw.card_updates), \
            f"失败态不许换卡（换卡会把已确认的卡退回待答）：{raw.card_updates!r}"
        assert box[-1].card is None, "toast 响应里也不许夹带卡片"
        assert out is not None and out != "SUPER_RESULT", \
            f"必须是我们自己的响应（不是交回内置）：{out!r}"
        assert box and box[-1].toast is not None, "失败必须留下提示（静默不动会被当成没反应）"
        toast = box[-1].toast
        assert toast.type == "error", f"失败提示应当是 error 类型：{toast.type!r}"
        assert toast.content == i18n.t("clarify.toast_failed"), toast.content
        # toast 也要双语文案（跟随客户端语言），且两种语言的文案必须真的不同
        assert set(toast.i18n or {}) == {"zh_cn", "en_us"}, \
            f"toast 缺 i18n 映射（非中文客户端会看到中文）：{toast.i18n!r}"
        assert toast.i18n["en_us"] != toast.i18n["zh_cn"], "两语言文案一样等于没本地化"
    finally:
        _drop_fake_clarify_gateway()


def test_clarify_other_toasts_and_keeps_the_card():
    """「其他（我直接输入）」：卡片保持原样（用户要在它上面继续输入），用 toast 说下一步。"""
    raw = _make()
    box: list = []
    _inject_toast_classes(raw, box)
    _fake_clarify_gateway([])
    try:
        raw._on_card_action_trigger(_clarify_click_data(answer=cards.OTHER_VALUE))
        assert all(card is None for card in raw.card_updates), "「其他」要保留输入卡，不许换掉"
        assert box and box[-1].toast is not None, "要给出下一步提示"
        assert box[-1].toast.type == "info", box[-1].toast.type
        assert box[-1].toast.content == i18n.t("clarify.toast_typing"), box[-1].toast.content
    finally:
        _drop_fake_clarify_gateway()


def test_clarify_rejected_text_toasts_and_keeps_the_card():
    """输入框里的内容没被接受 ⇒ 卡片保持原样 + 一条 error toast。

    ⚠️ 这里钉的是 **`NO_PENDING`** 那条判定（下面的假网关注册表是空的 ⇒ 解析必返
    `NO_PENDING`），所以文案必须是「已处理或已过期」那条 —— **不许是「请重试」**
    （R8 审计低-2：这条澄清已经没了，重试永远不会成功）。
    `REJECTED_*` 那条判定由图底部的 `test_clarify_rejected_selection_asks_for_a_retry` 钉。
    """
    raw = _make()
    box: list = []
    _inject_toast_classes(raw, box)
    _fake_clarify_gateway([])          # 网关注册表是空的 ⇒ 解析必返 NO_PENDING
    try:
        data = _clarify_click_data()
        data.event.action.tag = "input"
        data.event.action.input_value = "我想先观察一下"
        raw._on_card_action_trigger(data)
        assert all(card is None for card in raw.card_updates), "没被接受就不能回填「已答复」卡"
        assert box and box[-1].toast is not None, "要给出提示"
        assert box[-1].toast.type == "error", box[-1].toast.type
        assert box[-1].toast.content == i18n.t("clarify.toast_no_pending"), box[-1].toast.content
        # 判据落在**语义**上，不只落在「等于某个字符串」上（中-1 的同一课）：
        # NO_PENDING 的正确说法里不许出现「重试」这种不可能的邀请。
        assert "重试" not in box[-1].toast.content, \
            f"这条澄清已经没了，重试永远不会成功，不许邀请用户重试：{box[-1].toast.content!r}"
        assert "无需重复" in box[-1].toast.content, box[-1].toast.content
    finally:
        _drop_fake_clarify_gateway()


def test_clarify_rejected_selection_asks_for_a_retry():
    """`REJECTED_SELECTION`（选项题收到一个对不上的答案）⇒ 这才是**可以重试**的那一种。

    与上面那条成对：低-2 要求的正是「4 种判定不许塌缩成一句」——
    `REJECTED_*` 换个说法/选项就能救回来，`NO_PENDING` 不能。
    """
    raw = _make()
    box: list = []
    _inject_toast_classes(raw, box)
    # 真网关的 `resolve_gateway_clarify`，但**不带**解析函数 ⇒ clarify_text_answer 先
    # 返回 NO_PENDING。要拿到 REJECTED_SELECTION，得让核心的解析器说「无效选择」。
    fake_tools, fake_cg = _fake_clarify_gateway([])
    fake_cg._entries = {"cid-r8": types.SimpleNamespace(event=threading.Event())}

    def _coerce(entry, body):
        return None, "invalid_selection"

    fake_cg._coerce_text_response_detailed = _coerce
    try:
        data = _clarify_click_data()
        data.event.action.tag = "input"
        data.event.action.input_value = "Z 方案"
        raw._on_card_action_trigger(data)
        assert box and box[-1].toast is not None, "要给出提示"
        assert box[-1].toast.type == "error", box[-1].toast.type
        assert box[-1].toast.content == i18n.t("clarify.toast_rejected"), box[-1].toast.content
        assert "重试" in box[-1].toast.content or "换" in box[-1].toast.content, \
            f"这一种是能救回来的，应当让用户换个说法/选项：{box[-1].toast.content!r}"
        assert box[-1].toast.content != i18n.t("clarify.toast_no_pending"), \
            "两种判定不许塌缩成一句（低-2）"
    finally:
        _drop_fake_clarify_gateway()


def test_clarify_failed_toast_never_invites_an_impossible_retry():
    """失败 toast 的**语义**判据：它不许邀请用户做一件不可能成功的事（R8 审计中-1）。

    事实（Hermes ``tools/clarify_gateway.py:90-98``）：``resolve_gateway_clarify`` 返回
    ``False`` 的条件**只有两条** —— entry 不存在，或 ``entry.event.is_set()`` 已经解开。
    也就是说走到这条提示时，这条澄清**已经**被处理掉或根本不存在，**再点一次永远不可能成功**
    （clarify_id 是每张卡现生成的，不会被重新登记）。

    而这句话最常见的触发者是「一次迟到的重复点击」：那一瞬间卡片**已经**被前一次点击换成
    「已确认」，于是用户同时看到「卡片 = 已确认」+「toast = 请重试」两条互相矛盾的信息，
    合理的反应是再点一次（再吃一条同样的错话），或者把答案**用文字再发一遍** ——
    后者会变成一条新的用户消息进会话，而 agent 那边其实早就收到答案了。

    ⚠️ **为什么断言不写成 `== i18n.t("clarify.toast_failed")`**：那种写法对**文案内容**
    一句话都不说 —— 把文案改回「请重试」它照样绿（那正是这条发现的成因）。
    所以这里的判据是**语义的**：① 不许出现「重试」；② 必须说「无需重复」；
    ③ 英文同理（两种语言都要过，因为客户端按语言挑一份）。
    """
    raw = _make()
    box: list = []
    _inject_toast_classes(raw, box)
    _fake_clarify_gateway([], commit=False)
    try:
        raw._on_card_action_trigger(_clarify_click_data())
        assert box and box[-1].toast is not None, "失败必须留下提示"
        toast = box[-1].toast
        try:
            zh = toast.i18n.get("zh_cn") or ""
            en = toast.i18n.get("en_us") or ""
        except AttributeError as exc:  # R8-4 变异：toast 只给 content 不给 i18n
            raise AssertionError(f"失败 toast 必须带双语 i18n：{exc!r}; toast={toast!r}") from exc
        assert "重试" not in zh, f"再点一次永远不可能成功，不许邀请重试：{zh!r}"
        assert "retry" not in en.lower(), f"英文同理不许邀请重试：{en!r}"
        assert "无需重复" in zh, f"必须明确告诉用户不必重复点击：{zh!r}"
        assert "no need to" in en.lower(), f"英文同理：{en!r}"
        # 中英两段都必须与 content 一致（客户端可能只认其中一份）
        assert toast.content in (zh, en), toast.content
    finally:
        _drop_fake_clarify_gateway()


def test_clarify_failed_priority_beats_other_hint():
    """「提交未生效」必须**优先于**「其他（我直接输入）」提示（R8 审计中-3）。

    载荷刻意构造成「`is_other` **且** `committed=False`」—— 把
    ``if not committed:`` 与 ``if is_other:`` 的先后换过来（例如
    ``if not committed and not is_other:``）四门禁曾经全绿。

    代价是**提示说错话**：点「其他」而这条澄清已失效 ⇒ 用户被告知「去输入框打字」，
    于是他打字 —— 而网关注册表里已经没有这条澄清，``clarify_text_answer`` 会返回
    ``NO_PENDING``，他打的字会变成一条**普通聊天消息**（更坏的是：他以为自己答上了）。
    所以判据是「用户被告知的是**失败**，而不是**去打字**」。
    """
    raw = _make()
    box: list = []
    _inject_toast_classes(raw, box)
    _fake_clarify_gateway([], commit=False)
    try:
        raw._on_card_action_trigger(_clarify_click_data(answer=cards.OTHER_VALUE))
        assert box and box[-1].toast is not None, "要给出提示"
        toast = box[-1].toast
        assert toast.type == "error", f"这是失败态，不该是 {toast.type!r}"
        assert toast.content == i18n.t("clarify.toast_failed"), \
            f"失效的澄清上点「其他」是在提交（不是等待打字），必须报失败：{toast.content!r}"
        assert toast.content != i18n.t("clarify.toast_typing"), "不许让用户去打字（打了也白打）"
        assert "输入框" not in toast.content, \
            f"骗用户去打字等于把他的答案变成一条普通聊天消息：{toast.content!r}"
        assert all(card is None for card in raw.card_updates), "失败态不许换卡"
    finally:
        _drop_fake_clarify_gateway()


def test_clarify_empty_submit_no_longer_silent():
    """空提交（``mode == "none"``）**不许静默**（R8 审计中-2）。

    2.0 卡的 ``input`` 组件 ``behaviors.value`` 里**刻意没有** ``answer`` 键（模式由载荷
    推导，见 ``cards.clarify_card_2``），所以三条入口都落进 ``mode == "none"``：
      ① 输入框空着回车（``input_value=''``）；② 只输空白（``'   '``）；
      ③ 多选把勾选**全部取消**再提交（``options=[]`` —— 而 ``options=['A']`` 走 multi，
         所以「空/非空」这个判据**代码可判**，不需要真机）。
    以前这一支是 ``_ld_card_response_safe()``：**无 toast、无换卡**，用户屏幕上一个像素
    都不动 —— 本项目头号失败模式（静默），而且落在**默认方言（2.0）**上。

    ⚠️ **留白（审计明确留下的，我没能核实）**：「空输入框按回车，飞书客户端到底发不发
    ``card.action.trigger``」这半边**只有真机能答**。若客户端**不发**回调，这条分支就是
    死代码（属低而非中）；若发一个 ``input_value=''`` 的回调，它就是真缺口。本用例证明的
    只是「**收到**一个解析不出来的空点击时我们不再静默」，**不证明**它在真机上会被触达。
    """
    raw = _make()
    box: list = []
    _inject_toast_classes(raw, box)
    resolved: list = []
    _fake_clarify_gateway(resolved)
    try:
        # ① 输入框空着回车 / ② 只输空白：三种取值字段全空，value 里也没有 answer
        for label, action in (
            ("input 空着回车", types.SimpleNamespace(
                value={"larkdeck_action": "clarify", "clarify_id": "cid-r8",
                       "session_key": "sk-1", "question": "选哪个？"},
                option=None, options=None, input_value="")),
            ("input 只有空白", types.SimpleNamespace(
                value={"larkdeck_action": "clarify", "clarify_id": "cid-r8",
                       "session_key": "sk-1", "question": "选哪个？"},
                option=None, options=None, input_value="   ")),
            # ③ 多选把勾选全部取消：官方形状是 action.options（复数），空列表
            ("多选全取消 options=[]", types.SimpleNamespace(
                value={"larkdeck_action": "clarify", "clarify_id": "cid-r8",
                       "session_key": "sk-1", "question": "选哪些？"},
                option=None, options=[], input_value=None)),
        ):
            before = len(box)
            out = raw._on_card_action_trigger(types.SimpleNamespace(
                event=types.SimpleNamespace(
                    action=action, operator=types.SimpleNamespace(open_id="ou_ok"))))
            assert len(box) > before, f"{label}：必须留下提示，不许静默"
            assert box[-1].toast is not None, f"{label}：要弹 toast"
            assert box[-1].toast.type == "error", f"{label}：{box[-1].toast.type!r}"
            assert box[-1].toast.content == i18n.t("clarify.toast_empty"), \
                f"{label}：{box[-1].toast.content!r}"
            assert out is not None and out != "SUPER_RESULT", \
                f"{label}：必须是我们自己的响应（交回内置会把载荷打进会话）"
        assert resolved == [], f"空答案**绝不提交**（提交空串会污染澄清）：{resolved!r}"
        assert all(card is None for card in raw.card_updates), "空提交不许换卡"
    finally:
        _drop_fake_clarify_gateway()


def test_clarify_other_hint_matches_the_dialect():
    """「其他」提示按方言选文案：1.0 卡上**没有输入框**，不许提「输入框」（R8 审计低-3）。

    1.0 澄清卡（``cards._clarify_elements``）只有按钮 + 一个「其他（我直接输入）」按钮；
    2.0 卡才有 ``input`` 组件。在 1.0 卡上说「请在输入框里输入答案」是错的 ——
    用户会去屏幕上找一个不存在的东西。
    """
    defaults = dict(adapter._DEFAULTS)
    raw = _make()
    box: list = []
    _inject_toast_classes(raw, box)
    _fake_clarify_gateway([])
    try:
        for dialect, must_have, must_not in (
            ("2.0", "输入框", None),
            ("1.0", "回复文字", "输入框"),
        ):
            adapter._CONFIG["clarify_dialect"] = dialect
            before = len(box)
            raw._on_card_action_trigger(_clarify_click_data(answer=cards.OTHER_VALUE))
            assert len(box) > before, f"{dialect}：要给出提示"
            text = box[-1].toast.content
            assert must_have in text, f"{dialect} 方言的提示文案不对：{text!r}"
            if must_not:
                assert must_not not in text, \
                    f"{dialect} 卡上根本没有输入框，不许提它：{text!r}"
        # 两种方言的文案必须真的不同，否则「按方言选」等于没做
        assert len({b.toast.content for b in box}) == 2, \
            f"两种方言给出了同一句话（等于没分流）：{[b.toast.content for b in box]!r}"
    finally:
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        _drop_fake_clarify_gateway()


def test_success_click_swaps_the_card_inline_and_still_resolves():
    """成功那一击：**内联换卡**（省一次 API 调用）且澄清真的被解析掉。"""
    raw = _make()
    resolved: list = []
    _fake_clarify_gateway(resolved)
    try:
        out = raw._on_card_action_trigger(_clarify_click_data())
        assert resolved == [("cid-r8", "B 方案")], resolved
        assert out is not None, "回调必须有响应"
        assert len(raw.card_updates) == 1 and isinstance(raw.card_updates[0], dict), \
            f"成功时要内联换卡（一张完整的卡）：{raw.card_updates!r}"
        # 换上去的必须是**已答复**卡（方言不同文案不同，所以两种标记都认）
        swapped = json.dumps(raw.card_updates[0], ensure_ascii=False)
        assert "B 方案" in swapped and ("✅" in swapped or "已答复" in swapped), swapped
    finally:
        _drop_fake_clarify_gateway()


def test_inline_swap_degrades_when_core_cannot_take_a_card():
    """核心那侧的 ``_card_response`` 只接受无参调用时：**不换卡但点击仍然生效**，绝不抛。

    判别力：变异 `R8-3`（去掉 `compat.accepts_positional` 探测、无条件带卡调用）⇒ 红。

    ⚠️ **这条断言为什么盯日志**：两种实现（「先探测签名」vs「带卡调用 + 用 TypeError 兜」）
    在**行为上等价**（都退回无参调用），差别在**是谁做的决定** —— 靠 TypeError 分诊会把
    「卡片构造里冒出来的 TypeError」误读成「核心不认识这个实参」，于是诊断信息撒谎。
    所以这里钉的是**走了哪条机制**，不是返回了什么。

    ⚠️ **而且这条路上必须留下用户可见的反馈**（R8 审计低-1）：退化本身是对的，但用户看到的是
    「点了没反应」→ 他会再点一次 → 第二次必然 `not committed` → 吃一条**误报**的失败提示
    （答案早就交上去了）。所以退化时补一条 success toast；这里把 toast 类**注入成哑类**，
    好让判据与「这台机器上有没有 `lark_oapi`」无关（本仓库门禁跑在 Hermes venv 里、有 SDK，
    而系统解释器上没有 —— 依赖它的断言会在两种环境下给出不同结论）。
    """
    raw = _make()
    box: list = []
    _inject_toast_classes(raw, box)
    raw._card_response = lambda: {"toast": "ok"}      # 模拟另一个版本的核心签名
    resolved: list = []
    _fake_clarify_gateway(resolved)
    try:
        with _LogCapture("larkdeck") as records:
            out = raw._on_card_action_trigger(_clarify_click_data())
        assert resolved == [("cid-r8", "B 方案")], "答案照样要提交（不能因为不换卡就不解析）"
        assert out is not None and out != "SUPER_RESULT", \
            f"必须是我们自己的响应（不许交回内置）：{out!r}"
        text = "\n".join(r.getMessage() for r in records)
        assert "不接受卡片实参" in text, \
            f"必须先探测签名、再决定带不带卡调用（不许靠 TypeError 分诊）：{text!r}"
        # 用户可见的反馈：成功也必须说话，否则「点了没反应」
        assert box and box[-1].toast is not None, \
            "退化路径上的**成功**点击也要有反馈（不然用户会再点一次并吃一条误报）"
        assert box[-1].toast.type == "success", box[-1].toast.type
        assert box[-1].toast.content == i18n.t("clarify.toast_submitted"), box[-1].toast.content
        assert all(card is None for card in raw.card_updates), \
            f"退化时不许换卡：{raw.card_updates!r}"
    finally:
        _drop_fake_clarify_gateway()


def test_inline_swap_degrade_still_answers_without_toast_class():
    """退化路径 + **拿不到 toast 类**（系统解释器没有 ``lark_oapi``）⇒ 仍退回无参调用。

    这条是 `R8-5` 的对偶：`_ld_toast_or_noop` 有能力缺了就退，退化路径也必须一样 ——
    而且**绝不能**因为「想弹 toast 弹不出来」就把这次点击交回内置（那会变成一条合成命令）。
    """
    raw = _make()
    raw._ld_toast_classes = lambda: (None, None)
    raw._card_response = lambda: {"toast": "ok"}
    resolved: list = []
    _fake_clarify_gateway(resolved)
    try:
        out = raw._on_card_action_trigger(_clarify_click_data())
        assert resolved == [("cid-r8", "B 方案")], "答案照样要提交"
        assert out is not None and out != "SUPER_RESULT", f"必须是我们自己的响应：{out!r}"
        assert out == {"toast": "ok"}, f"没有 toast 类时退回无参调用：{out!r}"
        assert raw.calls == [], f"不许交回内置：{raw.calls!r}"
    finally:
        _drop_fake_clarify_gateway()


def test_clarify_inline_swap_fallback_never_returns_to_builtin():
    """探测说「能收」但真调用抛了 ⇒ 兜住、退回无参调用，**绝不交回内置实现**。

    判别力：变异 `R8-10`（把 `_ld_card_response_safe` 里那次带卡调用改成不带 try/except）
    ⇒ 抛出去 ⇒ 红。

    ⚠️ **为什么要专门钉「绝不交回内置」**（R8 审计中-5：这条兜底撤掉时四门禁全绿）：
    异常穿透的真实代价**不是**「什么都没发生」—— 它会冒到 `_on_card_action_trigger` 的
    外层 `except`，于是走 `_ld_passthrough_click` → **内置实现**；内置对不认识的 value 的
    做法是把它当成一条合成命令 ``"/card button {…载荷…}"`` **发进会话**
    （Hermes ``plugins/platforms/feishu/adapter.py:2083`` / ``2340-2355``），**卡片还不换**。
    也就是说用户会在聊天里看到自己的点击载荷原文 —— 这与「不改卡但点击生效」是两个
    完全不同的结果，而只断言「没抛」区分不了它们。
    """
    raw = _make()
    box: list = []
    _inject_toast_classes(raw, box)
    calls = {"n": 0}

    def _picky_card_response(card=None):
        """签名看着能收一张卡（探索探测会判 True），真调用却抛。"""
        calls["n"] += 1
        raise TypeError("意外：核心内部对这张卡不满意")

    raw._card_response = _picky_card_response
    resolved: list = []
    _fake_clarify_gateway(resolved)
    try:
        out = raw._on_card_action_trigger(_clarify_click_data())
        assert calls["n"] >= 1, "带卡调用必须真的发生（否则这条用例没测到兜底那层）"
        assert resolved == [("cid-r8", "B 方案")], "答案照样要提交（不能因为不换卡就不解析）"
        assert raw.calls == [], \
            f"绝不交回内置（内置会把载荷做成一条合成命令发进会话）：{raw.calls!r}"
        assert all(card is None for card in raw.card_updates), \
            f"兜底之后不许换卡：{raw.card_updates!r}"
        # 兜底之后**仍然要说话**（审计低-1）：走到这一支时提交是**成功**的
        # （答案已送达 agent），用户只需知道「点了有用、只是卡没重绘」。
        assert box and box[-1].toast is not None, "兜底之后不许静默（用户会以为没点上）"
        assert box[-1].toast.type == "success", box[-1].toast.type
        assert box[-1].toast.content == i18n.t("clarify.toast_submitted"), box[-1].toast.content
        assert out is not None, "必须给回调一个响应"
        assert out != "SUPER_RESULT", f"必须是我们自己的响应：{out!r}"
    finally:
        _drop_fake_clarify_gateway()


def test_toast_path_never_raises_without_the_sdk():
    """拿不到 toast 类（系统解释器没有 ``lark_oapi``）⇒ 退回「无变更」响应，**绝不抛**。

    这条同时钉住「绝不把点击交给内置实现」：内置实现对我们自己的 value 一无所知，
    交回去等于这次点击没有任何效果。
    """
    raw = _make()
    raw._ld_toast_classes = lambda: (None, None)
    _fake_clarify_gateway([], commit=False)
    try:
        out = raw._on_card_action_trigger(_clarify_click_data())
        assert out is not None and out != "SUPER_RESULT", f"必须是我们自己的响应：{out!r}"
        assert all(card is None for card in raw.card_updates), "没有 toast 也不许换卡"
    finally:
        _drop_fake_clarify_gateway()


def test_accepts_positional_is_the_real_contract():
    """`compat.accepts_positional` 是「能不能吃下这张卡」的判据：**看位置实参个数**，不看形参名。

    判别力：这条直接锁住跨版本判据本身（形参改名不该让换卡能力失效）。
    """
    assert compat.accepts_positional(lambda card_data=None: None, 1) is True
    assert compat.accepts_positional(lambda card=None: None, 1) is True, "形参名无关"
    assert compat.accepts_positional(lambda: None, 1) is False
    assert compat.accepts_positional(lambda *a: None, 3) is True
    assert compat.accepts_positional(lambda a, b: None, 2) is True
    assert compat.accepts_positional(lambda a, b: None, 3) is False


# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 15. R4 卡链的**切点纯函数**（卡片容量相关的判据都在这里，逐条钉住）
# --------------------------------------------------------------------------- #
def test_ck_split_point_respects_budgets_and_avoids_code_fences() -> None:
    """`_ck_split_point` 是卡链的判据中心：预算、代码区、以及「切不开」的返回 None。

    判别力：变异 `R4-5`（不再避开代码围栏）必须让这条红。
    """
    limit = cards.FEISHU_CARD_BYTE_LIMIT
    seal, tail = limit // 2, int(limit * 0.9)

    # ① 预算：切点左边装得进 `seal`，右边装得进 `tail`
    text = ("甲" * 400) + "\n" + ("乙" * 400) + "\n" + ("丙" * 400)
    cut = adapter._ck_split_point(text, 0, seal_budget=2000, tail_budget=limit)
    assert cut is not None and 0 < cut < len(text), cut
    assert adapter._card_body_bytes(text[:cut]) <= 2000, "封卡那一段必须装得进预算"
    assert adapter._card_body_bytes(text[cut:]) <= limit, "尾巴也必须装得下"

    # ② 代码围栏：切点**不能落在围栏/行内代码中间**（否则两张卡的 markdown 各自残缺）
    fenced = "前面一段话。\n\n```\n" + ("code\n" * 200) + "```\n\n后面一段话。"
    cut2 = adapter._ck_split_point(fenced, 0, seal_budget=len("前面一段话。") + 40,
                                   tail_budget=limit)
    spans = cards.code_spans(fenced)
    if cut2 is not None:
        assert not any(start < cut2 < end for start, end in spans), \
            f"切点落在代码区里（{cut2} ∈ {spans}）—— 两张卡的 markdown 都会残缺"
        assert not fenced[:cut2].endswith("```"), "切点不能正好把围栏劈开"

    # ③ 切不开：一帧的增量连「最大的可行切点」都留不下尾巴 ⇒ None（调用方 fail-open）
    huge = "汉" * (limit * 2 // 3)
    assert adapter._ck_split_point(huge, 0, seal_budget=seal, tail_budget=tail) is None, \
        "尾巴装不下时必须返回 None（否则会发一张必被飞书拒的卡）"

    # ④ 已经切过（offset > 0）时只从 offset 往后算，且**绝不回头**
    cut3 = adapter._ck_split_point(huge, 1000, seal_budget=seal, tail_budget=tail)
    assert cut3 is None or cut3 > 1000, cut3


# --------------------------------------------------------------------------- #
# R11-B2：元素容量到顶（`300305` / 包装码 `300315`）的**解析契约**与处置
# --------------------------------------------------------------------------- #
#: 两条**真机字面形状**（2026-09-15 实测，`tests/probe_ck_stream_ops.py --capacity-codes`；
#: 两条容量臂与重复 id 臂各跑两遍，字面逐字节一致）。
#: ⚠️ **不许改写这两个字符串**（例如顺手写成 `Code: 300305`）：它们是解析契约的**输入本身**，
#: 改一个字符就等于把门禁从「对着真机测」降级成「对着我们的想象测」。
_CK_CAPACITY_MSG = "ErrMsg: msg: [element exceeds the limit], code: 300305; "
_CK_DUPLICATE_ID_MSG = ("ErrMsg: msg: [ElementID panel_body: Code 1001: Duplicate ID], "
                        "code: 300301; ")


def test_gate_import_guard_rejects_a_foreign_tree():
    """「门禁先自证被测代码」那条守卫必须**真的会拒绝**（否则它只是一句注释）。

    与 `test_plan_progress_table_is_checkable_from_its_own_rows` 同一手法：门禁自己也要有输入
    （`docs/lessons.md` 推论 11 —— 「给验证器本身写输入」）。
    判据是拿一个**明确不属于本仓库**的路径去问它，它必须报出问题；本仓库自己的路径必须通过。
    """
    foreign = _pathlib.Path("/tmp/some-other-export/larkdeck/core/adapter.py")
    problems = _gate_import_problems(foreign, _REPO_ROOT)
    assert problems and "/tmp/some-other-export" in problems[0], problems
    assert _gate_import_problems(adapter.__file__, _REPO_ROOT) == [], \
        "本仓库自己的 adapter 必须通过 —— 否则这条守卫会天天假红"
    # ⚠️ 反向：**同目录名**的拷贝（`/tmp/xxx/larkdeck`）也必须被认出来不是本仓库
    assert _gate_import_problems(str(_REPO_ROOT) + "-copy/core/adapter.py", _REPO_ROOT), \
        "「同目录名的另一份拷贝」正是审计实测踩到的那一种，必须判不合格"


def test_ck_inner_code_parses_the_two_real_machine_shapes():
    """`300315` 是**包装码** ⇒ 必须解析内层码，否则两种病会被判成同一种（R11-B2）。

    真机两种形状（都是 `code=300315`）：
      * 容量满：`... [element exceeds the limit], code: 300305; `
      * 重复 id：`... [ElementID panel_body: Code 1001: Duplicate ID], code: 300301; `
    判别力落在**第二形状**上：只看外层码的实现会把「我们自己的 id 炒了」说成「元素到顶」，
    而这两件事的修法相反（前者要提前算预算，后者是我们的 bug）。
    """
    res = adapter._CkResult
    cap = res(False, 300315, _CK_CAPACITY_MSG)
    assert cap.inner_code() == 300305, cap.inner_code()
    assert cap.capacity_exceeded() is True, "容量满的内层码就是 300305"

    dup = res(False, 300315, _CK_DUPLICATE_ID_MSG)
    assert dup.inner_code() == 300301, \
        f"尾部 `code:` 才是权威内层码（方括号里的 1001 只是描述码）：{dup.inner_code()}"
    assert dup.capacity_exceeded() is False, \
        "**只看外层码**就会把「id 重复」误判成「元素到顶」—— 300315 是包装码，真原因在内层"

    # 建实体时服务端**直接**回 300305（P7 实测：递归 200 收下、204 拒收）⇒ msg 里没有内层码
    direct = res(False, 300305, "ErrMsg: element exceeds the limit")
    assert direct.inner_code() is None, direct.inner_code()
    assert direct.capacity_exceeded() is True, "外层 300305 本身就是容量码"

    # ⚠️ **解析不出必须是 None，绝不能是 0**：0 在飞书语义里是**成功**
    # （`_ld_response_code(None)` 恰好也返回 0）⇒ 「解析不出来」被读成「成功码」= 把失败判成成功。
    for msg in ("boom", "", None, "success", "code: 0", "code: abc"):
        got = res(False, 300315, msg).inner_code()
        assert got is None, f"msg={msg!r} 解析不出内层码时必须返回 None，实际 {got!r}"

    # `ErrCode:` 是**另一套码空间**（`card.create` 的 230099 用 `ext=ErrCode: 11310;` 记它），
    # `\b` 故意把它挡在外面 —— 混进来会让「内层码」同时有两种口径。
    assert res(False, 230099, "ErrCode: 11310; ErrMsg: element exceeds the limit"
               ).inner_code() is None, "ErrCode 不是 `code:` 标签（前缀粘连必须挡住）"
    # 取**最后一个**匹配（权威内层码在 msg 末尾）
    assert res(False, 300315, "code: 999; blah, code: 300305; ").inner_code() == 300305


def test_ck_capacity_codes_are_deterministic_never_retried():
    """容量到顶 = **确定性失败 ⇒ 绝不重试**（R11-B2）。

    两半一起钉：① 码表里没有它们（声明）；② **行为**上真的只发了一次调用（判别力来源）。
    ② 里带一条**对照臂**（`230020` 限流码必须重试满 4 次）—— 没有它的话，「只发一次」可能只是
    「这台替身根本不会重试」这个恒真事实（本项目头号缺陷类型：断言写得对、但没有判别力）。
    """
    assert 300305 not in adapter._WRITE_RETRY_CODES and 300315 not in adapter._WRITE_RETRY_CODES, \
        f"容量码不是「服务端拒绝执行」而是「这张卡装不下」：{adapter._WRITE_RETRY_CODES}"
    raw = _make()
    old_backoff = adapter._TRANSIENT_BACKOFF
    adapter._TRANSIENT_BACKOFF = (0.0, 0.0, 0.0)      # 测试里不等真实退避
    try:
        for code, msg, want in ((300315, _CK_CAPACITY_MSG, 1),
                                (300305, "ErrMsg: element exceeds the limit", 1),
                                (300315, _CK_DUPLICATE_ID_MSG, 1),
                                (230020, "flood", len(old_backoff) + 1)):   # 对照臂
            attempts: list = []

            def _make_request():
                attempts.append("req")
                return object()

            def _call(_req, code=code, msg=msg):
                attempts.append("call")
                return types.SimpleNamespace(code=code, msg=msg)

            _run(raw._ld_write_with_retry(_make_request, _call, "测试"))
            sent = attempts.count("call")
            assert sent == want, f"code={code} 应当发 {want} 次调用，实际 {sent}"
    finally:
        adapter._TRANSIENT_BACKOFF = old_backoff


def test_ck_capacity_on_a_body_write_is_fatal_not_degrade():
    """正文元素拿到容量码 ⇒ 仍然是 **FATAL**（附录 A），**不许**当卡级死法降级（R11-B2）。

    为什么这两件事不能混：`300309`（会话已关）/`300317`（序号废了）走 `DEGRADE` 是因为
    **消息还在、换条车道照样能写**；而容量到顶意味着这张卡的正文元素根本写不进去，
    patch 到同一张满卡也救不回来 —— 硬降级只会让用户盯着一张**永远不再更新**的卡
    （比 fail-open 掉成纯文本更坏：纯文本至少内容是全的）。
    """
    assert 300315 not in adapter._CARD_DEATH_CODES and 300305 not in adapter._CARD_DEATH_CODES, \
        f"容量码不是卡级死法：{adapter._CARD_DEATH_CODES}"
    calls, client = _mk_cardkit_fake(fail_at=2, fail_at_code=300315)      # 第 2 次写 = 正文
    raw = _make()
    raw._client = client
    adapter.configure(native_transport="cardkit", unified_panel=True)
    old_reqs = adapter.LarkDeckMixin._ld_ck_requests
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter._STREAM_MIN_INTERVAL = 0.0
    adapter.LarkDeckMixin._ld_ck_requests = staticmethod(_fake_ck_requests)
    try:
        assert _run(raw.send_stream_frame("", chat_id="oc_ck_cap", turn_id="t-cap")), "seed 帧"
        assert not _run(raw.send_stream_frame("正文", chat_id="oc_ck_cap", turn_id="t-cap")), \
            "正文元素撞容量码必须 FATAL（fail-open 交核心回落）"
        assert calls["patch"] == 0, "容量到顶**不是**卡级死法，绝不走 DEGRADE 车道"
        state = raw._ld_stream_get("oc_ck_cap:t-cap") or {}
        assert not state.get("ck_degrade"), f"不许把容量满记成降级：{state.get('ck_degrade')}"
    finally:
        adapter.LarkDeckMixin._ld_ck_requests = old_reqs
        adapter._STREAM_MIN_INTERVAL = old_interval


def test_ck_create_rejection_is_traced_with_the_inner_reason():
    """建实体被拒必须**按内层码点名原因**（R11-B2）—— 以前这里是静默 `return None`。

    两种病的排查方向相反（容量 ⇒ 提前算预算；id 错 ⇒ 我们自己的 bug），
    在旧代码里却塌成同一句「CardKit 建实体/发实体卡失败」⇒ 归因消失。
    本地闸门 `_ck_create_wall` 用的是**本地**递归计数，与服务端口径漂移时
    唯一能留下凭据的就是这条 WARNING（Phase C 的动态元素正是这种情形）。
    """
    res = adapter._CkResult
    assert "元素数到顶" in adapter._ck_reject_reason(res(False, 300315, _CK_CAPACITY_MSG))
    assert "id" in adapter._ck_reject_reason(res(False, 300315, _CK_DUPLICATE_ID_MSG)), \
        "重复 id 与容量满必须给两种不同的原因（否则归因等于没做）"
    assert "元素数到顶" in adapter._ck_reject_reason(res(False, 300305, "element exceeds the limit"))
    assert "解析不出" in adapter._ck_reject_reason(res(False, 300315, "boom")), \
        "内层码解析不出时要**如实说解析不出**，不许猜一个原因"

    # 帧级：建实体直接回 300305 ⇒ 这一帧 fail-open，且日志点名「确定性失败：不重试」
    calls, client = _mk_cardkit_fake(create_codes=[300305])
    raw = _make()
    raw._client = client
    adapter.configure(native_transport="cardkit", unified_panel=True)
    old_reqs = adapter.LarkDeckMixin._ld_ck_requests
    adapter.LarkDeckMixin._ld_ck_requests = staticmethod(_fake_ck_requests)
    adapter._log_ck_reject_once._at = 0.0
    try:
        with _LogCapture("larkdeck") as records:
            assert not _run(raw.send_stream_frame("正文", chat_id="oc_ck_cap2", turn_id="t-cap2")), \
                "建实体被拒 ⇒ 本帧 fail-open"
    finally:
        adapter.LarkDeckMixin._ld_ck_requests = old_reqs
    text = "\n".join(r.getMessage() for r in records)
    assert "元素数到顶" in text and "不重试" in text, f"必须留痕并说清处置：{text!r}"
    assert calls["create"] == 1, f"建实体只该试一次（确定性失败，永不重试）：{calls['create']}"


# --------------------------------------------------------------------------- #
# R11-B1：滑窗写入守卫（**构造命中** + 稳态不该触发）
# --------------------------------------------------------------------------- #
def _frame_key(chat: str, turn: str) -> str:
    return f"{chat}:{turn}"


def test_empty_panel_diagnostic_reports_only_on_terminal_frames():
    """空面板诊断**只在收尾帧**报（R11 §3.4 的尾巴，真机实测的噪声）。

    两半都要断：
      * **中间帧**（含建卡 seed 帧 —— 那一刻必然还没有任何过程数据）不许报：旧写法每回合开头
        都刷一条「面板为空 · 诊断」，而这条 INFO 正是「面板恒空」那个真 bug 的**唯一**排查
        凭据（2026-09-14 真机就是靠它定的位）⇒ 被正常噪声淹没等于没有凭据；
      * **收尾帧仍然为空**必须照旧报：否则这条诊断就废了（它存在的理由就是抓「一整个回合
        都没有过程数据」这种病）。
    """
    chat, turn = "oc_ck_diag", "t-diag"
    calls, client = _mk_cardkit_fake()
    raw = _make()
    raw._client = client
    adapter.configure(native_transport="cardkit", unified_panel=True)
    old_reqs = adapter.LarkDeckMixin._ld_ck_requests
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter.LarkDeckMixin._ld_ck_requests = staticmethod(_fake_ck_requests)
    adapter._STREAM_MIN_INTERVAL = 0.0
    panel.reset()
    try:
        adapter._log_empty_panel_once._at = 0.0
        with _LogCapture("larkdeck") as records:
            assert _run(raw.send_stream_frame("", chat_id=chat, turn_id=turn)), "seed 帧"
            assert _run(raw.send_stream_frame("正文", chat_id=chat, turn_id=turn)), "中间帧"
        noise = [r.getMessage() for r in records if "面板为空" in r.getMessage()]
        assert not noise, \
            f"中间帧（含 seed）的面板为空是**正常**的，不该报诊断（真机噪声实测）：{noise}"
        adapter._log_empty_panel_once._at = 0.0
        with _LogCapture("larkdeck") as records:
            assert _run(raw.send_stream_frame("正文，收尾", finalize=True,
                                              chat_id=chat, turn_id=turn)), "收尾帧"
        hit = [r.getMessage() for r in records if "面板为空" in r.getMessage()]
        assert hit, ("收尾帧仍然为空必须照旧报出来 —— 那是「面板恒空」的唯一凭据"
                     f"（不报就等于把这条诊断废掉）：{[r.getMessage() for r in records]}")
        assert "诊断" in hit[0], f"报出来的必须带只读诊断（否则猜不出是哪种成因）：{hit[0]}"
    finally:
        adapter.LarkDeckMixin._ld_ck_requests = old_reqs
        adapter._STREAM_MIN_INTERVAL = old_interval
        adapter._log_empty_panel_once._at = 0.0
        panel.reset()


def test_ck_write_window_semantics_are_pinned_by_the_constants():
    """`_ck_window_*` 的**语义与常量**必须被直接钉住（B1 审计高-1 / 低-1 的收口）。

    为什么单开一条：原来只验「消费侧」（窗口顶满 ⇒ 让出装饰），而**记账侧**（`_ck_window_note`
    真的 `append`、窗口长度常量、修剪边界）**零门禁** —— 审计实测三种改法都四门禁全绿：
    ① 摘掉 `.append(now)`（窗口永远空 ⇒ 守卫永不触发）；② `_CK_WINDOW_SECONDS = 1.0 → 0.2`
    （判据静默缩水）；③ 修剪边界 `<=` 改 `<`。对一个「给 Phase C 预留的闸门」来说，这三条
    任意一条悄悄发生，守卫都会退化成装饰品，而真机上只看得到「装饰偶尔不更新」。
    ⚠️ 常量用**字面量**断言（不是 `== adapter._CK_WRITES_PER_SECOND` 那种自比较）。
    """
    assert adapter._CK_WINDOW_SECONDS == 1.0, "窗口长度就是「每秒」的口径，改小 = 静默缩小限速"
    assert adapter._CK_WINDOW_MAXLEN == 64, "窗口容量上界（只关心最近 1 秒）"
    state: Dict[str, Any] = {}
    t0 = 1_700_000_000.0
    # ① 允许到第 10 次，第 11 次必须被拒（`len(win) < 10` ⇒ 窗口内最多 10 条）
    for i in range(adapter._CK_WRITES_PER_SECOND):
        assert adapter._ck_window_allow(state, t0) is True, f"第 {i + 1} 次应当被允许"
        adapter._ck_window_note(state, t0)
    assert len(adapter._ck_window(state)) == 10, len(adapter._ck_window(state))
    assert adapter._ck_window_allow(state, t0) is False,         "窗口满了就必须拒 —— 允许的话守卫等于不存在（审计实测摘掉 append 就会变成这样）"
    # ② 边界：正好 1.0 秒之后，过期的条目**被修剪掉**（判据是 `<=`）⇒ 又能写
    assert adapter._ck_window_allow(state, t0 + adapter._CK_WINDOW_SECONDS) is True, \
        "到点就该过期（修剪用 `<=`；改成 `<` 会让窗口永远慢一拍）"
    # ③ 「只看不记」：allow 不能顺手记账（记了就等于把「打算写」记成「写了」）
    st2: Dict[str, Any] = {}
    for _ in range(30):
        adapter._ck_window_allow(st2, t0)
    assert len(adapter._ck_window(st2)) == 0, "`_ck_window_allow` 不许记账（记账只在真的发出之后）"
    # ④ 记账面：note 必须真的把时刻记进去（这条正是审计说的「窗口永远不被喂养」）
    adapter._ck_window_note(st2, t0)
    assert list(adapter._ck_window(st2)) == [t0], list(adapter._ck_window(st2))
    # ⑤ 坏值不许炸：状态里塞非 deque ⇒ 重建一个空窗口（守卫归零重来，不抛）
    assert len(adapter._ck_window({"ck_window": "不是 deque"})) == 0


def test_ck_write_window_guard_skips_the_decor_batch_without_freezing_it():
    """窗口顶满时**让出装饰批量**，但两条边界必须同时守住（R11-B1）。

    边界① **不置 `ck_decor`**：记账只在 batch 真的写成功之后 ⇒ 让出的那几个元素下一帧
    还会被当成「该写」⇒ 补写。在这里记一笔就等于把「没写」说成「写了」，去重逻辑随后会
    **永久跳过**它们 = 装饰静默冻结（本项目最怕的失败形态：看得见、没日志、也不回落）。
    边界② **跳过 ≠ 失败**：本帧照旧返回 `True` —— 装饰是增强，为它把整帧判失败会买下
    「核心停用本回合 native ⇒ 用户掉成纯文本」这条链。

    ⚠️ 窗口必须**构造**出来（真机稳态 ≈8.2 次/秒 < 10，永远撞不到它）：把 10 个「现在」的
    时刻塞进这一回合的窗口，等价于「这一秒已经写满」。这也是为什么守卫不能是死代码。
    """
    chat, turn = "oc_ck_win", "t-win"
    calls, client = _mk_cardkit_fake()
    raw = _make()
    raw._client = client
    adapter.configure(native_transport="cardkit", unified_panel=True)
    old_reqs = adapter.LarkDeckMixin._ld_ck_requests
    old_interval = adapter._STREAM_MIN_INTERVAL
    adapter.LarkDeckMixin._ld_ck_requests = staticmethod(_fake_ck_requests)
    adapter._STREAM_MIN_INTERVAL = 0.0
    try:
        assert _run(raw.send_stream_frame("", chat_id=chat, turn_id=turn)), "seed 帧"
        # 第一帧：装饰与正文都写出去（这一帧之后窗口里才有东西可谈）
        assert _run(raw.send_stream_frame("正文一", chat_id=chat, turn_id=turn))
        state = raw._ld_stream_get(_frame_key(chat, turn)) or {}
        assert calls["batch"], "前提：第一帧必须真的发过装饰批量"
        assert isinstance(state.get("ck_window"), deque), \
            f"窗口必须挂在这一回合的状态里：{state.get('ck_window')!r}"

        # 让装饰**真的变一次**（页脚走真数据层 + 真时钟），否则「未变化不重写」会先把 batch 拦下，
        # 这条用例就变成在验去重、不是在验守卫。
        before_footer = raw._ld_footer() or ""
        context.record_api_call(model="test-model",
                                usage={"input_tokens": 4242, "output_tokens": 8})
        context.set_context_override(10000)
        assert (raw._ld_footer() or "") not in ("", before_footer), "前提：页脚要真的变了"

        # ① 窗口顶满 ⇒ 本帧的装饰批量必须被让出
        state = raw._ld_stream_get(_frame_key(chat, turn)) or {}
        state["ck_window"].extend([time.monotonic()] * adapter._CK_WRITES_PER_SECOND)
        batch_before = len(calls["batch"])
        content_before = len(calls["content"])
        decor_before = dict(state.get("ck_decor") or {})
        adapter._log_ck_window_skip_once._at = 0.0
        with _LogCapture("larkdeck") as records:
            assert _run(raw.send_stream_frame("正文一，正文二", chat_id=chat, turn_id=turn)) is True, \
                "跳过装饰 **≠** 失败：这一帧必须照旧算成功（否则本回合的 native 会被停用）"
        assert len(calls["batch"]) == batch_before, \
            f"窗口顶满时不该再发装饰批量（发了 {len(calls['batch']) - batch_before} 次）"
        # ⚠️ **让出装饰 ≠ 让出正文**（B1 审计高-2）：正文是**提交点**，跳过它用户就永远看不到
        # 那一段（核心按「这一帧送达」乐观记账，不会再补发）。审计实测：在守卫分支里加一句
        # `return True, seq, None`（连正文一起跳过）**四门禁全绿** ⇒ 这条断言就是为它加的。
        assert len(calls["content"]) == content_before + 1, \
            ("被让出的那一帧**正文必须照旧写**（只让装饰，绝不让提交点）："
             f"{len(calls['content']) - content_before} 次")
        skipped_text = "\n".join(r.getMessage() for r in records)
        assert "滑窗写入守卫" in skipped_text and "没有被记账" in skipped_text, \
            f"让出装饰必须留痕、且说清「没有记账」：{skipped_text!r}"
        state = raw._ld_stream_get(_frame_key(chat, turn)) or {}
        assert state.get("ck_window_skips") == 1, state.get("ck_window_skips")
        assert state.get("ck_decor") == decor_before, \
            f"被让出的写**绝不能记账**（记账 = 去重逻辑永久跳过它们 = 装饰静默冻结）：{state}"
        # 被让出的元素名单从**日志那句**里精确取出来（`、` 分隔；原来是宽松正则，
        # 会把整句中文当一个「词」—— 审计低-4），再和下一帧实际补写的 id 对照。
        seg = skipped_text.split("跳过的元素 ", 1)[-1].split(" **", 1)[0]
        skipped_ids = {t.strip() for t in seg.split("、") if t.strip()}
        assert skipped_ids and all(re.fullmatch(r"[A-Za-z0-9_]+", i) for i in skipped_ids), \
            f"日志里的元素名单必须是我们自己的 id 形状：{seg!r}"

        # ② 窗口放开（等价于过了 1 秒）⇒ 下一帧必须把刚才让出的装饰**补上**
        (raw._ld_stream_get(_frame_key(chat, turn)) or {})["ck_window"].clear()
        assert _run(raw.send_stream_frame("正文一，正文二，正文三", chat_id=chat, turn_id=turn))
        assert len(calls["batch"]) == batch_before + 1, \
            "让出的装饰必须下一帧补写 —— 不补就是静默冻结（用户看到面板/页脚停在旧内容）"
        wrote = {a["params"]["element_id"] for a in calls["batch"][-1][0]}
        assert wrote == skipped_ids, \
            f"补写的必须**正好**是刚才让出的那些元素（让出 {skipped_ids} / 补写 {wrote}）"
    finally:
        adapter.LarkDeckMixin._ld_ck_requests = old_reqs
        adapter._STREAM_MIN_INTERVAL = old_interval
        # ⚠️ `context.set_context_override` 钉的是**配置类**状态，`context.reset()` **故意不清**它
        # （见它的 docstring）—— 不复位就会泄漏给后面的用例（实测：把「未命中返回 None」那条
        # 用例顶红，而它与本守卫毫无关系）。
        context.set_context_override(None)
        context.reset()
        panel.reset()


def test_ck_write_window_guard_never_trips_at_production_cadence():
    """稳态下守卫**不该触发**，而且**窗口必须逐帧与真实写出数对账**（R11-B1 + B1 审计高-1）。

    两件事一起钉：
      * **「稳态不可达」是 B1 的设计前提**：每帧 ≤2 次逻辑写 × 4 帧/秒 + 预览 5 秒限频
        ⇒ **摊还均值 ≈8.2 次/秒** < 卡级上限 10（⚠️ 摊还均值，不是「任何 1 秒窗口都 ≤10」：
        正文与预览**不查 allow**，所以理论上窗口内最多能记到 12 条 —— 这是有意的口径，
        见 `_ck_window_note` 的 docstring）。把窗口改小、把上限调低、或让守卫无条件触发，
        装饰就会在真机上**默默**掉帧，而日志只在 60 秒限流里留一句；
      * **记账面**：这一帧窗口里新增的条目数必须**正好等于**这一帧真的发出的逻辑写数
        （装饰 batch + 正文 content + 预览 settings）。审计实测：摘掉 `_ck_window_note` 的
        `.append`、或删掉预览那一笔记账，**四门禁全绿** —— 窗口永远空或永远少一笔，
        守卫就悄悄变成装饰品（本项目点名的「恒假门禁」形态）。
    做法：假时钟**按 0.25s 前进**（= 真实帧节流窗口），跑 24 帧（6 秒 > 1 秒窗口，且能撞上
    至少一次预览写入），逐帧与调用账本对账。
    """
    chat, turn = "oc_ck_cadence", "t-cadence"
    calls, client = _mk_cardkit_fake()
    raw = _make()
    raw._client = client
    adapter.configure(native_transport="cardkit", unified_panel=True)
    old_reqs = adapter.LarkDeckMixin._ld_ck_requests
    real_monotonic = time.monotonic
    clock = {"t": 1_700_000_000.0}
    adapter.LarkDeckMixin._ld_ck_requests = staticmethod(_fake_ck_requests)
    time.monotonic = lambda: clock["t"]          # type: ignore[assignment]
    try:
        assert _run(raw.send_stream_frame("", chat_id=chat, turn_id=turn)), "seed 帧"
        history: list = []          # [(这一帧的时刻, 这一帧真的写出去几次)]
        prev = {"batch": 0, "content": 0, "settings": 0}
        for i in range(24):
            clock["t"] += adapter._STREAM_MIN_INTERVAL      # 与生产同节奏（4 帧/秒）
            assert _run(raw.send_stream_frame(f"正文{i}", chat_id=chat, turn_id=turn)), f"第 {i} 帧"
            now_counts = {"batch": len(calls["batch"]), "content": len(calls["content"]),
                          "settings": len(calls["settings"])}
            wrote = sum(now_counts[k] - prev[k] for k in prev)
            prev = now_counts
            history.append((clock["t"], wrote))
            state_i = raw._ld_stream_get(_frame_key(chat, turn)) or {}
            window = adapter._ck_window(state_i)
            # 窗口里应当**正好**是「最近 1 秒真的写出去的那些次」（修剪判据是 `t <= now - 1.0`）
            expect = sum(c for (t, c) in history if t > clock["t"] - adapter._CK_WINDOW_SECONDS)
            assert len(window) == expect, (
                f"第 {i} 帧：窗口里应当正好有最近 1 秒真的写出去的 {expect} 次，实际 {len(window)} 次"
                " —— 对不上说明**记账面**漏了/多了（窗口永远空或永远少一笔 ⇒ 守卫静默失效）")
            assert state_i.get("ck_window_skips") in (None, 0), \
                f"稳态不该让出装饰（第 {i} 帧）：{state_i.get('ck_window_skips')}"
        assert calls["settings"], "前提：6 秒里必须至少写一次会话预览（否则覆盖不到预览那一笔记账）"
        assert len(calls["content"]) == 24, f"每一帧的正文都该照常写：{len(calls['content'])}"
    finally:
        time.monotonic = real_monotonic           # type: ignore[assignment]
        adapter.LarkDeckMixin._ld_ck_requests = old_reqs
        context.reset()
        panel.reset()


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok    {name}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
