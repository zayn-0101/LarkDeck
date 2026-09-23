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
    # ⚠️ 这一段断言的是 **v0.7.1 的冻结记录**（当时确实是「青绿 Running / 绿色 Succeeded」）。
    # 2026-09-22 的 C1 + 默认② 把生产改成了「蓝色 Running / 绿色 ✓」——历史文件不改写，
    # 现行口径由 `_assert_tool_status_literals()` 与 `test_units.py::test_v072_*` 断言。
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
    # ⚠️ v0.7.1 夹具记的是 aiduPOP 的元素 id（`loading_icon`），我们的命名空间里叫
    #    `loading_hint` —— 它是**冻结的历史证据**、不是生产契约（v0.7.2 起由
    #    `_assert_v072_contracts()` 把生产常量钉住，并在 docs/audits/v0.7.2/loading-asset.md 说明）。
    assert anchors["loading"] == "loading_icon"
    assert anchors["down_icon"] == "down-small-ccm_outlined"
    assert "footer_order" in data["panel_header"], "visual-tokens.json 缺 footer_order"
    # ⚠️ 这条断言的是 **v0.7.1 的冻结记录**（当时页脚确实带短码）。用户 2026-09-21 推翻了这个口径
    #    （「我从来没有提过这个要求」）⇒ 生产契约改在 `_assert_v072_contracts()` 里断言
    #    「页脚**没有**短码」。历史文件不改写，所以这里保留旧值 —— 但别把它当成现行口径。
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
                detail="/tmp/a.txt", icon_token=cardview.ICON_TOKENS["read"]),
                # ⚠️ 必须**同时**有运行中那一行：running 分支（自制动图 + 不带 size）与
                # 已结束分支（静态线性图标）是两条不同的返回，少一条就有半条路不过白名单
                # （2026-09-22 审计发现夹具只有 ok/error，running 分支从来没被这条门禁走到）。
                cardview.ToolStepView(
                    name="terminal", title="terminal", status="running",
                    icon_token=cardview.ICON_TOKENS["terminal"])],
            reasoning_rounds=[cardview.ReasoningRoundView(
                index=0, text="原始推理", elapsed_ms=1200, finalized=True),
                cardview.ReasoningRoundView(
                    index=1, text="还在想", finalized=False)],
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
    # A1（2026-09-22 拍板）：**已结束轮折叠**（finalized=True 的那一轮）
    assert reasoning["expanded"] is False, reasoning
    assert reasoning["vertical_spacing"] == "8px", reasoning
    assert reasoning["padding"] == "8px 8px 8px 8px", reasoning
    assert reasoning["elements"][0]["content"] == "原始推理", reasoning
    # A1：**当前轮展开**（finalized=False）
    cur = panel["elements"][1]
    assert cur["tag"] == "collapsible_panel" and cur["expanded"] is True, cur
    assert cur["elements"][0]["content"] == "还在想", cur
    title_md, detail = panel["elements"][2], panel["elements"][3]
    # 用户 2026-09-22 真机三臂选版：`markdown.icon`（官方叫「前缀图标」）0px 垂直偏差；
    # 元素级 `div.icon` 实测图标高 3px（2026-09-21 用户嫌「偏上」的就是它）⇒ 默认走前缀图标。
    assert title_md["tag"] == "markdown", title_md
    assert title_md["icon"] == {"tag": "standard_icon", "token": "file-link-text_outlined",
                                "color": "grey"}, title_md
    assert title_md["content"].startswith("**读取文件**"), title_md["content"]
    # 默认②（2026-09-22）：**只有成功**换成绿色 `✓`（其余状态保留词）
    assert "<font color='green'>✓</font>" in title_md["content"], title_md["content"]
    assert "icon" not in title_md.get("text", {}), title_md
    # D1′：运行中那一行 = 动图（`markdown.icon` + `custom_icon`，**不带 size**；
    # 生效 key 走 `spinner_img_key()` —— P2 完成前它是借来的共享 key，过渡态）
    run_md = panel["elements"][4]
    assert run_md["tag"] == "markdown", run_md
    assert run_md["icon"] == {"tag": "custom_icon",
                              "img_key": cardview.spinner_img_key()}, run_md["icon"]
    assert "size" not in run_md["icon"], run_md["icon"]
    assert "<font color='blue'>Running</font>" in run_md["content"], run_md["content"]
    assert detail["tag"] == "markdown" and detail["margin"] == "0px 0px 0px 22px", detail
    assert detail["icon"] == {"tag": "standard_icon", "token": "tool-indent_outlined",
                              "color": "grey"}, detail
    # 灰色**只能写进 content**：`markdown` 没有 `text_color` 字段（真机 200621 实测，整卡被拒）
    assert detail["content"] == "<font color='grey'>/tmp/a.txt</font>", detail
    assert "text_color" not in detail, detail
    # v0.7.3 + 2026-09-23 真机：细节行（markdown / plain_text）= x-small；Error 块用
    # `markdown` + 逐行 inline code + x-small（fenced 代码块字号被客户端固定，`text_size` 无效）；
    # 标题与生产常量仍是 notation。
    assert detail["text_size"] == "x-small", detail
    err_step = cardview.ToolStepView(
        name="terminal", title="terminal", status="error",
        detail='{"command": "false"}', error_block="boom")
    err_els = cardview.tool_step_elements(err_step, "line")
    assert len(err_els) >= 3, err_els
    assert err_els[0].get("text_size") == "notation", err_els[0]
    assert err_els[1].get("text_size") == "x-small", err_els[1]
    assert err_els[2].get("tag") == "markdown", err_els[2]
    assert err_els[2].get("text_size") == "x-small", err_els[2]
    assert err_els[2].get("icon"), err_els[2]
    assert "```" not in str(err_els[2].get("content")), err_els[2]
    assert "`boom" in str(err_els[2].get("content")), err_els[2]
    assert cardview.PANEL_TEXT_SIZE == "notation"
    panel_op = {"partial_element": cardview.panel_partial(view.panel)}
    assert "tag" not in panel_op["partial_element"], panel_op
    assert "text_size" not in panel_op["partial_element"], panel_op
    # A1（2026-09-22 拍板）：**中间帧固定不带 `expanded`** —— 带了就是每帧重放建卡时的
    # 展开态，用户手动收起的面板会被下一个 token 顶回展开（真机实测）。
    assert "expanded" not in panel_op["partial_element"], panel_op
    assert panel_op["partial_element"]["elements"], panel_op
    assert [e.get("element_id") for e in body] == ["answer", "panel", "footer"], body


