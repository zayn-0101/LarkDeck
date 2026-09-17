# Phase 0 · 计划审计 A（技术可行性与不变量）

- 审计对象：`docs/plan-cls-ui.md`（起草阶段版本；审计期间 plan 有追加，已按 H1 要求改为
  commit-tree 冻结后才算最终输入）
- Agent：`4415e9f8-28a4-4aa3-87ca-2919f1ef9393`（独立子代理，只读）
- 原始结论：**GO-WITH-CHANGES**
- 证据解释器：项目要求 `/Users/Zayn/.hermes/hermes-agent/venv/bin/python3`
  （3.11.15；审计第一轮误用 pyenv 3.14 得到 218/223，已在第二轮撤回并改以 venv 为准）

## Findings

| ID | 严重度 | finding | 证据 | 处置 |
|---|---|---|---|---|
| P-A1 | 高 | 冻结基线不可重建/自指：只锁 `git diff` 哈希，未跟踪 plan 不在内；写 verify-log 会改变被冻结 diff | plan L33-46/L63；审计期间 plan 135→150 行仍“哈希有效” | 采用 C 提案 1：commit-tree + `refs/audit/cls-ui/phase0`，四项 SHA；变更即作废 |
| P-A2 | 高 | `_ck_create_wall` 只数 CardKit 固定结构；真正元素墙是 `fit_reply_card/count_elements`；unified_panel 与 CardKit 两块路径预算口径不同 | `core/adapter.py:1102`、`core/cards.py` fit/keep/max_steps 路径 | 纳入 Phase 1 F3/预算同源；计划 Phase 2 必须做边界复验 |
| P-A3 | 高 | 全量终判未写死 `⚪`（验证器把非对照 ⚪ 当失败） | `tests/mutate_check.py` 分类逻辑 | 计划已写死：EXIT=0、🔴=MUTATIONS、🟢=💥=❓=0、非对照 ⚪=0、对照 ⚪=8 |
| P-A4 | 高 | 未验证数字（223/358/361）写进计划/CHANGELOG | 仓库三处数字不一致 | 冻结树 venv 实跑后统一回填；旧数字标 superseded |
| P-A5 | 中 | 漏掉草稿中已改行为的范围：`_TOOL_LABELS`、`_shell_summary`、summary dict、`_ld_footer` 归属等 | git diff | 已补入 Phase 1 范围与审计报告 |
| P-A6 | 中 | 不变量无机械验证动作 | 计划原目标段 | 采纳 amendment 8：不变量→命令/断言/变异 ID/证据文件 |
| P-A7 | 中 | `<font color>` 缺降级判据 | README:108 | 采纳 D3：探针 + 三类降级 + 未验前 pending |
| P-A8 | 低 | 解释器、日志易失、Phase C 编号、版本语义 | 计划 L61/L74/L133 | 已写死 venv 绝对路径；日志持久留档；Phase C 另立 |

## 第二轮讨论结论

- 接受“以 venv 冻结实跑为准”，撤回 218/223 作为代码回归结论。
- D2 最终投 i（英文 CLS 动作词）：无法证明 markdown 元素承载 `i18n_content`。
- D1 最终投 a（带真机探针与失败回退），理由：CLS 在 `segment_helper.py:103-142`
  用 `partial_update_element` 改 `panel.header.title`，与本仓库 `_ld_ck_batch` 同 action 路径；
  约束为并入现有装饰 batch、不新增 sequence/元素、失败语义不变、探针失败退回 c。

## 对 D1 的保留

审计 C 坚持 c。按 Phase 0 分歧规则交第 4 位中立代理裁决；裁决前本报告对 a/c 均不作最终结论。
