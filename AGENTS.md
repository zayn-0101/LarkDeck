# AGENTS.md — larkdeck

> Hermes Agent 的飞书流式卡片插件。中文日常叫法「卡组」。
> 使用者文档在 `README.md`；踩坑记录与开工索引在 `docs/lessons.md`；原理与部署在 `docs/`。
> 全局人格与安全红线见 `~/.hermes/SOUL.md`；本文件只加项目内规则。

## 不变量（改动前必读）

1. **绝不修改 Hermes 源码**，绝不 monkeypatch。所有能力通过公开契约拿：
   `ctx.register_platform()` 覆盖内置 `feishu` 平台 + 子类化官方适配器
   （`_discover_base_class()` 从注册表拿上一个工厂产出的官方类，再经 `merged_class()` 混入
   `LarkDeckMixin`）。任何"顺手 patch 一下"都是设计错误，不是权宜之计。
2. **卡片是增强，不是替代。** 任何卡片路径失败都必须回落到 `super()` 的官方实现。
   宁可退回纯文本，也不能因为卡片报错而丢消息。改 `adapter.py` 时逐条保住这个性质。
   native streaming（`SUPPORTS_NATIVE_STREAMING` + `send_stream_frame`）的帧失败由核心
   自动回退 edit/send —— 这条 fail-open 链是官方契约，别绕过、别在帧里吞掉回落。
3. **Hermes 私有接口只允许出现在 `compat.py`。** 目前分八组登记
   （会话归属与澄清网关另见 `SESSION_ATTRIBUTION_API` 与「约定」里的归属条目）：
   适配器必需 3 个（`REQUIRED_ADAPTER_ATTRS`，`probe_adapter_class()` 运行时校验，
   缺了拒绝覆盖）；适配器可选 1 个（`OPTIONAL_ADAPTER_ATTRS` —— `edit_message`，
   有则用、无则退回内置）；**显示 chrome 1 个**（`DISPLAY_CHROME_ATTRS` —— `format_tool_event`：
   我们**覆盖它并允许返回 `None`**（基类 docstring 明写的官方扩展点）。
   ⚠️ **2026-09-14 实测更正：这条覆盖在 0.21.1 里根本不在这条路上** ——
   `format_tool_event` 的唯一调用点是 `gateway/stream_dispatch.py`，而那个
   `GatewayEventDispatcher` **只在测试里被构造**（全树 grep：生产侧零引用）。
   真机上的工具进度行由 `gateway/run_turn_runner.py` 的 `_progress_build_message` 生成，
   在 native 流式下由 `gateway/stream_consumer.py` 的
   `"\n\n---\n".join((accumulated, progress))` **合成进同一帧**。
   所以这条覆盖只是**向前兼容的保险**（哪天核心把它接回来就自动生效），
   **不是**「正文干净」的活杠杆 —— 后者是 R11-A7 的正文净化（下一节）。
   探测照旧上报：缺了不致命，但那时连保险也没了）；
   点击回调路径 5 个类属性（`CALLBACK_ADAPTER_ATTRS`）+ 2 个实例属性
   （`CALLBACK_INSTANCE_ATTRS` —— ⚠️ **2026-09-16 实测更正**：这组**根本没有探测键**
   （`probe_report` 不读它，全仓只有 `compat.py:50` 的定义、一条测试、和本行），
   且 `_loop` 在 `adapter.py:3562` 是**裸访问、无局部回落** ——
   旧文「只在运行时 `AttributeError` 时回落」**对 `_loop` 不成立**）；
   **信号型契约 1 个**（`SIGNAL_ADAPTER_ATTRS` ——
   核心在 `/stop`、`/new` 路径**主动调我们**的 `interrupt_session_activity`，
   缺了不致命但「中止后卡片不变色」是静默失灵 —— ⚠️ **2026-09-16 补充：探的是基类有没有它，
   而真正的契约是核心的查找名（`gateway/run_agent_cache.py:415` 的
   `getattr(type(adapter), "interrupt_session_activity", None)`）—— 那个名字改了，合并类属性
   探测结构上看不见。P1b 已加**静态 best-effort 源码字面量探测**（status「信号契约」行 +
   False 时 WARNING），但运行期派发仍未由它验证；核心等价重构/局部变量改名会误报 False，
   残余盲区仍在 `docs/plugins-compare.md` §7.6 如实登记**）；**处理生命周期 1 个**
   （`REACTION_ADAPTER_ATTRS` —— `_reactions_enabled`，覆盖它必须**尊重父类**语义，
   缺了 `reactions: false` 静默失效）；澄清网关内部结构
   （`_lock` / `_entries` / `entry.multi_select` / `mark_awaiting_text` / `resolve_gateway_clarify`，
   已封装成 `clarify_multi_select()` 等函数）；会话归属公开面
   （`SESSION_ATTRIBUTION_API`）。订阅的钩子清单也在本文件（`OBSERVED_HOOKS`，
   文档/门禁/自检都读它，同步规矩见「约定」）。新增依赖一律先登记。
   🔴 **2026-09-16 独立复核：本条里的「探测上报」比实际强得多，别按字面读。**
   已核实（完整清单见 `docs/plugins-compare.md` §7.6）：
   * **历史核对（P1a 之前）**：探测结论的**唯一出口是被动日志**（`~/.hermes/logs/agent.log`）；
     启动自检写的 `SELFCHECK` 在**生产代码里没有任何读者**（读者只有 `tests/check_override.py`）；
     `/larkdeck status` 卡上**一个 `probe_report` 键都没有**（当时只有 6 个账本计数 + 「钩子 N/7」）。
   * ⇒ 当时上面每一处「探测上报」，实际含义都是「**往日志里写一行**」，**不是**「用户能问出来」；
     唯一真正上到用户可见渠道的是 `OBSERVED_HOOKS`（状态卡显示 `钩子 N/7`）。
   * 已登记 **7 条静默路径（S1–S7）**，其中 S7（`interrupt_session_activity` 的**核心查找名**）
     原先连日志都没有。**P1b 已加静态 best-effort 源码字面量探测 + `/larkdeck status`
     「信号契约」行 + False 时 WARNING；但运行期派发仍未由它验证，残余盲区如实保留**。
     修复项与优先级见 §7.8 —— **登记不等于开工**。
   * ✅ **2026-09-16 P1a 更新（已实现并过门禁）**：`probe_report` 的 10 个契约键
     已按「状态 / 缺失 / 探测契约」三行摘要上到 `/larkdeck status`；`build_adapter()` 存
     进程级只读快照，状态卡区分「未探测」「已接管」「必需接口缺失」「覆盖层构造失败」
     「报告缺键」，且**从不写「正常」**。S1（`session_attribution_ok` 算了不报）随之上卡解决；
     S2（`CALLBACK_INSTANCE_ATTRS` 无探测键）、S7（核心查找名）与 S6 仍不在本批。
4. **不假设版本。** Mac 与 NAS 都跑 Hermes 0.21.1（NAS 是镜像内固定版本），升级随时会发生。
   能力一律运行时探测，不写死版本号分支。
