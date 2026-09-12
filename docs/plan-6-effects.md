# 方案：补齐 aiduPOP 的 6 项卡片效果 + 清掉审计遗留

> 状态：**草案，待方案审计**。第 5 条（clarify）有一个未决的决策门，见 §4。
> 目标来源：`https://github.com/monkey2jack/aiduPOP` README 的「效果展示」6 条。

## 0. 约束（不可协商，来自 `AGENTS.md`）

1. **绝不改 Hermes 源码、绝不 monkeypatch**，只用公开契约。
2. **卡片失败必须回落官方实现**（宁可纯文本，绝不丢消息）。
3. **Hermes 私有名只允许出现在 `compat.py`**，且必须登记 + 运行时探测。
4. **要接服务端点击的卡只能是 legacy 1.0**（本项目真机实测；本地佐证：Hermes 官方
   飞书适配器 `_card()` 自身也是 1.0，且其按钮回调只认自己的载荷键）。
5. **观察者纪律**：钩子回调只写内存、微秒级返回、异常自吞、永不返回 directive。
   特别地 **禁用 `transform_*` 类钩子**（那是改行为的，不是观察）。

## 1. 六项效果 → 数据源 → 可达性

公开钩子菜单已实测确认为 **37 个**（`hermes_cli.plugins.VALID_HOOKS`）。逐条对齐：

| # | 效果 | 数据源 | 判定 |
|---|---|---|---|
| 1 | 即时响应 + 打字机 | 已有 native streaming 的 **seed 帧**（第一个 token 前就建卡）；「打字机」= 帧节流 `_STREAM_MIN_INTERVAL`；「无输入提示 / 无 `回复：` 前缀」= 不发 typing、不带 reply 前缀 | **大半已有**。节奏上限待实测（飞书更新接口限流 vs 核心帧派发，两条瓶颈都要量） |
| 2 | 完成态绿色面板 | **新增订阅 `on_session_end`**（每回合一次）→ `completed`；改 **`collapsible_panel.border.color`**（不是 `header.template`，见 §6）为 `green`。面板头显示 模型名 / 思考轮次 / 工具数 / 耗时 | **可达**，路径干净 |
| 3 | 中止 / 报错态 | **同一个 `on_session_end`**：`interrupted` → `yellow`、`failed` → `red`。⚠️ **判定优先级必须 `interrupted > failed > completed`** —— 官方 `completed` 的表达式**不含 `interrupted`**，被中断但已有部分正文的回合会 `completed=True` 且 `interrupted=True` | **可达**（原方案猜「中止没有公开信号」是错的，见 §4 R1） |
| 4 | 展开面板 + 「时间戳」 | 面板已有 `collapsible_panel`。「时间戳」**实为相对耗时**：轮次 `第 N 波 · X.Xs`、工具 `Name (36 ms)`。轮次需把推理**按打断切段**（现在我们是整回合拼成一坨）；工具耗时有 `post_tool_call.duration_ms` | **可达**，需新增切轮逻辑 + `panel_expanded` 配置项 |
| 5 | Clarify 交互式选项卡 | **待调研** —— aiduPOP 声称「原生 Cardkit 2.0」，但已查实它是 patch 型（§6），所以它这条大概率也靠改写核心 | **决策门**，见 §4 |
| 6 | Clarify 回填 + 确认徽章 | 已有 `clarify_resolved_card`（✅ + 用户名）+ 上一轮刚修的「提交未生效不回填」 | **已可达**，只差「确认徽章」的视觉 |

## 2. 阶段划分

每个阶段都遵守同一套收口：**改完跑四门禁 → 起一个独立子 Agent 做对抗性代码审计 →
审计通过才进下一阶段**。四门禁 = `test_units.py` / `check_override.py` / `check_hooks.py`
/ `check_clarify_e2e.py`；动到卡片结构时**额外跑 `probe_render.py`**（真发卡，需授权）。

### 阶段 0：清掉上轮审计遗留（先修地基，再加功能）

在动新功能前把已知缺陷清掉，否则新功能会长在坏地基上。按文件聚合，便于一次性审计：