#: 官方 2.0 字段白名单（只登记**我们会发出去**的元素/文本/图标节点）。
#: 出处（2026-09-22 逐页核对）：富文本 `card-json-v2-components/content-components/rich-text`、
#: 普通文本 `…/content-components/plain-text`（组件 tag 是 `div`）、折叠面板
#: `…/containers/collapsible-panel`。
#: 为什么要有这道门禁：服务端对**未知字段**是 `200621` **整卡被拒**（不是忽略），而且一次只报一个
#: 字段 —— 2026-09-22 真机探针才发现「`markdown` 上写了 `text_color`」和「`icon` 挂进 `text`」。
#: 本地白名单能在提交前拦住整类故障（长回合 ⇒ 纯文本回落 ⇒「卡片 + 灰色气泡」两张）。
_ALLOWED_COMPONENT_FIELDS = {
    "markdown": {"tag", "element_id", "margin", "content", "text_size", "text_align", "icon", "href"},
    "div": {"tag", "element_id", "margin", "width", "text", "icon", "fields", "extra"},
    "collapsible_panel": {"tag", "element_id", "margin", "expanded", "vertical_spacing",
                          "vertical_align", "horizontal_spacing", "horizontal_align", "direction",
                          "padding", "background_color", "header", "border", "elements"},
}
#: 文本子对象（`div.text`、面板标题）。`i18n_content` 只出现在面板标题（生产在用的 i18n 形态）。
_ALLOWED_TEXT_FIELDS = {"tag", "element_id", "content", "text_size", "text_color", "text_align", "lines"}
_ALLOWED_ICON_FIELDS = {"tag", "token", "color", "size", "img_key"}
_ALLOWED_HEADER_FIELDS = {"title", "vertical_align", "icon", "icon_position",
                          "icon_expanded_angle", "background_color", "width", "padding"}


