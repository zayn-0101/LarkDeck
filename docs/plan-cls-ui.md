# CLS 观感改造 — 分阶段实施计划（2026-09-17）

> 本文是 `/goal` 模式下的唯一计划源。**计划本身先过 3 个独立子代理审计**；
> 执行阶段每结束一个 Phase 也各过 3 个独立子代理对抗审计；有分歧必须由相关代理
> 互相讨论到统一结论，结论写回本文件后才进入下一 Phase。代码未过对应 Phase 审计前
> 不提交、不推送、不发布。

## 目标

把默认卡片的过程面板 / 工具行 / footer 向 `Cheerwhy/hermes-lark-streaming`（CLS）
的用户可见观感看齐，但**不照抄机制**：

1. 面板摘要 = `💭 思考 {耗时} · 🛠️ 工具执行 · {n} 步`；思考与工具用不同 emoji；
2. 工具行 = 图标 + 加粗动作名 + 耗时 + 带颜色的状态词（绿 `Succeeded` /
   青绿 `Running` / 红 `Failed`·`Blocked`），命令或 skill 名另起一行灰色小字；
   > ⚠️ **2026-09-22（v0.7.2 面板 UX 批次）已取代**：运行中改**蓝色 `Running`**、成功改绿色
   > **`✓`**（其余状态词不变）；页脚也去掉了段前缀 emoji。本文件其余部分仍是 CLS 观感的
   > 设计记录；现行口径以 `docs/plan-v0.7.2-panel-ux.md` §0 与代码为准。
3. footer = 状态 → 耗时 → 模型 → 上下文用量 + 本卡短码；
4. 不因坏 hook 数据、未知工具状态、元素预算或标签不被客户端识别而丢整卡；
5. 保持不变量：不改 Hermes 源码、不 monkeypatch、卡片失败 fail-open、
   流式中间帧严格前缀链、私有名只在 `core/compat.py`。

## 当前状态（诚实登记）

- 本计划制定前已有一版**快速实现草稿**（工作树未提交）：CLS 观感主体、测试、golden
  trace、check_hooks 与变异锚点已改；四门禁曾跑到 `test_units 223/223` +
  三个 `check_*` 全绿。
- 已收到一轮单子代理审计，其中 H1（`status="cancelled"` 解包崩溃）及 M1/M2/M3/M5/M6、
  L3 已修；这些修复仍需 Phase 1 的三路对抗审计独立复核。
- **本计划的协议缺口**：没有在“计划后、执行前”先过 3 路计划审计。Phase 0 就是补这一步，
  并把已存在的草稿降级为“待审计输入”，不视为已通过。

---

## 冻结基线（Phase 0 审计输入）

- 基线 commit：`ac3b25e7d1127c2e3968cfa3ef72404067064b9b`（v0.5.0）
- 工作树 diff 快照：`sha256 = daf6600a026ffdc6277d6ba1f9e23aee8696b224d550a6cf4f82503cceaf8b02`
  （算法：`git diff --binary | shasum -a 256`；不含未跟踪的 `docs/plan-cls-ui.md`）
- 变更文件：AGENTS.md、CHANGELOG.md、README.md、core/adapter.py、core/cards.py、
  core/i18n.py、docs/plugins-compare.md、docs/verify-log.md、plugin.yaml、
  tests/check_hooks.py、tests/golden_cardkit_trace.json、tests/mutate_check.py、
  tests/probe_render.py、tests/test_units.py
- 当前门禁（草稿态）：`test_units 223/223`、`OVERRIDE OK`、`HOOKS OK`、
  `CLARIFY E2E OK`；定向 `CLS-1..CLS-9` 九条变异全被抓住（`/tmp/mutCLS2.log`）。
  这些只说明草稿自洽，**不代替** Phase 0/1/2 的审计结论。
- 审计冻结纪律：计划审计期间不再改代码/测试/golden；只允许补审计输入或文档中的
  `pending` 更正。任何代码改动都会使上述哈希失效并必须在报告里重新冻结。

## Phase 0 收敛记录（v2，D1 待中立裁决）

