"""飞书卡片 JSON 构造 —— 纯函数、无网络、无 Hermes 依赖，便于单测。

两种方言，按「要不要服务端回调」分开用
--------------------------------------
飞书卡片有两套互不兼容的写法，混用会被直接拒绝：

* **legacy 1.0**：顶层 ``elements`` + ``{"tag": "action", "actions": [...]}`` 按钮行。
  按钮带 ``value``，点击经 WebSocket 送到 ``p2.card.action.trigger``。
  **需要服务端处理点击的卡片必须用这套。**
* **schema 2.0**：``config`` / ``header`` / ``body.elements``。
  提供 ``streaming_mode``（打字机流式）和 ``collapsible_panel``（可折叠面板）。

两种方言各自的点击怎么接
------------------------
**要接服务端点击的组件，必须在它所属的方言里用对应的声明**（``AGENTS.md`` 不变量 5）：

* **1.0**：``{"tag": "action", "actions": [...]}`` 按钮行 + 按钮**顶层** ``value``；
* **2.0**：**组件级** ``behaviors: [{"type": "callback", "value": {...}}]`` —— ``value``
  会**原样**成为 ``event.action.value``；组件可以是 ``button`` / ``select_static``
  （回调带 ``action.option``）/ ``multi_select_static``（带 ``action.options``）/
  ``input``（带 ``action.input_value``）。

在 2.0 卡里放 1.0 的 ``action`` 行、或只有顶层 ``value`` 的按钮 → 飞书**拒收**
（IM API ``230099``）。混用是**静默失灵**（点了没反应），所以这条纪律要守。

> **这里曾经写过相反的结论**（「2.0 的 ``behaviors`` 到不了 ``p2.card.action.trigger``，
> 所以澄清卡只能是 1.0」）—— 那是**误诊**：当时那张「2.0 澄清卡」实际用的是没有
> ``behaviors``、只有顶层 ``value`` 的 1.0 按钮，属于「2.0 卡里放 1.0 组件」这个病；
> 论据还抄自第三方插件的**代码注释**。已在 2026-09-12 更正（见 ``AGENTS.md`` 不变量 5）。

所以本模块导出两组构造函数：
  * :func:`clarify_card` / :func:`clarify_resolved_card` —— 1.0（**当前默认**，真机跑通）
  * :func:`clarify_card_2` / :func:`clarify_resolved_card_2` —— 2.0（由配置
    ``clarify_dialect`` 选择；真机点击确证后再翻默认）
  * :func:`reply_card` —— 2.0（流式 + 折叠面板；**不接点击**）

元素级方言差异（实测，别再踩）
------------------------------
``note`` 元素在 **2.0 卡里已被飞书废弃**：实测 ``im/v1/messages`` 返回
``230099 / ErrCode 200861 · "cards of schema V2 no longer support this capability"``，
而同样的 ``note`` 放在 1.0 卡里正常通过。2.0 想要小字脚注只能用 :func:`footnote`。
**结构正确 ≠ 飞书接受** —— 卡片合法性只有真发一次才知道，见 ``tests/probe_render.py``。

界面文案走 :mod:`larkdeck.core.i18n`，靠飞书原生 ``i18n_content`` 做双语。
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Union

from . import i18n as _i18n

SCHEMA = "2.0"

#: 「其他（自己输入）」按钮的哨兵值；点它不提交答案，而是让网关等用户下一条文字。
OTHER_VALUE = "__larkdeck_other__"

#: **方言探针卡**专用键：值里带它的点击只会在日志里留一行载荷，不改任何状态。
#: 用途见 ``adapter._ld_log_probe_click`` 与 ``tests/probe_render.py`` 的探针卡。
PROBE_VALUE_KEY = "larkdeck_probe"

DEFAULT_TITLE = "Hermes"

#: 2.0 卡片在通知栏 / 会话列表里显示的一句话摘要上限，超长会被截断。
SUMMARY_MAX = 120

#: 推理文本 / 单条工具结果的字数上限 —— 超出的部分折叠成一行说明。
#: 默认值对齐 HFC 在真机上跑出来的经验值（推理 1200 / 工具结果 600），
#: 它那个量级是「能看清一步在干什么，又不至于把卡片撑成论文」。
MAX_REASONING_CHARS = 1200
MAX_TOOL_RESULT_CHARS = 600

#: 单轮推理至少渲染这么多字符才读得下去；同时它也是**渲染轮数的分母** ——
#: 面板最多渲染 ``max_reasoning_chars // _MIN_ROUND_CHARS`` 轮（见 :func:`unified_panel`）。
_MIN_ROUND_CHARS = 120

#: 统一面板最多保留多少条步骤；更早的收成一行计数。
MAX_PANEL_STEPS = 30

# --------------------------------------------------------------------------- #
# 状态色 —— **唯一载体是面板边框**
# --------------------------------------------------------------------------- #
#: 飞书卡片的颜色枚举（官方 resource/colors.md：14 个基础色名，无后缀 = -600）。
#: 这里只用其中四个，语义与 aiduPOP 对齐（它的源码是
#: ``"green" if not is_error and not is_aborted else ("red" if is_error else "yellow")``；
#: 注意它的 README 把红/黄写反了，以源码为准）。
BORDER_NEUTRAL = "grey"   # 进行中 / 还没有结论
BORDER_OK = "green"       # 完成
BORDER_ERROR = "red"      # 报错
BORDER_STOPPED = "yellow"  # 用户中止

#: 回合状态 → 边框色。键必须与 ``panel.STATUS_*`` 一致
#: （单测 ``test_status_colors_match_panel_constants`` 钉住这件事 —— 两边改名而另一边
#: 没改的后果是**边框永远灰色**，即状态色整条静默失效）。
STATUS_BORDERS: Dict[str, str] = {
    "ok": BORDER_OK,
    "error": BORDER_ERROR,
    "stopped": BORDER_STOPPED,
}


def border_for_status(status: Any) -> str:
    """状态 → 边框色；未知/未给一律中性灰（不猜）。"""
    return STATUS_BORDERS.get(str(status or ""), BORDER_NEUTRAL)


#: 状态 → 面板正文里的兜底文案键（面板没有别的内容时用）。
_STATUS_TEXT_KEYS: Dict[str, str] = {
    "ok": "panel.status_ok",
    "error": "panel.status_error",
    "stopped": "panel.status_stopped",
}

# --------------------------------------------------------------------------- #
# 客户端打字机（`streaming_config`）
# --------------------------------------------------------------------------- #
#: 流式卡片里的 ``streaming_config``：**客户端**逐字渲染的节奏。
#:
#: 关键事实（aiduPOP 与 hermes-fry-cards 两个独立项目取值完全一致，都是 15/1/fast）：
#: 流式模式下我们推的是**全文**，**平台自己算增量、逐字打出来** —— 所以这是纯客户端动画，
#: 与我们推帧的快慢无关。而 2026-09-13 真机实测：``message.patch`` 往返 ≈0.5s/帧
#: （`probe_render.py --rate-limit`，16 次连打零拒绝）⇒ **推帧侧最快也就 ~2 帧/秒**，
#: 想让观感顺滑，唯一有效的杠杆就是这个字段。
#:
#: 实测已确证「飞书接受它」：带它的卡在 create 与 patch 两条路径上都是 ``code=0``。
#:
#: ⚠️ **但「客户端到底打不打字」仍未确证，而且有反证倾向**（2026-09-13 追查）：
#:   * 官方文档只有一句「``streaming_mode`` 流式更新模式（配 ``streaming_config``）」，
#:     没有说清哪种写入 API 才触发动画；
#:   * **Hermes 上游自己有两个 PR 明确把「打字机」与 CardKit 流式接口绑在一起**
#:     （标题原文：*Card Kit streaming — typewriter-style output via Card Kit API*、
#:     *streaming cards for native typewriter effect using Feishu's CardKit streaming
#:     update API*）—— 这倾向于「``im.v1.message.patch`` 拿不到动画」；
#:   * 同类项目 lark-hls-v2 的注释也说**第一次推送必须用 ``card_element.content``**
#:     才有打字机，之后改 ``partial_update_element`` 是为了**避免**动画重放。
#: 反方向只有 HFC 归档代码的三处注释（patch + ``streaming_config``，作者相信它能打字，
#: 但那段代码已不在运行路径上）。
#:
#: 结论：这一条**只能靠肉眼定**，`tests/probe_render.py --typing` 就是为它准备的
#: （两张卡交替长大 12 秒，一眼看得出哪张在逐字）。带上它本身无害（飞书接受、
#: 收尾帧不带），所以默认开着；**确证无效就写 ``streaming_print_ms: 0`` 关掉，
#: 真要打字机则得换 CardKit 卡片实体传输**（那时才值得付那份复杂度）。
#:
#: ⚠️ **只在流式帧上带**（``streaming=True``）：收尾帧是 ``streaming_mode: false``，
#: 带上它可能让客户端把整段答案**再打一遍**，那是明确要避免的观感。
DEFAULT_PRINT_FREQUENCY_MS = 15


#: 逐字间隔的上限（毫秒）。超过就没有「打字机」可言了 —— 客户端永远追不上推送节奏，
#: 卡片看起来是「慢慢挤牙膏」（而且它是**客户端**动画，调大到几千毫秒不会省任何额度）。
PRINT_FREQUENCY_MAX_MS = 2000


def streaming_config(print_frequency_ms: Any = DEFAULT_PRINT_FREQUENCY_MS) -> Dict[str, Any]:
    """``streaming_config`` 节点：15ms/字、每步 1 字、``fast`` 策略（两个同类项目的取值）。

    间隔会夹到 ``[1, PRINT_FREQUENCY_MAX_MS]``：越界值一律退回默认，**不是**原样下发 ——
    配置写错不该让卡片变成挤牙膏（也不该让 ``int(inf)`` 抛出去把整张卡打回纯文本）。
    """
    try:
        value = int(print_frequency_ms)
    except (TypeError, ValueError, OverflowError):
        value = DEFAULT_PRINT_FREQUENCY_MS
    if not 1 <= value <= PRINT_FREQUENCY_MAX_MS:
        value = DEFAULT_PRINT_FREQUENCY_MS
    return {
        "print_frequency_ms": {"default": value},
        "print_step": {"default": 1},
        "print_strategy": "fast",
    }


#: 上下文进度条的格子数（8 格是 fry-cards 实测过的宽度，手机上不换行）。
CONTEXT_BAR_WIDTH = 8


# --------------------------------------------------------------------------- #
# 通用元素（两种方言都认）
# --------------------------------------------------------------------------- #
def md(content: str) -> Dict[str, Any]:
    """markdown 文本块。空内容给一个空格，避免卡片元素被判为空。"""
    return {"tag": "markdown", "content": content if content else " "}


def note(content: str) -> Dict[str, Any]:
    """**1.0 专属**小字脚注行 —— 放进 2.0 卡会被飞书拒（请用 :func:`footnote`）。"""
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": content}]}


def footnote(content: str) -> Dict[str, Any]:
    """**2.0 专属**小字脚注：``markdown`` + ``text_size: "notation"``。

    飞书已废掉 2.0 里的 ``note`` 元素，2.0 想要灰字小字只能走 markdown 的 text_size。
    """
    node: Dict[str, Any] = {"tag": "markdown", "content": content if content else " "}
    node["text_size"] = "notation"
    return node


def note_i18n(key: str, **fmt: Any) -> Dict[str, Any]:
    """双语脚注行。"""
    return {"tag": "note", "elements": [_i18n.i18n_text(key, **fmt)]}


def button(label: Union[str, Dict[str, Any]], value: Dict[str, Any],
           *, btype: str = "default") -> Dict[str, Any]:
    """按钮元素（1.0 方言里必须放进 :func:`action_row`）。

    ``label`` 可直接给 :func:`i18n.i18n_text` 的双语节点 —— 1.0 按钮的
    ``text`` 接受 ``i18n_content``，已由真机探针确证。
    """
    text = label if isinstance(label, dict) else {"tag": "plain_text", "content": label}
    return {
        "tag": "button",
        "type": btype,
        "text": text,
        "value": value,
    }


def action_row(buttons: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """1.0 的按钮行容器 —— 服务端回调的唯一可靠载体。"""
    return {"tag": "action", "actions": list(buttons)}


def collapsible(title_node: Dict[str, Any], elements: Sequence[Dict[str, Any]], *,
                expanded: bool = False, element_id: str = "auxiliary_timeline",
                border_color: str = BORDER_NEUTRAL,
                ) -> Dict[str, Any]:
    """可折叠面板（**2.0 专属**元素）。用于把推理过程 / 工具调用收进底部。

    ⚠️ ``header.title`` **必须**是 ``plain_text`` 节点。塞 ``lark_md`` / ``markdown``
    飞书不会报错，只会**悄悄把面板降级成普通行**——标题文字照常显示，但三角箭头
    没了、内容全部铺开，面板等于白做。这个坑静态检查抓不到，只有真机探针能抓。
    """
    panel: Dict[str, Any] = {
        "tag": "collapsible_panel",
        "expanded": bool(expanded),
        "header": {
            "title": title_node,
            "vertical_align": "center",
            # ⚠️ 箭头是**选配**的：不给 ``icon``，飞书面板就没有任何展开/收起控件 ——
            # 不报错、不提示，只是点不动，等于一个"永远打不开的抽屉"。
            # 官方示例 JSON 里是显式给 icon + icon_position + icon_expanded_angle 的。
            "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined",
                     "size": "16px 16px"},
            "icon_position": "right",
            "icon_expanded_angle": -180,
        },
        "border": {"color": str(border_color or BORDER_NEUTRAL),
                   "corner_radius": "8px"},
        "padding": "8px 8px 8px 8px",
        "elements": list(elements),
    }
    if element_id:
        panel["element_id"] = element_id
    return panel


# --------------------------------------------------------------------------- #
# 指标渲染：上下文用量 / 页脚（纯函数，可在单测里钉死每个边界）
# --------------------------------------------------------------------------- #
#: 进度条密度：空 → 半空 → 半实 → 实。用渐变而不是二值块，低占用时也能一眼看出趋势。
_BAR_DENSITY = ("░", "▒", "▓", "█")


def compact_tokens(value: Any) -> str:
    """``45200 -> "45.2k"``，``200000 -> "200k"``，``1000000 -> "1.0m"``。

    整数化后再压缩，避免 ``200.0k`` 这种别扭写法。非数字返回空串。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    n = int(value)
    if n < 0:
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}m".replace(".0m", "m")
    if n >= 1_000:
        return f"{n / 1_000:.1f}k".replace(".0k", "k")
    return str(n)