5. **卡片方言不可混用 —— 但「2.0 的回调到不了服务端」是错的，2026-09-12 更正。**
   正确规则：**要接服务端点击的组件，必须在它所属的方言里用对应的声明**。
   - **1.0**：`{"tag":"action","actions":[...]}` 按钮行 + 按钮**顶层** `value`；
   - **2.0**：**组件级** `behaviors: [{"type":"callback","value":{...}}]` —— `value` 会原样成为
     `event.action.value`；组件可以是 `button`、`select_static`（回调带 `action.option`）、
     `input`（回调带 `action.input_value`）。
   在 2.0 卡里放 1.0 的 `action` 行、或只有顶层 `value` 的按钮 → 飞书**拒收**（IM API `230099`）。
   混用后果是**静默失灵**（点了没反应），所以这条纪律照旧要守。

   ✅ **四格现在全都真机证过了（2026-09-16 16:12 补齐最后一格）**：
   `select_static` / `multi_select_static` / `input` 由探针 ⑫⑬⑭ 的「探针点击到达」日志
   ＋ `check_clarify_e2e.py` 全绿证明；**`button`** 这一格由探针 ⑮ 补上 ——
   真机点击后 `agent.log` 出现
   `[larkdeck] 探针点击到达 ✅ tag=button … value={'kind': 'button', 'larkdeck_probe': True}`
   ⇒ **「2.0 卡里组件级 `behaviors` 的 `button` 能把点击送到服务端」现在是实测事实**
   （在那之前它**只有官方文档**）。判定协议见 `docs/handoff-route.md` §12（**点击前**写死的）。
   ⚠️ 这段措辞本身有历史，别把升格当成理所当然：R11 做同类插件对照时发现它写得**比手上的
   证据更强**，于是按证据强度**降级**并留了探针 ⑮ 去补 —— **补完才升回来**。
   这条纪律的通用形态（**结论要标证据强度**：谁量的、怎么量的；
   「官方文档写了」≠「真机验过」；**别人的代码注释不算证据**）见 `docs/lessons.md` 推论 38。

   > **旧结论错在哪（别再重蹈）**：原表述是「2.0 的 `behaviors` 回调到不了
   > `p2.card.action.trigger`，所以澄清卡只能是 1.0」。这是**误诊** —— 当时那张「2.0 澄清卡」
   > 实际用的是**没有 `behaviors`、只有顶层 `value` 的 1.0 按钮**放进 2.0 的 `body.elements`，
   > 那是「2.0 卡里放 1.0 组件」这个病；而且论据抄自第三方插件的**代码注释**，不是真机实验
   > （该插件自己的代码还与那句注释自相矛盾）。
   > **认证据的规矩：官方文档 + 真机探针为准，不抄别人注释。**
   > 2.0 澄清卡（`select_static` + `input`）是可行的，见 `docs/plan-6-effects.md` 阶段 4。

   澄清卡现在**默认用 2.0**（`select_static` / `multi_select_static` / `input` + 组件级
   `behaviors`）—— **2026-09-13 翻的，两条前提都满足并留了一手证据**：
   ① 真机点一次：探针 ⑫（2.0 `select_static`）点下去后网关日志（**本机实测在 `~/.hermes/logs/agent.log`** —— `gateway.log` 里只有内置 feishu 平台的行，插件的 `[larkdeck]` 行在 `agent.log`）出现
   `[larkdeck] 探针点击到达 ✅ tag=select_static option='opt_a'`；探针 ⑬⑭（真 2.0 澄清卡）
   点下去后出现 `澄清提交未生效（clarify=probe-c2）` —— 探针卡没在网关登记澄清，所以
   「没东西可解」是预期，而它证明**点击到达并正确解析出了 clarify id**；
   ② `check_clarify_e2e.py` 的 2.0 场景全绿。
   想回到旧路径就配 `clarify_dialect: "1.0"`（那条路径仍然可用、仍有测试锁它的形状，
   `check_clarify_e2e.py` 的每条场景现在**自己声明方言**，不再偷偷依赖默认值）。
   `cards.clarify_card_2()` / `clarify_card()` 就是两个版本，由配置选。
   多选走 `multi_select_static` 且**不给**自由输入框（与编号/标签解析打架）。
   元素级方言差异见 `README.md` 的表。真正的红线是**方言不许混用**：
   1.0 的 `action` 行放进 2.0 卡会被飞书拒（`230099`），2.0 组件不带 `behaviors` 则点击到不了服务端。

## 目录

```
plugin.yaml   插件清单（kind: platform，含 requires_env 与 config_schema 声明）
__init__.py   插件入口：只从 core.adapter 转发 register
core/         插件本体（Hermes 加载器以 hermes_plugins.larkdeck.core.* 命名空间加载）
  adapter.py    覆盖层：LarkDeckMixin（含 native streaming 契约）+ merged_class() + build_adapter() + register() + 启动自检
  cards.py      卡片 JSON 构造（纯函数、无 I/O）—— 两种方言的边界在这里；
                也放 CardKit 实体卡与面板 markdown 的构造（`cardkit_entity_card` /
                `panel_rounds_markdown` + `panel_tools_markdown`（R3 收窄版：面板两块）/
                `panel_markdown`（普通卡与 `patch` 传输：两块拼成一个 markdown））
  i18n.py       双语文案（飞书原生 i18n_content）
  compat.py     版本 / 能力探测 —— Hermes 私有名的唯一存放处
  context.py    运行时指标（钩子写入 → 页脚读取的进程内全局快照）；
                R9 起还持有**自检账本**（入站心跳 / 写卡 / 写卡失败，R11-C2 起再加运行时长 /
                掉回纯文本 / 错误码 top-N），由 `/larkdeck status` 读
  panel.py      面板数据层（推理轮 / 工具 / 回合结局写入 → 卡片面板读取；
                按会话分桶 + chat_id→session_id 确定性归属，拿不到才退回「最近活跃」）；
                R11-A7 起还持有**正文累积**（单独的小仓库 + 自己的「最近活跃」盒子，
                只服务正文净化的前缀判据，**不参与**面板的回合切换与归属回退）
  hooks.py      官方钩子订阅（7 个观察型钩子，清单见 compat.OBSERVED_HOOKS）：
                只写内存、异常自吞、永不返回 directive
install.sh    安装脚本（默认软链；NAS 用 --copy，其 FILES 数组是手动的，新增模块要同步）
docs/         踩坑与开工索引（lessons）、指标钩子原理、部署与迁移步骤、
              同类插件横向对比与 ROI（plugins-compare）、
              当前分阶段方案（plan-v1，已过三路审计）、
              **交接单 handoff-route（新会话从这里开始：§13 当前坐标 + 待办队列 + 提交纪律）**、
              全量变异验证的留档账本（verify-log）
tests/        见「验证」
```

## 约定

- `LarkDeckMixin` 的方法用 `_ld_` 前缀；卡片追踪状态挂在 `self._ld_state`。
- 卡片按钮 value 一律带 `larkdeck_action` 键，由重写的 `_on_card_action_trigger` 拦截；
  不带这个键的点击必须原样交给 `super()`。
- **工具参数预览必须脱敏**（`panel.redact_inline_secrets`：JSON 键值 / 头部形态 / 裸 `Bearer` /
  `KEY=value` **五条**规则 + 家目录折叠成 `~`；`_REDACT_HEADER_RE` 是审计 A2 补的第五条）。
  判据是**键名以凭据词结尾**，**不是猜值** ——
  猜值会把 `max_tokens` / `token_count` / `MAX_TOKENS` 这类正常内容涂掉，而那比不脱敏更难查
  （用户看到一个像被打过码的**正常**参数，会以为工具收到了别的东西）。
  卡片会出现在群里 ⇒ 这是**安全**项：以后任何新增的展示字段都要先过一遍这条判据。
  ⚠️ 值类必须吃**转义**（`_args_preview` 喂进来的是 `json.dumps` 的产物）：旧值类 `[^\"]*`
  会在转义引号处**提前收尾**，只涂前半截、后半截照旧露在卡上（**半截凭据同样是凭据**，
  而且看起来像处理过了）。四个形状各有一条变异守着：`G1-11..G1-14`。
- 界面文案只从 `i18n.t()` / `i18n.i18n_text()` 取，不硬编码中文字符串。
  i18n 只覆盖界面文案，AI 生成的正文不翻译。
  例外边界（2026-09-17 明确）：`markdown` element.content 不承载 `i18n_content`；
  工具行动作词/状态词固定英文（CLS 风格）；面板分区小标题、footer 状态、等待期占位
  「⏳ 正在生成…」、轮标题「第 N 轮」、`…更早的 N 轮/步已折叠`、`（续下一条）`
  均为固定中文默认，不得被声明成“随客户端语言切换”。**只有走 `i18n_content` 的
  plain_text / 面板摘要节点才跟随客户端语言。**
- 插件配置路径是 `plugins.entries.larkdeck.settings.<key>`，由 `register()` 里的
  `_apply_ctx_settings()` 经官方 `ctx.get_config()` 读入。Hermes **从不**调用
  `configure()`（它只是自有运行时入口，单测在用）。取值优先级：环境变量
  `LARKDECK_<KEY>` > config.yaml settings > `_DEFAULTS`。新增配置项必须同时加到
  `_DEFAULTS` 和 `plugin.yaml` 的 `config_schema`；未接入业务的配置项要在 README 标 no-op。
  P2 起配置有**热刷新**通道：`/larkdeck config` 只读展示，`/larkdeck config reload` 从官方
  `ctx.get_config()` 重读进内存（**异常时全有全无**，不是文件系统事务）。聊天侧**没有**写入
  命令：handler 拿不到发送者身份，无法安全授权（安全审计 B1）⇒ 插件**从不**直接写
  `config.yaml`、也不调用 `ctx.set_config()`；写配置走官方 Hermes CLI / 配置文件，再 reload。
  官方 ctx 的**只读**句柄存进程级共享盒子 `adapter.PLUGIN_CTX`（命令可能来自旧世代模块对象）。
- 视觉三键的**现役默认值**（2026-09-21 v0.7.2 起）：`visual_engine="structured"`（`legacy` 配置键
  **已退役**：设了只留一条 WARNING，行为仍是 structured；真正回退要 revert 到 v0.7.0）、
  `card_status_header=false`（用户口径「顶栏默认不显示」）、`show_reasoning=false`（摘要行始终保留）。
  改这三项口径必须同提交改 README / plugin.yaml / CHANGELOG。

- **平台 entry 字段从 dataclass 派生透传**（`_IDENTITY_ENTRY_FIELDS` 除外），新增字段自动跟随；
  唯一**有意覆盖**的是 `standalone_sender_fn`（P3）：内置 sender 自己 new 官方适配器 ⇒ cron /
  无网关进程只有纯文本；我们换成经 `_factory` 的卡片 sender，发送前用
  `compat.ensure_standalone_client()` 补官方同款 SDK client，**带媒体附件的那一块回落内置**
  （附件不丢；分块非末块仍可能走卡片路径，卡片硬异常会在末块附件前停下）。`check_override.py`
  对字段的判据是「可调用且不是内置那一枚」，并核对 fresh process 真能建出 client。⚠️ 无网关
  cron 真机投递仍待验证；自动定时 cron 仍需 gateway 进程（ticker 只在那儿）。
