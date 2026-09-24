# LarkDeck

> 飞书 / Lark 流式卡片渲染器，给 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 用。
> 中文日常叫法：**卡组**。

一条回复从第一个 token 到最终答案，始终是**同一张卡片**，原地长出来。推理过程和工具调用收进底部一个可折叠面板，正文区只放回答。v0.7.0 起默认 `body_source: own`：正文只认插件从 `on_stream_delta(kind="text")` 累积的正文，core 帧只作刷新信号/finalize 兜底 ⇒ **工具进度行从结构上不会进入正文**；旧的「事后剥帧」路径（`legacy`）仅作过渡回退，见下面 ④。

---

## 它解决什么问题

Hermes 官方带的飞书适配器**只会发纯文本**，然后靠"编辑消息"来流式更新。所以：

- 没有卡片，没有折叠面板，没有按钮；
- 澄清提问（clarify）只能发文字，用户得手打选项；
- 想看推理过程 / 工具调用，只能被塞进正文里，正文被切得稀碎。

现在网上几个第三方方案（hermes-feishu-streaming-card、hermes-fry-cards、aiduPOP 等）能做出卡片，但代价是**改写 Hermes 源码**：往 `gateway/` 下 8 个核心文件里注入代码。Hermes 一升级，注入就被冲掉，得重装一遍；NAS 上还得额外挂一个"每次容器启动重新注入"的启动脚本。

LarkDeck 换了一条路：**不改源码，不 monkeypatch，升级不用重装**。

---

## 它是怎么做到不改源码的

三句话：

1. Hermes 的 `gateway/platform_registry.py` 对同名平台是**最后写入者胜**。LarkDeck 注册在官方 `feishu` 平台**之后**，于是被解析到的是 LarkDeck 的工厂。
2. LarkDeck 的工厂先问**官方工厂**要出真实的适配器类，再据此建一个子类
   `type("LarkDeckFeishuAdapter", (LarkDeckMixin, <官方类>), {})`，**直接构造它**。
3. 于是只需要覆盖四处：**发消息 / 改消息 / 澄清提问 / 卡片点击**。其余全部继承 ——
   鉴权、WebSocket、媒体、重试、限流都还是官方实现。

> 为什么不直接改 `instance.__class__`：CPython 除了要求内存布局一致，还要求新类的
> **直接基类链**一致。`(LarkDeckMixin, FeishuAdapter)` 的直接基类是 LarkDeckMixin 而非
> FeishuAdapter，所以即使布局数字完全一样，赋值依然抛
> `TypeError: __class__ assignment: object layout differs`。
> 直接构造子类没有这个约束，MRO 也更干净。

流式主路径是 Hermes 官方的 **native streaming 协议**：核心把整回合的正文与工具进度合成一条流、逐帧交给 `send_stream_frame()` 原地更新同一张卡 —— **一次回复只有一张卡**，工具调用不再另起消息。任何一帧失败，核心会自动回退到 **edit 传输**（`send()` 建卡 → `edit_message()` 连续更新 → `edit_message(finalize=True)` 封口，由 `REQUIRES_EDIT_FINALIZE = True` 驱动）—— 卡片永远只做增强，不会弄丢消息。

> 为什么不去 `import` 官方适配器模块：目录名、包名、加载方式都可能变。从注册表拿"上一个工厂"是唯一不依赖路径的接法。

---

## 卡片方言：1.0 与 2.0（改卡片前必读）

飞书卡片有两套互不兼容的写法，**混用不会报错，只会让按钮静默失灵**：

| | legacy 1.0 | schema 2.0 |
|---|---|---|
| 结构 | 顶层 `elements` | `config` / `header` / `body.elements` |
| 交互组件 | `{"tag": "action", "actions": [...]}` 按钮行，按钮**顶层** `value` | 组件**自己**带 `behaviors: [{"type":"callback","value":{...}}]`；组件可为 `button` / `select_static` / `multi_select_static` / `input` |
| 点击能到服务端吗 | ✅ 走 `p2.card.action.trigger` | ✅ **能**（组件级 `behaviors` 的 `value` 原样成为 `event.action.value`） |
| 流式（打字机） | ❌ | ✅ `streaming_mode` |
| 可折叠面板 | ❌ | ✅ `collapsible_panel` |
| `config.summary` | 不需要 | **流式时必带**，漏了通知栏空白 |
| `note` 小字脚注 | ✅ | ❌ **飞书已废弃** → 改用 `footnote()` |

所以本项目的分界是：

- **澄清交互卡**：待答卡与点击后的*回填卡*必须**同方言**（混用会让回填那一帧被飞书静默
  丢弃，表现为"点了没反应"）。当前默认 **2.0**（`select_static` / `multi_select_static` /
  `input` + 组件级 `behaviors`）—— **2026-09-13 翻的默认**，前置是真机上真点过
  （探针 ⑫⑬⑭ 的「探针点击到达」日志 + `check_clarify_e2e.py` 的 2.0 场景全绿）。
  1.0（按钮）仍然可用、仍有测试锁它的形状，配 `clarify_dialect: "1.0"` 回到旧路径。
  2.0 卡的**选项文本会以纯文本列出来**（与下拉**同源**、编号一致 ⇒ 不点开也知道有哪几个选项）；
  问题与选项都做 markdown 转义（按字面显示），脚注**按方言分流** —— 2.0 那张卡上没有按钮，
  就**不说**「点按钮」。
- **流式回复卡 → 2.0。** `streaming_mode` 和统一面板的 `collapsible_panel` 是 2.0 独有能力。

> **历史上这里写过「2.0 的回调到不了服务端」—— 那是错的**（2026-09-12 更正，
> 见 `AGENTS.md` 不变量 5）：当时那张「2.0 澄清卡」用的是**没有 `behaviors`** 的 1.0 按钮，
> 属于"2.0 卡里放 1.0 组件"，而不是「2.0 回调不可用」。
> `tests/test_units.py::test_clarify_card_must_be_legacy_dialect` 锁的是**当前默认实现
> 别被误改**，不代表 2.0 不可行。

### 元素级方言差异（实测，非文档推断）

飞书对卡片**不做静态校验** —— 不支持的字段是*发送时*才拒。所以下面这些只能靠真发一次卡才知道：

| 元素 | 1.0 | 2.0 | 依据 |
|---|---|---|---|
| `note` | ✅ | ❌ | 飞书返回 `230099 / ErrCode 200861 · cards of schema V2 no longer support this capability`；2.0 的小字脚注改用 `markdown` + `text_size: "notation"`（即 `footnote()`） |
| `action`（按钮行） | ✅ | ❌ | 1.0 的 `action` 容器嵌进 2.0 卡会被拒（`230099`）；2.0 里要用**组件级** `behaviors` |
| `collapsible_panel` | ❌ | ✅ | 2.0 专属 |
| 顶层文本元素 | `lark_md` / `plain_text` | 只认 `markdown` | 2.0 的 `body.elements` 里直接放 `lark_md` 或 `plain_text` 都会被拒（`ErrCode 200621`）；`plain_text` 只在嵌套位置有效（collapsible 标题、按钮 text） |
| `i18n_content` | ✅ | ✅ | 文本元素级字段，两个方言均实测接受；1.0 的 header title 与按钮 text 也接受且生效（2026-09-12 互换实验确证） |

**结论：本地单测只能验结构，验不了合法性。** 改完卡片必须跑 `tests/probe_render.py` ——
用真凭据把探针卡发到自己的飞书 DM，看飞书返回的 `code`（它会自动先删掉上次发的探针卡，
不留垃圾）。`note` / `action` 两条已在本地被 `test_dialect_element_exclusivity` 锁死。

---

## 功能

