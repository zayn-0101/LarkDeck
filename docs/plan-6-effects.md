# 方案：补齐 aiduPOP 的 6 项卡片效果 + 清掉审计遗留

> 状态：**实施中**。阶段 0 / 1 / 2 已完成（进度与实测更正见 §9）；第 5 条（clarify）的
> 决策门 D1 已解，见 §4。
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

**aiduPOP 删掉了 card-level header**（**它仓库里的** `tests/test_v220.py` 断言 `"header" not in card`），把模型名与统计
搬进 `collapsible_panel` 自己的 header；我们保留着蓝色 card header。保留蓝头**不影响**绿边/红边效果，
但观感会与它的截图不同。→ **决策点 D2，见 §4。**


## 7. 方案审计结论与设计更正（2026-09-12，两路独立审计）

两路方案审计逐条对照 Hermes 真实源码后，**推翻了本方案阶段 1 的核心设计、并纠正了阶段 2/3 的多处事实**。
下面每条都是「原方案写的 → 源码事实 → 改成什么」。**实施时以本节为准。**

### 7.1 阻断级：阶段 1 的状态语义全错，必须重做

| 原方案 | 源码事实 | 更正 |
|---|---|---|
| `on_stream_end(finished=True)` = 成功 → 绿 | **流中途掉线被核心故意转成正常返回的 stub**（`agent/chat_completion_helpers.py` 的 `_finish_chat_stream`：工具参数没传完 / 纯文本无 finish_reason 无 usage 都 `return _build_partial_stream_stub(...)`）→ emitter 报「成功」 | **断线的回合会被渲染成绿色**。`finished=True` 只能当「这次调用没抛异常」的弱证据，**不能单独当绿** |
| 「每回合一次 end，用最后一次判定」 | **三层放大**：工具轮（N+1 起）× stream 重试（默认 2 → 最多 3 次）× API 级重试。**中间每一次都是 `finished=True`** | 长工具回合里卡片会**一直是绿色**。颜色必须由**回合级状态机**决定，不是「读最后一次 end」 |
| 「中止不发 end，要推断」 | **中止会发 end**：中止不是 asyncio 取消，是协作式标志 + socket abort → `agent/interrupt_control.py` 置标志 → 抛 `InterruptedError`（**`OSError` 子类，属于 `Exception`**）→ 被 `_with_stream_emitters` 的 `except Exception` 接住 → `on_stream_end(finished=False, error="Agent interrupted during streaming API call")` | 但**中止与报错在载荷上不可区分**（无 `reason`/`cancelled` 字段）。**绝不要对 `error` 字符串做分类**（那正是 `lessons.md` 明令禁止的） |
| 「中止靠推断」 | **有现成的公开契约**：`BasePlatformAdapter.interrupt_session_activity(session_key, chat_id, metadata=None)`（`gateway/platforms/base.py`），由 `gateway/run_agent_cache.py` 在 `/stop`、`/new` 路径调用。**带 `chat_id`** → 直接解决多会话归属 | 覆盖它（我们已经在子类化那个适配器）+ `super()` 回落。**签名必须带 `metadata` 或 `**kwargs`**（核心用 `_accepts_keyword` 特性探测）。按不变量 3 要登记进 `compat.py` 新的一组「信号型适配器契约」并用 `probe_report` 上报 |
| 「渲染时读状态就知道该上什么色」 | **中止后卡片根本不会被重绘**：`/stop` 让 consumer 直接 return（`gateway/stream_consumer.py` 的「Session reset: abandon rather than deliver stale deltas」），而 `_abandon_native_stream` 对 native 模式是**空操作**（`stream_consumer_transport.py` 里 `if not self._use_draft_streaming: return`）→ **永远不会有 finalize 帧** | 插件**必须自己**在被通知时（`interrupt_session_activity` 内，那里有 async 上下文与 `chat_id`）主动把那张卡重绘成 stopped。否则状态改了、卡片不变、还不报错 |
| 未提及 | **非流式模式下 `on_stream_start`/`on_stream_end` 完全不触发**（只在 `_with_stream_emitters` 的 3 个调用点发出，非流式走 `agent/turn_api_call.py`，没有任何 emitter） | 必须有一条**显式降级路径**（例如以 `post_api_request` 作为「回合有活动」的弱替代），并在文档里写清「颜色可信范围」 |
| 「报错 → `api_request_error` → 黄」 | **每次失败尝试都发，包括随后重试成功的**（`agent/turn_api_error.py`、`agent/turn_response_check.py` 里带 `retryable=True`） | 判据要用它自带的 `retryable` / `retry_count >= max_retries`，否则**自愈的回合会闪黄** |

