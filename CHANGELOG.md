# 更新日志（CHANGELOG）

本项目按版本倒序记录**用户可见**的变化。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> 每个条目的门禁数字都是**当次实测**抄下来的（本项目的纪律：`docs/lessons.md` 第四节）。
> **v0.4.0（2026-09-15）**：R11 的 Phase A/B —— 双加载（同进程两遍插件发现）的家底清点与根治、
> 正文净化（**有证据**才剥核心叠加的工具进度行）、`300315` 容量码的解析契约、滑窗写入守卫。
> 配置键**零改名、零默认值翻转**（所以还不到 1.0.0 —— 那条判据还要求跨过一次上游升级）。
> 真机项目另附真机探针结果。
> **v0.5.0（2026-09-17）**：效果债交付（脱敏 / 澄清卡外显 / 卡片短码 / status 三条）+
> P1/P2/P3（设备字号、能力探测可见、AP-lite 主题、聚合诊断、只读配置刷新、cron standalone
> 卡片投递）。**新增 `theme` 默认值，属于用户可见默认观感变化**；配置键无改名。
> 已知待验：`text_profile` / `ap_lite` 真机视觉、无网关 cron 真机投递。


## [0.7.8] - 2026-09-24 思考面板：手动展开/收起 + 轮结束自动折叠 + show_reasoning auto

### 新增 / 变更（用户可见）

- **`show_reasoning` 默认改为 `auto`**：跟随 Hermes 的 `display.show_reasoning`
  （`display.platforms.feishu.show_reasoning` 平台覆盖优先），用户在飞书 `/reasoning on|off`
  或改 Hermes 配置即可生效，LarkDeck 无需改配置；`on`/`off` 仍可显式覆盖，旧布尔
  `true`/`false` 继续接受。Hermes 未开启 `plugins.stream_reasoning_deltas`（收不到
  reasoning delta）时按关闭处理，并在 `/larkdeck status` 写明原因。
- **每轮思考是独立的可折叠子面板**：
  * 标题统一为「第 N 轮思考 · X.Xs」；
  * 当前轮、已结束轮都由用户手动展开/收起；已存在的子面板在后续帧**不再重放
    `expanded`**，所以手动状态不会被 token 顶掉；
  * 一轮结束时**立刻自动折叠**：把子面板的 element_id 从 `live` 换成 `folded` 并只带
    一次 `expanded=false`（真机探针结论：同一 element_id 上改 `expanded` 会被客户端手动
    状态忽略；换新 id 才会按 false 重建）。
- **折叠提示去掉前导省略号**：`…已折叠 N 条早期思考/工具记录` → `已折叠 N 条早期思考/
  工具记录`，`…更早的 N 轮已折叠` → `更早的 N 轮已折叠`；「…已省略 N 字符」保留（那是
  被截断的尾巴，语义不同）。

### 门禁与证据（2026-09-24，定向）

- `test_units.py`：**342/342 passed**（新增：轮次 id 换代与 expanded 记账、标题/折叠文案、
  `show_reasoning auto/on/off` 与无 delta 诊断）。
- `run_fast.py --full`：8 步全 `[OK]`。
- `mutate_check.py --preflight`：**599/599 可用**（变异 587 + 对照 12）；
  新增 `V076-1..4`（换 id 自动折叠、手动状态保持、auto 跟随 Hermes、无 delta 隐藏）
  与 `V076C-1..4`（澄清修复）均实测 `red-assert`。
- 真机部署与视觉验收记录见 `docs/releases/v0.7.8.md`（本版发布时补）。


## [0.7.7] - 2026-09-24 澄清选择后主卡即时更新

### 修复（用户可见）

- **点选澄清卡后，主卡不再停在 `clarify · Running` / `💬 等待你的选择...`**：
  核心在 clarify 边界会把主卡收尾成「等待你的选择」占位；而 clarify 是**阻塞工具**，
  用户点选后还要等工具返回（真机实测 36.96s）才发 `post_tool_call`，这段时间主卡
  一直看起来像「点了没反应」。现在：
  1. 结构化车道识别 clarify 边界，**不关主卡流**，正文改写为「⏳ 等待你的选择…」；
     只要收尾帧里面板里 `clarify` 仍在 running 就按边界处理（模型先写半段正文时
     文本不是占位，真机复现过——只认占位会漏掉这一类）；
  2. 点击成功（`resolve_gateway_clarify` 返回 True，含输入框提交）立即把面板里的
     `clarify` 步骤乐观标为完成；点「其他」则保持 running 并显示「等待输入」；
  3. 异步把「✅ 已收到你的选择，正在继续…」写回**同一张主卡**（元素通道关闭时回落
     同卡整卡 patch）；后续 delta 继续更新这张卡；
  4. 真正的 `post_tool_call` 到达时覆盖乐观状态并复用同一行，不产生重复的 clarify 行
     （失败/取消也能覆盖）。提交未生效的迟到重复点击仍然只回 toast，绝不换卡。
- **多选澄清卡增加「提交」按钮**：`multi_select_static` 不会自动提交，现在把它放进
  官方 `form` 容器并加一个 `form_action_type: submit` 的提交按钮；提交按钮必须挂官方
  `behaviors.value`（只挂历史 `value` 时真机会落到内置 `/card` 合成命令），提交时从
  `action.form_value["clarify_options"]` 读选中值、拼成网关规范的 JSON 数组。
- 修复只影响结构化 CardKit 车道与 2.0 澄清卡；旧 `patch`/降级车道与 1.0 澄清卡行为不变。

### 门禁与证据（2026-09-24，定向）

- `test_units.py`：**339/339 passed**（新增：面板乐观更新与真实结果覆盖、边界保留主卡流、
  有累积正文时的边界识别、多选表单值解析）。
- `run_fast.py --full`：8 步全 `[OK]`。
- `mutate_check.py --preflight`：**595/595 可用**（变异 583 + 对照 12）。
- 新增变异 `V076C-1/2/3/4` 均 `red-assert`（定向跑 4/4）。
- 真机（2026-09-24，用户目视）：单选点击后主卡立即从 `clarify · Running` 变为已收到选择；
  多选勾选后点「提交」成功提交（此前无反应是提交按钮缺 `behaviors.value`）。
- 真机部署与用户点击验收记录见 `docs/releases/v0.7.7.md`。


## [0.7.6] - 2026-09-24 Hermes 0.21.4 延迟平台兼容

### 修复（用户可见）

- **Hermes 0.21.4 升级后不再出现“插件加载超时、飞书退回纯文本”**：0.21.4 把 bundled
  平台注册成 deferred loader；旧 `register()` 在插件加载 worker 里直接
  `platform_registry.get("feishu")`，与主线程持有的 discovery RLock 互等到
  `plugins.load_timeout_seconds`（默认 10s）超时，平台接管被丢弃。
- **插件侧三分支兼容，不依赖任何 Hermes 全局配置**：
  1. 内置 entry 已具体（老版 Hermes / 热进程）→ 直接复用；
  2. 内置平台是 deferred（0.21.4+）→ 直接导入 bundled 模块并捕获 `register_platform`
     全量参数，绕过 registry 锁；
  3. 老版 registry 没有 snapshot API → 回落 `get()`（当时没有 deferred loader，不会死锁）。
- **升级后无需改配置**：上一轮临时使用的 `plugins.load_timeout_seconds: 0` 已移除，
  默认 10s 下 LarkDeck 约 0.6s 完成加载并接管。

### 门禁与证据（2026-09-24）

- 定向：`test_units 336/336`；`check_override OVERRIDE OK`、`check_hooks HOOKS OK`、
  `check_clarify_e2e CLARIFY E2E OK`、`check_cardview CARDVIEW OK`、
  `check_cls_alignment --require CLS ALIGN OK`。
- `mutate_check --delta`：**25/25 red-assert + 12/12 对照全绿**（跳过 554 条锚点区域
  未变、上轮全量已验的条目）；`--update-ledger` 已写入；`--preflight 591/591`
  （变异 579 + 对照 12）。
- 真机：Hermes 0.21.4 默认 10s、网关重启后自检通过
  （`内置 'feishu' 为 deferred，已直接导入 bundled 模块捕获完整 entry`），启动卡片正常发出。


## [0.7.5] - 2026-09-23 心跳进面板 + seed 防闪旧

### 修复（用户可见）

- **长任务 `⏳ Working — N min…` 不再新建中间卡**：默认模式下，上游每 180s 的
  `_interim_send` 心跳被 `send()` 吸收进当前结构化主卡的 `collapsible_panel` 标题
  （`⏳ Working … · 原摘要`），并返回空 `message_id`，让上游永不进入 `edit_message`
  整卡 patch 路径（避免关闭 CardKit 流式会话/清空答案）。后续 3s 面板心跳和结构化帧
  都保留这条标题；finalize 终卡会清掉它。无 active 主卡时只建**一张**专用静默卡并复用；
  多条 active、degraded、面板关闭、终态安静窗口内一律抑制，不新建卡、不碰 answer。
- **追问不再先闪上一回合工具步骤**：新 consumer 回合的 seed 帧不再读 `_panel.snapshot()`
  的上一回合过程数据（结构化 / legacy CardKit / patch 三个 seed 分支统一空面板壳）；
  seed 后、`on_stream_start` 清空前，插件 3s 面板心跳和上游心跳合卡也被面板闸门挡住
  （只跳过装饰，不写旧 panel）；`on_stream_start` 后的首帧起照常画当前回合数据。
  CardKit 面板元素结构保留，golden 夹具按契约更新（首张实体卡为「执行详情」空壳）。
- **声明不做**：不同入站回合各自一张卡；18:43 后台进程完成通知触发的自动回合是**独立
  新回合**，插件侧不做跨回合合并/抑制（会吞掉真实回答）。同轮 native finalize 失败/
  boundary 多卡需上游接口（`_stream_turn_id` / `get_stream_message_id`）后再评。

### 已知限制

- 只识别默认心跳字面量 `⏳ Working — `（`_interim_send=True`）。`long_running_notifications:
  generic` 的任意文案仍走 v0.7.4 的静默卡，不进面板；等上游独立 metadata 标记后再收口。
- **首个 live 帧抢跑**：面板闸门只覆盖 seed 后的 3s tick 与上游心跳（秒级主因）；若首个
  带正文的 live 帧在 `on_stream_start` 派发前到达（跨钩子队列无顺序保证），它仍可能读到
  上一回合 panel。无上游同命名空间 turn marker 前不做 live 帧 capture-only 闸门（会把
  “seed 晚于 begin_turn”的当前面板误判成旧数据）。真机若复现，按上游接口项推进。
- **无 active 长任务的专用心跳卡**：native/结构化主卡不可用时只建一张静默 Working 卡并
  复用；真实终稿走另发的答案卡，专用卡不会被自动删除/合并（上游拿不到它的 mid，插件也
  没有 delete_message）。属已知观感取舍，行为有回归测试钉住。
- 上游 `edit_message` 心跳在本设计下不应发生；插件对已不在追踪表里的 `⏳ Working — …`
  非 finalize 编辑做 no-op 防御，绝不回落官方 update。

### 门禁与证据（全量跑后回填）

- 定向：`test_units 331/331`、`mutate_check -k V075` **完整四门禁 38/38 red-assert**、
  `--preflight 589/589`（变异 577 + 对照 12）；`run_fast --full` 8/8；golden `--check` 一致。
- 全量 6 分片（冻结提交 `3dce128`）：**577/577 red-assert**、12/12 对照、🟢0/💥0/❓0、
  `tree_dirty=false`、`n_inh=0`、墙钟 **1282.4s**（22:11:59→22:33:21）；证据
  `~/.larkdeck-scratch/v0.7.5/evidence-3dce128/`、`docs/audits/v0.7.5/p3-full-run-3dce128.md`。

### 发布结果

- tag `v0.7.5` = `7ccffa0`；GitHub Release https://github.com/zayn-0101/larkdeck/releases/tag/v0.7.5；`.deploy` 指向该提交；网关重启自检通过 23:21:31。
- 用户真机终验三项通过：长任务无独立 Working 卡且标题进主卡面板、追问不闪上一回合工具、真实回合 ✅ 保留。

## [0.7.4] - 2026-09-23 系统/命令提示静默 + 长任务面板

### 修复（用户可见）

- **Hermes 本地化系统/命令回复静默**：`/reset`、`/new`、`/reload-mcp`、`/reload-skills`、
  `/resume`、`/stop`、`/reasoning`、title/footer/model、approve/deny 等命令回复命中登记
  header 后整卡不渲染面板/状态头/页脚；`/larkdeck` 自诊卡同样静默。根因：这些**最终回复**
  与真实模型 non-native 终稿同样带 `notify=True`，不能据此判非回合，只能按上游 locales
  内容登记（前缀 + 纯词整段相等）。**真实回合的 `✅ 已完成` 一个字不动**；
  未登记的命令回执仍可能带 ✅（负清单残余，发现即登记并重跑全量）。
