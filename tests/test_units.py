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
import json
import logging
import pathlib as _pathlib
import os
import sys
import threading
import time
import types
from typing import Any, Dict

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_PARENT = os.path.dirname(os.path.dirname(_HERE))  # .../code —— 使 `import larkdeck` 成立
if _REPO_PARENT not in sys.path:
    sys.path.insert(0, _REPO_PARENT)

from larkdeck.core import adapter, cards, compat, context, i18n, panel  # noqa: E402


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
        self.submitted: list = []
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


def _fake_clarify_gateway(resolved: list, *, commit: bool = True):
    """装一个假的 ``tools.clarify_gateway``。

    ``commit=False`` 模拟网关**拒绝**这次提交（重复点击 / 该澄清已被超时或文字回答
    消费掉）—— 真实实现就是返回 bool 的这个语义，见 Hermes
    ``tools/clarify_gateway.py`` 的 ``resolve_gateway_clarify``。
    """
    fake_tools = types.ModuleType("tools")
    fake_cg = types.ModuleType("tools.clarify_gateway")
    fake_cg._lock = threading.Lock()
    fake_cg._entries = {}

    def _resolve(cid, resp):
        resolved.append((cid, resp))
        return commit

    def _awaiting(cid):
        resolved.append((cid, "__await_text__"))
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
    assert list(bare["missing_reactions"]) == list(compat.REACTION_ADAPTER_ATTRS), bare
    assert list(bare["missing_signal"]) == list(compat.SIGNAL_ADAPTER_ATTRS), bare
    assert list(bare["missing_callback"]) == list(compat.CALLBACK_ADAPTER_ATTRS), bare


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
                     "missing_reactions", "session_attribution_ok"):
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
    result = _run(raw.send("oc_1", "你好"))
    assert result.message_id == "om_text_1", "卡片失败必须回落纯文本"
    assert any(c[0] == "SUPER.send" for c in raw.calls), raw.calls


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
        assert "推理中……" in joined and "bash" in joined
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
        def _mk_fake(*, fail_write_after=10 ** 9, create_ok=True, fail_answer_only=False,
                     fail_panel_only=False):
            calls = {"create": 0, "send": 0, "content": [], "patch": 0, "entity": []}

            class _Resp:
                def __init__(self, code=0, **data):
                    self.code = code
                    self.msg = "success" if code == 0 else "boom"
                    self.data = types.SimpleNamespace(**data) if data else None

                def success(self):
                    return self.code == 0

            class _CardRes:
                def create(self, request):
                    calls["create"] += 1
                    calls["entity"].append(request.request_body["card_json"])
                    return _Resp(0, card_id="ck_1") if create_ok else _Resp(300305)

            class _ElemRes:
                def content(self, request):
                    calls["content"].append((request.element_id, request.request_body.content,
                                             request.request_body.sequence))
                    if fail_answer_only and request.element_id == cards.CARDKIT_ANSWER_ID:
                        return _Resp(300309)
                    if fail_panel_only and request.element_id == cards.CARDKIT_PANEL_BODY_ID:
                        return _Resp(300309)
                    if len(calls["content"]) > fail_write_after:
                        return _Resp(300309)
                    return _Resp(0)

            class _MsgRes:
                def create(self, request):
                    calls["send"] += 1
                    # ⚠️ 形状必须与替身适配器的 `_finalize_send_result` 一致（它读 dict）
                    return {"code": 0, "data": {"message_id": "om_ck_1"}}

                def patch(self, request):
                    calls["patch"] += 1
                    return _Resp(0)

            client = types.SimpleNamespace(
                cardkit=types.SimpleNamespace(v1=types.SimpleNamespace(
                    card=_CardRes(), card_element=_ElemRes())),
                im=types.SimpleNamespace(v1=types.SimpleNamespace(message=_MsgRes())))
            return calls, client

        # ⚠️ `test_units.py` 是**零 Hermes 依赖**的，所以 SDK 的请求构造必须可注入：
        # 真环境用 `_ld_ck_requests()` 里的 SDK builder，这里换成等价的哑对象。
        def _fake_requests():
            return types.SimpleNamespace(
                create_card=lambda card_json: types.SimpleNamespace(
                    request_body={"card_json": card_json}),
                send_entity=lambda receive_id, card_id: types.SimpleNamespace(
                    receive_id=receive_id, card_id=card_id,
                    request_body=types.SimpleNamespace(
                        content=json.dumps({"type": "card", "data": {"card_id": card_id}}))),
                write_element=lambda cid, eid, content, seq, uuid_value: types.SimpleNamespace(
                    card_id=cid, element_id=eid,
                    request_body=types.SimpleNamespace(content=content, sequence=seq,
                                                       uuid=uuid_value)),
            )

        # ① 全链路成功：建实体 → 两次帧（各写正文+面板两个元素）→ 收尾走 patch
        calls, client = _mk_fake()
        raw = _make()
        raw._client = client
        raw._ld_send_card = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("cardkit 模式不该走 _ld_send_card"))
        adapter.configure(native_transport="cardkit")
        old_reqs = adapter.LarkDeckMixin._ld_ck_requests
        old_interval = adapter._STREAM_MIN_INTERVAL
        adapter._STREAM_MIN_INTERVAL = 0.0        # 帧节流窗口：测试里连发两帧要都能过
        adapter.LarkDeckMixin._ld_ck_requests = staticmethod(_fake_requests)
        assert _run(raw.send_stream_frame("", chat_id="oc_ck", turn_id="t-ck"))
        assert calls["create"] == 1 and calls["send"] == 1, calls
        assert _run(raw.send_stream_frame("正文一", chat_id="oc_ck", turn_id="t-ck"))
        assert _run(raw.send_stream_frame("正文一，正文二", chat_id="oc_ck", turn_id="t-ck"))
        ids = [c[0] for c in calls["content"]]
        seqs = [c[2] for c in calls["content"]]
        # ⚠️ 必须写**字面量**：两边都用 `cards.CARDKIT_*` 是**自比较** ——
        # 第十一路审计实测「两个元素 id 都叫 answer」「面板 id 抄错字面量」两种变异四门禁全绿，
        # 而真机 `cardkit.v1.card.create` 对这两种形状都回 `300301`
        # （`Code 1001: Duplicate ID` / `Code 1002: elementID format error`）。
        assert ids == ["answer", "panel_body"] * 2, ids
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), seqs
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

        # ② 写**正文**元素失败（面板那条仍成功）⇒ 这一帧必须返回 False
        #    （核心据此停用 native 并回落 edit/send）。这一条专门堵「吞掉正文失败」那种变异：
        #    如果两个元素都失败，面板那一层的失败会把它掩盖过去。
        calls, client = _mk_fake(fail_answer_only=True)
        raw2 = _make()
        raw2._client = client
        adapter.configure(native_transport="cardkit")
        assert _run(raw2.send_stream_frame("", chat_id="oc_ck2", turn_id="t-2"))
        assert not _run(raw2.send_stream_frame("正文", chat_id="oc_ck2", turn_id="t-2")), \
            "写元素失败必须返回 False 让核心回落，绝不能吞掉"

        # ②b **正文写成功、面板写失败**：这一帧也必须返回 False（否则卡片停在
        #    「正文新、面板旧」的半更新态，而核心以为成功、不做回落）—— 第十一路审计
        #    实测这个形态此前没有任何断言（M07 四门禁全绿）。
        calls, client = _mk_fake(fail_panel_only=True)
        raw2b = _make()
        raw2b._client = client
        adapter.configure(native_transport="cardkit")
        assert _run(raw2b.send_stream_frame("", chat_id="oc_ck2b", turn_id="t-2b"))
        assert not _run(raw2b.send_stream_frame("正文", chat_id="oc_ck2b", turn_id="t-2b")), \
            "面板写失败必须也返回 False（否则卡片半更新且无日志）"
        assert any(c[0] == cards.CARDKIT_ANSWER_ID for c in calls["content"]), \
            "前提：正文那一次确实写成功了"

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
        elem_ids = [e.get("element_id") for e in entity["body"]["elements"]]
        assert elem_ids == ["answer"], \
            f"unified_panel: false 时面板元素不该进卡（README 承诺两条传输下都关得掉）：{elem_ids}"
        assert _run(raw5.send_stream_frame("正文一", chat_id="oc_ck5", turn_id="t-5"))
        assert _run(raw5.send_stream_frame("正文一，正文二", chat_id="oc_ck5", turn_id="t-5"))
        sent_ids = [c[0] for c in calls["content"]]
        assert sent_ids == ["answer", "answer"], sent_ids
        sent_seqs = [c[2] for c in calls["content"]]
        assert sent_seqs == [1, 2], sent_seqs
        assert all(cards.CARDKIT_PANEL_BODY_ID not in c[0] for c in calls["content"]), \
            "面板关掉后写 panel_body 会得 300313（元素不存在）⇒ 每帧失败、整回合打回纯文本"
    finally:
        try:
            adapter.LarkDeckMixin._ld_ck_requests = old_reqs
            adapter._STREAM_MIN_INTERVAL = old_interval
        except NameError:
            pass
        adapter._CONFIG.clear()
        adapter._CONFIG.update(defaults)
        adapter._apply_metrics_config()


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
    # 造满容量：一半是泄漏流，一半是活跃流
    for i in range(adapter._MAX_STREAMS):
        raw._ld_stream_put(f"leaked-{i}", {"t0": leaked, "last_at": leaked,
                                          "message_id": f"om_l{i}"})
    assert len(raw._ld_streams) == adapter._MAX_STREAMS
    raw._ld_stream_put("newcomer", {"t0": now, "last_at": now, "message_id": "om_new"})
    assert len(raw._ld_streams) <= adapter._MAX_STREAMS
    assert "newcomer" in raw._ld_streams, "新流必须能进来"


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
    # 全部是「创建很久、但最近刚活动」的流：旧实现按 t0 淘汰，会把它们踢掉
    for i in range(adapter._MAX_STREAMS):
        raw._ld_stream_put(f"active-{i}", {"t0": now - 99999.0, "last_at": now,
                                          "message_id": f"om_a{i}"})
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
        assert raw.card_updates == [None], \
            "提交没成功就不能回填「已答复」卡片，否则用户以为答案已送达"
    finally:
        _drop_fake_clarify_gateway()


