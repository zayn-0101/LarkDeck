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
from typing import Any, Dict, List, Optional, Tuple

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
#: 工具名 → `standard_icon` token：**逐条对齐 CLS `streaming/tooluse.py::_TOOL_DESCRIPTORS`**
#: （2026-09-21 审计 B 实测：我们过去用**子串匹配**，`mem0_search` 会被误判成 `search_outlined`；
#: 且缺 `task/spawn/determine/verify/summarize/analyze/prepare` 这些别名、fallback 也不是 CLS 那个）。
#: ⚠️ **有序**：CLS 的语义是「归一化（lower + `-`→`_`）后 **精确匹配或 `alias_` 前缀匹配**」，
#: 先匹配到的赢（见 `_resolve_tool_descriptor`）。
ICON_ALIASES: List[Tuple[str, str]] = [
    ("skill", "app-default_outlined"),
    ("read", "file-link-text_outlined"),
    ("open", "file-link-text_outlined"),
    ("write", "edit_outlined"),
    ("edit", "edit_outlined"),
    ("web_search", "search_outlined"),
    ("search", "search_outlined"),
    ("web_fetch", "language_outlined"),
    ("fetch", "language_outlined"),
    ("grep", "doc-search_outlined"),
    ("glob", "folder_outlined"),
    ("exec", "setting_outlined"),
    ("bash", "setting_outlined"),
    ("command", "setting_outlined"),
    ("run", "setting_outlined"),
    ("browser", "browser-mac_outlined"),
    ("playwright", "browser-mac_outlined"),
    ("navigate", "browser-mac_outlined"),
    ("agent", "robot_outlined"),
    ("task", "robot_outlined"),
    ("spawn", "robot_outlined"),
    ("check", "list-check_outlined"),
    ("determine", "list-check_outlined"),
    ("verify", "list-check_outlined"),
    ("summarize", "report_outlined"),
    ("analyze", "report_outlined"),
    ("prepare", "report_outlined"),
    ("clarify", "chat_outlined"),
]

#: 未知工具的图标 —— **CLS 的 fallback**（`tooluse.py:320`：`desc["icon"] if desc else "setting-inter_outlined"`）
ICON_FALLBACK = "setting-inter_outlined"

#: **登记在案的本地扩展**（CLS 表**不含**这些名字，但 Hermes 的真实工具名需要它们）。
#: 为什么需要（审计 B 实测）：把 Hermes `tools/*.py` 里 42 个真实工具名喂进来，**29 个落兜底**
#: —— 其中 `delegate_task`（子代理）、`execute_code`、`memory`、`session_search`、
#: `cronjob_manage`、`todo_list` 都是用户配置里**已启用**的 toolset ⇒ 卡片上一排 🔧 兜底图标。
#: 纪律（三条）：
#:   ① 只有 CLS 表**没命中**时才轮到它（不遮任何 CLS 别名）；
#:   ② token 只用 CLS 的 14 个字面量（emoji 表是唯一的额外信息），不引入编造的服务端图标名；
#:   ③ 每条都要写明理由 —— 这条表的**唯一目的**是用户观感，不是「多抄几家」。
ICON_ALIASES_LOCAL_EXTRA: List[Tuple[str, str]] = [
    ("terminal", "setting_outlined"),        # Hermes 的 shell 工具真名（tools/terminal_tool.py:1258）
    ("execute", "setting_outlined"),         # execute_code / code_execution（跑代码 = 跑命令）
    ("delegate", "robot_outlined"),           # delegate_task（子代理）
    ("skills", "app-default_outlined"),       # skills_list（注意 `skill` 别名只匹配 `skill_` 前缀）
    ("session", "search_outlined"),           # session_search（历史会话检索）
    ("todo", "list-check_outlined"),          # todo_list（待办清单）
    ("memory", "folder_outlined"),            # memory（长期记忆库 = 一个库）
    ("cronjob", "list-check_outlined"),       # cronjob_manage（计划任务）
    ("process", "setting_outlined"),          # process_manage（进程管理）
    ("image", "app-default_outlined"),        # image_generate / video_generate（生成类）
    ("video", "app-default_outlined"),
    ("speech", "language_outlined"),          # text_to_speech
    ("vision", "language_outlined"),          # vision_analyze / video_analyze / browser_vision
    ("computer", "browser-mac_outlined"),     # computer_use（看屏幕/操作界面）
    ("gui", "browser-mac_outlined"),          # gui_tour
    ("desktop", "browser-mac_outlined"),      # desktop_preview / desktop_project
    ("preview", "file-link-text_outlined"),   # *_preview（读某个东西给人看）
    ("web", "language_outlined"),             # web_extract（web_search/web_fetch 已被 CLS 命中）
    ("feishu", "chat_outlined"),              # feishu_doc_read / feishu_drive_*（飞书自身）
    ("drive", "folder_outlined"),
    ("ha", "app-default_outlined"),           # ha_call_service / ha_get_state（HomeAssistant）
    ("yb", "chat_outlined"),                  # yb_*（元宝侧工具）
    ("x_search", "search_outlined"),          # x_search（社交检索）
    ("patch", "edit_outlined"),               # patch（改文件）
    ("send", "chat_outlined"),                # send_message（发消息）
    ("react", "chat_outlined"),               # react_to_message（表情回应）
    ("text", "language_outlined"),            # text_to_speech（`speech` 匹配不到 `text_` 开头）
    ("setup", "setting_outlined"),            # setup_mcp
    ("show_tip", "chat_outlined"),            # show_tip（对用户说话）
    ("focus", "browser-mac_outlined"),        # focus_pane（界面操作）
    ("apply", "browser-mac_outlined"),        # apply_layout
    ("annotate", "browser-mac_outlined"),     # annotate_preview
    ("close", "setting_outlined"),            # close_preview / close_terminal（关掉一个东西）
]