- **长任务/多卡中间卡面板空白**：终局帧快照已无过程数据（多回合交错/原生流回退把 panel
  顶掉）时，不再渲染「有标题、展开空白」的空面板 shell；状态色由页脚承载。非空面板行为不变。

### 门禁与证据（全量跑后回填）

- 定向：`test_units 301/301`、`--preflight` 动态总数全可用、`-k V074` **8/8 red-assert**
  （前缀删除、notify 反转、startswith→in、整段相等放宽、approve/deny 删除、空 shell 回归、
  过度修正吞非空面板）；`V073-2*` 21/21 仍 red。
- 全量 6 分片（冻结提交 `5b95374`）：**539/539 red-assert**、12/12 对照绿、🟢0/💥0/❓0、`tree_dirty=false`、`n_inh=0`、墙钟 **1398.9s**（17:38:59→18:02:18）；`--preflight 551/551`。证据：`~/.larkdeck-scratch/v0.7.4/evidence-5b95374/`、`docs/verify-log.md`。

### 探针与残余

- `show_reasoning=true` 嵌套面板探针（mid `om_x100b640d4e092480de298ce9014a9a3`）：用户
  2026-09-23 确认**内层能展开** ⇒ 不改 cardview、不重生成 golden，默认 `false` 维持。
- 结构信号（上游 `EphemeralReply`/slash 派发打点）未进 metadata，本批未采纳；作为后续批次
  替代 locales 枚举的优先调研项。

## [0.7.3] - 2026-09-23 面板细节与系统提示口径

### 变更（用户可见）

- **工具细节行与 Error/Result 块**：`text_size: x-small`。细节行的 `markdown` / `plain_text` 两宿主2026-09-23 真机确认更小；Error/Result 块的 fenced 代码块字号被飞书客户端固定死，改为 **`markdown` + 逐行 inline code + `x-small`**（用户选定形态③）后标签与每行代码一起变小、可读。字面量，不随 `text_profile` 放大；无运行时自动回退，宿主行为变化需改代码并重跑全量。见 `docs/verify-log.md`。
- **系统提示去状态词**：Gateway online/restarting、Session database、Hermes update、cron/后台完成等已知系统提示整卡不渲染面板/状态头/页脚（不再出现 `✅ 已完成`）；**真实回合卡一个字不动**。
- 顺手修正历史口径注释（context/i18n/adapter/cards/mutate_check）。

- **门禁（P3 重跑）**：`--preflight 543/543`（531 变异 + 12 对照）；冻结提交 `a2290da` 六分片全量 **531/531 red-assert**、12/12 对照绿、🟢0/💥0/❓0、`tree_dirty=false`、`n_inh=0`、墙钟 **1753.4s**；`-k V073` 32/32 red。证据：`~/.larkdeck-scratch/v0.7.3/evidence-a2290da/`、`docs/verify-log.md`。

## [0.7.2] - 2026-09-22 真机反馈收敛

### 变更（用户可见）· 面板 UX 定版（2026-09-22 用户逐条拍板）

- **面板展开策略（A1）**：面板**运行中展开、收尾折叠**；**嵌套推理轮**按「当前轮展开、已结束轮
  折叠」；**流式中间帧不再带 `expanded`**（⇒ 你手动收起之后，后面的 token 不会把它顶开）。
  新配置 **`streaming_panel_expanded`**（默认 `true`；设 `false` = 回到「全程折叠」）。
  `panel_expanded`（默认 `false`）现在只管**收尾**那一次。⚠️ 「中间帧省略 ⇒ 尊重手动状态」这条
  依赖飞书的 `partial_update_element` 合并语义，用真机探针单独验证（见 `docs/verify-log.md`）。
- **页脚去段前缀 emoji（B1）**：`⏱`/`🤖` 与 `footer_metrics` 的 `⚡`/`🔁`/`🐢` 全部改纯文本
  （`12.3s` / 模型名 / `cache 75%` / `api 7` / `ttfb 0.4s`）。状态词里的 `✅`/`❌`/`⛔` **保留**。
- **工具行状态词（C1 + 默认②）**：运行中 `Running` 改**蓝色**（原来青绿，浅色主题下与成功撞色）；
  **只有成功**换成绿色 **`✓`**；失败 / 超时 / 中止 / 跳过**保留词**（红/灰）。两张状态表同源。
- **工具行运行中图标 = 动图（D1′）**：与正文前的加载指示**共用同一张自研 GIF**（源文件
  `assets/spinner-tool.gif` + 生成脚本 `tools/make_spinner_gif.py` 入库，用自己的 app 上传一次）。
  ✅ **已落库**（用户 2026-09-22「就它」）：`assets/spinner-tool.gif` + 生成脚本入库，`SPINNER_TOOL_IMG_KEY`
  = `img_v3_0215p_a0b0bd11…`（借来的共享 key 降级为**最后回落**）。记录见
  `docs/audits/v0.7.2/loading-asset-v2.md`。
- **加载指示**形状不变（`div` + `custom_icon` + 空格；无文字），只换资产。

### 变更（用户可见）
- **页脚不再有 🔖 短码**（用户 2026-09-21 口径：「我从来没有提过这个要求」）。
  页脚字段 = 状态 · 耗时 · 模型 · ctx 用量（对齐同类插件；**2026-09-22 B1 起段前缀 emoji 已去掉**）。短码只保留在**日志自检行**
  `卡片=<6 位>`；卡片头/正文/面板/折叠提示/收尾 patch **一律不出现**。
  ⚠️ 该条**推翻** v0.5.0 / v0.7.1 里「页脚带短码」的登记（旧行为是内部审计加的，不是用户要求）。
- **页脚显示「模型名」而不是模型 ID**（用户 2026-09-22 口径：「显示现在的好像是模型 ID，我想要
  做成显示模型名」）。**名字的来源**（优先级从高到低）：
  ① 配置 `model_aliases` 精确匹配；② `~/.hermes/model_aliases.json`（`{"子串": "显示名"}`，
  与 `hermes-fry-cards` 同一份文件、改文件即生效）；③ **models.dev 真名** —— 走 Hermes 自己的
  解析器（`agent.models_dev`，只读本机 `models_dev_cache.json`，不发网络请求），例如
  `opencode-go` 下的 `deepseek-flash` 会显示成 **`DeepSeek V4.1 Flash`**（用户实测指出的真名；
  该 provider 条目里 `name` 就是 ID，真名在厂商条目里 ⇒ 会扫一遍注册表找同名条目）；
  ④ 都没有才做**确定性格式化**（token 表 / 版本号 / 参数量 / 尾部日期戳，
  `deepseek-v4-flash` ⇒ `DeepSeek V4 Flash`）。别名文件或数据源损坏只退化成下一档，
  绝不弄坏页脚。
- **加载指示 = aiduPOP `_loading_element`**：`div` + `custom_icon(共享 img_key)` + `text:" "`
  —— **会动、无文字**（用户真机目视确认「① 会动」）。旧版「正在加载上下文…」文案与静态图标
  `time_outlined` 一并撤掉；首字到达即删元素（删失败换号重试、上限 3 次、`300313` 按 msg 判定）。
- **顶部状态条默认关闭**（`card_status_header: false`，用户口径「顶栏默认不显示」）。
- **工具图标**：主表 **28 条逐条等于 CLS**（含顺序），`terminal` 单列登记偏差
  （CLS 表里没有它，而 Hermes 的 shell 工具就叫这个名字）；`tests/check_cls_alignment.py`
  直接解析 CLS 源码比对（CLS 侧改动会红）。
  - **工具行图标定版：飞书官方线性图标做「文本前缀」**（2026-09-22 真机三臂对照选版，
    卡 `om_x100b6414825f3ca8c339ed0a7cef3e9`；我按用户截图逐像素量过）：
    * 甲 元素级 `div.icon`（CLS 同款）⇒ 图标比文字**高 3px**（2026-09-21 那次「偏上」就是它）；
    * **乙 `markdown.icon`（官方文档叫「前缀图标」）⇒ 0px** —— **用户选它**；
    * 丙 `column_set` 居中 ⇒ 1px 但横向空 179px。
    风格 = **线性 `_outlined` + 统一灰 `color:"grey"`**（用户口径：「emoji 花里胡哨、颜色不统一，
    CLS 那种统一颜色更高级」）。token 全部**逐个对飞书官方图标枚举页查证存在**
    （`enumerations-for-icons`，1154 个），名字写错客户端不渲染且不报错 ⇒ 白名单冻结在
    `test_units.py::_VERIFIED_LINEAR_TOKENS`。
  - **图标扩展到更多场景**（用户：「CLS 只在一部分场景用了这些图标，我们应该更全面」）：
    同一批线性图标现在也用在**工具详情行**（`tool-indent_outlined`，替掉文字箭头 `↳`）、
    **错误块标题**（`warning_outlined`）、**长回合折叠提示**（`more_outlined`）。
  - **可切换**：`tool_row_icon: "line"`（默认）/ `"emoji"`（2026-09-21 选过的 emoji 内联形态，
    保留为可回退；两条路都有门禁 + 变异钉住）。
    `ICON_ALIASES`（28 条逐条等于 CLS）**仍是对齐判据的唯一真相** —— `check_cls_alignment` 只读它；
    `tool_icon_token(name, token)` 只是渲染层精化（60+ 真实工具名不再挤 14 个 token）。
- **澄清卡表单提交契约**：`value` 为空、答案只在 `action.form_value` 的提交形态已覆盖
  （真 SDK payload 类 e2e）；组件级 `behaviors` 路径不变。
- **出站留痕**（排障）：`send()` 卡片成功 / 卡片失败回落 / `edit_message` 成功 / 回落 各留一行
  「出站=<card|edit|text> chat=… mid=… 前置=…」，限流按 (kind, chat) 独立。

### 修复
- **折叠提示元素类型（P0，14:44 灰气泡真根因）**：`collapsible_panel` 的直接子元素曾是
  `plain_text`（非法）⇒ tools > 20 触发折叠提示时 `300313` ⇒ 收尾 `200621` ⇒ 核心回落 `send()`
  发出纯文本「⏳ Working — 」。已改 `markdown`（变异 `V4-33` 实红）。
- **`markdown` 上没有 `text_color` 字段（P0 补刀：同一个灰气泡的另一半）**：上面那次只换了元素
  类型，新的 `markdown` 节点仍带着 `text_color: "grey"` —— 官方 2.0 富文本字段表里没有这个字段，
  服务端回 `200621 unknown property`，而且是**整卡被拒**（不是忽略该字段）⇒ 长回合依旧会掉进
  纯文本回落（用户反馈 #4 的症状没被真正修掉，而单测看不出来 —— 它只钉我们想要的形状，不钉
  服务端收不收这个字段）。P3.1 新增的工具详情行用了同一写法。灰色统一改走
  `<font color='grey'>…</font>`（官方富文本「彩色文本样式」，也是工具行状态色一直在用的写法）。
  **错误块前缀图标**同理：`icon` 必须挂组件级（`div.text` 里没有这个字段），挂错位置同样 200621。
  新增**面板元素字段白名单门禁**（`check_cardview._assert_panel_element_fields`：未登记 tag 直接红、
  `text` 里不许有 `icon`、`markdown`/`lark_md` 不许有 `text_color`；一次报出**所有**越界字段，
  不再跟着服务端一轮一轮试）+ 变异 `V4-60`/`V4-61`。真机探针复验：修复后发送成功
  （卡 `om_x100b64159f75b0a0c2f35ecdf3f0d36`）。
- 预加载提示删除失败会静默「以为删掉了」⇒ 现在保持标志重试、且**换号**（同 uuid 重发会撞 `200770`）。

### 修复（配置）
- **`panel_expanded: true` 在结构化卡片上被静默忽略**（`panel_shell()` 的 `expanded` 形参默认写死
  `False`、调用方不传）⇒ 打开"面板默认展开"后卡片仍是收起的。已修为默认取 `view.expanded`。

### 修复（打包 / 安装）
- `install.sh` 的 FILES 清单漏了 `core/cardview.py`（v0.7.1 新增的运行模块）⇒ `--copy` / NAS
  安装会装出 import 就失败的插件；对账门禁还把 `tools/` 与 `.deploy/` 当运行文件 ⇒ 只要部署目录存在脚本必拒跑。
  已补齐并用 `HERMES_HOME=/tmp/ldinstall bash install.sh --copy` 真跑验证。

### 门禁与流程（非用户可见）
- `test_units.py --only <子串>`；变异判定改「目标门禁先跑 ⇒ 绿则继续到第一支红」+ 120s 硬超时；
  `check_clarify_e2e` 去掉 6 处固定睡眠（4.2s → 1.98s）；`test_cardkit_transport_*` 的批次断言
  不再依赖时钟（4 分片并发下的假红已修）。

