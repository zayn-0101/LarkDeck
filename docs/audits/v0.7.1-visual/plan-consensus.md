# v0.7.1 视觉重构计划统一结论（A/B/C 收敛）

- 原计划：`docs/plan-v0.7.1-visual.md` SHA256 `e0784738…`（2026-09-20 冻结）
- 审计：A 架构/源码证据、B 用户可见/回归、C 执行/反假绿；三份完整报告 + 两轮逐条表态
- 结论：**GO-WITH-CONDITIONS**；条件全部落入计划 §9「三方共识修订（v2）」
- 收敛规则：反例实验 > 安全优先级（不丢消息/不吞正文 > 不泄漏进度 > 不退化 > 行数/速度）>
  父级书面仲裁；异议保留在 Appendix

## 1. 已统一的关键决策

### 1.1 live 默认与部署
- `visual_engine` live 默认**保持 legacy 到 V4**；V1/V2/V3 只用显式 `structured` canary。
- V1 canary 走独立进程/探针卡，不重启网关；V3 必须至少一次经用户同意的 live structured 回合。
- 若任一阶段 `golden_cardkit_trace` 的 legacy 输出发生非空 diff，或 live 软链仍指开发树导致
  重启会加载半成品：**先建独立部署 worktree，再继续**（B 的硬条件）。
- V4 才把默认切 structured；回滚 = 改回 legacy 配置 + 重启，代码级问题 revert commit。

### 1.2 渲染架构
- 放弃「通用树 diff」；采用**子树重建 + 精确 API 操作矩阵**：
  1. `card_element.content`：answer / 推理正文逐字写，**最后写**（提交点）；
  2. `partial_update_element`：替换面板 `header.title` / 外层面板 `elements` / `border`；
  3. `card_element.create`：新增顶层/嵌套 segment 元素（已由本仓库真机肉眼确认）；
  4. `card.batch_update` 的 `add_elements/delete_elements`：本仓库未验证，**V1 不得依赖**；
     要用先补真机 schema 探针。
- 禁止 `partial_element` 顶层带 `tag`、禁止增量改 markdown `text_size`（300312）。
- 单 sequence 计数器共用、严格 +1、不回退不复用；uuid 由内容确定；id 唯一且生命周期冻结。

### 1.3 覆盖范围与回退
- structured 覆盖：流式卡、finalize 收尾卡、`/stop` 重绘、卡链封旧卡/seed。
- **DEGRADE 是唯一同卡回退车道**：保留 legacy 渲染 + `engine_stamp=degraded`，
  本回合后续帧不回切 structured；V4 删除的是 legacy 配置路径，不删降级渲染器。
- 结构化 op 失败必须 fault-injection：正文零丢失、状态色不丢、单卡不重复、WARNING 留痕。

### 1.4 元素/字节预算
- 递归序列化 card JSON 实数计数（含嵌套/转义）；元素墙 200、软阈值 180 + reserve 2。
- 工具步估算：基础 3 + 每步 3 + detail 2 + result/error 2；推理轮 4；answer 按 markdown 膨胀估算。
- 运行时 create/delete 必须记账；`300315` 外层 + `300305` 内层解析。
- `max_panel_steps` 语义改为元素预算推导；默认值由预算反推；超限 trim 最早步骤 +
  「已折叠」提示，再考虑封卡；字节墙与元素墙同时守。

### 1.5 P3 嵌套面板
- 目标仍是用户拍板的 A 形态；V3 实现：流式期用已证 API 的顶层面板，完成卡整卡重建 A 嵌套面板。
- P3 三路优先顺序（父级仲裁）：① `card_element.create`（本仓库真机证据最强）→
  ② `partial_update_element` 替换外层面板 elements（AP 证据，A 的首选）→
  ③ `batch_update add_elements`（仅第三方证据，最后）。
- 每路判据 = 接口 code=0 + 会话未关 + sequence 账本 + 本地元素计数 + **人眼落点/展开/二次更新**；
  无读回接口，code=0 只作必要条件。
- P3 失败 → 先退 B（AP 式分区标题行），必须用户签字确认观感降级，release notes 不得宣称 A 已完成。

### 1.6 变异/门禁（满足反假绿且开发期不等几小时）
- 开发期 `tests/run_fast.py`（默认约 7.5s；`--full` 约 30s）只作快速回路，不替代阶段门禁。
- 每条新增/修改断言必须有一条 expected-kill，定向 `mutate_check -k` 跑出 🔴；
  锚点失效/崩溃不算红。
- `check_cardview` 必须接入 `mutate_check._run_gates`，且含 content 逐字断言 + 子树 JSON +
  每字段至少一次非空；只做结构投影对内容恒真，禁止。
- 阶段门禁：preflight + 定向红 + **改动面全量后台分片**（346 锚点，≤30 分钟/阶段）；
  任何用户可见重启前必须跑完并绿；merge/rebase 与发布候选跑全量分片 + expected-kill + wall-clock。
- 高风险族 inventory 至少覆盖：结构+内容、id/sequence、元素/字节预算+卡链、限流+create 记账、
  回退/降级、状态色双载体、配置键、answer 提交点、截断/脱敏、i18n/theme。

