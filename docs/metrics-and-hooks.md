# 指标、面板与钩子 —— 页脚和面板的数据是怎么来的

页脚要显示「模型名 + 上下文占用 + 耗时」。这三个里只有耗时是本地就能算的，
模型名和上下文占用**不在**流式适配器的入参里：

- `send()` / `edit_message()` 的 `metadata` 只带话题信息，没有模型名，也没有 token 用量。
- 核心 turn 函数内部有 `agent_result["last_prompt_tokens"]` / `["context_length"]`，
  但那是函数局部变量，**想拿就必须改核心源码**。

## 同类插件的做法（都走 patch，本项目不走）

| 插件 | 位置 | 样式 | 取数方式 |
| --- | --- | --- | --- |
| HFC | 页脚一行 | `ctx 45.6k/200k 23%` 纯文字 | patch 核心 turn 函数 |
| fry-cards | 面板 header | 图形条 `[███▓▒░░░] 23%` | 拦截器 patch 核心 |
| hermes-lark-streaming | 页脚 | 橙色 text_tag 纯文字 | patch 核心 |
| lark-hls-v2 | 页脚 | 橙色 text_tag 纯文字 | `interceptors/gateway.py` patch |
| aiduPOP | 页脚 | 纯文字 | `patching/gateway.py` patch |

它们的取数点完全一致：`result["last_prompt_tokens"]` + `result["context_length"]`。
只有 fry-cards 画了真图形条，其余四家都是纯文字百分比；位置分「页脚一行」和
「面板 header」两派。

## larkdeck 的做法：订阅 `post_api_request`

`ctx.register_hook("post_api_request", cb)` 是官方公开契约，仍然零源码改写。
回调拿到的载荷里：

```python
usage = asdict(CanonicalUsage(...))   # + prompt_tokens / total_tokens
```

键名（实测，见 `tests/check_hooks.py` 会打印）：

```
cache_read_tokens, cache_write_tokens, input_tokens, output_tokens,
prompt_tokens, reasoning_tokens, request_count, total_tokens
```

**上下文占用量要读 `prompt_tokens`，不是 `input_tokens`。**
`prompt_tokens = input_tokens + cache_read_tokens + cache_write_tokens`；
开了 prompt caching 之后 `input_tokens` 只是**未命中**的那一小段，用它算进度条
会把上下文严重低报（实测：input 800 + cache_read 44000 → 真实占用 44800）。

上限用 `agent.model_metadata.get_model_context_length(model, base_url=base_url)` 探测，按
`model@base_url` 缓存；探测不到时页脚退化成只显示已用量。也可以用
`context_max_override` 直接钉住。

⚠️ **`base_url` 必须从钩子载荷一路透传到 `record_api_call`**（`hooks._on_api_request` 里那一行）。
少了它，探测就退化成「无 base_url、无 provider」的**裸查表**：不在硬编码表里、靠 provider
元数据解析窗口的模型会落到家族兜底 —— opencode-go 的 `deepseek-flash` 真实窗口 1M，
兜底表里 `deepseek` 是 128K，于是页脚长期显示 `ctx x/128k`（用户按这个数判断「该压缩了」
必然误判）。2026-09-14 线上即此症。
`tests/check_hooks.py` **抓不到**这条：它自己把 `base_url` 塞进派发载荷，而缺陷在
「回调 → 指标层」这一跳。判据是 `tests/test_units.py::test_api_hook_passes_base_url_to_the_context_probe`
（变异清单 `HK`：撤掉透传必须红）。

## 面板数据：订阅七个钩子

推理、工具步骤与回合结局的订阅在 `hooks.py`，同样是只读观察（返回值一律 `None`）。
清单的单一事实来源是 `compat.OBSERVED_HOOKS`（门禁与文档都读它）：

| 钩子 | 面板里变成 | 备注 |
| --- | --- | --- |
| `on_stream_start` | 回合边界：到达新回合先清空旧面板 | 每次 API 尝试（含重试）触发，靠 `turn_id` 比较保证同回合幂等 |
| `on_stream_delta`（`kind="reasoning"`） | 推理文本 | **需要 `plugins.stream_reasoning_deltas: true`**，官方默认不发 reasoning 增量 |
| `on_stream_delta`（`kind="text"`，正文） | 只用来**切断推理轮**，正文不进面板 | 「一轮 = 一段连续推理，被正文或工具打断」 |
| `pre_tool_call` | 工具步骤（⏳ running） | fail-closed 钩子，回调纪律见坑 2 |
| `post_tool_call` | 工具步骤（✅ / ❌ / ⛔ + 耗时） | 载荷带 `duration_ms`、`status`、`result` |
| `post_api_request` | 页脚上下文用量 + 「回合在动」信号 | 见下面「非流式模式」一段 |
| `on_session_end` | **回合结局 → 卡片边框色** | 名字叫 session，实际**每回合**一次 |
| `pre_gateway_dispatch` | `chat_id -> session_id` 归属映射 | 只读观察，恒返回 None |

> 表里 8 行、钩子只有 7 个：`on_stream_delta` 按 `kind` 有两种用法（`reasoning` 进面板、
> `text` 只用来切轮），数钩子数时要按钩子名去重。`OBSERVED_HOOKS` 才是唯一事实来源。