## [0.7.1] - 2026-09-21 视觉层重构

### 变更（V0 登记）
- 新增过渡配置键 `visual_engine`（默认 `legacy`）、`card_status_header`（默认 `true`）、
  `show_reasoning`（默认 `false`）；V1–V4 逐步生效，未实现前显式设置非默认值会留 WARNING。
- 规划与三方审计结论：`docs/plan-v0.7.1-visual.md`、`docs/audits/v0.7.1-visual/`。
- 开发期快速门禁：`tests/run_fast.py`（默认约 7.5s；`--full` 约 30s）；阶段改动面变异后台分片。

## [0.7.0] - 2026-09-18

### 变更

- **正文来源切换为 own（默认）**：正文只认插件从 `on_stream_delta(kind="text")` 累积的文本；
  native 帧文本只作刷新信号与 finalize 兜底。工具进度行从结构上不再进入正文渲染输入，
  不再依赖「事后按证据剥帧」。`body_source: legacy` 仅作过渡回退。
- **finalize 规则**：core 非空且以 own 为前缀 → core 整段；core 是 own 精确后缀 → 保留 own
  前段（避免 core 只含最后一段时吞掉工具调用前已展示的正文）；其余分叉 → core 整段；
  绝不按 `\n\n---\n` split/rsplit。
- **严格绑定**：own 模式下 `chat_id -> session_id` 缺失/过期/漂移或正文桶世代漂移时，
  非 finalize 一律渲染空/占位，finalize 走 core 权威文本；不再退回「最近活跃会话」。
- **F4 安全**：core 帧失败后同文 finalize 重发在 own 下拒绝持久化，交回 core edit/send 回落，
  防止 terminal 命令/参数被写进正文。
- `/stop` 的 own 路径改为读取当前卡实际写出的 `last_rendered_body`（visible slice），
  不再读原始 core 帧文本。
- `on_stream_end` 作为第 8 个只读观察钩子接入 `OBSERVED_HOOKS`，只做对账快照，不参与正文决策。
- 面板标题不再含模型名；页脚顺序保持 状态 → 耗时 → 模型 → ctx → 短码；
  面板/工具行颜色与字号层级不变。

### 验证

- 默认 own 下快速门禁：`test_units` 242/242、`check_override` / `check_hooks` /
  `check_clarify_e2e` / `check_own_body` 全绿；变异 preflight 400/400 锚点可用。
- 用户桌面飞书真机确认：生成期占位、工具执行、终态正文（`开始` → `---` → `收尾句 END`）、
  绿边、页脚与展开面板均正常，正文无工具进度行。
- 未完成/待办：legacy 路径与 393+7 旧变异套件的归档/删除（Phase 2）尚未执行，列为后续版本收口。


## [0.6.4] - 2026-09-18

### 修复

- **空白累积 + 前导分隔符的多行进度帧不再漏进正文**：真实回合里 core 会持有只含
  空白（如一个 leading newline）的累积，`_compose_frame_content()` 于是发出
  `"\n\n---\n" + 进度行 + terminal 围栏`。旧判据看到分隔符就 fail-open，
  答案区出现分隔线、`🔍 Searching…` 和 `🖥 terminal` 代码块（用户 2026-09-18 15:17
  真机截图）。现在会把「纯空白前缀 + 核心分隔符」归一掉再做进度形状判定；分隔符
  前有任何真实正文时仍 fail-open，绝不切掉模型文本。
- 保留 v0.6.3 的 `⏳ 正在生成…` 占位修复。
- 新增变异 `CLS-43` 钉住前导分隔符路径，`CLS-42`/`W6b` 继续钉住正文占位。

### 门禁

- `test_units.py` 226/226；override / hooks / clarify 全绿。
- `--preflight` 400/400（393 变异 + 7 对照）；定向 `CLS-34` / `CLS-42` / `CLS-43` / `W6b`
  均断言红。
- 本次是 v0.6.2 观感回归与真实空白累积形状的热修；v0.6.2 的六分片全量证据仍见
  `docs/audits/cls-ui/phase-4/v0.6.2/`。

## [0.6.3] - 2026-09-18

### 修复

- **长工具回合不再显示空白正文**：v0.6.2 把空累积的多行进度剥离后，CardKit 中间帧
  会给 `answer` 元素写一个空格，真机上看像一张卡住了的空卡（用户 2026-09-18 复测反馈）。
  现在流式中间帧保留 `⏳ 正在生成…` 占位，正文一到就被替换；收尾帧仍不带占位，
  避免真的没有正文的回合永远停在等待文案。
- 新增变异 `CLS-42` 钉住“流式空正文不得写空格”，并保留原有 `W6b` 的正文常量守卫。

### 门禁

- `test_units.py` 226/226；override / hooks / clarify 全绿。
- `--preflight` 399/399（392 变异 + 7 对照）；定向 `CLS-42` / `W6b` 断言红。
- 本条是 v0.6.2 的观感回归热修，完整 392 条全量回归在发布后继续跑并补日志；
  四门禁 + 定向变异已覆盖本次改动面。

## [0.6.2] - 2026-09-18

### 修复

- **多行核心工具进度不再进入答案正文**（真机截图 2026-09-18）：模型尚未输出正文、
  核心连续跑多个工具时，进度块是一整段多行文本（friendly verb 行 + terminal 代码块 +
  `⏳ Working` 状态行）；旧判据只看第一行，整帧落进答案区。现在空累积时逐行识别核心
  进度形状（verb / tool name / 围栏 / `(×N)` / 光标 / Working），全帧可证明才剥，
  任何一行不匹配则 fail-open。裸围栏额外要求 terminal 仍在 running；工具名单与正文
  累积强制同会话。收尾帧仍永不剥；**例外**：核心某一帧确定性失败后会用同一合成文本
  再发一帧 finalize（`stream_consumer_transport.py`），该路径的进度行会留在收尾正文里
  （防不可逆吞正文的已知取舍）。
- 修复 `/stop` 重绘可行性判据没把设备字号档位新增的 `config.style.text_size` /
  元素 `text_size` 字节算进去的问题；被追踪正文不可能出现“留下却画不上中止色”。

### 变更（观感默认值）

- `panel_color_tags` 默认从 `false` 翻为 `true`：官方 Card 2.0 `markdown` 文档确认
  `<font color>` 与 14 色枚举后，默认启用彩色标签（绿色 `Succeeded` / 青绿 `Running` /
  红色 `Failed` / 灰色细节）；**真机视觉仍待用户截图确认**，不认颜色的客户端可显式关回 `false`。
- `text_profile` 默认从 `off` 翻为 `compact`：面板/页脚 `notation`（12px）、正文
  `normal`；`compact` 里未见于官方文档的 `x-small` 已移除。**真机字号视觉待确认**。
- `plugin.yaml` / `_DEFAULTS` / README 三处默认值同步，`CLS-33` 变异钉住“默认不许再
  被静默关回无色”。

### 门禁

- 冻结树实测（venv 3.11.15）：`test_units.py` 226/226；`check_override` / `check_hooks` /
  `check_clarify_e2e` 全绿；`--preflight` 398/398（391 变异 + 7 对照）。
- 定向变异 `CLS-33`–`CLS-41`、`A1a`–`A1d`、`R11-2` / `R11-4` / `R11-9` 全部断言红。
- 全量变异（6 分片并行，2026-09-18 14:09–14:57）：**391/391 断言红**，🟢0 / 💥0 / ❓0 / ⚪0（非对照）/ ❌0；**7/7 对照全绿**；无 `test_args_preview` / `225/226` / Traceback 抖动。日志与 sha256 见 `docs/audits/cls-ui/phase-4/v0.6.2/`。

## [0.6.1] - 2026-09-18

### 修复

- **工具调用不再以 terminal 代码块出现在答案正文**（用户 2026-09-18 真机截图）：
  模型尚未输出正文时，核心会把 terminal 进度块作为**唯一正文帧**发来；此前空累积
  导致正文净化判据不成立，答案区直接显示 `🖥 terminal` + 代码块。现在：累积为空、
  工具窗口打开且帧形状明确指向运行中工具（`🖥 terminal` / 裸围栏 /
  `{emoji} {tool_name}:`）时，正文落成空/等待占位，工具详情只留在折叠面板里；
  形状不明确仍 fail-open，收尾帧仍永不剥。新增单测与变异 `CLS-34` 钉住。

## [0.6.0] - 2026-09-17

> CLS 观感改造：面板摘要、工具行与页脚重做；模型名/回合耗时移入状态优先页脚；
> markdown i18n 边界写清；`panel_color_tags` 因真机 `<font color>` 视觉未确认而默认
> **false**（无色降级，确认后可显式打开）。最终工作树全量变异 run 4：382/382 断言红、
> 0 crash/green/missing/noop、8/8 对照，preflight 390/390；D1 维持方案 c（不做生产 header
> 局部更新）。

### 变更（CLS 观感）

- 面板摘要行改为 CLS 观感：`💭 思考 1.6s · 🛠️ 工具执行 · 3 步`；模型名与回合耗时
  不再出现在面板标题，改到页脚（顺序：状态 → 耗时 → 模型 → 上下文 → 短码）。
- 工具行重做：图标 + 加粗**英文动作名**（`Read file` / `Run command` / `Load skill` …，
  CLS 风格；工具行是裸 markdown，不承载 `i18n_content`，因此它是明确的语言固定边界）+
  耗时 + **带颜色的状态词**（绿色 `Succeeded` / 青绿 `Running` / 红色 `Failed`·`Blocked`·
  `Timed out` / 灰 `Cancelled`·`Skipped`）；命令或 skill 名另起一行灰色小字。
  被上游 80 字符截断的 JSON 预览改为**有界 key 提取**，不再整段丢细节、也不倒原文。
- **颜色默认关闭**：`panel_color_tags` 默认从 `true` 改为 **`false`**，状态词/灰色标题/
  细节行走无色降级；`<font color>` 真机三类消费者视觉确认后，可在配置中显式打开。
- 展开面板内新增 `💭 思考` / `🛠️ 工具执行` 两个分区小标题。
- 未知工具状态（`cancelled`/`timeout`/`skipped`）不再让面板整块渲染失败；
  ±Inf/NaN/超大 duration 与 elapsed 已做有界处理。

### 门禁

- 冻结树实测（venv 解释器）：`test_units.py` **225/225**（连续两次）、`check_override` /
  `check_hooks` / `check_clarify_e2e` 全绿；`mutate_check --preflight` **389/389**
  （381 变异 + 8 对照）；定向 `-k CLS` **CLS-1..32 共 32 条全 🔴（断言红）**，
  其中 `CLS-31`/`CLS-32` 钉住 `success` 与 `timeout` 两个状态分支。
- Phase 2 全量变异（run 3，tree `fd96f90f839f6621d83aff46006622d90ff27c1f` /
  commit `ecf11df`）：**381/381 断言红**，`🟢0 / 💥0 / ❓0`、非对照 `⚪0`、
  对照 **8/8** 全绿，`EXIT=0`；`--preflight` 389/389（381 变异 + 8 对照）。
  审计 C 发现的 6 条隐藏 `ERROR` 已由严格分类器与测试断言修复；日志
  `docs/audits/cls-ui/phase-2/logs/fullrun_phase2_run3.log`，结论见
  `docs/audits/cls-ui/phase-2/consensus.md`。
- 颜色默认翻转与 release-gate 修正后在 release 候选工作树上重跑：`test_units.py`
  **225/225**、`check_override` / `check_hooks` / `check_clarify_e2e` 全绿、
  `--preflight` **390/390（382 变异 + 8 对照）**、**全量变异 run 4 EXIT=0：382/382 断言红**，
  `🟢0 / 💥0 / ❓0`、非对照 ⚪0、对照 8/8；日志
  `docs/audits/cls-ui/phase-4/logs/fullrun_v0.6.0.log`（sha256 `70e28b6b…`）。
- golden trace 按本次**有意**的行为变化重新生成。
- ⚠️ `<font color>` 真机渲染与 header 局部更新的**视觉**结论仍为 pending：
  接口探针已发送（`card.create` / `content` / `batch_update` / `content` 全 `code=0`），
  但“标题真的变了 / 颜色真的画出来了”需要用户真机截图确认；因此本版默认
  `panel_color_tags: false`，D1 维持方案 c（生产不写 header 局部更新）。确认颜色后可
  在配置显式打开（见 `docs/audits/cls-ui/phase-0/consensus.md` 与 `phase-1/probe-header.md`）。
- 详细数字与留档以 `docs/verify-log.md` / `docs/audits/cls-ui/` 的实跑日志为准。

## [0.5.0] - 2026-09-17（效果债 + AP-lite + cron）

### 安全（**新增能力** + 开发中审计现场抓到的三处漏脱）