| 缺陷 | 位置 | 修法 |
|---|---|---|
| 事件循环被阻塞 HTTP 卡住 | `context.py:context_max` | 缓存未命中时起一次性守护线程探测 + in-flight 标记；本次渲染返回 `None`（页脚退化成只显示已用量），后续渲染取到值 |
| fail-closed 路径上持全局锁做全量 `json.dumps` | `panel.py:_args_preview` | 预览计算**移出锁**；并给序列化加**大小上限短路**（超大 args 不序列化，直接给占位符） |
| 淘汰会踢掉**仍在活跃**的长回合 → 重复卡 + 永久流式态卡 | `adapter.py:_ld_stream_put` | 改按 `last_at`（最近活动）淘汰而非 `t0`；**只淘汰确实陈旧的流**；容量真满时告警而非静默踢 |
| 迟到的旧回合事件被当成新回合 → 清空新回合面板 | `panel.py:_touch_locked` | 每会话维护一个有界的「已作废 turn_id」集合；`begin_turn` 换回合时把旧的记进去；迟到事件命中即丢弃 |
| `_ld_track` 淘汰后回落 `edit_message` 对 interactive 卡无效 → 卡片冻结 | `adapter.py:_ld_track` | 同样改按最近活动淘汰；淘汰时**主动 forget**（避免留下无法更新的追踪项） |
| `_ld_setup()` 在 try 之外 → 构造期异常穿出工厂掀翻平台 | `adapter.py:build_adapter` | 移进 try；失败回落 `base_factory(config)` |
| 截断不感知 markdown → 未闭合 `**` / 反引号 / 围栏被切断 | `cards.py:truncate` | 截断后做一次**闭合修复**（统计未闭合的 `**`/`` ` ``/``` 并补齐或整体回退到更早的安全点） |
| 按码点切会切断 ZWJ 组合字形（emoji 家族） | `cards.py:truncate` | 截断点回退到字形边界（跳过 ZWJ / 组合字符 / 变体选择符） |
| 正文（AI 答案）无长度上限 → 超长被飞书拒收 | `cards.py:reply_card` | 加长度上限 + 超限时在卡片内留痕；上限值待实测 |
| 回填卡的「其他」标签不双语 | `cards.py:clarify_resolved_card` | 改用可承载 `i18n_content` 的节点（**需真机验方言**） |
| 工具名未清洗（含反引号/换行会破坏行内代码） | `cards.py:tool_step` | 与 preview 同rules 清洗 |
| `compact_tokens` 进位毛刺（999950→"1000k"）/ `nan`、`inf` 硬化 | `cards.py` | 边界收口 + 非有限值直接返回空串 |
| 版本探测 2/3 层是死代码（`hermes_constants` 里没有那些属性） | `compat.py:hermes_version` | 删掉永远返回 None 的层；保留有实际命中的路径 |

**验收**：四门禁全绿；每条缺陷都有**能抓住它**的回归测试（不是"测试通过"，是"修掉修复会红"）。

### 阶段 1：状态颜色（第 2、3 条）

- 新增订阅 `on_stream_end`（登记进 `compat.py` 的钩子名清单 + `has_hook` 复核）。
- `panel.py` 增加**回合状态**：`running` / `ok` / `error` / `stopped`。
- 卡片渲染按状态改 `header.template` 与面板 `border.color`。
- **必须先实测飞书接受哪些颜色枚举**（`border.color` 的合法值），用 `probe_render.py` 验。

**验收**：三种状态在真机上各自渲染正确（`probe_render` 加对应探针卡）。

### 阶段 2：面板信息密度（轮次 + 时间戳，第 4 条）

- 面板头补「思考轮次」（`iteration`）与工具数；正文在上、面板在下（现已如此）。
- 每个工具步骤补起止时刻；格式待定（绝对时钟 vs 相对耗时，倾向相对 + 首次绝对）。
- 面板默认收起保持。

**验收**：四门禁 + `probe_render` 真机确认展开态渲染。