def _check_icon(node: dict, where: str, *, host: str = "") -> None:
    """图标字段白名单 + 宿主分档（2026-09-22 真机 200621 的产物）。

    ⚠️ ``size`` **按宿主分档**（官方 2.0 字段表逐页核对）：
      * ``div`` 的组件级 ``icon`` **有** ``size``（加载指示就靠它定 16px）；
      * ``markdown`` 的 ``icon`` 是「**前缀图标**」（单图标槽）⇒ **没有** ``size`` 字段，
        带上它就是 ``200621`` **整卡被拒**（不是忽略）—— 工具行正是这个宿主。
    ``custom_icon`` 必须给 ``img_key``：空 key 在真机是 ``300313``（元素写失败），
    会把整条结构化装饰链带走（静默回落到纯文本）。
    """
    assert node.get("tag") in ("standard_icon", "custom_icon"), f"{where}: 图标 tag 非法：{node}"
    unknown = sorted(set(node) - _ALLOWED_ICON_FIELDS)
    assert not unknown, f"{where}: 图标字段不在官方白名单里：{unknown}"
    if node.get("tag") == "standard_icon":
        assert node.get("token"), f"{where}: standard_icon 必须给 token"
    else:
        assert str(node.get("img_key") or "").strip(), \
            f"{where}: custom_icon 必须给 img_key（空 key 真机 300313）"
    if host == "markdown":
        assert "size" not in node, \
            f"{where}: markdown 的前缀图标槽没有 size 字段（真机 200621 整卡被拒）：{node}"


def _check_text(node: dict, where: str, *, allow_i18n: bool = False) -> None:
    allowed = _ALLOWED_TEXT_FIELDS | ({"i18n_content"} if allow_i18n else set())
    assert node.get("tag") in ("plain_text", "lark_md"), f"{where}: 文本 tag 非法：{node}"
    unknown = sorted(set(node) - allowed)
    assert not unknown, f"{where}: 文本字段不在官方白名单里：{unknown}（查 rich-text/plain-text 字段表）"
    assert "icon" not in node, (
        f"{where}: 前缀图标必须挂**组件级** `icon` —— `text` 里没有 icon 字段（真机 200621 整卡被拒）")
    if node.get("tag") != "plain_text":
        assert "text_color" not in node, (
            f"{where}: `text_color` 只对 plain_text 生效；markdown/lark_md 上写它会 200621 整卡被拒，"
            "灰色要写进 content：<font color='grey'>…</font>")


def _check_element(node: dict, where: str) -> None:
    tag = node.get("tag")
    assert tag in _ALLOWED_COMPONENT_FIELDS, (
        f"{where}: 未登记的元素 tag {tag!r} —— 新增元素前先核官方字段表并登记白名单")
    unknown = sorted(set(node) - _ALLOWED_COMPONENT_FIELDS[tag])
    assert not unknown, f"{where}: 元素字段不在官方白名单里：{unknown}"
    if "text" in node:
        _check_text(node["text"], f"{where}.text")
    if "icon" in node:
        _check_icon(node["icon"], f"{where}.icon", host=str(tag or ""))
    if tag == "collapsible_panel":
        header = node.get("header") or {}
        unknown = sorted(set(header) - _ALLOWED_HEADER_FIELDS)
        assert not unknown, f"{where}.header: 字段不在官方白名单里：{unknown}"
        if "icon" in header:
            _check_icon(header["icon"], f"{where}.header.icon")
        if "title" in header:
            _check_text(header["title"], f"{where}.header.title", allow_i18n=True)
        for i, child in enumerate(node.get("elements") or []):
            _check_element(child, f"{where}.elements[{i}]")