⚠️ **脱敏本身是这一批新增的能力**：`v0.4.0` **完全没有脱敏** —— `_args_preview` 直接把
`json.dumps(参数)` 的前 80 字符印在卡上。下面三条是这一批**开发过程中**由对抗审计在
**尚未发布的工作副本**里抓到的形状，**没有**出现在任何已发布版本里，别当成「线上漏过」。

- **`export KEY="值"`**：`_args_preview` 喂的是 `json.dumps` 的产物，命令里的引号变成 `\"`，
  而当时的值类撞上第一个 `"` 就收尾 ⇒ **凭据原文完整露出**；
- **`Cookie:` / `Set-Cookie:` 头**：当时四条规则一条都不覆盖它（`cookie` 只出现在 JSON **键名**规则里），
  而同批里 `Authorization: Bearer …` 是被涂掉的 ⇒ 格外容易误以为「头都覆盖了」；
- **值里含转义引号**时只涂前半截（`{"password": "***"jwt"}`，**半截凭据同样是凭据**，
  而且看起来像处理过了）。
- **家目录规则把 URL 截断**：`https://x.com/home/dashboard` → `https://x.com~` ⇒ 用户看到一个
  **不存在的 URL**。加了左边界（只在路径起始处匹配）。
- ⚠️ **更正一条本批次自己写错的「修复」**（2026-09-16 对抗审计实测推翻，原始表述保留在此
  以便对照）：原稿写的是「**建实体时页脚元素不进卡**（审计实测的功能缺陷）…⇒ 重启后第一回合
  永远没有页脚、也永远没有短码，已改成按配置决定」。**那个缺陷不存在** —— `_ld_ck_create`
  建卡时用的是函数体里**写死**的 `footer_text=("" if _cfg("footer") else None)`（本来就按
  **配置**判），而调用方传进去的 `footer_text=` **形参从来没被读过**（AST 实测：「形参未被使用」），
  且那一行在 `v0.4.0`、父提交与当时 HEAD **三处完全相同**。⇒ 那次「修复」是**空转**，
  而守着它的用例恰好是本批次自己点名要消灭的两种无牙形态（**只调 helper = 验函数不验接线**
  ＋ **手工照生产那一行再建一次卡 = 验表达式不验调用点**）。
  现在的处置：① 形参 `footer_text` 与它唯一的生产者 `_ld_seed_footer_text()` **都已删除**，
  判据只剩 `_ld_ck_create` 体内那一行（**一处真相**）；② 用例改成**真驱动 `_ld_ck_create`**
  并断言**建出来的那张卡**里有没有 `CARDKIT_FOOTER_ID`；③ 变异 `G2-9` 重新对准那一行
  （打上去**必红**，实测）。

### 修复（正确性）

- **收尾帧护栏**（R11-A7 尾巴）：`_strip_core_progress` 新增**条件 0**（收尾帧一律不剥）。
  起因：核心对 finalize 是**乐观记账**、不会再补发 ⇒ 在收尾帧上剥错就是**静默丢正文**、不可逆；
  而我们的累积来自**钩子队列**（异步），收尾帧可能早于最后一个正文增量 ⇒ 累积是**陈旧的完整前缀**。
  ⚠️ 如实登记：核心有一条 finalize 路径**确实带进度块**（`stream_consumer_transport.py:391`，帧失败后
  用同一合成文本重发）⇒ 那条路上会留下可见的进度行 —— **有意的取舍**（可见的进度行 vs 不可逆地吞正文）。
- **已答复卡的问题行没转义**：用户点一下，卡片就从「按字面显示」变成「露出 markdown 语法」。
- **选项含换行**会破坏「卡面列表 == 下拉标签」的同源判据，并把 `#` 顶到**行首**变成语法 ⇒ 展示标签
  先折叠空白（提交值仍是原文）。
- **踩踏自检口径**：`/larkdeck help` 写「三条心跳记录」而卡上已是六条；「掉回纯文本」的口径文本与
  实现相反（三处一起改正，并钉住那条「没有活跃流 ⇒ 按契约交还核心」的**正常路径不许进账本**）。
- **`/stop` 重绘会抹掉卡片上已有的短码**（它手上就有 `message_id`）。
- **CardKit 正文写失败丢诊断（P1a）**：`_CkOp` 是 NamedTuple、只有 `fail_reason()`，帧路径却把它
  当 `_CkResult` 调 `inner_code()` ⇒ 正文写失败抛 `AttributeError`，被外层接成通用「帧处理异常」，
  具体失败元素与返回码一起丢。现在失败 op 回填 `code`，状态卡保留「正文元素失败（answer）」并把
  `230099` 计入错误码 top-N。变异 `P1a-1` 实测「撤掉即红」。
- **澄清点击的三条静默路径补 toast（P1b）**：缺 `clarify_id`、未授权、适配器 loop 未就绪
  原先都只写 WARNING、用户屏幕无变化；现在各回一条错误 toast，且仍然不换卡、不抛。
  变异 `P1b-1/2/3` 实测「撤掉即红」。

### 新增

- 澄清卡：**卡面可见的选项列表**（与下拉**同源**，编号/去重一致）+ 脚注**按方言分流**
  （2.0 那张卡上没有按钮，就不说「点按钮」）+ 问题与选项的 markdown 转义。
- **卡片短码**（帧页脚 `🔖 xxxxxx` + 每回合自检行 `卡片=xxxxxx`）⇒ 用户截图与日志有了确定的对齐方式。
- `/larkdeck status` 三条：**已运行** / **掉回纯文本**（**我们真的发起过一次写、而它失败了**
  的次数 —— 与「写卡失败」**同点同帧 +1**、当前**不可分辨**；**不含**「没有活跃流 ⇒ 按契约
  交还核心」那条**正常路径**）/ **错误码 top-N**（只数非零码，不同码上限 24 + `other` 桶）。
- **探针 ⑮**（2.0 `button` + 组件级 `behaviors`）与 `--button-2` 单卡模式 —— 补不变量 5 里
  `button` 唯一只有官方文档的一格。⚠️ **真机点击尚未完成**。
- **能力探测上卡（P1a）**：`/larkdeck status` 新增「状态 / 缺失 / 探测契约」三行摘要，覆盖
  `compat.PROBE_REPORT_KEYS` 全部 10 键；`build_adapter()` 存进程级只读快照，状态卡区分
  「未探测」「已接管」「必需接口缺失」「覆盖层构造失败」「报告缺键」，且**从不写「正常」**。
  变异 `P1a-2/3/4` 实测「撤掉即红」。
- **核心中断查找名静态探测（P1b）**：`probe_report` 新增 `core_interrupt_lookup` 键，
  `check_override.py` 在真实 Hermes 上核对 `getattr(type(adapter), "interrupt_session_activity")`
  的源码查找点；`/larkdeck status` 增加「信号契约」行。它是 best-effort 静态证据，
  不替代运行期派发核对。
- **设备字号档位（P1b，默认 off）**：新增 `text_profile` 配置（`off` / `mobile_friendly`
  / `compact` / `large`），在建卡期写 `config.style.text_size` 设备映射 + 元素 `text_size`
  引用；不做流式期结构写。`mobile_friendly` 为 PC 小、手机大。
  - **观感主题（P2，默认 `ap_lite`）**：新增 `theme` 配置（`neutral` 原符号 / `ap_lite`
    抽象 emoji / `ap_bubble` AP 泡波风可选）。只改面板标题与工具行的符号、图标，不碰卡片结构；
    `neutral` 直接调用 `footer_line` / `tool_step` 时仍是逐字不变的旧观感。默认主题变更已同步
    到 golden trace（面板标题 🌊/🧰、工具行 📖/⌨️ 等）。
  - **聚合诊断上卡（P2）**：`/larkdeck status` 顶部新增两行「能力/链路 + 运行/账本」总览：
    探测结论 / 钩子数 / 命令注册 / 模块世代 / 入站年龄 / 写卡与失败计数 / 掉回纯文本 / 错误码；
    有明确异常才带 `⚠️`，**从不写「正常」「健康」**，渲染失败也把原因留在卡上而不是静默消失。
  - **配置查看与热刷新（P2）**：`/larkdeck config` 只读展示每个键的本进程生效值与来源；
    `config reload` 从官方 `ctx.get_config()` 重读（**任一键失败整次取消**；外部并发写入时
    可能读到混合快照，不是文件系统事务）。**聊天侧没有写入命令**：handler 拿不到发送者
    身份，无法安全授权（安全审计 B1）⇒ 插件从不直接写 `config.yaml`、也不调用
    `ctx.set_config()`；写配置走官方 Hermes CLI / 配置文件，再 `config reload` 热刷新。
  - **cron / 无网关投递走卡片层（P3，代码级；无网关真机投递待验）**：通过官方
    `PlatformEntry.standalone_sender_fn` 字段把内置纯文本 sender 换成「先经合并适配器
    卡片层、补官方同款 SDK client、失败 fail-open 到内置、**带媒体附件的那一块回落内置**」的
    sender；cron / `send_message` 在没有常驻网关的进程里会尝试卡片，附件不丢（分块长文里
    非末块仍可能走卡片路径；卡片硬异常会在末块附件前停下）。不改 Hermes 源码、不 monkeypatch；`check_override.py` 在真加载器
    里核对 entry 字段为可调用卡片 sender，并验证 `compat.ensure_standalone_client()` 能真的
    建出 SDK client（真机审计复现过“不补 client ⇒ `Not connected`”的阻断项）。
    边界：**自动定时 cron 仍需要 gateway 进程在跑**（ticker 只在那儿）；standalone 车道覆盖
    `hermes cron run` 与无常驻网关时的 `send_message`。
  - **P3 六家候选 go/no-go（如实登记，不硬做）**：见 `docs/plugins-compare.md` §8 ——
    表格降级 no-go（官方核心明确保留 markdown 表格）、**结果不明保护 partial**（我们 CardKit
    实体/元素写 uuid 确定性；但**卡片首发与文本发送一样**继承官方 `_send_raw_message` 的
    每次 uuid4 重试，已送达但响应丢失时仍可能重复；覆盖它要碰官方私有名，登记待上游）、
    动态结构写/图片上传/命令卡分别因 Phase C 前置、无点名需求与范围外而 defer/no-go。

### 验证

- 新增门禁 `test_every_python_file_compiles`（树里每个 `.py` 都要能编译）—— 起因是真机探针
  **不被任何门禁 import**，一个语法错会一路穿过五道门禁、直到**真机上要用户配合时**才炸。
- 修掉一条**会造成假红**的断言（`⏱ 12.3s` 写死了小数位，高负载下变 `12.4s`）——假红会被变异验证器
  当成判别力证据，**结论正好写反**。
- 修掉验证器把**真断言失败**误判成「只有崩溃、不算判别力证据」的分类缺陷（裸 `ImportError` 子串
  在 `test_units` 的正常输出里就有）。
- 变异：**本轮新增 51 条**（`R11-1..R11-9` · `G1-1..G1-23` · `G2-1..G2-11` ·
  `X14a/X14b/X18/X19/X20/X21/Y5/Y20`）；**清单总计 310 条 + 对照 8 条**，逐条实测变红。
  留档：影子树那轮与仓库那轮各一份日志，都是 `🔴310 · 🟢0 · 💥0 · ❓0` + 对照 8/8 + `EXIT=0`。
  其中 **12 条是「改坏却全绿」逼出来的**，编号可查：
  `G1-22` / `G2-10` / `G2-11` / `G1-6`（4 条**无牙变异**重做）
  ＋ `X14a` / `X14b` / `X18` / `X19` / `X20` / `X21` / `Y5` / `Y20`（8 条**断言缺口**）。
- **P2 门禁**：`test_units.py` **216/216**；`check_override.py` / `check_hooks.py` /
  `check_clarify_e2e.py` 全绿；golden trace 已按默认 `ap_lite` 重生成（diff 只含符号/图标）。
  新增/改锚 **16 条 P2 变异**（`P2-1..P2-8` · `P2-11..P2-13` · `P2-15..P2-19`），逐条实测变红；
  `--preflight` **354/354** 锚点可用。
- **P2 对抗审计**：3 个不同子 Agent 独立审计（`deepseek-v4.1-flash` / `glm-5.3-flash` /
  `omen-alpha`）先给 `PASS WITH ISSUES`，经讨论统一后按修复集改完并 delta 复审**全部 PASS**。
  修掉的真问题：`context_max_override` 归零/删键不清运行时覆盖（A1）；「原子重读」措辞与实际
  逐键读取不符（A2）；聊天侧 `config set` 无发送者身份可授权（B1，**整体移除写入路径**）；
  `read_terminal` 图标误分类（C1）；未知主题静默回退（C2）；reload 不披露 env 遮蔽（C3）；
  空 env 吞提示（A5）；异常日志格式化病态异常（A6）。
