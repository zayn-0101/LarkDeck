"""飞书卡片 JSON 构造 —— 纯函数、无网络、无 Hermes 依赖，便于单测。

两种方言，按「要不要服务端回调」分开用
--------------------------------------
飞书卡片有两套互不兼容的写法，混用会被直接拒绝：

* **legacy 1.0**：顶层 ``elements`` + ``{"tag": "action", "actions": [...]}`` 按钮行。
  按钮带 ``value``，点击经 WebSocket 送到 ``p2.card.action.trigger``。
  **需要服务端处理点击的卡片必须用这套。**
* **schema 2.0**：``config`` / ``header`` / ``body.elements``。
  提供 ``streaming_mode``（打字机流式）和 ``collapsible_panel``（可折叠面板）。

为什么不能只用 2.0
------------------
CardKit v2 的 ``behaviors`` 回调是**客户端**交互，到不了 ``p2.card.action.trigger``
这个 WebSocket 处理器；反过来，1.0 的 ``action`` 容器**嵌进 2.0 卡会被拒**。
所以：**带按钮、要服务端接点击的卡 → 1.0；只要流式/折叠、不接点击的卡 → 2.0。**
（结论来自 hermes_feishu_card/render.py 的 legacy-callback-card 实现与内置
FeishuAdapter 的 ``_card()`` —— 两者在本机都是已验证可用的。）

所以本模块导出两组构造函数：
  * :func:`clarify_card` / :func:`clarify_resolved_card` —— 1.0（要接点击）
  * :func:`reply_card` —— 2.0（要流式 + 折叠面板，不接点击）

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
        "border": {"color": "grey", "corner_radius": "8px"},
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
                tools: Optional[int] = None, context: str = "") -> Optional[str]:
    """页脚一行 —— 全部用「符号 + 数字 + 英文缩写」，天然无需翻译（不依赖 i18n）。

    各段之间用 ``·`` 分隔；一段都没有时返回 ``None``，调用方就不渲染脚注元素。
    """
    parts: List[str] = []
    if model:
        parts.append(f"🤖 {model}")
    # 注意排除 bool：Python 里 isinstance(True, int) 为真，不排会拼出「🔧 True」。
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
         title: str = DEFAULT_TITLE, streaming: Optional[bool] = None,
         update_multi: bool = True, summary: str = "") -> Dict[str, Any]:
    """2.0 卡片：``config`` / ``header`` / ``body.elements``。

    **不带按钮行** —— 需要接点击的卡片请用 :func:`legacy_card`。
    """
    config: Dict[str, Any] = {"wide_screen_mode": True}
    if update_multi:
        # 多端同步更新：所有客户端都看到同一份最新卡片。
        config["update_multi"] = True
    if streaming is not None:
        config["streaming_mode"] = bool(streaming)
    # 流式卡必须带 summary，否则通知栏空白、且部分场景会被拒。
    # 内容为空时（seed 帧 / 空 finalize 帧）退回卡片标题，不能留空串。
    if streaming or summary:
        config["summary"] = _summary_of(summary, fallback=str(title or DEFAULT_TITLE))
    return {
        "schema": SCHEMA,
        "config": config,
        "header": {"template": template,
                   "title": {"tag": "plain_text", "content": title}},
        "body": {"elements": list(elements)},
    }


def reply_card(answer: str, *, streaming: bool = False, panel: Optional[Dict[str, Any]] = None,
               footer: Optional[str] = None, template: str = "blue",
               title: str = DEFAULT_TITLE) -> Dict[str, Any]:
    """正文回复卡：正文区 + 可选统一面板 + 可选脚注。"""
    elements: List[Dict[str, Any]] = [md(answer)]
    if panel:
        elements.append(panel)
    if footer:
        elements.append(footnote(footer))
    return card(elements=elements, template=template, title=title,
                streaming=streaming, summary=answer)


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


def card_bytes(node: Dict[str, Any]) -> int:
    """卡片 JSON 的 utf-8 字节数（发送时用的就是这份序列化）。"""
    try:
        return len(json.dumps(node, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0


def fit_reply_card(answer: str, *, streaming: bool = False,
                   panel: Optional[Dict[str, Any]] = None, footer: Optional[str] = None,
                   template: str = "blue", title: str = DEFAULT_TITLE,
                   budget: int = CARD_BYTE_BUDGET) -> "tuple[Dict[str, Any], str]":
    """构造回复卡，超预算时**分级丢装饰**。返回 ``(card, 降级档位)``。

    档位：``ok`` → ``no-panel`` → ``bare`` → ``over-budget``。

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
                          template=template, title=title)
        if card_bytes(node) <= budget:
            return node, tier
    # 连装饰全摘都超预算：正文本身太大。**照常返回**，让发送失败去走官方回落
    # （官方会分块），而不是在这里把答案切掉。
    return reply_card(answer, streaming=streaming, template=template, title=title), "over-budget"