### 阶段 3：即时响应与打字机（第 1 条）—— 核心是把传输换成 CardKit

**调研结论彻底改了这一条的做法。** aiduPOP 的「打字机 15ms」**不是服务端刷新间隔，而是飞书客户端的逐字打印频率**：
卡片 JSON 里的 `streaming_config = {"print_frequency_ms": {"default": 15}, "print_step": {"default": 1},
"print_strategy": "fast"|"delay"}`。官方文档明确：流式模式下你推**全文**，**平台自己算增量、逐字渲染打字机**。

**为什么我们现在拿不到打字机**：我们用 `im.v1.message.patch` 整卡替换。官方 `message.patch` 文档写的是
**单条消息更新频控 5 QPS**，且打字机的官方路径指向**组件级** `cardkit.v1.card_element.content`。
所以这笔改动的价值点是**拿到客户端打字机动画**，不是「更高帧率」。

**两条独立天花板（都拦死「每 15ms 推一帧」）**：
1. **Hermes 核心 ≈20 帧/秒**：`stream_consumer.py` 的 `run()` 是 `await asyncio.sleep(0.05)` 轮询循环，
   把所有 delta 合并成一个 tick、每轮最多一次 `send_stream_frame`，且串行 await。**不改核心上不去。**
2. **飞书 API**：`message.patch` 单条消息 **5 QPS（≥200ms/帧）**；CardKit 流式 **单卡 10 次/秒（≥100ms/帧）**。
   我们现在的 `_STREAM_MIN_INTERVAL = 0.25`（4/s）**已经在 5QPS 的 80% 位置**。

**做法（不改 Hermes 一行）**：把 `send_stream_frame` 的落地从 `message.patch` 换成
**CardKit 卡片实体 + `card_element.content`**：`cardkit.v1.card.acreate` 建实体 →
`im.v1.message.acreate` 把卡发到聊天 → 逐帧 `cardkit.v1.card_element.acontent(card_id, element_id, 全文, sequence)` →
`cardkit.v1.card.asettings` 封口。这些 API 都在 Hermes venv 自带的 `lark_oapi` 里，而
**Hermes 0.21.1 核心一行都没用过 cardkit**（排除 venv grep = 0），所以是纯 SDK 调用，不碰源码。
服务端帧率**维持 150~200ms 即可**（正是 aiduPOP 的「150ms 推 + 15ms 打」）。

**必须保留的回落**：新传输任何一步失败 → 回落现有 `message.patch` → 再失败回落 `super()`。
这是本项目的不变量 2，`CardKit` 路径必须走同样的 fail-open 链。

**同时纳入的三个零 patch 借鉴**：
- `FEISHU_REACTIONS=false` 关掉 Hermes 在用户消息上打的 `Typing` 表情（公开配置；aiduPOP 写了
  抑制 wrapper 却**把安装那几行注释掉了**，所以它默认并没关）。
- **空闲攒批**：距上次刷新 > 2s 时不立即发，再等 100ms 合并一批 —— 降低上游 LLM 抖动重试被误当成新段。
- **限频当瞬态重试**：`99991400`（限频）/ `300309`（streaming closed）/ `300317`（sequence conflict）
  纳入退避重试（0.1/0.3/0.6s，3 次）。与现有主动节流互补。

**待真机核实（不猜）**：`message.patch` 整卡替换到底能不能触发 2.0 卡的打字机动画。若答案是「能」，
阶段 3 就退化成只调 `streaming_config`，工作量大幅缩小 —— **所以阶段 3 的第一步是先花一张探针卡验证
这件事，而不是先写代码。**

**验收**：真机上肉眼确认逐字动画；量出实际刷新节奏；`probe_render` 覆盖新传输路径。

### 阶段 4：Clarify（第 5、6 条）—— 依赖决策门

见 §4。

### 阶段 5：洁癖收尾 + 交付

- neat-freak：代码 / 运行态 / 文档 / 规则四方对齐；`docs/lessons.md` 补新踩的坑。
- commit（每阶段一个 commit，message 尾部附实测门禁行）+ push。
- Mac 插件同步到最新并生效（软链已最新，重启网关 + live 验证新代码独有日志行）。