- **P3 门禁**：`test_units.py` **218/218**；`check_override.py`（含 fresh-process
  `standalone client init: ok`）/ `check_hooks.py` / `check_clarify_e2e.py` 全绿；
  新增 **3 条 P3 变异**（P3-1 卡片工厂、P3-2 媒体回落、P3-3 SDK client 初始化）逐条实测变红；
  `--preflight` **356/356** 锚点可用。
- **P3 对抗审计**：3 个不同子 Agent 独立审计（`deepseek-v4.1-flash` / `glm-5.3-flash` /
  `omen-alpha`）。其中一路用 fresh-process + 真 Hermes 加载器复现出**阻断项**：standalone
  sender 没补官方同款 SDK client ⇒ 文本 cron 得到 `Not connected`，相对 HEAD 透传是回退。
  修复为 `compat.ensure_standalone_client()`（私有名集中 compat）并在 `check_override.py` 加
  fresh-process 断言；另两路指出的文档口径、媒体缺失兜底、uuid4 残余都一并收口；delta 复审
  三路全部 **PASS**。真机 `hermes cron run` 无网关投递仍待验证，已在 README/CHANGELOG/§8 标注。
- **发布前全量变异（P4）**：`349/349` 定向变异全部被门禁抓住，`🟢0 / 💥0 / ❓0`，
  8/8 对照全绿，基线四门禁全绿（`218/218`）；收在 v0.5.0 发布候选树上。
  留档 `docs/verify-log.md`（本次因会话沙箱日志在 `/tmp/fullrun10b.log`；复跑命令见该文件）。

### 修复（第三轮对抗审计，2026-09-16 凌晨）

- ⚠️ **撤回一条自己写错的「修复」**：本文件曾声称「建实体时页脚元素不进卡 ⇒ 重启后第一回合
  永远没有页脚与短码，已修」。**那个缺陷不存在** —— `_ld_ck_create` 建卡时本来就用函数体里
  **写死**的 `footer_text=("" if _cfg("footer") else None)`（按**配置**判），而调用方传进去的
  `footer_text=` 形参**从来没被读过**（v0.4.0 与当时 HEAD 那一行完全相同）⇒ 那次修复是**空转**，
  守它的用例恰好是本批次自己要消灭的两种无牙形态。现已**删除那个形参**与它唯一的生产者
  `_ld_seed_footer_text()`（判据只剩一处），用例改成**真驱动 `_ld_ck_create`** 并断言
  **建出来的那张卡**，变异 `G2-9` 重新对准（打上去必红）。
- **变异验证器 `--preflight` 的 5 个缺陷**（详见 `docs/handoff-route.md` §11.3）：
  ① 条目**元数**两处判据不一致（6 元组：预检 ✅ / 全量跑 `ValueError` 崩）→ 共用 `_shape_error()`；
  ② 目标文件不存在时全量跑**裸 traceback** → 走逐条记账；
  ③ **「变异没生效」被报成 🟢「断言没有判别力」= 结论正好写反**（替换串等价 / 锚点落在注释里，
  实测注入 `raise` 仍四门禁全绿）→ 现在打印 `⚪ 变异没生效（原因）`；
  ④ 基线全红时预检仍 ✅ 且**毫无告诫** → 输出里明说「没跑门禁、别当提交前的绿灯」；
  ⑤ `-k` **拼错**时打印「全部 **0** 条变异都被门禁抓住 ✅」+ `exit 0` → 空命中直接 exit 2。
- **两处指路到空处的引用**：`AGENTS.md` 说 transport.py:391 的缺口「登记在 plan-v1 附录 F」，
  而附录 F 里没有它 → 附录 F 末尾真的补上那一条；`CHANGELOG` 的「掉回纯文本」口径曾与实现相反
  （AGENTS/README 早改过，漏了这一处）→ 按代码口径改正。

### 验证（第三轮补）

- **「请用户点一次」这件事不再可能白费**：`probe_render.py --button-2` 现在**先本地自检再发**
  （`probe_card_problems()`：方言混用 `230099` / 缺组件级 `behaviors` / 回调 value 少了
  `PROBE_KEY`（点了也没有凭据）/ 元素与字节超限）。理由：这一格是「真人点一次」，而那一击
  只能点一次 —— 能本地判的先本地判。自检**对四条缺陷各证伪一次**（都真的报出来）。
  ⚠️ 它**查不出**「点击能不能到服务端」—— 那正是需要真人点的原因，不是它要替代的事。

### 验证（第四轮补）

- **探针 ⑮ 的「凭据链」现在有断言守着**（`test_units` 199/199，变异 `Y21` 实测红）：
  那一格是「**请真人点一次**」，而一击**只能点一次** —— 用户点完我们手上只有日志里那一行。
  派发链一旦中途断掉（判据键改名 / 被澄清分支截走 / 降级成 debug），用户点完**什么都看不到**，
  而我们只会得出「点击没到服务端」的结论 ⇒ **把一次成功的真机实验误判成失败**，
  再据此决定「澄清卡不加按钮形态」。这就是本项目最怕的形态：**误诊比没有结论更糟**。
  新断言把本地能证的部分封口：`value` 一进适配器就一定打出 `探针点击到达 … tag=button`，
  且**不许**被当成澄清点击。⚠️ 它刻意**不 import** `probe_render.py`（真机探针不被门禁 import
  是一条**有意的隔离**：一被 import，以后动探针就得跟着重跑一次全量变异）——
  卡片那一侧由 `probe_card_problems()` 在**发出去之前**自检。

### 修复（第三轮对抗审计 · 探针 ⑮ 的凭据链，2026-09-16 凌晨）

审计把「本地能证的都证掉了」这句话**推翻**了 —— 它给出了一条**完全本地可证**的反例：

- **探针自检与适配器判据分叉**（真缺陷）：自检用的是 `PROBE_VALUE_KEY in value`（**键存在**），
  而适配器判的是 `value.get(...)` 的**真值性** ⇒ `{"larkdeck_probe": False}` 被自检**放行**，
  却被适配器静默交回内置实现 ⇒ **一行日志都不会有**，用户一次**成功**的点击会被读成
  「飞书没投递」。⇒ 判据收进 `cards.is_probe_value()`（**一处真相**），适配器与探针都调它，
  并加断言钉住它的语义（变异 `Y23`）。
- **自检本身零门禁覆盖**：把它改成恒真、或忽略它的返回值，**四门禁照样全绿** ⇒
  「自检也验过」当时只是提交信息里的一次手工声明。现在有断言 + 变异守着。
- **自检的 7 类假阴性 / 1 类假阳性**（逐条补上，并逐条复验）：
  嵌套在 `column_set` 里的 1.0 `action` 行（飞书照样拒收 `230099`）· `config.summary` 形态
  · `button.text` 必须是 `plain_text` · `behaviors[0]` 不是 dict 会崩 · 多条 `behaviors`
  （行为未定义）· `value` 是裸字符串；反过来，**合法的 2.0 容器写法**（按钮放进
  `column_set`）原先被误拒，字节边界 `>=` 也与真机「128000 被接受」矛盾 —— 都改了。
- **`--button-2` 不记探针账本**（全文件 11 处入账、唯独它没有）⇒ 它只靠「7 天内 / 最近 50 条 /
  内容读得回来」的内容补扫才能被清掉，超出任一条就永久留在用户 DM 里。已补上入账。
- **清理的补扫不筛发送者**：标记里混着 `这张卡渲染正常吗？` 这种**自然中文句子**，理论上会删掉
  用户/agent 的真实消息 —— 与 `AGENTS.md` 的红线「清理只许按 `message_id` 删」相抵触。
  已加发送者过滤。
- **一条断言「因错误原因通过」**（审计实测）：把插件 logger 的 `propagate` 关掉 ⇒ `199/199` 全绿，
  而 Hermes 的文件 handler 挂在 **root** 上 ⇒ 那行**永远到不了 `agent.log`**。现在断言钉在
  **logger 对象身份 + 可达性**上（变异 `Y22`）。
- 顺带：`core/cards.py` 的 `escape_inline_md` docstring 里有一处**无效转义序列**
  （`SyntaxWarning`，未来 Python 会变成错误）—— 全树编译现在零警告。

### 修复（第四轮对抗审计 · 功能之间的接缝，2026-09-16 清晨）

这一轮审的是「单看每个功能都对、合起来错」的接缝。**6 条接缝里没有中/高危缺陷**，
但抓到一条**理由写错的注释**与三处**没有判据守着的前提**：

- **`_strip_core_progress` 的「只剥后缀」那条注释，理由曾是错的**（同义反复）：
  它说「因为我们只做后缀剥离」，而 `return accumulated` **只有在 `accumulated` 真是前缀时**
  才叫剥后缀。真正的保证来自**条件 ③+④**（`tail = text[len(acc):]` 且以分隔符开头
  ⇒ `acc == text[:text.index(SEP)]` ⇒ 必然是前缀），而这条又依赖一个**外部前提**：
  我们的累积与核心的累积**逐字节同源**。**那个前提没有任何判据守着** ——
  一旦核心改了投递方式，`display` 就可能不再是帧文本的前缀 ⇒ **在切卡接缝上静默吞正文，
  而四门禁全绿**。⇒ 注释改成正确的推导 + 新增**独立**用例
  `test_strip_core_progress_only_ever_returns_a_prefix_of_the_frame`（带构造自证；
  实测：施加「返回非前缀」的实现变异后**它自己也变红**，不再被前面的具体断言遮住）。
- **`_REDACT_SCAN_CHARS > _ARGS_PREVIEW_CHARS` 是「可见的未脱敏凭据 = 0」的充要前提**：
  审计做了穷举（0..4096 步长 1 × 5 种形状 ⇒ 可见未脱敏 **0 例**），但那个「0」是**这条不等式
  换来的**，不是设计上的巧合 —— 把扫描窗口调到 ≤ 展示窗口，同一形状立刻变成**可见的未脱敏
  凭据**。⇒ 把不等式本身钉住（变异 `Y24` 实测红）。
- **澄清卡去重后的编号空洞**（低危观感/误导）：`["甲","乙","甲","丙"]` 显示成
  `1. 甲 / 2. 乙 / 4. 丙`（没有 3）。编号沿用**原始 1 基下标**是**对的**（核心按
  `choices[n-1]` 解析文字回答），所以**绝不许为了让编号连续而重排** —— 那会让卡面**说谎**
  （卡面写「3. 丙」而核心给「甲」），比跳号严重得多。⇒ 新增断言钉住「卡面数字 → 那一项」
  这条映射 + 反向断言钉住「不许重排」（变异 `Y25` 实测红）。⚠️ 真正的修法在网关那一侧
  （让 `entry.choices` 与去重后的列表一致），**未做**，登记在交接单。

### 内部（踩坑索引与规则的收口，2026-09-16）

`docs/lessons.md` 是**开工索引**，所以这一批的新教训必须落进去（此前只写在 `AGENTS.md` 里）。
新增 5 条推论，并修掉一条**指向对不上的引用**：

- **推论 34**：「撤掉修复也不红」的**形态清单**（8 种形态 + 各自病根）。此前 `AGENTS.md` 用
  「第五种形态」指代最后一条、并把前四种写成「推论 6/7/8」—— **三个编号对四种形态**；
  现在清单只有一处，`AGENTS.md` 只做指向（对不上的引用本身就是这一族的病）。
- **推论 35**：锚点「唯一」还不够 —— 它必须落在**这批用例真的会经过**的语句上（`G2-11`：
  右文件、唯一锚点、**错分支**，报告照常打出 🟢）。
- **推论 36**：写「已修」之前先证明缺陷存在（**本批次最贵的一条**，见上面那条更正）。
- **推论 37**：注释里的「为什么」必须是**推导**，不能是同义反复；**外部前提**要有独立判据。
- **推论 38**：结论要标**证据强度**（「官方文档写了」≠「真机验过」，别人的代码注释不算证据）
  ＋ 一次性真机实验要**事前**写死判定协议（成立 / 不成立 / **不可判定**）。
- 另补：§三 由「三个验证盲区」变**四个**（新增 `--preflight` 的边界：它只数锚点、
  不跑门禁也不做基线自校验 —— 实测「318/318 ✅ + exit 0」与「这棵树跑不了」可以同时成立）；
  §四 新增交付约定「**不让用户做我能自己做的取证**」。
