#!/usr/bin/env python3
"""v0.7.1 视觉门禁：卡片结构 + content 逐字 + token 表一致性。

V0 版本先冻结：
  * `visual-tokens.json` 的 token/图标/状态表（字面量断言，不从生产 import）；
  * 当前 legacy 实体卡的 element id 顺序与 content 逐字 round-trip；
  * tool_step 的状态色/状态词字面量。
V1 起在文件末尾追加 structured builder 的元素树 golden（tag/属性/token/缩进/字号/content）。

跑法：`python3 tests/check_cardview.py`（EXIT 0 = OK）。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent
for _p in (str(_REPO.parent), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from larkdeck.core import cards  # noqa: E402
from larkdeck.core import cardview  # noqa: E402


def _assert_token_file() -> None:
    path = _REPO / "docs" / "audits" / "v0.7.1-visual" / "visual-tokens.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("tokens", "status", "tool_icons", "tool_status", "element_anchors", "panel_header"):
        assert key in data, f"visual-tokens.json 缺顶层键：{key}"
    for key in ("panel_radius", "panel_padding", "panel_vertical_spacing",
                "reasoning_vertical_spacing", "markdown_margin", "tool_detail_indent",
                "panel_text_size", "icon_size", "icon_color", "icon_position",
                "icon_expanded_angle", "header_vertical_align", "expanded_default",
                "footer_hr"):
        assert key in data["tokens"], f"visual-tokens.json 缺 token：{key}"
    for key in ("processing", "completed", "stopped", "error"):
        assert key in data["status"], f"visual-tokens.json 缺状态：{key}"
    assert data["tokens"]["panel_radius"] == "5px", data["tokens"]
    assert data["tokens"]["panel_padding"] == "8px 8px 8px 8px"
    assert data["tokens"]["panel_vertical_spacing"] == "4px"
    assert data["tokens"]["reasoning_vertical_spacing"] == "8px"
    assert data["tokens"]["markdown_margin"] == "0px 0px 0px 0px"
    assert data["tokens"]["tool_detail_indent"] == "0px 0px 0px 22px"
    assert data["tokens"]["panel_text_size"] == "notation"
    assert data["tokens"]["icon_size"] == "16px 16px"
    assert data["tokens"]["icon_color"] == "grey"
    assert data["tokens"]["icon_position"] == "right"
    assert data["tokens"]["icon_expanded_angle"] == -180
    assert data["tokens"]["header_vertical_align"] == "center"
    assert data["tokens"]["expanded_default"] is False
    assert data["tokens"]["footer_hr"] is True
    assert data["tokens"]["body_text_size_pending_probe"] == ["normal", "normal_v2"]
    assert isinstance(data["unverified"], list) and data["unverified"], data.get("unverified")
    icons = data["tool_icons"]
    expected_icons = {
        "skill": "app-default_outlined",
        "read": "file-link-text_outlined",
        "edit": "edit_outlined",
        "search": "search_outlined",
        "fetch": "language_outlined",
        "grep": "doc-search_outlined",
        "glob": "folder_outlined",
        "terminal": "setting_outlined",
        "browser": "browser-mac_outlined",
        "agent": "robot_outlined",
        "check": "list-check_outlined",
        "analyze": "report_outlined",
        "clarify": "chat_outlined",
        "fallback": "setting-inter_outlined",
    }
    assert icons == expected_icons, icons
    for key, token in icons.items():
        assert token.endswith("_outlined") and not any(ord(ch) > 0x2000 for ch in token), \
            f"{key}: 图标必须是 standard_icon 字符串 token，不能是 emoji：{token!r}"
    expected_status = {
        "processing": {"header_template": "blue", "border": "grey", "title_zh": "🫧 处理中…"},
        "completed": {"header_template": "green", "border": "green", "title_zh": "✅ 已完成"},
        "stopped": {"header_template": "yellow", "border": "yellow", "title_zh": "⛔ 已停止"},
        "error": {"header_template": "red", "border": "red", "title_zh": "❌ 执行出错"},
    }
    assert data["status"] == expected_status, data["status"]
    assert data["tool_status"] == {
        "running": {"label": "Running", "color": "turquoise"},
        "success": {"label": "Succeeded", "color": "green"},
        "error": {"label": "Failed", "color": "red"},
    }, data["tool_status"]
    anchors = data["element_anchors"]
    for key in ("answer", "panel", "panel_body", "panel_tools", "footer", "loading", "down_icon"):
        assert key in anchors, f"visual-tokens.json 缺 element_anchors：{key}"
    assert anchors["answer"] == "answer" and anchors["panel"] == "panel"
    assert anchors["panel_body"] == "panel_body" and anchors["panel_tools"] == "panel_tools"
    assert anchors["loading"] == "loading_icon"
    assert anchors["down_icon"] == "down-small-ccm_outlined"
    assert "footer_order" in data["panel_header"], "visual-tokens.json 缺 footer_order"
    assert data["panel_header"]["footer_order"] == [
        "status", "elapsed", "model", "context", "short_code"]
    assert data["panel_header"]["format"] == "💭 思考 {elapsed}s · 🛠️ 工具执行 · {n} 步"
    assert data["panel_header"]["model_in_panel"] is False


def _assert_legacy_entity_card_content() -> None:
    card = cards.cardkit_entity_card(
        "ANSWER", panel_text="P_BODY", panel_tools_text="P_TOOLS",
        footer_text="FOOT", streaming=False)
    body = card["body"]["elements"]
    assert [e.get("element_id") for e in body] == ["answer", "panel", "footer"], body
    assert body[0]["content"] == "ANSWER", body[0]
    assert body[1]["tag"] == "collapsible_panel", body[1]
    panel_elems = body[1]["elements"]
    assert [e.get("element_id") for e in panel_elems] == ["panel_body", "panel_tools"], panel_elems
    assert panel_elems[0]["content"] == "P_BODY", panel_elems[0]
    assert panel_elems[1]["content"] == "P_TOOLS", panel_elems[1]
    assert body[2]["content"] == "FOOT", body[2]
    # V0 冻结的是 legacy 现状；V1 结构化切换时必须同 commit 更新为 5px 并声明行为变更。
    assert body[1]["border"]["corner_radius"] == "8px", body[1]["border"]


def _assert_structured_builder() -> None:
    """V1：CardView 结构化元素树 golden（tag/属性/token/缩进/字号/content）。"""
    view = cardview.CardView(
        answer="HELLO",
        footer="状态 · 1.2s · model · ctx · AB12",
        panel=cardview.PanelView(
            title="💭 思考 1.2s · 🛠️ 工具执行 · 1 步",
            tools=[cardview.ToolStepView(
                name="read_file", title="读取文件", status="ok", duration_ms=120,
                detail="/tmp/a.txt", icon_token=cardview.ICON_TOKENS["read"])],
            reasoning_rounds=[cardview.ReasoningRoundView(
                index=0, text="原始推理", elapsed_ms=1200)],
        ),
    )
    card = cardview.entity_skeleton(view)
    body = card["body"]["elements"]
    assert [e.get("element_id") for e in body] == ["answer", "panel", "footer"], body
    assert body[0]["content"] == "HELLO" and body[0]["tag"] == "markdown"
    assert card["header"]["template"] == "blue", card.get("header")
    panel = body[1]
    assert panel["tag"] == "collapsible_panel", panel
    assert panel["border"]["corner_radius"] == "5px", panel["border"]
    assert panel["vertical_spacing"] == "4px", panel
    assert panel["padding"] == "8px 8px 8px 8px", panel
    assert panel["header"]["title"]["content"] == "💭 思考 1.2s · 🛠️ 工具执行 · 1 步"
    reasoning = panel["elements"][0]
    assert reasoning["tag"] == "collapsible_panel", reasoning
    assert reasoning["expanded"] is False, reasoning
    assert reasoning["vertical_spacing"] == "8px", reasoning
    assert reasoning["padding"] == "8px 8px 8px 8px", reasoning
    assert reasoning["elements"][0]["content"] == "原始推理", reasoning
    title_div, detail = panel["elements"][1], panel["elements"][2]
    assert title_div["tag"] == "div", title_div
    assert title_div["icon"] == {"tag": "standard_icon", "token": "file-link-text_outlined",
                                 "color": "grey"}, title_div["icon"]
    assert "**读取文件**" in title_div["text"]["content"], title_div
    assert "Succeeded" in title_div["text"]["content"], title_div
    assert detail["tag"] == "div" and detail["margin"] == "0px 0px 0px 22px", detail
    assert detail["text"] == {"tag": "plain_text", "content": "↳ /tmp/a.txt",
                              "text_color": "grey", "text_size": "notation"}, detail["text"]
    panel_op = {"partial_element": cardview.panel_partial(view.panel)}
    assert "tag" not in panel_op["partial_element"], panel_op
    assert "text_size" not in panel_op["partial_element"], panel_op
    assert [e.get("element_id") for e in body] == ["answer", "panel", "footer"], body


def _assert_tool_status_literals() -> None:
    # token 表必须与生产状态映射同源核对，不能只锁 JSON 自己；键存在必须先显式断言。
    for key in ("running", "ok", "success", "error"):
        assert key in cards._TOOL_STATUS_STYLES, f"生产缺状态映射键：{key}"
    assert cards._TOOL_STATUS_STYLES["running"] == ("Running", "turquoise")
    assert cards._TOOL_STATUS_STYLES["ok"] == ("Succeeded", "green")
    assert cards._TOOL_STATUS_STYLES["success"] == ("Succeeded", "green")
    assert cards._TOOL_STATUS_STYLES["error"] == ("Failed", "red")
    cards.set_color_tags_enabled(True)
    try:
        step = cards.tool_step("read_file", status="ok", duration_ms=120,
                               preview='{"path": "/tmp/a.txt"}', theme="ap_lite")
        assert "<font color='green'>Succeeded</font>" in step, step
        assert "↳ /tmp/a.txt" in step, step
        err = cards.tool_step("read_file", status="error", duration_ms=120,
                              preview='{"path": "/tmp/a.txt"}', theme="ap_lite")
        assert "<font color='red'>Failed</font>" in err, err
        run = cards.tool_step("read_file", status="running", duration_ms=120,
                              preview='{"path": "/tmp/a.txt"}', theme="ap_lite")
        assert "<font color='turquoise'>Running</font>" in run, run
    finally:
        cards.set_color_tags_enabled(False)


def main() -> int:
    _assert_token_file()
    _assert_legacy_entity_card_content()
    _assert_tool_status_literals()
    _assert_structured_builder()
    print("CARDVIEW OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
