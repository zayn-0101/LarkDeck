# AGENTS.md — larkdeck

> Hermes Agent 的飞书流式卡片插件。中文日常叫法「卡组」。
> 使用者文档在 `README.md` 与 `docs/guide/`；开发文档在 `CONTRIBUTING.md` 与 `docs/development/`；
> 三层文档地图见 `docs/README.md`。
> `docs/internal/`（plans / audits / handoff / compare / lessons / verify-log）是**维护者本地归档，
> 不随仓库发布**；本文件里引用它的路径只在本机有效，别把它们写进对外文档或提交信息。
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
3. **Hermes 私有接口统一在 `compat.py` 登记与隔离。** 新增依赖先登记，按能力探测，
   不把已有兼容债务当成允许继续裸访问的先例。适配器必需 / 可选、点击类属性 / 实例属性、
   显示 chrome、信号契约、reaction 与会话归属分别见该模块的契约清单。
   - 必需接口缺失时拒绝覆盖；可选能力缺失时保留消息回落，并如实报告缺失。
   - `format_tool_event` 覆盖只是兼容保险，不能据此证明正文没有核心进度行；正文来源见下文。
   - `CALLBACK_INSTANCE_ATTRS` 只有登记，**没有类级探测键**；点击路径仍有裸 `self._loop`
     访问，不能宣称实例属性缺失已全面回落。
   - `interrupt_session_activity` 的核心查找名另有静态 best-effort 源码探测；它不能验证
     运行期派发，等价重构也可能误报。覆盖 `_reactions_enabled` 必须尊重父类语义。
   - `build_adapter()` 保存进程级探测快照；现役默认状态卡只显示接管摘要，完整契约与缺失项
     在 `/larkdeck status --detail` 和日志里。未探测、报告缺键与覆盖失败必须可区分，不能写“正常”。
   - 钩子登记清单是 `compat.OBSERVED_HOOKS`，与 `hooks.SUBSCRIPTIONS` 同步；不要另抄字面量。
   历史静默路径与证据边界见本地 `docs/internal/compare/plugins-compare.md` §7.6–7.8；
   登记项不等于已实现，也不等于真机验收。
4. **不假设版本。** 开发机与部署环境可能跑不同 Hermes 版本（容器 / NAS 常是镜像内固定版本），
   升级随时会发生，判据要按当前进程实际值来。
   能力一律运行时探测，不写死版本号分支。
5. **卡片方言不可混用。** 要接服务端点击，必须使用所属方言的声明：
   - **1.0**：`{"tag":"action","actions":[...]}` 按钮行 + 按钮顶层 `value`。
   - **2.0**：组件级 `behaviors: [{"type":"callback","value":{...}}]`；回调值进入
     `event.action.value`，下拉另带 `action.option`，输入框另带 `action.input_value`。
   2.0 卡里放 1.0 的 `action` 行 / 仅顶层 `value` 的按钮会被拒；不要把方言混用误诊成
   “2.0 回调到不了服务端”。默认澄清方言是 2.0，1.0 仍可配置回退，测试必须显式声明方言。
   单选可带自由输入；多选使用 `multi_select_static` + 提交按钮，不加自由输入框。
   待答卡与确认卡必须保持同一方言。形态说明见 `docs/guide/card-capabilities.md`。
   `button` / `select_static` / `multi_select_static` / `input` 的回调到达已有历史真机记录，
   其中 button 于 2026-09-16 补齐；原始凭据见本地 `docs/internal/handoff/handoff-route.md` §12。
   **官方文档、历史探针与本轮真机验证分开标注**；第三方注释不能充当实测证据。
   通用证据纪律见本地 `docs/internal/lessons.md` 推论 38。

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
  cardview.py   现役结构化元素树、面板局部更新载荷、图标与字号契约
  i18n.py       双语文案（飞书原生 i18n_content）
  compat.py     版本 / 能力探测 —— Hermes 私有名的唯一存放处
  context.py    运行时指标（钩子写入 → 页脚读取的进程内全局快照）；
                R9 起还持有**自检账本**（入站心跳 / 写卡 / 写卡失败，R11-C2 起再加运行时长 /
                掉回纯文本 / 错误码 top-N），由 `/larkdeck status` 读
  panel.py      面板数据层（推理轮 / 工具 / 回合结局写入 → 卡片面板读取；
                按会话分桶 + chat_id→session_id 确定性归属，拿不到才退回「最近活跃」）；
                R11-A7 起还持有**正文累积**（单独的小仓库 + 自己的「最近活跃」盒子，
                服务 own 正文与 legacy 净化判据，**不参与**面板的回合切换与归属回退）
  hooks.py      官方钩子订阅（8 个观察型钩子，清单见 compat.OBSERVED_HOOKS）：
                只写内存、异常自吞、永不返回 directive