| 功能 | 状态 |
|---|---|
| 流式卡片（一回合一张卡，工具进度合入同卡） | ✅ 走 Hermes 官方 native streaming 协议；失败自动回落到内置发送 |
| 统一面板（推理 + 工具合并为一个可折叠底部面板） | ✅ 数据来自官方钩子（`on_stream_delta` / `pre_tool_call` / `post_tool_call`）；推理流需开启 Hermes 侧 `plugins.stream_reasoning_deltas` |
| 澄清交互卡（按钮点击直接作答，不再手打选项） | ✅ |
| 模型别名（可选：把 `deepseek-flash` 显示成你认得出来的名字；默认关闭） | ✅ |
| 双语 UI（跟随飞书客户端语言） | ✅ 真机确证（2026-09-12）：**走 `i18n_content` 的 plain_text / 面板摘要节点**按客户端语言切换。⚠️ 边界：markdown `element.content` 不承载 `i18n_content` —— 工具行动作词/状态词固定英文，分区小标题、footer 状态、等待期占位、轮标题、折叠/续卡提示固定中文默认，均不随客户端切换（完整清单见 AGENTS.md i18n 边界） |
| 即时响应：首帧早于首个 token（native seed 帧）+ 等待期占位 + 可关的「处理中」表情 | ✅ 真机实测 seed 帧建卡 `code=0`；等待期正文区显示「⏳ 正在生成…」（默认中文；markdown 无 `i18n_content`，英文客户端也显示这一份），收尾帧不带，避免空答案停在「正在生成…」；`reactions: false` 可关掉飞书那侧相当于「输入提示」的表情（**默认保持 Hermes 行为 = aiduPOP 的做法**，它的测试明确断言「reaction 拦截保持禁用」，见 `docs/plan-6-effects.md` §9） |
| **回合状态色**：完成绿边 / 报错红边 / 中止黄边 | ✅ 数据来自官方 `on_session_end`（每回合一次）；颜色画在面板边框上 |
| **推理按轮分段**（`第 N 轮 · 6.2s`；一轮 = 一段连续推理，被正文或工具打断） | ✅ |
| 过程面板（CLS 观感）：`💭 思考 1.6s · 🛠️ 工具执行 · 3 步`；工具行 = **飞书官方线性图标做文本前缀**（`markdown.icon`，统一灰 `color:"grey"`；2026-09-22 真机三臂对照选版：`div.icon` 实测图标高 3px、`column_set` 横向空 179px，前缀图标 0px）—— 同一批图标也用在**工具详情行 / 错误块标题 / 长回合折叠提示**；状态词为**加粗工具名** + 耗时 + 状态：运行中蓝色 `Running`（前缀图标是**自研动图** `img_v3_0215p_a0b0bd11…`，源文件 `assets/spinner-tool.gif`；借来的共享 key 只作**最后回落**）/ 成功绿色 `✓` / 红色 `Failed`·`Blocked`·`Timed out` / 灰色 `Cancelled`·`Skipped`，命令或 skill 名另起一行灰色小字；`tool_row_icon: "emoji"` 是 2026-09-21 那版 emoji 内联的**回退开关**（那条路不带 `custom_icon`） | ✅ 数据来自官方钩子；工具行动作词/状态词为**语言固定边界**（markdown 不承载 `i18n_content`）。**图标四条纪律**（2026-09-22 定版）：① 默认**线性 `_outlined` + 统一灰**（CLS 观感；彩色 `_colorful` 只有 13 个且颜色写死，不用）；② **token 必须逐个对飞书官方图标枚举页查证存在**（`enumerations-for-icons`；写错客户端不渲染且不报错）—— 白名单冻结在 `test_units.py::_VERIFIED_LINEAR_TOKENS`；③ `ICON_ALIASES`（28 条逐条等于 CLS）仍是**对齐判据的唯一真相**，`tool_icon_token(name, token)` 只是**渲染层精化**（60+ 真实工具名不再挤 14 个 token）；④ **字段表纪律**（同日真机 `200621` 的产物）：服务端对**未知字段**是**整卡被拒**（不是忽略，而且一次只报一个）—— `markdown` 没有 `text_color`（灰色只能写进 content：`<font color='grey'>…</font>`），`div` 的前缀 `icon` 在**组件级**；新增/改元素前先核官方 2.0 字段表并登记进 `check_cardview._assert_panel_element_fields()` 的白名单（未登记 tag 直接红）。✅ v0.6.2 默认 `panel_color_tags: true`（官方 Card 2.0 markdown 文档确认 `<font color>` 与色板）+ `text_profile: compact`（面板/页脚 12px notation、正文 normal）。⚠️ 真机视觉待用户目视确认（探针卡 `om_x100b64159f75b0a0c2f35ecdf3f0d36`）。⚠️ `panel_color_tags: false` 今天**只作用 legacy 文本函数**（`core/cards.py::_colorize`），结构化卡（v0.7.1 起唯一在跑的引擎）**无条件**写 `<font>` ⇒ 关它不会去色、反而可能把字面标签显示出来（v0.7.4 未做，v0.7.5 登记项，见 `docs/plan-v0.7.4.md` §5）。⚠️ **v0.7.3**：工具细节行（`markdown` / `plain_text` 两宿主）与 Error 块经 2026-09-23 真机目视确认 `x-small` **确实更小且可读**；Error 块的飞书 fenced 代码块字号被客户端固定死，改为 **`markdown` + 逐行 inline code + `x-small`**（用户选定形态③）后标签与每行代码一起变小。它是字面量，不随 `text_profile` 放大。见 `docs/verify-log.md`；**无运行时自动回退**，宿主行为变化需改代码并重跑全量。 |
| 页脚：状态 → 耗时 → 模型 → 上下文用量（`✅ 已完成 · 10.4s · … · ctx …`；v0.7.2 去段前缀 emoji；状态词 `✅`/`❌`/`⛔` 保留） | ✅ 数据来自官方钩子。**v0.7.2 起不再有 🔖 短码**（用户 2026-09-21 口径：那从来不是要求；同类插件页脚也都没有）——短码只保留在**日志自检行**（`卡片=<6 位>`），用户可见处一律不出现（`test_v4_17b` 整卡 + 出站载荷全量扫描）。**v0.7.3**：已知系统提示（Gateway online/restarting、Session database、Hermes update、cron/后台完成等）不再渲染面板/状态头/页脚；真实回合卡的状态词一个字不动。 |
| 上下文用量三样式（纯文字 / 图形条 / 数字+条） | ✅ 真机渲染已确认 |
| 推理文本 / 工具结果上限 + 元素溢出保护 | ✅ |
| **工具参数预览会脱敏** | ✅ 面板里的工具步骤行显示的是**参数预览**（截到 80 字符），其中明显是凭据的片段会被涂成 `***`：JSON 键值对（键名以 `token` / `secret` / `password` / `api_key` / `cookie` … **结尾**）、头部形态（`Cookie:` / `Authorization:` / `X-Api-Key:`）、裸 `Bearer <token>`、shell 风格 `KEY=value`；家目录折叠成 `~/…`。判据是**键名**而**不是猜值** —— 猜值会把 `max_tokens` / `token_count` 这类正常内容涂掉，那比不脱敏更难查。卡片会出现在群里，所以这是**安全**项、不是观感项。 |
| **cron / 无网关任务推卡片（P3）** | ✅ **代码级已验证；无网关 cron 真机投递待验**。通过官方 `PlatformEntry.standalone_sender_fn` 字段接管投递：cron / `send_message` 在**没有常驻网关**的进程里会先经我们的合并适配器走卡片层（失败 fail-open 到内置纯文本）；**带媒体附件的那一块投递回落内置 sender**（附件不丢；分块长文里非末块仍可能先走卡片路径，卡片硬异常会在末块附件前停下）。不 monkeypatch、不改核心，见 `docs/plugins-compare.md` §8 |
| **markdown 卫生**（写**完整文本**的每条路径）：游离的 `**` 删掉、H1–H3 降级为加粗 | ✅ 模型偶发漏一个 `**`（整段被未闭合加粗包住）时，卡片正文会露出一个**字面的 `**`**；`#` 标题在卡里会变成夸张大字。现在**每条写完整文本的路径**都做清理：**代码区之外的游离 `**` 删掉**（不补 —— 补 `**` 会把后半段整段吞进加粗；删的是**第一个**候选 —— 删最后一个会拆掉后文合法加粗对的闭合标记）、**H1–H3 降级为加粗**。覆盖 `send()` / 收尾整卡 / `/stop` 中止重绘 / native 收尾帧；**流式中间帧一个字节都不动**（必须严格是「累积全文的前缀链」，中途改写会让用户看到文字跳变）；代码区里一个字节都不动（围栏识别含**未闭合围栏**、**四反引号**、**`~~~`**）；卫生后**再量一次字节**，超上限就退回原文（不让「卫生把卡顶爆」）。|
| **自检与配置命令** `/larkdeck status\|config`（版本 / 生效传输 / 钩子 / **聚合诊断** / 六条记录；config 只读查看与热刷新） | ✅ 只报**有证据**的事：没记录就写「无记录」，绝不说「正常」；顶部两行聚合诊断把「能力 / 链路 / 运行 / 账本」压成两行，有明确异常才带 `⚠️`；口径四句话（写卡数的是**帧**不是 API 次数 / 每一次真的写卡都记 / 同一帧只记一次 / 「写卡失败」只算我们发起且失败的写）与「这些记录是进程级累计」都写在卡上。`config` 只读，`config reload` 从官方 `ctx.get_config()` 重读（异常全有全无）；**聊天侧没有写入命令**，写配置走官方 Hermes CLI / 配置文件。⚠️ **在飞书网关里**生成中敲的命令会被排队（CLI / TUI 里可直接执行）。见下面「自检」与「配置」两节 |
| **澄清点击反馈**：成功当场换卡 / 失败只弹提示 | ✅ 成功那一击走**内联换卡**（回调响应里直接带新卡，省一次 API 调用）；提交未生效时卡片**保持原样**并弹一条原生 **toast**（中英双语）—— 绝不换卡，理由见下面那段。**每一条收不到有效答案的路径都有提示**（失败 / `NO_PENDING` / 空提交 / 无法内联换卡时的成功），没有一条静默路径 |
| **超长回答自动分卡**（一张卡装不下就封卡、下一条接着写） | ✅ R4：单卡正文到硬上限的一半就**封卡 + 开新卡**，新卡**只显示剩下的那一段**（绝不重放前半段）；封掉那条带一行「（续下一条）」并**关掉流式态**；切点优先落在换行上、并**避开代码围栏**（切在围栏中间会让两张卡的 markdown 各自残缺）。装不下时仍然 fail-open 回落纯文本 —— 内容一个字节都不丢 |
| 打字机逐字显示 | ✅ **已实现，且是默认**：`native_transport: cardkit`（CardKit 实体 + `card_element.content` 逐帧写元素 = **真逐字**）—— 真机 + **用户肉眼**双重确认：普通卡 `message.patch` 只是「几个字几个字」地跳，CardKit 才是一个字一个字往外冒；`--cardkit-prod` 走**生产路径**实测建实体 1 次 + 发实体卡 1 次 + 元素写入 6 次 + patch 收尾 1 次全 `code=0`。翻默认的前置条件是「一轮对抗审计 + 真机生产路径探针」，两者都过了（`docs/plan-6-effects.md` 第十一路审计）。任何一步失败自动 fail-open 回落到 edit/send —— **宁可有一次没有动画的卡，也绝不丢消息**。想回到旧路径（字几个几个跳，但稳定）就配 `native_transport: patch`；自测用 `tests/probe_render.py --cardkit-prod` |