def progress_cells(pct: float, width: int = CONTEXT_BAR_WIDTH) -> str:
    """把百分比画成 ``width`` 格渐变条，如 ``██▓▒░░░░``。

    每格取它自身的填充比例（0..1）映射到四档密度，所以边界格是半实心而不是
    突然从满格跳到空格 —— 8 格也能看出 12% 和 25% 的差别。
    """
    width = max(1, int(width))
    filled = max(0.0, min(100.0, float(pct))) / 100.0 * width
    cells = []
    for index in range(width):
        frac = min(max(filled - index, 0.0), 1.0)
        cells.append(_BAR_DENSITY[min(3, int(frac * 4 + 1e-9))])
    return "".join(cells)


def context_indicator(used: Any, maximum: Any, *, style: str = "text") -> str:
    """页脚里的上下文用量片段；数据不全返回空串（调用方据此不渲染）。

    * ``text``（默认）—— ``ctx 45.2k/200k · 23%``
    * ``bar``         —— ``ctx [███▓▒░░░] 23%``
    * ``both``        —— ``ctx 45.2k/200k [███▓▒░░░] 23%``
    """
    if isinstance(used, bool) or not isinstance(used, int) or used <= 0:
        return ""
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
        return ""
    pct = min(100.0, used / maximum * 100.0)
    amount = f"{compact_tokens(used)}/{compact_tokens(maximum)}"
    if style == "bar":
        return f"ctx [{progress_cells(pct)}] {pct:.0f}%"
    if style == "both":
        return f"ctx {amount} [{progress_cells(pct)}] {pct:.0f}%"
    return f"ctx {amount} · {pct:.0f}%"