install.sh    安装脚本（默认软链；NAS 用 --copy，其 FILES 数组是手动的，新增模块要同步）
docs/         文档地图见 docs/README.md：用户文档在 guide/、开发文档在 development/、
              internal/ 是维护者本地归档（gitignore，不随仓库发布；含 plans/audits/handoff/
              compare/lessons/verify-log）。
              **新会话先读 docs/README.md 与 docs/development/testing.md，不要从旧 handoff 起步。**
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
  `_DEFAULTS` 和 `plugin.yaml` 的 `config_schema`；不作用于现役路径的配置项要在 README / 配置参考 / config_schema 描述里标 no-op。
  P2 起配置有**热刷新**通道：`/larkdeck config` 只读展示，`/larkdeck config reload` 从官方
  `ctx.get_config()` 重读进内存（**异常时全有全无**，不是文件系统事务）。聊天侧**没有**写入
  命令：handler 拿不到发送者身份，无法安全授权（安全审计 B1）⇒ 插件**从不**直接写
  `config.yaml`、也不调用 `ctx.set_config()`；写配置走官方 Hermes CLI / 配置文件，再 reload。
  官方 ctx 的**只读**句柄存进程级共享盒子 `adapter.PLUGIN_CTX`（命令可能来自旧世代模块对象）。
- 视觉三键的**现役默认值**（2026-09-24 起）：`visual_engine="structured"`（`legacy` 配置键
  **已退役**：设了只留一条 WARNING，行为仍是 structured；真正回退要 revert 到 v0.7.0）、
  `card_status_header=false`（用户口径「顶栏默认不显示」）、`show_reasoning="auto"`
  （跟随 Hermes `display.show_reasoning` / 平台覆盖；也接受 `on`/`off` 与旧布尔 `true`/`false`）。
  Hermes 未开启 `plugins.stream_reasoning_deltas` 时 auto 按关闭处理，并在 `/larkdeck status --detail` 说明原因。
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
  （不是增量）；CardKit 更新元素、patch 路径整卡替换。回合状态挂 `self._ld_streams`
  （key = `chat:turn_id`），现役结构化帧与心跳共用回合写锁。
