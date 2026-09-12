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

    # --- 卡片点击路径依赖的辅助（内置适配器上有真实实现） ---
    def _is_interactive_operator_authorized(self, open_id):
        return open_id == "ou_ok"

    def _loop_accepts_callbacks(self, loop):
        return loop is not None

    def _submit_on_loop(self, loop, coro):
        self.submitted.append(coro)

    def _get_cached_sender_name(self, open_id):
        return "汪老师"

    def _card_response(self, card=None):
        return {"card": card} if card else {"toast": "ok"}


def _run(coro):
    return asyncio.run(coro)


def _make(**cfg: Any):
    """按与真实路径完全相同的方式造一个「内置适配器 + 卡片层」实例。"""
    return adapter.build_adapter(StubAdapter, _StubConfig(**cfg))


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


def test_clarify_gateway_bridge_degrades_safely():
    # 无 Hermes 环境（没有 tools 模块）时保守返回 False，而不是抛异常。
    assert compat.clarify_multi_select("cid-x") is False
    assert compat.CALLBACK_INSTANCE_ATTRS == ("_loop",)


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
    """要接服务端点击的卡片只能是 1.0 —— 混进 2.0 会让飞书拒绝 action 行。

    这是本插件最容易被改崩的不变量：2.0 的 behaviors 回调到不了
    ``p2.card.action.trigger``，而 1.0 的 action 容器嵌进 2.0 卡会被拒。
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


def test_native_streaming_state_cap_evicts_oldest():
    """回合状态表有上限，满了淘汰最旧的四分之一。"""
    raw = _make()
    for i in range(adapter._MAX_STREAMS + 5):
        raw._ld_stream_put(f"session-{i}", {"t0": float(i), "message_id": f"om_{i}"})
    assert len(raw._ld_streams) <= adapter._MAX_STREAMS
    assert "session-0" not in raw._ld_streams, "最旧的回合应被淘汰"


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
    fake_tools = types.ModuleType("tools")
    fake_cg = types.ModuleType("tools.clarify_gateway")
    fake_cg._lock = threading.Lock()
    fake_cg._entries = {}
    fake_cg.resolve_gateway_clarify = lambda cid, resp: resolved.append((cid, resp))
    fake_cg.mark_awaiting_text = lambda cid: resolved.append((cid, "__await_text__"))
    fake_tools.clarify_gateway = fake_cg
    sys.modules["tools"] = fake_tools
    sys.modules["tools.clarify_gateway"] = fake_cg
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
        assert raw.submitted, "应把解析动作提交到适配器 loop"
        _run(raw.submitted[0])
        assert resolved == [("cid-9", "B 方案")], resolved
    finally:
        sys.modules.pop("tools", None)
        sys.modules.pop("tools.clarify_gateway", None)


def test_clarify_click_other_marks_awaiting_text():
    raw = _make()
    resolved: list = []
    fake_tools = types.ModuleType("tools")
    fake_cg = types.ModuleType("tools.clarify_gateway")
    fake_cg._lock = threading.Lock()
    fake_cg._entries = {}
    fake_cg.resolve_gateway_clarify = lambda cid, resp: resolved.append((cid, resp))
    fake_cg.mark_awaiting_text = lambda cid: resolved.append((cid, "__await_text__"))
    fake_tools.clarify_gateway = fake_cg
    sys.modules["tools"] = fake_tools
    sys.modules["tools.clarify_gateway"] = fake_cg
    try:
        data = types.SimpleNamespace(
            event=types.SimpleNamespace(
                action=types.SimpleNamespace(value={
                    "larkdeck_action": "clarify", "clarify_id": "cid-3",
                    "answer": cards.OTHER_VALUE}),
                operator=types.SimpleNamespace(open_id="ou_ok")),
        )
        raw._on_card_action_trigger(data)
        _run(raw.submitted[0])
        assert resolved == [("cid-3", "__await_text__")], resolved
    finally:
        sys.modules.pop("tools", None)
        sys.modules.pop("tools.clarify_gateway", None)


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
    """页脚确实由「配置 + 钩子快照」拼出来，且开关立刻生效。"""
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
        assert adapter.LarkDeckMixin._ld_footer() == "🤖 Test Model · ctx 1k/10k · 10%"
        with_time = adapter.LarkDeckMixin._ld_footer(time.monotonic() - 12.3)
        assert with_time.endswith("⏱ 12.3s")

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
        assert "1 次工具调用" in title, title
        texts = " ".join(e.get("content", "") for e in node["elements"])
        assert "先想一下。" in texts and "read_file" in texts and "✅" in texts

        # 只有推理（没有工具）也给面板，标题退回通用文案
        panel.reset()
        panel.record_reasoning("s1", "t1", "只有推理。")
        only = adapter.LarkDeckMixin._ld_panel()
        assert only is not None
        assert only["header"]["title"]["content"] == i18n.t("panel.title", i18n.ZH)

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
    panel.reset()
    limit_before = panel._MAX_REASONING_CHARS
    try:
        # 片段合并：大量小片段最终会被压成单段，内容不丢。
        for i in range(panel._PARTS_COMPACT_AT + 50):
            panel.record_reasoning("s1", "t1", "x")
        state = panel._STATE["s1"]
        assert len(state["reasoning_parts"]) < panel._PARTS_COMPACT_AT
        assert panel.snapshot()["reasoning"] == "x" * (panel._PARTS_COMPACT_AT + 50)
        # 上限：超长时保留最早部分（渲染层再截断并留痕）。
        panel.reset()
        panel._MAX_REASONING_CHARS = 10
        panel.record_reasoning("s1", "t1", "0123456789ABCDEF")
        assert panel.snapshot()["reasoning"] == "0123456789"
    finally:
        panel._MAX_REASONING_CHARS = limit_before
        panel.reset()


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