## 3. 与「6 项效果」无关但要一起交代的

- `panel.title_tools_one` 之类新增 i18n 键要双语齐全。
- 新增配置项必须同时进 `_DEFAULTS` 与 `plugin.yaml.config_schema`（README 同步）。
- 每加一层能力，配一个「能自证」的东西（门禁断言或限流诊断日志）—— 本项目最怕静默失败。

## 4. 风险与决策门

**决策门 D1（已解，2026-09-12）：2.0 澄清卡可行 —— 我们不变量 5 的原表述是错的。**
- **结论：aiduPOP 的 clarify 卡是真的 schema 2.0，点击回调也真的到得了 `p2.card.action.trigger`。**
  链路是组件级 `behaviors: [{"type":"callback","value":{...}}]`，组件用 `select_static`
  （回调带 `action.option`）与独立 `input`（回调带 `action.input_value`），**没有 `form`、没有 `action` 行**。
- 证据三重：官方文档（本机 `lark-im` skill 的官方参考包逐项吻合，且明确「按 `"schema":"2.0"` 判版本」、
  `card.open_ids` 是 1.0 only）+ aiduPOP 实拍图（用户选完后同一张卡原地变「已选择 + 已确认」、
  Agent 回「收到～大叔选了生活节奏」）+ 它的生产日志（`callback received action=select`）。
- **旧结论错在哪**：当时那张「2.0 澄清卡」用的是 **没有 `behaviors`、只有顶层 `value` 的 1.0 按钮**
  放进 2.0 的 `body.elements` —— 那是「2.0 卡里放 1.0 组件」这个病；而论据抄自第三方插件的
  **代码注释**（且该插件自己的代码与那句注释自相矛盾）。时间线也支持"从未真机验过"。
  → 已同步更正 `AGENTS.md` 不变量 5。
- **路径 A（采用）**：待答卡改 2.0（选项用 `markdown` 或 `div`+`lark_md`；**禁 `note`** → 用 `footnote()`；
  **禁 1.0 `action` 行**）+ `select_static` + `input`，各带 `behaviors` callback；回填态**也一起改 2.0**
  （不能一半 1.0）。拦截键仍是 `larkdeck_action`，`event.action.value` 原样透传 →
  **`_on_card_action_trigger` 的拦截、授权检查、`resolve_gateway_clarify` 一行都不用改。**
- **第一步必须是探针**（成本极低）：往 `probe_render.py` 加一张只带 2.0 `select_static` + `input`、
  `value` 用独立探针键的卡，点击时打 INFO 日志。**你点一下下拉 + 回车，就能把这条从"三方印证"
  升级成一手真机事实**，且完全不碰生产路径。
- **保留回退**：加方言开关，默认先 1.0；探针验证通过后再翻默认。`clarify_cards` 配置项已在。

**决策门 D2（已拍：去掉 card-level header）。**
- 按你的选择走 (b)：**去掉 header，信息全压进 `collapsible_panel` 自己的 header**
  （模型名 / 轮次 / 工具数 / 耗时），最接近 aiduPOP 观感。
- 注意：aiduPOP 删 header 的直接原因是它用 cardkit `batch_update` **改不了 card-level header**；
  我们走整卡 `message.patch`，所以删 header 是**主动的观感选择**，不是被迫 —— 也因此
  「header 跟状态变色」这条路我们本来有，选了 (b) 就用不上了（信息已在面板头）。

**决策门 D2（不阻断，但要你拍）：card-level header 留不留？**
- aiduPOP **删掉了** card header（断言 `"header" not in card`），把模型名与统计搬进面板自己的 header。
- 我们保留蓝色 card header。**保留不影响绿边/红边效果**，只是观感与它的截图不同。
- 选项：(a) 保留蓝头（改动小、保持现状）；(b) 去掉 header、信息全压进面板（更接近 aiduPOP 观感）；
  (c) 保留 header 但让它也跟着状态变色（我们走整卡 `message.patch`，理论上可改；aiduPOP 做不到是因为
  它用 cardkit `batch_update`）。

