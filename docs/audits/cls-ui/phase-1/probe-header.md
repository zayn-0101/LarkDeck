# Phase 1 · D1 header 局部更新探针（接口部分已跑）

- 目的：按 Phase 0 第 4 方裁决，验证 CardKit 实体卡流式期间能否通过
  `card.batch_update -> partial_update_element` 更新 `collapsible_panel.header.title`；
  同时对比 `markdown` 与 `lark_md` 元素的 `<font color>` 渲染。
- 脚本：`/tmp/probe_header_partial.py`（一次性探针，未进产品代码）
- 目标会话：用户飞书 DM（`FEISHU_HOME_CHANNEL`，前缀 `oc_f33e3b…`）
- 探针卡内容：
  - panel header：`Header Probe A1 · LarkDeck`
  - panel body：`<font color>` grey / green / turquoise / red 四行（markdown 元素）
  - panel tools：`🛠️ 工具执行 · 1 步` + `⌨️ **Run command** · Succeeded` + `↳ echo probe`
  - 额外 `div`：`lark_md` 的 grey/green 对照
- 执行记录（2026-09-17）：

| 步骤 | 请求 | 返回 |
|---|---|---|
| create | `card.create` | `code=0 success` |
| send | `im.message.create` | `code=0`（message_id 已记录在运行日志） |
| content 1 | `card_element.content(answer)` | `code=0 success` |
| batch | `partial_update_element(panel, header.title=A2)` | `code=0 success` |
| content 2 | `card_element.content(answer)` | `code=0 success` |

- 接口层结论：**路径没有被 300309/300317/300313 关闭**，与 CLS 一致；但按项目纪律，
  `code=0` 只证明飞书收下，**不证明客户端渲染**。
- 待用户肉眼确认（release 阻断项）：
  1. 标题是否从 `Header Probe A1` 变成 `Header Probe A2 · updated`；
  2. 四行 `<font color>` 是否显示为灰/绿/青/红，而不是字面标签；
  3. `lark_md` 对照行是否同样渲染颜色；
  4. 面板箭头是否仍在、能否收起展开（没有静默降级成普通行）；
  5. 第二次 content 写后卡片是否仍正常（没有卡死/回退成文本）。
- 判定：
  - 五项全满足 ⇒ header partial update 可进入生产实现（并入现有装饰 batch，
    不新增 sequence/元素；失败语义不变；补 action JSON/去重/批数/失败处置测试）。
  - 任一失败/歧义/超时 ⇒ 自动转 c：不写 header 生产代码，文档/Release 标 pending，
    实时摘要列为未来 Phase C。
- 当前状态：**接口 PASS；协议不完整 + 视觉未确认 ⇒ 按裁决转 c**。

## 审计复核与最终判定（Phase 1 审计 A/B）

- 审计认定本探针不满足冻结协议：A2 使用硬编码 `plain_text`（不是生产
  `_ld_panel_summary` 的 i18n 节点）、只发一张卡（无 A1 对照）、未验证第二次更新、
  建卡 `expanded=True` 不代表默认折叠态、视觉结论未拿到。
- 依 Phase 0 裁决“任何一步失败/歧义/超时即转 c”：**Phase 1 不写生产 header 代码**，
  默认 CardKit 流式折叠态在收尾前只有「执行详情」；docs/验收/Release 按 pending 写。
- 未来 Phase C 入口：协议完整版探针（A1/A2 两卡、生产 i18n 摘要、默认 collapsed、
  二次更新 + 箭头收起展开）通过后，再实现并入现有装饰 batch 的 header 局部更新。