def test_clarify_click_from_unauthorized_user_is_ignored():
    raw = _make()
    data = types.SimpleNamespace(
        event=types.SimpleNamespace(
            action=types.SimpleNamespace(value={
                "larkdeck_action": "clarify", "clarify_id": "cid-4", "answer": "A"}),
            operator=types.SimpleNamespace(open_id="ou_intruder")),
    )
    raw._on_card_action_trigger(data)
    assert raw.submitted == [], "未授权用户的点击不得触发澄清解析"


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
    line = cards.footer_line(model="Sonnet 4.6", context="ctx 45.2k/200k · 23%", duration=12.3)
    assert line == "🤖 Sonnet 4.6 · ctx 45.2k/200k · 23% · ⏱ 12.3s"
    assert cards.footer_line() is None
    assert cards.footer_line(model="m") == "🤖 m"
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


def test_unified_panel_applies_caps() -> None:
    panel = cards.unified_panel(reasoning="x" * 5000, tools=["a"])
    assert panel is not None
    first = panel["elements"][0]["content"]
    assert "已省略" in first and len(first) < 5000
    trimmed = cards.unified_panel(tools=[f"step{i}" for i in range(40)])
    joined = " ".join(e.get("content", "") for e in trimmed["elements"])
    assert "更早的 10 步已折叠" in joined
    assert "step0" not in joined and "step39" in joined, "保留最近的步骤"
    # 每条工具结果也各自受限
    big = cards.unified_panel(tools=["y" * 5000])
    assert "已省略" in big["elements"][0]["content"]
    # 上限可调（配置进来就是这个口子）
    assert cards.unified_panel(reasoning="z" * 50, max_reasoning_chars=10) is not None
    assert cards.unified_panel() is None