def format_elapsed(seconds: float) -> str:
    """``12.3s``；超过一分钟换成 ``2m05s``，避免出现 ``125.7s`` 这种要心算的读数。"""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


def footer_line(*, duration: Optional[float] = None, model: str = "",
                tools: Optional[int] = None, rounds: Optional[int] = None,
                context: str = "") -> Optional[str]:
    """「符号 + 数字 + 英文缩写」拼成的信息行 —— 天然无需翻译（不依赖 i18n）。

    两个调用点：**面板标题行**（``model + rounds + tools + duration``，决策 D2 把这些
    从卡片级 header 搬进面板头）与**页脚**（只放 ``context``）。

    各段之间用 ``·`` 分隔；一段都没有时返回 ``None``，调用方就不渲染。
    """
    parts: List[str] = []
    if model:
        parts.append(f"🤖 {model}")
    # 注意排除 bool：Python 里 isinstance(True, int) 为真，不排会拼出「🧠 True」。
    if isinstance(rounds, int) and not isinstance(rounds, bool) and rounds > 0:
        parts.append(f"🧠 {rounds}")
    if isinstance(tools, int) and not isinstance(tools, bool) and tools > 0:
        parts.append(f"🔧 {tools}")
    if context:
        parts.append(context)
    if isinstance(duration, (int, float)) and duration >= 0.1:
        parts.append(f"⏱ {format_elapsed(float(duration))}")
    return " · ".join(parts) or None


#: 工具步骤状态符号（符号语言无关，无需 i18n；未知状态用「•」兜底）。
_TOOL_STATUS_MARKS = {"running": "⏳", "ok": "✅", "error": "❌", "blocked": "⛔"}


def tool_step(name: str, *, status: str = "ok", duration_ms: Any = None,
              preview: str = "") -> str:
    """一步工具调用的单行摘要，供 :func:`unified_panel` 的 ``tools`` 参数使用。

    形如 ``✅ read_file · 2.3s · `` ``{"path": "…"}``。耗时毫秒转秒复用
    :func:`format_elapsed`（不足 0.1s 显示 ``0.1s``，避免难看的 ``0.0s``）；
    参数预览包成行内代码 —— 预览是 JSON，可能有 markdown 特殊字符，
    内部的反引号会被换成单引号，避免破坏行内代码的边界。
    """
    mark = _TOOL_STATUS_MARKS.get(str(status or ""), "•")
    line = f"{mark} {name or 'tool'}"
    if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool):
        ms = max(0.0, float(duration_ms))
        if ms > 0:
            line += f" · {format_elapsed(max(0.1, ms / 1000.0))}"
    if preview:
        safe_preview = str(preview).replace("`", "'")
        line += f" · `{safe_preview}`"
    return line


# --------------------------------------------------------------------------- #
# 溢出保护：任何一段用户不可控的长文本，进卡片前都必须过这一关
# --------------------------------------------------------------------------- #
def _positive(value: Any) -> bool:
    """配置值是否为正数（用于「0 = 关掉这个特性」的开关型配置，**不是**上限）。"""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return number == number and number not in (float("inf"), float("-inf")) and number > 0


def _cap(value: Any, default: int) -> int:
    """把配置来的上限归一到**正数**。

    ⚠️ ``<= 0`` **不能理解成「不设限」** —— 面板是收进卡片里的：不截断就会把整段推理
    （panel 的缓冲上限 262144 字符）塞进卡片，卡片 JSON 膨胀到几百 KB，飞书直接拒收，
    那个回合的卡片功能整块丢掉（按不变量会回落纯文本，消息不丢，但卡片没了）。
    `panel.py` 里最多还缓存 200 步工具。所以 0 / 负数 / 转不动一律退回默认值 ——
    这与 README 把这三个键描述成「上限」的自然预期一致。
    """
    try:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):  # NaN / ±Inf
            return default
        n = int(number)
    except (TypeError, ValueError, OverflowError):
        # OverflowError：int(float("inf")) 抛的是它，不是 ValueError。
        # 漏了会让 _ld_panel 静默整块丢面板（那里 catch 住后返回 None）。
        return default
    return n if n > 0 else default


def truncate(text: str, limit: int, *, key: str = "panel.overflow") -> str:
    """超长文本截断并补一行「已省略 N 字符」，而不是静默切掉。

    静默截断会让用户以为模型就说了这么多；补一行说明才知道下面还有内容。

    截断点按**优先级阶梯**选，目的是不切断 markdown 的块结构：

    1. 空行（``\\n\\n``，段落边界）—— 最优；
    2. 行首块级标记（``#`` / ``-`` / ``*`` / ``+`` / ``>`` / ``|`` / ```` ``` ````）；
    3. 任意换行；
    4. 空白（用 ``isspace()`` 而不是 ``" "`` —— CJK 文本里几乎不出现半角空格，
       原来的 ``" " in head[-80:]`` 对中文基本不生效，这也是更容易切进 emoji 中间的原因）；
    5. 实在没有就往回退到 ``limit``。

    每级都要求落在 ``limit`` 的后半段（``>= limit // 2``），否则宁可用上一级 ——
    避免为了对齐块边界而丢掉大半内容。

    截断点最后还要过一次字形回退（ZWJ 组合、变体选择符、组合记号、国旗对），
    免得把 👨‍👩‍👧 这类多码点字形切成两半。

    **只修围栏与行内反引号，绝不补 ``**``**：``**`` 在 markdown 里语义歧义（乘法、
    行内代码里的 ``**``、``*`` 与 ``**`` 混用都会让计数失真），补错的闭合符比不补更糟；
    核心自己给正文做同类处理时也只碰围栏与反引号。

    调用方必须先经 :func:`_cap` 归一 ``limit`` —— 这里 ``limit <= 0`` 保持「原样返回」
    的 fail-open 行为，只作为最后一道兜底，生产路径不会走到（:func:`unified_panel` 已归一）。
    """
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    # 切点可能被块级/字形回退拉到 limit 之前，所以丢弃量必须按**实际切点**算 ——
    # 用 len(text) - limit 会在回退时少报（实测实际丢 108、文案声称 68）。
    cut = _glyph_safe(text, _block_boundary(text, limit))
    head = _close_code_spans(text[:cut])
    return f"{head.rstrip()}\n> {_i18n.t(key, n=len(text) - cut)}"