def _assert_panel_element_fields() -> None:
    """面板元素树字段白名单（两种图标模式都过一遍）。

    服务端对未知字段**整卡被拒**、且一次只报一个 —— 这条门禁是 2026-09-22 真机 200621 的产物：
    本地必须能一次报出**所有**越界字段（`_check_*` 不做「遇到第一个就返回」）。
    """
    view = cardview.PanelView(
        title="🛠️ 工具执行 · 2 步",
        collapsed_hint="还有 12 步未显示（折叠提示也带前缀图标）",
        tools=[
            cardview.ToolStepView(name="terminal", title="terminal", status="ok", duration_ms=1,
                                  detail='{"command": "df -h"}',
                                  icon_token=cardview.ICON_TOKENS["terminal"]),
            cardview.ToolStepView(name="web_search", title="web_search", status="error",
                                  error_block="403 Forbidden",
                                  icon_token=cardview.ICON_TOKENS["search"]),
        ],
    )
    for mode in ("line", "emoji"):
        view.tool_icon_mode = mode
        for i, node in enumerate(cardview.panel_elements(view)):
            _check_element(node, f"panel_elements[{i}]（tool_icon_mode={mode}）")
        _check_element(cardview.panel_shell(view), f"panel_shell（tool_icon_mode={mode}）")


