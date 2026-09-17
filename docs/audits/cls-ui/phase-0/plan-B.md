# Phase 0 · 计划审计 B（用户效果与验收口径）

- Agent：`28cd3672-02ef-4d45-a22b-ee2c5cff9393`（独立子代理，只读）
- 原始结论：**GO-WITH-CHANGES**
- 关键实测：默认配置下 CardKit 流式折叠态只有 `执行详情 + footer`；摘要 `💭/🛠️`
  只在收尾整卡 patch 后出现；footer 状态默认只有中文；`<font color>` 与截断风险未真机确认。

## Findings

| ID | 严重度 | finding | 证据 | 处置 |
|---|---|---|---|---|
| P-B1 | 高 | 验收无可见性分层：流式折叠/展开、收尾折叠/展开四格未定义；默认折叠态看不到摘要与步数 | `core/cards.py:1668`、`core/adapter.py` `_ld_ck_create` / finalize 路径 | 计划验收矩阵必须写四格；D1 决定摘要是否实时可见 |
| P-B2 | 高 | footer 状态不是双语：`_ld_status_text` 走 `_i18n.t()` 默认 ZH；英文客户端看中文 | `core/adapter.py:1474`、`core/i18n.py:250` | 若不能做 i18n 节点，登记语言边界；完整句子仍走 i18n |
| P-B3 | 高 | `<font color>` 同时用于状态词/灰色标题/灰色细节，降级方案只覆盖状态词 | `core/cards.py` tool_step/heading | D3：探针 + 三类降级 + 未验前 pending |
| P-B4 | 中 | 长工具行截断可能切坏 `<font>` / `↳` 行 | `truncate`、`panel_tools_markdown` | Phase 1 加极小 `max_tool_result_chars` 边界用例；truncate 或降级处理 |
| P-B5 | 中 | 元素墙压力场景没有用户可见验收 | `fit_reply_card` no-panel 档 | Phase 4 加长推理+满步数+超长正文压力项；确认 panel 存活或 `status_shell` 保色 |
| P-B6 | 中 | 两个耗时口径不同（rounds 求和 vs wall-clock）；短码/status 有出现时点 | `cards._rounds_elapsed_ms`、`adapter._ld_footer` | 定义权威口径；首帧/中途/收尾三时点验收 |
| P-B7 | 中 | 异常状态（Cancelled/Skipped/Timeout）与 0/1/N 工具组合未验收 | `_TOOL_STATUS_STYLES` | 验收矩阵补状态组合 |
| P-B8 | 低 | 计划没有独立用户效果节；文档 ✅ 先于真机 | README/CHANGELOG | 收敛后统一改 pending/实测数字 |

## 第二轮讨论结论

- D1 初投 a，因拿不出本项目 header 级证据而撤回，接受 c（维持 + pending + Phase C 候选），
  同时要求把“默认折叠态摘要要等收尾”写进验收矩阵和 Release 边界。
- D2 撤回 ii，改投 i：markdown 不承载 `i18n_content`，双语表是假双语；英文动作词集中一张表，
  AGENTS/README 明写语言边界。
- D3 同意。
- D4 GO（前提：先实跑后写数 + D1/D2 修正）。