#: 行首块级标记：截断点落在这类行之前，不会把块切成两半。
_BLOCK_START_MARKS = ("#", "-", "*", "+", ">", "|")

#: 字形回退最多回溯多少个码点。
_GLYPH_BACK_LIMIT = 8


def _block_boundary(text: str, limit: int) -> int:
    """在 ``limit`` 附近找一个**块级安全**的截断位置（返回下标）。"""
    window = text[:limit]
    floor = limit // 2
    pos = window.rfind("\n\n")
    if pos >= floor:
        return pos
    for index in range(len(window) - 1, floor - 1, -1):
        if window[index] != "\n":
            continue
        tail = window[index + 1:]
        if tail.startswith("```") or tail[:1] in _BLOCK_START_MARKS:
            return index
    pos = window.rfind("\n")
    if pos >= floor:
        return pos
    for index in range(len(window) - 1, floor - 1, -1):
        if window[index].isspace():
            return index
    return limit


def _glyph_safe(text: str, pos: int) -> int:
    """把截断点从**字形中间**挪出来，最多回溯 ``_GLYPH_BACK_LIMIT`` 个码点。

    ZWJ 序列（👨‍👩‍👧）、变体选择符（U+FE0E/FE0F）、组合记号都是「多个码点组成一个
    可见字形」，按码点硬切会渲染出两个 emoji 或豆腐块。国旗是两个 regional indicator
    组成一对，也不能切散。
    """
    backed = 0
    while pos > 0 and backed < _GLYPH_BACK_LIMIT:
        ch = text[pos - 1]
        if ch == "\u200d" or ch in ("\ufe0e", "\ufe0f") or unicodedata.combining(ch):
            pos -= 1
            backed += 1
            continue
        if ("\U0001F1E6" <= ch <= "\U0001F1FF" and pos >= 2
                and "\U0001F1E6" <= text[pos - 2] <= "\U0001F1FF"):
            pos -= 2
            backed += 2
            continue
        break
    return pos


def _close_code_spans(text: str) -> str:
    """闭合被截断打断的代码围栏与行内反引号；**不碰 ``**``**（语义歧义，见 docstring）。"""
    if not text:
        return text
    # 先剥掉完整的 ```…``` 区段，再数尾部未闭合的那个
    stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    if stripped.count("```") % 2:
        text += "\n```"
        stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    if stripped.count("`") % 2:
        text += "`"
    return text


# --------------------------------------------------------------------------- #
# 2.0：回复卡（流式 + 统一面板）
# --------------------------------------------------------------------------- #
def _summary_of(text: str, *, fallback: str = "") -> Dict[str, Any]:
    """2.0 卡片的 ``config.summary`` —— 官方 SDK 与 HFC 都强制带，漏了会出问题。

    ⚠️ 归一化后为空时**必须给兜底文案**：``{"content": ""}`` 等于通知栏空白，
    正是这个字段要防的那件事。而空文本在真实路径上会出现两次 —— native 流式的
    seed 帧（用空文本建卡），以及归档点落在末尾时的 finalize 帧
    （``_ld_stream_frame`` 里写的 ``display or " "``）。
    """
    flat = " ".join(str(text or "").split())
    return {"content": flat[:SUMMARY_MAX] or fallback}


def card(*, elements: Sequence[Dict[str, Any]], template: str = "blue",
         title: Optional[str] = DEFAULT_TITLE, streaming: Optional[bool] = None,
         update_multi: bool = True, summary: str = "",
         print_frequency_ms: Any = DEFAULT_PRINT_FREQUENCY_MS) -> Dict[str, Any]:
    """2.0 卡片：``config`` / （可选）``header`` / ``body.elements``。

    ``title=None`` 表示**不要卡片级 header** —— 决策 D2 走这条路：模型名与统计搬进
    ``collapsible_panel`` 自己的 header，卡片只剩正文 + 面板（最接近 aiduPOP 的观感，
    也是「状态色放边框」这个选择的配套）。``header`` 是 2.0 的可选字段，不给完全合法。

    **不带按钮行** —— 需要接点击的卡片请用 :func:`legacy_card`。
    """
    config: Dict[str, Any] = {"wide_screen_mode": True}
    if update_multi:
        # 多端同步更新：所有客户端都看到同一份最新卡片。
        config["update_multi"] = True
    if streaming is not None:
        config["streaming_mode"] = bool(streaming)
    # 打字机只挂在**流式帧**上，理由见 DEFAULT_PRINT_FREQUENCY_MS 的说明
    if streaming and _positive(print_frequency_ms):
        config["streaming_config"] = streaming_config(print_frequency_ms)
    # 流式卡必须带 summary，否则通知栏空白、且部分场景会被拒。
    # 内容为空时（seed 帧 / 空 finalize 帧）退回卡片标题，不能留空串。
    if streaming or summary:
        config["summary"] = _summary_of(summary, fallback=str(title or DEFAULT_TITLE))
    node: Dict[str, Any] = {
        "schema": SCHEMA,
        "config": config,
        "body": {"elements": list(elements)},
    }
    if title:
        # ``title`` 可以是**字符串**，也可以是 :func:`i18n.i18n_text` 的双语节点。
        # ⚠️ 后者曾经被直接塞进 ``content`` 里，产出 ``{"content": {"tag": ...}}`` 这种
        # 嵌套结构 —— 飞书**拒收**（``230099 / ErrCode 200621 parse card json err``），
        # 而本地单测只核「有没有 header」看不出来。2026-09-13 由 `probe_render.py` 的
        # 真机探针抓到（⑬⑭ 两张真 2.0 澄清卡全被拒）。``legacy_card`` 一直是对的，
        # 这里补齐同样的处理。
        title_node = (title if isinstance(title, dict)
                      else {"tag": "plain_text", "content": str(title)})
        node["header"] = {"template": template, "title": title_node}
    return node


#: 「答案还没开始」时正文区的占位文案键（i18n）。理由见 :func:`answer_or_pending`。
_PENDING_TEXT_KEY = "stream.pending"


def answer_or_pending(answer: str, streaming: bool) -> str:
    """正文为空且**还在流式**时，给一个占位文案（空白卡看起来像卡住了）。

    aiduPOP 的「即时响应」（它的效果图 1）不只是「卡出现得早」，还包括**一眼看出它在干活**：
    它的卡片在等待期显示占位文案，正文一到就被替换。我们此前建卡后正文只有一个空格
    （:func:`md` 为了避免元素为空而补的），用户看到的是一张**近乎空白**的卡 ——
    效果 1a 的「即时」在观感上打了折扣。

    ⚠️ **收尾帧（``streaming=False``）绝不带占位**：否则一个真的没有正文的回合
    （被中止、或模型只输出了思考）会永远停在「正在生成…」，那是明确的错误信息。
    这也是为什么判据挂在 ``streaming`` 上，而不是「正文为空」上。
    """
    if streaming and not str(answer or "").strip():
        return _i18n.t(_PENDING_TEXT_KEY)
    return answer


def reply_card(answer: str, *, streaming: bool = False, panel: Optional[Dict[str, Any]] = None,
               footer: Optional[str] = None,
               print_frequency_ms: Any = DEFAULT_PRINT_FREQUENCY_MS) -> Dict[str, Any]:
    """正文回复卡：正文区 + 可选统一面板 + 可选脚注。

    **没有卡片级 header**（决策 D2）。原来标题固定是 "Hermes"，既没信息量又占一行，
    现在模型名/轮数/工具数/耗时全在面板头里。
    """
    elements: List[Dict[str, Any]] = [md(answer_or_pending(answer, streaming))]
    if panel:
        elements.append(panel)
    if footer:
        elements.append(footnote(footer))
    return card(elements=elements, title=None, streaming=streaming, summary=answer,
                print_frequency_ms=print_frequency_ms)