**R1（已解除）：「用户中止」没有公开信号 —— 原判断是错的。**
- 原推断：`_with_stream_emitters` 用 `except Exception` 包 `run()`，`CancelledError` 继承
  `BaseException`，所以中止不发 `on_stream_end`。这条本身没错，但**结论错了** ——
  有更合适的钩子。
- **实测到的正解**：`on_session_end`（`agent/turn_finalizer.py` 的 `finalize_turn`）虽然名字叫
  session，**实际每回合触发一次**，载荷是
  `completed` / `failed` / `interrupted` / `turn_exit_reason` —— 正好是官方对「完成 / 报错 / 中止」
  的权威判定。
- **时序红利**：它在 `run_conversation()` 内**同步**执行（受 30s 超时约束，回调跑完才返回），
  而流式收尾帧在 `run_conversation()` 返回**之后**才发（`gateway/run_turn_runner.py` 的
  `_finish_stream_consumer`）。**所以收尾那一帧里已经能读到本回合结局，不需要额外补一次 edit。**
- ⚠️ **两个必须绕开的坑**：
  1. **判定优先级 `interrupted > failed > completed`**。官方 `completed` 的表达式
     （`turn_finalizer.py`）里**没有** `interrupted`，所以「被中断但已产出部分正文」的回合会
     同时 `completed=True, interrupted=True`。先看 `completed` 就会把中止显示成完成。
  2. **关联靠不住**：`on_session_end.turn_id` 与 `send_stream_frame(turn_id=...)` **对不上**
     （后者是 StreamConsumer 自己的 uuid4），且钩子载荷**都没有 chat_id**。只能按 chat 级
     pending 槽在 finalize 时消费 —— 多会话并发会串台，与现有面板同一局限，**必须在文档里写明**。
  3. 另需过滤会话级收尾（`/new`、cron 推送等路径也可能发 `on_session_end`），
     按 `turn_id` 为空 / `platform` 取值做防御，别误判成一次失败回合。

**R2（中，影响第 1 条）：15ms 是宣传值，不能照抄。** 必须实测飞书限流，否则会把插件
做成被限流的重试机器。方案里只写「实测上限」。

**R3（低，影响阶段 1/2）：颜色枚举与时间戳格式都可能被飞书静默忽略。**
- 缓解：`probe_render.py` 必须覆盖新样式；本地单测只验结构。

**R4（低）：新增钩子订阅扩大了与官方的耦合面。** 每个新钩子名都要进 `compat.py`
清单，并在启动时用 `has_hook` 复核（上一轮刚修的那个假成功问题）。

## 5. 调研结论：aiduPOP 到底怎么做的（一手源码 + 官方文档）

**判定：它是重度 patch 型（monkeypatch），不是插件型。** 铁证：`patching/__init__.py` 的模块 docstring
是 `Runtime monkey patching`，`register()` 里调 `apply_patches()`；`patching/__init__.py` 直接给类赋方法
（`FeishuAdapter.send = _wrap_...`、`edit_message`、`send_clarify`、`_handle_card_action_event`），
另 patch `GatewayRunner._handle_message / _run_agent / _run_conversation / cron`。
全仓库只有**一处** `ctx.register_hook()`（挂 `/aowen` 斜杠命令），**零** `register_platform()`，
且**完全没有实现** `SUPPORTS_NATIVE_STREAMING` / `send_stream_frame`。
`plugin.yaml` 里那串 `provides_hooks` 是误导 —— `patching/hooks.py` 自己写明那些函数是
「被 patch 的 Hermes 方法调用」的，文件里标了 **11 个注入点**。

**但它的机制要分开看**：卡片构造 + CardKit 推送（`feishu/client.py` 全是 `self._client.cardkit.v1.*`
/ `im.v1.*`）是**纯 SDK 调用、可移植**；而「首帧时机」与「流式帧来源」是从被 patch 的核心钩子抠的，
**不能照抄**。我们发现它真正需要的数据，公开契约都有等价或更好的来源。

### 六条效果各自的真实来源