- **钩子清单有两处，必须一一对应**：`hooks.SUBSCRIPTIONS`（真正订阅的）与
  `compat.OBSERVED_HOOKS`（登记/文档/自检读的）。新增或删除观察型钩子时**两处一起改**，
  由 `check_override.py` 核对（不一致直接失败）—— 只改一处的结果是「文档说有、代码没订阅」
  这种静默失灵。清单本身是唯一事实来源，别在别处再抄一份字面量。
- 运行时只 import 标准库与 Hermes 环境；不新增第三方依赖。
- native 流式是官方契约：`send_stream_frame(text, ...)` 的 `text` 是**累积全文**
  （不是增量），整卡替换到同一张卡；回合状态挂 `self._ld_streams`
  （key = `chat:turn_id`），帧间有节流（`_STREAM_MIN_INTERVAL`）。
- **native 帧有两条传输**（配置 `native_transport`，**默认 `cardkit`** —— 2026-09-13 翻的，
  前置是「一轮对抗审计 + 真机生产路径探针」；`tests/test_units.py` 的
  `test_declared_defaults_are_an_explicit_decision` 钉住这个默认值）：
  * `patch`：普通卡 + `message.patch` 整卡替换。字是**几个几个跳**（用户实测语）。
  * `cardkit`：CardKit 实体（`card.create` + 发实体卡）+ 每帧 **至多一次 `card.batch_update`
    写装饰（面板**两块** + 页脚）+ 一次 `card_element.content` 写正文** ⇒ **真逐字打字机**（用户实测语）。
    ⚠️ **面板是两块**（R3 收窄版，2026-09-14）：`panel_body`（推理轮）+ `panel_tools`（工具行列表），
    **都在建实体时建好**（结构仍然「建实体时定死」，没破那条不变量），两块走**同一次** batch
    ⇒ **逻辑写次数一次都不增加**。拆开的目的可以写成两条**与时钟无关**的不变量：
    ① **工具结束那一帧不重发推理块**；② **推理增长那一帧不重发工具块**
    （推理逐字在长时不再把那几十行工具摘要一起重发，量级 ≈2.7KB/帧）。
    ⚠️ **工具开始那一帧通常两块都写** —— 工具会打断当前推理轮，轮次标题要补耗时
    （`**第 1 轮**` → `**第 1 轮 · 0.4s**`）⇒ 推理块内容真的变了；别把「工具事件只写工具块」
    当成通则（2026-09-14 R3 代码审计实测：真实时钟下开局那一帧就是两块）。
    判据是 `test_units` 的 `㉕/㉖` 两条（字面量 id 列表 + `⏳/✅` 字面量 + **反向**搜索
    「工具名不许出现在推理块里」），变异 `R3-1..R3-6` 六条全红。
    ⚠️ **收尾/降级/`/stop` 重绘走的是普通卡**（`unified_panel` = `auxiliary_timeline`），
    那三条路径**没有** `panel_body`/`panel_tools`（有反向断言钉住）—— 所以「面板两块」是
    **实体卡流式期间**的形态，收尾那一刻整卡一换就回到一个 markdown（今天本来就是这样）。
    ⚠️ **观感摘要（2026-09-17，向 CLS 看齐）**：折叠面板摘要行是
    `💭 思考 {耗时} · 🛠️ 工具执行 · {n} 步`（i18n 节点，中英文各一份）；展开后正文里
    同样有 `💭 思考` / `🛠️ 工具执行` 两个灰色分区小标题。工具行 = **飞书官方线性图标**
    做**文本前缀**（`markdown.icon` + `color:"grey"`；放法是用户 2026-09-22 真机三臂对照选的：
    元素级 `div.icon` 实测图标高 3px、`column_set` 横向空 179px，前缀图标 0px）+
    加粗**英文动作名**（Read file / Run command / Load skill …）+ 耗时（`25 ms` / `1.2 s`）+
    **带颜色的状态词**（`Succeeded` 绿 / `Running` 青绿 / `Failed`、`Blocked` 红 /
    `Cancelled`·`Skipped` 灰），命令或 skill 名另起一行（灰色 + `tool-indent_outlined` 前缀图标）。
    ⚠️ **图标四条纪律**：① 默认**线性 `_outlined` + 统一灰**（彩色 `_colorful` 只有 13 个、
    颜色写死，不用）；② token 必须**逐个对飞书官方枚举页查证存在**（`enumerations-for-icons`，
    写错客户端不渲染且不报错）—— 白名单冻结在 `test_units.py::_VERIFIED_LINEAR_TOKENS`；
    ③ `ICON_ALIASES`（28 条逐条等于 CLS）是**对齐判据的唯一真相**（`check_cls_alignment` 只读它），
    `tool_icon_token(name, token)` 只是渲染层精化；想回 emoji 形态配 `tool_row_icon: "emoji"`；
    ④ **字段表纪律**（2026-09-22 真机 `200621` 的产物）：服务端对**未知字段**是**整卡被拒**
    （不是忽略，而且一次只报一个）——`markdown` 没有 `text_color`（灰色只能写进 content：
    `<font color='grey'>…</font>`，见 `cardview._grey`），`div` 的前缀 `icon` 在**组件级**
    （`div.text` 里没有这个字段）。新增/改元素前先核官方 2.0 字段表，并登记进
    `check_cardview._assert_panel_element_fields()` 的白名单（未登记 tag 直接红）。
    ⚠️ **i18n 边界**：`markdown` element.content 不承载 `i18n_content` ⇒ 工具行动作词/
    状态词固定英文、分区小标题固定中文；完整句子提示仍走 `i18n.t()`。不要把它写成
    “动作词双语”。
    参数预览被上游 80 字符截断时走 `_preview_value()` 的有界提取，**绝不把 JSON 原文
    倒回卡上**。CardKit 实体卡的外层折叠面板 header 建卡时定死；Phase 1 header
    局部更新探针的最终结论是 **c：不实现生产代码**，收尾/降级/`/stop` 仍走整卡替换
    （见 `docs/audits/cls-ui/phase-1/consensus.md`）。`<font color='…'>` 是给
    `markdown`/`lark_md` 正文上色的**唯一**合法写法（官方富文本「彩色文本样式」；`markdown`
    没有 `text_color` 字段，写了整卡被拒），生产自 v0.6.2 起默认开（`panel_color_tags: true`）；
    ⚠️ 但这个开关**只作用 legacy 文本函数**（`core/cards.py::_colorize`），结构化卡（v0.7.1 起
    唯一在跑的引擎）**无条件**写 `<font>` ⇒ 关掉它不会去色，有的客户端反而会把字面标签显示出来
    （v0.7.3 登记项：要么让 cardview 也吃这个开关，要么删掉开关）。
    每帧**元素写**预算 2 次（常量 `_CK_WRITES_PER_FRAME`；卡级上限 10 次/秒 × 帧窗口 0.25s）；
    R7 起再加一次**会话预览**写（`card.settings`，`_CK_SUMMARY_INTERVAL = 5s` 限频 ⇒ 平均
    ≈0.2 次/秒，且**不重试**）⇒ 折算 ≈8.2 逻辑写/秒 < 卡级上限 10 次/秒；
    ⚠️ 不是 HTTP 调用数：元素写撞限流时同一请求退避重发最多 4 次，而预览**不重试**
    ⇒ **单帧最坏 9 次调用 / 退避 2.0s**（切卡帧另算：封卡 patch + 建实体 + 发实体卡也都能重试）；
    ⚠️ **滑窗写入守卫**（R11-B1，`_ck_window_*`）：1 秒窗口内已经写满 10 次逻辑写时，
    **本帧让出那次装饰 batch**（可重放 ⇒ 下一帧补写；**跳过 ≠ 失败**，帧照旧返回 True），
    **不置 `ck_decor`**（记了就等于把没写说成写了 = 去重逻辑永久跳过 = 装饰静默冻结）。
    **正文（提交点）与预览永不跳过** ⇒ 它不是硬限速器，极端情况下窗口仍可能被正文顶破
    （有意的取舍：宁可多花一次配额，也不能让卡片停在半截）。窗口挂**回合状态**上
    （不是进程级共享盒子 —— 口径是**每张卡** 10 次/秒，且进程级窗口会让测试按执行顺序漂移），
    记账口径是**配额消耗**（调用发出去了就计，含限流重试与失败响应），与 `/larkdeck status` 的
    「只统计真的写出去的帧」**故意不同**。⚠️ 它**当前稳态不可达** —— 但那句话有**两个前提**
    （B1 审计实验验证）：`_STREAM_MIN_INTERVAL` 是常量（成功帧 ≤4 帧/秒）、核心在 definitive
    `False` 后停用本回合 native；前提一变守卫就复活（审计反事实实验：1 秒内顶满、让出 68 次）。
    ⚠️ **「≈8.2 次/秒」是摊还均值，不是「任何 1 秒窗口都 ≤10」**：正文与预览不查 allow ⇒
    记账口径下 1 秒窗口理论峰 **12** 条（第 10 条放行 + 正文 + 预览），别把 8.2 写成硬上界。
    它是 Phase C 的前置之一，但**单独一件不够**（审计中-3）：守卫每帧最多让出一次装饰，
    ≥2 次 `create`/帧 时还要补 plan-v1 §193 的另一半「含创建的帧不发装饰 batch」。
    ⚠️ 喂养侧也有门禁（B1 审计高-1 的收口）：窗口里这一帧新增的条目数必须**逐帧等于**
    这一帧真的发出的逻辑写数（`test_ck_write_window_guard_never_trips_at_production_cadence`
    用假时钟逐帧对账），窗口长度/容量常量由 `test_ck_write_window_semantics_*` 用**字面量**钉住；
    变异 `B1-0/4/5/6/7` 分别钉「守卫被整体撤掉」「只修剪不记账」「跳过帧顺手连正文也不写」
    「窗口长度被改小」「预览那一笔不入账」—— 审计实测这五种改法原来**四门禁全绿**。
    **装饰内容没变就不发那次 batch**（稳态下每帧 1 次）；**正文最后写**（提交点在后）；
    装饰失败只标死 + 留痕（不 fail-open）；正文失败 fail-open —— **但有两条例外（R5）**：
    ① 元素通道拿到**卡级死法**（`300309`/`300313`/`300317`）⇒ **降级成整卡 `message.patch`
    续写同一张卡**（清 `card_id`，之后每帧走 patch；`300313` 落在装饰上时例外：只标死被点名的
    那个元素、保住打字机）；② 撤回类码（`230011`/`99992354`，只可能在**整卡写入**拿到）⇒
    标死 + 清追踪 + **不补发**。码表与处置见 `docs/plan-v1.md` 附录 A。
    硬约束（全部真机实测）：**我们的实现**把结构在建实体时定死，因为**整卡替换**
    （`message.patch` / `card.update`）会**关闭流式会话**（之后写元素得 `300309`）。
    ⚠️ 2026-09-13 更正：**不是「任何结构性写入」都会关** —— CardKit 自己的元素级/批量接口
    （`card_element.patch` / `card_element.create` / `card_element.update` /
    `card.batch_update`）在流式期间**实测可用且不关会话**（见 `docs/plan-6-effects.md`
    的「重大更正」一节）。想做「流式期间加元素/改面板/上状态色」时别被旧结论挡住；
    序号必须**单调递增**（重开会话后没对齐得 `300317`）；收尾那一帧才用 `message.patch`
    整卡替换（补面板与状态色 —— 那一刻流式本来就结束）。**失败的分档见上面那两条例外**：
    确定性失败 fail-open 返回 `False` 交核心回落 edit/send（不变量 2）；卡级死法走降级车道
    （返回 `True`、续写同一张卡）；撤回类码标死 + 清追踪（不补发）。
    `unified_panel: false` 时面板元素**不进卡** —— 这个结构决定由**元素表**（`ck_elems`，
    从建出来的卡 JSON 抽的）承载，`_ck_plan` 只对表里的 id 发写入（写不在卡里的 id 会得
    `300313` ⇒ 每帧失败 ⇒ 整回合被打回纯文本）。⚠️ 别再写 `ck_panel`：那是 R1 之前用过的
    布尔字段，**已经删了**（2026-09-14 审计发现文档还在引用它，照文档写就会造一个没人读的字段）。
  * 想翻默认：先过一轮对抗性审计 + 真机 `probe_render.py --cardkit-prod`，
    再改 `_DEFAULTS` + `plugin.yaml` + README（三处同步有机械门禁：
    `test_config_schema_matches_defaults_exactly` 连 README 的键集一起核对，
    另有 `test_declared_defaults_are_an_explicit_decision` 专门钉默认值）。