#: 飞书 Card 2.0 的**硬上限**：整张卡里（含嵌套）带 ``tag`` 键的对象总数 ≤ 200。
#: 超了不是「截断」，而是**整张卡完全不渲染**。
#:
#: **2026-09-13 真机实测（tests/probe_render.py --elements）**，不是抄来的。
#: 探针按 (196, 200, 201, 204) 四档递进、递归计数后实际发出的是同样这四个数，
#: 输出为：**196 ✅ · 200 ✅ · 201 ❌ · 204 ❌**（拒收时
#: ``code=230099`` · ``ext=ErrCode: 11310; ErrMsg: element exceeds the limit``）。
#: ⇒ 墙在 200/201 之间，官方口径的 200 成立。
#: （早先这里写过「198 收下 / 202 拒收」—— 那是另一次阶梯的输出，与当前探针口径对不上，
#: 已按可复现的数字改写。下一次飞书收紧上限时，用同一个阶梯就能复现新墙的位置。）
#: ``230099`` 是**不可重试**的确定性错误（同样内容重试必然同样失败），别塞进退避集合。
#:
#: ⚠️ 计数必须是**递归数所有含 tag 的对象**，不是数 ``body.elements`` 的长度：
#: 折叠面板里的每个 markdown 都算一个，只数顶层会低估几倍 —— 上面那两次实测用的
#: 就是「一个折叠面板塞 N 个子元素」的真实形状。
FEISHU_ELEMENT_LIMIT = 200

#: 给正文 / 脚注 / 收尾留的余量（收尾帧会补脚注，不能刚好卡在 200）。
_ELEMENT_LIMIT_RESERVE = 6

#: 面板**之外**那张卡的固定元素数（递归口径）：正文 markdown 1 + 面板本体 1 +
#: 面板标题 plain_text 1 + 面板标题的 icon 1 + 脚注 1 = 5。
#: 面板能放多少个子元素必须用它来算，**不能**用「上限减个大概」——
#: `panel_room` 与 `fit_reply_card` 的 `element_limit` 口径不一致时，会出现
#: 「面板自己以为放得下、降载阶梯却把整块面板摘掉」的 3~4 个元素的缝
#: （2026-09-13 审计实测：steps=190 → tier=no-panel、190 个元素的推理面板全没了）。
_CARD_FIXED_ELEMENTS = 5

#: 面板里可能出现的「…更早的 N 步已折叠」提示行，也要算进子元素预算。
_PANEL_HINT_ELEMENTS = 1

#: 面板**子元素**的可用额度：与 :func:`fit_reply_card` 的判据同源，保证「面板自认为
#: 放得下」⇒「降载阶梯也认」。留 1 个给折叠提示，最终整卡正好落在 element_limit 上。
_PANEL_CHILDREN_ROOM = (FEISHU_ELEMENT_LIMIT - _ELEMENT_LIMIT_RESERVE
                        - _CARD_FIXED_ELEMENTS - _PANEL_HINT_ELEMENTS)

#: 卡片 JSON 的 UTF-8 字节预算。飞书对 interactive 卡有大小上限，超了会被拒收。
#: **量纲必须是字节**：``ensure_ascii=False`` 下汉字占 3 字节，按字符估会低估 3 倍。
#:
#: ⚠️ 这个数字**仍是保守猜测，尚无权威依据** —— 审计核实过：Hermes 源码里没有任何
#: 卡片大小常量（唯一相关错误码 `230099` 的官方注释是「failed to create card content」，
#: 是**内容创建失败**的通用码，不是大小上限），官方飞书适配器也没有 native streaming
#: 可供参照（它只有纯文本的 `MAX_MESSAGE_LENGTH = 8000`）。
#: 取值权衡：**过小会在正常长度的回答上静默摘掉整个面板**（早先取 20000 时，
#: ~4600 汉字的正文就会把「推理+工具」面板整块摘掉，而此前那种卡片是能正常发出的），
#: 过大则会在飞书拒收时让每一帧都失败，而**一帧失败会永久关掉本回合的 native 流式**
#: （核心的行为，见 stream_consumer_transport）—— 那比丢面板更糟。
#: 所以先取一个「正常回答不动、超大才降载」的宽松值；`tests/probe_render.py`
#: 现在会实测并打印每张探针卡的字节数，用真机数据把它钉死。
CARD_BYTE_BUDGET = 40000

#: 飞书**实测**的卡片 JSON 硬上限（utf-8 字节）。2026-09-13 真机阶梯（`--bytes`）：
#: **128000 仍 `code=0`**（create 与 patch 都是），**160000 被拒**：
#: ``230025 The length of the message content reaches its limit``。
#: 所以真实上限落在 (128000, 160000] 之间，取 128000 是**保守**的那一侧。
#:
#: 它只在一处用得上：正文已经超过 :data:`CARD_BYTE_BUDGET` 时，我们**仍然要给状态色
#: 留一个落脚点**（见 :func:`status_shell`）—— 那一档不受软预算约束，只受这道硬墙约束。
#: `core/adapter.py` 的 `_MAX_TRACKED_TEXT` 也以它为准（「发得出去的卡就存得下正文」）。
FEISHU_CARD_BYTE_LIMIT = 128000


def count_elements(node: Any) -> int:
    """递归数出卡片里**所有**带 ``tag`` 键的对象（嵌套面板的子元素全算）。

    见 :data:`FEISHU_ELEMENT_LIMIT`：这是飞书的硬墙，撞上不是截断而是整卡不渲染。
    """
    if isinstance(node, dict):
        total = 1 if "tag" in node else 0
        for value in node.values():
            total += count_elements(value)
        return total
    if isinstance(node, (list, tuple)):
        return sum(count_elements(item) for item in node)
    return 0


