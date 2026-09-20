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

## 0.4 重构蓝图：从「markdown 字符串面板」到「结构化元素树」

**现状问题**：我们把面板压成 `panel_body` / `panel_tools` 两个 markdown 字符串，
所以无法表达 CLS/FC 的 `div + standard_icon`、22px 缩进、嵌套 `collapsible_panel`、
逐元素状态色和实时结构更新；这不是调间距能解决的，需要重构渲染层。

**目标架构（官方 CardKit API，不改 Hermes）**：
1. **视图模型** `core/cardview.py`：
   - `CardView`：header / answer / panel / footer；
   - `PanelNode`：类型 `reasoning_round | tool_step | tool_group`，字段
     `element_id / icon_token / title / status / elapsed_ms / detail / result_block /
     error_block / children / dirty`；
   - 纯数据、可序列化、可做 golden snapshot。
2. **构建器**：把 `PanelNode` 映射成飞书元素：
   - 工具行 = `div + standard_icon + lark_md(notation)`（CLS `builder.py:155-238`）；
   - 推理轮 = 嵌套 `collapsible_panel`（CLS `builder.py:248-280` / FC `:283-315`）；
   - 外层统一面板 = `collapsible_panel`（4px/8px/5px token）；
   - 卡片 header = `plain_text + i18n_content + template`（CLS/FC `:82-105`）。
3. **Diff/更新引擎**：每帧对 `CardView` 与上次快照做 diff，产出 `card.batch_update`
   动作（add / update / delete），维护元素 id、sequence、元素预算与卡链；
   对应 CLS `streaming/controller.py:180-330` 的 `build_add_segment_action` /
   `build_tool_update_action` / `build_reasoning_finalized_action` 思路，
   但用官方 `card.batch_update` 接口实现。
4. **流式文本**：answer 仍走现有 own 累积；推理/工具节点按 dirty 标记增量写元素内容。
5. **过渡开关**：新增 `visual_engine: "structured" | "legacy"`（默认先 `legacy`，
   V1 完成后切 `structured`），任何一步失败可回落旧 markdown 面板；V4 再删 legacy。

**为什么这是路线内重构**：CLS/FC/AP 的 `batch_update` / `card_element.content` /
`collapsible_panel` 全部是飞书官方 CardKit 接口；我们只是把「拼 markdown」换成
「维护元素树 + diff」。事件仍来自官方 hooks，不需要 AST patch。

**配套门禁**：
- `tests/check_cardview.py`：元素树 golden（元素 tag/属性/token/缩进/字号/颜色）；
- 每帧 diff 的 sequence 单调、元素数不超预算、失败可回落；
- 工具图标 token 表（§V3）逐项断言；
- 真机截图仅 V1/V3/V4 三次。

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
- 冻结 §0.4 视图模型 schema、§2 token 表、CLS 图标 token 表；
- 新增 `visual_engine` 配置（默认 `legacy`）、`card_status_header`、`show_reasoning`
  三个配置键的**空实现**（不改变现有行为）；
- 记录当前卡片 JSON golden snapshot 作为 before/after 对照；
- 真机截图 0 张。

### V1：结构化渲染引擎（3–5 天，核心重构）
- 新建 `core/cardview.py`：`CardView` / `PanelNode` / 序列化；
- 新建结构化构建器：`div + standard_icon + lark_md(notation)` 工具行、
  22px 缩进细节行、`collapsible_panel` 外层；
- 新建 diff/更新引擎：`card.batch_update` add/update、元素 id、sequence、元素预算；
- adapter 接入 `visual_engine="structured"`：answer 仍走 own 累积，面板走元素树；
- 旧 markdown 面板保留为 `legacy` 回退；**默认仍 legacy**，探针/单测通过后 V1 末切 structured；
- 门禁：`tests/check_cardview.py` 元素树 golden + token 断言 + 元素预算；
- 真机验收：**1 张截图**（工具行小图标 + 面板结构）。

### V2：状态条与实时摘要（2–3 天，依赖 P1/P2/P4）
- 卡片级 header（配置 `card_status_header`）：处理中蓝 / 完成绿 / 停止黄 / 出错红；
- 面板 header 单行实时摘要：`💭 思考 Xs · 🛠️ 工具执行 · N 步`；
- 完成/中止/失败时切 header template 与面板边框色；
- 心跳定时器显示 Working/耗时（依赖 P4）；
- 真机验收：**1 张流式截图 + 1 张终态截图**。

