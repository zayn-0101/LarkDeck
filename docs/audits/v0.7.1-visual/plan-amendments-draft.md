# v0.7.1 计划修正草案（B 中期发现 + 源码复核）

- 冻结计划：`docs/plan-v0.7.1-visual.md` SHA256 `e0784738…`（本草案不改它）
- 来源：审计 B 中期预警（2026-09-20）+ 父代理只读复核
- 状态：等待 A/C 完整报告后合并 → 三方讨论 → 更新计划 → 重新冻结 hash → 重新审计修正项

## 修正 1：V1 必须覆盖全部整卡路径，不能只接流式元素通道

B 证据：收尾整卡 patch（`core/adapter.py:3381-3385`）、`/stop` 重绘（`:3790+`）、
卡链封旧卡（`:2930+`）、降级 patch（`:3580+`）仍走 `_ld_build_card` → `_ld_panel` →
`cards.unified_panel`（markdown 面板）。

后果：流式期是结构化元素树，一收尾/`/stop` 变回 markdown 列表 → 观感跳变，
而 `/stop` 恰恰是用户最可能截图的帧。

修正：
- V1 定义 `render_card_view(view, mode)` 为唯一渲染入口，四条整卡路径全部接同一个 view model；
- `visual_engine=structured` 时，收尾/`/stop`/封卡/降级都渲染结构化面板；
- V1 golden 必须包含：流式帧、收尾卡、`/stop` 卡、卡链封旧卡、降级 patch 五类快照；
- V1 真机截图必须包含终态（或 `/stop`）卡，而不是只看流式中间帧。

## 修正 2：结构化元素预算必须重算，否则会撞 200 元素墙

B 证据：CLS `estimate_tool_elements` 口径 ≈ 3 元素/工具步 + 2 detail + 2 output；
推理轮 ≈ 4 元素/轮；而现有 `max_panel_steps=30`、`_PANEL_CHILDREN_ROOM=188`、
`_CARD_FIXED_ELEMENTS=5` 全是「一个 markdown 装全部」的旧口径。

后果：30 步结构化 ≈ 90–210 元素，必撞飞书 200 硬墙；撞墙是整卡不渲染/帧失败，
核心会停用本回合 native → 掉纯文本。

修正（V1 前置子任务）：
- 采用 CLS 的精确口径（`streaming/segment_helper.py:18-51`）：
  `ELEMENT_THRESHOLD=180`、`FOOTER_RESERVE=2`；
  工具面板基础 3 元素；每个工具步 `+3`（title div + standard_icon + lark_md），
  有 detail `+2`，有 result/error block `+2`；每个推理轮 `4`；answer `1`。
- 结构化模式重新定义 `max_panel_steps` 语义：默认值从 30 降到由预算反推的值
  （按 6 元素/步保守估算，30 步=180+ 必撞墙；建议默认 12–15，并在文档/配置说明里写清
  「结构化模式下它是元素预算推导上限，不是行数上限」）；
- 每帧做元素预算墙：超出先丢最早步骤并加「已折叠」提示，再考虑封卡；
- 预算/墙的 expected-kill 变异：调高 max_panel_steps 不崩、调低保留最近步骤。

## 修正 3：继承 plan-v1 登记的前置基础设施

B 证据：`docs/plan-v1.md:186-206` 当时把「每工具一行」推迟，并登记 6 条前置：
1. 滑窗写入守卫对含 `create` 帧的处置（最坏 14 次/秒）；
2. `300315` 一名两义（重复 id / 200 墙内层 300305）→ 解析内层码；
3. 失败分支白名单合并会丢 create 记账 → 重复 id；
4. `_ck_elems_from_card` 动态元素表要支持运行时增长（list 类型）；
5. 收尾 append-only（最早 N 步）与 `unified_panel`（最近 N 步）语义差；
6. golden 夹具必须含工具步，否则零判别力。

修正：V1 任务列表显式包含以上 6 条；未完成不进入 V1 真机验收。

## 修正 4：冻结「各阶段看什么/几张/是否重启」表

B 证据：计划 §0、§0.4、§6、V2、§8.4 四处口径不一致（1 次 / 3 次 / 3–4 张 / V2 两张 /
V0/V2 不重启）。

修正后建议表（待 A/C 讨论）：

| 阶段 | 用户截图 | 网关重启 | 看什么 |
|---|---|---|---|
| V0 | 0 | 否 | 只看 golden/单测 |
| V1 | 1 张（终态卡） | 是 | 工具行小图标、22px 缩进、收尾结构化 |
| V2 | 1 张（流式态） | 是 | 状态条颜色、单行摘要、Working 心跳 |
| V3 | 1 张（展开面板） | 是 | A 形态嵌套推理、Result/Error 块 |
| V4 | 1 张（最终验收） | 是 | 全套 + 页脚顺序 + 正文干净 |

