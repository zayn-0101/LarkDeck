# LarkDeck v0.7.1 外观对齐规划（CLS / FC / aiduPOP）

> 目标：在不改 Hermes 源码、不换 Monkey Patch 路线的前提下，把卡片外观从「功能对但观感平」
> 拉到参考插件的水平。先静态排版、再实时状态、最后结构增强；每一阶段只做一次真机截图验收。

## 0. 参考对象与目标

已核对的参考截图与源码：
- CLS `Cheerwhy/hermes-lark-streaming`：`assets/streaming.jpg`、`reasoning.jpg`、`cover.jpg`；
  `cardkit/builder.py` 的 `_collapsible_panel`、`_build_reasoning_panel`、`_build_tool_panel`。
- FC `techysy/hermes-fry-cards`：`assets/processing.png`、`expand.png`、`completed.png`、`abort.png`；
  `cardkit/builder.py` 的卡片级 header、统一面板、嵌套 reasoning 面板。
- aiduPOP：`assets/screenshots/04-panel-expanded.png`、`02-panel-completed.png`；
  `aowen/__init__.py` 的 `lark_md` / `vertical_spacing=4px` / `notation` 用法。

目标视觉语言（推荐 hybrid，不照抄机制）：
1. **CLS 的工具行**：图标 + 加粗动作名 + 耗时 + 彩色状态词 + 灰色细节行；
2. **FC 的折叠面板层级**：统一面板内按轮/按工具分区，密而不挤；
3. **aiduPOP 的密度**：`notation` 小字、4px 间距、每轮一个小标题；
4. **保留用户既定约束**：面板标题不含模型名；页脚顺序
   `状态 → 耗时 → 模型 → ctx → 短码`；正文只放回答、无工具进度。

## 0.1 已拍板决策（2026-09-20）

- **卡片顶部状态条：加**，但必须可配置显隐（`card_status_header: true|false`，默认 `true`）。
  只显示状态词（处理中 / 已完成 / 已停止 / 执行出错），不重复模型/耗时/ctx。
- **推理展示形态：A（FC 式）**：每轮一个嵌套折叠面板，标题「💭 思考 · Xs」，
  面板内是原始推理文本；依赖探针 P3，失败则退 B（分区标题行）。
- **中止色：黄**（保持既定验收）；红色只用于 error。
- **推理默认显隐（新增）**：参考插件默认值已核实：
  - CLS `config.py:57-70`：`show_reasoning` 默认 **false**（默认不显示推理文本）；
  - aiduPOP `config/reader.py:232-242`：`show_reasoning` 默认 **false**；
  - FC `config.py:80-93`：`show_reasoning` 默认 **true**（默认显示，嵌套面板）。
  ⇒ 我们新增 `show_reasoning: false|true`，**默认 false**：不显示推理正文/嵌套面板；
  面板 header **同一行**保留思考耗时摘要，与工具计数拼接，不新增元素、不新增行：
  `💭 思考 Xs · 🛠️ 工具执行 · N 步`；只有思考/只有工具时对应省略另一段；
  都没有时回退「执行详情」。设为 true 时按形态 A 展示每轮推理。
  ⚠️ 若你希望连 `💭 思考 Xs` 也隐藏，再加 `show_reasoning_summary` 开关。

  ⚠️ **源码复核（2026-09-20）**：三家在 `show_reasoning=false` 时都是**直接不记录推理**
  （CLS `controller.py:214`、FC `controller.py:284`、AP `controller.py:527` 都在
  `on_reasoning` 入口 `return`），所以它们的卡片里**连「思考 Xs」这一行也没有**；
  `true` 时才出现 CLS/FC 的 `💭 思考了 Xs` 面板或 AP 的 `第 N 波 · Xs` 轮次标题。
  我们提议的「false 仍留一行摘要」是 hybrid，不是三家默认行为；待用户最终选择。



## 0.2 源码级外观映射（不看截图，直接对源码）

### CLS `hermes_lark_streaming/cardkit/builder.py`
- `:26-60` `_collapsible_panel`：`vertical_spacing=4px`、`padding=8px`、`border.color=grey`、
  `corner_radius=5px`、右侧 `standard_icon: down-small-ccm_outlined 16px`、`icon_expanded_angle=-180`。
- `:61-72` `_streaming_element`：`markdown`、`text_align=left`、`text_size=normal_v2`、
  `margin=0px`（正文不留默认段距）。
- `:82-105` `_build_header`：卡片级 header 模板 `streaming=blue / completed=green /
  error=red / stopped=red`；标题是 `plain_text + i18n_content`。