| 效果 | aiduPOP 的做法 | 我们能不能做 |
|---|---|---|
| 1 即时响应 | patch 在消息入口发 seed 卡 | ✅ **不需要抄**：`stream_consumer_transport.py` 的 `_try_seed_frame` 在 `run()` 开头就发 `send_stream_frame("")`，同样早于首 token |
| 1 打字机 | CardKit `streaming_config.print_frequency_ms=15`（**客户端**逐字频率） | ✅ 能做，但要**换传输**（见阶段 3） |
| 2 完成态绿面板 | `collapsible_panel.border.color` | ✅ 能做（`on_session_end`） |
| 3 中止/报错变色 | 从被 patch 的函数抠 `session._was_aborted` / `session.state` / `error_message` | ✅ 能做（`on_session_end` 的 `interrupted`/`failed`/`completed`） |
| 4 轮次 + 耗时 | 自维护状态机按「推理段 + 打断」切轮 | ✅ 能做（需新增切轮逻辑） |
| 5 Clarify 2.0 选项卡 | `send_clarify` / `_handle_card_action_event` 被 wrapper 包住 | ⏳ **调研中**（决策门 D1） |
| 6 Clarify 回填 | 自己的控制器状态机 | ✅ 我们已有 |

### 三个必须记下来的「反直觉」事实

1. **README 的配色映射写反了。** README 三处说「红=中止、黄=报错」，源码是
   `border_color="green" if not is_error and not is_aborted else ("red" if is_error else "yellow")`
   —— **green=完成 / red=报错 / yellow=中止**。调研员还用 PIL 对截图做了像素验证：
   `03-panel-stopped.png`（文件名就是 stopped，正文是 `/stop`）边框是暗琥珀 RGB(128-143,80-95,16-31)，
   **不是红色**。**以源码为准，别照抄它的 README。**

2. **变色改的是 `collapsible_panel.border.color`，不是 `header.template`** —— 而且这不是随意选择：
   它的 CHANGELOG 记录了**主动删除** header 颜色切换，理由是「CardKit `batch_update` 不支持更新
   card-level header」。**边框方案是被 header 方案的失败逼出来的。**
   （对本项目的差异：我们走整卡 `message.patch`，不是 cardkit batch_update，所以理论上可改 header；
   但 `border.color` 已是现成杠杆，优先用它。）

3. **README 说的「时间戳」其实是相对耗时**：轮次渲染成 `第 1 波 · 6.2s`、工具渲染成
   `Run command (36 ms)`，全是 `time.time()` 差值；`ReasoningRound.start_time`（绝对 epoch）
   **存了但全仓库从未渲染**。

### 它的「轮次」定义（与我们的不同）

**一轮 = 一段连续的推理文本**，被「正文开始」或「工具调用」打断 —— **不是** API 调用轮次，
也**不是**工具轮次。计数点在 `state/linear.py` 的 `on_reasoning_delta` / `on_answer_delta` / `on_tool_event`。
我们现在的 `panel.py` 把整回合推理**拼成一坨**，要复现这个观感必须引入「按打断切段」。
（备选：`post_api_request.api_call_count` / 流式载荷的 `iteration`，但那是 API 次数，语义不同。）

### 一处结构性差异，需要你做决定

**aiduPOP 删掉了 card-level header**（`tests/test_v220.py` 断言 `"header" not in card`），把模型名与统计
搬进 `collapsible_panel` 自己的 header；我们保留着蓝色 card header。保留蓝头**不影响**绿边/红边效果，
但观感会与它的截图不同。→ **决策点 D2，见 §4。**


## 6. 每阶段的固定收口动作

1. 四门禁全绿（+ 动卡片时 `probe_render`）。
2. **新回归测试必须先证明能抓住对应缺陷**（撤掉修复会红）。
3. 起一个**独立子 Agent** 做对抗性代码审计（只读，只报能导致错误行为的问题）。
4. 审计发现分级处置：真问题当阶段内修完；重构级建议记入下一阶段。
5. 文档/rules 与代码同源更新，再 commit。