def _assert_v072_contracts() -> None:
    """v0.7.2 契约：图标**全表**（28 条 CLS + 顺序 + 登记偏差 + 兜底）与页脚字段（**没有**短码）。

    ⚠️ v0.7.1 的 `visual-tokens.json` 是**冻结的历史产物**（只有 14 个图标键、`footer_order`
    里还写着 `short_code`）—— 那份记录**不许改写**（它证明当时的状态），但它**不再**是生产契约。
    生产契约在 `docs/audits/v0.7.2/`：`tool-icons.json`（逐条 + 顺序 + `local_extra`）与
    `footer-contract.json`。

    为什么这条必须在**门禁**里而不是只在单测里：审计 C 实测把 `("exec", "setting_outlined")`
    改成 `robot_outlined` 时五门禁全绿 —— 而 `check_cardview.py` 正是「视觉表」那道门禁，
    它当时只比对 v0.7.1 JSON 的 14 个旧键 ⇒ 生产表怎么漂移都看不见。
    """
    # ① **独立字面量**（审计 C2 的绿变异）：把生产表与契约 JSON *同时*改掉（并按 docstring 的
    #    「有意变更 ⇒ 重生成夹具」流程重跑黄金夹具）时，两边自比会全绿 —— 所以门禁自己必须
    #    有一小份**不来自生产、也不来自 JSON** 的字面量，钉住最吃重的几条与 spinner 资产。
    assert cardview.SPINNER_IMG_KEY == "img_v3_02vb_496bec09-4b43-4773-ad6b-0cdd103cd2bg", \
        f"spinner 资产 key 被改动了（真机无效 asset 会 300313 拖垮装饰链）：{cardview.SPINNER_IMG_KEY}"
    literal_icons = {
        "exec": "setting_outlined", "bash": "setting_outlined",
        "command": "setting_outlined", "run": "setting_outlined",
        "read": "file-link-text_outlined", "open": "file-link-text_outlined",
        "write": "edit_outlined", "edit": "edit_outlined",
        "web_search": "search_outlined", "web_fetch": "language_outlined",
        "browser": "browser-mac_outlined", "agent": "robot_outlined",
        "check": "list-check_outlined", "analyze": "report_outlined",
        "clarify": "chat_outlined",
    }
    got_literal = dict(cardview.ICON_ALIASES)
    for alias, token in literal_icons.items():
        assert got_literal.get(alias) == token, \
            f"{alias}: 生产表 {got_literal.get(alias)!r} != 字面量 {token!r}"
    # Hermes 真实工具名的**常用族**：不许落兜底（审计 B 实测 29/42 落兜底是用户可见落差；
    # 这张表是**本地登记扩展**，不参与 CLS 逐条比对）
    literal_local = {
        "terminal": "setting_outlined", "execute_code": "setting_outlined",
        "delegate_task": "robot_outlined", "skills_list": "app-default_outlined",
        "session_search": "search_outlined", "todo_list": "list-check_outlined",
        "memory": "folder_outlined", "cronjob_manage": "list-check_outlined",
        "computer_use": "browser-mac_outlined", "text_to_speech": "language_outlined",
        "patch": "edit_outlined", "send_message": "chat_outlined",
    }
    def _resolve(name: str) -> str:
        """工具名 → token（与 `_ld_icon_token` 同语义：精确或 `alias_` 前缀；CLS 表优先）。"""
        for alias, token in list(cardview.ICON_ALIASES) + list(cardview.ICON_ALIASES_LOCAL_EXTRA):
            if name == alias or name.startswith(alias + "_"):
                return token
        return cardview.ICON_FALLBACK

    for name, token in literal_local.items():
        assert _resolve(name) == token, \
            f"真实工具名 {name}: {_resolve(name)!r} != 字面量 {token!r}（用户会看到兜底图标）"
    # spinner 元素的**逐字段字面量**（审计 C2 的 G2：`startswith("img_v")` + 与生产常量自比
    # 会被 `img_v3_FAKE` + 同步重生成夹具绕过 ⇒ 这里把三个字段写成字面量）
    _hint = cardview.loading_hint_element()
    assert _hint["icon"] == {"tag": "custom_icon",
                             "img_key": "img_v3_0215p_a0b0bd11-a182-433f-9647-8573d0dd7efg",
                             "size": "16px 16px"}, _hint["icon"]
    assert _hint["text"] == {"tag": "plain_text", "content": " "}, _hint["text"]

    icons = json.loads(
        (_REPO / "docs" / "audits" / "v0.7.2" / "tool-icons.json").read_text(encoding="utf-8"))
    prod = list(cardview.ICON_ALIASES)
    want = list(icons["tool_icons"].items())
    assert prod == want, f"生产图标表与冻结契约不等：{dict(prod)} != {dict(want)}"
    assert list(cardview.ICON_ALIASES_LOCAL_EXTRA) == list(
        icons["local_extra"].items()), cardview.ICON_ALIASES_LOCAL_EXTRA
    assert cardview.ICON_FALLBACK == icons["fallback"], cardview.ICON_FALLBACK
    for alias, token in want + list(icons["local_extra"].items()):
        assert token.endswith("_outlined") and not any(ord(ch) > 0x2000 for ch in token), \
            f"{alias}: 图标必须是 standard_icon 字符串 token，不能是 emoji：{token!r}"

    # ⚠️ **P2 的强制守卫**：`assets/spinner-tool.gif` 一旦入库，生效 key 就必须是自研那条
    # （`SPINNER_TOOL_IMG_KEY != SPINNER_IMG_KEY`）——D1′ 的验收条件之一就是「不是复用旧 key」。
    # 资产还没入库时这条不成立（过渡态：`SPINNER_TOOL_IMG_KEY` 只是共享 key 的别名，见
    # `docs/audits/v0.7.2/loading-asset-v2.md`）。
    if (_REPO / "assets" / "spinner-tool.gif").exists():
        assert cardview.SPINNER_TOOL_IMG_KEY not in ("", cardview.SPINNER_IMG_KEY), (
            "自研动图入库后 SPINNER_TOOL_IMG_KEY 仍然是旧 key（等于没换）："
            f"{cardview.SPINNER_TOOL_IMG_KEY!r}")
        assert cardview.spinner_img_key() == cardview.SPINNER_TOOL_IMG_KEY, (
            f"生效 key 必须优先取自研资产：{cardview.spinner_img_key()!r}")

    foot = json.loads(
        (_REPO / "docs" / "audits" / "v0.7.2" / "footer-contract.json").read_text(encoding="utf-8"))
    assert foot["footer_order"] == ["status", "elapsed", "model", "context"], foot
    assert foot["short_code_visible"] is False, foot
    # B1（2026-09-22）：页脚**段前缀** emoji 去掉（状态词里的 ✅/❌/⛔ 保留）。契约文件与门禁
    # 同时钉 —— 只改契约没人看，只改门禁则「契约文件」会与实现漂移。
    assert foot["segment_prefix_emoji"] is False, foot
    # 元素 id 的生产常量（v0.7.1 夹具里那个 `loading_icon` 是 aiduPOP 的命名，不是我们的）
    assert cardview.LOADING_HINT_ID == "loading_hint", cardview.LOADING_HINT_ID

    # 生产源码级的反面守卫：**真正的代码行**里不许再出现短码 emoji
    # （注释与 docstring 里的历史说明不算 —— 它们正是「为什么去掉」的证据，不该被清掉）
    for name in ("adapter.py", "cardview.py", "cards.py"):
        code = "\n".join(_executable_lines(
            (_REPO / "core" / name).read_text(encoding="utf-8")))
        assert "\U0001f516" not in code, f"{name} 的代码行里又出现了短码（用户可见处不许有）"