- **现役 native 路径是 structured + CardKit。** `_ld_visual_engine()` 始终返回 structured；
  `_ld_stream_frame()` 在旧传输分流之前进入 `_ld_stream_frame_structured()`，新回合直接建实体。
  `native_transport: patch` **不会切换现役路径**；状态标题仍读这个配置值，不能拿标题证明
  实际传输。旧 markdown 渲染器和 patch 车道留作兼容 / 降级；`visual_engine: legacy`
  只留退休告警，真正回退旧引擎需回到 v0.7.0。改默认值须先对抗审计与真机生产路径探针，
  同步 `_DEFAULTS` / `plugin.yaml` / `docs/guide/configuration.md`，保留默认值门禁。
  **当前流程与传输边界以 `docs/development/architecture.md` 为开发入口。**
  - seed 不画上一回合过程数据，正文留空，建 `panel` 与加载提示；首段正文到达后删除提示。
    `ck_elems` 从真实 JSON 抽出正文 / 页脚等流式 id；外层面板用 `ck_has_panel`，提示用
    `ck_loading`，不能拿不含它们的 `ck_elems` 判存在性，也不能恢复旧 `ck_panel` 布尔字段。
  - 面板通过 `card.batch_update` 的 `partial_update_element` 更新 header / 子树；页脚另走
    batch，正文最后用 `card_element.content` 写入。旧 `panel_body` / `panel_tools` 两块
    markdown **不是现役面板形态**，也不再有“所有结构只能建实体时定死”的约束。
  - 要保持流式就不能整卡替换：`message.patch` / `card.update` 会关闭流式会话，随后元素写
    可得 `300309`；元素级局部更新可以改结构。收尾先取消心跳，再整卡替换、关闭 streaming。
    澄清等待边界例外：不关流，成功点击后同卡继续。`/stop` 清状态后，在飞帧不得把它插回去。
  - 所有同回合写者（帧、3 秒面板心跳、澄清刷新）共用回合锁；序号单调递增，UUID 不复用。
    心跳锁忙就跳过，不排队；新回合 seed 的旧快照窄闸门只作用于 seed 后的 tick / 心跳，
    不能直接套到 live 帧。外层面板中间更新不写 expanded；推理子面板只在首次出现时发
    展开态，后续保留用户选择。详细行为由 `check_cardview` 与真机探针共同验证。
  - 正文失败无法恢复时返回 `False` 交核心 edit/send；卡级死法可同卡降级（标 degraded、清
    `card_id`、整卡续写）；装饰失败不能吞正文。撤回类错误停止写入并清追踪，不由插件补发。
    不同入站回合各自一张主卡；同轮 fallback 合卡依赖上游回合标识，不能跨回合拼答案。
  - 上游默认 `⏳ Working — ` 心跳由 `send()` 返回 `success=True, message_id=""`；
    不把主卡 mid 交给无元数据的上游 edit。唯一 active structured 主卡只更新面板标题；
    无 active 时复用专用静默卡。多 active、已降级、无 panel 或刚终态窗口内抑制；
    generic 心跳不识别。毫秒级 live 归属竞态仍是已知限制。
  - 结构化面板保留最近最多 20 步 / 20 轮；`max_panel_steps` 可进一步收紧。
    `max_reasoning_chars` 按轮截断，`max_tool_result_chars` 截断 Result / Error 块。
    参数预览先脱敏；上游 80 字符截断时用 `_preview_value()` 有界提取，绝不回显 JSON 原文。
  - **写入预算必须分车道。** 旧 markdown CardKit 的装饰 batch + 正文、限频预览和滑窗守卫
    由 `_ck_plan` / `_ld_ck_maybe_summary` 等实现；结构化路径另有面板局部更新、页脚 batch、
    正文及加载提示删除，**不能宣称现役只有 2+1 次写或有统一 8.2 次/秒上界**。
    改旧守卫时仍须保留：跳过装饰不记已写 / 不跳正文与预览、失败重试计入配额、每帧逐笔对账；
    配额口径与状态卡的写卡帧数不同。`B1-*` 变异与 `_ck_window_*` 用例是这些边界的判据。