- `:155-185` 工具标题：`div` + `standard_icon(token=tool_02, grey)` +
  `lark_md(text_size=notation)`，内容是 `**动作名** · <font color>状态词</font>`。
- `:187-204` 工具细节：`div`，`margin="0px 0px 0px 22px"`，`plain_text`、灰色、`notation`。
- `:206-238` 工具输出：`div` 左缩进 22px，`lark_md` 包 `**Error/Result**` + fenced code block。
- `:248-280` 推理面板：**每段推理一个 collapsible_panel**，标题 `plain_text` 灰色 `notation`
  （`💭 Thought for Xs` / `思考了 Xs`），内容一个 `markdown`（`notation`），
  `vertical_spacing=8px`。
- `:395-455` 流式卡结构：可选推理面板 → 可选工具面板 → streaming 元素 → loading 元素；
  header 可选。`:458-545` 完成卡：按 segment 顺序渲染推理/工具/正文，footer 前加 `hr`。

### FC `hermes_fry_cards/cardkit/builder.py`
- `:61-90` `_collapsible_panel` 与 CLS 同 token（4px/8px/5px/标准 down 图标）。
- `:117-140` `_build_header` 与 CLS 同模板；卡片级 header 可选。
- `:591-770` 完成卡：正文在上，**一个统一 collapsible_panel** 在下；面板内
  **每轮推理一个嵌套 `_build_reasoning_panel`**（`:283-315`，标题 `💭 思考了 Xs`，
  内容 markdown `notation`，`vertical_spacing=8px`），工具步骤用 `_build_tool_panel` 的
  `elements` 平铺；外层面板 header 是 `plain_text` 灰色 `notation` 统计行
  （模型 · 轮数 · 工具数 · ctx · 耗时），border 颜色 `green/yellow/red`。
- `:520-590` 流式卡：可选推理面板 → streaming 元素 → 工具面板（底部）→ loading 元素；
  header 可选。

### aiduPOP `cardkit/elements.py` + `aowen/__init__.py`
- `_fold`（`aowen/__init__.py:229-256`）：`vertical_spacing=4px`、`padding=8px`、
  `corner_radius=5px`、标题 `plain_text` 灰色 `notation`、标准 down 图标。
- `_icon_div`（`:155-168`）：`div` + `standard_icon` + `lark_md`，`notation` 小字。
- `_build_reasoning_round_title`（`cardkit/elements.py:543-575`）：每轮一个 `div`，
  状态色 `green`（已完成）/`orange-300`（进行中）/`red`（失败），`lark_md` `notation`。
- `build_panel_header`（`:240-287`）：统一面板 header 统计行 `plain_text` 灰色 `notation`。
- `build_panel_children`（`:309-430`）：推理轮标题 + 左缩进 22px 的 `div` 正文；
  工具步骤平铺；带折叠提示与体积预算。

**结论**：三家视觉一致的核心 token 是
`vertical_spacing=4px`、`padding=8px`、`corner_radius=5px`、`notation` 小字、
`margin=0`、`div + standard_icon + lark_md` 的工具/轮次行、卡片级彩色 header。
我们当前缺的正是这些；而事件来源三家都靠 patch/monkeypatch，与外观无关。

## 0.3 官方钩子能力核对（决定哪些必须 patch）

- **工具结果/错误块**：官方 `post_tool_call` 载荷包含 `result` / `error_type` /
  `error_message` / `status` / `duration_ms`（`agent/tool_executor.py:264-279`、
  `agent/inline_tool_executors.py:29-56`）。⇒ CLS/FC 的 `**Result**` / `**Error**`
  fenced block 我们可以直接用官方钩子实现，**不需要 patch 工具执行**。
- **推理增量**：官方 `on_stream_delta(kind="reasoning")`（需 `plugins.stream_reasoning_deltas:
  true`，本机已开）。⇒ A 形态嵌套推理面板不需要 patch reasoning_callback。
- **回合结局**：官方 `on_session_end` 给 completed/failed/interrupted。⇒ 状态条/边框色
  不需要 patch finalize。
- **用量/模型**：官方 `post_api_request` 给 model/provider/usage/base_url。⇒ 页脚不需要 patch。
- **正文增量**：官方 `on_stream_delta(kind="text")`。⇒ 正文来源已切 own，不需要 patch
  stream consumer。

⇒ 外观与功能缺口全部落在「CardKit 官方 schema/接口能力」和「渲染层实现」上；
Monkey Patch 只在需要访问**官方钩子没有暴露的内部状态**时才有意义，目前没有这种缺口。

## 1. 现状 vs 参考：差距清单