**澄清卡的三种状态**（R8）：**待答**（原卡）→ **已确认**（回调响应里内联换成已答复卡）→
**空提交也有提示**（2026-09-14 真机确认）：飞书客户端**不允许完全空提交**，所以这条路径的真机入口
是「只输空白字符再提交」—— 实测弹出红底 toast「没收到内容：请先选择一个选项，或在输入框里输入答案」，
**卡片保持原样**。**会话列表预览**同样已真机确认：该行会从 `Hermes` 变成回答开头（R7）。

**失败不再邀请重试**（卡片**不动**，弹一条 toast「这条澄清已被处理或已过期，无需重复点击」——
R8 收口时改的文案：再点**永远不可能成功**，旧文案既与「已确认」的卡片自相矛盾，还会把人推去
把答案用文字再发一遍。**2026-09-14 真机确认**：toast 真的弹出（**红底错误态**）、文案正是这一句、
卡片保持原样 ⇒ 「toast 是否真的弹出」这条留白已关闭）。
失败态为什么**不换卡**：这一条最常见的触发者是**一次迟到的重复点击**（点两下 / 网络重放），
那一瞬间卡片可能已经被前一次点击换成「已确认」—— 换卡会把用户的确认**退回待答**，
而答案其实早已送达 agent（不可逆的用户可见错误）。toast 能告知失败，又不可能覆盖卡片状态。
另一条纪律：**换卡能力要运行时探测** —— 核心那侧的 `_card_response` 若不接受卡片实参，
就自动退回「不改卡但点击仍然生效」，绝不抛（点击回调跑在 SDK 线程上，
抛一次会把「别人的卡」（审批卡等）的点击整条炸掉）。

**超长回答为什么会分成几条卡**（R4）：飞书对**单张卡**有硬上限（整卡 JSON 128000 字节、
递归元素 200 个，见「已知限制」），而 native 流式的帧文本是**累积全文** —— 正文涨到一张卡装不下时，
核心并不会替我们切分（源码注释写明「native streaming bypasses this: the adapter truncates against
the stream protocol's own limit」）。所以卡组自己切：**封掉当前这张**（整卡替换、关掉流式态、
正文末尾加「（续下一条）」）→ **建下一张**，只把剩下的那一段写进去。
⚠️ 两条已知取舍：① 切卡那一帧会多花写入配额（封卡 patch + 建实体 + 发实体卡 + 元素写 —— 低频事件，
说清代价见 `docs/plan-v1.md`）；② **只有最新那张**卡会跟着 `/stop` 变色（早前封掉的那几张保持原样）。

> 每项效果**验证到什么程度**（本地门禁 / 真机 API / 肉眼）见
> [`docs/plan-6-effects.md`](docs/plan-6-effects.md) 的「6 项效果的实施完成度」表 ——
> 那里区分了「API 说了算」与「只能人看」两类，别把前者当成后者已通过。

**界面文案双语**用的是飞书卡片原生的 `i18n_content`：服务端只发一份卡片，每个文本元素同时带中文和英文，客户端按自己的语言设置挑一份渲染。AI 生成的正文**不翻译**。

---

## 版本与升级

当前版本见 `plugin.yaml` 的 `version`（启动自检与页脚都能看到）。变更记录在
[`CHANGELOG.md`](CHANGELOG.md)，**默认值与行为的变化**在那里单列。

**首次安装**：见下一节。
**升级**：Mac 上是软链安装 ⇒ `git -C <仓库> pull` 之后 **`hermes gateway restart`**
（网关进程内加载的模块**不会热重载**，不重启就还在跑旧代码）。NAS 上是 `--copy` 安装 ⇒
拉取后重跑 `install.sh --copy` 再重启。
**回退**：`git checkout <tag>` 后重启。**已发布**：`v0.7.7`（澄清选择后主卡即时更新）、`v0.7.6`（Hermes 0.21.4 延迟平台兼容）、`v0.7.5`（长任务心跳进主卡面板 + seed 防闪旧）、`v0.7.4`（系统/命令回复静默 + 长任务面板修复）、`v0.7.3`、`v0.7.2`、`v0.7.1`、`v0.6.4` … `v0.6.0`、`v0.5.0`、`v0.4.0`、`v0.3.0`、`v0.2.0`，`v0.1.0` 是内部基线 tag（未建 Release）。

---

## 安装

```bash
git clone https://github.com/zayn-0101/larkdeck.git
cd larkdeck
./install.sh
```

然后把 `larkdeck` 加进 `~/.hermes/config.yaml`：

```yaml
plugins:
  enabled:
    - larkdeck
```

重启网关即可。启动日志里会有一行自检结果（下面是 0.21.1 实测原文）：

```
[larkdeck] 启动自检通过：Hermes 0.21.1 · feishu 平台已由 larkdeck 接管 · native 传输 cardkit · 钩子 post_api_request/on_stream_start/on_stream_delta/pre_tool_call/post_tool_call/pre_gateway_dispatch/on_session_end · /larkdeck 命令已注册
```

如果自检失败，会打 `ERROR` 并**保持官方适配器原样工作** —— 卡片不生效，但飞书不会被弄坏。

### 卸载

从 `plugins.enabled` 里删掉 `larkdeck`，再删 `~/.hermes/plugins/larkdeck/` 即可。没有任何源码被改过，所以不存在"卸载后残留注入"。

---

## 配置

在 `~/.hermes/config.yaml` 里：

