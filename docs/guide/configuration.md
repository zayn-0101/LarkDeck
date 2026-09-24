# 配置参考

本文面向需要调整 LarkDeck 行为的用户：以 `plugin.yaml` 的 `config_schema` 为唯一事实来源，列出全部可配置项、默认值、作用与常用示例，并说明配置优先级和 `config reload` 的边界。

## 配置写在哪

所有键都写在 `~/.hermes/config.yaml` 的 `plugins.entries.larkdeck.settings` 下。最小可用配置：

```yaml
plugins:
  entries:
    larkdeck:
      settings:
        cards: true
```

插件是否加载由 `plugins.enabled` 里的 `larkdeck` 决定，不属于下面 28 个配置键。键名、类型与默认值以 [`plugin.yaml`](../../plugin.yaml) 的 `config_schema` 为准，版本号也只从那里读。

### 优先级

1. 环境变量 `LARKDECK_<KEY>`，键名大写，例如 `LARKDECK_CARDS=0`。
2. 官方插件设置 `plugins.entries.larkdeck.settings.<key>`。
3. 插件内置默认值，与 `plugin.yaml` 的 `default` 一致。

环境变量优先级最高：它一旦存在，`config reload` 也不会改变该键的本进程生效值，只会在结果里提示被环境变量覆盖。

## 常用场景

### 只要一张安静的卡片

```yaml
plugins:
  entries:
    larkdeck:
      settings:
        unified_panel: false        # 不显示执行详情
        reactions: false            # 不在用户消息上打“处理中”表情
        footer: false               # 不显示页脚
        footer_metrics: off
        card_status_header: false
```

### 看到第 N 轮思考（round）

```yaml
plugins:
  stream_reasoning_deltas: true     # Hermes 侧开关，默认关闭
  entries:
    larkdeck:
      settings:
        show_reasoning: auto        # 跟随 Hermes 的 /reasoning on|off
        unified_panel: true
        streaming_panel_expanded: true
```

### 回到旧的整卡 patch 传输

```yaml
plugins:
  entries:
    larkdeck:
      settings:
        native_transport: patch     # 字几个几个跳；默认 cardkit 是逐字打字机
```

### 控制长回合的面板体积

```yaml
plugins:
  entries:
    larkdeck:
      settings:
        max_reasoning_chars: 600
        max_tool_result_chars: 300
        max_panel_steps: 15
        unified_panel: false        # 更极端：整块面板都不要
```

### 页脚显示更多指标

```yaml
plugins:
  entries:
    larkdeck:
      settings:
        footer_metrics: full        # off（默认）/ basic / full
        context_style: both         # text（默认）/ bar / both
```

## 完整参考

### 卡片与传输

- **cards** · `boolean` · 默认 `true`
  总开关。`false` 完全退回官方纯文本行为。示例：`cards: false`。
- **native_streaming** · `boolean` · 默认 `true`
  官方 native 流式：一回合一张卡，工具进度合入同卡。关掉后退回核心的逐段发送 / 编辑路径。示例：`native_streaming: false`。
- **clarify_cards** · `boolean` · 默认 `true`
  澄清提问用交互卡；无线索或卡片失败时自动改用内置文字提问。示例：`clarify_cards: false`。
- **native_transport** · `string` · 默认 `"cardkit"`
  流式帧传输。`cardkit` 用 CardKit 实体加逐元素写入，带来真逐字打字机；`patch` 是旧路径，整卡替换、字跳得更粗。任何一步失败都会自动切换。示例：`native_transport: "patch"`。
- **clarify_dialect** · `string` · 默认 `"2.0"`
  澄清卡（clarify）形态，配置键为 `clarify_dialect`。`2.0` 是下拉 / 多选 / 输入框；`1.0` 是按钮旧路径。待答卡与确认卡必须同形态。示例：`clarify_dialect: "1.0"`。

### 过程面板与推理