#: 兼容旧调用点（探针/单测按 key 取 token）：由上面的有序表派生，fallback 另算
ICON_TOKENS: Dict[str, str] = {alias: token for alias, token in ICON_ALIASES}
ICON_TOKENS.update(dict(ICON_ALIASES_LOCAL_EXTRA))
ICON_TOKENS["fallback"] = ICON_FALLBACK


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
    #: 是否插入「正在准备上下文…」预加载提示（首字到达后由帧路径删元素 + 置 False）
    loading_hint: bool = False
    footer: str = ""
    footer_enabled: bool = True
    engine: str = "structured"
    engine_stamp: str = "structured"


#: 预加载提示元素 id（建卡时插入、首个正文 token 到达即删 —— aiduPOP 的 `_LOADING_ELEMENT_ID`）
LOADING_HINT_ID = "loading_hint"

#: spinner 资产 key —— aiduPOP / CLS / FC **三家硬编码的是同一个 key**（三家都没有自己上传的代码；
#: 证据：`tests/probe_loading.py` 的对照卡 + 真机目视结论）。
#: 用户口径是「**会动、无文字**」（2026-09-21 反馈 #4）：`custom_icon` 引用的动图资产在客户端
#: 会自己动，而 `standard_icon` 是静态字节图 —— 这是换掉 `time_outlined` 的全部理由。
SPINNER_IMG_KEY = "img_v3_02vb_496bec09-4b43-4773-ad6b-0cdd103cd2bg"

#: 允许被「上传一次并缓存」得到的 key 覆盖（adapter 启动时若拿到自有资产就注入）；
#: 空串 ⇒ 用三家共享 key。真机探针若判定共享 key **不动**，只需在这里换成上传得到的 key。
_SPINNER_KEY_OVERRIDE = ""


def set_spinner_img_key(img_key: Any) -> None:
    """注入自有 spinner 资产（上传成功后调用）；空值 ⇒ 回落共享 key。"""
    global _SPINNER_KEY_OVERRIDE
    _SPINNER_KEY_OVERRIDE = str(img_key or "").strip()


def spinner_img_key() -> str:
    """当前生效的 spinner 资产 key（自有优先，其次三家共享）。"""
    return _SPINNER_KEY_OVERRIDE or SPINNER_IMG_KEY


