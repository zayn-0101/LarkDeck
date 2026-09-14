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
   （`CALLBACK_INSTANCE_ATTRS` —— 实例属性在类上探不到，只在运行时 `AttributeError` 时回落）；
   **信号型契约 1 个**（`SIGNAL_ADAPTER_ATTRS` ——
   核心在 `/stop`、`/new` 路径**主动调我们**的 `interrupt_session_activity`，
   缺了不致命但「中止后卡片不变色」是静默失灵）；**处理生命周期 1 个**
   （`REACTION_ADAPTER_ATTRS` —— `_reactions_enabled`，覆盖它必须**尊重父类**语义，
   缺了 `reactions: false` 静默失效）；澄清网关内部结构
   （`_lock` / `_entries` / `entry.multi_select` / `mark_awaiting_text` / `resolve_gateway_clarify`，
   已封装成 `clarify_multi_select()` 等函数）；会话归属公开面
   （`SESSION_ATTRIBUTION_API`）。订阅的钩子清单也在本文件（`OBSERVED_HOOKS`，
   文档/门禁/自检都读它，同步规矩见「约定」）。新增依赖一律先登记。
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

   > **旧结论错在哪（别再重蹈）**：原表述是「2.0 的 `behaviors` 回调到不了
   > `p2.card.action.trigger`，所以澄清卡只能是 1.0」。这是**误诊** —— 当时那张「2.0 澄清卡」
   > 实际用的是**没有 `behaviors`、只有顶层 `value` 的 1.0 按钮**放进 2.0 的 `body.elements`，
   > 那是「2.0 卡里放 1.0 组件」这个病；而且论据抄自第三方插件的**代码注释**，不是真机实验
   > （该插件自己的代码还与那句注释自相矛盾）。
   > **认证据的规矩：官方文档 + 真机探针为准，不抄别人注释。**
   > 2.0 澄清卡（`select_static` + `input`）是可行的，见 `docs/plan-6-effects.md` 阶段 4。

   澄清卡现在**默认用 2.0**（`select_static` / `multi_select_static` / `input` + 组件级
   `behaviors`）—— **2026-09-13 翻的，两条前提都满足并留了一手证据**：
   ① 真机点一次：探针 ⑫（2.0 `select_static`）点下去后网关日志出现
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
                R9 起还持有**自检账本**（入站心跳 / 写卡 / 写卡失败），由 `/larkdeck status` 读
  panel.py      面板数据层（推理轮 / 工具 / 回合结局写入 → 卡片面板读取；
                按会话分桶 + chat_id→session_id 确定性归属，拿不到才退回「最近活跃」）；
                R11-A7 起还持有**正文累积**（单独的小仓库 + 自己的「最近活跃」盒子，
                只服务正文净化的前缀判据，**不参与**面板的回合切换与归属回退）
  hooks.py      官方钩子订阅（7 个观察型钩子，清单见 compat.OBSERVED_HOOKS）：
                只写内存、异常自吞、永不返回 directive
install.sh    安装脚本（默认软链；NAS 用 --copy，其 FILES 数组是手动的，新增模块要同步）
docs/         踩坑与开工索引（lessons）、指标钩子原理、部署与迁移步骤、
              同类插件横向对比与 ROI（plugins-compare）、
              当前分阶段方案（plan-v1，已过三路审计）