- **tool_row_icon** · `string` · 默认 `"line"`
  工具行图标形态。`line` 用飞书官方线性图标；`emoji` 是旧版 emoji 内联降级。示例：`tool_row_icon: "emoji"`。
- **unified_panel** · `boolean` · 默认 `true`
  把推理与工具合并成底部一个可折叠的执行详情（panel）。关掉后过程信息不再上卡。示例：`unified_panel: false`。
- **panel_expanded** · `boolean` · 默认 `false`
  **回合结束**时面板是否展开。展开态占屏幕，默认收起。示例：`panel_expanded: true`。
- **streaming_panel_expanded** · `boolean` · 默认 `true`
  **运行中**面板是否展开。只有新建卡那一刻生效；流式中间帧不会重放展开态，所以你手动收起后不会被下一个 token 顶开。示例：`streaming_panel_expanded: false`。
- **streaming_print_ms** · `integer` · 默认 `15`
  客户端打字机的逐字间隔（毫秒），只对流式帧有效。`0` 关闭打字机；超出 `[1, 2000]` 退回默认并留日志 WARNING。示例：`streaming_print_ms: 30`。
- **reactions** · `boolean` · 默认 `true`
  在用户消息上打“处理中”表情，相当于飞书的输入提示。关掉更接近无表情的即时响应观感。示例：`reactions: false`。
- **progress_lines_in_body** · `boolean` · 默认 `false`
  核心的工具进度行要不要写进正文。只在 `body_source: legacy` 下可观察；默认 `own` 模式下工具行结构上不会进入正文。示例：`progress_lines_in_body: true`。
- **body_source** · `string` · 默认 `"own"`
  正文来源。`own` 只认插件从模型文本累积的正文；`legacy` 是旧的剥帧路径，仅作过渡回退。示例：`body_source: "legacy"`。
- **visual_engine** · `string` · 默认 `"structured"`
  视觉引擎。`structured` 是当前唯一在跑的引擎；把 `legacy` 写进来只会留一条退休提示，行为仍是 `structured`。示例：`visual_engine: "structured"`。
- **card_status_header** · `boolean` · 默认 `false`
  卡片顶部状态条：处理中蓝 / 完成绿 / 停止黄 / 出错红。默认不显示。示例：`card_status_header: true`。
- **show_reasoning** · `string` · 默认 `auto`
  推理正文是否显示。`auto` 跟随 Hermes 的 `display.show_reasoning` 与平台覆盖；`on` / `off` 由插件强制。旧布尔 `true` / `false` 仍接受。Hermes 未发 reasoning delta 时按关闭处理，`/larkdeck status --detail` 会说明原因。示例：`show_reasoning: "on"`。

### 页脚与外观

- **footer** · `boolean` · 默认 `true`
  页脚：状态 → 耗时 → 模型 → 上下文用量。关掉后整条页脚不渲染。示例：`footer: false`。
- **show_model** · `boolean` · 默认 `true`
  页脚里显示模型名。关掉后模型名不出现，面板标题本来也只放思考 / 工具摘要。示例：`show_model: false`。
- **context_style** · `string` · 默认 `"text"`
  上下文用量样式：`text` 纯文字 / `bar` 图形条 / `both` 数字加条。示例：`context_style: "bar"`。
- **footer_metrics** · `string` · 默认 `"off"`
  页脚在上下文用量之外附加的指标：`off` / `basic`（缓存命中率加本回合 API 次数）/ `full`（再加首字节延迟）。缺数据的段直接不显示，不编造 0。示例：`footer_metrics: "basic"`。
- **text_profile** · `string` · 默认 `"compact"`
  设备字号档位：`compact` 面板 / 页脚用 12px notation、正文 normal；其余可选 `off`、`mobile_friendly`、`large`。工具细节行与错误块的字号是字面量，不随本档位放大。示例：`text_profile: "large"`。