def loading_hint_element() -> Dict[str, Any]:
    """「正在加载」占位元素 —— **逐字段对齐 aiduPOP `_loading_element`**（V4.14b）。

    用户真机口径（2026-09-21 反馈 #4）：「正在加载上下文…」**不对**；aiduPOP 那个是
    「**会动、无文字**」的状态指示。于是本元素的形状与它逐字段相同：

    ``div`` + ``icon.custom_icon(img_key)`` + ``text.plain_text(" ")``（一个空格，**没有文案**）。

    key 为空（资产拿不到）时回落静态 `standard_icon`：宁可「不动」也不能把建卡写坏
    —— 无效 asset 在真机撞 300313（元素写失败），整条结构化装饰链会跟着掉。
    """
    key = spinner_img_key()
    icon: Dict[str, Any] = ({"tag": "custom_icon", "img_key": key, "size": "16px 16px"}
                            if key else
                            {"tag": "standard_icon", "token": "time_outlined",
                             "size": "16px 16px", "color": "grey"})
    return {
        "tag": "div",
        "element_id": LOADING_HINT_ID,
        "icon": icon,
        # ⚠️ 那个空格是**契约**（aiduPOP 同字段同值），不是随手写的占位：`div.text` 是
        # 文本节点属性（不是 collapsible_panel 的子元素），所以这里用 plain_text 合法。
        "text": {"tag": "plain_text", "content": " "},
    }


def _title_node(title: Any, **extra: Any) -> Dict[str, Any]:
    """标题 → `plain_text` 节点。`title` 可以是裸字符串，也可以是 i18n 节点（自带
    `content` + `i18n_content`，中英文客户端各显示各的）。V4.13：结构化标题过去是硬编码中文。"""
    node = ({"tag": "plain_text", **title} if isinstance(title, dict)
            else {"tag": "plain_text", "content": str(title)})
    node.update(extra)
    return node


#: `standard_icon` token → **内联 emoji**。为什么要有这张表（用户 2026-09-21 真机选版）：
#: 飞书把 `div.icon` 里的图标**顶部对齐**渲染，而我们的行文本长/字号不一，用户逐版比对后判定
#: 「**乙（emoji 内联进文本）图标与文字对得最齐**」（探针 `tests/probe_icons.py`，
#: `om_x100b6424d23e24a8c3368dfbdaad661`；同一次探针还证伪了「长文本换行导致偏上」的假说 ——
#: 用户明确回「丙 那行没有换行」）。所以工具行改为**内联 emoji**：token 表仍是 CLS 逐条对齐的
#: 唯一真相，emoji 只负责渲染。
ICON_EMOJI: Dict[str, str] = {
    "app-default_outlined": "🧩",
    "file-link-text_outlined": "📄",
    "edit_outlined": "✏️",
    "search_outlined": "🔍",
    "language_outlined": "🌐",
    "doc-search_outlined": "🔎",
    "folder_outlined": "📁",
    "setting_outlined": "🛠️",
    "browser-mac_outlined": "🖥️",
    "robot_outlined": "🤖",
    "list-check_outlined": "✅",
    "report_outlined": "📊",
    "chat_outlined": "💬",
    "setting-inter_outlined": "🔧",
}


def icon_emoji(token: str) -> str:
    """token → 内联 emoji（未知 token 用兜底 emoji，绝不返回空串 —— 空串会让行首多一个空格）。"""
    return ICON_EMOJI.get(str(token or ""), ICON_EMOJI[ICON_FALLBACK])


def _tool_title_div(step: ToolStepView) -> Dict[str, Any]:
    status_text, color = step.status_style
    duration = f" ({step.duration_ms} ms)" if step.duration_ms else ""
    # ⚠️ emoji **内联在文本里**，不用 `div.icon` —— 用户选版见 `ICON_EMOJI` 的说明。
    content = (f"{icon_emoji(step.icon_token)} **{step.title}**{duration} · "
               f"<font color='{color}'>{status_text}</font>")
    return {
        "tag": "div",
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
        # ⚠️ **不能用 `plain_text`**（真机 2026-09-21 14:44 实测）：`collapsible_panel` 的
        # `elements` 不收 `plain_text` 子元素 —— 服务端会回
        # `300313 failed to unmarshal … body->elements[0]` ⇒ **整帧装饰失败 + 收尾整卡失败**
        # ⇒ 核心回落纯文本，用户看到「卡片 + 灰色气泡」两张（用户反馈 #4 的根因）。
        # 而且这条只在工具步数 > `max_steps`（默认 20）时才出现 ⇒ 长回合必炸。
        # `markdown` 与 `div` 都是合法子元素，这里用 markdown（同 legacy 面板的写法）。
        elements.append({"tag": "markdown", "content": view.collapsed_hint,
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
    if view.loading_hint:
        elements.append(loading_hint_element())
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