```yaml
plugins:
  entries:
    larkdeck:
      settings:
        cards: true              # 用卡片渲染回复（关掉则完全退回官方纯文本行为）
        native_streaming: true   # 一回合一张卡（工具进度合入同卡）；关掉退回逐段新消息
        clarify_cards: true      # 澄清用交互卡
        clarify_dialect: "2.0"   # 澄清卡方言：2.0 下拉+输入框（默认，真机点击已验证）/ 1.0 按钮（旧路径，仍然可用）
        native_transport: cardkit # 流式帧传输：cardkit（默认，**真逐字打字机**；买它的代价见「已知限制」）/ patch ⚠️ **在 structured 引擎下无效**（自 v0.7.1 起唯一引擎是 structured，帧路径不看这个键；要回退请 revert 到 v0.7.0）
        tool_row_icon: "line"    # 工具行图标形态：line（默认，飞书官方**线性**图标做文本前缀——2026-09-22 真机三臂对照选版：0px 垂直偏差、统一灰色）/ emoji（2026-09-21 选的 emoji 内联，可切回）
        unified_panel: true      # 推理 + 工具合并为一个底部面板
        panel_expanded: false    # **收尾**时面板展开（默认收起：展开态很占屏）
        streaming_panel_expanded: true   # **运行中**面板展开（默认 true；流式中间帧不带 expanded ⇒ 你手动收起后不会被顶开）
        streaming_print_ms: 15   # 客户端打字机的逐字间隔（毫秒，只对流式帧有效）；0 = 关闭；超出 [1,2000] 退默认并留 WARNING
        reactions: true          # 在用户消息上打「处理中」表情（飞书的「输入提示」）；关掉更接近 aiduPOP 的观感
        footer_metrics: "off"    # 页脚附加指标：off（默认）/ basic（+ cache 命中率 + api 次数）/ full（再 + ttfb 首字节延迟）
        progress_lines_in_body: false  # 核心的工具行不进正文（仅 body_source: legacy 生效；own 模式结构上不读帧）
        body_source: "own"      # 默认 own：正文只认插件 on_stream_delta 累积；legacy 仅过渡回退（P2b 后删除）
        visual_engine: "structured" # v0.7.1 视觉引擎：structured（**默认**，结构化元素树）。`legacy` 配置键自 v0.7.1 起**已退役**（设了只留一条退休 WARNING，行为仍是 structured）；旧渲染器仍作为 DEGRADE 车道的降级渲染器保留。真正回退请 revert 到 v0.7.0
        card_status_header: false # v0.7.2：顶部状态条**默认关**（用户口径「顶栏默认不显示」）；置 true 可开回来（非默认值告警）
        show_reasoning: auto    # 是否显示推理正文：auto（默认，跟随 Hermes display.show_reasoning / 平台覆盖）/ on / off；旧布尔 true/false 仍接受。Hermes 未发送 reasoning delta（plugins.stream_reasoning_deltas 关闭）时按关闭处理，并在 /larkdeck status 说明原因
        footer: true             # 页脚：状态 → 耗时 → 模型 → 上下文用量（v0.7.2 起无段前缀 emoji/短码；v0.7.3 起已知系统提示整段不渲染）
        show_model: true         # 页脚里显示模型名（面板标题只放「💭 思考 / 🛠️ 工具执行」摘要）
        context_style: text      # 上下文用量样式：text | bar | both
        text_profile: "compact"  # CardKit 设备字号：compact/off/mobile_friendly/large；细节行与 Error 块（逐行 inline code）为字面量 x-small，不随档位放大
        theme: "ap_lite"         # 观感主题：neutral（原符号）/ ap_lite（默认，抽象 emoji）/ ap_bubble（AP 泡波风，含人物 emoji，可选）
        panel_color_tags: true   # 默认彩色（官方 Card 2.0 markdown 已确认 <font color>；真机视觉待截图）；不认则设 false 走纯文本降级
        model_aliases: ""        # "真名=显示名, 真名2=显示名2"；也可写 ~/.hermes/model_aliases.json（{"子串": "显示名"}，与 hermes-fry-cards 同源、改文件即生效）。都没有时页脚用 models.dev 真名（如 deepseek-flash ⇒ DeepSeek V4.1 Flash），查不到才格式化
        max_reasoning_chars: 1200   # 推理文本上限（超出截断并留痕；写 0 视为用默认值，不是不设限）
        max_tool_result_chars: 600  # 单条工具步骤行上限；面板显示的是参数预览，预览已被截到 80 字符，所以这项现实里几乎不会触发
        max_panel_steps: 30         # 面板最多保留多少步（超出保留最近的；写 0 视为用默认值）
        context_max_override: 0     # 非 0 时钉住上下文上限（探测不准时兜底）
```

键名、默认值与类型声明在 `plugin.yaml` 的 `config_schema` 里（写错类型 Hermes 会告警，
不会阻止加载）。插件在 `register()` 时经官方 `ctx.get_config()` 读入这些设置。

也可以用环境变量临时覆盖，如 `LARKDECK_CARDS=0`、`LARKDECK_CONTEXT_STYLE=bar`
（优先级：环境变量 > config.yaml 设置 > 默认值）。

### 查看与刷新配置：`/larkdeck config`

- `/larkdeck config`：**只读**列出每个键在**本进程**的生效值、来源（环境变量 / 官方插件设置 /
  默认值）。若官方文件改了而进程内存还是旧值，会显式提示执行 `config reload`。
- `/larkdeck config reload`：从官方 `ctx.get_config()` 重读全部键并替换内存配置；
  **任一键读取失败就整次取消**，不会留下半套新配置。⚠️ 这不是文件系统事务：官方
  `get_config` 每个键独立读盘，外部并发写入时可能读到混合快照；需要强一致时先停外部写入。
- **聊天侧没有配置写入命令**：插件层拿不到发送者身份（handler 只收原始参数），无法安全授权
  —— 安全审计后移除了 `config set`。请用官方 Hermes CLI / 配置文件修改
  `plugins.entries.larkdeck.settings`，再执行 `/larkdeck config reload` 热刷新；插件从不直接写
  `config.yaml`，也不调用 `ctx.set_config()`。

面板里的**推理过程**依赖 Hermes 侧开关（官方默认关闭）：

```yaml
plugins:
  stream_reasoning_deltas: true   # 允许插件订阅推理增量；不开就只有工具步骤
```

工具步骤不受这个开关影响。面板、状态色与页脚的数据全部来自官方钩子，不拦截核心代码。

面板标题行与上下文数字**来自官方钩子**，不是拦截核心源码。回合状态色来自
`on_session_end` —— 它虽然叫 session，实际**每回合触发一次**，载荷是官方的
`completed` / `failed` / `interrupted`。判定优先级是 `interrupted > failed > completed`
（官方 `completed` 的表达式不含 `interrupted`，先看它会把中止显示成绿色完成）。
原理、载荷键名与踩过的坑见 [`docs/metrics-and-hooks.md`](docs/metrics-and-hooks.md)。

**中止（`/stop`）那条路要特别说明**：中止后核心不会再有收尾帧（stream consumer 直接
放弃本回合、native 模式下也是空操作），所以插件覆盖了 `interrupt_session_activity`，
在被通知时**自己把那张卡重绘成黄边**，然后照常把中止交给内核。

---

## 自检：`/larkdeck status`

启动自检只证明「注册那一刻接管成功」，此后插件是死是活**没有别的证据** —— 而本插件的失败
形态大多是**静默**的（官方给钩子改名 ⇒ 页脚变空、帧失败 ⇒ 回答掉成一条条纯文本）。所以有一个
可以随时敲的自检命令：

```
/larkdeck          # 等同 /larkdeck status
/larkdeck config   # 查看本进程生效配置 / 来源；config reload 从官方设置重读
/larkdeck help     # 用法（含下面那条「生成期间会被排队」的提醒）
```

它的文本回复走我们自己的 `send()` ⇒ **自动成一张卡**：

```
🃏 larkdeck v0.6.4 · 传输 cardkit · 钩子 7/7 已挂
（下面的数字是进程级累计：含本进程上全部会话，不只你这一条对话）
🩺 聚合（能力/链路）：探测=已接管 · 钩子=7/7 · 命令=已注册 · 世代=1/1
📊 聚合（运行/账本）：入站=12s前 · 写卡=118 帧 · 写卡失败=0 · 掉回纯文本=0 · 错误码=0
能力探测：已接管 · Hermes 0.21.1 · 适配器 … · 会话归属 就绪
入站心跳：09-14 03:11:07 · 距上次 12s · 累计 42 条消息
最近写卡：09-14 03:11:09 · 累计 118 帧真的有写出（帧数，不是 API 调用次数）
最近写卡失败：无记录（累计 0 次）
⏱ 已运行：3h12m
🪂 掉回纯文本：无记录
❗ 错误码：无记录
```