| 维度 | 参考 | 我们当前 | 差距等级 |
|---|---|---|---|
| 卡片级状态头 | FC/CLS：流式蓝 / 完成绿 / 停止红 的 header 条 | 无 header，仅面板边框 + 页脚状态词 | 高（观感差异最大） |
| 面板 header | FC：模型 · 轮数 · 工具数 · ctx · 耗时；CLS：工具数+耗时 | 流式固定「执行详情」，收尾才换摘要 | 高 |
| 推理层级 | CLS/FC：每轮一个独立折叠面板/标题行，含「思考了 Xs」 | 一个 markdown 里 `**第 N 轮**` + 正文 | 中高 |
| 面板间距 | `vertical_spacing=4px`、`padding=8px`、圆角 5px、markdown `margin:0` | 圆角 8px、无 vertical_spacing、markdown 默认边距 | 中 |
| 工具行 | CLS：`Terminal (297 ms) · Succeeded` + 灰色命令；FC 同 | 已接近；图标/字重/细节行还需对齐 | 低 |
| 页脚 | CLS/FC 顺序不同 | 已是用户指定顺序 | 保持 |
| 流式状态 | FC：顶部「处理中...」+ 面板实时统计；AP：实时耗时 | 正文占位 + 固定「执行详情」 | 高 |
| 正文排版 | 三家都偏紧凑、`normal` 字号 | 正文已正常，但边距/段距未统一 | 低中 |
| 卡片圆角/边框 | 5px 细边、状态色 | 8px、状态色已有 | 低 |

## 2. 设计 token（先冻结，再改实现）

在 `cards.py` 集中定义一套「视觉 token」，不再散落硬编码：

```
PANEL_RADIUS = "5px"            # CLS builder.py:47 / FC :82 / AP :251
PANEL_PADDING = "8px 8px 8px 8px"  # 同上
PANEL_SPACING = "4px"           # 外层 collapsible_panel
REASONING_SPACING = "8px"       # 嵌套推理面板（CLS :274 / FC :309）
MD_MARGIN = "0px 0px 0px 0px"   # CLS/FC `_streaming_element`
TOOL_DETAIL_INDENT = "0px 0px 0px 22px"  # CLS :191 / AP :381
PANEL_TEXT_SIZE = "notation"    # 三家统一
BODY_TEXT_SIZE = "normal"       # 我方现有 token；CLS/FC 用 normal_v2，V0 先做客户端探针
```

状态头（配置键 `card_status_header`，默认 `true`）：
- `processing`：template `blue`，标题「🫧 处理中…」
- `completed`：template `green`，标题「✅ 已完成」
- `stopped`：template `yellow`（已拍板），标题「⛔ 已停止」
- `error`：template `red`，标题「❌ 执行出错」

面板 header 文案（保留既定约束）：
- 流式：`💭 思考 {elapsed}s · 🛠️ 工具执行 · {n} 步`
- 收尾：同上，耗时定格
- 模型名/ctx/短码仍只在页脚，不放进面板标题。

工具/推理行的元素形态（源码级）：
- 工具标题：`div` + `standard_icon` + `lark_md`，`**动作名** · <font color>状态词</font>`；
- 工具细节：`div`，左缩进 22px，灰色 `plain_text` `notation`；
- 推理轮：A 形态 = 嵌套 `collapsible_panel`，标题 `plain_text` 灰色 `notation`，
  内容一个 `markdown` `notation`，`vertical_spacing=8px`。

## 3. 技术路线与探针（全部走官方 CardKit API）

不换 Monkey Patch。需要先做 4 个只读/真机探针，决定实时能力的上限：

| 探针 | 问题 | 影响 | 备选方案 |
|---|---|---|---|
| P1 | `card.batch_update` 能否在流式期间 partial 更新 `collapsible_panel.header.title` / `border`？ | 面板标题/状态色实时更新 | 不能则收尾/分段时更新（CLS 现状） |
| P2 | 卡片级 `header` 能否在流式期间更新 template/title？ | 顶部状态条实时变色 | 不能则只在建卡时定蓝、收尾 patch 换绿/红 |
| P3 | `card_element.create` 能否在流式期间安全追加嵌套 `collapsible_panel`（每轮一个）？ | FC 式嵌套推理面板 | 不能则用单个 markdown 内的分区标题行（AP 式） |
| P4 | 插件自己起 asyncio 心跳定时器（每 2–5s 更新耗时/Working）是否触发限流或卡级死法？ | Working 心跳/实时耗时 | 不能则只在核心帧到达时更新 |

