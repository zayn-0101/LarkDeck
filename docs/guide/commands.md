# 常用命令

给日常使用 LarkDeck 的用户：说明与卡片相关的命令、大致输出，以及哪些命令只在网关空闲时才能执行。

## 命令一览

| 命令 | 归属 | 作用 |
|---|---|---|
| `/larkdeck` | LarkDeck | 等同 `/larkdeck status` |
| `/larkdeck status` | LarkDeck | 自检卡：版本、生效传输、钩子、聚合诊断与六条进程级记录 |
| `/larkdeck config` | LarkDeck | 只读查看本进程生效配置与来源 |
| `/larkdeck config reload` | LarkDeck | 从 Hermes 官方设置重读全部插件配置键 |
| `/larkdeck help` | LarkDeck | 用法与执行时机提醒 |
| `/reasoning on\|off` | Hermes | 开关推理正文显示；插件默认 `show_reasoning: auto` 会跟随 |
| `/stop` | Hermes | 中止当前回合；有卡片时把最新一张重绘成中止色 |

## `/larkdeck status`

默认命令。它本身会走卡片发送，所以回复就是一张卡，首行类似：

```text
🃏 larkdeck v<当前版本> · 传输 cardkit · 钩子 8/8 已挂
```

后面依次是：

- 聚合诊断：能力 / 链路 / 运行 / 账本各一行，有明确异常才带 `⚠️`。
- 能力探测：适配器接管、Hermes 版本、点击回调与命令注册是否就绪。
- 六条记录：入站心跳、最近写卡、最近写卡失败、已运行、掉回纯文本、错误码。

这些数字是**进程级累计**，多会话并发时包含其他会话，不是本对话统计。没有记录时卡片写“无记录”，不会写“正常”。

## `/larkdeck config`

只读视图，列出每个键在本进程的生效值，并标出来源：环境变量、官方插件设置或默认值。官方文件改了而进程仍是旧值时会显示提示：

```text
⚙️ 生效配置（来源优先级：环境变量 > 官方插件设置 > 默认值）：
· cards = `true`（默认值）
```

`/larkdeck config reload` 从官方 `ctx.get_config()` 重读全部插件键。它不是文件事务：任一键读取失败就整次取消；被环境变量覆盖的键不会改变生效值，结果里会提示。它也不会重载代码或 Hermes 侧配置。

聊天侧没有配置写入命令。请用官方 Hermes CLI / 配置文件改 `plugins.entries.larkdeck.settings`，再执行 `/larkdeck config reload`。完整键表见 [配置参考](configuration.md)。

## `/larkdeck help`

打印命令用法，并再次提醒下面“执行时机”的规则。

## `/reasoning on|off`

Hermes 官方命令，管理推理强度与显示。LarkDeck 的 `show_reasoning` 默认是 `auto`：跟随 Hermes 的 `display.show_reasoning` 与平台覆盖，所以空闲时在飞书发 `/reasoning on|off` 就能生效，下一次交互（约 1 秒内）跟着变。

只把显示打开还不够：Hermes 默认不发送推理增量，需要在 `~/.hermes/config.yaml` 开启：

```yaml
plugins:
  stream_reasoning_deltas: true
```

关着时执行详情（panel）只显示工具步骤与思考耗时摘要，不会显示推理正文；`/larkdeck status` 会说明“Hermes 未发送 reasoning delta”。想强制显示或隐藏，再设 `show_reasoning: on|off`。

## `/stop`

中止当前回合。LarkDeck 会监听中止事件，把**最新一张**卡片重绘成黄边中止态，然后照常把中止交给内核。

- 本回合还没有卡片时，没有卡可以变色，你只会看到核心的中止回复。
- 超长回答分成多张卡时，只有最新那张会变色；已经封掉的旧卡保持原样。
- `/stop` 专门支持在回合中执行，不需要等空闲。

## 执行时机

- **飞书网关里，`/larkdeck *` 命令只在核心空闲路径上派发**。生成回答期间发送会被当成普通输入排队到回合结束，不是被忽略，但也不会立刻执行。
- **CLI / TUI 里可以直接执行**插件命令，没有忙碌概念。
- `/reasoning on|off` 在网关忙碌时会拒绝执行并提示等待或先 `/stop`；它影响的是下一回合，建议空闲时发。
- `/stop` 是忙碌状态下的例外，随时可用。

卡片能力与失败后的表现见 [卡片能力与限制](card-capabilities.md)。