- **当天下午就把其中一条残留补成了门禁**：`_strip_core_progress` 依赖的那个**外部前提**
  （我们的累积与核心的累积逐字节同源）原先是「只有一段引用核心行号的论证」，
  现在 `tests/check_hooks.py` 的**前提核对**一格会**驱动核心真实的投递链路**
  （`_fire_stream_delta` → 核心自己 docstring 写明的 `consumer.on_delta` → `_drain_queue`
  → `_filter_and_accumulate` → `_append_accumulated`），断言「钩子收到的累积」与
  「核心 `_accumulated`」**逐字节相同**，并用核心真实的 `_compose_frame_content()` 合成的帧
  做一次剥离回环。变异 `PA-1`（把我们入账的 delta 顺手 `.strip()`）实测**必红** ——
  它吃的正是核心在工具轮边界自己插的那个 `"\n\n"`。桩掉三处（异步 `run()` 循环、那个
  布尔位、宿主侧 `_strip_think_blocks` 的 identity），都在代码里写明；**仍未覆盖**的是 `run()` 自己的分支 / 传输层与回合边界的
  `_adopt_final_text`（失败方向是 fail-open，只会「不剥」不会吞正文），已登记在附录 F。

### 验证（探针 ⑮ 真机点击 —— **效果债的最后一块拼图**，2026-09-16 16:12）

**2.0 卡里「组件级 `behaviors` 的 `button`」能不能把点击送到服务端 —— 现在有真机答案了：能。**

```
2026-09-16 16:12:37,131 INFO larkdeck: [larkdeck] 探针点击到达 ✅ tag=button option=None
  input_value=None value={'kind': 'button', 'larkdeck_probe': True} open_id=… chat_id=… token=True
```

- 判定用的是**点击之前写死的协议**（`docs/handoff-route.md` §12），不是事后解释：
  `tag=button`（飞书报的组件类型）＋ `value={'kind': 'button', …}`（**我们自己的回声载荷**，
  证明 `behaviors` 里的 `value` 原样到达 `event.action.value`）＋ 点击时刻平台 `connected`。
- ⇒ `AGENTS.md` 不变量 5 里那一格**从「只有官方文档」升格为「真机已证」**：
  `select_static` / `multi_select_static` / `input` / **`button`** 四种**全部真机点过**。
  ⚠️ 这段措辞的历史要一起读：R11 发现它写得**比证据强** ⇒ 按证据强度**降级** + 留探针 ⑮ ⇒
  **补完才升回来**（`docs/lessons.md` 推论 38 的通用形态）。
- 用户当时的反馈是「**界面上没有任何反馈**」—— **那是预期的**：这张探针卡刻意不带任何改卡逻辑，
  它唯一的产物就是上面那行日志（卡片文案本身也写着「应产生一个日志」）。
- 顺带：这一格**不是**「澄清卡该不该加按钮形态」的决策 —— 点击到达只是这个决策的**前置**。

### 修复（第五轮对抗审计 · **新加的那格门禁自身**的五个缺口，2026-09-16）

这一轮审的对象是**上一轮刚补的那格门禁**（`check_hooks.py` 的「前提核对」）。审计全程**只读**
（实验都在 `/tmp` 副本上做，收尾 `git status --porcelain` = 0 行）。结论：**5 条缺口，最高 medium**，
已全部修掉并各有判据：

- **中-1：这一格读的是「最近活跃」指针，不是它要核对的会话。** `answer_state()` 的第一个形参是
  `chat_id`，传空串会退回进程级 `_ANSWERS_LAST[0]` ⇒ 审计把正文仓库的**按会话分桶整条拆掉**
  （`_answer_bucket_locked` 的 `sid` 换成常量），这一格**照样 `HOOKS OK` / exit 0** ——
  它读到的其实是「上一个写过正文的桶」。
  ⇒ 修法：先 `bind_chat_session(chat, session)`（**真机上就是 `pre_gateway_dispatch` 那条绑定**）
  再按 chat 读，并**自证归属**落在目标会话上；变异 **`PA-2`** 钉这一条（实测必红）。
  同一处病也在更早那两格（R11-A7 的 `answer_state("sess-golden")`）—— 一并改成绑定后读。
- **中-2：「上游改形状它会红并打印原因」是假的。** 真跑 `del StreamDeliveryMixin._fire_stream_delta`
  ⇒ 抛 `AttributeError`、只打 traceback、**没有 FAIL 行**，而且**这一格之后的断言全部不再执行**
  （黄金路径那几条一起消失）⇒ 升级时会变成「一格崩掉、半张门禁没跑」。
  ⇒ 修法：整段驱动套 `try/except`，退化成一条可读的 FAIL。实测（注入模拟故障）：
  `EXIT=7` + 一行 `FAIL: 核心投递链路的形状变了（…）⇒ 前提核对无法进行`，
  且后面「黄金序列 ①..⑤」照常跑完。
- **中-3：判据所服务的那个文件没同步。** `core/adapter.py:474` 仍写着「**那个前提没有任何判据
  守着**」—— 上一轮 commit 的同步清单列了四处文档，唯独漏掉 `_strip_core_progress` **自己那段注释**
  （而 `AGENTS.md` 恰好写着「改这一段时把那条断言一起看」）。
  ⇒ 已改写成「现在有判据守着」+ 两条判据的分工 + 仍未覆盖什么。
- **低-1：「只桩掉两处」不准。** 宿主类其实盖了四个方法，其中 `_strip_think_blocks` 是 identity，
  而核心真实现（`agent/agent_runtime_helpers.py` 的 `strip_think_blocks`）**会改写文本**。
  结论不受影响（`stream_delivery.py` 是洗完之后把**同一个局部 `text`** 分发给两边），但**话说过头了**
  ⇒ 四处文档（lessons / plan-v1 / AGENTS / 本文件）统一改成「桩掉三处」并写明第三处为何动不了结论。
- **低-2：形状断言比它宣称的弱。** 注释写「必须**就是**『累积 + 分隔符 + 进度行』」，
  代码却只有 `startswith(累积 + 分隔符)` ⇒ 往分隔符后面拼任意垃圾照样通过。
  ⇒ 改成**整条相等**（`_our_acc + "\n\n---\n" + "\n".join(进度行)`）。

审计同时**确认**了三件要紧的事（都跑了实验）：这一格**真的驱动了核心链路、且有独立判别力**
（把核心的 `_enqueue_stream_hook` 改成 no-op，只有它变红）；`PA-1` 的归因正确（在副本里**删掉这一格**
再打 `PA-1` ⇒ `HOOKS OK`，说明它是 `check_hooks` 里唯一的捕获点）；失败路径是响的
（`EXIT=7` + `^FAIL:` 2 行）。

### 修复（第二轮对抗审计，2026-09-15 深夜）

- **`_dur` 对脏值会编数字、甚至会抛**（真缺陷，不是断言问题）：`nan` → `int(nan)` 抛
  `ValueError`、`inf`/`-inf` → `OverflowError`；`0`/`-1` → **`'497079h14m'` 这种形态**
  （「已运行 56 年」这种**看着像真数字**的脏值 —— ⚠️ 具体数字是**墙钟读数**，
  今天复现是 `497081h26m`，别把它当常量对）；未来时间戳（`1e18`）→ `'0s'`（显示成"刚重启"）。
  四类一律「无记录」，判据与 `_when` 的 `_EPOCH_FLOOR` 对齐。
- **账本快照是浅拷贝**：`status_snapshot()["codes"]` 曾是**活动账本本身** ⇒ 读者改快照
  即污染共享账本（测试 / 探针 / 以后的排障面板都是读者）。现在 `codes` 跟着拷一层。
- **`note_plaintext_fallback` 的原因串没做单行归一**：异常字符串里的换行会把
  `/larkdeck status` 的卡片撑成多行，行首的 `#` 还会被解释成 markdown 标题。
  `note_frame_fail` 早就是对的，两处口径现已统一。
- **变异清单与源码脱节**：**7 条失效锚点**重新对准；**4 条无牙变异**重做 ——
  `G1-22`（原用例的探针构造**让错误分支不可达**：条件④按长度切片，多出来的那个字让切片错位
  ⇒ 连错误实现也原样返回，已改成**带自证**的探针）、`G2-10`（原用例**手工照生产那一行再建一次
  卡** ⇒ 验的是表达式不是接线，已改成真驱动 `/stop` 并读真的发出去的那份载荷）、
  `G2-11`（锚点在源码里唯一，但**打在另一条分支上** ⇒ 变异从没被执行过）、
  `G1-6`（`Authorization: Bearer …` 已被头部规则整个覆盖 ⇒ 那句断言没有守住 Bearer 规则，
  补了「**裸 `Bearer`**」这个它唯一独占的形状）。

### 验证（第二轮）

- **修掉第二个假红源**：状态卡那条用例**整行比对**了 `⏱ 已运行：2s` 这种**墙钟读数** ⇒
  跨一秒就变红，而变异验证器会把假红当成判别力证据（**结论正好写反**）。
  判据改成「稳定的行整行比对 + uptime 只钉标签」。
- 补 **8 条断言缺口**（`X14a/X14b/X18/X19/X20/X21/Y5/Y20`）与对应的 **8 条变异** ——
  它们全都是「**代码是对的、但没有任何判据守着**」：`send()` 两处回落计数、错误码表上限、
  账本快照深浅、原因串单行、`_dur` 脏值、收尾整卡短码、共享盒子里的账本那一格。
  其中 **3 处验的是「接线」**（`send()` / 收尾整卡 / `/stop` 重绘），是同一型缺陷的第三个实例。

## [0.4.0] - 2026-09-15

### 新增

- **元素「容量到顶」有了自己的码与处置**（R11-B2）。真机实测（新探针
  `tests/probe_ck_stream_ops.py --capacity-codes`，两条臂各跑两遍、字面一致）把原来那句
  「`300315` 内包 `300305`」补成了**可解析的契约**：
  * 运行时新增元素撞 200 墙（`append(panel)` 与 `insert_after(answer)` **同形**）⇒
    `code=300315`，msg 形如 `... [element exceeds the limit], code: 300305; `；
  * 复用卡里已存在的元素 id ⇒ 外层同样是 `300315`，但**尾部内层码是 `300301`**
    （方括号里的 `Code 1001` 只是描述码）。
  ⇒ `300315` 是**包装码**，只看外层码会把「id 重复」（我们自己的 bug）误判成「元素到顶」
  （容量问题），两者的修法相反。现在按内层码分档：**确定性失败 —— 不重试、留痕**，
  正文列仍是 FATAL（fail-open 交核心回落，绝不当卡级死法降级）。
  验收：单测 **189/189** · `OVERRIDE OK` · `HOOKS OK` · `CLARIFY E2E OK` ·
  变异 `B2-1..B2-8` 八条全红（含「只看外层码」「解析不出返回 0」「容量码进重试表」
  「容量码当卡级死法」「正则去掉 `\b`」「取第一个匹配」「原因不再分辨内层码」）。

- **滑窗写入守卫**（R11-B1）：1 秒窗口内写满 10 次逻辑写时，本帧**让出装饰批量**
  （下一帧补写、不置 `ck_decor`、**帧照旧算成功**），正文与预览永不跳过。
  动机如实写：稳态**摊还** ≈8.2 逻辑写/秒，根本触发不到它 —— 它是给 Phase C 的运行时
  `card_element.create`（会把滑窗顶到 10–14 次/秒）预留的余量闸门。⚠️ 两处口径不许含糊：
  ① 8.2 是**摊还均值**，正文与预览**不查**这个闸门 ⇒ 1 秒窗口内理论峰 12 条；
  ② 「不可达」依赖两个前提（帧节流是常量 + 核心在 definitive `False` 后停用本回合 native）——
  审计做过反事实实验：前提一变，1 秒内就能顶满并让出 68 次装饰。
  **喂养面同样有门禁**（B1 对抗审计实测五种改法原来四门禁全绿，逐条补上）：
  窗口里每一帧新增的条目数必须**逐帧等于**这一帧真的发出的逻辑写数（假时钟对账）、
  窗口长度/容量用**字面量**钉住、**让出装饰 ≠ 让出正文**（提交点永不跳过）有独立断言。
  验收：变异 `B1-0/1/2/3/4/5/6/7` **八条全红**（含「守卫被整体撤掉」「只修剪不记账」
  「跳过帧顺手连正文也不写」「窗口长度改小」「预览不入账」）。

### 修复

- **「面板为空」的诊断不再每个回合都刷一条**（R11 §3.4 的尾巴）：建卡 seed 帧**必然**没有
  过程数据，旧写法会在每个回合开头都打一条「面板为空 · 诊断」（真机实测噪声），而这条
  INFO 正是「面板恒空」那个真 bug 的**唯一**排查凭据 —— 被正常噪声淹掉等于没有凭据。
  现在只在**收尾帧仍然为空**时才报（那才是病）。变异：退回「每一帧都报」⇒ 单测变红。
- **README 里「正文只有回答」改成写实的那一句**（R11 §3.4 的尾巴）：工具**执行期间**核心的
  进度行可能短暂出现在正文里（模型先调工具、后写回答的回合尤其如此），**回答一开始就干净**。
  真机凭据：一个 6 推理轮 + 6 工具调用的回合自检行 `正文剥进度=0`、用户回看终态无异常 ——
  那是 fail-open 在按设计工作，不是缺陷。
