# Phase 0 三路计划审计 · 收敛结论

- 基线：HEAD `ac3b25e7d1127c2e3968cfa3ef72404067064b9b`（v0.5.0）+ 当前工作树草稿
- 门禁解释器：`/Users/Zayn/.hermes/hermes-agent/venv/bin/python3`（3.11.15）
- 冻结记录：`freeze.json`（tree / snapshot commit / plan sha / diff sha）
- 计划源：`docs/plan-cls-ui.md`

## 审计结论

| 审计 | Agent | 结论 |
|---|---|---|
| 计划 A（技术/不变量） | `4415e9f8-28a4-4aa3-87ca-2919f1ef9393` | GO-WITH-CHANGES |
| 计划 B（用户效果/验收） | `28cd3672-02ef-4d45-a22b-ee2c5cff9393` | GO-WITH-CHANGES |
| 计划 C（流程/反假绿） | `b272fc5a-38dd-495a-a088-151472ccb57b` | GO-WITH-CHANGES |
| 第 4 方 D1 中立裁决 | `1b9bc95b-ff34-48a7-a1f3-791b96fc8938` | D1 = 条件式 a（先探针，失败转 c） |

## D1–D4 统一结论

- **D1**：最终转 c。探针接口 `code=0`，但审计认定协议不完整（硬编码 title、单卡、无二次更新、
  默认 expanded、视觉未确认）⇒ 不写生产 header 代码；实时更新列未来 Phase C 候选。
- **D2**：英文 CLS 风格动作词。`_TOOL_LABELS` 与 `_tool_label` 的中文兜底一起改；
  AGENTS/README/plugin.yaml 明写“工具行动作词/状态词不随客户端语言切换”，
  完整提示句仍走 i18n。
- **D3**：Phase 1 先做 markdown vs lark_md 字体色探针；状态词/灰色标题/灰色细节三类
  各写降级；未验前 README/CHANGELOG/Release 全部 pending。
- **D4**：**Conditional GO** 进入 Phase 1。条件：本文件、manifest、freeze 记录落盘；
  D1 探针门控、D2 兜底整改、D3 探针均纳入 Phase 1；不得预填未验证数字。

## 未闭环 high（Phase 1 必须收口）

| ID | 内容 | 关闭条件 |
|---|---|---|
| H1 | D1 探针协议不完整 + 视觉未确认（已按裁决转 c） | 生产无 header 代码；docs/Release 标 pending，Phase C 候选已登记 |
| H2 | 计划自相矛盾（残留“另开 Phase C，不混入”） | 已回写 D1 裁决；freeze 后重读确认 |
| H3 | D2 兜底未改：`_tool_label` 两处中文 `"工具"` 仍需英文 + 测试/变异 | 代码、测试、变异、边界文档同 commit |
| H4 | 旧文档数字 219/361/4 与实测 223/366/9 冲突 | 冻结树实跑数字回填，旧行标 superseded |

## 冻结时实跑证据（venv）

- `test_units.py` → 223/223，EXIT=0（`/tmp/phase0-units.log`）
- `check_override.py` → OVERRIDE OK，EXIT=0
- `check_hooks.py` → HOOKS OK，EXIT=0
- `check_clarify_e2e.py` → CLARIFY E2E OK，EXIT=0
- `mutate_check.py --preflight` → 366/366（358 变异 + 8 对照），EXIT=0
- 定向 `-k CLS` → 9/9 🔴（`/tmp/mutCLS2.log`）

## 保留异议

- 计划 C 仍保留对 D1 的 c 立场；按规则由第 4 方裁决为条件式 a，探针失败自动回到 c。
- 第 4 方保留对“平台支持 header partial update”的怀疑：CLS 代码/日志只证明请求形状，
  不能证明字段级渲染；`code=0` 可能对应静默忽略。