**另有一条跨阶段的阻断项（见 7.4）：卡归属问题没解决，而阶段 1/2/3 全都站在它上面。**

### 7.2 阶段 2 的三处小改（可做）

1. **`iteration` 必须改标签**：它 = `agent._api_call_count`，在**每轮工具循环的 API 调用之前** +1，
   而且有**退款**（compaction / fallback / redirect 重启会把编号退回去）→ 显示上会出现编号重复。
   重试**不计入**。所以标成「**工具轮次 / 第 N 步**」是准确的；标成「思考轮次 / API 调用次数」是误导。
2. **`duration_ms == 0` 有歧义**：0 是默认值，被 block / inline 路径 / 参数非法都会发 0，
   而 `panel.py` 现在把 0 当**真值**（`_as_int(0)` 是合法 int）→ 「自己算差值」的兜底**永不触发**，
   被 block 的步骤会显示「0ms」。判据只能用 `status == "blocked"` 或让 0 走兜底。
3. **时间源只有一个**：沿用 panel 里已有的 `t0 = time.monotonic()`，不要引入第二套。
   注意 `_TTL_SECONDS` / `_MAX_SESSIONS` 的淘汰会**在长回合中途**删掉 `t0` 让差值变成垃圾 ——
   所以**阶段 2 不能独立于阶段 0 的淘汰修复上线**（阶段 0 已修）。

### 7.3 阶段 3 目标要改，优先级最低

- **`edit_interval` 与本项目路径无关**：native 模式下核心**完全不看**它
  （`gateway/stream_consumer.py` 里 `if self._use_native_streaming: # No platform edit-rate limit:
  push every delta immediately`），那段带 `edit_interval` 的判定只在 `else` 分支。
  核心对 `send_stream_frame` **没有最小间隔限制**（那个被注释成节流器的字段只写不读，是死代码）。
- ~~**⇒ 插件侧的 `_STREAM_MIN_INTERVAL` 确实是当前唯一瓶颈，调小确实能提高刷新密度。**~~
  **2026-09-13 真机实测推翻了这一条**（`probe_render.py --rate-limit`，16 次连打一张探针卡）：
  单次 `message.patch` 往返 **≈ 440~530ms**（偶发 1.5s），16 次连打**一次都没被拒**。
  ⇒ 天花板是 **API 往返 ≈ 0.5s/帧 ≈ 2 帧/秒**，而我们的节流常量是 0.25s —— **常量不是瓶颈**，
  调小到 0 也不会更快（串行 await 的往返摆在那里）。
  ⇒ 想让观感更顺，唯一有效的杠杆是**客户端打字机**（`streaming_config`）：它是客户端动画，
  与我们推帧的快慢无关。这也把阶段 3 的优先级从「调帧率」改成了「加三行 JSON」。
- **但真实风险比原方案写的严重得多，而且机制不同**：一帧失败会**永久关掉本回合的 native 流式**
  （`stream_consumer_transport.py` 定义性失败后置 `_use_native_streaming = False`），
  后续输出改走 `send()`（可能变成**多条纯文本消息**）；而**飞书的限流错误不匹配 flood 启发式**
  （错误被格式化成 `[code] msg`，而 flood 只匹配 `flood`/`retry after`/`rate`）
  → 走硬失败分支、**连自适应退避都不会启动**。
  ⇒ 方案里必须写明：调小它的代价可能是「**本回合静默降级成多条纯文本**」，并配一个
  **能自证 native 仍在工作**的诊断日志（`lessons.md` 推论 1 的要求）。
- **前置确认**：本地 `native_streaming` 配置是否开启。**若为关**，实际走 edit 路径，
  本章结论**反过来** → 阶段 3 第一步必须先确认这件事。

### 7.4 归属设计（**已解，2026-09-12**）：用 `pre_gateway_dispatch` 建 `chat_id → session_id` 映射

`turn_id` 两套命名空间的问题**不能被修**（那是核心内部的实现事实），但**可以绕开**：
我们要的从来不是 `turn_id`，而是「这张卡属于哪个会话」。而这一层有干净的公开契约可用。