数据进 `panel.py`：按 `session_id` 分桶，`turn_id` 一变就重置（新回合不残留旧面板）。
`on_stream_start` 的清理必须走在卡片首帧前 —— 流式首帧可能早于新回合第一个推理/工具事件，
纯文本回合更是没有面板事件，不清就会把上一回合的面板带到新卡片上。

**归属**：钩子载荷只有 `session_id`，而卡片渲染只有 `chat_id`。`pre_gateway_dispatch`
同时给出 `event.source.chat_id` 与 `session_store`，于是能算出确定性的
`chat_id -> session_id` 映射（只读查找，绝不 `get_or_create_session`）。
拿不到映射时（新会话第一回合 / 老版本）退回「最近活跃会话」。

### 状态色（`on_session_end`）

**它每回合触发一次**：由 `agent/turn_finalizer.py` 的 `finalize_turn` 在每次
`run_conversation()` 结尾**同步**发出，载荷 `completed` / `failed` / `interrupted` /
`turn_exit_reason` 就是官方对「完成 / 报错 / 中止」的权威判定。于是：

- 判定优先级必须是 **`interrupted > failed > completed`**。官方源码里
  `completed = final_response is not None and not failed and ...`，**不含 `interrupted`**，
  所以「被中止但已产出部分正文」的回合会 `completed=True` 且 `interrupted=True`。
  先看 `completed` 就会把中止显示成绿色完成。
- **绝不对 `error` 之类的字符串做分类** —— 载荷里没有 `reason` / `cancelled`，中止与
  报错在字符串上不可区分。
- 时序红利：它在 `run_conversation()` 内同步执行，而流式收尾帧在那之后才发，
  所以**收尾那一帧里已经能读到本回合结局**，不需要额外补一次 edit。
- 颜色载体是面板边框 `collapsible_panel.border.color`（`green` / `red` / `yellow`）。
  面板是状态色**唯一**的载体，所以「有结局」时会强制渲染出面板（哪怕没有推理与工具），
  正文里补一行 ✅ / ❌ / ⛔ 兜底文案。

**例外：`/stop` 没有收尾帧。** 它让 stream consumer 直接 return（"abandon rather than
deliver stale deltas"），native 模式下 `_abandon_native_stream` 是空操作 —— 所以插件在
`interrupt_session_activity(session_key, chat_id, metadata=None)` 里**自己把那张卡重绘**
成中止态（沿用 `_ld_streams` 里最后一帧的累积全文），然后照常 `super()` 转发给内核。

### 非流式模式下的降级路径

`on_stream_start` **只在流式 emitter 里发**（`agent/stream_delivery.py` 的
`_with_stream_emitters` 三个调用点），非流式回合（`agent/turn_api_call.py`）一个都没有。
那样「新回合开始了」就没有信号，上一回合的状态色会一直挂着。补法是
`post_api_request` —— 它每回合至少发一次、**且带 `turn_id`**，于是
`panel.note_turn()` 能在开新回合时把旧状态清掉。

> 顺带纠正一条曾经写错的事实：**钩子之间**的 `turn_id` 是同一个值
> （`on_stream_start` / `on_stream_delta` / `on_session_end` / `post_api_request`
> 读的都是 `agent._current_turn_id`，由 `agent/turn_context.py` 的 `_bind_turn_identity`
> 每回合设一次）。对不上的只有 `send_stream_frame()` 收到的那个 —— 它是
> `GatewayStreamConsumer` 自己生成的裸 uuid，与钩子侧毫无关系。

## 两个坑（都踩过）

### 1. 模块会被加载两次，全局状态不共享

插件加载器把插件装进 `hermes_plugins.<name>` 命名空间。测试里若再用
`import larkdeck.core.context`，拿到的是**第二个模块对象**：钩子写进 A 的 `_LATEST`，
断言读 B 的 `_LATEST`，永远是空的，而且**不报错**。

正确取法见 `tests/check_hooks.py`：优先 `sys.modules["hermes_plugins.larkdeck.core.context"]`。
探针脚本用合成包 `_larkdeck_probe.core` 加载，也是为了让包内相对导入指向同一份模块。

### 2. 钩子回调必须极快

`post_api_request` **在超时受限集合里**（0.21.1 实测见 `hermes_cli/plugins_dispatch.py`）：
回调跑在核心的独立 **worker 线程**上，超时会被丢弃并触发 **60 秒抑制窗口**。
所以 `context.record_api_call()` 只做一次带锁的字典赋值：不做 I/O、不做网络、不解析，
上下文上限的探测留到渲染时才做（且带缓存）。

面板订阅的 `pre_tool_call` 更严格：它是 **fail-closed** 钩子——回调卡住/超时会被
当作拦截指令，工具调用直接不执行。所以 `hooks.py` 的回调只做内存写入（微秒级返回），
并且**永不返回 directive**；`panel.py` 里的字符串拼接也推迟到卡片渲染时。

## 验证

```bash
$PY tests/check_hooks.py    # 真实派发器 + 真实 CanonicalUsage 载荷 + 面板数据层 + 对照组
```

它会验证：`compat.OBSERVED_HOOKS` 里的 7 个钩子全部登记成功（并核对门禁清单与插件清单一致）、
真实 `invoke_hook` / 流式钩子队列能送达、指标语义正确、面板数据落桶正确、
正文增量切轮正确、三种回合结局（含 `completed=True` 同时 `interrupted=True` 的优先级坑）
落到正确的状态、新回合清空、坏载荷不冲掉好数据，以及**未启用插件时钩子为空**
（对照组，证明观测点可信）。
