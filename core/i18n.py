"""双语文案 —— 用飞书卡片原生的 ``i18n_content`` 机制。

要点：服务端**只发一份卡片**，每个文本元素同时携带默认文案和 ``i18n_content``
映射；飞书客户端按自己的语言设置挑一份渲染。所以「跟随客户端语言」是零成本的，
不需要为每种语言各发一遍，也不需要用户配置。

AI 生成的正文**不翻译**，只本地化界面文案（面板标题、按钮、状态、页脚）。
"""

from __future__ import annotations

from typing import Any, Dict

ZH = "zh_cn"
EN = "en_us"

# 顺序即默认语言优先级：第一项进 ``content``（客户端语言不在映射里时的兜底）。
DEFAULT_LOCALES = (ZH, EN)

# key -> {locale: text}；占位符用 str.format 风格的 {name}。
_STRINGS: Dict[str, Dict[str, str]] = {
    # 统一面板
    "panel.title":        {ZH: "执行详情", EN: "Run details"},
    "panel.title_tools":  {ZH: "思考与工具 · {n} 次工具调用", EN: "Thinking & tools · {n} tool calls"},
    # 英文单复数：单次调用时说 "1 tool call" 而不是 "1 tool calls"（中文无变化，但保持同表）。
    "panel.title_tools_one": {ZH: "思考与工具 · 1 次工具调用", EN: "Thinking & tools · 1 tool call"},
    # 溢出保护：内容被截断 / 步骤被裁掉时补一行说明，让用户知道「还有东西但没显示」
    "panel.overflow":     {ZH: "…已省略 {n} 字符", EN: "…{n} chars omitted"},
    "panel.trimmed":      {ZH: "…更早的 {n} 步已折叠", EN: "…{n} earlier steps folded"},
    # clarify
    "clarify.header":     {ZH: "需要你确认", EN: "Needs your input"},
    "clarify.other":      {ZH: "其他（我直接输入）", EN: "Other (I'll type it)"},
    "clarify.hint":       {ZH: "点按钮，或直接回复文字都行。", EN: "Tap a button, or just reply with text."},
    "clarify.multi_hint": {ZH: "可多选，回复编号用逗号隔开。", EN: "Multi-select — reply with numbers separated by commas."},
}


def t(key: str, locale: str = ZH, **fmt: Any) -> str:
    """取 ``key`` 在 ``locale`` 下的文案；缺失则退回默认语言，再退回 key 本身。"""
    entry = _STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(locale) or entry.get(DEFAULT_LOCALES[0]) or key
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError):
            return text
    return text


def i18n_text(key: str, **fmt: Any) -> Dict[str, Any]:
    """生成飞书 ``plain_text`` 元素体的双语版本。

    返回 ``{"tag": "plain_text", "content": <默认语言>, "i18n_content": {...}}``，
    可直接塞进卡片元素的 ``text`` 字段。
    """
    return {
        "tag": "plain_text",
        "content": t(key, DEFAULT_LOCALES[0], **fmt),
        "i18n_content": {loc: t(key, loc, **fmt) for loc in DEFAULT_LOCALES},
    }