- **markdown 卫生只作用在「完整文本」的写入上**（`cards.sanitize_markdown`，R6a）。核心契约是
  「流式帧的 `text` 是**累积全文**」⇒ 任何一帧改写前缀都会让用户看到文字**跳变**（核心自己只在
  收尾那一帧补未闭合围栏）。**判据是「这份文本是不是完整文本」**，不是「这是哪条调用路径」：
  * **做**：`send()`、`edit_message(finalize=True)`、`/stop` 中止重绘、native 收尾帧（两条传输）；
  * **不做**：`edit_message(finalize=False)` 与 CardKit 的**元素帧** —— 写的是累积帧的中间态，
    改一个字节就断前缀链（单测里两处都有**反向**断言）。
  四条纪律：① **代码区一个字节不许动** —— 围栏识别必须含**未闭合围栏**（延伸到文末：核心的
  「边界收尾」会把没闭合的累积文本直接送出来）、**四反引号及以上**、**`~~~`**；
  ② 游离 `**` **删掉而不是补齐**（补一个会把后半段吞进加粗），且删**第一个**候选
  （删最后一个会拆掉后文合法加粗对的闭合标记，比不改更糟）；③ **顺序不能反**（先删游离 `**`、
  再降级标题）；④ **幂等**（`f(f(x)) == f(x)`）—— 它**曾经是假的**，修法是「标题体里有
  反引号/`~~~` 时不做加粗降级」；⚠️ 它**不是**全域不变量（畸形输入会让 `_code_spans` 的跨度
  划分变化，用户看到的字一个没变）⇒ 口径只能是「**在真实语料上成立**」。
  最小复现、语料数字、审计条目见 `docs/plan-v1.md` 的 R6a 一节。
- **字节闸门量的是「要发出去的那一份」**（推论 13 的口径病，R6a 审计低-1/低-2 收口）：
  `adapter._sanitize_for_send()` 是卫生的**唯一入口** —— 它做卫生，然后**对卫生后的文本**
  量一次 `_card_body_bytes`，超了 `FEISHU_CARD_BYTE_LIMIT` 就**退回原文**并留一条限流告警。
  为什么必须有：卫生对标题密集的正文**只会变长**（每个降级标题 +2 字节），存在
  「卫生前过闸、卫生后超限」的窄带（审计构造过 `128000 → 128080`），而收尾帧原本
  **一道闸门都没有** ⇒ 超限的卡被拒 ⇒ fail-open ⇒ 用户从「一张卡」掉成「若干条纯文本」。
  判据与守卫必须同口径，否则两个都对、合起来还是漏（`docs/lessons.md` 推论 13）。