def _executable_lines(src: str) -> list:
    """剥掉注释行与（多行/单行）docstring 后剩下的行 —— 只用来做源码级反面守卫。"""
    out, in_doc, delim = [], False, ""
    for ln in src.splitlines():
        stripped = ln.lstrip()
        if in_doc:
            if delim in ln:
                in_doc = False
            continue
        if stripped.startswith("#"):
            continue
        if (stripped.count('"""') >= 2 or stripped.count("'''") >= 2):
            continue                      # 单行 docstring
        for d in ('"""', "'''"):
            idx = ln.find(d)
            if idx != -1 and ln.count(d) == 1:
                in_doc, delim = True, d
                ln = ln[:idx]
                break
        if ln.strip():
            out.append(ln)
    return out


def _assert_tool_status_literals() -> None:
    # token 表必须与生产状态映射同源核对，不能只锁 JSON 自己；键存在必须先显式断言。
    for key in ("running", "ok", "success", "error"):
        assert key in cards._TOOL_STATUS_STYLES, f"生产缺状态映射键：{key}"
    assert cards._TOOL_STATUS_STYLES["running"] == ("Running", "blue")
    assert cards._TOOL_STATUS_STYLES["ok"] == ("✓", "green")
    assert cards._TOOL_STATUS_STYLES["success"] == ("✓", "green")
    assert cards._TOOL_STATUS_STYLES["error"] == ("Failed", "red")
    # 两张表**逐键直接对等**（结构化侧 = `cardview.ToolStepView.status_style`）：
    # 只靠夹具比对时，两表一起漂移会在同一次提交里同时改掉夹具 ⇒ 抓不住（审计 C2 的绿变异）。
    for status in ("running", "ok", "success", "error", "blocked", "cancelled", "canceled",
                   "skipped", "timeout"):
        assert cardview.ToolStepView(name="x", title="x", status=status).status_style == \
            cards._TOOL_STATUS_STYLES[status], status
    # 未知状态：兜底成「首字母大写 + 灰」，不许抛（Hermes 会 emit 没登记的状态）
    assert cardview.ToolStepView(name="x", title="x", status="weird").status_style == \
        ("Weird", "grey")
    cards.set_color_tags_enabled(True)
    try:
        step = cards.tool_step("read_file", status="ok", duration_ms=120,
                               preview='{"path": "/tmp/a.txt"}', theme="ap_lite")
        assert "<font color='green'>✓</font>" in step, step
        assert "↳ /tmp/a.txt" in step, step
        err = cards.tool_step("read_file", status="error", duration_ms=120,
                              preview='{"path": "/tmp/a.txt"}', theme="ap_lite")
        assert "<font color='red'>Failed</font>" in err, err
        run = cards.tool_step("read_file", status="running", duration_ms=120,
                              preview='{"path": "/tmp/a.txt"}', theme="ap_lite")
        assert "<font color='blue'>Running</font>" in run, run
    finally:
        cards.set_color_tags_enabled(False)


def main() -> int:
    _assert_token_file()
    _assert_v072_contracts()
    _assert_legacy_entity_card_content()
    _assert_tool_status_literals()
    _assert_structured_builder()
    _assert_panel_element_fields()
    print("CARDVIEW OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
