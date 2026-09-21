# v0.7.1 发布说明草稿（视觉层重构）

> ⚠️ **历史文档（v0.7.1 时期）**：其中「页脚带 🔖 短码」的口径已被 **v0.7.2 推翻**
> （用户 2026-09-21：「我从来没有提过这个要求」）。现在的页脚是 `状态 · ⏱ 时长 · 模型 · ctx`，
> 短码只进日志自检行。**不要**按本文的旧口径改动代码。


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

## V4.8 待办（用户 2026-09-21 13:0x 真机三点反馈，发布前必修）

1. **占位符**：`⏳ 正在生成…`（沙漏 emoji）在正文首字到达前停留太久，用户明确觉得丑；
   对标插件不这么做。⇒ structured 卡片**不再渲染占位符**（正文元素留空，等首个 delta 直接长出来）；
   并检查 own 累积的刷新节流，别让第一批字「一次性涌出一大段」。
2. **工具行图标**：与对标插件不是同一套；且图标比文字**偏上**。
   CLS 源码（`hermes-lark-streaming/cardkit/builder.py:157-174`）用的是**统一的 `tool_02` 家族 token**
   （`step.get("icon", "tool_02")`），我们的 per-tool token（`setting_outlined`/`search_outlined`…）
   字形高度不一 ⇒ 既不像也不同高。⇒ 改用 CLS 的 token 映射；若观感仍偏，就试「把 emoji 内联进
   文本、不用 div.icon」两版，做 A/B 探针卡让用户挑。
3. **Result 块**：现在**每一步**都挂一个大号 fenced 代码块，用户觉得「丑陋、和别的插件不一样」。
   ⇒ 对齐 CLS：默认只留一行灰色细节；`Result`/`Error` 块只在**失败**（或展开时）出现，且字号收敛。
   ⚠️ 这会覆盖 plan 里「每步都渲染 Result/Error 块」那条，属于**用户口径优先**，要在 consensus 文档里记一笔。

## v0.7.2 待办（用户 2026-09-21 选择「先发 v0.7.1」，以下三项留给下一版）

1. **`/stop` 字节判据没有建模结构化重绘卡（真 bug，不只影响 i18n）**：
   `adapter._stop_redraw_would_paint()` 在 `visual_engine=structured` 下仍按 legacy
   `status_shell + unified_panel` 估壳体积，而 `/stop` 真正重绘的是**元素树卡**、
   面板来自该会话真实快照（可能 7+ 个工具步）⇒ 判据偏乐观，边界上会「判据说画得上色、
   实际丢正文」（测试 `test_tracked_body_always_fits_the_status_shell_end_to_end` 的
   `A1-a` 断言能抓住）。**修法**：把真实面板/视图从调用点（`_ld_note_text` / `/stop` 重绘）
   传进判据，而不是让它自己猜。
2. **i18n**：结构化标题（卡级状态头 / 面板摘要 / 推理轮 / 折叠提示）目前是中文硬编码。
   改法：接 `_i18n.i18n_text()`（双语节点）—— 已确认参考实现 aiduPOP 也是这个形态；
   依赖第 1 项先修（双语节点会撑大壳体积，判据必须准）。
3. **aiduPOP 式 loading hint**：`div` + `standard_icon: time_outlined`(16px 灰) + 双语文案，
   建卡时插入、**首个 token 到达即删除**（`aiduPOP/cardkit/elements.py:180`、
   `cards.py:185-187`）。需要给结构化帧路径加 `delete_elements` 动作。
