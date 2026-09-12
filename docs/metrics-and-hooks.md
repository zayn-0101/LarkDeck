# 指标与钩子 —— 页脚那两个数字是怎么来的

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

## 两个坑（都踩过）

### 1. 模块会被加载两次，全局状态不共享

插件加载器把插件装进 `hermes_plugins.<name>` 命名空间。测试里若再用
`import larkdeck.context`，拿到的是**第二个模块对象**：钩子写进 A 的 `_LATEST`，
断言读 B 的 `_LATEST`，永远是空的，而且**不报错**。

正确取法见 `tests/check_hooks.py`：优先 `sys.modules["hermes_plugins.larkdeck.context"]`。
探针脚本用合成包 `_larkdeck_probe` 加载，也是为了让包内相对导入指向同一份模块。

### 2. 钩子回调必须极快

`post_api_request` 不在超时隔离名单里，回调在**调用方线程**同步执行，异常虽被吞掉
但会拖慢请求。所以 `context.record_api_call()` 只做一次带锁的字典赋值：
不做 I/O、不做网络、不解析、上下文上限的探测留到渲染时才做（且带缓存）。

## 验证

```bash
PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3   # 系统 python3 太老，会 ImportError
$PY tests/check_hooks.py    # 真实派发器 + 真实 CanonicalUsage 载荷 + 对照组
```

它会验证：钩子登记成功、真实 `invoke_hook` 能送达、`prompt_tokens` 语义正确、
坏载荷不冲掉好数据、以及**未启用插件时钩子为空**（对照组，证明观测点可信）。