- **`/larkdeck status` 是自检的唯一入口**（`ctx.register_command`，公开 API）：报版本
  （**现读 `plugin.yaml`，不复制常量** —— 抄一份就会漂；**读不到就写「版本读不到」**，
  不许让版本段静默消失）、生效传输、钩子挂载数、**P2 的两行聚合诊断**（能力/链路 + 运行/账本；
  有明确异常才带 `⚠️`，绝不写「正常/健康」），以及**六条记录**（入站心跳 / 写卡 / 写卡失败 / 运行时长 / 掉回纯文本 / 错误码 top-N）。
  `/larkdeck config` 是同一条命令里的**只读**配置视图（见上面的配置刷新纪律）。四条纪律：
  ① **没记录就写「无记录」，绝不写「正常」**（永远说健康的自检 = 绿而无判别力）；
  ② **只统计真的写出去的动作** —— 节流跳过的帧与文本没变的去重帧不算（一个字节都没写）；
  ③ **口径是「帧」不是「次」**：cardkit 一帧最多 3 次逻辑写（装饰 batch + 正文 content +
     限频预览）、seed 帧是 2 次网络调用、一次逻辑写撞限流最多重发 4 次 HTTP ⇒ 卡片与文档一律说
     「**帧真的有写出**」，绝不说「写了 N 次 API」；
  ④ **记账挂在低层写卡收口点**（`_ld_send_card` / `_ld_update_card` 成功处）+ **两条帧路径特例**
     （cardkit 的 seed 建实体、每帧元素写 —— 它们不经那两个原语），并且**同一帧不许记两次**
     （DEGRADE 那帧既写元素又整卡 patch、seed 是两次网络调用，都只 +1）。这条是 R9 审计中-1 的
     修法：记账曾经只挂在 `_ld_stream_frame` 的 7 个调用点上 ⇒ `send()` 首发 / 非流式
     `edit_message` / `send_clarify` / `/stop` 重绘**真的写了卡却一个数都不加**。
  ⚠️ **「写卡失败」只统计「我们真的发起过一次写、而它失败了」的帧**，**不等于**「核心收到过几个
  `False`」—— `send_stream_frame` 返回 False 还有一支是「没有活跃流可收尾 ⇒ 按官方契约交还核心
  回落 edit/send」（一个写请求都没发，消息照常发出去）⇒ 那是**正常路径**，进失败账本会让健康
  回合显示一条指向不存在的写卡动作的原因。
  ⚠️ **R11-C2 那三条各有自己的口径，别混**（都是「用户看不见、排障必须知道」的那一类）：
  * `已运行` 是**时钟读数**（建共享盒子那一刻取），不是「记录」⇒ 它永远有值，而且
    `context.reset()` **不许**清它 —— 清了就永远显示「刚重启」，而那正是最容易误判的一个数；
  * `掉回纯文本` = **本回合我们把卡片车道让给了核心**的次数（核心收到 `False` 就停用本回合 native
    ⇒ 用户**肉眼能看出**「这次没卡片」）。
    ⚠️ **口径以代码为准（2026-09-15 审计 D1：这段文字曾与实现相反）**：调用点只有 `_ld_stream_fail`
    与 `send()` 两处，都是「**真的发起过一次写、而它失败了**」⇒ 在这一版里它与「写卡失败」
    **同点同帧各 +1**；它**不含**「没有活跃流可收尾 ⇒ 按契约交还核心」那条**正常路径**
    （一个写请求都没发）。**两数当前不可分辨**这件事如实写在这里，别让文档比代码乐观：
  * `错误码 top-N` 只数**非零**码（`0` 是成功、`None` 是没拿到响应），不同码有上限
    （超出并进 `other`，坏上游撑不大这张表）；计数挂在 `_ld_stream_fail` **一个**收口点，
    不在它那 **11** 个调用点各记一遍（数法：`self._ld_stream_fail(` 的出现次数；加上 `def` 那一行才是 12 —— 2026-09-15 审计实测纠正）（那是「同一件事两处真相」的标准入口）；
  * 卡片**短码**只出现在**日志自检行**（`卡片=xxxxxx`，v0.7.2 起；用户 2026-09-21 口径：
    「我从来没有提过这个要求」—— 页脚、面板、正文、卡头**一律不出现**）。它取自**本帧的卡**
    （`message_id`/`card_id` 后 6 位），**不是**进程级快照 —— 页脚指标串台是已知取舍，
    但**定位**用的短码串了就等于没有；它**不是**凭据，别当鉴权用。
    反面守卫：`test_v4_17b`（状态×mid 全枚举扫整卡 JSON + 建卡实体/元素 batch/收尾 patch）
    + 变异 `G2-10`/`Y20`/`V4-17B`（把短码挂回去必须实红）。
  ⚠️ **这些记录是进程级全局、跨会话共享**（`context._STATUS` 是模块级 dict ⇒ 并发时卡片上的
  数字含**别的会话**的部分）：卡片必须带一句口径说明（i18n `cmd.scope`），排障时别拿别人的失败
  查自己的卡。要真做对得从钩子载荷的 `session_id` 分桶（与页脚同一件事，**未做**）。
  ⚠️ 命令派发只挂在核心的 **idle 路径**上 ⇒ 网关里**生成回答期间敲的命令会被排队**（当成普通
  输入，回合结束才派发）；**别写成绝对规则**：CLI / TUI（`cli.py::_run_plugin_slash_command`、
  `tui_gateway/methods_tools.py`）**直接调处理器**、立刻回卡。注册不到不影响卡片功能，但启动
  自检必须**如实**写「命令未注册（原因）」，而那句自报由 `check_override.py` 用**核心自己的
  getter**（`get_plugin_command_handler`）核对（它以前可以硬编码成永远成立而四门禁全绿）。
- **心跳的归因必须可判定**（R9 审计中-3）：`core/hooks.py` 的 `note_inbound()` 是
  `_on_pre_gateway_dispatch` 的**第一条语句**（**位置本身就是纪律**：以前它排在归属绑定之后、
  还在同一个 try 块的末尾，于是 `_compat.lookup_session_id` / `_panel.bind_chat_session`
  这些**兄弟模块**的异常会被同一个 `except Exception` 连心跳一起吞掉 —— 实测「绑定抛 ⇒
  `inbound_count=0`」，而那正是判「消息到没到插件」的唯一凭据）。归因照实说：
  **心跳不动 ⇒ 消息没到插件的钩子回调**（进程换了 / 平台名被别的插件抢走 / 在网关更早的关口
  就被挡下 —— `_hm_admit_event` 里有四类消息在钩子**之前**就 return：internal 合成事件、
  profile 路由被拒、Slack 忽略频道、启动恢复期队列）；**心跳在动而写卡不动** ⇒ 消息到了，
  问题在卡片侧。反面同样要说：**心跳在动 ≠ 这条消息会被处理**（未授权发送者 / 无 user_id
  是在钩子**之后**被拒的，心跳照记）。
- **卡片点击回调（`_on_card_action_trigger`）跑在 SDK 回调线程上**，三条纪律：① **绝不抛**
  （抛一次就把「别人的卡」的点击整条炸掉）——能力缺了就退回「不改卡但点击生效」；
  ② **换卡能力运行时探测**（`compat.accepts_positional(_card_response, 1)`：核心那侧形参
  可能改名，我们按**位置**传参，真契约只是「能不能收下这张卡」）；③ **失败态只弹 toast、
  绝不换卡**（`clarify.toast_*` + `CallBackToast`）—— 换卡会让一次迟到的重复点击把
  「已确认」**退回待答**，而答案其实早已送达 agent（不可逆）。
- **超长回答要切成卡链**（R4，`_ld_ck_split`）：native 流式的帧文本是**累积全文**，核心**不会**
  替我们按卡片容量切分（源码注释：`native streaming bypasses this: the adapter truncates`）⇒
  卡组自己切。四条纪律：① 触发阈值 = 硬上限的**一半**（`_CK_SPLIT_SEAL_AT`，给「（续下一条）」
  与新卡一次装下整段留余量）；② 回合状态里的 **`ck_offset`** 是「本卡从累积全文的第几个字符开始」，
  正文、降级 patch、收尾 patch **三条车道都必须按 `text[ck_offset:]` 渲染**（写整段就是把前半段
  重放一遍 —— 变异 `R4-1/4/6/7` 四条各钉一条车道）；③ 封卡必须 `streaming=False`（否则旧卡永远
  停在「正在生成」）；④ 切点优先换行、**避开代码围栏**（`cards.code_spans`），整段都在代码区里时
  退到围栏起点。含「切不开就 fail-open」的两半判据（一帧的增量连新卡都装不下时必须回落，不发必被拒的卡）。
- **v0.7.0 正文来源 = own（默认）**：正文只认 `panel.record_answer_delta` 从
  `on_stream_delta(kind="text")` 累积的文本；native 帧只作刷新/ finalize 兜底。
  `finalize`：core 非空且以 own 为前缀 → core；**core 是 own 精确后缀 → 保留 own 前段**；
  其余分叉 → core 整段；绝不按分隔符 split/rsplit。F4 异常重发帧在 own 下拒绝持久化并交回
  core edit/send 回落（防 terminal 命令/参数进正文）。绑定漂移/回合漂移严格 fail-open。
  下面的「有证据地剥帧」是 **legacy 回退路径**，仅 `body_source: legacy` 生效，P2b 归档后删除。
