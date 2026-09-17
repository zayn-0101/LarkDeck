# Phase 0 · 执行向审计输入（3 路，供 Phase 1 范围）

> 这 3 个代理审计的是“已写草稿的实现”，不是计划；在 Phase 0 中作为输入证据，
> 不计入 Phase 0 计划审计的 3 份报告。Phase 1 结束时会另起 3 路实现审计。

## 执行审计 A（代码正确性，`962242e4-6059-4b99-9808-1e92ae5dfddc`）

- 复核：H1/M1/M3/M6/L3 成立；M2/M5 只修了一部分。
- 新增：
  - **F1** `_tool_detail` 合法 JSON 分支仍取任意首标量（`core/cards.py:763-771`），
    会把 `note`/`terminal_id` 当 path 展示 ⇒ Phase 1 必须让已知类别未命中键直接返回空。
  - **F2** `_unescape_json_fragment` 不认 `\uXXXX` 且未知转义会吃掉反斜杠 ⇒ 支持 `\uXXXX`
    （含代理对）或保守保留 `u`。
  - **F3** `unified_panel` steps 侧 `max(1, ...)` 越界缝（`core/cards.py:1765`），
    public 极端输入可让 `fit_reply_card` 进 no-panel ⇒ Phase 1 修边界。
  - **F4** `_ck_create_wall`（128000/200）与 `fit_reply_card`（40000/194）阈值不同源，
    CardKit/patch 观感分叉；登记为已知设计差异，Phase 2 复核。
  - **F5** neutral `_TOOL_STATUS_MARKS` 缺 `cancelled`/`timeout` ⇒ Phase 1 补。
- Hermes 真实状态集核实：`ok`/`error`/`cancelled`/`blocked`/`timeout`；映射覆盖。

## 执行审计 B（用户观感/真机风险，`91911e45-0104-441e-a80c-3fb58b755848`）

- **高-1** 默认 CardKit 流式折叠态看不到 `💭/🛠️` 摘要与步数；摘要只在收尾 patch 出现。
- **高-2** `_TOOL_LABELS` 中文硬编码违反 `AGENTS.md:157`；英文客户端中英夹生。
- **高-3** `<font color>` 塞进 markdown 未验真机，且 `truncate` 不闭合 font；失败可能赔上整卡。
- **中** 文档过度承诺；验证数字三口径；元素墙手工对账。
- 有意进一步优化：footer 顺序、彩色状态词、有界提取+脱敏、坏数据防崩、双 markdown 减少重发。

## 执行审计 C（测试/反假绿，`ca567acf-de82-47e4-9341-ffd63f2659cc`）

- 实测 venv 四门禁全绿 + preflight 366/366 + `-k CLS 9/9 🔴`。
- **H1** CHANGELOG/verify-log 旧数字（219/361/4）与实测（223/366/9）冲突。
- **H2** CLS-5 是崩溃红而非断言红：`ERROR ` 被 `mutate_check` 当断言标记；Phase 1 需把
  用例改为捕获 `TypeError` 后抛 `AssertionError`。
- **M1** `panel.STATUS_ERROR`（❌ 执行出错）无门禁覆盖（撤掉映射四门禁全绿）⇒ 补 footer 断言。
- **M2** `_rounds_elapsed_ms` 的单值 cap 无独立判别力（最终 min 可掩盖）⇒ 改为单次 cap +
  断言构造。
- **M3** golden/check_hooks 已覆盖绿色 Succeeded/灰色细节/i18n 摘要，但红色 Failed 未进页面路径、
  footer 缺耗时/ctx 组合。
- **L** P2 系列未逐条复跑；无顺序污染证据。
