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
        """状态词 + 颜色。**必须与 `cards._TOOL_STATUS_STYLES` 同表**（V4.5，审计 B 中-5；
        `tests/test_units.py` 有逐键相等用例，改一边不改另一边必红）：
        结构化只认 4 个键时，`blocked`/`timeout` 会掉进 fallback 变成**灰色**、词也变
        （`Timeout` ≠ legacy 的 `Timed out`）—— 同一件事在两条车道上两种颜色是最难查的漂移。
        """
        return {
            # C1（2026-09-22 拍板）：运行中改 **blue** —— turquoise 在浅色主题下和绿色成功
            # 状态撞色（用户真机反馈「分不清在跑还是跑完了」）。默认①：**保留 `Running` 词**
            # （配蓝色 + 动图，色盲用户也能读）。
            "running": ("Running", "blue"),
            # 默认②（用户 2026-09-22）：**只有成功**换成绿色 `✓`；失败/超时/中止/跳过保留词
            # （词比符号能说清是哪一种；见 §0.1 逐字表）。
            "ok": ("✓", "green"),
            "success": ("✓", "green"),
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
    #: 工具行图标形态（配置 `tool_row_icon`）：`line`（默认，官方线性图标 + 文本前缀）/ `emoji`
    tool_icon_mode: str = "line"


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

#: 三家共享的 spinner 资产 key —— aiduPOP / CLS / FC 硬编码的都是它（都是「借」来的资产；
#: 证据：`tests/probe_loading.py` 的对照卡 + 真机目视结论）。
#: 用户口径是「**会动、无文字**」（2026-09-21 反馈 #4）：`custom_icon` 引用的动图资产在客户端
#: 会自己动，而 `standard_icon` 是静态字节图。⚠️ D1′（2026-09-22）之后它**不再是默认**：
#: 默认走 `SPINNER_TOOL_IMG_KEY`（⚠️ **P2 完成前它是本 key 的别名**，见下），这里只作**最后回落**保留（自研 key 拿不到时，
#: 会动的共享资产仍比「不动」好）。
SPINNER_IMG_KEY = "img_v3_02vb_496bec09-4b43-4773-ad6b-0cdd103cd2bg"

#: **自研** spinner 资产的 img_key（D1′：工具行「运行中」前缀图标 + 正文前加载指示
#: **共用同一张图**）。真源 `assets/spinner-tool.gif`（生成脚本 `tools/make_spinner_gif.py`，
#: 一次性上传脚本 `tools/upload_card_asset.py`，用**我们自己的 app** 上传）。
#: ⏳ P2 进行中：第一轮候选（条纹/弧线）被用户否掉（「外观不行，仿 aiduPOP 那个」），
#: 第二轮按 aiduPOP 官方截图里的**三个圆点**重画，挑图卡 `om_x100b6411a860d8b4dd88420cef6dc33`
#: 等用户回「C」或「D」后上传，把新 key 写在这一行 ——
#: 形状/字段/回落语义均已定稿，只等这一个字符串；**别在别处再写一份 key**。
SPINNER_TOOL_IMG_KEY = "img_v3_0215p_a0b0bd11-a182-433f-9647-8573d0dd7efg"

#: 允许被「上传一次并缓存」得到的 key 覆盖（adapter 启动时若拿到自有资产就注入）；
#: 空串 ⇒ 用下面两个常量里的第一个非空值。
_SPINNER_KEY_OVERRIDE = ""


def set_spinner_img_key(img_key: Any) -> None:
    """注入自有 spinner 资产（上传成功后调用）；空值 ⇒ 回落常量里的 key。"""
    global _SPINNER_KEY_OVERRIDE
    _SPINNER_KEY_OVERRIDE = str(img_key or "").strip()


def spinner_img_key() -> str:
    """当前生效的 spinner 资产 key —— 优先级：**运行时注入 > 自研 > 三家共享**。

    ⚠️ **P2 未完成期间** `SPINNER_TOOL_IMG_KEY` 只是 `SPINNER_IMG_KEY` 的别名（**过渡态**）：
    那段时间生效的仍然是**借来的共享 key**；用户选定并上传自研资产后才换成新 key。

    「拿不到资产」只有一种表达：三者全空 ⇒ 返回空串 ⇒ 调用方回落 `standard_icon`
    （宁可不动，也不能拿无效 asset 去撞 300313 —— 那会把整条结构化装饰链带走）。
    """
    return _SPINNER_KEY_OVERRIDE or SPINNER_TOOL_IMG_KEY or SPINNER_IMG_KEY


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
    # ⚠️ 兜底必须**惰性**取（`ICON_EMOJI.get(ICON_FALLBACK, "🔧")`）：写 `ICON_EMOJI[ICON_FALLBACK]`
    # 会被**急切求值** —— 变异 V4-34（把 ICON_FALLBACK 改成 `tool_02`）实测直接 KeyError，
    # 把卡片的 fail-open 链拖成 120s 超时（不明真假的「崩溃」）。
    return ICON_EMOJI.get(str(token or ""), ICON_EMOJI.get(ICON_FALLBACK, "🔧"))


#: 工具名 → 工具行 emoji 的**渲染层覆盖**（用户 2026-09-21 P3 复验口径，两条）：
#:   ① **区段符号不复用**：面板标题里的 🛠️（工具执行）/💭（思考）是**区段**符号。用户截图把
#:      标题的 🛠️ 与 `terminal` 行一起标红：「面板顶部标题的图标和下方使用命令行命令的图标
#:      是同一个，这个不合理」。14 个 CLS token 里 `setting_outlined` 同时被 exec / bash /
#:      command / run / terminal / execute / process / setup / close 共用 ⇒ 一律撞标题符号。
#:   ② **同 token 多工具按名字精化**：60+ 个真实工具名挤 14 个 token，按工具名给更贴切的 emoji
#:      （skill 仍是 🧩、read 仍是 📄、search 仍是 🔍，只精化那些「一格塞多个语义」的格子）。
#: 匹配规则与 token 解析**同源**（归一化后「精确 或 `alias_` 前缀」，不是子串）；**具体在前**。
#: ⚠️ token 表（`ICON_ALIASES` / `ICON_ALIASES_LOCAL_EXTRA`）仍是 CLS 对齐的唯一真相；
#: 本表**只影响渲染**，不参与 `check_cls_alignment` 的逐条比对（那条门禁只读 token 表）。
TOOL_EMOJI_BY_ALIAS: List[Tuple[str, str]] = [
    # —— 终端 / 执行类：🛠️ 留给面板标题，工具行用「机器 / 齿轮」语义
    ("terminal", "💻"),
    ("exec", "💻"),
    ("bash", "💻"),
    ("command", "💻"),
    ("run", "💻"),
    ("execute", "💻"),
    ("process", "⚙️"),
    ("setup", "🔌"),
    ("close", "✖️"),
    # —— 生成 / 媒体类（同 token 的 app-default 🧩 / language 🌐 太笼统）
    ("image", "🎨"),
    ("video", "🎬"),
    ("speech", "🗣️"),
    ("text", "🗣️"),
    ("vision", "👁️"),
    # —— 记忆 / 历史 / 计划
    ("memory", "🧠"),
    ("session", "🕘"),
    ("cronjob", "⏰"),
    # —— 消息 / 互动
    ("send", "✉️"),
    ("react", "👍"),
    ("show_tip", "💡"),
    ("clarify", "❓"),
    # —— 界面操作
    ("computer", "🖱️"),
]


def tool_emoji(name: str = "", token: str = "") -> str:
    """工具行首 emoji：先按**工具名**覆盖（:data:`TOOL_EMOJI_BY_ALIAS`），再退回 token 表。

    ``name`` 缺省时与 :func:`icon_emoji` 逐字一致（老调用点/只给 token 的场景不受影响）。
    """
    normalized = str(name or "").strip().lower().replace("-", "_")
    if normalized:
        for alias, emoji in TOOL_EMOJI_BY_ALIAS:
            if normalized == alias or normalized.startswith(alias + "_"):
                return emoji
    return icon_emoji(token)


#: 工具名 → **行首线性图标 token**（渲染层覆盖；用户 2026-09-22 口径：「更全面、更贴切」，且
#: 明确「统一灰色线性图标看起来更高级」）。全部是飞书官方图标库的 `_outlined` token，
#: **逐个对官方枚举页 `enumerations-for-icons` 查证过存在**（清单缓存见
#: `~/.larkdeck-scratch/feishu-icons-tokens.txt`，1154 个）；名字写错客户端就不渲染 ⇒ 不猜。
#: 匹配规则与 token 解析**同源**（归一化后「精确 或 `alias_` 前缀」，具体在前）。
#: ⚠️ `ICON_ALIASES`（28 条 CLS 逐条对齐）仍是**对齐判据的唯一真相**，`check_cls_alignment`
#: 只读那张表；本表只影响**渲染**（把 14 个 CLS token 细化到更贴切的官方 token）。
TOOL_ICON_BY_ALIAS: List[Tuple[str, str]] = [
    # —— 终端 / 执行 / 进程
    ("terminal", "command_outlined"),
    ("exec", "command_outlined"),
    ("bash", "command_outlined"),
    ("command", "command_outlined"),
    ("run", "command_outlined"),
    ("execute", "code_outlined"),
    ("process", "admin-setting_outlined"),
    ("setup", "appstore_outlined"),
    ("close", "close_outlined"),
    # —— 文件 / 编辑 / 检索
    ("patch", "edit_outlined"),
    ("grep", "doc-search_outlined"),
    ("glob", "folder_outlined"),
    ("read", "file-link-text_outlined"),
    ("open", "view_outlined"),
    ("preview", "view_outlined"),
    # —— 网络
    ("web_search", "search_outlined"),
    ("web_fetch", "language_outlined"),
    ("web", "language_outlined"),
    # —— 生成 / 媒体
    ("image", "image_outlined"),
    ("video", "video_outlined"),
    ("speech", "mic_outlined"),
    ("text", "mic_outlined"),
    ("vision", "big-eye_outlined"),
    # —— 知识 / 记忆 / 计划
    ("memory", "organization-book_outlined"),
    ("session", "history-search_outlined"),
    ("cronjob", "alarm-clock_outlined"),
    ("todo", "todo_outlined"),
    # —— 消息 / 互动
    ("send", "send_outlined"),
    ("react", "emoji_outlined"),
    ("show_tip", "info_outlined"),
    ("clarify", "chat_outlined"),
    ("feishu_doc", "doc_outlined"),
    ("feishu_drive", "folder_outlined"),
    ("drive", "folder_outlined"),
    ("ha", "home_outlined"),
    # —— 界面 / 电脑
    ("computer", "computer_outlined"),
    ("browser", "browser-mac_outlined"),
    ("playwright", "browser-mac_outlined"),
    ("navigate", "browser-mac_outlined"),
    ("gui", "browser-mac_outlined"),
    ("desktop", "browser-mac_outlined"),
    ("focus", "browser-mac_outlined"),
    ("apply", "browser-mac_outlined"),
    ("annotate", "browser-mac_outlined"),
    # —— 子代理 / 技能 / 报告
    ("skill", "app-default_outlined"),
    ("summarize", "report_outlined"),
    ("analyze", "report_outlined"),
    ("prepare", "report_outlined"),
]

#: 详情行 / 折叠提示 / 结果块 / 错误块的固定前缀图标（同为官方 `_outlined`，已查证）
ICON_DETAIL = "tool-indent_outlined"
ICON_HINT_MORE = "more_outlined"
ICON_RESULT = "codeblock_outlined"
ICON_ERROR = "warning_outlined"


def tool_icon_token(name: str = "", token: str = "") -> str:
    """工具行首图标 token：先按**工具名**精化（:data:`TOOL_ICON_BY_ALIAS`），再退回 CLS token。

    ``name`` 缺省时与直接使用 ``token`` 一致（老调用点不受影响）。
    """
    normalized = str(name or "").strip().lower().replace("-", "_")
    if normalized:
        for alias, tok in TOOL_ICON_BY_ALIAS:
            if normalized == alias or normalized.startswith(alias + "_"):
                return tok
    return str(token or ICON_FALLBACK)


def _icon_node(token: str) -> Dict[str, Any]:
    """标准线性图标节点（统一灰 —— 用户口径：颜色统一才显高级；状态色只留在文字里）。"""
    return {"tag": "standard_icon", "token": token, "color": ICON_COLOR}


def _grey(text: Any) -> str:
    """灰色正文。**富文本组件（`markdown`）没有 `text_color` 字段。**

    官方 2.0 富文本字段表只有 `tag / text_align / text_size / icon / href / content`（外加公共的
    `element_id`、`margin`）—— 写上 `text_color` 服务端回
    `200621 unknown property, path: …(tag: markdown)`，而且**是整张卡被拒**（不是忽略该字段）：
    长回合一旦走到这段代码就掉进纯文本回落，用户看到「卡片 + 灰色气泡」两张（= 用户反馈 #4 的
    同一种故障）。2026-09-22 真机探针实测踩中，别再写回去。

    2.0 里给正文上色**只能写进 `content`**：官方富文本「彩色文本样式」与 `lark_md` 语法表都是
    `<font color='grey'>…</font>`（`color` 取颜色枚举值）。`div.text` 的 `text_color` 也**只对
    `plain_text` 生效**，所以灰色正文统一走这里。
    """
    return f"<font color='grey'>{text}</font>"


def _tool_title_div(step: ToolStepView, icon_mode: str = "line") -> Dict[str, Any]:
    status_text, color = step.status_style
    # §0.1 逐字：**运行中那一行没有耗时段**（`**terminal** · <font color='blue'>Running</font>`）。
    # 生产里 running 步的 `duration_ms` 恒为 None（`panel.record_tool_started`），这里是渲染层
    # 契约 ⇒ 结构化地只对「已结束」拼时长：脏数据也拼不出偏离逐字表的一行。
    duration = ("" if str(step.status or "").strip().lower() == "running"
                else (f" ({step.duration_ms} ms)" if step.duration_ms else ""))
    content = f"**{step.title}**{duration} · <font color='{color}'>{status_text}</font>"
    if str(icon_mode or "line").strip().lower() == "emoji":
        # 旧路径（用户 2026-09-21 选的「乙 = emoji 内联」）：保留为可切换的降级选项 ——
        # 用户 2026-09-22 的真机口径是「统一灰色线性更高级」，所以默认走下面的前缀图标。
        return {
            "tag": "div",
            "text": {"tag": "lark_md",
                     "content": f"{tool_emoji(step.name, step.icon_token)} {content}",
                     "text_size": PANEL_TEXT_SIZE},
        }
    # **前缀图标**（用户 2026-09-22 真机三臂对照选版：`markdown` 的 `icon` 字段 ⇒ 0px 垂直偏差，
    # 而元素级 `div.icon` 实测图标高 3px、`column_set` 居中版横向空 179px）。官方 2.0 文档把
    # `markdown.icon` 叫「前缀图标」；图标随文本排版走，所以不存在「比文字靠上」的观感问题。
    #
    # D1′：**运行中**那一行的前缀图标换成动图（和正文前的加载指示同一张；资产由 P2 选定，
    # 未定期间 `spinner_img_key()` 仍返回借来的共享 key）。
    # `markdown.icon` 是**单图标槽**，只认 `standard_icon` / `custom_icon`；`custom_icon`
    # 里**不许带 `size`**（2.0 文档：该槽位无此字段 ⇒ 带了就是 `200621` 整卡被拒，
    # 见 `tests/check_cardview.py` 的宿主分档白名单）。空 key ⇒ 回落下面的静态线性图标。
    if str(step.status or "").strip().lower() == "running":
        key = spinner_img_key()
        if key:
            return {
                "tag": "markdown",
                "icon": {"tag": "custom_icon", "img_key": key},
                "content": content,
                "text_size": PANEL_TEXT_SIZE,
            }
    return {
        "tag": "markdown",
        "icon": _icon_node(tool_icon_token(step.name, step.icon_token)),
        "content": content,
        "text_size": PANEL_TEXT_SIZE,
    }


def _tool_detail_div(text: str, icon_mode: str = "line") -> Dict[str, Any]:
    if str(icon_mode or "line").strip().lower() == "emoji":
        return {
            "tag": "div",
            "margin": TOOL_DETAIL_INDENT,
            "text": {"tag": "plain_text", "content": f"↳ {text}",
                     "text_color": "grey", "text_size": "x-small"},
        }
    # 前缀图标版（2026-09-22「更全面」批次）：缩进交给 margin，箭头改成官方线性图标
    # `tool-indent_outlined`（已查证存在）—— 整卡图标语言统一，不再用文字箭头。
    # 灰色**必须写在 content 里**（`markdown` 不收 `text_color`，见 `_grey`）。
    return {
        "tag": "markdown",
        "margin": TOOL_DETAIL_INDENT,
        "icon": _icon_node(ICON_DETAIL),
        "content": _grey(text),
        "text_size": "x-small",
    }


def _tool_output_div(block: str, label: str, icon_mode: str = "line") -> Dict[str, Any]:
    node: Dict[str, Any] = {
        "tag": "div",
        "margin": TOOL_DETAIL_INDENT,
        # ⚠️ Error/Result 块**退回 `notation`**（2026-09-23 真机目视）：`div.text=lark_md`
        # 宿主对 `text_size` 的 `x-small` **服务端接受但客户端不生效**（截图对比两段同长
        # 代码块，字号/行高完全一致）⇒ 按 §6.10.10 回退规则只让 Error 块保持 `notation`。
        # 细节行的 markdown / plain_text 两宿主已验证确实更小，继续用 `x-small`。
        "text": {"tag": "lark_md", "content": f"**{label}**\n```\n{block}\n```",
                 "text_size": PANEL_TEXT_SIZE},
    }
    if str(icon_mode or "line").strip().lower() != "emoji":
        # 标题行加前缀图标：Error → 警告线性版；其它（Result 类）→ 代码块图标。都已查证存在。
        # ⚠️ 图标挂**组件级** `icon`（官方 2.0 普通文本组件字段：`icon` 是组件的「前缀图标」）。
        # 挂进 `text` 里服务端**不认**（`text` 只有 tag/element_id/content/text_size/text_color/
        # text_align/lines）⇒ 同样 200621 整卡被拒（2026-09-22 随 `text_color` 一起查出来的）。
        tok = ICON_ERROR if str(label).strip().lower().startswith("error") else ICON_RESULT
        node["icon"] = _icon_node(tok)
    return node


def tool_step_elements(step: ToolStepView, icon_mode: str = "line") -> List[Dict[str, Any]]:
    """一个工具步的 1–3 个元素（CLS builder.py:146-190）。"""
    elements: List[Dict[str, Any]] = [_tool_title_div(step, icon_mode)]
    if step.detail:
        elements.append(_tool_detail_div(step.detail, icon_mode))
    # ⚠️ V4.8（用户真机口径优先）：**成功步不再挂 Result 大代码块** —— 每步一个 fenced block
    # 又长又丑，且对标插件只在有错误/需要排查时才展开输出。失败步的 Error 块保留
    # （plan 里「Result/Error 块」那条按用户这份反馈收窄，共识文档里有记）。
    if step.error_block:
        elements.append(_tool_output_div(step.error_block, "Error", icon_mode))
    return elements


def reasoning_panel(round_view: ReasoningRoundView,
                    expanded: bool = False) -> Dict[str, Any]:
    """推理轮：嵌套 collapsible_panel（FC/CLS 终态形态）。

    ``expanded`` 由调用方按 A1 给：**当前还在生成的那一轮展开、已结束的轮折叠**
    （见 :func:`panel_elements`）。默认 ``False`` = 折叠，与旧行为逐字节相同。
    """
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
        "expanded": bool(expanded),
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
    icon_mode = str(getattr(view, "tool_icon_mode", "line") or "line").strip().lower()
    elements: List[Dict[str, Any]] = []
    if view.collapsed_hint:
        # ⚠️ **不能用 `plain_text`**（真机 2026-09-21 14:44 实测）：`collapsible_panel` 的
        # `elements` 不收 `plain_text` 子元素 —— 服务端会回
        # `300313 failed to unmarshal … body->elements[0]` ⇒ **整帧装饰失败 + 收尾整卡失败**
        # ⇒ 核心回落纯文本，用户看到「卡片 + 灰色气泡」两张（用户反馈 #4 的根因）。
        # 而且这条只在工具步数 > `max_steps`（默认 20）时才出现 ⇒ 长回合必炸。
        # `markdown` 与 `div` 都是合法子元素，这里用 markdown（同 legacy 面板的写法）。
        # 灰色走 `_grey()` 写进 content —— 2026-09-22 真机探针实测：`markdown` 加 `text_color`
        # 会 200621 **整卡被拒**（见 `_grey` 的说明）。
        node: Dict[str, Any] = {"tag": "markdown", "content": _grey(view.collapsed_hint),
                                "text_size": PANEL_TEXT_SIZE}
        if icon_mode != "emoji":
            # 「更全面」批次：折叠提示也带前缀图标（more = 省略号，已查证存在）
            node["icon"] = _icon_node(ICON_HINT_MORE)
        elements.append(node)
    for round_view in view.reasoning_rounds:
        # A1（2026-09-22 拍板）：**当前轮展开、已结束轮折叠**。`finalized` 由 `panel.snapshot()`
        # 透出（`_finalize_round_locked` 在给这一轮定稿耗时的那一刻置 True）。
        elements.append(reasoning_panel(round_view, expanded=not round_view.finalized))
    for step in view.tools:
        elements.extend(tool_step_elements(step, icon_mode))
    return elements


def panel_shell(view: PanelView) -> Dict[str, Any]:
    """外层面板的 collapsible_panel 结构。

    展开状态**只取 `view.expanded`**（终审 B 实测的配置缺陷：早先这里有个形参默认写死
    `False`，而两个调用方都不传它 ⇒ 用户把 `panel_expanded: true` 打开后结构化车道仍然是收起的）。
    终审 C 的收口复核又指出：那个形参从此**没有任何调用方**（显式传 `False` 被忽略也全绿）
    ⇒ 直接删掉，不留「看着能覆盖、其实没人用」的死 API。
    """
    _expanded = view.expanded
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
        "expanded": bool(_expanded),
        "vertical_spacing": PANEL_SPACING,
        "padding": PANEL_PADDING,
        "border": {"color": view.border, "corner_radius": PANEL_RADIUS},
        "elements": panel_elements(view),
    }


def panel_partial(view: PanelView) -> Dict[str, Any]:
    """partial_update_element 用的面板字段：**顶层不带 tag**（300312）。

    ⚠️ **固定不带 `expanded`**（A1 / 2026-09-22 拍板）—— 不是漏了，是纪律：

    ``partial_update_element`` 是**合并**语义，帧里带 `expanded` 就等于**每一帧重放一次**
    建卡时的展开态 ⇒ 用户手动收起面板后，下一个 token 立刻把它顶回展开（用户真机试过）。
    省略之后「展开/收起」只剩两个真值来源：① 建卡实体（seed）那一份 ② 收尾整卡 patch；
    中间帧只换内容，不碰结构。

    因此这里的键集**是契约**：`ck_panel_sig`（去重签名）与真正发出的 partial 同源 ⇒
    签名里也不含 `expanded` ⇒ 「换了展开态但内容没变」不会被误判成「有变化要重发」。
    想改这个元组请先改 `tests/test_units.py` 里的 `"expanded" not in` 用例。
    """
    shell = panel_shell(view)
    return {key: shell[key] for key in
            ("header", "vertical_spacing", "border", "elements")}


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