**桥梁（全部是公开 API，且只读）：**

1. **`pre_gateway_dispatch`** —— 在 `VALID_HOOKS` 里，**每条入站消息**触发，且**在 auth 之前**
   （`gateway/run_inbound.py` 的 `_hm_pre_gateway_dispatch_hook`）。载荷是
   `event`（`MessageEvent`）、`gateway`、**`session_store`**。
2. `event.source` 是 **`SessionSource`**（`gateway/platforms/event.py` / `gateway/session.py`），
   带 `platform` 与 **`chat_id`** —— 正是适配器那侧有的东西。
3. `gateway/session.py` 有**公开**（无下划线）的
   `build_session_key(source, ...) -> str` 与
   `SessionStore.lookup_by_session_key(session_key) / lookup_by_session_id(session_id) -> SessionEntry`，
   而 `SessionEntry` 同时带 `session_key` 与 **`session_id`**（钩子那侧用的就是它）。

**⇒ 回调里算 `build_session_key(event.source)`，再 `lookup_by_session_key(...)`，就拿到 `session_id`，
把它和 `event.source.chat_id` 一起记进 `panel`。适配器渲染时按自己的 `chat_id` 取对应会话 —— 确定性的，
不再靠「最近活跃」猜。**

**三条必须遵守的纪律：**

- **只读**：用 `lookup_by_session_key`，**不要**用 `get_or_create_session` —— 后者会在 auth **之前**
  给未授权发送者创建会话，那是改变核心行为（违反不变量 1 的精神）。代价是**新会话的第一回合**
  还没有映射（那一刻 session 尚未落库）→ 该回合退回「最近活跃」，**从第二回合起确定性**。
- **永不返回 directive**：这是个能干预分发的钩子（`{"action": "skip"}` / `"rewrite"`），
  我们只观察，**必须恒返回 None**。
- **回调必须快**：它在每条入站消息的分发路径上。只做「查一次 + 写一次内存」，不做 I/O。
- 这些名字（`pre_gateway_dispatch`、`build_session_key`、`lookup_by_session_key`、`SessionSource.chat_id`）
  按不变量 3 **登记进 `compat.py`**（新增一组「会话归属契约」），并用 `probe_report` 上报缺失。

**保底**：映射拿不到时（新会话首回合、老版本 Hermes、查找失败）**必须保持现有行为**
（退回「最近活跃」），不能因为归属失败而不渲染面板。

> **2026-09-13 补充（阶段 1 审计的阻断项）**：这条「保底」有一个**必须同时成立**的边界 ——
> **有绑定、只是那个会话暂时没内容**时，绝不许退到「最近活跃」（那会把别的会话的面板与
> 颜色画到这张卡上，也会让 `/stop` 的中止状态写到别的会话）。「暂时没内容」的正确表现是
> **这张卡没有面板**。只有**没有绑定**才回退。

### 7.5 另外三条事实纠正

- **`core/hooks.py` 开头那句「最坏情况也只是面板少一行，不可能拦住任何工具」在超时路径上是错的。**
  默认 30s 超时（`hermes_cli/plugins_dispatch.py` 的 `_HOOK_CALLBACK_TIMEOUT_SECS`）后，
  核心会补 `{"action":"block"}` → **工具被拦掉**，且抑制窗口内会被判 skip 同样按 block 处理。
  阶段 2 若往 pre 回调里加任何阻塞物，就是把这个后果从「少一行」升级成「拦工具」。
- **`prune` 阶段 3 的两个「待验证项」是空动作**：飞书 `send_typing` 是空实现（「Feishu bot API does
  not expose a typing indicator」），且**飞书没有 reply 前缀**（只有 WhatsApp 实现有）。
  这两条已由源码确认，**不需要做任何改动**。
- **§0 约束 4 与决策门 D1 已过期**（不变量 5 已于 2026-09-12 更正：2.0 组件级 `behaviors`
  **能**接服务端回调）。以更正后的 `AGENTS.md` 为准。

### 7.6 审计建议的实施顺序

先阶段 0 + 阶段 2（低风险、纯加法），**阶段 1 按 §7.1 重新设计后再做**（引入
`interrupt_session_activity`、去掉推断、先解决 §7.4 的归属问题），**阶段 3 最后做且必须先测量**。

