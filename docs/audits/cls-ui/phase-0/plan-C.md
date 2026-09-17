# Phase 0 · 计划审计 C（执行风险与反假绿）

- Agent：`b272fc5a-38dd-495a-a088-151472ccb57b`（独立子代理，只读）
- 原始结论：**GO-WITH-CHANGES**
- 独立复跑（venv）：`test_units 223/223`、`OVERRIDE OK`、`HOOKS OK`、`CLARIFY E2E OK`、
  `--preflight 366/366（358+8）`、`-k CLS 9/9 🔴`；HEAD `ac3b25e`，无 CI workflow。
- 审计过程发现 plan 在审计中从 135 行被改到 150 行 ⇒ 冻结机制缺失的实证。

## Findings

| ID | 严重度 | finding | 证据 | 处置 |
|---|---|---|---|---|
| P-C1 | 高 | 冻结不可重建、自指（plan 不在 diff；写 verify-log 会改冻结 diff） | plan L33-46/L63 | commit-tree + refs；base/tree/plan/diff 四项 SHA；变更即作废 |
| P-C2 | 高 | 三代理审计无输入/报告/裁决机械定义；无 manifest、agent/model/时间/report sha | 计划原审计规则 | `docs/audits/cls-ui/phase-N/{manifest.json,A.md,B.md,C.md}`；中立裁决规则 |
| P-C3 | 高 | Phase 4 无 Phase 末审计 | 计划 L111-122 | 补 3 路发布前审计：版本/Release、真机证据、回退 |
| P-C4 | 高 | 全量留档规则不足且与 verify-log 冲突（/tmp vs durable） | 计划 L88-90 | 日志工作副本 /tmp + 持久复制进 `docs/audits/.../logs` + SHA256；完整计数/对账 |
| P-C5 | 高 | 退出条件可能“强行推进/未验写完成”；无 findings 台账/owner/期限 | 计划 L60-63/L80-96/L121-122 | F-id 台账；high 未闭环不推进；残余必须进 Release 已知边界 |
| P-C6 | 中 | 影响文件清单/行为声明无产物路径 | 计划 L50-53 | `docs/audits/.../impact.md`（本次以 plan 的 Phase 1 findings 表替代） |
| P-C7 | 中 | 文档数字 219/361/4 与实测 223/366/9 冲突 | CHANGELOG:29-31、verify-log:45 | 冻结后统一回填，旧行标 superseded |
| P-C8 | 中 | 358+8 硬编码，无防锚点静默删除机制 | 计划 L88 | preflight expected vs actual 对账；349→N 增删清单 |
| P-C9 | 中 | 无 CI 事实未说明；Release 状态术语错；提交切分未定义 | 计划 L100-103 | 明写“无 CI，四门禁+全量变异是等价物”；tag/push/gh 命令链；逻辑切片提交 |
| P-C10 | 中 | 全量 run 无“前后 tree SHA 不变”判据 | 计划 L87-90 | 运行前后 tree SHA；变化作废重跑 |

## 第二轮讨论结论

- D1 坚持 c：Phase 1 不夹带 panel header 动态写；a 登记为未来 Phase C 候选，进入条件为
  “用户明确要求折叠态实时 + API 探针通过 + 独立审计通过”。
- D2 改投 i：ii 无法证明可在 markdown 元素上按客户端语言生效；英文动作词 + 边界登记。
- D3 同意：探针协议与三类降级先于发布；未验前全部 pending。
- D4 条件式 GO：amendments 1-8 回写、含 plan 的 commit-tree 冻结、报告/manifest/裁决落盘、
  无 high 未闭环；任一未满足则 NO-GO。