> 三份计划审计已返回，第一轮投票与第二轮讨论记录如下；**D1 仍有 2:1 分歧，
> 已交第 4 位中立代理按证据裁决**。D2/D3 已三方一致。

**解释器与冻结**
- 门禁解释器写死：`/Users/Zayn/.hermes/hermes-agent/venv/bin/python3`（3.11.15）。
  其他解释器（pyenv 3.14）实测 `test_units 210/223`，**不得**进入门禁/CHANGELOG/verify-log。
- 冻结方式：临时 index + `git commit-tree` 建 `refs/audit/cls-ui/phase0`，包含 plan；
  记录 base/tree/plan/diff 四项 SHA；审计期间任何变更（含 plan）使快照作废并重跑。
- 当前草稿实跑（仅作 v2 冻结输入）：venv 四门禁全绿（`test_units 223/223`）、
  `--preflight 366/366（358+8）`、`-k CLS 9/9 🔴`。最终数字以 v2 冻结点重跑为准。

**D2（工具动作词 i18n）— 三方最终一致：英文 CLS 风格动作词 + 边界登记**
- `_TOOL_LABELS` 改为英文动作词（`Search` / `Read file` / `Run command` / `Load skill` …，
  认不出用原始工具名），与状态词 `Succeeded/Running/Failed` 一致；
- 工具行是裸 `markdown.element.content`，**不承载 i18n_content**，因此不假装双语；
  README/AGENTS/Release 明写“工具行动作词/状态词不随客户端语言切换”，完整提示句仍走 i18n；
- 新增/修改测试与变异钉住动作词表；中文动作词需求登记为未来 Phase C（拆 plain_text 元素）。

**D3（`<font color>` 真机风险）— 三方一致**
- Phase 1 做一次 `markdown` vs `lark_md` 字体色探针（状态词/灰色标题/灰色细节三类同卡）；
- 三类消费者各写降级（无色状态词+emoji / 普通小标题 / `↳` 无灰色），降级也要过门禁；
- 拿不到真机证据前，README/CHANGELOG/Release notes 相关项一律 `pending`，禁止写已达成。

**D1（流式折叠态摘要）— 最终转 c（探针接口 PASS，但不满足门控；实时更新列未来 Phase C）**
- 已发探针（`docs/audits/cls-ui/phase-1/probe-header.md`）：create/send/content/batch/content
  全 `code=0`，但审计认定协议不完整（A2 使用硬编码 plain_text、非生产 i18n 节点；单卡；
  未验证二次更新与收起展开；视觉未确认）⇒ 按裁决“不能确认即 fail”，**Phase 1 转 c**。
- 生产不写 panel header 代码；默认 CardKit 流式折叠态在收尾前只有「执行详情」，
  收尾整卡 patch 后显示 `💭 思考 Xs · 🛠️ 工具执行 · N 步`。验收矩阵与 Release notes
  必须按此写 pending/边界，不得把收尾形态写成流式形态。
- 未来 Phase C 候选：先做协议完整的头部探针（A1/A2 两卡、生产 `_ld_panel_summary` i18n、
  默认 collapsed、二次更新与箭头交互），通过后再实现并入现有装饰 batch 的 header 局部更新。

## Phase 1 — 实现收口（按统一计划改代码）

**范围（含 Phase 0 三份计划审计与三份执行审计的 finding）**
- H1 未知/取消状态安全映射；坏 `duration_ms`/`elapsed_ms` 不炸、不显假值；
- F1 `_tool_detail` 合法 JSON 分支：已知类别未命中键时不得取任意首标量（只给未知类别兜底）；
- F2 `_unescape_json_fragment` 支持 `\uXXXX`（含代理对）或保守保留 `u`，不能吃掉反斜杠；
- F3 `unified_panel` 工具行侧的 `max(1, ...)` 越界缝：剩余额度 ≤0 时不得强塞步骤/提示；
- F5 `_TOOL_STATUS_MARKS` 补 `cancelled`/`timeout`（neutral 不再退成 `•`）；
- 截断 JSON 预览的有界提取正确性（转义顺序、嵌套 key 不误报、绝不倒原文）；
- `unified_panel` 分区标题计入元素额度，保持与 `fit_reply_card` / `_ck_create_wall` 同源；
- 去掉不再使用的 `started` 形参及其调用/注释残留；
- footer 顺序、状态映射与 i18n 边界；`panel.status_error`（❌）必须有独立断言；
- `_rounds_elapsed_ms` 的封顶要有独立判别力（审计 C 实测单值 cap 可被最终 min 掩盖）；
- CLS-5 用例改为捕获 `TypeError` 后抛 `AssertionError`（避免被分类成“崩溃红”）；
- D1 最终转 c：探针接口 `code=0` 但协议不完整、视觉未确认 ⇒ 不写生产 header 代码；
  docs/验收矩阵明确“默认折叠态只有执行详情，摘要要等收尾”，实时更新登记未来 Phase C；