顶部两行是 P2 的**聚合诊断**：把「插件有没有接管 / 钩子挂全没有 / 命令注册没有 /
模块世代有没有裂脑」和「入站心跳多久没动 / 写卡成功失败数 / 掉回纯文本 / 错误码」压成两行；
有**明确异常**（未接管 / 钩子不全 / 命令未注册 / 世代不一致 / 任一失败计数非零）时行首带 `⚠️`。
它**从不写「正常」「健康」** —— 没有异常不等于有证据说健康，用户扫一眼读数自己判断。

后三条是**排障用**的（用户看不见的东西）：`已运行` 区分「刚重启」与「跑很久了」；
`掉回纯文本` 是**本回合把卡片车道让给核心**的次数（与「写卡失败」同点同帧 +1，两者当前不可分辨 ——
以代码为准）；`错误码` 只数**非零**码、最多记 24 个不同的码（超出并进 `other`），
把「失败很多次」收敛成「失败在哪个码上」。

（示例里的数字是示意；版本那一段是**现读 `plugin.yaml`** 的，所以它跟着清单走 ——
上面写的 `v0.6.4` 对应 v0.6.4 清单；清单读不到时它会写「版本读不到」，
而不是把版本段**静默**去掉。）

* **入站心跳**：官方钩子 `pre_gateway_dispatch`（复用已有订阅，不新增）每收到一条入站消息
  记一次，**记在那个回调的第一行**（2026-09-14 修正：以前它排在归属绑定之后，
  于是 `bind_chat_session` 之类**兄弟模块**抛一次异常就把心跳一起吞掉 —— 实测「绑定抛 ⇒
  心跳 0」，而那正是判「消息到没到插件」的唯一凭据）。它是**与卡片无关**的证据，
  于是两种病可以分开：**心跳不动** ⇒ 消息没到插件的钩子回调（进程换了 / 平台名被别的插件
  抢走 / 在网关更早的关口就被挡下 —— 有四类消息在钩子**之前**就 return：internal 合成事件、
  profile 路由被拒、Slack 忽略频道、启动恢复期队列）；**心跳在动而「写卡」不动** ⇒ 消息到了、
  卡片却没发出去（配置关了 / 帧全失败）。反面也说清楚：**心跳在动 ≠ 这条消息会被处理**
  （未授权发送者 / 没有 user_id 的消息是在钩子**之后**被拒的，心跳照记）。
* **写卡（帧数）**：数的是「**有多少帧真的有东西写出去**」，**不是 API 调用次数** ——
  建卡（首发 / 建实体）、正文元素写、降级后的整卡替换、收尾替换、澄清卡、
  `/stop` 的中止重绘都算（每一次真的写卡都记，**同一帧只记一次**）。
  一帧里可能发生多次写：cardkit 一帧最多 3 次逻辑写（装饰 batch + 正文 content + 限频的
  会话预览），seed 帧是**两次网络调用**（建实体 + 发实体卡），一次逻辑写撞限流时同一请求
  最多重发 4 次 —— 这些**都只 +1**。反之，**节流跳过**的那一帧与**文本没变**的去重帧**不算**
  （它们一个字节都没写）。
* **写卡失败**：只统计「**我们真的发起过一次写、而它失败了**」的帧，原始原因截断到 120 字符、
  换行折叠。⚠️ 它**不等于**「核心收到过几个帧失败」：`send_stream_frame` 返回 `False` 还有一种
  是「没有活跃流可收尾 ⇒ 按契约把控制权交还核心」，核心随后用 edit/send 把消息正常发出去 ——
  那是**正常路径**，不进这个计数（否则一个健康回合会在卡上显示一条指向不存在写卡动作的原因）。
  日志里那条 WARNING 有 30 秒限流，而用户看不到日志 —— 「刚才那回合为什么掉成纯文本」
  只有这张卡答得出来。
* ⚠️ **没记录就写「无记录」，绝不写「正常」**：一张永远说健康的自检卡与一张坏掉的自检卡，
  在用户眼里长得一模一样（`docs/lessons.md` 推论 6）。同理，**读不到就说读不到**
  （版本读不到、状态读不出原因），绝不让一段信息静默消失。
* ⚠️ **这些记录是进程级累计**（与页脚指标同一件事）：`context._STATUS` 是模块级快照，
  **没有会话维度**。多会话并发（或一个进程服务多人）时，卡上的「累计 42 条消息 / 118 帧写卡」
  里可能大部分来自**别的会话**，「最近写卡失败：1 次 · <原因>」也可能是**别人**那次的失败 ——
  所以卡片上带了一句口径说明，排障时别拿别人的失败原因查自己的卡。要真按会话显示得从钩子载荷的
  `session_id` 分桶（与页脚同一件事，**未做**）。
* ⚠️ **在飞书网关里，生成回答期间敲的命令会被排队**：命令派发只挂在核心的 **idle 路径**上
  （`gateway/run_inbound.py` 的 `_hm_dispatch_idle_commands`），生成中敲的命令会被当成
  **普通输入**排队到回合结束才派发 —— 不是被忽略，但也不会立刻回卡。
  ⚠️ 这条**只对飞书网关成立**（2026-09-14 更正措辞）：**CLI / TUI 里可以直接执行**
  （那边由 `cli.py::_run_plugin_slash_command`、`tui_gateway/methods_tools.py` 直接调处理器，
  没有忙碌概念）。旧文案写成绝对断言「仅空闲态可用」，在客户端那侧是不成立的。
* 注册不到不影响卡片功能：老版本 Hermes 没有 `ctx.register_command()` 时，启动自检会**如实
  写着** `· /larkdeck 命令未注册（原因）`，而不是让你敲了没反应。这句话现在有门禁：
  `check_override.py` 拿**核心自己的命令注册表**（`get_plugin_command_handler`）与它对拍，
  「说已注册但其实没注册」会当场红。

---

## 测试

```bash
PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3   # 用 Hermes 自带解释器最贴近线上；系统 python3 也能跑（零第三方依赖）

$PY tests/test_units.py            # 纯单测，零网络零 Hermes 依赖
$PY tests/check_override.py        # 对真实 Hermes 安装验证平台覆盖 + 插件配置桥接
$PY tests/check_hooks.py           # 对真实钩子派发器验证指标采集是否接通
$PY tests/check_clarify_e2e.py     # 澄清卡端到端：发送 → 点击 → 网关解除阻塞
$PY tests/mutate_check.py          # 变异验证器：撤掉每条修复必须变红（改了断言必跑）
$PY tests/probe_render.py          # 真发卡片到自己的飞书 DM，验飞书接不接受（要凭据）
```

前四个是**门禁**（必须全绿）；`mutate_check.py` 是**验证门禁自己有没有判别力**的元门禁：
清单里每条变异 = 一处「把某条修复撤掉」的定向改动，判定标准是**至少一个门禁变红**。
**撤掉修复还全绿 = 那条断言没有判别力** —— 这是本项目头号缺陷类型（见 `docs/lessons.md`
推论 6~8）。它的由来：第七路审计拿 50 条定向变异各跑一遍，**23 条撤掉修复后门禁仍然全绿**。
`-k <子串>` 只跑一部分；快照目录必须叫 `larkdeck`，否则会 import 到未变异的基线（曾导致假绿）。

`check_hooks.py` 走的是核心真正使用的派发器（`hermes_cli.lifecycle.invoke_hook`），
载荷用 Hermes 自己的 `CanonicalUsage` 生成，并带一组**对照组**（不启用插件时钩子必须为空）。
它专门盯住一个不报错的坑：插件加载器装在 `hermes_plugins.larkdeck.core.*` 命名空间下，
测试里若用 `import larkdeck.core.context` 会拿到**第二个模块对象**，读写状态对不上。

`check_clarify_e2e.py` 用的是从平台注册表里取出来的**真内置适配器类**，只把最底层
`_feishu_send_with_retry` 换成捕获器，**不连飞书、不发网络请求**。它能抓到桩类抓不到
的问题：签名不匹配、私有原语改名、覆盖没生效、返回类型不对。