- **「进程内共享」只搬了容器，漏了锁与两处兄弟状态**（R11-A0 + A6，都是「同进程两遍插件发现
  ⇒ 两份模块对象」的余波）：
  ① **锁各持一把** ⇒ 互斥从构造上失效（一份在遍历会话表、另一份同时插新桶 ⇒ 实测
     `RuntimeError: dictionary changed size during iteration`；真机上是「偶发丢面板」这种
     不可复现的症状）。现在 `panel._LOCK` / `context._LOCK` **与它们守的容器同源**（都在共享盒子里）。
  ② `context._INFLIGHT`（「正在探测」集合）各持一份 ⇒ 另一份 `discard` 不掉，
     那个模型的上下文窗口**永远不会再被探测**（静默退化成家族兜底 128K、页脚跟着显示错的窗口）。
  ③ `adapter.HOOKS` / `COMMAND`（注册结论）各记一份 ⇒ 启动自检与 `/larkdeck status`
     按「读到哪一代」给出**两个不同的答案**（钩子 7/7 还是 0/7）。
  共享盒子的键清单收敛到**一处真相**（`panel._SHARED_BOX_FACTORY`，`context` 委托），
  并加断言 `set(真实盒子) == set(声明)`；**明确不共享**的两项也写进文档（适配器类缓存 ——
  共享会把活适配器冻在旧世代的代码上；`_log_*_once` 的限流戳 —— 只影响日志噪声）。
  另修一个真窟窿：**适配器合并类会自套娃** —— 第二世代拿回来的基类是**第一世代的合并类**，
  照旧写法会叠成 `[新合并, 新混入, 旧合并, 旧混入, 官方]`，混入层里 `super()` 的
  **回退路径会被执行两遍**（都是「真的写一次卡」⇒ 同一帧写两次）。现在先剥掉我们自己的层、
  只继承官方类，并在日志里点名这件事。⚠️ 剥旧层**不能只按对象同一性判**：跨世代的
  `LarkDeckMixin` 是两个不同的类对象（模块被重新 exec）。
  验收：单测 **179/179** · `OVERRIDE OK` · `HOOKS OK` · `CLARIFY E2E OK` ·
  变异 `R12-1..R12-7` 七条全红（含「锁退回模块局部」「在飞集合退回模块局部」
  「盒子多出没人声明的键」「不再剥旧层」「只按同一性认自己的层」）。

- **`progress_lines_in_body: false` 一直是空转的 —— 工具进度行照样出现在卡片正文里**
  （R11-A7）。这个配置键从有卡片起就写在 `_DEFAULTS`/`plugin.yaml`/README 三处、默认 `false`
  （「正文只有回答」），但它唯一的实现是覆盖 `format_tool_event` 返回 `None` ——
  而 Hermes 0.21.1 的**生产路径根本不调用那个扩展点**（唯一调用者在
  `gateway/stream_dispatch.py`，那个 `GatewayEventDispatcher` 只在测试里被构造）；
  真机上的工具行由 `gateway/run_turn_runner.py` 生成，native 流式下被
  `gateway/stream_consumer.py` 的 `"\n\n---\n".join((accumulated, progress))` **合成进同一帧**，
  而卡片按契约渲染整帧。
  现在改在**帧文本**上做，判据是**证明**而不是猜：
  `帧文本 == 我们攒的正文 + "\n\n---\n" + 尾巴` ⇒ 尾巴只可能是核心加的。
  四个条件缺一不可（有工具窗口 / 正文累积完整 / 前缀对得上 / 尾巴里有内容），
  任何一条不成立就**原样渲染** —— 最坏是这一次进度行可见，**绝不吞正文**。
  旧版（`8f81b4d` 移除）是**按分隔符切**：模型写一条 markdown 分隔线就会把后半段答案切掉，
  而核心对 finalize 是乐观记账、不会再补发。
  **不需要动核心的任何配置**（早先用 `display.platforms.feishu.tool_progress: off` 绕过，
  已撤掉，`~/.hermes/config.yaml` 还原成与改动前**逐字节一致**）。
  真机凭据：每回合自检行新增 `正文剥进度=N`（N ≥ 1 = 真的剥到了；卡片没有读回接口，
  这是唯一凭据）。
  验收：单测 **178/178** · `OVERRIDE OK` · `HOOKS OK` · `CLARIFY E2E OK` ·
  变异 `R11-1..R11-8` 八条全红（含「按分隔符切」「累积不完整也剥」「没有工具窗口也剥」
  「配置键又变空转」四条反例）。

- **页脚的上下文上限长期按 128K 兜底（DeepSeek V4.1 Flash 实际是 1M）**：`hooks._on_api_request`
  往指标层传了 model / provider / usage / response_model，**漏了 `base_url`** ——
  而 `context.context_max()` 是按 `model@base_url` 解析窗口的，少了它探测就退化成
  「无 base_url、无 provider」的**裸查表**，落到家族兜底 `deepseek`: 128K（真实 1M）。
  用户可见症状：页脚一直显示 `ctx x/128k`，据此判断「该压缩了」必然误判。
  修复 = 把 `base_url` 从钩子载荷透传下去（一行）。
  ⚠️ `tests/check_hooks.py` **抓不到**这条（脚本自己把 `base_url` 塞进派发载荷，
  缺陷恰在「回调 → 指标层」这一跳）；判据是
  `tests/test_units.py::test_api_hook_passes_base_url_to_the_context_probe`，
  变异清单 `HK` 撤掉透传即红。盲区本身记进 `docs/lessons.md` 第三节。
  验收：单测 **177/177** · `OVERRIDE OK` · `HOOKS OK` · `CLARIFY E2E OK` · 变异 `HK` 红
  （`python3 tests/mutate_check.py -k HK` → 断言红=['test_units.py']）。

## [0.3.0] - 2026-09-14

面板拆两块（R3 收窄版）：`panel_body`（推理）+ `panel_tools`（工具），**都在建实体时建好**、
走同一次 batch ⇒ 逻辑写次数不增加；工具结束帧不再重发推理块、推理增长帧不再重发工具块。
外加审计抓到的两处既有缺陷修正（单帧最坏调用数 12 → 实测 9；`AGENTS.md` 引用了已删字段
`ck_panel`）。真机证据：`--cardkit-prod` 的装饰 batch 形状逐字为
`[三块] → [只 panel_tools] → [只 panel_body]`、序号账本 `[1..8]` 连续、R9 账本 7 帧 0 失败；
澄清失败 toast 真机确认（红底错误态 + 卡片不动）；面板两块的真机观感确认（含「无工具时不多空行」）。
验收：单测 **176/176** · `OVERRIDE OK` · `HOOKS OK` · `CLARIFY E2E OK` · 变异 **226/226 全红 + 8 对照绿**。

### 新增

- **面板拆成两块**（R3 收窄版）：CardKit 实体卡的面板从「一个 markdown 装推理 + 工具」改成
  **两个** markdown —— `panel_body`（推理轮）与 `panel_tools`（工具行列表），**都在建实体时建好**
  （流式期间**不做**结构性写入，那条不变量没破），两块走**同一次** `card.batch_update`
  ⇒ **逻辑写次数一次都不增加**。收益是两条与时钟无关的不变量：**工具结束那一帧不重发推理块、
  推理增长那一帧不重发工具块** —— 推理逐字在长时不再把那几十行工具摘要一起每帧重发
  （量级 ≈2.7KB/帧），客户端也不再整块重绘。
  ⚠️ **工具开始那一帧通常两块都写**（工具打断推理轮 ⇒ 轮次标题补上耗时 ⇒ 推理块真的变了）；
  旧措辞「工具事件一律只写工具块」只在工具结束帧成立。
  观感与拆分前**逐行一致**（面板本来就是「先推理块、后工具行」两段式）。
  ⚠️ 收尾/降级/`/stop` 重绘走的是普通卡（`unified_panel`），那一刻整卡一换仍是一个 markdown
  —— 也就是说这两块是**实体卡流式期间**的形态（今天本来就是这样）。

### 修复

- **文档里的「单帧最坏调用数」是错的**（三处）：README / AGENTS / `docs/plan-v1.md` 曾写
  「3 次逻辑写 × 4 = **12 次调用 / ≈3.0s**」，那是**把会话预览也按 4 次尝试算的** ——
  而预览走 `retry=False`，永远只发一次。真实值是 **2×4 + 1 = 9 次调用、退避睡眠 2.0 秒**
  （`core/adapter.py` 里还残留 R7 之前的「8 次 / ≈2.0s」，一并改准；R4 的切卡帧另有 5 次可重试
  的写，文档里从未覆盖过，现已写明）。
- `AGENTS.md` 还在引用**已经删掉**的状态字段 `ck_panel`（R1 之后是元素表 `ck_elems`）——
  照那份文档写会造出一个没人读的字段。

### 内部

- 变异验证器：**225 条**变异 + 8 条对照（新增 `R3-1..R3-6`：两块结构、写入计划、两块写反、
  推理块混进工具行、工具块忘了截断、裁减方向写反）。后两条**第一次跑是绿的** ——
  它们暴露了「工具块的截断与裁减方向**零门禁**」这个既有缺口，已补
  `test_panel_tools_block_truncates_and_keeps_the_most_recent`。
- `golden_cardkit_trace.json` 的**定义域扩了**：场景里加了一个工具事件（此前只灌推理 ⇒
  对「面板两块」这类改动**零判别力**）。新夹具如实钉住「工具事件那一帧**只含 `panel_tools`**」。
- 单测 **176 条**（新增面板两块场景 ㉕/㉖ 与工具块截断用例）。

## [0.2.0] - 2026-09-14

**首次发布**（`v0.1.0` 只是内部基线 tag，没有建 Release）。分阶段方案、逐阶段证据与每一轮
对抗审计的收口见 [`docs/plan-v1.md`](docs/plan-v1.md)；能力、配置与部署见 [`README.md`](README.md)。
本版的门禁实测：单测 **175/175** · `OVERRIDE OK` · `HOOKS OK` · `CLARIFY E2E OK` ·
变异 **219/219 全红 + 8 条对照绿**；真机探针：CardKit 生产路径、降级车道、中止重绘、卡链。

### 行为变化（**不动配置也会看到不同**）

- **核心的工具行不再并进正文**（`progress_lines_in_body: false`，新默认）：以前正文区会滚
  `⚙️ mem0_search: "…"` 这类核心 chrome，而同一份信息在「执行详情」面板里还有一份结构化的
  ⇒ 两份重复、正文被污染。现在插件在**官方扩展点**上吃掉这些行（`format_tool_event` 返回
  `None`，基类 docstring 明写允许），**工具步骤仍在面板里**（来自官方钩子），正文只有回答。
  想恢复核心那套就配 `progress_lines_in_body: true`。

- **流式帧的默认传输改成 CardKit 实体**（`native_transport: cardkit`）——卡片变成**真逐字打字机**
  （普通卡 + `message.patch` 只是「几个几个字地跳」）。想回旧路径：`native_transport: "patch"`。
  真机证据：生产路径探针（建实体 1 次 + 发实体卡 1 次 + 元素写入 6 次 + 收尾 patch 1 次，全 `code=0`）。
- **澄清卡默认方言改成 2.0**（`clarify_dialect: "2.0"`）——下拉 + 输入框；想回按钮式：
  `clarify_dialect: "1.0"`。真机证据：点击到达并解析出 clarify id，2.0 端到端全绿。

### 新增

- **流式期间就能看到页脚与面板更新**（CardKit 传输）：页脚元素与面板元素在建实体时就建进卡里，
  之后每一帧按需刷新 —— 以前它们只在收尾那一帧出现（`footer: false` 时不建、也一次都不写它的 id）。
  真机证据：`--cardkit-prod` 4 帧 = 4 次正文写入 + 2 次装饰 batch（首帧 `panel_body+footer`、
  面板变化那一帧**只有 `panel_body`**），全部 `code=0`。
- **每帧元素写预算 2 次**（卡级上限 10 次/秒 × 0.25s 帧窗口）：装饰合并成**一次**
  `card.batch_update`，正文单独一次 `card_element.content` 且**最后写**；装饰**内容没变就不写**
  （稳态每帧 1 次）。装饰写失败只标死 + 留 WARNING（卡片继续逐字），**只有正文失败才回落**。
  R7 起再加一次**会话预览**写（`card.settings`，5 秒窗口限频 ⇒ 平均 ≈0.2 次/秒，且不重试）
  ⇒ 折算 ≈**8.2 逻辑写/秒 < 卡级上限 10 次/秒**。
  ⚠️ 口径说明：这是**逻辑写**口径，**不是** HTTP 调用数 —— 元素写撞限流时同一个请求会退避重发
  （最多 4 次尝试、`uuid` 不变所以幂等）⇒ **单帧最坏 12 次 HTTP 调用、约 3 秒**（⚠️ **这个数当场就算错了，已在 v0.3.0 更正**：预览走 `retry=False`、永远只发一次 ⇒ 真实最坏是 `2×4 + 1 = 9` 次调用 / 退避睡眠 2.0s。留在这里是为了让查历史的人看到「当时的结论」与「后来的更正」都在，而不是把旧数悄悄改掉 —— 本项目的纪律是**改口径时四处同步**，v0.2.0 这段属于历史记录，所以加注而不是改写）。
  这是「绝不早失败（早失败会让本回合退化成纯文本）」与「写入配额」之间的**有意取舍**，
  写在这里以免被读成「每帧最多 3 次调用」。

