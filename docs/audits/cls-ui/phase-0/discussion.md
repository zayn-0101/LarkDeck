# Phase 0 · 三代理讨论记录

## 第一轮（计划审计）

| 审计 | 结论 | 关键主张 |
|---|---|---|
| A `4415e9f8-…` | GO-WITH-CHANGES | 冻结/留档/终判/不变量机械验证；D1 初投 c |
| B `28cd3672-…` | GO-WITH-CHANGES | 验收必须分可见性四格；footer 双语；font 风险；D1 初投 a |
| C `b272fc5a-…` | GO-WITH-CHANGES | commit-tree 冻结、audit manifest、Phase 4 审计、findings 台账；D1 投 c |

## 第二轮（就分歧点互发反驳）

### 解释器冲突（A 报 218/223）
- A 使用默认 pyenv `python3`（3.14.3）→ 218/223；B/C 与冻结要求使用
  `/Users/Zayn/.hermes/hermes-agent/venv/bin/python3`（3.11.15）→ 223/223。
- **统一结论**：门禁解释器写死 venv 绝对路径；其他解释器数字不得进门禁/CHANGELOG/verify-log。
  A 撤回 218/223 作为代码回归的结论。

### D2 工具动作词 i18n
- A/B 初投 ii（双语表+边界登记）；C 投 i（英文 CLS 动作词），理由是 markdown 不承载
  `i18n_content`，ii 是假双语。
- **统一结论**：**i**。`_TOOL_LABELS` 改英文动作词并集中一张表；AGENTS/README/Release 明写
  “工具行动作词/状态词不随客户端语言切换”；完整提示句仍走 i18n。
  未来若要做中文动作词，须拆成带 `i18n_content` 的 plain_text 元素，归 Phase C 候选。

### D3 `<font color>`
- 三方一致：Phase 1 做 markdown vs lark_md 字体色探针；状态词/灰色标题/灰色细节三类各写降级；
  未验前 README/CHANGELOG/Release 全部 pending；降级也过门禁。

### D1 默认 CardKit 流式折叠态摘要
- B 初投 a → 撤回接受 c（无本项目 header 级证据）。
- C 坚持 c（超范围、无 header 级证据、属未来 Phase C）。
- A 初投 c → 改投 a（CLS `segment_helper.py:103-142` 用 `partial_update_element` 改
  `panel.header.title`，与本仓库 `_ld_ck_batch` 同 action 路径；附探针与失败回退约束）。
- 记录：2:1 分歧，按规则交第 4 位中立代理 `1b9bc95b-ff34-48a7-a1f3-791b96fc8938`
  按证据裁决；裁决前不进 Phase 1。