探针结果写入 `docs/audits/v0.7.1-visual/`，不靠猜。

## 4. 分阶段实施

### V0：冻结与基线（0.5 天）
- 冻结本规划与三份参考截图的关键 token 表；
- 给 `cards.py` 加视觉 token 常量 + 单测断言（不改行为）；
- 记录当前卡片 JSON 的 golden snapshot，作为 before/after 对照；
- 真机截图 0 张（用现有截图作 before）。

### V1：静态排版对齐（1–2 天，不改元素结构）
- 应用 §2 token：圆角、padding、vertical_spacing、markdown margin、text_size；
- 推理分区标题行样式：`💭 思考 · Xs` + 每轮 `第 N 轮 · Xs`，去掉双空行；
- 工具行在**现有 markdown 元素内**对齐 CLS 文案：加粗动作名 + 耗时 + 彩色状态词 +
  灰色细节行（`standard_icon` 与 22px 缩进属于元素结构，放 V3）；
- 正文 `margin:0`、段距统一；
- **门禁**：golden snapshot 更新并人工审 diff；无结构变化、无新探针。
- 真机验收：**1 张截图**（含工具 + 推理 + 收尾）。

### V2：实时状态与卡片头（2–3 天，依赖 P1/P2/P4）
- 建卡时加卡片级状态头（配置 `card_status_header`）；
- 面板 header 流式实时显示摘要（依赖 P1；不能则收尾更新）；
- 完成/中止/失败时切 header template 与面板边框色（停止=黄，错误=红）；
- 心跳定时器显示 Working/耗时（依赖 P4）。
- 真机验收：**1 张流式截图 + 1 张终态截图**（可同一回合前后）。

### V3：推理/工具结构增强（2–4 天，依赖 P3）
- 工具标题改成 CLS 同构：`div` + `standard_icon(token=tool_02, grey)` +
  `lark_md(notation)`，`**动作名** · <font color>状态词</font>`；
- 工具细节改成 `div` + `margin-left=22px` + 灰色 `plain_text`/`lark_md`；
- 推理按已拍板 **A**：每轮一个嵌套 `collapsible_panel`（`vertical_spacing=8px`，
  标题 `plain_text` 灰色 `notation`，内容一个 `markdown` `notation`）；
- 面板展开层级/缩进/间距对齐 CLS/FC/AP 源码 token；
- 长内容截断、元素预算、卡链兼容、`show_reasoning=false` 时只留摘要行。
- 真机验收：**1 张展开面板截图**。

### V4：边界与收口（1–2 天）
- `/stop`、失败、无工具、纯推理、超长面板、元素近上限；
- 中英文/emoji 图标一致性；
- 更新 README/AGENTS/CHANGELOG 的观感说明；
- 完整门禁 + 用户最终截图。

## 5. 已拍板决策（不再重复询问）

1. 卡片顶部状态条：**加**，配置键 `card_status_header` 控制显隐（默认 `true`），
   只显示状态词，颜色与边框一致（处理中蓝 / 完成绿 / 停止黄 / 出错红）。
2. 推理形态：**A（FC 式嵌套面板）**；P3 探针不通过时退 B（分区标题行）。
3. 中止色：**黄**。
4. 推理显隐：新增 `show_reasoning`（默认 `false`，对齐 CLS/aiduPOP）；
   `false` 时只保留面板 header 的 `💭 思考 Xs` 摘要；`true` 时按 A 展示全文。

## 6. 验收口径

- 展开面板无多余空行；推理、工具、正文三层视觉层级一眼可分；
- 工具行图标/字重/耗时/状态色与 CLS 截图同风格；
- 卡片顶部状态条可配置显隐，颜色与边框状态一致；页脚顺序不变；
- 正文无工具进度；流式期占位/心跳自然；
- `show_reasoning: false` 时面板不出现推理正文，但保留思考耗时摘要；
- 每阶段 golden snapshot 更新 + 人工审 diff；真机截图总数控制在 3–4 张；
- 不改 Hermes 源码，不引入 Monkey Patch。

## 7. 风险

- CardKit 结构在建卡时定死：header/border 实时更新可能不可用 → 探针 P1/P2 先定；
- 动态元素创建有元素/字节预算与 sequence 约束 → 只在 P3 做，且必须有卡链兜底；
- 心跳定时器可能撞限流 → P4 先探针，必要时降频/仅核心帧更新；
- emoji 在不同客户端渲染不一致 → 关键状态词保留文字，不单靠图标；
- 视觉验收依赖真机，无法纯自动化 → 用 golden snapshot 抓 token 回归 + 少量真机截图。
