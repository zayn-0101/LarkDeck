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
    # 推理轮标题。注意「轮」= 一段连续推理（被正文或工具打断），不是 API 调用次数。
    "panel.round_n":      {ZH: "第 {n} 轮", EN: "Round {n}"},
    # 轮数超过渲染上限时，更早的轮整轮折叠（保证面板总量有界，见 cards.unified_panel）
    "panel.rounds_trimmed": {ZH: "…更早的 {n} 轮已折叠", EN: "…{n} earlier rounds folded"},
    # 回合结局的文字兜底（面板没有别的正文时显示；边框色是主要载体）
    "stream.pending":     {ZH: "⏳ 正在生成…", EN: "⏳ Generating…"},
    "panel.status_ok":    {ZH: "✅ 已完成", EN: "✅ Completed"},
    "panel.status_error": {ZH: "❌ 执行出错", EN: "❌ Failed"},
    "panel.status_stopped": {ZH: "⛔ 已中止", EN: "⛔ Stopped"},
    # clarify
    "clarify.header":     {ZH: "需要你确认", EN: "Needs your input"},
    "clarify.other":      {ZH: "其他（我直接输入）", EN: "Other (I'll type it)"},
    "clarify.hint":       {ZH: "点按钮，或直接回复文字都行。", EN: "Tap a button, or just reply with text."},
    # 2.0 澄清卡：下拉占位 + 输入框占位（1.0 卡不用这两条）
    "clarify.pick":       {ZH: "选一个（也可在下面直接输入）", EN: "Pick one (or type below)"},
    "clarify.pick_multi": {ZH: "可多选：点开勾选", EN: "Multi-select: tap to pick"},
    "clarify.other_hint": {ZH: "直接输入你的答案，回车提交", EN: "Type your answer, press Enter"},
    "clarify.multi_hint": {ZH: "可多选，回复编号用逗号隔开。", EN: "Multi-select — reply with numbers separated by commas."},
    # 澄清点击的**瞬时提示**（toast）：失败态与「其他」提示态都**不动卡片** ——
    # 动卡会让一次迟到的重复点击把「已确认」回退成「待答」，那是不可逆的用户可见错误。
    "clarify.toast_failed": {ZH: "提交未生效：可能已被处理或过期，请重试",
                             EN: "Could not submit: already handled or expired — please retry"},
    "clarify.toast_rejected": {ZH: "这个答案没有被接受，请重试",
                               EN: "That answer was not accepted — please retry"},
    "clarify.toast_typing": {ZH: "请在输入框里输入答案（或直接回复文字）",
                             EN: "Type your answer in the box (or just reply with text)"},
    # /larkdeck 自检卡（R9）。三条状态行由 context.status_lines() 组装；没记录写「无记录」，
    # **绝不写「正常」** —— 一个永远说「正常」的自检与一个坏掉的自检，用户分辨不出来。
    "cmd.description":    {ZH: "larkdeck 状态：版本 / 生效传输 / 钩子 / 心跳",
                           EN: "larkdeck status: version / transport / hooks / heartbeats"},
    "cmd.header":         {ZH: "{name} · 传输 {transport} · 钩子 {wired}/{total} 已挂",
                           EN: "{name} · transport {transport} · hooks {wired}/{total} wired"},
    "cmd.help":           {ZH: "用法：/larkdeck [status|help]\n"
                               "· status（默认）：本卡 —— 版本 / 生效传输 / 钩子 / 三条心跳记录\n"
                               "· help：这段说明\n"
                               "⚠️ 仅空闲态可用：正在生成回答时发的命令会被当成普通输入排队。",
                           EN: "Usage: /larkdeck [status|help]\n"
                               "· status (default): this card — version / active transport / hooks / three heartbeat records\n"
                               "· help: this text\n"
                               "⚠️ Idle state only: a command sent while a reply is streaming is queued as plain input."},
    "cmd.unknown":        {ZH: "不认识的参数：{arg}（可用：status / help）",
                           EN: "Unknown argument: {arg} (available: status / help)"},
    "cmd.failed":         {ZH: "状态读取失败：{error}", EN: "Status read failed: {error}"},
    "status.inbound":     {ZH: "入站心跳：{when} · 累计 {n} 条消息",
                           EN: "Inbound heartbeat: {when} · {n} messages"},
    "status.frame_ok":    {ZH: "最近写卡：{when} · 累计 {n} 次",
                           EN: "Last card write: {when} · {n} writes"},
    "status.frame_fail":  {ZH: "最近写卡失败：{when} · 累计 {n} 次 · {reason}",
                           EN: "Last write failure: {when} · {n} · {reason}"},
    "status.frame_fail_none": {ZH: "最近写卡失败：无记录（累计 0 次）",
                               EN: "Last write failure: no record (0)"},
    "status.none":        {ZH: "无记录", EN: "no record"},
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