- **卡片不会因为「元素通道死了」而掉成纯文本**（R5）：元素写入拿到卡级死法
  （`300309` 会话已关 / `300313` 元素不存在 / `300317` 序号冲突）时，改用整卡 `message.patch`
  续写**同一张卡** —— 不再逐字，但卡片、内容、位置都在，也不会多出第二张卡。
  装饰批量与正文写入**两条通道**都会触发；唯一例外是 `300313` 落在装饰上（那只是某个装饰元素
  没了，正文元素照写 ⇒ 只标死被点名的那个、保住打字机）。
  真机证据：`tests/probe_render.py --degrade-lane`（生产路径手工关会话后那一帧 `True` +
  `ck_degrade=300309` + `card.create` 仍为 1；三次 patch 全部打在**原来那条 message_id** 上）。

- **自检命令 `/larkdeck status`**（R9）：一张卡回答「插件到底在不在动」—— 版本（**现读**
  `plugin.yaml`；**读不到就写「版本读不到」**，不让版本段静默消失）、生效传输、钩子挂载数，
  以及三条记录：入站心跳 / 最近写卡 / 最近写卡失败（含原始原因）。
  **没记录就写「无记录」，绝不说「正常」**。口径四句话：① 写卡那一行数的是**帧**
  （「累计 N 帧真的有写出」），不是 API 调用次数 —— 一帧可能包含多次写（装饰 batch +
  正文 + 预览），seed 帧是两次网络调用，撞限流时一次逻辑写最多重发四次，全都只 +1；
  ② **每一次真的写卡都记**，包括非 native 的 `send()` 首发、非流式 `edit_message`、
  澄清卡 `send_clarify`、`/stop` 的中止重绘（审计前这些路径一个数都不加）；
  ③ **同一帧只记一次**（降级车道既写元素又整卡 patch 也只 +1）；
  ④ **「写卡失败」只算我们真的发起过写、而它失败了**的帧，不含「没有活跃流可收尾 ⇒
  按契约交核心回落」这种正常返回（那时核心会把消息正常发出去）。
  节流跳过与去重帧**不算**（它们一个字节都没写）。
  ⚠️ 这三条记录是**进程级累计**：多会话并发时卡片上的数字含**别的会话**（卡片里有一句
  口径说明；与页脚指标同源，按会话分桶未做）。
  ⚠️ **在飞书网关里**生成回答期间敲的命令会被排队到回合结束（命令派发只挂核心 idle 路径），
  **CLI / TUI 里可直接执行**（`/larkdeck help` 里也写了这两半）。
- **会话列表预览随回合进展更新**（R7）：飞书会话列表里那行预览文字以前整个回合都停在
  `Hermes`（⚠️ 不是「⏳ 正在生成…」，那是**正文**占位；R7 审计低-1 更正；
  核心在流式期间不管它）——现在**每 ≥5 秒**在文字变化时更新一次
  （`card.settings`；与元素写入共用序号账本、算一次额外逻辑写，靠限频摊薄）。
  写失败只标死 + 一条 WARNING，不影响卡片内容、也不让这一帧失败。
- **页脚可选指标**（R7，`footer_metrics`，**默认 off**）：`basic` 加缓存命中率 `⚡ 75%`
  与本回合 API 次数 `🔁 7`，`full` 再加首字节延迟 `🐢 0.4s`。三个数都从官方钩子载荷直接算；
  缺数据就少一段、**绝不编 0**。成本不做（载荷里没有成本字段，只能估算）。

- **markdown 卫生（写「完整文本」的每条路径）**（R6a，2026-09-14 对抗审计后收口）：模型偶发
  漏一个 `**`（整段被未闭合的加粗包住）时，卡片正文会露出一个**字面的 `**`**；`#` 一级标题
  在卡片里会被渲染成夸张的大字。现在做两步清理：**代码区之外的游离 `**` 删掉**
  （**不补** —— 补一个 `**` 会把后半段整段吞进加粗；删的是**第一个**候选 —— 删最后一个会把
  后文**合法加粗对的闭合标记**拆掉、把中间整段吞进加粗，那比不改更糟）、
  **H1–H3 降级为加粗**；代码围栏与行内代码里的内容**一个字节都不动**。
  覆盖 `send()` / 收尾整卡 / `/stop` 中止重绘 / native 收尾帧 —— 判据是「这份文本是不是
  完整文本」。⚠️ **流式中间帧一个字节都不动**：它们必须严格保持「累积全文的前缀链」，
  中途改写前缀会让用户看到文字跳变（`edit_message(finalize=False)` 与 CardKit 的元素帧
  都在此列）。围栏识别含**未闭合围栏**（核心的边界收尾会把没闭合的累积文本直接送出来）、
  **四反引号**与 **`~~~`** 围栏（审计实测：不认这三种就会把代码内容改坏，而且是「用户先看到
  原文、收尾时突然变样」）。卫生后**再量一次字节**，超上限就退回原文（不让卫生把卡顶爆）。
  函数是纯函数，**幂等**（`f(f(x)) == f(x)`）：审计的两条语料上实测 2379 条穷举 / 6 万篇
  现实语料 / 20 万条随机输入**全部 0 条非幂等**（上一版这里曾写着幂等，而它是假的 ——
  最小复现 `'## 用 ``` 开围栏\n## 建议\n```\ncode\n```\n'`）。

- **澄清点击的失败态不再静默**（R8）：提交未生效（重复点击 / 澄清已过期 / 输入没被接受）时，
  以前卡片一动不动、用户以为「点了没反应」—— 现在弹一条**原生 toast**（`CallBackToast`，
  中英双语）说明原因，**卡片保持原样**。为什么不换一张「失败卡」：那一瞬间卡片
  可能已被前一次点击换成「已确认」，换卡会把用户的确认**退回待答**（不可逆的用户可见错误）。
  成功那一击走**内联换卡**（`CallBackCard`，回调响应里直接带新卡，省一次 API 调用）；
  核心那侧 `_card_response` 不接受卡片实参时自动退回「不改卡但点击仍然生效」
  （能力探测 + 兜底，绝不抛）。顺带修掉一处**既存的测试污染**：某个用例还原 `staticmethod`
  时取到的是解包后的普通函数，导致同进程后续用例里 `self._ld_build_resolved_card(...)`
  抛 `TypeError` 并被点击入口吞掉（表现成「点击走内置回落」，与真实原因无关）。
- **澄清点击的四条提示各说各的话，且不再有一条静默路径**（R8 对抗审计收口）：
  ① 失败 toast 的文案从「提交未生效……**请重试**」改成「这条澄清已被处理或已过期，
  **无需重复点击**」—— 网关返回 `False` 只有「entry 不存在 / `Event` 已 set」两种情形
  （Hermes `tools/clarify_gateway.py:90-98`）⇒ 再点**永远不可能成功**，而旧文案在
  「卡片已显示已确认」时邀请重试，两条信息自相矛盾，还会把用户推去**把答案用文字再发一遍**
  （变成一条新消息，而 agent 早就收到答案了）；② `NO_PENDING`（澄清已消失）与
  `REJECTED_*`（换个说法还能救）**拆成两条**，不再塌缩成一句「请重试」；
  ③ **空提交不再静默** —— 2.0 卡的 `input` 组件 value 里刻意没有 `answer` 键，于是
  「输入框空着回车 / 只输空白 / 多选全取消（`options=[]`）」都落进 `mode == "none"`，
  以前**无 toast、无换卡**（本项目头号失败模式，而且落在**默认方言 2.0** 上）；
  ④ 「无法内联换卡」（老签名核心 / 兜底命中）时**成功那一击也补一条 success toast** ——
  以前完全静默，用户会再点一次，而第二次必然 `not committed` ⇒ 吃一条**误报**的失败提示。
  另：「其他（我直接输入）」的提示**按方言选文案**（1.0 卡上没有输入框，此前却叫用户去输入框）。
- **`tests/mutate_check.py` 自己的一处误判修掉**（门禁 bug）：给 `check_clarify_e2e.py`
  配的失败标记是 `"FAIL: "`，而那个脚本打的是 `FAIL  `（两个空格）/ `FAILED:` ⇒
  这个门禁的**任何断言失败都被算成 `red-crash`**（不造成假绿，但报告里的「断言红」列对
  它恒为空，某条变异只被它抓住时还会让 `mutate_check` 退出码 1）。现在改对标记，并有实测：
  变异 `R8-1` 从 `💥 只有崩溃` 变成 `🔴 断言失败`。

- **超长回答不再掉成纯文本：自动分卡**（R4）。飞书对单张卡有硬上限（整卡 JSON 128000 字节），
  而 native 流式的帧文本是**累积全文**、核心不会替我们按卡片容量切分。现在正文涨到单卡容量的
  一半时，卡组**封掉当前这张卡**（整卡替换 + 关掉流式态 + 末尾一行「（续下一条）」）并**另开一张**，
  新卡**只写剩下的那一段**（不会让你把前半段再看一遍）；切点优先落在换行、并避开代码围栏。
  ⚠️ 已知取舍：封掉的那几张卡不会跟着 `/stop` 变色（只有最新那张会）；切卡那一帧会多花一次写入配额。
- **平台 entry 不再丢字段**：`register_platform` 是**整条替换** `PlatformEntry`、不合并，
  而我们此前只透传 8 个字段 ⇒ 丢掉 `standalone_sender_fn`（**没有常驻网关时 cron 投递会失败**、
  `send_message` 工具同理）、`max_message_length=8000`（长回复不再分块）、
  `apply_yaml_config_fn`（`feishu.allow_bots` 静默失效）。现在键集从 dataclass 字段派生，
  并有「注册前后逐字段比对」的机械门禁（漏一个即红）。
- **撤回守卫**（R5）：消息被撤回/删除（`230011`）或 message id 非法（`99992354`）时，
  标死那条卡、清掉追踪、打一条 WARNING，**不再往那条 message_id 写**。元素写入对撤回**无感**
  （真机实测 `code=0`），所以这条守卫只可能在整卡写入路径上生效。插件**不自己补发**；
  是否重新送达由核心的回落决定（因此 DM 里仍可能出现一条新消息 —— 那是核心行为，不是补发）。
- **装饰批量失败的标死范围收窄**（R5）：服务端在 `msg` 里点名了坏 `element_id`
  （真机实测 `ErrMsg: not find elementID : <id>;`，码 `300313`）**且它确实在这一批里**时，
  只标死那一个、其余装饰下一帧照常写；msg 形状不对（点名上下文 / id 被截断）就退回整批标死。
- CardKit 建实体**补上元素数墙**（递归 200，与飞书同口径）：以前只守字节上限 ⇒
  「面板里塞满子元素」这种形状会被飞书整卡拒收（`300305`），而本地闸门一声不吭。
- CardKit 传输补齐一批**会静默失灵**的路径：回复锚点（回答不再挂在提问下面）、
  写接口的限流退避、发实体卡的幂等 `uuid`、正文长大后的硬上限闸门、
  实体卡必须开 `streaming_mode`、`unified_panel: false` 时不再写不存在的元素 id。

### 内部（用户无感，但决定以后能不能持续改）

- CardKit 写入路径重构为**元素表驱动**（结构由数据决定，不再靠调用点上的 `if`），
  并用**冻结的 golden trace 夹具**证明成功路径逐字节不变。
- 变异验证器：**219 条**「撤掉修复必须变红」的定向变异 + 8 条对照，全绿才算过。
  加门禁的规矩是「先写变异，再写断言」：四门禁全绿而没有对应变异变红 ⇒ 那条断言没有判别力。
- 文档与规则收口：`AGENTS.md`（项目规则）、`docs/lessons.md`（30 条推论）、
  `docs/plan-v1.md`（分阶段方案 + 每轮对抗审计的收口）与 README 均按实测数字同步。

## [0.1.0] - 2026-09-13

首个可用的自我记录基线（**未单独发布 Release**，只打了一个 annotated tag 供对比与回退）。
包含：native 流式单卡、CardKit 逐字打字机、推理 + 工具统一面板、回合状态色、
2.0 澄清卡与回填、双语 i18n、上下文用量页脚、`/stop` 中止重绘，
且**全程不改 Hermes 源码、不 monkeypatch**（`register_platform` + 官方钩子）。
