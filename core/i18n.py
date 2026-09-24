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
    # 2026-09-17 CLS 观感：统一面板里的**分区小标题**与折叠面板标题都用「思考/工具」两个
    # 不同的 emoji；工具状态由 `cards.status_style` 与 `cardview.status_style` 两表对等给
    # （v0.7.2 起：完成 `✓`(绿) / 运行中 `Running`(蓝) / 失败·拦截·超时仍是词）。
    # 这两个键也会被 `_ld_panel_summary` 复用为外层折叠标题（客户端收起时也能看到）。
    "panel.sec_thinking": {ZH: "💭 思考 · {elapsed}", EN: "💭 Thought · {elapsed}"},
    "panel.sec_thinking_plain": {ZH: "💭 思考", EN: "💭 Thought"},
    "panel.sec_tools": {ZH: "🛠️ 工具执行 · {n} 步", EN: "🛠️ Tools · {n} steps"},
    "panel.sec_tools_one": {ZH: "🛠️ 工具执行 · 1 步", EN: "🛠️ Tools · 1 step"},
    "panel.summary_both": {ZH: "💭 思考 {elapsed} · 🛠️ 工具执行 · {n} 步",
                           EN: "💭 Thought {elapsed} · 🛠️ Tools · {n} steps"},
    "panel.summary_both_one": {ZH: "💭 思考 {elapsed} · 🛠️ 工具执行 · 1 步",
                               EN: "💭 Thought {elapsed} · 🛠️ Tools · 1 step"},
    "panel.summary_both_plain": {ZH: "💭 思考 · 🛠️ 工具执行 · {n} 步",
                                 EN: "💭 Thought · 🛠️ Tools · {n} steps"},
    "panel.summary_both_plain_one": {ZH: "💭 思考 · 🛠️ 工具执行 · 1 步",
                                     EN: "💭 Thought · 🛠️ Tools · 1 step"},
    # 溢出保护：内容被截断 / 步骤被裁掉时补一行说明，让用户知道「还有东西但没显示」
    "panel.overflow":     {ZH: "…已省略 {n} 字符", EN: "…{n} chars omitted"},
    # 2026-09-24 用户口径：折叠提示自己不带前导省略号（它是提示，不是被截断的尾巴）。
    "panel.trimmed":      {ZH: "已折叠 {n} 条早期思考/工具记录", EN: "{n} earlier steps folded"},
    # 推理轮标题。注意「轮」= 一段连续推理（被正文或工具打断），不是 API 调用次数。
    "panel.round_n":      {ZH: "第 {n} 轮思考", EN: "Thinking round {n}"},
    # 轮数超过渲染上限时，更早的轮整轮折叠（保证面板总量有界，见 cards.unified_panel）
    "panel.rounds_trimmed": {ZH: "更早的 {n} 轮已折叠", EN: "{n} earlier rounds folded"},
    # 回合结局的文字兜底（面板没有别的正文时显示；边框色是主要载体）
    "stream.pending":     {ZH: "⏳ 正在生成…", EN: "⏳ Generating…"},
    # 文案逐字取自 aiduPOP `cardkit/i18n.py:35`（"Loading context..." / "正在加载上下文..."）
    "stream.loading_context": {ZH: "正在加载上下文...", EN: "Loading context..."},
    "card.status_processing": {ZH: "🫧 处理中…", EN: "🫧 Working…"},
    "panel.reasoning_round": {ZH: "第 {n} 轮思考", EN: "Thinking round {n}"},
    "panel.status_ok":    {ZH: "✅ 已完成", EN: "✅ Completed"},
    "panel.status_error": {ZH: "❌ 执行出错", EN: "❌ Failed"},
    "panel.status_stopped": {ZH: "⛔ 已中止", EN: "⛔ Stopped"},
    # clarify
    "clarify.header":     {ZH: "需要你确认", EN: "Needs your input"},
    "clarify.other":      {ZH: "其他（我直接输入）", EN: "Other (I'll type it)"},
    "clarify.hint":       {ZH: "点按钮，或直接回复文字都行。", EN: "Tap a button, or just reply with text."},
    # 2.0 澄清卡上**没有按钮** ⇒ 脚注不能再说「点按钮」（R11-C1）
    "clarify.hint_2":     {ZH: "点下拉选择，或直接回复文字都行。", EN: "Pick from the list, or just reply with text."},
    # R11-C2 / V079：记录区文案统一成「标记 + 标签：值」；标记由代码加，文案本身不带 emoji。
    # 零计数写「0 次」，无数据写「无记录」——两者不许混用（用户看到的每一行同一套语法）。
    "status.inbound":     {ZH: "入站心跳：{when} · 距上次 {age} · 累计 {n} 条消息",
                           EN: "Inbound heartbeat: {when} · last {age} ago · {n} messages"},
    "status.inbound_none": {ZH: "入站心跳：无记录（累计 0 条消息）",
                            EN: "Inbound heartbeat: no record (0 messages)"},
    "status.frame_ok":    {ZH: "最近写卡：{when} · 累计 {n} 帧",
                           EN: "Last card write: {when} · {n} frames"},
    "status.frame_ok_none": {ZH: "最近写卡：无记录（累计 0 帧）",
                             EN: "Last card write: no record (0 frames)"},
    "status.frame_fail":  {ZH: "最近写卡失败：{n} 次（最近 {when} · {reason}）",
                           EN: "Last write failure: {n} (last {when} · {reason})"},
    "status.frame_fail_none": {ZH: "最近写卡失败：0 次",
                               EN: "Last write failure: 0"},
    "status.uptime":      {ZH: "已运行：{v}", EN: "Uptime: {v}"},
    "status.fallback":    {ZH: "掉回纯文本：{n} 次（最近 {when} · {reason}）",
                           EN: "Plain-text fallbacks: {n} (last {when} · {reason})"},
    "status.fallback_none": {ZH: "掉回纯文本：0 次", EN: "Plain-text fallbacks: 0"},
    "status.codes":       {ZH: "错误码：{n} 次（{top}）", EN: "Error codes: {n} ({top})"},
    "status.codes_none":  {ZH: "错误码：0 次", EN: "Error codes: 0"},
    "status.note_frames": {ZH: "写卡按帧计数，不是 API 调用次数；不计「没有活跃流可收尾」这类无写出的返回。",
                           EN: "Card writes are counted per frame, not per API call; normal no-op returns are not counted."},
    # 2.0 澄清卡：下拉占位 + 输入框占位（1.0 卡不用这两条）
    "clarify.pick":       {ZH: "选一个（也可在下面直接输入）", EN: "Pick one (or type below)"},
    "clarify.pick_multi": {ZH: "可多选：点开勾选", EN: "Multi-select: tap to pick"},
    "clarify.other_hint": {ZH: "直接输入你的答案，回车提交", EN: "Type your answer, press Enter"},
    "clarify.multi_hint": {ZH: "勾选需要的选项，再点「提交选择」；也可以直接回复文字。",
                           EN: "Check the options, then tap “Submit”; you can also reply with text."},
    "clarify.submit":     {ZH: "提交", EN: "Submit"},
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
    # P1b：三条此前完全静默的失败路径（只有 WARNING，用户屏幕无变化）。
    "clarify.toast_missing_id": {ZH: "这条点击缺少澄清标识，无法处理；请重新发起澄清后再试",
                                 EN: "This click has no clarification id; start a new clarification and try again"},
    "clarify.toast_unauthorized": {ZH: "你没有权限操作这张卡片",
                                   EN: "You are not allowed to use this card"},
    "clarify.toast_unavailable": {ZH: "卡片插件暂时不可用，请稍后再试",
                                  EN: "The card plugin is temporarily unavailable; please retry later"},
    # 主卡片正文在 clarify 等待/已选阶段的状态文案。核心给的占位是
    # 「💬 等待你的选择...」，选完后如果不改写，主卡会永远停在那一行（用户截图反馈）。
    "clarify.main_waiting": {ZH: "⏳ 等待你的选择…", EN: "⏳ Waiting for your choice…"},
    "clarify.main_choice_received": {ZH: "✅ 已收到你的选择，正在继续…",
                                     EN: "✅ Choice received, continuing…"},
    "clarify.main_text_pending": {ZH: "⌨️ 已切换为文字输入，请直接回复你的答案…",
                                  EN: "⌨️ Switched to text input — reply with your answer…"},
    # /larkdeck 自检卡（R9）。三条状态行由 context.status_lines() 组装；没记录写「无记录」，
    # **绝不写「正常」** —— 一个永远说「正常」的自检与一个坏掉的自检，用户分辨不出来。
    "cmd.description":    {ZH: "larkdeck 状态 / 配置：默认运行概览，status --detail 看完整诊断",
                           EN: "larkdeck status / config: overview by default, status --detail for full diagnostics"},
    "cmd.header":         {ZH: "{name} · 传输 {transport} · 钩子 {wired}/{total} 已挂",
                           EN: "{name} · transport {transport} · hooks {wired}/{total} wired"},
    # V080：默认卡的首行只留版本与传输 —— 钩子数改到表格里带 ✅/⚠️（一眼能看出缺失）。
    "cmd.header_short":   {ZH: "{name} · 传输 {transport}",
                           EN: "{name} · transport {transport}"},
    # ⚠️ 版本读不到时**必须看得出来**（R9 审计低-2）：以前版本段整段消失，卡片看起来跟一切正常一样。
    "cmd.version_unknown": {ZH: "版本读不到", EN: "version unreadable"},
    # ⚠️ 口径说明（R9 审计中-4）：三条记录是**进程级**全局，与页脚指标同源。
    # 不写清楚，多会话并发时用户会拿**别人会话**的失败原因来查自己的卡。
    # V079：改成系统口径的正式表述（旧句「不只你这一条对话」太口语）。
    "cmd.scope":          {ZH: "统计口径：本卡数字为进程级累计，含本进程全部会话。",
                           EN: "Scope: all counters below are process-wide, covering every conversation in this process."},
    # V079：状态卡改为分节排版（列表 / 表格 / 颜色 / 分隔线），这几条是新的版式文案。
    "status.section_diag":    {ZH: "🩺 能力与链路", EN: "🩺 Capability & wiring"},
    "status.section_probe":   {ZH: "🔍 能力探测", EN: "🔍 Capability probe"},
    "status.section_records": {ZH: "📋 记录", EN: "📋 Records"},
    "status.col_item":        {ZH: "项目", EN: "Item"},
    "status.col_value":       {ZH: "值", EN: "Value"},
    # V080：默认状态卡只给**结论**（表格七行），排障细节写日志、`status --detail` 才展开。
    # 这几条是精简视图的行标签与页脚；值仍取自 `status_lines()` / 诊断行的原文（不另造读数）。
    "status.short_takeover":  {ZH: "平台接管", EN: "Platform takeover"},
    "status.short_hooks":     {ZH: "钩子", EN: "Hooks"},
    "status.short_reasoning": {ZH: "推理显示", EN: "Reasoning display"},
    "status.short_last_write": {ZH: "最近写卡", EN: "Last card write"},
    "status.short_failures":  {ZH: "写卡失败 / 掉回纯文本",
                               EN: "Write failures / plain-text fallbacks"},
    "status.short_codes":     {ZH: "错误码", EN: "Error codes"},
    "status.short_uptime":    {ZH: "已运行", EN: "Uptime"},
    "status.short_hint":      {ZH: "详细诊断已写入 `agent.log`；完整视图：`/larkdeck status --detail`",
                               EN: "Details are in `agent.log`; full view: `/larkdeck status --detail`"},
    "status.tip":             {ZH: "生成回答期间命令会排队到回合结束；配置用 `/larkdeck config` 查看。",
                               EN: "Commands are queued until the turn ends while a reply is streaming; see `/larkdeck config` for settings."},
    "cmd.help":           {ZH: "用法：/larkdeck [status|config|help]\n"
                               "· status（默认）：运行概览 —— 平台接管 / 推理显示 / 最近写卡与失败计数\n"
                               "· status --detail：完整诊断 —— 版本 / 生效传输 / 钩子 / 六条记录，"
                               "外加能力探测、适配器与契约细节\n"
                               "· config：只读查看本进程生效配置；config reload 从官方设置重读；"
                               "聊天侧没有写入命令（写配置用官方 Hermes CLI / 配置文件）\n"
                               "· help：这段说明\n"
                               "⚠️ 在飞书网关里，**生成回答期间**发的命令会被当成普通输入排队到回合结束"
                               "（命令派发只挂在核心的 idle 路径上）；CLI / TUI 里可以直接执行。",
                           EN: "Usage: /larkdeck [status|config|help]\n"
                               "· status (default): overview — takeover / reasoning display / last card write and failures\n"
                               "· status --detail: full diagnostics — version / active transport / hooks / six records, "
                               "plus capability probe, adapter and contract details\n"
                               "· config: read-only effective in-process settings; config reload re-reads official settings; "
                               "there is no chat-side write command (use official Hermes CLI / config file)\n"
                               "· help: this text\n"
                               "⚠️ In the Feishu gateway, a command sent **while a reply is streaming** is queued as plain "
                               "input until the turn ends (dispatch only runs on the core's idle path); in the CLI / TUI "
                               "it runs right away."},
    "cmd.unknown":        {ZH: "不认识的参数：{arg}（可用：status / config / help）",
                           EN: "Unknown argument: {arg} (available: status / config / help)"},
    "cmd.failed":         {ZH: "状态读取失败：{error}", EN: "Status read failed: {error}"},
    # 兜底里的兜底（R9 审计低-4）：连 `str(异常)` 都抛时用它，绝不把「读不出原因」装成成功。
    "cmd.failed_no_reason": {ZH: "（读不出失败原因）", EN: "(reason unreadable)"},
    # P1a：能力探测摘要（`/larkdeck status` 用）。纪律：「未探测」与「缺了哪些接口」必须能区分；
    # 没有结论时不许写「正常」。覆盖 `compat.PROBE_REPORT_KEYS` 的全部键。
    "probe.none":         {ZH: "能力探测：未探测（无记录）",
                           EN: "Capability probe: not probed (no record)"},
    "probe.short_none":   {ZH: "未探测", EN: "not probed"},
    "probe.runtime":      {ZH: "运行环境：Hermes {version}",
                           EN: "Runtime: Hermes {version}"},
    "probe.adapter":      {ZH: "适配器：{adapter}", EN: "Adapter: {adapter}"},
    "probe.session":      {ZH: "会话归属：{state}", EN: "Session attribution: {state}"},
    "probe.missing":      {ZH: "缺失接口：{summary}",
                           EN: "Missing interfaces: {summary}"},
    "probe.missing_none": {ZH: "无", EN: "none"},
    "probe.contract":     {ZH: "探测契约：{contract}", EN: "Probe contract: {contract}"},
    "probe.contract_ok":  {ZH: "完整", EN: "complete"},
    "probe.contract_bad": {ZH: "缺键 {keys}", EN: "missing keys {keys}"},
    "probe.covered":      {ZH: "已接管", EN: "adopted"},
    "probe.blocked_required": {ZH: "未接管（必需接口缺失）", EN: "not adopted (required interface missing)"},
    "probe.blocked_build": {ZH: "未接管（覆盖层构造失败）", EN: "not adopted (card layer construction failed)"},
    "probe.incomplete":   {ZH: "探测报告缺键", EN: "probe report incomplete"},
    "probe.session_ok":   {ZH: "就绪", EN: "ready"},
    "probe.session_bad":  {ZH: "未就绪（多会话可能串台）", EN: "not ready (multi-session may mix up)"},
    "probe.signal":       {ZH: "信号契约：{state}", EN: "Interrupt contract: {state}"},
    "probe.signal_ok":    {ZH: "静态查到核心查找名字面量（不替代运行期）",
                           EN: "static core lookup literal found (not runtime proof)"},
    "probe.signal_bad":   {ZH: "静态未命中（未必等于运行期一定坏；请复跑 check_override）",
                           EN: "static literal not found (may not mean runtime failure; rerun check_override)"},
    "probe.signal_unknown": {ZH: "未取证（核心源码不可读）", EN: "unverified (core source unreadable)"},
    "probe.unknown":      {ZH: "未知", EN: "unknown"},
    "probe.none_list":    {ZH: "无", EN: "none"},
    # V079：状态卡「能力与链路」拆成逐项 (标签: 值)，标记（✅/⚠️）由代码统一加；
    # 不再用「聚合（…）：a=b · c=d」这种一行塞四件事的写法。
    "diag.item_probe":    {ZH: "能力探测：{value}", EN: "Capability probe: {value}"},
    "diag.item_hooks":    {ZH: "钩子：{wired}/{total} 已挂", EN: "Hooks: {wired}/{total} wired"},
    "diag.item_command":  {ZH: "命令：{value}", EN: "Command: {value}"},
    "diag.item_generation": {ZH: "世代：{gen}/{latest}", EN: "Generation: {gen}/{latest}"},
    # 聚合渲染自身失效时**不许静默少行**（R9 低-2）：把失败原因放到卡上。
    "diag.failed":        {ZH: "🩺 聚合诊断渲染失败：{error}（其余状态行仍可用）",
                           EN: "🩺 Aggregate diagnosis failed to render: {error} (other status lines are still available)"},
    "diag.command_ok":    {ZH: "已注册", EN: "registered"},
    "diag.command_bad":   {ZH: "未注册", EN: "not registered"},
    # 推理显示解析结果（`show_reasoning=auto` 跟随 Hermes）。必须能说出「为什么关」：
    # display 开着但 stream_reasoning_deltas 关着时，用户看到的卡片不会有推理正文，
    # 这张状态行是唯一能区分「Hermes 没发数据」与「插件 bug」的地方。
    "diag.reasoning":     {ZH: "💭 推理显示：{state} · 模式={mode} · {detail}",
                           EN: "💭 Reasoning display: {state} · mode={mode} · {detail}"},
    "diag.reasoning_on":  {ZH: "开", EN: "on"},
    "diag.reasoning_off": {ZH: "关", EN: "off"},
    "diag.reasoning_detail_explicit": {ZH: "插件显式设置", EN: "explicit plugin setting"},
    "diag.reasoning_detail_hermes_off": {ZH: "Hermes display.show_reasoning 未开启",
                                         EN: "Hermes display.show_reasoning is off"},
    "diag.reasoning_detail_hermes_unreadable": {ZH: "读不到 Hermes display.show_reasoning，按关闭处理",
                                                EN: "Hermes display.show_reasoning unreadable; treated as off"},
    "diag.reasoning_detail_no_deltas": {ZH: "Hermes 未发送 reasoning delta（plugins.stream_reasoning_deltas 未开）",
                                        EN: "Hermes is not sending reasoning deltas (plugins.stream_reasoning_deltas is off)"},
    "diag.reasoning_detail_deltas_unreadable": {ZH: "读不到 Hermes 的 reasoning delta 开关，按关闭处理",
                                                EN: "Hermes reasoning-delta switch unreadable; treated as off"},
    "diag.reasoning_detail_hermes_on": {ZH: "跟随 Hermes display.show_reasoning",
                                        EN: "following Hermes display.show_reasoning"},
    # P2 `/larkdeck config`：只读视图 + `config reload` 热刷新；聊天侧没有写入命令
    # （安全审计 B1：handler 拿不到发送者身份，无法安全授权）。
    "config.header":      {ZH: "⚙️ 生效配置（来源优先级：环境变量 > 官方插件设置 > 默认值）：\n"
                               "（只读视图；用 `config reload` 重读官方设置，本命令不写任何文件）",
                           EN: "⚙️ Effective config (precedence: env > official plugin settings > defaults):\n"
                               "(read-only view; `config reload` re-reads official settings — this command writes no files)"},
    "config.no_reader":   {ZH: "ℹ️ 当前 Hermes 未提供 ctx.get_config()：只能显示本进程内存值 / 环境变量，无法核对官方设置。",
                           EN: "ℹ️ This Hermes does not provide ctx.get_config(): only in-process / env values can be shown; official settings cannot be checked."},
    "config.item":        {ZH: "· {name} = `{value}`（{source}{note}）",
                           EN: "· {name} = `{value}` ({source}{note})"},
    "config.source_env":  {ZH: "环境变量", EN: "env"},
    "config.source_official": {ZH: "官方插件设置", EN: "official plugin setting"},
    "config.source_default": {ZH: "默认值", EN: "default"},
    "config.needs_reload": {ZH: "⚠️ 官方文件已改，本进程仍是旧值，执行 `config reload` 生效",
                            EN: "⚠️ official file changed; this process still uses the old value — run `config reload`"},
    "config.pending_visual": {ZH: "⏳ 已登记，V1–V4 才生效（当前观感不变）",
                              EN: "⏳ registered; effective in V1–V4 (current rendering unchanged)"},
    "config.read_errors": {ZH: "⚠️ 以下键读取失败（未参与判断）：{keys}",
                           EN: "⚠️ failed to read these keys (not used for judgement): {keys}"},
    "config.none":        {ZH: "无", EN: "none"},
    "config.reload_ok":   {ZH: "✅ 配置已刷新：{n} 个键变化（{keys}）",
                           EN: "✅ Config reloaded: {n} key(s) changed ({keys})"},
    "config.reload_failed": {ZH: "❌ 配置刷新取消（以下键读取失败，内存保持原样）：{keys}",
                             EN: "❌ Config reload cancelled (failed keys; memory unchanged): {keys}"},
    "config.reload_no_reader": {ZH: "❌ 当前 Hermes 未提供 ctx.get_config()，无法刷新官方设置。",
                                EN: "❌ This Hermes does not provide ctx.get_config(); nothing to reload."},
    "config.reload_apply_failed": {ZH: "❌ 配置已读到，但应用失败（内存已回滚）：{error}",
                                   EN: "❌ Config read succeeded but apply failed (memory rolled back): {error}"},
    "config.read_only_set": {ZH: "🛑 聊天侧没有配置写入命令（插件层拿不到发送者身份，无法安全授权）。"
                                  "请用官方 Hermes CLI / 配置文件修改 `plugins.entries.larkdeck.settings`，"
                                  "再执行 `/larkdeck config reload` 热刷新。",
                              EN: "🛑 There is no chat-side config write command (the plugin layer has no sender "
                                  "identity, so writes cannot be authorized safely). Change "
                                  "`plugins.entries.larkdeck.settings` via the official Hermes CLI / config file, "
                                  "then run `/larkdeck config reload`."},
    "config.reload_env_shadowed": {ZH: "⚠️ 以下键仍被环境变量优先覆盖，reload 不会改变它们的本进程生效值：{keys}",
                                    EN: "⚠️ these keys are still shadowed by env vars; reload does not change their "
                                        "effective in-process values: {keys}"},
    "config.unknown_action": {ZH: "不认识的 config 子命令：{arg}（可用：show / reload）",
                              EN: "Unknown config subcommand: {arg} (available: show / reload)"},
    # V079：记录区的 status.* 文案统一在上方只定义一次；这里只留通用的「无记录」。
    # 口径备忘（不许丢）：`frame_ok` 数的是「真的有东西写出去的帧」，不是 API 调用次数
    # （cardkit 一帧最多 3 次逻辑写、seed 帧 2 次网络调用、限流重发全部只 +1）；
    # `frame_fail` 只算「我们发出且失败的写」，不含「没有活跃流可收尾 ⇒ 正常返回 False」。
    # 这两条口径现在写进上面 `status.note_frames`，由状态卡的记录区脚注展示。
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