### V3：推理 A 形态与动态时间线（3–5 天，依赖 P3）
- 每轮推理一个嵌套 `collapsible_panel`（`vertical_spacing=8px`，标题灰色 notation，
  内容一个 `markdown` notation）；`show_reasoning=false` 时不创建，只在 header 留摘要；
- 用 segment/timeline 模型按事件顺序动态追加推理轮与工具行；
- 工具输出/错误块用官方 `post_tool_call.result/error_type` 渲染
  `**Result** / **Error**` fenced block；
- 卡链、元素预算、sequence 兼容；
- 真机验收：**1 张展开面板截图**。

### V4：收口与发布（1–2 天）
- `visual_engine` 默认切 `structured`，删除旧 markdown 面板路径；
- `/stop`、失败、无工具、纯推理、超长面板、元素近上限；
- 中英文/emoji 图标一致性；更新 README/AGENTS/CHANGELOG；
- 完整门禁 + 用户最终截图 + 版本发布。

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

## 8. 执行规则（老规矩，v0.7.1）

### 8.1 Goal 模式
- 本计划在 goal `goal-8c20640d-8ad5-477e-b331-1f68671eff7d`（max rounds 40）下执行；
- 每轮只推进一个阶段的明确子任务，阶段状态写入 `docs/audits/v0.7.1-visual/`。

### 8.2 计划与阶段审计
- 本计划冻结后，先由 3 个独立子代理从不同角度审计：
  A 架构/源码证据、B 用户可见效果/回归、C 执行风险/反假绿；
- 每份审计必须给 GO / GO-WITH-CONDITIONS / NO-GO + 证据；不同意的地方由父代理组织讨论，
  直到统一结论写入 `docs/audits/v0.7.1-visual/plan-consensus.md`；
- V0–V4 **每个阶段执行结束后**同样各过 3 个独立子代理对抗审计并收敛，未收敛不得进入下一阶段；
- 审计对象必须记录工作树 SHA；审计期间父代理不并发改同一批文件，避免证据失效。

### 8.3 Git / Commit / Push 规则
- **阶段审计收敛后**：本地 commit（Conventional Commit），说明本阶段行为变更与证据；
- **不 push 中间态**：未过阶段审计的 commit 只留本地；
- **Push/tag/release 只在发布里程碑**：最终用户截图确认 + 完整门禁通过 + 3 个子代理发布审计收敛后，
  才 `git push`、打 tag、创建 release；
- 任何“重跑夹具/更新 golden”的 diff 必须在同一 commit 里声明行为变更。

### 8.4 Mac 网关生效规则
- 插件通过软链 `/Users/Zayn/.hermes/plugins/larkdeck -> /Users/Zayn/Code/larkdeck` 生效；
- **V1 / V3 / V4 的验收节点**才重启网关并请用户看截图；V0/V2 默认不动用户可见运行态；
- 每次重启前先跑完该阶段自动门禁；探针需要真机发卡时先说明影响并征得同意；
- 最终发布后重启一次加载正式版本，用户只做最后一次桌面截图确认。

### 8.5 门禁与变异策略
- 每阶段：`test_units` + 定向 `check_cardview` + 旧四门禁；
- 变异只跑**定向 smoke**（每阶段 ≤20 条、只覆盖本阶段改动面），不再跑数小时全量；
- 发布候选才跑归档全量分片，并单独记录 wall-clock 与 red=expected-kill；
- 任何阶段失败：回退该阶段 commit，修完重跑本阶段 3 个审计，不带着问题进下一阶段。


## 9. 三方共识修订（v2，supersedes 上文冲突条款）

> 本节由 A/B/C 三份完整审计 + 两轮逐条讨论收敛而成；统一结论见
> `docs/audits/v0.7.1-visual/plan-consensus.md`。**与 §0–§8 冲突时以本节为准。**

### 9.1 live 默认与部署
- `visual_engine` live 默认保持 **legacy 到 V4**；V1/V2/V3 只用显式 `structured` canary。
- V1 用户确认 = 独立进程/探针卡，不重启网关；**首次 live 重启 = 窗口 1，覆盖 V2**。
- 任一阶段若 legacy `golden_cardkit_trace` 出现非空 diff，或 live 软链仍指开发树，
  **先建独立部署 worktree** 再继续；否则不得重启网关。
- **当前已触发**：`~/.hermes/plugins/larkdeck` 指向开发树且 HEAD 在 `v0.7.1-visual`；
  V0 必须先把软链重指到已验证 release 的独立部署 worktree（用户同意后），之后才允许重启。