- **卡片字段与观感纪律。** 新增元素先核对官方 2.0 字段表与图标枚举，再同步
  `check_cardview._assert_panel_element_fields()` 白名单。未知字段会整卡被拒，不是被忽略。
  - 静态工具图标默认 `_outlined` + 统一灰；运行中用自研动图。`ICON_ALIASES` 与 CLS 对齐，
    `_VERIFIED_LINEAR_TOKENS` 是已核实 token 白名单；`tool_row_icon: emoji` 保留降级选择。
  - `markdown` 没有 `text_color`；正文上色用 `<font color='…'>`。`div.icon` 在组件级；
    `plain_text.text_weight` 会被拒；客户端忽略部分十六进制颜色，灰色用 `grey`。
    工具细节用 markdown / plain_text；错误与结果块用逐行 inline code，不能指望 fenced
    code 或 `div.text=lark_md` 接受 text_size。语言边界按本文件 i18n 条款。
  - `panel_color_tags` 只作用旧 markdown 函数；structured 自行生成颜色，false 对其无效。
    不能再把它写成现役去色开关。这个限制已在 README / 配置参考 / 清单描述对齐。
  - 已知系统提示与命令回执不渲染回合面板 / 状态头 / 页脚。header 匹配与整段相等清单
    见 `_LD_SYSTEM_NOTICE_PREFIXES` / `_LD_SYSTEM_NOTICE_LINES`；纯句模板只允许整段相等。
    `notify=True` 也用于真实模型终稿，不能据此判非回合；负清单仍可能漏回执，真实回答
    首行误命中 header 则可能被误判。排查用 `send 判定 turn=` 日志，不能扩大匹配来掩盖问题。
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
  最小复现、语料数字、审计条目见 `docs/internal/plans/plan-v1.md` 的 R6a 一节。
- **字节闸门量的是「要发出去的那一份」**（推论 13 的口径病，R6a 审计低-1/低-2 收口）：
  `adapter._sanitize_for_send()` 是卫生的**唯一入口** —— 它做卫生，然后**对卫生后的文本**
  量一次 `_card_body_bytes`，超了 `FEISHU_CARD_BYTE_LIMIT` 就**退回原文**并留一条限流告警。
  为什么必须有：卫生对标题密集的正文**只会变长**（每个降级标题 +2 字节），存在
  「卫生前过闸、卫生后超限」的窄带（审计构造过 `128000 → 128080`），而收尾帧原本
  **一道闸门都没有** ⇒ 超限的卡被拒 ⇒ fail-open ⇒ 用户从「一张卡」掉成「若干条纯文本」。
  判据与守卫必须同口径，否则两个都对、合起来还是漏（`docs/internal/lessons.md` 推论 13）。
- **`/larkdeck status` 是自检入口**（`ctx.register_command`，公开 API）。默认七行概览：
  接管、钩子、推理显示、最近写卡、失败 / 回落、错误码与运行时长；`--detail` 展开能力 / 链路、
  契约与六条完整记录。两种视图同源，每次查询都把详细事实写日志；解析不到概览所需字段时
  回退完整视图。版本现读 `plugin.yaml`，读不到就明说，不能复制常量或让版本段消失。
  未知参数明确报错；有明确异常才带 `⚠️`，无记录与零计数分开，绝不写“正常 / 健康”。
  `/larkdeck config` 是同一条命令里的**只读**配置视图（见上面的配置刷新纪律）。四条纪律：
  ① **没记录就写「无记录」，绝不写「正常」**（永远说健康的自检 = 绿而无判别力）；
  ② **只统计真的写出去的动作** —— 节流跳过的帧与文本没变的去重帧不算（一个字节都没写）；
  ③ **口径是「帧」不是「次」**：结构化帧可能包含面板、页脚、正文和提示删除等写入，
     seed 是建实体 + 发消息，失败还可能重试；旧车道另有限频预览 ⇒ 卡片与文档一律说
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
    （超出并进 `other`）；计数挂在 `_ld_stream_fail` 一个收口点，不在各调用方重复记账；
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
- **legacy 正文净化必须有证据**（`adapter._strip_core_progress`），不能按分隔符 split/rsplit，
  也不能为了去进度行改 Hermes 的 `display.platforms.feishu.tool_progress`。
  - 收尾帧一律不剥；`tool_pending` 与累积完整性必须成立。非空累积还必须是帧文本的前缀，
    后缀以 `_CORE_PROGRESS_SEP` 开头且其后有内容，才允许只剥后缀、不改变正文内部偏移。
  - 空白累积有独立分支：用已见工具名、运行状态与核心进度行形状证明整段是进度，才返回空串；
    上游新增 verb 或任一判据不成立就原样返回，不能把空累积当作所有文本的证明。
  - 钩子异步队列可能滞后。中间帧短暂截短可被下一帧修复，finalize 没有下一帧，必须挡住
    不可逆吞正文。核心失败后同一合成文本重发 finalize 的例外在 legacy 下可能保留进度行；
    own 路径另用 F4 护栏拒绝持久化。剩余前提盲区见本地 `docs/internal/plans/plan-v1.md` 附录 F。
  - `正文剥进度=N` 只属于 legacy 净化计数；N=0 不证明失灵，own 本来就不靠剥帧。
    判据与变异见 `check_own_body.py`、`check_hooks.py` 与 `R11-*` / `PA-*`；历史分析见
    本地 `docs/internal/lessons.md` 推论 37，改外部前提时要独立核对核心实现。
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
  `~/.hermes/model_aliases.json` 子串匹配、热更新）→ **models.dev 真名**（Hermes 官方解析器
  `agent.models_dev`，只读本机 `models_dev_cache.json`、不发网络请求；`opencode-go` 的
  `deepseek-flash` ⇒ `DeepSeek V4.1 Flash` —— 该 provider 条目 `name` 就是 ID 时，会扫注册表找
  同名条目里的真名）→ 确定性格式化（token 表 / 版本号 / 参数量 / 尾部日期戳）。加名字只改这几处，
  **不要在页脚里再拼原始 ID**；任何一层坏掉只许退化成下一档，不许把页脚弄没
  （`test_v4_62` + 变异 `V4-62`/`V4-63`）。
