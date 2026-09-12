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

上限用 `agent.model_metadata.get_model_context_length(model)` 探测，按
`model@base_url` 缓存；探测不到时页脚退化成只显示已用量。也可以用
`context_max_override` 直接钉住。

## 面板数据：订阅四个钩子

推理与工具步骤的订阅在 `hooks.py`，同样是只读观察（返回值一律 `None`）：

| 钩子 | 面板里变成 | 备注 |
| --- | --- | --- |
| `on_stream_start` | 回合边界：到达新回合先清空旧面板 | 每次 API 尝试（含重试）触发，靠 `turn_id` 比较保证同回合幂等 |
| `on_stream_delta`（`kind="reasoning"`） | 推理文本 | **需要 `plugins.stream_reasoning_deltas: true`**，官方默认不发 reasoning 增量 |
| `pre_tool_call` | 工具步骤（⏳ running） | fail-closed 钩子，回调纪律见坑 2 |
| `post_tool_call` | 工具步骤（✅ / ❌ / ⛔ + 耗时） | 载荷带 `duration_ms`、`status`、`result` |

数据进 `panel.py`：按 `session_id` 分桶，`turn_id` 一变就重置（新回合不残留旧面板）。
`on_stream_start` 的清理必须走在卡片首帧前 —— 流式首帧可能早于新回合第一个推理/工具事件，
纯文本回合更是没有面板事件，不清就会把上一回合的面板带到新卡片上。
**限制**：这些载荷只有 `session_id`、没有 chat_id，卡片渲染时只能取「最近活跃会话」
——多会话并发时可能短暂串台（README 已知限制里也有记录）。

## 两个坑（都踩过）

### 1. 模块会被加载两次，全局状态不共享

插件加载器把插件装进 `hermes_plugins.<name>` 命名空间。测试里若再用
`import larkdeck.core.context`，拿到的是**第二个模块对象**：钩子写进 A 的 `_LATEST`，
断言读 B 的 `_LATEST`，永远是空的，而且**不报错**。

正确取法见 `tests/check_hooks.py`：优先 `sys.modules["hermes_plugins.larkdeck.core.context"]`。
探针脚本用合成包 `_larkdeck_probe.core` 加载，也是为了让包内相对导入指向同一份模块。

### 2. 钩子回调必须极快

`post_api_request` 不在超时隔离名单里，回调在**调用方线程**同步执行，异常虽被吞掉
但会拖慢请求。所以 `context.record_api_call()` 只做一次带锁的字典赋值：
不做 I/O、不做网络、不解析、上下文上限的探测留到渲染时才做（且带缓存）。

面板订阅的 `pre_tool_call` 更严格：它是 **fail-closed** 钩子——回调卡住/超时会被
当作拦截指令，工具调用直接不执行。所以 `hooks.py` 的回调只做内存写入（微秒级返回），
并且**永不返回 directive**；`panel.py` 里的字符串拼接也推迟到卡片渲染时。

## 验证

```bash
$PY tests/check_hooks.py    # 真实派发器 + 真实 CanonicalUsage 载荷 + 面板数据层 + 对照组
```

它会验证：4 个钩子登记成功、真实 `invoke_hook` / 流式钩子队列能送达、指标语义正确、
面板数据落桶正确、坏载荷不冲掉好数据、以及**未启用插件时钩子为空**（对照组，证明观测点可信）。