- D2（工具动作词 i18n）三方最终一致：`_TOOL_LABELS` 与 `_tool_label` 的中文兜底全部改
  英文 CLS 风格动作词；工具行动作词/状态词固定英文并作为 i18n 边界登记；完整提示句仍走
  i18n，README/AGENTS/plugin.yaml 不得再声明动作词双语；
- D3 `<font color>` 探针与三类消费者（状态词/灰色标题/灰色细节）的降级方案；
- 新增/修改测试、golden trace、check_hooks、变异清单（每条新断言必须有对应变异）。

**门禁（Phase 内每轮改动都跑）**
- `tests/test_units.py`（直接运行，不用 pytest：存在顺序污染）；
- `tests/check_override.py`、`tests/check_hooks.py`、`tests/check_clarify_e2e.py`；
- `tests/mutate_check.py --preflight` 锚点全可用；定向变异 `-k CLS`/相关集合全红。

**Phase 末审计（3 个独立子代理）**
- A 代码正确性 / B 用户观感与真机风险 / C 测试与反假绿；
- 分歧讨论到统一结论；未达成前不进 Phase 2。
- 通过后按 Conventional Commits 提交本 Phase 的实现与测试（不攒着）。

## Phase 2 — 证据与全量验证

**内容**
- 在 Phase 1 冻结树上跑**全量变异**：数量以 `--preflight` 打印的 expected 为准（当前 370+8（2026-09-17 Phase 1 收口）；
  若 Phase 1 新增则先冻结数字并对账 349→N 的增删）；
- 运行前/后记录 `git rev-parse HEAD^{tree}`；树变化 ⇒ 本轮作废重跑；
- 日志工作副本写 `/tmp/fullrun*.log`（P4 沙箱不允许写 `~/.larkdeck-scratch`），跑完把
  完整日志 + SHA256 复制进 `docs/audits/cls-ui/phase-2/logs/` 持久留档；
- 结论写 `docs/verify-log.md`：tree SHA、命令、解释器、EXIT、耗时、🔴/🟢/⚪/💥/❓、
  preflight 对账、与上一轮的增删清单、日志路径 + SHA256；
- 终判硬条件：EXIT=0、🔴=MUTATIONS、🟢=💥=❓=0、非对照 ⚪=0、对照 ⚪=8；
- 复核 `fit_reply_card` 元素/字节边界与 golden trace 的逐字节输出；
- 文档与 CHANGELOG 的门禁数字只在实测后写。

**Phase 末审计（3 个独立子代理）**
- A 验证证据链 / B 用户可见回归 / C 反假绿与留档完整性；
- 讨论到统一结论；任何 `🟢` / `💥` / `❓` 都要先归零或明确登记残余，才允许进 Phase 3。

## Phase 3 — neat-freak 洁癖收尾

按已加载的 `neat-freak` skill 完整路径执行：
- 建立代码 / 运行态 / 文档 / 规则 / 记忆 / 工作区六面事实矩阵；
- 优先修正 README、AGENTS.md、CHANGELOG、`docs/plugins-compare.md`、
  `docs/verify-log.md` 与 plugin.yaml 的现役说法；
- 清点会话残留与临时产物；删除只在获得明确授权后进行；
- 输出 `pending` / `out-of-scope` / 未消除 warning，不把未验证写成完成。