- 面板数据策略与页脚不同：`panel.py` 按 `session_id` 分桶；归属优先用
  `pre_gateway_dispatch` 观察到的 `chat_id -> session_id` 映射（确定性），拿不到才退回
  「最近活跃」。**回合状态色**（ok/error/stopped）走同一套归属，颜色载体是
  `collapsible_panel.border.color`（见 `docs/internal/metrics-and-hooks.md`）。
  钩子回调纪律源自 `pre_tool_call` 是 **fail-closed**（回调卡住会阻止工具执行）：
  只写内存、微秒级返回、异常自吞、**永不返回 directive**。

## 验证

命令、九步分工、通过标志与变异协议的现役入口是 `docs/development/testing.md`。
公开 CI 只运行 `tests/check_docs.py`；插件完整门禁仍在开发机执行，没有 lint / formatter。

```bash
PY="${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python3"
$PY tests/check_docs.py                 # 文档修改先跑，必须 DOCS OK
$PY tests/run_fast.py --full            # 官方门禁入口，恰好 9 步全部 [OK]
$PY tests/mutate_check.py --preflight   # 改锚点后先对账；不代表门禁或变异通过
$PY tests/mutate_check.py --delta       # 改过断言必须跑；未改区域可按三重指纹跳过
$PY tests/mutate_check.py               # 合并 / 变基 / git apply --3way / 大改后全量
$PY tests/mutate_check.py --ledger-status
```

- 一律用 Hermes 自带解释器、脚本自己的 `__main__` runner。系统 Python 缺 SDK 会假红，
  pytest 的 logging / 全局状态不适配这些脚本；先换回受支持入口再判断回归。
- `test_units.py` 必须证明加载的是本仓库；变异快照目录必须叫 `larkdeck`，防止测到另一份
  未变异代码。钩子集成用加载器命名空间 `hermes_plugins.larkdeck.core.context`。
  `check_override.py` 才能证明真注册表覆盖、配置桥接与字段透传；单测替身不能替代它。
- 改锚点先 `--preflight`，再 `-k` 定向复跑；锚点必须存在且唯一、落在用例会走的语句上。
  只有至少一个门禁 **red-assert** 才证明判别力；green、red-crash、找不到锚点都不能算已验。
  `ERROR ` 优先于同次 `FAIL` 判崩溃；只命中对照的 `-k` 退出 2，不能冒充变异证据。
