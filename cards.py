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

界面文案走 :mod:`larkdeck.i18n`，靠飞书原生 ``i18n_content`` 做双语。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import i18n as _i18n

SCHEMA = "2.0"

#: 「其他（自己输入）」按钮的哨兵值；点它不提交答案，而是让网关等用户下一条文字。
OTHER_VALUE = "__larkdeck_other__"

DEFAULT_TITLE = "Hermes"

#: 2.0 卡片在通知栏 / 会话列表里显示的一句话摘要上限，超长会被截断。
SUMMARY_MAX = 120


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


def button(label: str, value: Dict[str, Any], *, btype: str = "default") -> Dict[str, Any]:
    """按钮元素（1.0 方言里必须放进 :func:`action_row`）。"""
    return {
        "tag": "button",
        "type": btype,
        "text": {"tag": "plain_text", "content": label},
        "value": value,
    }


def action_row(buttons: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """1.0 的按钮行容器 —— 服务端回调的唯一可靠载体。"""
    return {"tag": "action", "actions": list(buttons)}


def collapsible(title_node: Dict[str, Any], elements: Sequence[Dict[str, Any]], *,
                expanded: bool = False) -> Dict[str, Any]:
    """可折叠面板（**2.0 专属**元素）。用于把推理过程 / 工具调用收进底部。"""
    return {
        "tag": "collapsible_panel",
        "expanded": bool(expanded),
        "header": {"title": title_node},
        "elements": list(elements),
    }


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
                  expanded: bool = False) -> Optional[Dict[str, Any]]:
    """统一面板：把推理过程和工具调用合并进一个可折叠块。

    这是「统一面板」功能的本体 —— 正文区保持干净，过程信息全部收进底部一个面板，
    而不是推理一个面板、工具另一个面板。没有内容时返回 None（调用方据此不渲染）。
    """
    inner: List[Dict[str, Any]] = []
    if reasoning:
        inner.append(md(reasoning))
    for item in tools:
        inner.append(md(item))
    if not inner:
        return None
    return collapsible(_i18n.md_i18n_text("panel.title"), inner, expanded=expanded)


# --------------------------------------------------------------------------- #
# 1.0：澄清交互卡（必须接点击，所以整条链路都用 legacy 方言）
# --------------------------------------------------------------------------- #
def legacy_card(*, elements: Sequence[Dict[str, Any]], template: str = "orange",
                title: str = DEFAULT_TITLE,
                update_multi: bool = True) -> Dict[str, Any]:
    """legacy 1.0 卡片 —— **顶层 ``elements``，绝不含 ``schema`` / ``body``**。

    混进 ``schema: "2.0"`` 会让飞书按 2.0 解析，从而拒绝 ``action`` 按钮行，
    按钮点击也就永远送不到服务端。这是本模块最需要守住的不变量。
    """
    config: Dict[str, Any] = {"wide_screen_mode": True}
    if update_multi:
        config["update_multi"] = True
    return {
        "config": config,
        "header": {"template": template,
                   "title": {"tag": "plain_text", "content": title}},
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
        _i18n.t("clarify.other"),
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
                       title=_i18n.t("clarify.header"))


def clarify_resolved_card(*, question: str, answer: str, user_name: str) -> Dict[str, Any]:
    """点击后原地替换的已答复卡。

    必须与待答卡同为 1.0 方言 —— 回调里回填的卡片也走同一条 legacy 轨迹，
    换成 2.0 会被飞书丢弃（HFC 的 ``interaction callback card suppressed`` 就是踩了这个）。
    """
    label = _i18n.t("clarify.other") if answer == OTHER_VALUE else answer
    return legacy_card(
        elements=[md(f"\u2753 {question}"), md(f"\u2705 **{label}**\u3000\u2014\u3000{user_name}")],
        template="green", title=_i18n.t("clarify.header"),
    )