### 1.7 配置与文档
- V0 同 commit 落地三键：`visual_engine`(legacy 默认)、`card_status_header`(true)、
  `show_reasoning`(false)；同步 `_DEFAULTS` + plugin.yaml + README + AGENTS + CHANGELOG +
  `check_override` + 变异 inventory。
- 每个键必须被生产路径读取：两种取值驱动真实 `send_stream_frame`，断言 card JSON 不同；
  未实现前显式设置非默认值必须 WARNING。
- `normal` vs `normal_v2`：V0 真机探针后写回 token；探针前保持 `normal` + `apply_text_profile`；
  结构化构建统一走 profile，不能写死 text_size 废掉配置。

### 1.8 Git / 冻结
- 从当前 main 开 `v0.7.1-visual` 分支，阶段 commit 只在分支；main 保持已验证 release。
- 每阶段审计前：`git stash create`/`write-tree` 得 TREE + `git archive` 不可变导出；
  manifest 记 plan sha、HEAD、TREE、dirty diff sha、解释器、参考源哈希、门禁日志 sha。
- 审计在飞期间 TREE 变化 ⇒ 三份结论全部作废重跑；审计报告必须写自己看到的 TREE。
- 里程碑：发布三审收敛 + 用户最终截图 + 全量门禁后 fast-forward main，再 push main +
  annotated tag（带 tree sha / expected-kill / wall-clock）；push 前 `git log origin/main..main` 白名单。

### 1.9 截图/重启矩阵（4 个用户确认窗口，8 张）
- 窗口 0（V0）：`normal` vs `normal_v2` 字号/间距探针卡 1 张；独立进程，不重启。
- 窗口 1（V1+V2，一次重启 + structured/header 开）：①流式工具行 ②同卡收尾结构不变 ③`/stop` 黄边；
  V1 自身 = 探针卡确认，首次 live 重启 = 本窗口，覆盖 V2。
- 窗口 2（V3，一次重启 + show_reasoning=true）：④展开嵌套轮 + Result/Error ⑤false 同回合只留摘要；
  P3 探针眼睛确认并入本窗口。
- 窗口 3（V4，默认切换后一次重启）：⑥≥20 步长回合 ⑦最终发布确认 + `/larkdeck status`。
- 每窗口前置 = 定向红 + 改动面分片绿；失败在同窗口修完重看，不新增窗口；看完恢复默认配置。

## 2. Appendix：保留的异议与未验证项

- **A 的异议（P3 顺序）**：A 首选 `partial_update_element` 替换外层面板 elements（AP 证据）；
  父级按 B/C 的「create 已有本仓库真机证据」排第一，A 路径排第二，保留为 P3 必测项。
- **B 的部署硬条件**：live 软链若仍指开发树，任何重启都会加载半成品；V0 需先建独立部署
  worktree 并重指软链（当前软链已指向 `v0.7.1-visual` 开发树，条件已触发）。
- **B 的非阻断残留**：R1 DEGRADE 车道 `show_reasoning=false` 断言；R2 心跳 finalize/stop 后
  零写入；R3 V0 字号探针并入窗口 0；R4 V1 点名「create 成功、后续写失败⇒记账保留」；
  R5 §9.2 措辞收紧为「顶层落点已证；嵌套容器待 P3」。以上已写入计划 §9。
- **冻结配对**：v2.0 `audit_target = dbf9c8a / tree f45592b4`（plan blob）；
  `manifest_commit = dd0186d / tree a77ad0d4`（仅新增审计 manifest，按 §9.8 元数据豁免）。
  v2.1 `audit_target = 84331b3 / tree 6f315c22`（plan blob SHA `c1e6cffb…`），
  manifest = `plan-freeze-v2.1.json`；其后的 consensus 窗口口径/manifest/tool 变更属元数据 delta，
  按 §9.8 豁免，不改 plan blob。
- **C 的 C1–C10** 全部接受，B1/B2 最小修法已写入 §9.3/§9.4/§9.6。
- **V0 冻结配对（v2.2）**：`audit_target = d397252482c0c6b82a472661739bb937aab0c5ce`（V0 代码+文档），
  plan 仅修正 `run_fast` 实测耗时数字（默认 ~7.5s / --full ~30s），新 plan SHA
  `cee0715305149f76b17a2f841a57a3a8197817eac2932dc998fd4bbdb00d2477`；
  `manifest_commit` 在 V0 commit 后补记。旧 v2.1 plan 配对归档不覆盖。
- **V1 必须迁移 token 到生产常量**：V0-4/8/9/14 的 expected-kill 目前打在 `visual-tokens.json`
  文档上；V1 结构化构建器落地时必须让生产代码引用同一常量表，check_cardview 改为从生产断言，
  否则这些是 doc-only kill（A2 F3）。
- **V0-5/6/7/15/16/17 是 expiring 锚点**：它们靠“未实现告警”路径存活；V2/V3 移除告警后必须改挂
  “两取值驱动真实 send_stream_frame，断言 card JSON 不同”的断言（A2 F4）。
- 未验证：嵌套 collapsible_panel 客户端渲染、header/border partial 视觉、normal_v2 支持、
  standard_icon 逐项显示、batch add/delete schema、心跳真机限流/生命周期、result 真实大小与隐私、
  detached 网关重启/回滚、check_cardview 尚未实现、tree manifest 尚未建立。