`probe_render.py` 是唯一**连真实飞书**的测试，也是唯一能判定卡片合法性的手段 ——
它会先删掉自己上次发的探针卡再发新的。只验渲染，**不验点击**（点击路由要平台被
LarkDeck 接管后才生效）。带参数的几种模式各答一个只有真机能答的问题：`--typing`
（打字机 A/B 肉眼比）、`--bytes`（字节上限阶梯，create + patch 都打）、`--elements`
（元素数阶梯，官方硬上限 200）、`--rate-limit`（连续 patch 的限流实证）、
`--stop-redraw`（**中止重绘的真机端到端**：正文超降载预算但发得出去时，`/stop` 必须真的
把那张卡重绘成中止色 —— 它用真适配器跑生产路径，**两条路径都跑**（非 native 的 `send`+patch、
以及真 native 流式 `send_stream_frame` 建卡→若干帧→`/stop`），断言**载荷里有颜色**
而不只是「发了 patch」）、
`--button-2`（**只发一张** 2.0 按钮探针卡：`button` + **组件级** `behaviors`。`AGENTS.md` 不变量 5 里「组件可以是 `button`」这句**只有官方文档**支撑 —— 真机点过的只有 `select_static` / `multi_select_static` / `input`。发一张、点一下、看网关日志（**本机实测在 `~/.hermes/logs/agent.log`** —— `gateway.log` 里只有内置 feishu 平台的行，插件的 `[larkdeck]` 行在 `agent.log`）里有没有 `探针点击到达 ✅ tag=button` 就行；单独一条命令是为了不用在一批探针卡里找那一张）、
`--clean-only` / `--no-clean`（清理控制）。

---

### 升级安全性：实测过一次真实的上游改动

Hermes **已经改过一次**内置飞书适配器的加载方式：它现在把内置适配器当**插件**加载
（`FeishuAdapter` 的模块名从 `plugins.platforms.feishu.adapter` 变成
`hermes_plugins.feishu_platform.adapter`）。我们**没有改一行代码、也没有重装**，
接管照旧成立 —— 因为基类是从平台注册表**运行时取**的，不是硬编码导入：

```
LarkDeckFeishuAdapter → LarkDeckMixin → FeishuAdapter → BasePlatformAdapter
   （我们）              （我们）        hermes_plugins.feishu_platform.adapter（上游）
启动自检通过：Hermes 0.21.1 · feishu 平台已由 larkdeck 接管
```

这条性质由 `tests/check_override.py` 守着（它用**真插件加载器**跑一遍再问注册表
「现在 feishu 解析到谁」）—— 上游再怎么挪文件，它都会在启动那一刻告诉我们有没有接上。

## 已知限制