合计 4 张；若 V2 的探针失败，V2 与 V3 合并为一次截图。

## 待 A/C 补充的问题

- `normal` vs `normal_v2` 的客户端兼容探针；
- `show_reasoning` 在 legacy 回退下的一致性；
- 流式期面板展开策略（默认收起还是展开）；
- P3 判据不能只看自动化，必须真机肉眼确认；
- Result/Error 收集必须截断 + 脱敏（复用现有 `redact_inline_secrets`）。

## B 完整报告条件（2026-09-20，待 A/C 表态后合并）

### 必改（H/M）
- **H1** V1 必须把 view model 双载体（CardKit entity + patch 卡）接进收尾 / `/stop` / 卡链封旧卡 / 降级四条整卡路径；golden 与截图包含终态与停止态。
- **H2** 按 CLS 公式重算元素/字节预算；补 48 步合成用例与真机长回合验收；重定义 `max_panel_steps`。
- **H3** V1 显式继承 `docs/plan-v1.md:186-206` 六条前置，并配门禁/变异。
- **H4** 冻结「阶段×场景×张数×是否重启×判定」表；P3 眼睛确认单列。
- **M1** answer 保持独立 `card_element.content` 通道且**最后写**；结构化 diff 不得把 answer 混进 batch。
- **M2** `post_tool_call.result/error_type` 进入 view model 前必须截断 + 脱敏（复用 `redact_inline_secrets`）+ 计入预算；补含密钥结果测试。
- **M3** `show_reasoning` 提升到 view model 过滤层，legacy/patch 回退也遵守；不能只在 A 形态生效。
- **M4** 心跳定时器生命周期：挂回合状态，finalize/stop/drop 取消，写前查 `ck_dead`/degrade/窗口。
- **M5** 嵌套推理面板流式期能力先探针：CLS/FC 流式期主要 append 顶层面板，嵌套面板多在整卡重建出现；计划需明确用 `add_elements` 还是 `card_element.create`，并用一张探针卡人眼判。
- **M6** `visual_engine` 只切一次，且在 V1 截图之前；消除 plan 内三处切换时点矛盾。
- **M7** 状态色第二载体引入后，`status_shell` / `_log_no_colour_once` / `_CARD_FIXED_ELEMENTS=5` / `"collapsible_panel" in blob` 判据要重算；header 纳入降载保留与计数。
- **M8** V0 新增三配置键必须同 commit 同步 README/`_DEFAULTS`/plugin.yaml，否则 `test_config_schema_matches_defaults_exactly` 会红。
- **M9** `normal` vs `normal_v2` 先做真机探针，写回 token/text_profile，再冻结 V1 golden；不能先锁死小字号。

### 建议（L）
- **L1** header「已停止」与 footer「已中止」文案统一；明确 processing 边框色是灰还是蓝。
- **L2** CLS/FC 的 loading custom_icon、footer 前 `hr` 是否纳入 V1/V3。
- **L3** V1/V2 截图不要展示 V3 才默认隐藏的推理正文。
- **L4** view model schema 补齐 `expanded/visible/truncated/border/transport/budget`，避免 V0 冻结后二次改。
- **L5** 探针卫生：自断言、自动清理、按 message_id 去重；P1/P3 可引用 CLS 已有证据减少用户轮次。

### 过渡期必须消灭的三种半更新
1. 流式 structured → 收尾 legacy 结构跳变；
2. 降级到 patch 车道 → legacy 面板；
3. 卡链旧卡 legacy / 新卡 structured。
修法：双载体 view model + 整卡粒度回退 + per-card engine stamp。

### B 的最小验收清单（建议并入计划 §6）
- V0（0 张）：三键三处同步、schema/token/截图矩阵冻结、before golden、test_units 绿；
- V1（1–2 张，切默认后）：工具行标准图标/加粗/耗时/彩色状态/22px 细节；收尾结构不退化；/stop 黄边结构不退化；
- V2（1 流式+1 终态，需重启）：状态条蓝→绿；面板 header 流式期就是单行摘要；关掉配置后消失；
- V3（1 张 show_reasoning=true 展开）：嵌套推理面板、Result/Error 缩进块；false 时无推理正文只留摘要；
- V4（1–2 张 + 完整门禁）：长回合/无工具/纯推理/超长面板//stop/失败；无空白面板/回退/重复卡/掉纯文本。


## A 完整报告条件（2026-09-20，待 C 表态后合并）

