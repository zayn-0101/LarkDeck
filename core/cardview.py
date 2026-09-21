"""LarkDeck v0.7.1 结构化 CardView（V1）。

纯数据 + 纯函数；不碰 IO / SDK。adapter 负责把这里的 dict 喂给官方 CardKit API。
结构以 CLS `cardkit/builder.py` 为真源：
  * 工具标题：div(icon=standard_icon, text=lark_md)；
  * 细节/输出：独立 div + margin 22px + text；
  * 推理轮：嵌套 collapsible_panel（8px，灰色 notation 标题，箭头右置）；
  * partial_update_element 的 partial_element 顶层**不带 tag**（300312）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import i18n as _i18n

PANEL_RADIUS = "5px"
PANEL_PADDING = "8px 8px 8px 8px"
PANEL_SPACING = "4px"
REASONING_SPACING = "8px"
MARKDOWN_MARGIN = "0px 0px 0px 0px"
TOOL_DETAIL_INDENT = "0px 0px 0px 22px"
PANEL_TEXT_SIZE = "notation"
ICON_SIZE = "16px 16px"
ICON_COLOR = "grey"
DOWN_ICON = "down-small-ccm_outlined"

#: 工具名 → `standard_icon` token。**与 CLS `streaming/tooluse.py` 的别名表逐条对齐**
#: （V4.8：用户真机反馈「图标和对标插件不一样」——过去只有 skill/read/edit/search/fetch 等
#: 少数键，`write_file`/`bash`/`exec`/`web_fetch` 这类都掉进 fallback，字形与别家完全不同）。
ICON_TOKENS: Dict[str, str] = {
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
    "fallback": "tool_02",          # CLS 的兜底 token（`step.get("icon", "tool_02")`）
    # ——— CLS 别名表的其余条目（顺序有意义：先匹配到的赢）———
    "write": "edit_outlined",
    "open": "file-link-text_outlined",
    "exec": "setting_outlined",
    "bash": "setting_outlined",
    "command": "setting_outlined",
    "run": "setting_outlined",
    "web_search": "search_outlined",
    "web-search": "search_outlined",
    "web_fetch": "language_outlined",
    "web-fetch": "language_outlined",
    "playwright": "browser-mac_outlined",
    "navigate": "browser-mac_outlined",
    "patch": "edit_outlined",
    "todo": "list-check_outlined",
}


@dataclass
class ToolStepView:
    name: str
    title: str
    status: str = "running"
    duration_ms: Optional[int] = None
    detail: str = ""
    result_block: str = ""
    error_block: str = ""
    icon_token: str = ICON_TOKENS["fallback"]

    @property
    def status_style(self) -> tuple[str, str]:
        """状态词 + 颜色。**必须与 `cards._TOOL_STATUS_STYLES` 同表**（V4.5，审计 B 中-5）：
        结构化只认 4 个键时，`blocked`/`timeout` 会掉进 fallback 变成**灰色**、词也变
        （`Timeout` ≠ legacy 的 `Timed out`）—— 同一件事在两条车道上两种颜色是最难查的漂移。
        """
        return {
            "running": ("Running", "turquoise"),
            "ok": ("Succeeded", "green"),
            "success": ("Succeeded", "green"),
            "error": ("Failed", "red"),
            "blocked": ("Blocked", "red"),
            "cancelled": ("Cancelled", "grey"),
            "canceled": ("Cancelled", "grey"),
            "skipped": ("Skipped", "grey"),
            "timeout": ("Timed out", "red"),
        }.get(self.status, (self.status.capitalize() or "Unknown", "grey"))


@dataclass
class ReasoningRoundView:
    index: int
    text: str = ""
    elapsed_ms: Optional[int] = None
    finalized: bool = False


@dataclass
class PanelView:
    title: str = "💭 思考 0s · 🛠️ 工具执行 · 0 步"
    expanded: bool = False
    border: str = "grey"
    reasoning_rounds: List[ReasoningRoundView] = field(default_factory=list)
    tools: List[ToolStepView] = field(default_factory=list)
    collapsed_hint: str = ""


@dataclass
class CardView:
    answer: str = ""
    #: ``unified_panel: false`` ⇒ **整块面板不进卡**（legacy 车道同一条门禁；V4.5）
    panel_enabled: bool = True
    header_enabled: bool = True
    header_status: str = "processing"
    header_title: str = "🫧 处理中…"
    panel: PanelView = field(default_factory=PanelView)
    footer: str = ""
    footer_enabled: bool = True
    engine: str = "structured"
    engine_stamp: str = "structured"


def _title_node(title: Any, **extra: Any) -> Dict[str, Any]:
    """标题 → `plain_text` 节点。`title` 可以是裸字符串，也可以是 i18n 节点（自带
    `content` + `i18n_content`，中英文客户端各显示各的）。V4.13：结构化标题过去是硬编码中文。"""
    node = ({"tag": "plain_text", **title} if isinstance(title, dict)
            else {"tag": "plain_text", "content": str(title)})
    node.update(extra)
    return node


def _tool_title_div(step: ToolStepView) -> Dict[str, Any]:
    status_text, color = step.status_style
    duration = f" ({step.duration_ms} ms)" if step.duration_ms else ""
    content = f"**{step.title}**{duration} · <font color='{color}'>{status_text}</font>"
    return {
        "tag": "div",
        "icon": {"tag": "standard_icon", "token": step.icon_token, "color": ICON_COLOR},
        "text": {"tag": "lark_md", "content": content, "text_size": PANEL_TEXT_SIZE},
    }


def _tool_detail_div(text: str) -> Dict[str, Any]:
    return {
        "tag": "div",
        "margin": TOOL_DETAIL_INDENT,
        "text": {"tag": "plain_text", "content": f"↳ {text}",
                 "text_color": "grey", "text_size": PANEL_TEXT_SIZE},
    }


def _tool_output_div(block: str, label: str) -> Dict[str, Any]:
    return {
        "tag": "div",
        "margin": TOOL_DETAIL_INDENT,
        "text": {"tag": "lark_md", "content": f"**{label}**\n```\n{block}\n```",
                 "text_size": PANEL_TEXT_SIZE},
    }


def tool_step_elements(step: ToolStepView) -> List[Dict[str, Any]]:
    """一个工具步的 1–3 个元素（CLS builder.py:146-190）。"""
    elements: List[Dict[str, Any]] = [_tool_title_div(step)]
    if step.detail:
        elements.append(_tool_detail_div(step.detail))
    # ⚠️ V4.8（用户真机口径优先）：**成功步不再挂 Result 大代码块** —— 每步一个 fenced block
    # 又长又丑，且对标插件只在有错误/需要排查时才展开输出。失败步的 Error 块保留
    # （plan 里「Result/Error 块」那条按用户这份反馈收窄，共识文档里有记）。
    if step.error_block:
        elements.append(_tool_output_div(step.error_block, "Error"))
    return elements


def reasoning_panel(round_view: ReasoningRoundView) -> Dict[str, Any]:
    """推理轮：嵌套 collapsible_panel（FC/CLS 终态形态）。"""
    title: Any = _i18n.i18n_text("panel.reasoning_round", n=round_view.index + 1)
    if round_view.elapsed_ms:
        suffix = f" · {round_view.elapsed_ms / 1000:.1f}s"
        title = {**title,
                 "content": str(title.get("content", "")) + suffix,
                 "i18n_content": {lang: str(val) + suffix
                                  for lang, val in (title.get("i18n_content") or {}).items()}}
    return {
        "tag": "collapsible_panel",
        "element_id": f"reasoning_{round_view.index}_panel",
        "header": {
            "title": _title_node(title, text_size=PANEL_TEXT_SIZE, text_color="grey"),
            "vertical_align": "center",
            "icon": {"tag": "standard_icon", "token": DOWN_ICON,
                     "size": ICON_SIZE, "color": ICON_COLOR},
            "icon_position": "right",
            "icon_expanded_angle": -180,
        },
        "expanded": False,
        "vertical_spacing": REASONING_SPACING,
        "padding": PANEL_PADDING,
        "border": {"color": "grey", "corner_radius": PANEL_RADIUS},
        "elements": [{
            "tag": "markdown",
            "element_id": f"reasoning_{round_view.index}_text",
            "content": round_view.text,
            "text_size": PANEL_TEXT_SIZE,
            "margin": MARKDOWN_MARGIN,
        }],
    }


def panel_elements(view: PanelView) -> List[Dict[str, Any]]:
    elements: List[Dict[str, Any]] = []
    if view.collapsed_hint:
        elements.append({"tag": "plain_text", "content": view.collapsed_hint,
                         "text_size": PANEL_TEXT_SIZE, "text_color": "grey"})
    for round_view in view.reasoning_rounds:
        elements.append(reasoning_panel(round_view))
    for step in view.tools:
        elements.extend(tool_step_elements(step))
    return elements


def panel_shell(view: PanelView, *, expanded: bool = False) -> Dict[str, Any]:
    return {
        "tag": "collapsible_panel",
        "element_id": "panel",
        "header": {
            "title": _title_node(view.title, text_size=PANEL_TEXT_SIZE,
                                 text_color="grey"),
            "vertical_align": "center",
            "icon": {"tag": "standard_icon", "token": DOWN_ICON,
                     "size": ICON_SIZE, "color": ICON_COLOR},
            "icon_position": "right",
            "icon_expanded_angle": -180,
        },
        "expanded": expanded,
        "vertical_spacing": PANEL_SPACING,
        "padding": PANEL_PADDING,
        "border": {"color": view.border, "corner_radius": PANEL_RADIUS},
        "elements": panel_elements(view),
    }


def panel_partial(view: PanelView) -> Dict[str, Any]:
    """partial_update_element 用的面板字段：**顶层不带 tag**（300312）。"""
    shell = panel_shell(view)
    return {key: shell[key] for key in
            ("header", "expanded", "vertical_spacing", "border", "elements")}


def entity_skeleton(view: CardView) -> Dict[str, Any]:
    """建实体卡：answer + panel + footer；卡片级 header 单独放 card.header。"""
    elements: List[Dict[str, Any]] = [{
        "tag": "markdown", "element_id": "answer", "content": view.answer or " ",
        "margin": MARKDOWN_MARGIN,
    }]
    if view.panel_enabled:
        elements.append(panel_shell(view.panel))
    if view.footer_enabled:
        elements.append({"tag": "markdown", "element_id": "footer",
                         "content": view.footer or " ",
                         "text_size": PANEL_TEXT_SIZE, "margin": MARKDOWN_MARGIN})
    card: Dict[str, Any] = {
        "schema": "2.0",
        "config": {"streaming_mode": True},
        "body": {"elements": elements},
    }
    if view.header_enabled:
        card["header"] = {
            "template": {"processing": "blue", "completed": "green",
                         "stopped": "yellow", "error": "red"}.get(view.header_status, "blue"),
            "title": _title_node(view.header_title),
        }
    return card