- **theme** · `string` · 默认 `"ap_lite"`
  观感主题：`neutral` 原符号 / `ap_lite` 抽象 emoji / `ap_bubble` 泡波风格（含人物 emoji）。示例：`theme: "ap_bubble"`。
- **panel_color_tags** · `boolean` · 默认 `true`
  面板 markdown 是否使用 `<font color>` 上色。客户端不认时可显式设 `false` 走纯文本降级。示例：`panel_color_tags: false`。

### 限额与高级

- **model_aliases** · `string` · 默认 `""`
  模型别名。可写 `"真名=显示名, 真名2=显示名2"`，也会读 `~/.hermes/model_aliases.json` 的 `{"子串": "显示名"}`；值也可以是 `{model@base_url: 显示名}` 映射。示例：`model_aliases: "deepseek-flash=Flash"`。
- **max_reasoning_chars** · `integer` · 默认 `1200`
  执行详情里推理文本的上限，超出截断并留痕。写 `0` 或负数表示用默认值，不是不设限。示例：`max_reasoning_chars: 600`。
- **max_tool_result_chars** · `integer` · 默认 `600`
  单条工具步骤行的上限。面板显示的是已截到 80 字符的参数预览，所以这项现实中很少触发。写 `0` 或负数表示用默认值。示例：`max_tool_result_chars: 300`。
- **max_panel_steps** · `integer` · 默认 `30`
  面板最多保留多少步，超出保留最近的步骤。写 `0` 或负数表示用默认值。示例：`max_panel_steps: 15`。
- **context_max_override** · `integer` · 默认 `0`
  非 0 时钉住上下文窗口上限，用于自动探测不准的兜底；`0` 表示按探测值。示例：`context_max_override: 200000`。

## 完整样例

照抄后按需删除：下面是一份含全部 28 个键、且每个键都取默认值的样例；只保留你要改的行即可。

```yaml
plugins:
  entries:
    larkdeck:
      settings:
        cards: true
        native_streaming: true
        clarify_cards: true
        native_transport: "cardkit"
        clarify_dialect: "2.0"
        tool_row_icon: "line"
        unified_panel: true
        panel_expanded: false
        streaming_panel_expanded: true
        streaming_print_ms: 15
        reactions: true
        footer_metrics: "off"
        progress_lines_in_body: false
        body_source: "own"
        visual_engine: "structured"
        card_status_header: false
        show_reasoning: auto
        footer: true
        show_model: true
        context_style: "text"
        text_profile: "compact"
        theme: "ap_lite"
        panel_color_tags: true
        model_aliases: ""
        max_reasoning_chars: 1200
        max_tool_result_chars: 600
        max_panel_steps: 30
        context_max_override: 0
```

## 查看与刷新配置

- `/larkdeck config`：只读列出每个键在本进程的生效值与来源（环境变量 / 官方插件设置 / 默认值）。官方文件改了而进程仍是旧值时，会提示执行 `config reload`。
- `/larkdeck config reload`：从官方 `ctx.get_config()` 重读全部插件键并替换内存配置。

`config reload` 的边界：

1. **任一键读取失败就整次取消**，内存保持原样，不会留下半套新配置。
2. **不是文件系统事务**：官方读取每个键都独立读盘，外部并发写配置时可能读到混合快照；需要强一致时先停外部写入。
3. **环境变量仍然优先**：被 `LARKDECK_<KEY>` 覆盖的键，reload 后生效值不变，结果里会列出它们。
4. **只管插件键**：不会重载插件代码，也不会把 `plugins.enabled`、`plugins.stream_reasoning_deltas`、`display.show_reasoning` 等 Hermes 侧配置读进来；启用项与代码升级仍要重启网关。
5. **聊天侧没有写入命令**：插件不写 `config.yaml`。请用官方 Hermes CLI / 配置文件修改 `plugins.entries.larkdeck.settings`，再执行 `/larkdeck config reload`。

配置优先级、命令与执行时机见 [命令](commands.md)；配置不生效时的排查见 [故障排查](troubleshooting.md)。
