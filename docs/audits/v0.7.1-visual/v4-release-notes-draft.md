# v0.7.1 发布说明草稿（视觉层重构）

> 状态：**未发布**。里程碑顺序 = 完整门禁 → 部署 `fdabddb` → 用户终验截图 + `/larkdeck status` → `push` / `tag v0.7.1`。

## 用户可见变更

1. **卡片视觉层重写为 CardKit 元素树**（`visual_engine: structured`，v0.7.1 起**默认**）：
   * 顶部**状态条**：🫧 处理中（蓝）/ ✅ 已完成（绿）/ ⛔ 已停止（黄）/ ❌ 执行出错（红），
     可用 `card_status_header: false` 关掉；
   * 面板标题是**单行摘要**：`💭 思考 Xs · 🛠️ 工具执行 · N 步`（模型名搬到页脚）；
   * **工具行**：`div(standard_icon) + lark_md`，绿色 `Succeeded` / 红色 `Failed`，细节行 `↳` 与
     `Result` / `Error` 代码块缩进 22px（对齐 CLS 观感）；
   * **推理**：`show_reasoning: true` 时按嵌套折叠面板（A 形态）展示；**默认 false** 时
     所有车道只留 `💭 思考 Xs` 摘要行（不泄漏推理正文）；
   * **Working 心跳**：长工具回合期间面板耗时持续跳秒；
   * **页脚顺序**：状态 → ⏱ 时长 → 🤖 模型 → ctx 用量 → 🔖 本卡短码（截图与日志对齐用）。
2. **`visual_engine: legacy` 配置键已退役**：设了只留一条退休 WARNING，行为仍是 structured。
   旧渲染器**没有消失** —— 它作为 `DEGRADE` 车道的降级渲染器保留（元素通道遇卡级死法时
   同卡回退，正文/状态色不丢）。
3. 结构化覆盖**全部车道**：流式卡、收尾整卡、`/stop` 重绘、切卡封旧卡、以及非流式的
   `send()` / `edit_message()` 回落（`/stop` 回复卡也走新样式）。
4. 失败回合正确显示**红头红边**；正文/页脚遇卡级死法会**同卡降级**而不是掉成纯文本。

## 配置

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `visual_engine` | `structured` | 唯一引擎；`legacy` 已退役（只告警） |
| `card_status_header` | `true` | 顶部状态条显隐 |
| `show_reasoning` | `false` | 推理正文显隐（false 时**所有车道**只留摘要行） |
| `unified_panel` / `panel_expanded` / `max_panel_steps` / `max_reasoning_chars` / `max_tool_result_chars` / `streaming_print_ms` / `text_profile` | 同前 | 结构化车道同样生效（V4.5 起） |

## 已知限制 / 未完成（记录在案，不掩盖）

* **中英文一致性**：结构化面板/状态条的标题目前是硬编码中文，尚未接 `i18n_content`
  （英文客户端会看到中文；legacy 面板本来就有双语）——列入 v0.7.2 第一项。
* 对抗审计 C（测试质量）给出的**门禁盲区**尚未全部收口：心跳「先写后到」的反向交错、
  `msg` 只在 200770 测试过、装饰失败日志限流戳未在用例里恢复、`test_units` 里
  duplicate 用例在 `pytest -k` 单跑下依赖前置 `body_source`。
  已收口的部分：processing 帧短码、`200770` 之外的 msg、心跳 skip/failed/dead 三档语义。
* `mutate_check` 的基线在**高负载**下会被既有的性能/时间派生用例（`test_code_spans_*`、
  `test_cardkit_transport_*`）闪红，可能把 flake 误记成「变异实红」——运行变异门禁前先看负载。
