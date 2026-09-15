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
    # 2.0 澄清卡上**没有按钮** ⇒ 脚注不能再说「点按钮」（R11-C1）
    "clarify.hint_2":     {ZH: "点下拉选择，或直接回复文字都行。", EN: "Pick from the list, or just reply with text."},
    # R11-C2：`/larkdeck status` 的三条新记录
    "status.uptime":      {ZH: "⏱ 已运行：{v}", EN: "⏱ Uptime: {v}"},
    "status.fallback":    {ZH: "🪂 掉回纯文本：{n} 次（最近 {when} · {reason}）",
                           EN: "🪂 Fell back to plain text: {n} (last {when} · {reason})"},
    "status.fallback_none": {ZH: "🪂 掉回纯文本：无记录", EN: "🪂 Fell back to plain text: no record"},
    "status.codes":       {ZH: "❗ 错误码（累计 {n} 次）：{top}", EN: "❗ Error codes ({n} total): {top}"},
    "status.codes_none":  {ZH: "❗ 错误码：无记录", EN: "❗ Error codes: no record"},
    # 2.0 澄清卡：下拉占位 + 输入框占位（1.0 卡不用这两条）
    "clarify.pick":       {ZH: "选一个（也可在下面直接输入）", EN: "Pick one (or type below)"},
    "clarify.pick_multi": {ZH: "可多选：点开勾选", EN: "Multi-select: tap to pick"},
    "clarify.other_hint": {ZH: "直接输入你的答案，回车提交", EN: "Type your answer, press Enter"},
    "clarify.multi_hint": {ZH: "可多选，回复编号用逗号隔开。", EN: "Multi-select — reply with numbers separated by commas."},
    # 卡链（R4）：封掉的那张卡尾部加一行，告诉用户回答还没完（下一条继续）
    "stream.continued":   {ZH: "（续下一条）", EN: "(continues in the next card)"},
    # 澄清点击的**瞬时提示**（toast）：失败态与「其他」提示态都**不动卡片** ——
    # 动卡会让一次迟到的重复点击把「已确认」回退成「待答」，那是不可逆的用户可见错误。
    #
    # ⚠️ **文案里绝不能出现「请重试」**（R8 审计中-1/低-2）：`resolve_gateway_clarify` 返回
    # False 的条件**只有两条**（Hermes `tools/clarify_gateway.py:90-98`：entry 不存在，
    # 或 `entry.event` 已经 set）⇒ 走到这条提示时，这条澄清**已经**被处理掉或根本不存在，
    # 再点一次**永远不可能成功**（clarify_id 是每张卡现生成的，不会被重新登记）。
    # 而这句话最常见的触发者是「一次迟到的重复点击」—— 那一瞬间卡片**已经**被前一次点击
    # 换成了「已确认」，于是用户同时看到「卡片=已确认」+「toast=请重试」两条互相矛盾的信息，
    # 合理的反应是再点一次（再吃一条同样的错话），或者把答案**用文字再发一遍** ——
    # 后者会变成一条新的用户消息进会话，而 agent 那边其实早就收到答案了。
    "clarify.toast_failed": {ZH: "这条澄清已被处理或已过期，无需重复点击",
                             EN: "Already handled or expired — no need to tap again"},
    # 「没被接受」**不能**与上面那条合并成一句（低-2）：这里只有 `REJECTED_*` 是
    # 「换个说法/换个选项还能救回来」的中间态，所以「重试」在这里才是真话。
    # **`NO_PENDING` 不许用这条** —— 那种情况重试永远不会成功，走 toast_no_pending。
    "clarify.toast_rejected": {ZH: "这个答案没有被接受，请换个说法或换个选项",
                               EN: "That answer was not accepted — try different wording or options"},
    # NO_PENDING（澄清已消失 / 核心那侧没有解析函数）：与 toast_failed 同一个事实，
    # 但入口不同（用户是在输入框里提交的），所以各留一条、都**不许**提「重试」。
    "clarify.toast_no_pending": {ZH: "这条澄清已被处理或已过期，无需重复提交",
                                 EN: "Already handled or expired — no need to submit again"},
    # 空提交（中-2）：2.0 卡的 `input` 组件 value 里**刻意没有** `answer` 键，
    # 于是「空着回车 / 只输空白 / 多选全取消」都落进 mode=="none" —— 以前这条分支
    # **无 toast、无换卡**，正是本项目头号失败模式（静默）。提示必须说清「应该做什么」。
    "clarify.toast_empty": {ZH: "没收到内容：请先选一个选项，或在输入框里输入答案",
                            EN: "Nothing received — pick an option or type an answer first"},
    # 老签名核心（`_card_response()` 不收卡片实参）上的**成功**那一击：不改卡，
    # 但点击**真的生效了**，必须有反馈 —— 否则用户「点了没反应」→ 再点一次
    # → 第二次必然 not committed → 吃一条误报。toast 不可能覆盖卡片状态，
    # 与「失败态绝不换卡」的纪律不冲突。
    "clarify.toast_submitted": {ZH: "已提交，卡片不会更新（当前版本不支持原地换卡）",
                                EN: "Submitted — the card won't update (inline swap unsupported here)"},
    # 「其他（我直接输入）」：2.0 卡上真有输入框，1.0 卡上**没有**（低-3）。
    # 在 1.0 卡上说「请在输入框里输入」是错话，所以按方言分两条。
    "clarify.toast_typing": {ZH: "请在输入框里输入答案（或直接回复文字）",
                             EN: "Type your answer in the box (or just reply with text)"},
    "clarify.toast_typing_text": {ZH: "请直接回复文字把你的答案告诉我",
                                  EN: "Just reply with text to give me your answer"},
    # /larkdeck 自检卡（R9）。三条状态行由 context.status_lines() 组装；没记录写「无记录」，
    # **绝不写「正常」** —— 一个永远说「正常」的自检与一个坏掉的自检，用户分辨不出来。
    "cmd.description":    {ZH: "larkdeck 状态：版本 / 生效传输 / 钩子 / 心跳",
                           EN: "larkdeck status: version / transport / hooks / heartbeats"},
    "cmd.header":         {ZH: "{name} · 传输 {transport} · 钩子 {wired}/{total} 已挂",
                           EN: "{name} · transport {transport} · hooks {wired}/{total} wired"},
    # ⚠️ 版本读不到时**必须看得出来**（R9 审计低-2）：以前版本段整段消失，卡片看起来跟一切正常一样。
    "cmd.version_unknown": {ZH: "版本读不到", EN: "version unreadable"},
    # ⚠️ 口径说明（R9 审计中-4）：三条记录是**进程级**全局，与页脚指标同源。
    # 不写清楚，多会话并发时用户会拿**别人会话**的失败原因来查自己的卡。
    "cmd.scope":          {ZH: "（下面的数字是**进程级累计**：含本进程上全部会话，不只你这一条对话）",
                           EN: "(the counters below are process-wide: every conversation in this process, not just yours)"},
    "cmd.help":           {ZH: "用法：/larkdeck [status|help]\n"
                               "· status（默认）：本卡 —— 版本 / 生效传输 / 钩子 / 六条记录\n"
                               "· help：这段说明\n"
                               "⚠️ 在飞书网关里，**生成回答期间**发的命令会被当成普通输入排队到回合结束"
                               "（命令派发只挂在核心的 idle 路径上）；CLI / TUI 里可以直接执行。",
                           EN: "Usage: /larkdeck [status|help]\n"
                               "· status (default): this card — version / active transport / hooks / six records\n"
                               "· help: this text\n"
                               "⚠️ In the Feishu gateway, a command sent **while a reply is streaming** is queued as plain "
                               "input until the turn ends (dispatch only runs on the core's idle path); in the CLI / TUI "
                               "it runs right away."},
    "cmd.unknown":        {ZH: "不认识的参数：{arg}（可用：status / help）",
                           EN: "Unknown argument: {arg} (available: status / help)"},
    "cmd.failed":         {ZH: "状态读取失败：{error}", EN: "Status read failed: {error}"},
    # 兜底里的兜底（R9 审计低-4）：连 `str(异常)` 都抛时用它，绝不把「读不出原因」装成成功。
    "cmd.failed_no_reason": {ZH: "（读不出失败原因）", EN: "(reason unreadable)"},
    "status.inbound":     {ZH: "入站心跳：{when} · 累计 {n} 条消息",
                           EN: "Inbound heartbeat: {when} · {n} messages"},
    # ⚠️ 口径（R9 审计中-5）：数的是「有多少帧**真的有东西写出去**」，不是 API 调用次数 ——
    #    cardkit 一帧最多 3 次逻辑写（装饰 batch + 正文 content + 限频的会话预览）、
    #    seed 帧是 2 次网络调用、一次逻辑写撞限流最多重发 4 次 HTTP，全都只 +1。
    #    写成「写卡：累计 N 次」会让人以为它数的是写动作（而 patch 车道恰好一比一，纯属巧合）。
    #    （正文里不加 `**`：文案走 `i18n.t()` 是**纯文本**，星号会原样显示给用户。）
    "status.frame_ok":    {ZH: "最近写卡：{when} · 累计 {n} 帧真的有写出（帧数，不是 API 调用次数）",
                           EN: "Last card write: {when} · {n} frames actually wrote (frames, not API calls)"},
    # ⚠️ 同样写死口径（R9 审计中-2）：「写卡失败」只统计**我们真的发起过写、而它失败了**的帧；
    #    不含「没有活跃流可收尾 ⇒ 按契约返回 False 交核心回落」这种**正常**返回
    #    （那时一个写请求都没发，核心的 edit/send 会把消息正常发出去）。
    "status.frame_fail":  {ZH: "最近写卡失败：{when} · 累计 {n} 次（只算我们发出且失败的写）· {reason}",
                           EN: "Last write failure: {when} · {n} (only writes we sent and that failed) · {reason}"},
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