tests/        见「验证」
```

## 约定

- `LarkDeckMixin` 的方法用 `_ld_` 前缀；卡片追踪状态挂在 `self._ld_state`。
- 卡片按钮 value 一律带 `larkdeck_action` 键，由重写的 `_on_card_action_trigger` 拦截；
  不带这个键的点击必须原样交给 `super()`。
- 界面文案只从 `i18n.t()` / `i18n.i18n_text()` 取，不硬编码中文字符串。
  i18n 只覆盖界面文案，AI 生成的正文不翻译。
- 插件配置路径是 `plugins.entries.larkdeck.settings.<key>`，由 `register()` 里的
  `_apply_ctx_settings()` 经官方 `ctx.get_config()` 读入。Hermes **从不**调用
  `configure()`（它只是自有运行时入口，单测在用）。取值优先级：环境变量
  `LARKDECK_<KEY>` > config.yaml settings > `_DEFAULTS`。新增配置项必须同时加到
  `_DEFAULTS` 和 `plugin.yaml` 的 `config_schema`；未接入业务的配置项要在 README 标 no-op。
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
    每帧**元素写**预算 2 次（常量 `_CK_WRITES_PER_FRAME`；卡级上限 10 次/秒 × 帧窗口 0.25s）；
    R7 起再加一次**会话预览**写（`card.settings`，`_CK_SUMMARY_INTERVAL = 5s` 限频 ⇒ 平均
    ≈0.2 次/秒，且**不重试**）⇒ 折算 ≈8.2 逻辑写/秒 < 卡级上限 10 次/秒；
    ⚠️ 不是 HTTP 调用数：元素写撞限流时同一请求退避重发最多 4 次，而预览**不重试**
    ⇒ **单帧最坏 9 次调用 / 退避 2.0s**（切卡帧另算：封卡 patch + 建实体 + 发实体卡也都能重试）；
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
  不许让版本段静默消失）、生效传输、钩子挂载数，以及三条记录（入站心跳 / 写卡 / 写卡失败）。
  四条纪律：
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
  ⚠️ **这三条记录是进程级全局、跨会话共享**（`context._STATUS` 是模块级 dict ⇒ 并发时卡片上的
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
- **正文净化 = 有证据地剥掉核心叠加的工具进度块**（R11-A7，`adapter._strip_core_progress`）。
  起因：native 流式下核心会把工具进度行**合成进同一帧**
  （`"\n\n---\n".join((accumulated, progress))`），而我们按契约渲染整帧 ⇒ 那几行出现在卡片正文里。
  用户明确要求**一个核心配置都不动**（不改 `display.platforms.feishu.tool_progress`）⇒ 只能在插件侧做。
  判据是**证明**不是猜：`帧文本 == 我们的正文累积 + "\n\n---\n" + 尾巴` ⇒ 尾巴只可能是核心加的
  （模型写下的每个字节——**包括它自己写的 `---`**——都在累积里）。四个条件缺一不可：
  ① 有工具窗口（自上次正文增量以来有过 `pre_tool_call`，与核心 `_tool_progress_lines` 同生命周期）；
  ② 累积**完整**（没被 `_MAX_ANSWER_CHARS` 冻结——残缺的累积仍可能是前缀，拿它当证据会吞正文）；
  ③ 累积非空且是帧文本的前缀（归属错/回合错/核心换了累积都对不上）；
  ④ 尾巴以分隔符开头**且分隔符之后还有内容**。
  任何一条不成立 ⇒ **原样渲染**（fail-open：那一次进度行短暂可见，核心下个正文增量到达时自己会清）。
  ⚠️ **只剥后缀**：`ck_offset` 等偏移都指向正文内部，剥后缀不会让任何偏移失效（R4 卡链的前提）。
  ⚠️ 旧版（`8f81b4d` 移除）是**按分隔符切**——那是猜，模型写一条 markdown 分隔线就会把后半段答案
  吞掉、而且核心对 finalize 是乐观记账、不会再补发。判别力由变异 `R11-1..R11-7` 七条守住
  （含「按分隔符切」「不完整也剥」「没有工具窗口也剥」三条反例），
  真机凭据是自检行里的 `正文剥进度=N`（卡片读不回来，这是唯一凭据；没有它真机没法验）。
- 页脚指标是进程内全局（钩子记「最近一次 API 请求」），多会话并发共享同一快照；
  要按会话隔离得从钩子载荷的 `session_id` 分桶（未做）。
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
python3 tests/mutate_check.py      # 变异验证器：撤掉每条修复必须变红（改断言后必跑）
```

**改了任何断言，都要跑 `tests/mutate_check.py`。** 它是本仓库「先写变异，再写断言」的落点：
清单里每条变异 = 一处「把某条修复撤掉」的定向改动，判定标准是**至少一个门禁变红**。
四门禁全绿、却没有对应变异变红 ⇒ 那条断言**没有判别力**（本项目头号缺陷类型，见
`docs/lessons.md` 推论 6/7/8）。三条已固化的历史形态：改常量口径后被同文件旧赋值覆盖、
「真对象返回空列表」型断言（被观测对象要**真的缺东西**才有判别力）、`isinstance(x, int)`
这类恒真断言。`-k <子串>` 只跑一部分。
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
⚠️ **合并 / 变基 / `git apply --3way` 之后必须重跑全量变异**（不是只跑 `-k`）：
2026-09-14 实测 `--3way` 在一个「删除死代码」的补丁上静默取了他们的版本、把已删掉的恒假门禁
**还原回来** ⇒ 对应变异 `CK23` 又变 🟢，而四门禁照旧全绿。冲突解决得再干净也不能替代重跑
（见 `docs/lessons.md` 推论 26）。

没有 CI / lint / formatter，这五个脚本就是全部验证。**一律用 Hermes 自带解释器**
`/Users/Zayn/.hermes/hermes-agent/venv/bin/python3`（系统 `python3` 少了 Hermes 的依赖：
`lark_oapi` 导不进来 ⇒ CardKit 那几条用例会**假红** —— 它们的前提断言（「拿不到 SDK 就
fail-open」）被顺带满足，报出来的失败信息与真实原因无关。2026-09-14 实测踩过一次）。

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
  `--clean-only` / `--no-clean`（清理控制）。
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

- 不往仓库提交任何凭据、`config.yaml`、`.env`、日志或真实 chat_id / open_id。这是**公开仓库**。
- 不在 NAS 上做写操作而不先确认；破坏性操作前先说清范围与回滚点。