- **更新流的 fenced 代码块（``` 开头）故意不登记为系统提示**：真实答案也常以代码块开头，按内容前缀
  会误杀真终稿；若 P4 真机日志显示它在飞书成为可见噪声，再单独设计（metadata 或专用前缀）。
- **系统提示判定 = 默认回合 + 已知前缀负清单**（v0.7.3 Design D）：只有来源可枚举的已知提示（Gateway online/restarting、Session database、Hermes update、cron/后台任务、Goal 等，见 `core/adapter.py::_LD_SYSTEM_NOTICE_PREFIXES`）整卡不出面板/状态头/页脚；**未登记的新系统提示仍按回合卡渲染出 `✅ 已完成`**。发布前用 `send 判定 turn=` 日志复核**非原生/通知/命令车道**（真实回合走原生 CardKit streaming、不经过 `adapter.send()`，不会产生该日志；它的 `✅ 已完成` 由真机目视确认）；发现漏网先登记前缀再重跑全量变异。**v0.7.4 起**：Hermes 本地化系统/命令回复（`/reset`、`/new`、`/reload-*`、`/stop`、
  `/reasoning`、title/footer/model/approve-deny 等）与 `/larkdeck` 自诊卡命中登记 header 时
  整卡静默；`notify=True` 不能单独判非回合（真实 non-native 终稿也带它）。**未登记的命令回执
  仍可能带 `✅ 已完成`**，见下方已知限制。
- **长任务/多卡中间卡面板空白**（v0.7.4 修复）：终局帧快照无过程数据时不再渲染空 shell，状态色由页脚承载；真机长任务场景若仍出现空白，按 v0.7.5 专项复现处理。
- **上游 `⏳ Working — …` 长任务心跳进主卡面板**（v0.7.5）：默认模式下每 180s 的心跳
  合入当前结构化主卡的 `collapsible_panel` 标题（`⏳ Working … · 原摘要`），返回空
  `message_id` 让上游不进入 `edit_message`；finalize 终卡会清掉 Working。无 active 主卡时
  只建**一张**专用静默卡并复用；多条 active、degraded、`unified_panel: false`、刚 finalize
  的安静窗口内一律抑制。**已知限制**：① 只识别默认字面量 `⏳ Working — `，
  `long_running_notifications: generic` 的任意文案仍走 v0.7.4 静默卡；② 无 active 主卡时
  的那张专用 Working 卡在真实终稿后不会被自动删除/合并（保留在时间线，行为有回归测试）；
  ③ 面板闸门只覆盖 seed 后的 3s tick 与上游心跳，首个 live 帧抢在 `on_stream_start` 前
  到达的毫秒级竞态仍属已知残余（详见下方「已知限制」条目）。
- **追问不再先闪上一回合工具步骤**（v0.7.5）：新 consumer 回合 seed 帧不再读上一回合
  panel 快照，只建空面板壳；seed 后、`on_stream_start` 清空前，3s 面板 tick 与上游心跳
  也被闸门挡住；`on_stream_start` 后的首帧起画当前回合数据。
- **不同入站回合各自一张卡**：v0.7.5 明确不做跨回合答案卡合并/抑制。后台进程完成通知
  （`Watch pattern notification`）等自动注入也是独立新回合，合并会吞掉它的真实回答；
  同轮 native finalize 失败 / boundary 多卡需上游 `_stream_turn_id` /
  `get_stream_message_id` 接口后再评估。
- **Hermes 本地化系统/命令回复仍有未登记项可能带 `✅ 已完成`（v0.7.4 起覆盖已登记 header）**：`/reset`、`/new` 等命令的
  最终回复在上游同样带 `notify=True`，v0.7.3 的已知前缀清单未覆盖这些本地化命令头；
  用户 2026-09-23 截图确认 `/reset` 回复仍显示页脚（根因与登记见 `docs/verify-log.md`、
  `docs/plan-v0.7.4.md`）。**真实回合卡不受影响**；发现新命令回执先登记 header 并重跑全量。
- **必须和官方适配器同一进程**：官方 `feishu` 平台被禁用时，LarkDeck 无处附着。
- **依赖官方适配器的内部方法**：发送/编辑路径 3 个必需（`_feishu_send_with_retry` 等，启动自检校验，缺了**拒绝覆盖**并保持内置行为）+ 1 个可选（`edit_message`，有则用、无则退回内置）；点击回调路径 5 个类属性 + 2 个实例属性（`_on_card_action_trigger`、`_card_response`、`_client` 等）；信号型 1 个（`interrupt_session_activity`，缺了「中止后卡片不变色」）；处理生命周期 1 个（`_reactions_enabled`，缺了 `reactions: false` 静默失效）；澄清网关内部结构（`_lock` / `_entries` / `entry.multi_select` / `mark_awaiting_text` / `resolve_gateway_clarify`）。全部集中登记在 `compat.py`（分七组 + 会话归属公开面）：`probe_adapter_class()` 守必需项，`probe_report()` 的完整快照在启动时打进日志（缺点击回调会提级 WARNING 并写明「澄清按钮会静默失灵」）—— 官方哪天改了名字，日志会直接说出来，而不是静默失效。
- ~~**`i18n_content` 的元素级支持需真机确认**~~ → **已实测确认**（2026-09-12）：
  文本元素同时带 `content` 与 `i18n_content`，1.0 与 2.0 卡均被飞书接受；
  1.0 的 header title 与按钮 text 也接受且生效。**互换实验**（把 `zh_cn` 分支里放英文）
  在中文客户端上显示出英文，证明客户端确实按 `i18n_content` 选语言，而不是永远读默认值。
  兜底仍然安全：客户端不认时回落到 `content`，不会让卡片发不出去。
  **注意 AI 生成的正文不翻译**，双语只覆盖界面文案。
- 下一步要做什么（分阶段方案，已经过三路审计）：[`docs/plan-v1.md`](docs/plan-v1.md)
- 与同类插件的横向对比（六家、含各自特色与我们可借鉴的部分、ROI 排序）：[`docs/plugins-compare.md`](docs/plugins-compare.md)
- 与 hermes-feishu-streaming-card（HFC）**不能共存**：两边都要接管 `feishu` 平台，且 HFC 还改了源码。切换步骤见 [`docs/switch-from-hfc.md`](docs/switch-from-hfc.md)。
- **页脚数据是进程内全局的**：钩子记录的是「最近一次 API 请求」，多会话并发时所有卡片共享同一份快照。单用户单会话无影响；真要按会话隔离，得从钩子载荷里的 `session_id` 分桶，目前没做。
- **面板归属**：钩子载荷只有 `session_id`、没有 chat_id。归属靠 `pre_gateway_dispatch` 观察到的
  `chat_id -> session_id` 映射（确定性），**拿不到映射时才退回「最近活跃会话」**。
  退回的窗口是**新会话的第一回合**（那一刻 session 还没落库）、会话映射过期（24h）、以及
  老版本 Hermes；这些窗口里多会话并发可能短暂显示另一个会话的推理/工具（正文不受影响）。
- **打字机的动画本身没有自证手段**：`streaming_print_ms`（默认 15）会把
  `streaming_config` 带在**流式帧**上（飞书接受它，实测 create/patch 都是 `code=0`），
  但「客户端会不会逐字打」是纯客户端行为，**API 返回码看不到**。而且有三条证据倾向于
  「`im.v1.message.patch` 拿不到这个动画」：官方文档没说清是哪种写入 API、**Hermes 上游
  两个 PR 都把这个效果绑在 CardKit 流式接口上**、同类项目 lark-hls-v2 的注释也要求
  「第一次推送必须用 `card_element.content`」。**这件事最后是用户肉眼定的案**（甲几个字几个字
  跳、乙一个字一个字冒），所以默认传输已经翻成 `cardkit`：`streaming_print_ms` 在 cardkit 下
  是 no-op（见下一条），而 `patch` 路径上的它只是「无害但没用」。自测方法：
  `tests/probe_render.py --typing`（两张卡交替长大 12 秒，一眼看得出哪张在逐字）、
  `--cardkit-prod`（走**生产代码路径**验整链）。
  **另一条传输的实现依据**（2026-09-13）：`tests/probe_render.py --cardkit` 把
  `cardkit.v1.card.create`（建卡片实体）→ `im.v1.message.create`
  （`{"type":"card","data":{"card_id":…}}`）→ `cardkit.v1.card_element.content`
  （按 `sequence` 逐帧写元素）→ `cardkit.v1.card.settings`（`streaming_mode: false` 收尾）
  整条链跑通，**每一步都是 `code=0`**；SDK 就位、Hermes 自身完全没用 CardKit（不冲突）。
  它同时发一张**公平的对照卡**（同样节奏、同样切分，但走 `message.patch` + `streaming_config`），
  两张并排留在 DM 里 —— 哪张逐字、哪张整段跳，一眼就能定「打字机是否需要 CardKit 实体」。
  在那之前不实现它：这是本项目「先量，不猜」的规矩，也是当初把它列为「确证后再付的复杂度」的原因。
  **现在它已经实现并且是默认传输**：设计边界与真机结果写在
  `docs/plan-6-effects.md` 的「阶段 9 实施记录」里（只换 native 流式帧的传输、
  任何一步失败都 fail-open 回落、翻默认前先过对抗审计 + 真机生产路径探针）。
  取值超出 `[1, 2000]` 毫秒会被退回默认 15ms，并在日志里留一条限流 WARNING
  （写错配置不会静默 —— 「想要最慢」却得到「最快」是必须能查出来的）。
- **`native_transport: patch` 本身在 `structured` 引擎下是 no-op**（自 v0.7.1 起唯一引擎是
  structured，帧路径不读这个键）—— 想要真回退请 revert 到 v0.7.0；登记 v0.7.3 决定是恢复真回退
  还是删掉这个键。
- **`cardkit` 传输下还有两个配置是 no-op**：`streaming_print_ms`（打字机由
  `card_element.content` 带来，实体卡不带 `streaming_config`，拧它没有任何效果）与
  `panel_expanded`（**收尾整卡**的展开态在建卡那一刻定死，改配置要等下一次建卡才生效；
  运行中的展开态由 `streaming_panel_expanded` 决定，见上一条）。
  `unified_panel: false` 在两条传输下都关得掉面板。
- **两条 expanded 配置各管一头**（v0.7.2 定版）：`streaming_panel_expanded`（默认 true）管
  **运行中**那一次建卡实体，`panel_expanded`（默认 false）管**收尾**整卡。流式**中间帧**
  （`panel_partial`）**永远不带 `expanded`** —— 带了就等于每帧重放建卡时的展开态，用户手动
  收起的面板会被下一个 token 顶开。嵌套推理轮按「当前轮展开 / 已结束轮折叠」渲染。
- **`cardkit` 传输的取舍**：它换来真正的逐字打字机，代价是三条硬约束 ——
  ① 卡片**结构在建实体时定死**（流式期间只按 `element_id` 写内容；**整卡替换**
  `message.patch`/`card.update` 会关闭流式会话。⚠️ 更正：CardKit 的**元素级/批量**接口
  `card_element.patch` / `card_element.create` / `card.batch_update` 实测在流式期间**可用**
  且不关会话，只是我们当前实现还没用它们）；R3 收窄版起，折叠面板在 cardkit 模式下是**两个**
  markdown 子元素 —— `panel_body`（推理轮）+ `panel_tools`（工具行列表）。**两块都在建实体时
  建好、之后只改内容**（不做流式期间的结构性写入），拆开的收益是**不再让两块互相带着重发**：
  两条与时钟无关的不变量 —— ① **工具结束那一帧不重发推理块**；② **推理增长那一帧不重发工具块**
  （推理逐字在长时，那几十行工具摘要不再跟着每帧发；量级 ≈2.7KB/帧）。⚠️ **工具开始那一帧
  通常两块都写**：工具会打断当前推理轮，而轮次标题要补上耗时（`**第 1 轮**` → `**第 1 轮 · 0.4s**`）
  ⇒ 推理块的内容**真的变了**（2026-09-14 审计指出旧措辞把这条说成了「工具事件一律只写工具块」，
  那个说法只在工具结束帧成立）。观感与「一个 markdown」一致；
  ② 面板边框的**状态色在收尾那一帧**才上（那时流式
  本来也结束），所以流式期间是灰边、结束才变绿/黄/红；③ 序号必须单调递增。
  失败的分档（2026-09-13 R5 起，逐条真机实测过）：**建实体失败 / 拿不到 SDK / 超硬上限 /
  正文写入拿到确定性拒收码** ⇒ 这一帧返回 `False`，核心随即**停用本回合的 native 流式**、
  改用普通发送/编辑（用户看到的仍然是一张卡，只是不再逐字；再不行才回落官方纯文本）；
  **元素通道拿到卡级死法**（`300309` 会话已关 / `300313` 元素不存在 / `300317` 序号冲突）
  ⇒ **降级成整卡 `message.patch` 续写同一张卡**（下面那条），不掉纯文本也不多出第二张卡；
  **消息被撤回/删除**（`230011`）⇒ 这一帧失败、插件不再往那条消息写（是否重新送达由核心决定，
  所以 DM 里**可能出现**一条新消息 —— 那是核心的回落，不是插件在补发）。
  **消息永远不会因为卡片出错而丢。**
  ⚠️ 注意「回落」的准确含义：它**不是**「自动换成 `patch` 传输继续流式」，而是掉出 native
  流式这条路（排查「卡片怎么变成一条条消息了」的人容易在这里被带偏）。
- **cardkit 下流式期间实时更新的东西与不更新的东西**（2026-09-13 更新）：
  ① **页脚**（`footer: true` 的上下文用量）**现在流式期间就会更新** —— 建实体时就把页脚元素
  建进卡里，之后每帧随正文一起刷新（`footer: false` 时不建、也一次都不写它的 id）；
  ② 与之同理，面板内容（推理轮 / 工具步骤）整个回合都在更新；
  ③ **面板标题**现在是 CLS 观感的 `💭 思考 1.6s · 🛠️ 工具执行 · 3 步`；模型名与回合耗时
  在**页脚**（流式期间也会更新）。CardKit 实体卡的外层折叠面板（`collapsible_panel`）
  的 `header.title` 建卡时定死，收尾 / 降级 /
  `/stop` 的整卡替换才把它换成这个双语摘要 —— 流式期间展开面板，正文里也有同样的
  `💭 思考` / `🛠️ 工具执行` 分区小标题与灰色细节行；
  ④ **核心的工具行（`⚙️ terminal: "…"`）不进正文**。v0.7.0 起默认 `body_source: own`：
  正文只认插件从 `on_stream_delta(kind="text")` 累积的正文；native 帧文本只当刷新信号、
  finalize 兜底（core 非空整段采用，core 是 own 精确后缀时保留 own 前段，**绝不按分隔符切片**）。
  ⇒ 工具进度行**从结构上**不在正文渲染输入里；面板里仍是结构化的一行（耗时 / 参数预览 / 状态色）。
  **`legacy` 回退**（仅过渡）：保留旧的有证据剥帧路径；那条路在工具执行初期仍可能短暂看到进度行，
  但回答开始后会被清除。`progress_lines_in_body: true` 只在 legacy 下生效。
  ⚠️ F4 异常帧（core 帧失败后同文 finalize 重发）在 own 下会拒绝持久化并交回 core edit/send 回落，
  避免合成帧里的 terminal 命令/参数进正文；legacy 下保持旧行为。旧路径的 `正文剥进度=N`
  自检只在 legacy 出现，P2b 归档后删除。
  ⑤ 页脚**可以**再带上三个指标（`footer_metrics`，**默认 off**，不影响现有观感）：
  `basic` = 缓存命中率 `cache 75%` + 本回合 API 次数 `api 7`；`full` 再加首字节延迟 `ttfb 0.4s`。
  三个数都是**载荷直接算出来的**；缺数据就少一段，**绝不编 0**（「不知道」与「真的 0%」是两件事）。
  成本**不做**：`post_api_request` 的载荷里没有成本字段，只能估算，而估算值放进卡片是误导；
  ⑥ **会话列表预览会随回合进展更新**（R7）：建卡时它的 `config.summary` 是 `Hermes`（卡标题
  兜底 —— ⚠️ 不是「⏳ 正在生成…」，那是**正文**占位），之后**每 ≥5 秒**在文字真的变了时更新
  一次（`card.settings`，占一个序号、算一次额外逻辑写，靠限频摊薄）；写失败**或抛异常**都只
  标死 + 留一条 WARNING，**不会**影响卡片内容，也不会让这一帧失败。
- **建实体时同时守两道墙**：整卡 JSON 超过飞书硬上限（128000 字节）或**递归**元素数超过 200
  （真机实测：递归 200 收下、204 拒收，码 `300305`），当场放弃 CardKit 这一帧、回落给核心 ——
  因为结构建实体时就定死，超了就是整张卡被拒（不像 `patch` 路径那样能分级丢掉面板）。
- **每帧的**元素写**预算是 2 次**（装饰一次 `card.batch_update` + 正文一次
  `card_element.content`；R7 起还会多一次**会话预览** `card.settings`，受 5 秒窗口限频）：
  卡级写入上限按官方口径 10 次/秒，而帧节流窗口是 0.25s ⇒ 元素写每帧最多 2 次。装饰
  （面板 + 页脚）合并成**一次** batch；**正文最后写**（提交点在后，与核心「按最后一次成功帧
  记账」同口径）。装饰**内容没变就不发那次 batch**（面板/页脚往往好几帧不动）⇒ 稳态下每帧
  只写 1 次。
  按 0.25s 帧窗口折算：元素写 ≤2×4 = 8 次/秒，加预览（5 秒窗口 ⇒ 平均 ≈0.2 次/秒）
  ≈ **8.2 逻辑写/秒 < 卡级上限 10 次/秒**。⚠️ 不能写成「3 次/帧 × 4 帧/秒 = 12 次/秒」——
  预览那一份被 5 秒窗口卡住，不可能每帧都发。
  ⚠️ 这是**逻辑写**口径，**不是**「每帧最多 3 次 HTTP 调用」：元素写撞限流时同一个请求会退避
  重发（0.1/0.3/0.6，最多 4 次尝试，`uuid` 不变所以幂等）⇒ 单帧最坏 = 2 次可重试的逻辑写
  × 4 + 预览那 1 次（**它不重试**）= **9 次调用、退避睡眠 2.0 秒**（2026-09-14 更正：
  这里以前写「12 次 / 约 3 秒」，那是把预览也按 4 次尝试算的 —— 而预览走 `retry=False`，
  永远只发一次；R4 的切卡帧另算，见下）。
  另有一条**滑窗写入守卫**（1 秒窗口 / 上限 10 次逻辑写）：窗口满了就把这一帧的**装饰**
  （面板/页脚）让出去，下一帧补写（**正文永远不跳** —— 它是提交点，跳了用户就永远看不到那一段）。
  它是给「将来元素会动态增长」预留的余量闸门，**稳态下不会触发**（摊还 ≈8.2 次/秒 < 10；
  注意正文与预览不查这个闸门，所以 1 秒窗口内理论峰是 12 条，不是「任何一秒都 ≤10」）；
  真触发时会留一条 WARNING，且被让出的装饰**不会被记账**（否则会被去重逻辑永久跳过）。
  之所以选「重试」而不是「早失败」：这一帧失败会让核心停用本回合的流式卡片，退化成纯文本。
  （**预览那一路不重试**：它排在正文写之后、失败只标死 ⇒ 重试只剩白等最多 ≈1 秒。）
  装饰写失败**不会**让整帧失败（标死 + 留 WARNING，卡片继续逐字）；只有正文失败才 fail-open。
  卡级死法（`300309` 会话已关 / `300313` 元素不存在 / `300317` 序号冲突）**不会**让你掉回纯文本：
  插件会**降级成整卡 `message.patch`**，继续更新**同一张卡**（不再逐字，但卡片与内容都在，
  也不会多出第二张卡）—— 装饰批量与正文写入**两条通道**撞上卡级死法都会触发降级。
  唯一的例外是 `300313` 落在**装饰**上：那只是某一个装饰元素在卡里没了（服务端会在 msg 里
  **点名它**），正文元素是另一个 id、照样写得进去 ⇒ 只标死被点名的那个、**保住打字机**。
  装饰失败的标死粒度：**服务端点名的 id 确实是这一批里的元素** ⇒ 只标死那一个；否则**整批标死**
  （msg 形状不止一种：点名上下文、id 被截断都会让「按名字标死」标错人，所以宁可保守）。
  代价是「本回合内装饰冻结」，但**收尾帧的整卡替换会一次补齐**（面板 / 页脚 / 状态色）。
- **面板的更新时机由核心决定**：Hermes 自己就跳过「文本没变」的中间帧
  （`gateway/stream_consumer_transport.py`：`if not finalize and text == self._last_sent_text`），
  所以**纯推理阶段（正文还是空的）根本不会有帧**，面板只能在正文开始增长之后才更新。
  长回答前的长时间思考期间，卡片会停在「⏳ 正在生成…」+ 首帧那一刻的面板状态 ——
  这是核心的帧策略，不是插件的取舍（我们无法凭空让核心多发帧）。
- **状态色的可信范围**：颜色来自 `on_session_end`（每回合一次，含非流式路径）。
  `/stop` 那一帧由插件自己重绘（核心不会再有收尾帧）。若某回合连一次流式帧都没有
  （没建卡），自然也没有卡可上色 —— 那时看到的仍是官方纯文本。
  正文超过字节预算（40000）时，状态色由 `cards.status_shell()` 保住（只带边框色的小面板，
  ≈545 字节，见上一条）；只有正文贴近飞书硬上限（128000）时才会为了「发得出去」放弃它。
  真机自测：`tests/probe_render.py --stop-redraw`。
- **CardKit 实体卡顶层 header 不建**（决策 D2）；被建卡定死的是**外层折叠面板的
  `header.title`**，收尾整卡替换时才换成摘要。过程信息收进统一面板，摘要行是
  `💭 思考 1.6s · 🛠️ 工具执行 · 3 步`；模型名与回合耗时在**页脚**。
  面板被关（`unified_panel: false`）或正文超过字节预算（40000）时，**这些信息会消失，
  但状态色不会** —— 超预算档会保留一个只带边框色的小面板（`cards.status_shell`）。
  这条是 2026-09-13 真机实测补出来的：早先那一档把面板整块摘掉，于是「正文发得出去、
  卡片也在，但 `/stop` 之后不变色」（载荷里连颜色都没有）。代价约 545 字节；
  正文大到贴近飞书硬上限（128000）时才会放弃这个色块，那时优先保发送成功。
  正文永远完整（见上面的降载策略）。真机自测：`tests/probe_render.py --stop-redraw`。

---

## 许可

MIT