- “先写变异，再写断言”：先复现缺陷，证明错误实现能进入错误分支，再验证修复。
  恒真断言、前面失败遮住后面判据、注释替换与等价替换均不能充数；关键性质单独成条。
  `MUTATIONS` 不许放对照 `expect==`；变异未生效要如实归因。历史形态见本地
  `docs/internal/lessons.md` 推论 34–38，本文不再复制事故叙事。
- 增量账本按 **代码区域（完整 old 定位、±15 行）× 当年命中用例名集合 × helper/fixture**
  三重指纹判跳过。`inherited(ref)` 不等于实跑；`--seed-inherited` 不能证明测试套件未变。
  不必每版重复全量，但 `full_audit_at` 缺失 / 过期时需补全量；合并后的全量不能用 `-k` 替代。
  所有 `--shard`（含 1/1）都不自行盖全量章；用 `tools/merge_ledger4.py` 核对当日 red-assert
  名字并集、对照零假红与现场三指纹后合并。账本 / 运行日志只在本机，不提交。
- 测试等待必须有界，用 `_await_event(event, timeout, what)` 把超时转断言。
  存量 `await release.wait()` 仍有 3 处，处于带有界等待或 finally 释放的用例中；全仓静态
  门禁尚未实现，不得写成已完成。变更这些用例时复核短路后是否仍能释放。
- 改 `cards.py` 或卡片结构，须跑 `tests/probe_render.py` 真机渲染；降载档位 / 状态色变化
  还要跑 `--stop-redraw`。本地断言验结构，飞书返回码验接收，客户端观感与点击到达另验，
  三者不能互相替代。其他探针参数与分工见测试文档。
- 探针必须先本地自检：方言、字段、字节 / 元素预算、回调 value 共用 `cards.is_probe_value()`。
  每次发送使用新 UUID；CardKit 生产探针也要记入消息账本。清理只按账本 `message_id` /
  精确 card_id，不按时间窗删“最近消息”；实体卡对象可能仍在远端，不能宣称已全部清掉。
  真机发送与清理遵循本会话授权边界，不因脚本存在而自动获得权限。

## 部署

部署拓扑与命令见 `docs/development/release.md` 与 `INSTALL.md`；本文件不记录任何具体机器、
地址或路径。两条要点：

- 软链安装（`./install.sh`）改代码后须重启网关；先查软链是否指向 `.deploy`，开发树与部署树
  不可混淆。容器 / NAS 用 `--copy`，升级时要先移开旧目录。
- 容器镜像内的 Hermes 源码是**非持久**层，重建即回滚；容器启动脚本如果会重装旧插件，
  必须先拆掉，否则每次重建都会顶掉 LarkDeck。

## 红线

- 不往仓库提交任何凭据、`config.yaml`、`.env`、**可能含凭据或真实 ID 的运行日志**、
  真实 chat_id / open_id，以及维护者的机器名、地址、绝对路径等运维信息。这是**公开仓库**。
- `docs/internal/`（规划 / 审计 / 调研 / 交接 / 验证日志）**一律不进仓库**，已 gitignore；
  它只作为维护者本机的开发依据。对外文档不得链接或复制其中的内容。
- ⚠️ `tests/mutation-verdicts.json` 与 `tools/release-v0.7.*.py` 在 v0.7.9 之前的 tag 里是**被跟踪**的：
  `git checkout v0.7.8` 之类会把它们恢复成旧版，切回 `main` 时又会从工作树删掉。要切旧 tag 前先备份
  （例如 `cp tests/mutation-verdicts.json /tmp/`），或事后用 `git show <tag>:<path> > <path>` 还原。
- 不在生产 / 容器环境做写操作而不先确认；破坏性操作前先说清范围与回滚点。