def test_panel_caps_treat_zero_as_default_not_unlimited():
    """配置写 0 / 负数必须退回默认上限，**不是**取消上限。

    取消上限会把整段推理（panel 缓冲可达 262144 字符）塞进卡片，飞书直接拒收，
    那个回合的卡片功能整块丢掉。README 把这三个键描述成「上限」，行为得对得上。
    """
    for zeroish in (0, -1):
        long_panel = cards.unified_panel(reasoning="x" * 5000, max_reasoning_chars=zeroish)
        assert "已省略" in long_panel["elements"][0]["content"], \
            f"max_reasoning_chars={zeroish} 不能变成「不截断」"

        many = cards.unified_panel(tools=[f"step{i}" for i in range(40)], max_steps=zeroish)
        joined = " ".join(e.get("content", "") for e in many["elements"])
        assert "更早的 10 步已折叠" in joined, f"max_steps={zeroish} 不能变成「全量保留」"

        big = cards.unified_panel(tools=["y" * 5000], max_tool_chars=zeroish)
        assert "已省略" in big["elements"][0]["content"], \
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


def test_adapter_footer_wiring() -> None:
    """页脚 = **只有上下文用量**；模型名与耗时归面板标题（决策 D2，别又搬回页脚）。"""
    defaults = dict(adapter._DEFAULTS)
    try:
        context.reset()
        adapter.configure(footer=True, show_model=True, context_style="text",
                          model_aliases="test-model=Test Model")
        # 钩子还没触发 → 没有任何一段可显示 → 不渲染脚注
        assert adapter.LarkDeckMixin._ld_footer() is None

        context.record_api_call(model="test-model",
                                usage={"input_tokens": 1000, "output_tokens": 5})
        context.set_context_override(10000)
        assert adapter.LarkDeckMixin._ld_footer() == "ctx 1k/10k · 10%"
        # 同一屏里不重复：模型名与耗时只在面板标题行出现
        assert "🤖" not in adapter.LarkDeckMixin._ld_footer()
        assert "⏱" not in adapter.LarkDeckMixin._ld_footer()

        adapter.configure(context_style="bar")
        assert "[█░░░░░░░]" in adapter.LarkDeckMixin._ld_footer()
        adapter.configure(show_model=False)
        assert not adapter.LarkDeckMixin._ld_footer().startswith("🤖")
        adapter.configure(footer=False)
        assert adapter.LarkDeckMixin._ld_footer() is None
    finally:
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
        adapter.configure(unified_panel=True)
        assert adapter.LarkDeckMixin._ld_panel() is None, "没有数据不该渲染面板"

        panel.record_reasoning("s1", "t1", "先想一下。")
        panel.record_tool_started("s1", "t1", "read_file", {"path": "/tmp/a.txt"}, "c1")
        panel.record_tool_finished("s1", "t1", tool_name="read_file", status="ok",
                                   duration_ms=42, tool_call_id="c1")
        node = adapter.LarkDeckMixin._ld_panel()
        assert node is not None and node["tag"] == "collapsible_panel"
        title = node["header"]["title"]["content"]
        # 决策 D2：卡片级 header 去掉了，模型名 / 轮数 / 工具数 / 耗时全在面板标题行
        assert "🧠 1" in title and "🔧 1" in title, title
        assert node["border"]["color"] == "grey", "还没有结局 → 中性灰边"
        texts = " ".join(e.get("content", "") for e in node["elements"])
        assert "先想一下。" in texts and "read_file" in texts and "✅" in texts

        # 模型名与耗时进面板标题（show_model 控制前者）
        adapter.configure(show_model=True, model_aliases="test-model=Test Model")
        context.record_api_call(model="test-model", usage={"input_tokens": 1})
        with_time = adapter.LarkDeckMixin._ld_panel("", time.monotonic() - 12.3)
        header = with_time["header"]["title"]["content"]
        assert "🤖 Test Model" in header and "⏱ 12.3s" in header, header
        adapter.configure(show_model=False)
        assert "🤖" not in adapter.LarkDeckMixin._ld_panel("", None)["header"]["title"]["content"]

        # 只有推理（没有工具）也给面板，标题仍带轮数
        panel.reset()
        panel.record_reasoning("s1", "t1", "只有推理。")
        only = adapter.LarkDeckMixin._ld_panel()
        assert only is not None
        assert "🧠 1" in only["header"]["title"]["content"]

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

    # 没给 rounds 时退回把 reasoning 当一整段（向后兼容）
    flat = cards.unified_panel(reasoning="一整段推理")
    assert "一整段推理" in flat["elements"][0]["content"]
    assert "轮" not in flat["elements"][0]["content"]

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
        assert _find_collapsible(body)["border"]["color"] == "yellow", \
            "中止态不是黄边 —— 状态色整条没落地"
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
    original_builder = adapter.LarkDeckMixin._ld_build_resolved_card
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
        assert "yellow" in json.dumps(json.loads(updates[-1]["content"]), ensure_ascii=False)

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
    # 自引用结构不许无限递归
    cyc: dict = {}
    cyc["self"] = cyc
    assert len(panel._args_preview(cyc)) <= panel._ARGS_PREVIEW_CHARS + 1


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