**Phase 末审计（3 个独立子代理）**
- A 文档与代码一致性 / B 规则与记忆边界 / C 清场授权与残留风险；
- 统一结论后按 `docs(closeout): ...` 提交。

## Phase 4 — 发布与本机生效

- 版本策略：默认为 **0.6.0**（用户可见默认观感变化、无配置键改名）；如 Phase 0/2
  审计给出相反证据，则改由统一结论决定；
- `plugin.yaml` 版本、CHANGELOG 发行段、tag `vX.Y.Z`、GitHub Release
  （概要 / 变更 / 验证 / 已知边界）全部使用规范格式；
- 按阶段提交与推送：不把多个 Phase 攒成一次“大提交”；
- push 后确认远端分支/标签/Release 状态（draft、merged、deployed 分开写）；
- Mac 本机：`~/.hermes/plugins/larkdeck` 软链已指向本仓库，`git pull`/确认 commit 后执行
  `hermes gateway restart`；核对网关日志与 `/larkdeck status` 自检；
- 真机 visual（**发布硬门禁，不允许只写在脚注**）：
  1. 用户在飞书确认 `<font color>` 三类消费者（状态词/灰色标题/灰色细节）真的渲染成色，
     且不是字面标签；颜色对照含 markdown 与 lark_md；
  2. 确认收尾折叠标题是 `💭 思考 Xs · 🛠️ 工具执行 · N 步`、footer 状态在最前；
  3. 确认默认 CardKit 流式折叠态只有「执行详情」（D1 已转 c 的边界）可接受；
  4. 任一颜色项未通过 ⇒ 发布前把 `panel_color_tags` 默认改为 `false`（无色降级已接线）
     并重跑四门禁 + 定向变异；不得在未确认时默认开启 color tags 发布；
  5. 真机证据（截图或日志）写入 `docs/audits/cls-ui/phase-4/`。

## 统一审计规则（每个 Phase 都适用）

1. 3 个独立子代理，角色固定为 A 技术/证据、B 用户效果、C 风险/反假绿；
2. 每个子代理拿到**同一份冻结基线 + 本计划 + 证据包**，不得互相看中间步骤；
3. 报告按 高/中/低 + 文件:行 + 可复现证据；无问题的方面也要明确写；
4. 分歧处理：把对方的原始结论匿名化后发给持不同意见的代理互相反驳；仍不一致时，
   由第 4 个中立代理只根据证据裁决；最终统一结论写回本文件并附裁决理由；
5. 任何 Phase 不得靠“大部分通过”推进；未决项要么修掉，要么降级为明确登记残余。

## 提交与发行规范

- Conventional Commits：`feat(ui): ...` / `fix(ui): ...` / `test: ...` /
  `docs: ...` / `chore(release): ...`；
- 正文讲**为什么**、验证数字、已知边界；不写“杂项/随便改改”；
- 显式 `git add <路径>`，禁止 `git add -A/-u`；
- CHANGELOG 用 Keep a Changelog 风格；Release notes 按“概要 / 用户可见变更 /
  验证 / 边界”撰写；
- 不伪造真机证据：未做的真机项写 `pending`。

## 残余与回退条件

- `<font color>` 在飞书 `markdown` 组件的真机渲染未确认前，只能声明“代码级 JSON
  结构断言 + 真机探针待跑”；仓库没有离线客户端渲染器，不得写“离线渲染已验证”。
  若真机不认，回退到三类消费者的无色降级方案并重新过 Phase 1 审计；
- CardKit 实体卡 panel header 建卡定死 ⇒ 默认折叠流式态在收尾前只有「执行详情」；
  Phase 1 先跑 header `partial_update_element` 真机探针，pass 才实现实时摘要，
  fail/超时/静默忽略即转 c（docs/验收标 pending，实时更新列未来 Phase C）；
- 元素墙当前 `rounds≤50` 生产边界恰好兜住；任何新元素都必须重新对账
  `_PANEL_CHILDREN_ROOM` / `_CARD_FIXED_ELEMENTS` / `_ck_create_wall`。