- **C1 操作矩阵替代通用 diff**：官方 batch_update 只有
  `add_elements(insert_before/after)`、`partial_update_element`（整字段替换）、`delete_elements`；
  没有按路径字段级 diff/移动/重排。V0 必须冻结操作矩阵：
  ① 外层面板/工具面板整段替换 `elements`（+header/border）；
  ② answer/推理正文走 `card_element.content` 逐字写；
  ③ 新顶层 segment 用 `add_elements` 插在稳定锚点前；
  ④ loading 等固定元素删除；禁止顶层 tag/text_size 进 partial。
- **C2/C3/C7**：同意 B 的 H1/H2/H3；V1 必须覆盖收尾/stop/卡链 seed/DEGRADE；预算按 CLS/FC
  口径重算；继承 plan-v1:107-127 的递归 200 口径、运行时 create 计入、300315 内层 300305、
  单 sequence 共用、整卡替换关流式、DEGRADE 同卡 patch 可行。
- **C4 回退语义**：结构化实体卡不能中途换回 legacy markdown；唯一同卡回退是 DEGRADE 整卡 patch
  （清 card_id、后续帧走 patch）；renderer 异常要有熔断与本回合降级测试。
- **C5/C7 默认切换**：反对 V1 末就切默认；建议默认保持 legacy 到 V3 完成、预算/回退/收尾门禁通过；
  V1 用 `visual_engine=structured` 显式 canary。
- **C6 变异策略**：≤20 条 smoke 不能替代「每条新断言至少红一次 + merge/rebase 全量」；
  `check_cardview` 必须接入 mutate_check gate 集；分片能力要先实现或明确等价方案。
- **F3 嵌套面板先例**：CLS/FC 流式期都是顶层面板，FC 嵌套只在完成卡一次性构建；
  AP 全程单面板 + div 轮标题。建议 V3 默认 B；A 作为 P3 双路径通过后的增强。
- **F10/F11/F12/F13/F14/F16/F18**：reasoning 摘要依赖 Hermes 配置；header 需要 config.locales 且
  终态路径也要有 header；sequence/uuid/id 生命周期要冻结；Running 行需要 pre_tool_call；
  Result/Error 要 cap+围栏+脱敏；panel 状态需扩展 timeline；§2 token 表补图标/border/expanded/
  loading 锚点。
- **F15 冻结记录**：freeze JSON 必须包含 HEAD tree SHA + `git status --porcelain` + 引用文件哈希；
  计划改动先 commit 或归档，保证审计对象可唯一重建。
- **F17**：AP 没有卡片级 header；计划「三家一致」表述要改成「CLS/FC 有，AP 无，我们按用户决策新增」。

## A/B 分歧与初步裁决建议（待 C 表态）

| 分歧 | A 最终 | B | 建议裁决 |
|---|---|---|---|
| V1 末是否默认切 structured | 反对；默认保持 legacy 到 V3 门禁通过，V4 才作为发布动作切；V1 只配置 canary | 建议 V1 截图前切默认 | 以 A 的安全顺序为准：V1 引擎+预算+回退+收尾门禁通过后，先用 `visual_engine=structured` canary 真机 1 回合；V2 验收窗口切默认并截图；V4 删 legacy |
| 推理 A 形态流式期 | 无先例；建议默认 B（AP 式），A 作为 P3 通过后的增强；P3 首选 AP 已证的 partial_update 替换外层面板 elements，add_elements/card_element.create 两条 nested target 未证 | 用户已选 A；要求真机眼睛判 | 目标仍 A：流式期用 CLS/FC 先例的顶层推理面板；收尾/完成卡重建为 A 嵌套面板；P3 三路对比 + 人眼，只决定流式期是否也嵌套；未过不阻塞 A 的终态形态 |
| H1 四条整卡路径 | finalize/stop/卡链 seed 必须 V1 结构化；**DEGRADE 按定义保留 legacy**，但要标记回合已降级并测试 | 四条都结构化 | 采纳 A：三条结构化 + DEGRADE 明确 legacy 回退车道 + engine stamp；V1 golden 含终态/停止态/降级态 |
| 变异策略 | 每条新断言至少红一次 + merge/rebase 全量；check_cardview 接入 gate 集 | 同意门禁风险，但接受用户不想跑长任务 | 定向红一次必做；merge/rebase 全量改为后台分片，不阻塞；发布候选全量留证；分片能力先实现 |
| V1 截图/重启 | V1 至少流式+终态；默认切换推迟 | V1 1–2 张，切默认后 | 合并为：V1 内部 golden；V2 统一一次重启窗口看流式+终态；V3 展开面板；V4 最终 |
| 嵌套面板 API | partial_update 替换外层面板 elements 首选；add_elements/card_element.create nested 未证 | 计划 P3 写法不一致 | V0 冻结 P3 三路探针；API code=0 只作必要条件，落点必须人眼判 |
| M9 正文字号 | 反对探针前写死；V0 真机探针 normal vs normal_v2，保持现有 normal+profile 直到确认 | 同样要求探针 | 一致：V0 探针后写回 token；结构化构建后统一走 apply_text_profile |