- 重启 checklist：`hermes gateway status` 确认空闲 → 跑本阶段门禁 → `hermes gateway restart`
  → 重启后一张探针卡 + `/larkdeck status`；回滚 = 改回 legacy 配置 + 重启，代码级问题 revert commit。
- V4 才切默认 structured；回退 = 改回 legacy 配置 + 重启，代码级问题 revert commit。

### 9.2 渲染架构（取代 §0.4 的「通用树 diff」）
- 操作矩阵：① `card_element.content` 写 answer/推理正文，**最后写**；
  ② `partial_update_element` 替换 header.title / 外层面板 elements / border；
  ③ `card_element.create` 新增**顶层** segment（本仓库真机已证）；**嵌套落点待 P3 人眼**；
  ④ `batch_update add_elements/delete_elements` 本仓库未验，V1 不得依赖，要用先补 schema 探针。
- 禁止 partial_element 顶层带 `tag`、禁止增量改 markdown `text_size`（300312）。
- 单 sequence 计数器共用、严格 +1、不回退不复用；uuid 由内容确定；id 唯一、生命周期冻结。
- **失败分支合并必须保留 create/delete 记账**（白名单合并旧 state 不许丢）；同 id create 本地
  幂等/拒绝，避免 300301；`_ck_elems_from_card` 的运行时元素表必须是**可增长的 list**。

### 9.3 覆盖与回退（取代 §4 V1/V3/V4 冲突项）
- structured 覆盖：流式卡、finalize 收尾卡、`/stop` 重绘、卡链封旧卡/seed。
- **DEGRADE 是唯一同卡回退车道**：legacy 渲染 + `engine_stamp=degraded`，本回合不回切
  structured；V4 删除 legacy 配置路径，但保留降级渲染器。
- 结构化 op 失败必须 fault-injection：正文零丢失、状态色不丢、单卡不重复、WARNING 留痕；
  降级 patch 自身失败才走核心纯文本回退。
- **心跳/定时器生命周期**：每回合至多 1 个 timer；finalize/`/stop`/error/`on_session_end`/
  插件卸载必须 cancel；写前查 `ck_degrade`/`ck_dead`/滑窗；回调异常自吞；
  假时钟断言 finalize 后推进 ≥10s 出站写入 = 0。
- **show_reasoning 全车道**：DEGRADE/legacy 降级渲染器同样过 `show_reasoning=false` 过滤；
  断言降级卡 JSON 不含推理正文、只留摘要。

### 9.4 元素/字节预算（取代 §7 与 V3 的「卡链一句话」）
- 递归序列化 card JSON 实数计数；元素墙 200、阈值 180 + reserve 2。
- 工具步 = 3 基础 + 3/步 + 2 detail + 2 result/error；推理轮 4；answer 按 markdown 膨胀估算。
- 运行时 create/delete 必须记账；300315 外层 + 300305 内层解析。
- `max_panel_steps` 改为元素预算推导；超限 trim 最早 + 「已折叠」提示，再考虑封卡；
  元素墙与字节墙同时守。
- **near-limit fixture 必须证明墙会响**：45 轮 + 5 工具（210 元素）触发 trim/封卡；
  100KB 含假密钥 result 触发截断/脱敏/字节墙；删 trim/截断/脱敏的 mutation 必须 🔴。
- **失败分支记账**：create 成功、同帧后续写失败时，合并旧 state 必须保留 create/delete 记账；
  同 id create 本地幂等/拒绝；元素表 `_ck_elems_from_card` 必须是可增长的 list。

### 9.5 P3 嵌套面板（取代 §3 P3 / V3 A 形态描述）
- 目标仍是 A 形态；V3 实现：流式期用已证 API 的顶层面板，完成卡整卡重建 A 嵌套面板。
- P3 三路顺序：① `card_element.create`（本仓库真机证据最强）→ ② `partial_update_element`
  替换外层面板 elements（AP 证据 / A 的首选）→ ③ `batch_update add_elements`（第三方证据，最后）。
- 每路判据 = code=0 + 会话未关 + sequence 账本 + 本地元素计数 + **人眼落点/展开/二次更新**；
  code=0 只作必要条件。P3 失败 → 先退 B（AP 式分区标题行），必须用户签字确认观感降级。

### 9.6 变异/门禁（取代 §8.5 的 ≤20 手挑）
- 开发期 `tests/run_fast.py`（3.65s）只作快速回路，不替代阶段门禁。
- 每条新增/修改断言必须有一条 expected-kill，定向 `mutate_check -k` 跑出 🔴；锚点失效/崩溃不算红。
- `check_cardview` 必须接入 `mutate_check._run_gates`，含 content 逐字断言 + 子树 JSON +
  每字段至少一次非空；只做结构投影对内容恒真，禁止。
