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
    if isinstance(tools, int) and tools > 0:
        parts.append(f"🔧 {tools}")
    if context:
        parts.append(context)
    if isinstance(duration, (int, float)) and duration > 0:
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
def truncate(text: str, limit: int, *, key: str = "panel.overflow") -> str:
    """超长文本截断并补一行「已省略 N 字符」，而不是静默切掉。

    静默截断会让用户以为模型就说了这么多；补一行说明才知道下面还有内容。
    截断点回退到最近一个空白，避免把一个词劈成两半。
    """
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    hidden = len(text) - limit
    head = text[:limit]
    if " " in head[-80:]:
        head = head[: head.rfind(" ")]
    return f"{head.rstrip()}\n> {_i18n.t(key, n=hidden)}"


# --------------------------------------------------------------------------- #
# 2.0：回复卡（流式 + 统一面板）
# --------------------------------------------------------------------------- #
def _summary_of(text: str) -> Dict[str, Any]:
    """2.0 卡片的 ``config.summary`` —— 官方 SDK 与 HFC 都强制带，漏了会出问题。"""
    flat = " ".join(str(text or "").split())
    return {"content": flat[:SUMMARY_MAX]}


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
    if streaming or summary:
        config["summary"] = _summary_of(summary)
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


def unified_panel(*, reasoning: str = "", tools: Sequence[str] = (),
                  expanded: bool = False,
                  max_reasoning_chars: int = MAX_REASONING_CHARS,
                  max_tool_chars: int = MAX_TOOL_RESULT_CHARS,
                  max_steps: int = MAX_PANEL_STEPS,
                  ) -> Optional[Dict[str, Any]]:
    """统一面板：把推理过程和工具调用合并进一个可折叠块。

    这是「统一面板」功能的本体 —— 正文区保持干净，过程信息全部收进底部一个面板，
    而不是推理一个面板、工具另一个面板。没有内容时返回 None（调用方据此不渲染）。

    所有进来的长文本都过 :func:`truncate`：面板是「收纳」不是「倾倒」，
    真跑一个长任务，原始推理和工具输出能把卡片撑到几十屏。
    """
    inner: List[Dict[str, Any]] = []
    if reasoning:
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
    title = (_i18n.i18n_text("panel.title_tools", n=len(tools)) if tools
             else _i18n.i18n_text("panel.title"))
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