- **正文净化 = 有证据地剥掉核心叠加的工具进度块**（legacy 回退；R11-A7，`adapter._strip_core_progress`）。
  起因：native 流式下核心会把工具进度行**合成进同一帧**
  （`"\n\n---\n".join((accumulated, progress))`），而我们按契约渲染整帧 ⇒ 那几行出现在卡片正文里。
  用户明确要求**一个核心配置都不动**（不改 `display.platforms.feishu.tool_progress`）⇒ 只能在插件侧做。
  判据是**证明**不是猜：`帧文本 == 我们的正文累积 + "\n\n---\n" + 尾巴` ⇒ 尾巴只可能是核心加的
  （模型写下的每个字节——**包括它自己写的 `---`**——都在累积里）。**五个条件缺一不可**：
  ⓪ **收尾帧一律不剥**（R11-A7 尾巴，**这条最强**）—— 它是**证明**而不是保险：核心的收尾帧发的
     是**纯累积正文**（`gateway/stream_consumer.py` 里 `display_text = self._accumulated` 之后
     **只有 `tick.is_interim`** 才走 `_compose_frame_content()` 合成进度块；而 `finalize` 只可能是
     `got_done` 或 `got_segment_break`，两者都让 `is_interim` 为假 ⇒ finalize 发送点里
     **除一处例外**（下条）传的都是纯累积）。⇒ 在**这些**收尾帧上，「累积之后还有内容」
     **只可能是模型自己写的**（含它自己写的 `---`）。
     ⚠️ **例外（审计 2026-09-15 实测抓出，别再把「所有」写回来）**：
     `gateway/stream_consumer_transport.py:391` —— 某一帧**确定性失败**后、关流前，核心会用
     **同一个合成文本**（累积 + 进度行 + 光标）再发一帧 `finalize=True`。那条路上条件 0 会把
     本该剥的进度行留在收尾正文里。**这是有意的取舍**：可见的进度行 vs **不可逆地吞正文**；
     要做对需要「形状判据」（尾巴逐行符合核心进度行形状，tool_name 用我们 `pre_tool_call`
     见过的名字比对）—— **未做**。⚠️ **登记处 = `docs/plan-v1.md` 附录 F 末尾那一条**
     （2026-09-16 审计实测：这句原本写的是「登记在附录 F」，而附录 F 里**并没有它** ——
     一个指路到空处的引用。现在两端都补上了；改这一条时**两处一起看**）。
     为什么非加不可：累积来自**钩子队列**（异步投递），收尾帧完全可能**早于**最后一个正文增量到达
     ⇒ 累积是一个**陈旧的完整前缀**（`complete` 仍为真）⇒ 旧版四个条件**全部成立**，它会在
     **唯一不可逆**的那一帧上把模型的续写剥掉，而核心对 finalize 是**乐观记账、不会再补发**。
     ⚠️ 残留（如实登记）：同样的滞后在**中间帧**上仍可能剥掉一小段，但那只是**一帧** ——
     累积追上后下一帧就渲染出来（自愈）；收尾帧没有「下一帧」，所以只有它必须挡。
  ① 有工具窗口（自上次正文增量以来有过 `pre_tool_call`，与核心 `_tool_progress_lines` 同生命周期）；
  ② 累积**完整**（没被 `_MAX_ANSWER_CHARS` 冻结——残缺的累积仍可能是前缀，拿它当证据会吞正文）；
  ③ 累积非空且是帧文本的前缀（归属错/回合错/核心换了累积都对不上）；
  ④ 尾巴以分隔符开头**且分隔符之后还有内容**。
  任何一条不成立 ⇒ **原样渲染**（fail-open：那一次进度行短暂可见，核心下个正文增量到达时自己会清）。
  ⚠️ **只剥后缀**：`ck_offset` 等偏移都指向正文内部，剥后缀不会让任何偏移失效（R4 卡链的前提）。
  ⚠️ 旧版（`8f81b4d` 移除）是**按分隔符切**——那是猜，模型写一条 markdown 分隔线就会把后半段答案
  吞掉、而且核心对 finalize 是乐观记账、不会再补发。判别力由变异 `R11-1..R11-9` 九条守住
  （含「按分隔符切」「不完整也剥」「没有工具窗口也剥」「**撤掉收尾帧护栏**」四条反例），
  真机凭据是自检行里的 `正文剥进度=N`（卡片读不回来，这是唯一凭据；没有它真机没法验）。
  ⚠️ 这个 N 数的是**中间帧**（收尾帧自 R11-A7 尾巴起**永不剥**）⇒「N=0 而用户回看正文干净」
  是可能的（工具先行那种回合就是），别把 N=0 一律当成失灵。
- **进程内状态清点（R11-A0）——「共享」不只是容器。** 真机根因（同进程两遍插件发现 ⇒ 两份模块
  对象）逼我们把状态搬到进程级共享盒子（`builtins._larkdeck_shared_state`），但**第一批只搬了容器**，
  漏了三类：
  ① **锁**：容器共享、`_LOCK` 模块级 ⇒ 两份模块对象**各持一把锁**，互斥从构造上失效
     （实测 `RuntimeError: dictionary changed size during iteration`，真机上是「偶发丢面板」）；
  ② **在飞集合**：`context._INFLIGHT` 各持一份 ⇒ 另一份 `discard` 不掉，那个模型**永远不再被探测**
     （静默退化成家族兜底 128K）；
  ③ **结论性数据**：`adapter.HOOKS` / `COMMAND` 各记一份 ⇒ 启动自检与 `/larkdeck status`
     按「读到哪一代」给两个不同的答案（钩子 7/7 还是 0/7）。
  ⇒ 纪律：**凡「进程内全局」的东西，逐个问一句「它和它的守卫/兄弟容器同源吗」**。
  键清单**只有一处真相**（`panel._SHARED_BOX_FACTORY`，`context._shared_box()` 委托；
  以前两处各声明一串同名键 ⇒ 加一个键很容易只加一处，漏掉的那份会**静默新建一个不属于盒子的
  容器**，症状与世代裂脑一模一样）。断言：`set(真实盒子) == set(_SHARED_BOX_FACTORY)`（单测 ㉙）。
  **明确不共享的两项**（都有理由，不是忘了）：① 适配器的**类缓存** `_BASE_CLASSES`/`_MERGED_CLASSES`
  —— 缓存的是**代码**，共享会把活适配器冻在上一世代的代码上（而钩子是新世代 ⇒ 一个回合里两个版本
  的代码协作）；② 各 `_log_*_once` 的限流戳（挂在函数对象上）—— 换世代最多多打一条限流日志，
  不影响任何行为与数据。
- **适配器合并类不许自套娃（R11-A6）**：第二世代的 `_discover_base_class()` 拿回来的是**第一世代的
  合并类**（注册表里挂的就是上一世的 `build_adapter`）⇒ 照旧写法会叠成
  `[新合并, 新混入, 旧合并, 旧混入, 官方]`，而混入层里 `super().xxx()` 的**回退路径会执行两遍**
  （我们的回退都是「真的写一次卡 / 真的交还控制权」⇒ 同一帧写两次）。
  `merged_class()` 现在先用 `_official_base_class()` 把我们的层剥掉，只继承官方类。
  ⚠️ **剥旧层不能只按对象同一性判**：跨世代的 `LarkDeckMixin` 是**两个不同的类对象**（模块被重新
  `exec`），只按 `is` 判会认不出旧层 ⇒ 自套娃照旧。判据 = 对象同一性 **或** 命中我们自己的名字
  （`LarkDeckMixin` / 合并类名），变异 `R12-6/R12-7` 各钉一半。
- 页脚指标是进程内全局（钩子记「最近一次 API 请求」），多会话并发共享同一快照；
  要按会话隔离得从钩子载荷的 `session_id` 分桶（未做）。
- **页脚模型段显示「模型名」不是模型 ID**（用户 2026-09-22：「显示现在的好像是模型 ID，我想要
  做成显示模型名」）：`context.display_model()` = 别名优先（配置 `model_aliases`，其次
  `~/.hermes/model_aliases.json` 子串匹配、热更新）→ 确定性格式化（token 表 / 版本号 / 参数量 /
  尾部日期戳）。加名字只改这两处，**不要在页脚里再拼原始 ID**；别名文件读坏只许退化成格式化，
  不许把页脚弄没（`test_v4_62` + 变异 `V4-62`/`V4-63`）。
- 面板数据策略与页脚不同：`panel.py` 按 `session_id` 分桶；归属优先用
  `pre_gateway_dispatch` 观察到的 `chat_id -> session_id` 映射（确定性），拿不到才退回
  「最近活跃」。**回合状态色**（ok/error/stopped）走同一套归属，颜色载体是
  `collapsible_panel.border.color`（见 `docs/metrics-and-hooks.md`）。
  钩子回调纪律源自 `pre_tool_call` 是 **fail-closed**（回调卡住会阻止工具执行）：
  只写内存、微秒级返回、异常自吞、**永不返回 directive**。

## 验证

```bash
python3 tests/test_units.py        # 纯单测，零网络、零 Hermes 依赖，必须全绿
python3 tests/check_override.py    # 真跑 Hermes 插件加载器（临时 HERMES_HOME），必须打印 OVERRIDE OK
python3 tests/check_hooks.py       # 真钩子派发器验证指标采集 + 面板数据层，必须打印 HOOKS OK
python3 tests/check_clarify_e2e.py # 澄清卡端到端，必须打印 CLARIFY E2E OK
python3 tests/check_cardview.py    # 视觉表 golden + **独立字面量**（图标表/真实工具名/spinner 三字段）
python3 tests/check_cls_alignment.py --require  # 图标表 vs CLS 源码逐条（缺席即 FAIL）
python3 tests/mutate_check.py      # 变异验证器：撤掉每条修复必须变红（改断言后必跑）
# ↑ 正式门禁 = **六支**（上面 5 支 + check_cls_alignment；`run_fast.py --full` 就是它们）
python3 tests/mutate_check.py --preflight   # 只做锚点对账（0.1 秒级）；见下
```

