"""飞书卡片 JSON 构造 —— 纯函数、无网络、无 Hermes 依赖，便于单测。

统一用卡片 2.0 schema：``streaming_mode``、``collapsible_panel`` 都是 2.0 能力。
界面文案走 :mod:`larkdeck.i18n`，靠飞书原生 ``i18n_content`` 做双语。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from . import i18n as _i18n

SCHEMA = "2.0"

#: 「其他（自己输入）」按钮的哨兵值；点它不提交答案，而是让网关等用户下一条文字。
OTHER_VALUE = "__larkdeck_other__"

DEFAULT_TITLE = "Hermes"


# --------------------------------------------------------------------------- #
# 基础元素
# --------------------------------------------------------------------------- #
def md(content: str) -> Dict[str, Any]:
    """markdown 文本块。空内容给一个空格，避免卡片元素被判为空。"""
    return {"tag": "markdown", "content": content if content else " "}


def note(content: str) -> Dict[str, Any]:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": content}]}


def note_i18n(key: str, **fmt: Any) -> Dict[str, Any]:
    """双语脚注行。"""
    return {"tag": "note", "elements": [_i18n.i18n_text(key, **fmt)]}


def button(label: str, value: Dict[str, Any], *, btype: str = "default") -> Dict[str, Any]:
    return {
        "tag": "button",
        "type": btype,
        "text": {"tag": "plain_text", "content": label},
        "value": value,
    }


def collapsible(title_node: Dict[str, Any], elements: Sequence[Dict[str, Any]], *,
                expanded: bool = False) -> Dict[str, Any]:
    """可折叠面板（卡片 2.0 元素）。用于把推理过程 / 工具调用收进底部。"""
    return {
        "tag": "collapsible_panel",
        "expanded": bool(expanded),
        "header": {"title": title_node},
        "elements": list(elements),
    }


def card(*, elements: Sequence[Dict[str, Any]], template: str = "blue",
         title: str = DEFAULT_TITLE, streaming: Optional[bool] = None,
         update_multi: bool = True) -> Dict[str, Any]:
    config: Dict[str, Any] = {"wide_screen_mode": True}
    if update_multi:
        # 多端同步更新：所有客户端都看到同一份最新卡片。
        config["update_multi"] = True
    if streaming is not None:
        config["streaming_mode"] = bool(streaming)
    return {
        "schema": SCHEMA,
        "config": config,
        "header": {"template": template,
                   "title": {"tag": "plain_text", "content": title}},
        "body": {"elements": list(elements)},
    }


# --------------------------------------------------------------------------- #
# 回复卡片
# --------------------------------------------------------------------------- #
def reply_card(answer: str, *, streaming: bool = False, panel: Optional[Dict[str, Any]] = None,
               footer: Optional[str] = None, template: str = "blue",
               title: str = DEFAULT_TITLE) -> Dict[str, Any]:
    """正文回复卡：正文区 + 可选统一面板 + 可选脚注。"""
    elements: List[Dict[str, Any]] = [md(answer)]
    if panel:
        elements.append(panel)
    if footer:
        elements.append(note(footer))
    return card(elements=elements, template=template, title=title, streaming=streaming)


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
# Clarify 交互卡
# --------------------------------------------------------------------------- #
def clarify_card(question: str, choices: Sequence[str], *, clarify_id: str,
                 session_key: str, multi: bool = False) -> Dict[str, Any]:
    """带按钮的澄清卡。

    按钮 value 里带 ``larkdeck_action="clarify"``，由 LarkDeckMixin 重写的
    ``_on_card_action_trigger`` 拦截并调用 ``resolve_gateway_clarify()``。
    """
    elements: List[Dict[str, Any]] = [md(f"❓ {question}")]
    for idx, choice in enumerate(choices, start=1):
        elements.append(button(
            f"{idx}. {choice}",
            {"larkdeck_action": "clarify", "clarify_id": clarify_id,
             "session_key": session_key, "answer": str(choice)},
        ))
    elements.append(button(
        _i18n.t("clarify.other"),
        {"larkdeck_action": "clarify", "clarify_id": clarify_id,
         "session_key": session_key, "answer": OTHER_VALUE},
    ))
    elements.append(note_i18n("clarify.multi_hint" if multi else "clarify.hint"))
    return card(elements=elements, template="orange", title=_i18n.t("clarify.header"))


def clarify_resolved_card(*, question: str, answer: str, user_name: str) -> Dict[str, Any]:
    """点击后原地替换的已答复卡，避免用户重复点。"""
    label = _i18n.t("clarify.other") if answer == OTHER_VALUE else answer
    return card(
        elements=[md(f"❓ {question}"), md(f"✅ **{label}**　—　{user_name}")],
        template="green", title=_i18n.t("clarify.header"),
    )