- 阶段门禁 = preflight + 定向红 + **改动面全量后台分片**（≤30 分钟/阶段）；
  任何用户可见重启前必须跑完并绿；merge/rebase 与发布候选跑全量分片 + expected-kill + wall-clock。
- 阶段门禁清单固定包含：`test_units`、`check_own_body`、`check_override`、`check_hooks`、
  `check_clarify_e2e`、`probe_render`（改卡片结构时强制）、`mutate_check --preflight`。
- 高风险族 inventory 至少覆盖：结构+内容、id/sequence、元素/字节预算+卡链、限流+create 记账、
  回退/降级、状态色双载体、配置键、answer 提交点、截断/脱敏、i18n/theme、**心跳/定时器生命周期**。
- 心跳 gate：每回合 ≤1 timer、终态 cancel、finalize 后零写入；删 cancel 的 mutation 必须 🔴。
- 预算 gate 必须用 near-limit fixture（45 轮+5 工具、100KB 假密钥 result）证明墙会响。

### 9.7 配置与字号（取代 §2/V0 冲突项）
- V0 同 commit 落地三键：`visual_engine`(legacy)、`card_status_header`(true)、
  `show_reasoning`(false)；同步 `_DEFAULTS` + plugin.yaml + README + AGENTS + CHANGELOG +
  `check_override` + 变异 inventory。
- 每个键必须被生产路径读取：两取值驱动真实 `send_stream_frame`，断言 card JSON 不同；
  未实现前显式设置非默认值必须 WARNING。
- `normal` vs `normal_v2`：V0 真机探针后写回 token；探针前保持 `normal` + `apply_text_profile`；
  结构化构建统一走 profile，不能写死 text_size 废掉配置。
- `show_reasoning=false` 过滤必须覆盖 DEGRADE/legacy 降级车道：断言降级卡 JSON 不含推理正文。
- §2 token 表补齐：border 状态色、standard_icon token/size/color、icon_position、
  icon_expanded_angle、header vertical_align、tool 状态色、expanded 默认、loading 锚点、footer hr；
  AP 无卡片级 header（§0.2 的「三家一致」表述修正为 CLS/FC 有）。

### 9.8 Git / 冻结（取代 §8.2/§8.3 冲突项）
- 从当前 main 开 `v0.7.1-visual` 分支，阶段 commit 只在分支；main 保持已验证 release。
- 每阶段审计前：`git stash create`/`write-tree` 得 TREE + `git archive` 不可变导出；
  manifest 记 plan sha、HEAD、TREE、dirty diff sha、解释器、参考源哈希、门禁日志 sha。
- 审计在飞期间 TREE 变化 ⇒ 三份结论全部作废重跑；审计报告写自己看到的 TREE。
- **元数据豁免**：只新增/修改 `docs/audits/` 下 manifest 的提交不改变 plan blob，不触发作废；
  引用时写明 `audit_target: commit/tree` 与 `manifest_commit: commit/tree` 配对。
- 里程碑：发布三审收敛 + 用户最终截图 + 全量门禁后 fast-forward main，再 push main +
  annotated tag（带 tree sha / expected-kill / wall-clock）；push 前 `git log origin/main..main` 白名单。

### 9.9 截图/重启矩阵（取代 §0.4/§6/§8.4 口径）
| 窗口 | 阶段 | 用户看什么 | 张数 | 重启 |
|---|---|---|---|---|
| 0 | V0 | `normal` vs `normal_v2` 探针卡（字号/间距） | 1 | 独立进程，不重启 |
| 1 | V1+V2 | ①流式工具行 ②同卡收尾结构不变 ③`/stop` 黄边 | 3 | 一次，覆盖 V2；V1 自身=探针卡确认 |
| 2 | V3 | ④展开嵌套轮+Result/Error ⑤false 同回合只留摘要；P3 探针眼睛并入 | 2 | 一次 |
| 3 | V4 | ⑥≥20 步长回合 ⑦最终发布确认 + `/larkdeck status` | 2 | 一次，默认切 structured |

每窗口前置 = 定向红 + 改动面分片绿；失败在同窗口修完重看，不新增窗口；看完恢复默认配置。
重启前按 §9.1 checklist（gateway status 空闲 → 门禁 → restart → 探针卡 + status）。