**改完锚点先跑 `--preflight`（0.1 秒），别等 65 分钟的全量**（2026-09-16 加）。
它把 `MUTATIONS` + `CONTROLS` 每条的锚点在目标文件里数一次，要求**恰好 1 次**，有问题就逐条
打印并退出码 1。判据抽成 `_anchor_problem()`，**变异循环 / 对照循环 / 预检三处共用**（以前两处
各写一遍 —— 预检再抄一遍就是三处真相）；条目**形状**校验同理（`_shape_error()`）。
⚠️ **分类器与留档口径（2026-09-17）**：`test_units.py` 输出里只要有行首 `ERROR `，
即使同一次运行还有 `FAIL  `，也一律判 `red-crash`；`-k` 只命中对照时直接 EXIT=2，
对照不能替代变异证据。审计日志被 `*.log` 忽略，持久留档必须 `git add -f`，并在
`RUN*-ATTESTATION.md` / manifest 写清 run id、tree、commit、EXIT 与外部见证路径。
force-add 前先扫描敏感值，例如：
`grep -RInE '(ou|oc|om)_[A-Za-z0-9]{16,}|app_secret|Bearer |-----BEGIN' docs/audits`，
命中真实凭据/真实 ID 的日志不入库。
⚠️ **它的边界要记牢**：它只回答「锚点还在不在、唯不唯一」**这一个**问题 ——
**没跑门禁、也没做基线自校验**，所以「锚点 ✅ + exit 0」与「这棵树根本跑不了」**可以同时成立**
（实测：把 `core/i18n.py` 弄成语法错误，它照样 `--preflight` ✅、exit 0，而全量跑立刻
`EXIT=2`「基线不是绿的」；当时对账 318/318，2026-09-17 已是 390/390）。它**看不出**：锚点落在注释里、落在用例不经过的分支上、
或撤掉后压根不会变红。⇒ **别拿它当提交前的绿灯**，它是**前置筛子 + `-k` 子集跑的补盲**
（它有意**不受 `-k` 影响**，这样 `-k` 子集跑看不到的坏锚点它也查）。
最后两类「变异没生效」现在会被**如实归因**（不再打印 🟢「断言没有判别力」—— 那会把结论写反）：
替换串与原文**逐字节等价**、或锚点整段落在**注释**里，都打印
`⚪ 变异没生效（原因）`。⚠️ 这条判据**只对 `MUTATIONS` 用** —— `CONTROLS` 里那条「纯注释改动」
是**故意的**等价对照。

**改了任何断言，都要跑 `tests/mutate_check.py --delta`（增量：只跑锚点区域变过的 + 新增的）。**
跳过判据是**三重指纹**：`代码区域`（锚点 ±15 行，按**完整 old** 定位）× `用例名集合`
（当年抓它的 `def test_*` 今天是否都还在；只新增用例不算失效）× `helper/fixture`
（`write_golden_trace.py` + golden 夹具 + 两份契约 JSON）。三者任一变了就重跑。
⚠️ `MUTATIONS` 里**不许**留 `expect==`（那是**对照**，写进 `CONTROLS`）——否则它永远不跑、
还会被算成「已跳过」；`--preflight` 会直接报错。
全量直跑**不再是每版必跑**（实测：分 2 片并行 **≈18 分钟**，旧口径 60–90 分钟）：协议见
`docs/verify-log.md` 的「09-21 协议变更」——`--seed-inherited <上次全绿的 tag>` 标继承、
`--ledger-status` 看覆盖率、`full_audit_at` 为空或过期时在低负载后台补一次全量直跑。
⚠️ 继承只对生产代码区域做指纹、**不对测试套件**做 —— 别把 inherited 念成「跑过了」。
⚠️ **`--shard i/n`（n≥2）分片跑不会自己盖 `full_audit_at`**（`_full` 的判据是「本片 picked 数 ==
全量条数」，n≥2 时必然不等；`1/1` 才会为真）：分片账本要按「当日 🔴 名字并集覆盖全部条目 +
对照 0 假红 + 三重指纹一致」合并后才能盖章（逐段证据写进账本 `_meta`；工具固化已登记 v0.7.3）。
⚠️ **测试里的等待必须有界**：用例里写无界的 `await <Event>.wait()`，一旦被测分支被变异短路，
事件永不 set ⇒ 整支门禁挂到 45s 超时 ⇒ 被记 💥「只有崩溃」，一条**有牙的变异**就被记成
「没有证据」（`V1-2` 实测踩到）—— 用 `_await_event(event, timeout, what)`（超时转断言）。
存量还有 3 处无界 `await release.wait()`（`tests/test_units.py:11730/11786/12346`，
都在带 `_await_event` 或 `finally: release.set()` 的同用例里，当前不会实际挂死），
已登记 v0.7.3 全仓扫描 + 静态门禁。

**改了任何断言，都要跑 `tests/mutate_check.py`。** 它是本仓库「先写变异，再写断言」的落点：
清单里每条变异 = 一处「把某条修复撤掉」的定向改动，判定标准是**至少一个门禁变红**。
四门禁全绿、却没有对应变异变红 ⇒ 那条断言**没有判别力**（本项目头号缺陷类型；**这一族的形态
清单只有一处**：`docs/lessons.md` 推论 34，8 种形态 + 各自病根）。三条已固化的历史形态：
改常量口径后被同文件旧赋值覆盖、「真对象返回空列表」型断言（被观测对象要**真的缺东西**才有
判别力）、`isinstance(x, int)` 这类恒真断言。`-k <子串>` 只跑一部分。
⚠️ 它自己踩过的坑：快照目录**必须叫 `larkdeck`**，否则 `import larkdeck` 会解析到未变异的
基线（曾导致一批假绿）。
⚠️ **它退出码非 0 有两种，都算红**：① 变异跑完了但没被门禁抓住（断言假绿）；
② **锚点失效**（`❓ 锚点没找到` —— 源码改了、清单里那条变异的原文串已不存在）。
第 ② 种**不是**「跳过一条」，而是「清单与源码脱节」：跑不到的变异等于没验，
所以必须把锚点重新对准当前源码，**绝不允许把跑不到当通过**。
⚠️ **锚点还必须唯一**（同属第 ② 种）：清单里那条原文串在目标文件里出现多次时，
`replace(..., 1)` 只换**第一处** ⇒ 变异打到别处去，而报告照常打印「🟢 断言没有判别力」，
**结论正好写反**（真发生的是「变异没生效」）。所以 `count(old) != 1` 也一律算红，
锚点要带足够上下文让它唯一。（2026-09-13 实测：`if panel:` 在 `cards.py` 里有两处。）
⚠️ **「锚点唯一」还不够 —— 它必须落在「那条用例真的会经过的语句」上**（2026-09-15 实测）：
`G2-11` 的锚点 `if self._ld_transport() == "cardkit":` 在源码里**恰好唯一**，但它在
`state is None` 的**非 finalize** 支上，而对应用例喂的是 `finalize=True` —— 它在更早的
`if finalize: return False` 就返回了 ⇒ 变异插进去的那句**从来没被执行过**，报告照常打出
🟢。`count(old) != 1` 只防「歧义」，防不住「找对了文件、**打错了分支**」。
⇒ 改完锚点必须用 `-k` 逐条重跑，而且**只认 `🔴 断言失败`**：`❓ 锚点没找到` 与
`💥 只有崩溃` **都不算判别力证据**（前者是清单脱节，后者是「把代码弄坏了」）。
（这一条的完整形态与它的邻居「锚点撞车」「分类器看错」见 `docs/lessons.md` 推论 35。）
⚠️ **「无牙」的又一种形态：输入构造让错误分支不可达**（`G1-22` 实测）。
⚠️ 这一族的**形态清单只有一处**：`docs/lessons.md` **推论 34**（8 种形态 + 各自病根）。
以前这里写的是「第五种形态……前四种见推论 6/7/8」—— **三个编号对四种形态**，
而对不上的引用本身就是这一族的病（2026-09-16 收口，**别在别处再重新编号**）。
用例 `⑨` 想证明「条件③是**前缀**判据、不是**子串**判据」，
探针写的是 `"宋" + 累积 + 分隔符 + 进度` —— 看着对，但条件④那一行按**长度**切片
（`text[len(accumulated):]`），多出来的 `"宋"` 让切片**错位一个字符** ⇒ 尾巴不以分隔符开头
⇒ **连错误实现也原样返回** ⇒ 断言两边都成立（把 `startswith` 放宽成 `not in`，四门禁全绿）。
同型还有 `X20`：判据直接读「已经建好的那个账本格子」，而**初始化分支**在进程启动时就跑完了
⇒ 撤掉那行初始化代码照样成立，必须先把那一格删掉、逼初始化再跑一次。
⇒ 判据是：**用例必须能证明「错误实现会走进那条分支」**。拿不准就把这条自证也写成断言
（`G1-22` 就是这么做的：先断言构造本身能把错误实现推到剥离分支，再断言行为）。
⚠️ **写「已修」前先写出可复现的缺陷现场**；「这个参数没人读」用 AST / `grep` 数一遍；
生产代码与判据同次引入时，先问「撤掉生产代码那一行，判据会红吗」。完整病根与三个
反例见 `docs/lessons.md` 推论 36，别在别处重新编号。
⚠️ **注释里的「为什么」必须是推导，不能是同义反复；外部前提要有独立判据**。当前同源
    前提由 `tests/check_hooks.py` 的前提核对与 `PA-1`/`PA-2` 钉住；仍未覆盖 `run()` 自身分支、
    传输层与 `_adopt_final_text`（fail-open，只可能不剥、不会吞正文），登记在
    `docs/plan-v1.md` 附录 F。完整形态与反例见 `docs/lessons.md` 推论 37。关键性质
    **单独成条**：判据放大用例里会被前面的具体断言遮住，永远无法证明它有没有判别力。