def _round_title(index: int, elapsed_ms: Any) -> str:
    """推理轮的标题行：``第 N 轮 · 6.2s``（耗时是**相对时长**，不是时刻）。"""
    base = _i18n.t("panel.round_n", n=index)
    if isinstance(elapsed_ms, int) and not isinstance(elapsed_ms, bool) and elapsed_ms > 0:
        return f"{base} · {format_elapsed(max(0.1, elapsed_ms / 1000.0))}"
    return base


def unified_panel(*, reasoning: str = "", rounds: Sequence[Dict[str, Any]] = (),
                  tools: Sequence[str] = (),
                  expanded: bool = False,
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

    round_list = [item for item in rounds if isinstance(item, dict) and str(item.get("text") or "")]
    if round_list:
        # 每轮至少给 _MIN_ROUND_CHARS 才读得下去，但这意味着**轮数必须收住**：
        # 旧写法 share = max(120, 预算 // N) 在 N > 预算/120 时让渲染总量恒等于 120·N，
        # 与配置无关 —— 默认预算 1200 时第 11 轮起线性膨胀，实测 40 轮撑到 7845 字符，
        # 把 1500 字的正文一起顶穿字节预算 → fit_reply_card 降载到 no-panel，
        # **整个推理面板消失**（只剩一行 INFO 日志，正是本项目最怕的静默降级）。
        # 所以宁可有界地少显示历史轮：渲染轮数 ≤ 预算 // _MIN_ROUND_CHARS。
        keep = max(1, min(len(round_list), max_reasoning_chars // _MIN_ROUND_CHARS))
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
    if max_steps > 0 and len(steps) > max_steps:
        # 保留最近几步：排查问题基本只看尾部，早期的步骤价值随时间递减。
        dropped = len(steps) - max_steps
        inner.append(md(_i18n.t("panel.trimmed", n=dropped)))
        steps = steps[-max_steps:]
    for item in steps:
        inner.append(md(truncate(item, max_tool_chars)))
    if not inner:
        return None
    # 标题必须是 plain_text，且带上工具计数 —— 收起状态下这是用户唯一看得到的信息
    if tools:
        # 英文单复数：1 tool call / N tool calls
        key = "panel.title_tools_one" if len(tools) == 1 else "panel.title_tools"
        title = _i18n.i18n_text(key, n=len(tools))
    else:
        title = _i18n.i18n_text("panel.title")
    return collapsible(title, inner, expanded=expanded)


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


def clarify_resolved_card(*, question: str, answer: str, user_name: str) -> Dict[str, Any]:
    """点击后原地替换的已答复卡。

    必须与待答卡同为 1.0 方言 —— 回调里回填的卡片也走同一条 legacy 轨迹，
    换成 2.0 会被飞书丢弃（HFC 的 ``interaction callback card suppressed`` 就是踩了这个）。
    """
    label = _i18n.t("clarify.other") if answer == OTHER_VALUE else answer
    return legacy_card(
        elements=[md(f"\u2753 {question}"), md(f"\u2705 **{label}**\u3000\u2014\u3000{user_name}")],
        template="green", title=_i18n.i18n_text("clarify.header"),
    )