## 8. 每阶段的固定收口动作

1. 四门禁全绿（+ 动卡片时 `probe_render`）。
2. **新回归测试必须先证明能抓住对应缺陷**（撤掉修复会红）。
3. 起一个**独立子 Agent** 做对抗性代码审计（只读，只报能导致错误行为的问题）。
4. 审计发现分级处置：真问题当阶段内修完；重构级建议记入下一阶段。
5. 文档/rules 与代码同源更新，再 commit。

## 9. 实施记录（进度 + 实施期发现的更正）

### 已完成

| 阶段 | commit | 门禁 | 备注 |
|---|---|---|---|
| 阶段 0（清审计遗留 + 归属层） | `ee2ac91` `520f1a2` | 四门禁全绿 | 含「一个环境变量就能静默打死插件」的缺陷 |
| 阶段 2（推理按轮分段 + 每轮耗时 + `panel_expanded`） | `e1b5a1a` + `866fd3f` | 四门禁全绿 | `866fd3f` = 阶段 2 对抗审计的三条必修项（面板被撑爆 / 幻影长度 / 假轮）+ 6 条新哨兵 |
| 阶段 1（三态状态色 + 决策 D2 去掉卡片 header） | `8e01490` | 四门禁全绿 | 见下面三条更正 |

### 实施期发现的更正（**比方案里写的更准，以这里为准**）

1. **钩子之间的 `turn_id` 是同一个值 —— 方案 R1 的第 2 条说反了。**
   `on_stream_start` / `on_stream_delta` / `on_session_end` / `post_api_request` 读的都是
   `agent._current_turn_id`（`agent/turn_context.py::_bind_turn_identity` 每回合设一次，
   `finalize_turn` 的 `turn_id` 参数也来自它）。对不上的**只有**
   `send_stream_frame(turn_id=)` —— 那是 `GatewayStreamConsumer` 自己生成的裸 uuid4。
   ⇒ 结论：**回合状态可以按 `turn_id` 与面板数据精确对齐**，不需要方案里设想的
   「chat 级 pending 槽」。卡片↔会话仍走 §7.4 的 `chat_id -> session_id` 映射。

2. **`post_api_request` 的载荷也带 `turn_id`** ⇒ 它有第二个用处：非流式模式下
   `on_stream_start` 完全不触发（它只在流式 emitter 里发），「新回合开始」就没有信号，
   上一回合的状态色会一直挂着。`post_api_request` 每回合至少一次，正好补上这个缺口
   （`panel.note_turn()`）。这是 §7.1 那条「必须有一条显式降级路径」的具体实现。

3. **`streaming.transport: edit` 与本机走不走 native 无关。** 核心的
   `StreamConsumer._resolve_native_streaming()`（`gateway/stream_consumer_transport.py`）
   只看三件事：适配器是不是 `BasePlatformAdapter`、类级 `SUPPORTS_NATIVE_STREAMING`、
   `supports_native_streaming()` 探针返回真。**完全不读 `transport` 配置** ——
   native 先试，seed 帧失败才回落 edit。所以本机（`transport: edit`）实际走的是 native，
   插件侧 `_ld_streams` 有数据，「中止重绘」那条路成立。

4. **`getattr(type(parent), ...)` 是个永远失败的探针**：`parent = super(...)` 时
   `type(parent)` 恒为 `super`，签名探测永远判「不支持 metadata」。要走 MRO 找父类实现，
   必须 `getattr(parent, name)`。已写进 `docs/lessons.md`。

### 阶段 1 的两处设计决定（超出方案文字、但由方案目标推出）

- **有结局就强制渲染面板**：面板是状态色**唯一**的载体（颜色画在
  `collapsible_panel.border.color` 上），而「简单问答」这种最常见的情形既没有推理轮
  （`stream_reasoning_deltas` 官方默认关）也没有工具 —— 不强制渲染的话效果 2/3 在
  最常见的回合里根本看不见。aiduPOP 的面板同样是常驻的。
- **`/stop` 必须自己重绘**：方案 §7.1 已经论证「永远不会有 finalize 帧」，
  实现落在覆盖 `interrupt_session_activity` 里（沿用 `_ld_streams` 最后一帧的累积全文），
  且**无论重绘成败都照常 `super()`** —— 中止是内核的职责，不能被卡片挡住。