⚠️ **合并 / 变基 / `git apply --3way` 之后必须重跑全量变异**（不是只跑 `-k`）：
2026-09-14 实测 `--3way` 在一个「删除死代码」的补丁上静默取了他们的版本、把已删掉的恒假门禁
**还原回来** ⇒ 对应变异 `CK23` 又变 🟢，而四门禁照旧全绿。冲突解决得再干净也不能替代重跑
（见 `docs/lessons.md` 推论 26）。

没有 CI / lint / formatter，这五个脚本就是全部验证。**一律用 Hermes 自带解释器**
`/Users/Zayn/.hermes/hermes-agent/venv/bin/python3`（系统 `python3` 少了 Hermes 的依赖：
`lark_oapi` 导不进来 ⇒ CardKit 那几条用例会**假红** —— 它们的前提断言（「拿不到 SDK 就
fail-open」）被顺带满足，报出来的失败信息与真实原因无关。2026-09-14 实测踩过一次）。
⚠️ 这几个脚本必须用**它们自己的 `__main__` runner** 跑；`pytest -q tests/test_units.py` 不是
支持的入口：pytest 的 logging/全局状态让一批 CardKit/ledger 用例出现**既有**失败（与本次改动
无关，HEAD 同样复现；审计从 `git archive` 到 /tmp 验证过）。看到 pytest 失败先换回
`python3 tests/...` 再判断，别把运行器差异当成代码回归。

- **门禁先自证「测的就是这份代码」**（R11-B2 审计高-1）：`test_units.py` 开跑时会比对
  `larkdeck.core.adapter.__file__` 是否在本仓库目录下，不在就 `SystemExit`。为什么必须有：
  这个脚本靠 `sys.path` 找 `larkdeck` —— 搜索路径上更靠前的地方只要有**另一份同名目录**
  （`/tmp` 下前一次导出的镜像、旧拷贝、陈旧 `__pycache__`），门禁就会去测那份代码，而
  **四门禁照常全绿**（审计实测：把 `capacity_exceeded()` 整条改坏，在镜像目录里 184/184 全绿；
  我自己起临时拷贝时也当场踩到）。这与「`mutate_check` 的快照目录必须叫 `larkdeck`」同源，
  都属于**二类假绿**（跑是跑了，跑的不是这份代码）的入口。
- `check_override.py` 是唯一能证明「注册表覆盖生效」的手段 —— 单测用替身，证明不了运行时行为。
  它还负责验证插件配置桥接：临时 config.yaml 里写 `plugins.entries.larkdeck.settings`，
  断言 `_CONFIG` 生效且未配置的键保持默认。
- 测试里要用加载器那份 `context` 模块（`hermes_plugins.larkdeck.core.context`）；
  直接 `import larkdeck.core.context` 会拿到第二个模块对象，读写状态对不上（`check_hooks.py` 盯这个）。
- **改 `cards.py` 或任何卡片结构后必须跑 `tests/probe_render.py`**：唯一连真实飞书的测试
  （从 `~/.hermes/.env` 读凭据，把探针卡真发到自己的飞书 DM：功能卡 + 双语互换实验 +
  页脚样式对照，自动先清理上次的探针卡）。
  本地单测只能验结构，卡片合法性由飞书 API 返回码说了算。它只验渲染，不验点击。
  带参数的几种模式各答一个「只有真机能答」的问题：`--typing`（打字机 A/B，动画只能肉眼判）、
  `--bytes`（字节上限阶梯，create + patch 都打）、`--elements`（元素数阶梯，官方硬上限 200）、
  `--rate-limit`（连续 patch 的限流实证）、`--stop-redraw`（**中止重绘真机端到端**：
  正文超预算但发得出去时 `/stop` 必须真的把卡重绘成中止色，断言**载荷里有颜色**；
  **两条路径都跑** —— 非 native 与真 native 流式）、
  `--clean-only` / `--no-clean`（清理控制）、
  `--button-2`（**只发 ⑮ 一张**：2.0 `button` + **组件级** `behaviors`。不变量 5 里 `button` 是
  唯一**只有官方文档**的一格，只能**真机点一次**判定 —— 而「请人点一次」是全项目唯一消耗用户
  时间的动作，所以它**先本地自检再发**（`probe_card_problems()`：方言混用（**整棵树**扫，
  嵌套在 `column_set` 里的 1.0 `action` 行照样拒收）/ 缺组件级 `behaviors` / `config.summary` 形态 /
  `button.text` 必须是 `plain_text` / 回调 value **必须让 `cards.is_probe_value()` 为真** /
  元素与字节超限），自检不过就**不发**，免得白费那一击）。
  ⚠️ 那条 value 判据**必须与适配器共用 `cards.is_probe_value()`**：审计实测过两者分叉的后果 ——
  自检按「键存在」放行、适配器按「真值性」不派发 ⇒ `{"larkdeck_probe": False}` 的卡
  **一行日志都不会有**，用户一次**成功**的点击被读成「飞书没投递」。变异 `Y23` 钉这一条。
  ⚠️ **探针发消息的 `uuid` 必须每次都不同**（2026-09-14 实测）：飞书按 `uuid` 去重，
  用固定 uuid 时「删掉再重发」会拿到**同一个已删的 `message_id`** —— 接口回 `code=0`，
  而 DM 里什么都没有（我因此让用户白找了一次）。同理，`--cardkit-prod` 建出来的卡
  **必须记进探针账本**（否则 `--clean-only` 删不掉它，只能靠人长按）。
  ⚠️ **清理只许按 `message_id` 删**（探针自己的账本 / `card_id` 精确匹配）。**绝不许按「最近 N 分钟」的时间窗盲删** —— 2026-09-13 实测：那样会把用户那一回合的**真实回答卡**一起删掉（时间窗分不清「我的探针卡」和「用户的卡」）。
  ⚠️ 改 `cards.py` 的**降载档位**或状态色载体后，`--stop-redraw` 与默认模式都要跑一遍。
- **`tests/probe_ck_stream_ops.py`（独立探针）**：回答「CardKit **流式会话进行中**做元素级/批量写入
  会不会关会话」。每个操作之后紧跟一次 `card_element.content` 写，**以返回码为判据**
  （`0` = 没关，`300309` = 关了）。实测结论：关会话的只有**整卡替换**与**显式关流式**；
  `card_element.patch` / `card_element.create` / `card_element.update` / `card.batch_update`
  在流式期间**可用且不关会话**（更正了「任何结构性写入都关」那句旧话）。
  `--lanes` 答 R5 的两个问题（**都以返回码为判据，不需要眼睛**）：① `batch_update` 混一个
  卡里不存在的 id ⇒ 返回码是卡级的、且 **msg 点名坏 id**（实测 `300313` +
  `ErrMsg: not find elementID : <id>;`，之后写元素仍 `code=0`）；② 整卡 `message.patch`
  **能覆盖 CardKit 实体卡的消息**（`code=0`，随后写元素得 `300309`）⇒ `DEGRADE` 车道可做。
  `--capacity-codes` 答 R11-B2 的两个码（**以返回码 + msg 字面为判据，自带断言、自动删消息**）：
  运行时 `card_element.create` 撞 200 墙（`append(panel)` 与 `insert_after(answer)` **同形**）⇒
  `code=300315` + msg 尾部内层 `code: 300305`；复用已存在的元素 id ⇒ 同样外层 `300315`，
  但尾部内层是 `code: 300301`（方括号里的 `Code 1001` 只是**描述码**）⇒ 这就是「`300315` 是
  **包装码**、`capacity_exceeded()` 必须解析内层码」的实测出处。⚠️ 它只删**消息**，
  `card.create` 建出来的实体卡本体留在远端（DM 里看不见，既有探针同此行为）。
  `--visual` 出**一张给人看的卡**（流式期间新增元素 + 面板边框改黄 + 面板内容更新）——
  「接口收下了」与「客户端画出来了」是两件事，后者只有眼睛能判（2026-09-13 已确认过一次）。
  默认自动删卡，`--visual --delete` 同样不留下卡片。

## 部署

- **Mac（现役）**：`~/.hermes/plugins/larkdeck` 软链到本仓库（`./install.sh`），改代码即生效。
- **NAS（待装）**：`/opt/data/plugins/larkdeck`（`HERMES_HOME=/opt/data`，bind 挂载，容器重建不丢）。
  官方镜像源码在 `/opt/hermes`，**非持久** —— 这正是本项目存在的理由。
  装之前先读 `docs/switch-from-hfc.md`：那边有一步是拆掉容器启动期的源码注入脚本，
  不拆的话每次开机都会把旧注入打回 `/opt/hermes`，直接顶掉本插件。

## 红线

- 不往仓库提交任何凭据、`config.yaml`、`.env`、**可能含凭据或真实 ID 的运行日志**、
  真实 chat_id / open_id。这是**公开仓库**；`docs/audits/**` 的审计日志经脱敏扫描后
  可 `git add -f` 入库（见「验证」一节的分类器与留档口径）。
- 不在 NAS 上做写操作而不先确认；破坏性操作前先说清范围与回滚点。