def card_bytes(node: Dict[str, Any]) -> int:
    """卡片 JSON 的 utf-8 字节数（发送时用的就是这份序列化）。"""
    try:
        return len(json.dumps(node, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0


def status_shell(panel: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把完整面板缩成**只保留状态色**的最小面板（标题 + 一行状态文字，约 300 字节）。

    为什么必须存在这个东西（2026-09-13 真机实测，新加的 ``--stop-redraw`` 探针抓到的）：
    状态色（绿/红/黄）的载体**只有** ``collapsible_panel.border.color``，而正文一旦超过
    :data:`CARD_BYTE_BUDGET`，:func:`fit_reply_card` 的降载阶梯会把面板**整块**摘掉
    ⇒ 那张卡**没有任何颜色**。于是出现一个很荒谬的组合：``/stop`` 时我们确实发了
    ``message.patch``（code=0，第七路审计的阻断项修好了），可**载荷里根本没有颜色**
    —— 用户看到的还是「没变色」。换句话说：光把正文留住只解决了一半，
    另一半是**别把颜色的载体丢在降载里**。

    这一档的取舍：面板的数据（推理/工具/页脚）照旧不要，只留下「一个带颜色的框 +
    一行状态文字」。成本约 300 字节，而这时卡片通常已经 40KB 以上（软预算的 15% 都不到），
    相对飞书 128000 的硬上限可以忽略。
    """
    if not isinstance(panel, dict):
        return None
    border = panel.get("border")
    color = (border or {}).get("color") if isinstance(border, dict) else None
    if not color or color == BORDER_NEUTRAL:
        return None                      # 没有状态色就没有要保住的东西
    status = next((key for key, value in STATUS_BORDERS.items() if value == color), None)
    if status is None:
        return None
    header = panel.get("header") if isinstance(panel.get("header"), dict) else {}
    title = header.get("title")
    if not isinstance(title, dict):
        title = _i18n.i18n_text("panel.title")
    return collapsible(title, [md(_i18n.t(_STATUS_TEXT_KEYS.get(status, "panel.title")))],
                       expanded=False, border_color=color)


def fit_reply_card(answer: str, *, streaming: bool = False,
                   panel: Optional[Dict[str, Any]] = None, footer: Optional[str] = None,
                   budget: int = CARD_BYTE_BUDGET,
                   element_limit: int = FEISHU_ELEMENT_LIMIT - _ELEMENT_LIMIT_RESERVE,
                   print_frequency_ms: Any = DEFAULT_PRINT_FREQUENCY_MS,
                   ) -> "tuple[Dict[str, Any], str]":
    """构造回复卡，超预算时**分级丢装饰**。返回 ``(card, 降级档位)``。

    档位：``ok`` → ``no-panel`` → ``bare`` → ``over-budget``。
    ⚠️ ``over-budget`` 档**不是**裸卡：它会带一个只保状态色的小面板（:func:`status_shell`，
    除非加上它就越过飞书硬上限）。别在这里按「裸卡」假设写调用方逻辑。
    降载由**两道独立的墙**触发：字节预算（我们自己的保守值）与元素数硬上限（飞书 200）。
    两者任一超了就往下丢装饰 —— 元素那一侧撞上不是「卡片变小」而是**整张卡不渲染**。

    **刻意不截断正文。** 按审计核实：Hermes 官方明确把 native 流式下的长度责任推给适配器
    （``gateway/stream_consumer.py`` 的注释写着 "Native streaming bypasses this: the adapter
    truncates against the stream protocol's own limit"），而官方 ``send()`` 本身是**分块**的
    （``splits_long_messages``）。所以正文过长时正确的做法是**让它发失败**，由核心的
    fail-open 链回落到官方 ``send()`` → 分块发送（退化成多条纯文本，但**内容完整**）。
    静默截断答案会让用户以为模型就说了这么多 —— 这正是 ``docs/lessons.md`` 里最怕的失败模式。
    """
    attempts = ((panel, footer, "ok"),
                (None, footer, "no-panel"),
                (None, None, "bare"))
    for panel_try, footer_try, tier in attempts:
        node = reply_card(answer, streaming=streaming, panel=panel_try, footer=footer_try,
                          print_frequency_ms=print_frequency_ms)
        # 两道**独立**的墙：字节数（我们自己的保守预算）与元素数（飞书硬上限 200）。
        # 后者是 2026-09-13 真机量出来的（202 个元素 → 230099 / ErrCode 11310），
        # 撞上它不是「卡片变小」而是**整张卡不渲染**，所以必须一起判。
        if card_bytes(node) <= budget and count_elements(node) <= element_limit:
            return node, tier
    # 连装饰全摘都超预算：正文本身太大。**照常返回**，让发送失败去走官方回落
    # （官方会分块），而不是在这里把答案切掉。
    bare = reply_card(answer, streaming=streaming, print_frequency_ms=print_frequency_ms)
    # ……但**状态色必须留住**（这是 `status_shell` 的用途，理由见它的 docstring）。
    # 这一档故意**绕开软预算**（正文已经超了，再省那 300 字节没有意义），
    # 只守飞书的**实测硬上限**：两张卡都贴边时，多这 300 字节可能把一张能发的卡顶成
    # 「拒收 → 回落纯文本」，那就不划算。所以判据是「加完之后还在硬墙之内」。
    shell = status_shell(panel)
    if shell is not None:
        with_shell = reply_card(answer, streaming=streaming, panel=shell,
                                print_frequency_ms=print_frequency_ms)
        if card_bytes(with_shell) <= FEISHU_CARD_BYTE_LIMIT:
            return with_shell, "over-budget"
    return bare, "over-budget"


def _round_title(index: int, elapsed_ms: Any) -> str:
    """推理轮的标题行：``第 N 轮 · 6.2s``（耗时是**相对时长**，不是时刻）。"""
    base = _i18n.t("panel.round_n", n=index)
    if isinstance(elapsed_ms, int) and not isinstance(elapsed_ms, bool) and elapsed_ms > 0:
        return f"{base} · {format_elapsed(max(0.1, elapsed_ms / 1000.0))}"
    return base


#: CardKit 实体卡的**固定元素 id**（结构在建实体时定死，之后只按 id 写内容）。
#: 见 docs/plan-6-effects.md「阶段 9」：**任何结构性写入都会关闭流式会话**
#: （真机实测 `300309`），所以结构不能边流边改。
CARDKIT_ANSWER_ID = "answer"
CARDKIT_PANEL_ID = "panel"
CARDKIT_PANEL_BODY_ID = "panel_body"


def panel_markdown(*, reasoning: str = "", rounds: Sequence[Dict[str, Any]] = (),
                   tools: Sequence[str] = (),
                   max_reasoning_chars: int = MAX_REASONING_CHARS,
                   max_tool_chars: int = MAX_TOOL_RESULT_CHARS,
                   max_steps: int = MAX_PANEL_STEPS) -> str:
    """面板内容的 **markdown 文本**（CardKit 流式写入用）。

    为什么需要它：CardKit 只能按 ``element_id`` 往元素里**写文本**，不能边流边改结构，
    所以那个「折叠面板」里的内容必须是一个 markdown 子元素的字符串。这里复用与
    :func:`unified_panel` 同样的截断/收轮规则（`_cap` + `truncate` + `_round_title`），
    保证两条路径**看到的内容一致**，只是载体不同（多个元素 vs 一个 markdown）。
    """
    max_reasoning_chars = _cap(max_reasoning_chars, MAX_REASONING_CHARS)
    max_tool_chars = _cap(max_tool_chars, MAX_TOOL_RESULT_CHARS)
    max_steps = _cap(max_steps, MAX_PANEL_STEPS)
    lines: List[str] = []
    round_list = [item for item in rounds if isinstance(item, dict) and str(item.get("text") or "")]
    if round_list:
        keep = max(1, min(len(round_list), max_reasoning_chars // _MIN_ROUND_CHARS))
        share = max(_MIN_ROUND_CHARS, max_reasoning_chars // keep)
        dropped = len(round_list) - keep
        if dropped > 0:
            lines.append(_i18n.t("panel.rounds_trimmed", n=dropped))
        for index, item in enumerate(round_list[-keep:], start=dropped + 1):
            body = truncate(str(item.get("text") or ""), share)
            lines.append(f"**{_round_title(index, item.get('elapsed_ms'))}**\n\n{body}")
    elif reasoning:
        lines.append(truncate(reasoning, max_reasoning_chars))
    steps = [str(item) for item in tools]
    if steps and len(steps) > max_steps:
        lines.append(_i18n.t("panel.trimmed", n=len(steps) - max_steps))
        steps = steps[-max_steps:]
    for item in steps:
        lines.append(truncate(item, max_tool_chars))
    return "\n\n".join(lines)


def cardkit_entity_card(answer: str, panel_text: str, *, streaming: bool = True,
                        status: Any = None) -> Dict[str, Any]:
    """**CardKit 实体卡**的 JSON（结构固定：一个正文元素 + 一个折叠面板，面板里一个 markdown）。

    结构固定是有原因的（真机实测）：`card_element.content` 只能按 id 写内容；而
    `message.patch` / `card.update` 这类**结构性写入会关闭流式会话**（再写元素得 `300309`）。
    所以：流式期间只写这两个元素，收尾才用 patch 整卡替换（那一刻流式本来也结束了）。

    面板的边框色按状态给（收尾帧会连面板一起换成带色的完整卡，这里只是建实体时的初始值）。
    """
    return {
        "schema": SCHEMA,
        "config": {"streaming_mode": bool(streaming), "update_multi": True,
                   "summary": _summary_of(answer, fallback=DEFAULT_TITLE)},
        "body": {"elements": [
            {"tag": "markdown", "element_id": CARDKIT_ANSWER_ID,
             "content": answer_or_pending(answer, streaming)},
            {"tag": "collapsible_panel", "element_id": CARDKIT_PANEL_ID, "expanded": False,
             "header": {"title": _i18n.i18n_text("panel.title"),
                        "vertical_align": "center",
                        "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined",
                                 "size": "16px 16px"},
                        "icon_position": "right", "icon_expanded_angle": -180},
             "border": {"color": border_for_status(status), "corner_radius": "8px"},
             "padding": "8px 8px 8px 8px",
             "elements": [{"tag": "markdown", "element_id": CARDKIT_PANEL_BODY_ID,
                           "content": panel_text or " "}]},
        ]},
    }


def unified_panel(*, reasoning: str = "", rounds: Sequence[Dict[str, Any]] = (),
                  tools: Sequence[str] = (),
                  expanded: bool = False,
                  status: Any = None,
                  summary: Optional[str] = None,
                  max_reasoning_chars: int = MAX_REASONING_CHARS,
                  max_tool_chars: int = MAX_TOOL_RESULT_CHARS,
                  max_steps: int = MAX_PANEL_STEPS,
                  ) -> Optional[Dict[str, Any]]:
    """统一面板：把推理过程和工具调用合并进一个可折叠块。

    这是「统一面板」功能的本体 —— 正文区保持干净，过程信息全部收进底部一个面板，
    而不是推理一个面板、工具另一个面板。没有内容时返回 None（调用方据此不渲染）。

    传了 ``rounds`` 就**按轮分段渲染**（每轮一个耗时标题行），这是 aiduPOP 的观感：
    「一轮 = 一段连续推理，被正文或工具打断」。没传就退回把 ``reasoning`` 当一整段渲染。
    每轮分到的截断额度是 ``max_reasoning_chars`` 的均分，且**渲染轮数收在
    ``max_reasoning_chars // _MIN_ROUND_CHARS`` 以内**（更早的轮补一行「…更早的 N 轮已折叠」）
    —— 这样「推理文本上限」这个配置约束的是**总量**，不会因为轮数变多而整体膨胀。

    所有进来的长文本都过 :func:`truncate`：面板是「收纳」不是「倾倒」，
    真跑一个长任务，原始推理和工具输出能把卡片撑到几十屏。

    三个上限都先过 :func:`_cap`：配置写 0 / 负数等于退回默认上限，**不是**取消上限
    （取消上限会让卡片被飞书拒收，后果见 :func:`_cap`）。
    """
    max_reasoning_chars = _cap(max_reasoning_chars, MAX_REASONING_CHARS)
    max_tool_chars = _cap(max_tool_chars, MAX_TOOL_RESULT_CHARS)
    max_steps = _cap(max_steps, MAX_PANEL_STEPS)
    inner: List[Dict[str, Any]] = []
    # 面板**自己**也要为飞书的元素硬墙让路（见 :data:`FEISHU_ELEMENT_LIMIT`）：
    # 面板的元素 = 轮次行 + 步骤行 + 可能的折叠提示。`max_panel_steps` 是用户可配的，
    # 配大了会把整张卡顶废（超限是**整卡不渲染**，不是截断），所以这里先算可用额度。
    # 额度与 :func:`fit_reply_card` 的判据**同源**（`_PANEL_CHILDREN_ROOM`）：
    # 面板自认为放得下 ⇒ 阶梯也认，不会出现「面板被整块摘掉」的缝。
    panel_room = max(2, _PANEL_CHILDREN_ROOM)

    round_list = [item for item in rounds if isinstance(item, dict) and str(item.get("text") or "")]
    if round_list:
        # 每轮至少给 _MIN_ROUND_CHARS 才读得下去，但这意味着**轮数必须收住**：
        # 旧写法 share = max(120, 预算 // N) 在 N > 预算/120 时让渲染总量恒等于 120·N，
        # 与配置无关 —— 默认预算 1200 时第 11 轮起线性膨胀，实测 40 轮撑到 7845 字符，
        # 把 1500 字的正文一起顶穿字节预算 → fit_reply_card 降载到 no-panel，
        # **整个推理面板消失**（只剩一行 INFO 日志，正是本项目最怕的静默降级）。
        # 所以宁可有界地少显示历史轮：渲染轮数 ≤ 预算 // _MIN_ROUND_CHARS。
        # 两个约束一起收：字符预算（均分额度）与元素硬墙（给步骤行留出位置，
        # 有工具时至少留 1 行，没工具时轮次可以吃满面板额度）。
        keep = max(1, min(len(round_list),
                          max_reasoning_chars // _MIN_ROUND_CHARS,
                          max(1, panel_room - (1 if tools else 0) - 1)))
        share = max(_MIN_ROUND_CHARS, max_reasoning_chars // keep)
        dropped = len(round_list) - keep
        if dropped > 0:
            inner.append(md(_i18n.t("panel.rounds_trimmed", n=dropped)))
        for index, item in enumerate(round_list[-keep:], start=dropped + 1):
            body = truncate(str(item.get("text") or ""), share)
            inner.append(md(f"**{_round_title(index, item.get('elapsed_ms'))}**\n\n{body}"))
    elif reasoning:
        inner.append(md(truncate(reasoning, max_reasoning_chars)))
    steps = [str(item) for item in tools]
    # 面板里已经放了几个元素，剩下的额度才是步骤能用的
    max_steps = max(1, min(max_steps, panel_room - len(inner)))
    if max_steps > 0 and len(steps) > max_steps:
        # 保留最近几步：排查问题基本只看尾部，早期的步骤价值随时间递减。
        dropped = len(steps) - max_steps
        inner.append(md(_i18n.t("panel.trimmed", n=dropped)))
        steps = steps[-max_steps:]
    for item in steps:
        inner.append(md(truncate(item, max_tool_chars)))
    border = border_for_status(status)
    if not inner and not status:
        return None
    if not inner:
        # 有结局但没有过程数据：补一行状态文字 —— 面板不能是空的（空面板飞书会拒，
        # 而且用户看不到任何东西），状态色也需要一个可见的落点。
        inner.append(md(_i18n.t(_STATUS_TEXT_KEYS.get(str(status or ""), "panel.title"))))
    # 标题必须是 plain_text；优先级：调用方给的摘要行 > 固定的「执行详情」。
    if summary:
        title: Dict[str, Any] = {"tag": "plain_text", "content": str(summary)}
    elif tools:
        # 英文单复数：1 tool call / N tool calls
        key = "panel.title_tools_one" if len(tools) == 1 else "panel.title_tools"
        title = _i18n.i18n_text(key, n=len(tools))
    else:
        title = _i18n.i18n_text("panel.title")
    return collapsible(title, inner, expanded=expanded, border_color=border)


# --------------------------------------------------------------------------- #
# 1.0：澄清交互卡（必须接点击，所以整条链路都用 legacy 方言）
# --------------------------------------------------------------------------- #
def legacy_card(*, elements: Sequence[Dict[str, Any]], template: str = "orange",
                title: Union[str, Dict[str, Any]] = DEFAULT_TITLE,
                update_multi: bool = True) -> Dict[str, Any]:
    """legacy 1.0 卡片 —— **顶层 ``elements``，绝不含 ``schema`` / ``body``**。

    混进 ``schema: "2.0"`` 会让飞书按 2.0 解析，从而拒绝 ``action`` 按钮行，
    按钮点击也就永远送不到服务端。这是本模块最需要守住的不变量。

    ``title`` 可给 :func:`i18n.i18n_text` 的双语节点（1.0 header 接受
    ``i18n_content``，真机已证）。
    """
    config: Dict[str, Any] = {"wide_screen_mode": True}
    if update_multi:
        config["update_multi"] = True
    title_node = title if isinstance(title, dict) else {"tag": "plain_text", "content": title}
    return {
        "config": config,
        "header": {"template": template, "title": title_node},
        "elements": list(elements),
    }


def _clarify_elements(question: str, choices: Sequence[str], *, clarify_id: str,
                      session_key: str) -> List[Dict[str, Any]]:
    buttons: List[Dict[str, Any]] = []
    for idx, choice in enumerate(choices, start=1):
        buttons.append(button(
            f"{idx}. {choice}",
            {"larkdeck_action": "clarify", "clarify_id": clarify_id,
             "session_key": session_key, "question": question, "answer": str(choice)},
            btype="primary" if idx == 1 else "default",
        ))
    buttons.append(button(
        _i18n.i18n_text("clarify.other"),
        {"larkdeck_action": "clarify", "clarify_id": clarify_id,
         "session_key": session_key, "question": question, "answer": OTHER_VALUE},
    ))
    return [md(f"\u2753 {question}"), action_row(buttons)]


def clarify_card(question: str, choices: Sequence[str], *, clarify_id: str,
                 session_key: str, multi: bool = False) -> Dict[str, Any]:
    """带按钮的澄清卡（**1.0 方言**）。

    按钮 value 里带 ``larkdeck_action="clarify"``，由 LarkDeckMixin 重写的
    ``_on_card_action_trigger`` 拦截并调用 ``resolve_gateway_clarify()``。
    """
    elements = _clarify_elements(question, choices,
                                 clarify_id=clarify_id, session_key=session_key)
    elements.append(note_i18n("clarify.multi_hint" if multi else "clarify.hint"))
    return legacy_card(elements=elements, template="orange",
                       title=_i18n.i18n_text("clarify.header"))


def _clarify_options(choices: Sequence[str]) -> List[Dict[str, Any]]:
    """2.0 下拉的选项：显示用带序号，**回调值用原始选项文本**（答案要的是规范标签）。

    去重：``select_static`` 的 ``value`` **不可重复**（官方文档明写「否则交互异常、
    服务端无法区分选了哪个」），所以重复的选项只留第一个。
    """
    options: List[Dict[str, Any]] = []
    seen: set = set()
    for idx, choice in enumerate(choices, start=1):
        text = str(choice)
        if text in seen:
            continue
        seen.add(text)
        options.append({"text": {"tag": "plain_text", "content": f"{idx}. {text}"},
                        "value": text})
    return options


def clarify_card_2(question: str, choices: Sequence[str], *, clarify_id: str,
                   session_key: str, multi: bool = False) -> Dict[str, Any]:
    """带下拉/输入框的澄清卡（**2.0 方言**，组件级 ``behaviors`` 接回调）。

    ⚠️ **默认不启用**（配置 ``clarify_dialect`` 默认 ``"1.0"``）。原因不是它不可行 ——
    官方文档与 aiduPOP 的实拍都支持这条路 —— 而是这个项目对**要接服务端点击的卡片**
    有硬纪律：只有真机点一次才算证据（``AGENTS.md`` 不变量 5，这条纪律是被「抄第三方
    注释」坑出来的）。真机确证后把默认值翻成 ``"2.0"`` 即可，拦截逻辑一行都不用改：
    ``behaviors`` 的 ``value`` 会**原样**成为 ``event.action.value``，所以
    ``larkdeck_action`` 那个拦截键照旧成立；用户选了什么则在 ``action.option``，
    输入框内容在 ``action.input_value``（见 ``adapter._ld_clarify_answer``）。

    多选用 ``multi_select_static``（回调的 ``option`` 是**列表**，要拼成 JSON 数组
    —— 与网关那条「文字回答多选」的规范一致，见 ``clarify_gateway`` 的
    ``_coerce_multi_select_text``）。多选时**不给**自由输入框：混在一起会与
    「编号/标签」的解析规则打架。
    """
    value: Dict[str, Any] = {"larkdeck_action": "clarify", "clarify_id": clarify_id,
                             "session_key": session_key, "question": question}
    selector: Dict[str, Any] = {
        "tag": "multi_select_static" if multi else "select_static",
        "placeholder": {"tag": "plain_text",
                        "content": _i18n.t("clarify.pick_multi" if multi else "clarify.pick")},
        "options": _clarify_options(choices),
        "behaviors": [{"type": "callback", "value": dict(value)}],
    }
    elements: List[Dict[str, Any]] = [md(f"\u2753 {question}"), selector]
    if not multi:
        elements.append({
            "tag": "input",
            "label": _i18n.i18n_text("clarify.other"),
            "placeholder": {"tag": "plain_text", "content": _i18n.t("clarify.other_hint")},
            # 只带路由键与澄清标识：**模式由载荷推导**（有 `action.input_value` 就是
            # 自由文本），所以这里不再写一个没人读的 `free_text` 标志键
            # （2026-09-13 审计：那个键全仓只有写入点、没有读取点）。
            "behaviors": [{"type": "callback", "value": dict(value)}],
        })
    elements.append(footnote(_i18n.t("clarify.multi_hint" if multi else "clarify.hint")))
    return card(elements=elements, template="orange",
                title=_i18n.i18n_text("clarify.header"), summary=question)


def clarify_resolved_card_2(*, question: str, answer: Any, user_name: str) -> Dict[str, Any]:
    """2.0 的已答复卡 —— **必须与待答卡同方言**，否则回调里回填的那一帧会被飞书丢弃。"""
    return card(elements=[md(f"\u2753 {question}"),
                          md(f"\u2705 **{_clarify_answer_label(answer)}**\u3000\u2014\u3000{user_name}")],
                template="green", title=_i18n.i18n_text("clarify.header"), summary=question)


def _clarify_answer_label(answer: Any) -> str:
    """把（可能来自多选 JSON 的）答案整理成给人看的一行。"""
    text = str(answer or "")
    if text.startswith("["):
        try:
            items = json.loads(text)
            if isinstance(items, list):
                text = "、".join(str(item) for item in items)
        except Exception:
            pass
    return _i18n.t("clarify.other") if text == OTHER_VALUE else text


def clarify_resolved_card(*, question: str, answer: str, user_name: str) -> Dict[str, Any]:
    """点击后原地替换的已答复卡。

    必须与待答卡同为 1.0 方言 —— 回调里回填的卡片也走同一条 legacy 轨迹，
    换成 2.0 会被飞书丢弃（HFC 的 ``interaction callback card suppressed`` 就是踩了这个）。
    """
    label = _clarify_answer_label(answer)
    return legacy_card(
        elements=[md(f"\u2753 {question}"), md(f"\u2705 **{label}**\u3000\u2014\u3000{user_name}")],
        template="green", title=_i18n.i18n_text("clarify.header"),
    )