### 阶段 1 / 3 / 4 的对抗审计与修复（2026-09-13）

| 审计对象 | 结论 | 处置 |
|---|---|---|
| 阶段 1（状态色 + 去 header） | **1 条阻断项**：`/stop` 的中止态会写错会话，且该回合还没有过程数据时**静默无色** | 已修（归属按绑定直写 + 缺桶建桶 + 重绘强制带 stopped 面板 + 回退分支只认有内容的桶） |
| 阶段 1 其余 | 中止重绘不清流状态（每次 /stop 重画全部历史卡）· `super()` 排在重绘之后（被取消时内核的中止不发生）· 父类可能被调用两次 · edit 路径静默无色 | 全部已修 |
| 阶段 3/4 | **2 条必修**：2.0 多选读错字段（`action.options` vs `option`）+ 输入框会答错**另一个**澄清 | 全部已修，并新增走**真实网关 + 真解码器**的 2.0 端到端门禁 |
| 阶段 3/4 其余 | 瞬态码表缺 `230020`（patch 接口文档明写的限频码）· 异常回落不打 native 停用告警 | 已修 |

**翻 `clarify_dialect` 默认值的前提**（两条都要满足）：
1. 真机点一次 ⑫ 方言探针卡，日志里出现 `[larkdeck] 探针点击到达 ✅`；
2. A1（多选）与 A2（输入框错配）的修复已在 `check_clarify_e2e.py` 里被真实判据覆盖（已完成）。

### 打字机到底靠哪个 API（2026-09-13 追查，**仍未定论，但天平偏向 CardKit**）

| 证据 | 指向 |
|---|---|
| 官方文档（本地 reference pack 全量 grep） | 只有一句「`streaming_mode` 流式更新模式（配 `streaming_config`）」，**没说清哪种写入 API 触发动画** |
| **Hermes 上游两个 PR** 的标题 | *「Card Kit streaming — typewriter-style output via Card Kit API」*、*「streaming cards for native typewriter effect using Feishu's CardKit streaming update API」* ⇒ 倾向「`im.v1.message.patch` 拿不到打字机」 |
| lark-hls-v2 的代码注释 | 「第一次推送必须用 `card_element.content` 才有打字机；之后改 `partial_update_element` 是为了**避免**动画重放导致的文字碎片」 |
| HFC 归档代码（三处注释） | patch + `streaming_config` 被作者称作「打字机」—— 但那段代码**不在运行路径上**，且真正的 CardKit 元素接口是死代码 |
| 我们的真机实测 | 带 `streaming_config` 的卡在 create 与 patch 两条路径上都是 `code=0`（**接受≠动画**） |

⇒ 现状：`streaming_print_ms`（默认 15）**带上但无害**，动画与否只能肉眼定。
`probe_render.py --typing` 已改成**可看的对照**：两张卡交替长大 12 秒，甲带字段、乙不带。
* 甲在逐字 ⇒ 收工（agent 侧不用改）；
* 两张都跳 ⇒ **确认无效**，把 `streaming_print_ms` 设 0，想真打字机就得换 CardKit 传输；
* 顺带一个**升级观察点**：上游正在往内置适配器里加 CardKit 流式，将来某个版本可能自带；
  但那会走 `send_stream_frame` / native streaming 这条路，而**我们覆盖了它** ——
  升级时值得看一眼内置实现有没有长出新能力（这也是本项目「不假设版本」的又一处落点）。

### 真机实测的两个数字（2026-09-13）

| 测什么 | 怎么测 | 结果 |
|---|---|---|
| 卡片字节上限 | `probe_render.py --bytes`（20KB→80KB 阶梯 + 同尺寸 PATCH） | **80KB 仍 `code=0`**（create 与 patch 都是）⇒ 40000 的预算有 2 倍余量；第三方流传的「28000 / 30KB」不成立 |
| 卡片元素数上限 | `probe_render.py --elements`（递归计数阶梯） | 198 收下、**202 拒收**（`230099 / ErrCode 11310 element exceeds the limit`）⇒ 官方 200 成立，且**必须递归数含 `tag` 的对象** |
| 推送节奏 | `probe_render.py --rate-limit`（16 次连打） | 往返 **≈0.5s/帧**、**零拒绝** ⇒ `_STREAM_MIN_INTERVAL` 不是瓶颈，打字机只能靠客户端动画 |