## C 放行条件（C1–C10）与父级建议裁决

### C 条件摘要
- **C1** 按 H1–H4/M1–M9 修订计划 → 重新冻结 sha → 重审修订项。
- **C2** tree-SHA 冻结 manifest + 不可变导出 + 树变作废/收敛规则。
- **C3** 显式门禁清单（含 `probe_render`/`check_own_body`）；`check_cardview` 接入
  `mutate_check._run_gates`；smoke 用机器可读 inventory 覆盖高风险族；V1/V3 网关重启前
  跑全量改动面。
- **C4** V3 前置 = plan-v1 六条 + 运行时元素墙 + create 滑窗记账，全部变异验红。
- **C5** 定义并 fault-injection 验证运行时回退；V4 删 legacy 前回退必须可替代。
- **C6** live 默认 legacy 至 V4 或独立部署 worktree；写清重启/回滚命令。
- **C7** 分支策略 + push 白名单 + tag tree 绑定 + 全量 expected-kill。
- **C8** 元素/字节 worst-case、trim、卡链触发、result 截断脱敏门禁。
- **C9** 状态色单映射 + 双载体矩阵 + 三配置键矩阵 + M9 真机探针。
- **C10** 心跳生命周期 + M1 提交点顺序门禁。

### 父级建议裁决（待 A/B/C 回复确认）

| 争议 | 建议统一结论 | 依据 |
|---|---|---|
| live 默认切换 | **live 默认保持 legacy 到 V4**；V1/V2/V3 只用显式 `visual_engine=structured` canary + 用户同意；V4 全量门禁通过后切默认并删 legacy | C 的软链/任意重启证据 + A 的安全顺序；B 的 V1 切换无反驳证据 |
| 用户截图窗口 | 压缩为 **3 次确认窗口**：① V1+V2 canary（流式+终态）② V3 canary（展开 A 形态）③ V4 最终；每次重启前跑改动面变异分片 | A/B/C 都要求口径冻结；用户要求少测试 |
| 嵌套面板 API | V3 默认：流式用已证 `card_element.create`/顶层面板，完成卡整卡重建 A 嵌套面板；P3 三路对比（create / partial_update 外层面板 elements / add_elements），眼睛判落点；失败先 B（AP 式）再迭代 A | C 的 create 真机证据 + A 的 AP partial_update 证据 + B 的无流式先例 |
| DEGRADE | 三条整卡路径（finalize/stop/卡链 seed）结构化；**DEGRADE 作为唯一同卡回退车道**，渲染最小 fallback 卡（answer+状态色，不是旧 markdown 面板），标记 engine stamp；fault-injection 门禁 | A 的 DEGRADE 定义 + C 的运行时回退要求；V4 删 legacy 后 fallback 仍存在 |
| 变异门禁 | 开发期 `run_fast`（3.65s）；每条新断言定向红一次；V1/V3 重启前跑改动面 346 锚点后台分片；发布候选全量 393+7 分片 + expected-kill | C 的反假绿 + 用户速度要求；A/B 同意 |
| Git | 从 main 开 `v0.7.1-visual` 分支做阶段 commit；main 保持已验证 release；里程碑 fast-forward main 后 push + annotated tag（带 tree sha/expected-kill） | C 的 push 泄漏证据；A/B 无反对 |
| 冻结 manifest | 强制：`git stash create`/`write-tree` 得 TREE，`git archive` 导出不可变副本；manifest 记 TREE/HEAD/plan sha/dirty diff sha/解释器/门禁日志 sha；TREE 变化 ⇒ 审计作废重跑；收敛 = 3 份同结论且条件闭环 | C 的可复现证据 + A 的 §8.2 要求 |
| V0 配置键 | 三键同 commit 进 `_DEFAULTS`+plugin.yaml+README+AGENTS+CHANGELOG+变异锚点；生产必须读键，两取值驱动真实帧断言 card JSON 不同；未实现前显式设置打 WARNING | C 的静默吞配置反例 + A/B 的同步要求 |
| body 字号 | V0 真机探针 `normal` vs `normal_v2`；探针前保持 `normal` + `apply_text_profile`；确认后写回 token，结构化构建统一走 profile | A/C 的“不能先写死” |
| 预算 | 递归序列化 JSON 实数；CLS 工具估算 + FC answer 膨胀 + 运行时增删记账 + 300315 内层码；元素/字节双墙；默认上限由预算反推；trim 最早 + 折叠提示 | A/B/C 一致 |

