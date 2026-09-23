# v0.7.4 计划（系统/命令提示静默 + 长任务面板 + 推理面板探针）

> 承接 v0.7.3（已发布：tag `v0.7.3` = `1cc6ecc`）。本文件是下一批的唯一执行依据。
> 老规矩：先出计划 → ≥3 路子代理对抗审计 → 收敛后才动手 → 每阶段结束再过审计 →
> 6 分片全量重验 → 部署真机终验 → 用户终验后发布 → neat-freak 收尾。

## 0. 来源与证据

| 项 | 来源 | 证据 |
| --- | --- | --- |
| P0 Hermes 系统/命令回复仍带 `✅ 已完成` | 用户 2026-09-23 真机截图 | `/reset` mid `om_x100b640c239740bcc2b83b679166f11`；日志 `send 判定 turn=True keys=['notify']`；内容 `✨ 会话已重置！重新开始。…` |
| P1 长任务/多卡中间卡面板空白 | 用户 2026-09-23 反馈 | `docs/audits/v0.7.3/p4-long-task-blank-panel.md`（卡片/消息 id 与日志） |
| P2 `show_reasoning=true` 嵌套面板真机渲染 | 历史挂起项 | `docs/plan-v0.7.3.md` §7；默认 false 无用户可见风险 |

## 1. P0：系统/命令提示静默（本批核心）

### 1.1 事实约束

* 上游 `notify=True` 是**所有最终回复**的通用标记（`gateway/platforms/base.py` 注释
  「Final content gets notify=True」；non-native 真实终稿同标记）⇒ **不能据此判非回合**，
  否则真实回合会丢 `✅ 已完成`。
* 上游命令回复的本地化头来自 `~/.hermes/hermes-agent/locales/{zh,en}.yaml`，例如：
  `reset.header_default/header_new/header_titled`、`resume.list_header`、`reload_mcp.header/
  confirm_prompt/cancelled`、`reload_skills.header/failed`、`stop.stopped/stopped_pending`、
  `reasoning.*` 等。
* 判据仍是「默认回合 + 已知系统/命令前缀负清单」（Design D）；新增前缀必须来自上游
  可枚举字面量，不猜模型输出。

### 1.2 收敛范围（B 审计 + locales 逐条核对后的候选）

* **startswith 前缀表**（全部为上游 header 字面量，中英）：
  - reset：`✨ 会话已重置`、`✨ 新会话已启动`、`✨ Session reset`、`✨ New session started`；
  - reload skills：`🔄 **技能已重新加载**`、`🔄 **Skills Reloaded**`、
    `❌ 技能重新加载失败`、`❌ Skills reload failed`；
  - reload MCP：`🔄 **MCP 服务器已重新加载**`、`🔄 **MCP Servers Reloaded**`、
    `🟡 已取消 /reload-mcp`、`🟡 /reload-mcp cancelled`、
    `⚠️ **确认 /reload-mcp**`、`⚠️ **Confirm /reload-mcp**`、
    `❌ MCP 重新加载失败`、`❌ MCP reload failed`；
  - resume：`📋 **已命名会话**`、`📋 **Named Sessions**`、`⚠️ /resume blocked`；
  - reasoning：`🧠 **推理设置**`、`🧠 **Reasoning Settings**`、
    `🧠 ✓ 推理强度已设置`、`🧠 ✓ Reasoning effort set to`、
    `🧠 ✓ 已清除本会话的推理覆盖`、`🧠 ✓ Session reasoning override cleared`；
  - stop：`⚡ 已停止。`、`⚡ Stopped.`；
  - gateway restart：`♻ 正在重启网关`、`♻ Restarting gateway`；
  - 本插件自诊卡：`🃏 larkdeck v`、`🃏 LarkDeck v`（带版本号，避免用户询问插件时误杀）。
* **整行相等表**（首行 trim 后必须完全相等，避免裸词误杀真实回答）：
  `会话数据库不可用。` / `Session database not available.`、
  `没有可停止的活跃任务。` / `No active task to stop.`、
  `未找到已命名的会话。` / `No named sessions found.`。
  实现为 `_LD_SYSTEM_NOTICE_LINES`（VS16 归一后比首行），不参与 startswith。
* 边界：**不能用 `notify=True` 判非回合**；只按可枚举内容前缀/整行。
* 测试：
  - `test_v073_non_turn_send_has_no_footer_element` 增加**每个**新前缀/整行字面量（无页脚/无面板/无状态头）；
  - **反向矩阵**：对每个新前缀旁加一条普通回答（`notify=True`）必须保留 `✅ 已完成`；
    再直测 `_ld_send_is_turn(chat, "普通回答", {"notify": True}, False) is True`；
  - **行首语义**：前缀出现在第二行不得触发；
  - 既有 `test_v073_footer_turn_scope...` 等回合保真用例不回归。
* 变异（新命名 `V074-*`）：
  - `V074-2ab`：删除 reset 前缀（`✨ 会话已重置`）⇒ test_units 实红；
  - `V074-2ac`：`_ld_send_is_turn` 改成 `return not bool(md.get("notify"))` ⇒ test_units 实红
    （真实回合丢 ✅）；
  - `V074-2ad`：把 `_ld_is_system_notice` 的 startswith 改成 `in`（内嵌也命中）⇒ test_units 实红；
  - `V074-2ae`：删掉整行相等表（纯词命令回复又出 ✅）⇒ test_units 实红。
* 黄金夹具预计不受影响（分类发生在 `send()`，不进 cardkit trace 场景）。

### 1.2a 匹配策略与残余风险（审计 B 2026-09-23 收敛）

* **前缀必须是首行完整 header**（emoji + 具体短语 + 标点），拒绝裸 emoji / 裸词；
  纯词候选（`没有活动任务`、`No active task to stop`、`会话数据库不可用` 等）单列为
  「整行相等」候选，只有整行（trim 后）完全相等才判非回合。
* **已知残余风险（接受并披露）**：真实模型终稿若首行恰好等于/前缀命中已登记 header，
  会被静默（丢 ✅），且 `edit_message` 沿用 send 的 `turn_card` 记账，误伤是终态的；
  运行期靠 `send 判定 turn=` 日志发现，发现即登记/收紧前缀并重跑全量。
* **第三信号调研（不在本批实现，先立项）**：优先评估 slash 命令派发打点
  （`pre_gateway_dispatch` TTL，见 plan-v0.7.3 §6.10.2）与上游 `_mark_notify_metadata`
  能否把 `notify=True` 区分为 command/final 两类；若能，替代 locales 内容枚举。

### 1.3 旁路测试点（审计 B）

* native `send_stream_frame` finalize：命令文本若走 native 帧，前缀判断不参与 —— 先钉
  「当前行为 + 是否需要上游/配置层不投 native」的结论；
* `edit_message(finalize=True)`：手工 `_ld_track` 后喂命令文本，钉住 `turn_card` 继承
  导致的静默/出页脚预期；
* standalone sender（cron/无网关投递）：钉住新前缀内容是静默还是保页脚。

### 1.4 审计清单（≥3 路）

* A：前缀完整性 vs 误杀（逐条对照上游 locales；提出最终字面量清单与排除项）。
* B：真实回合保真（`notify=True` 语义、non-native 终稿、`/stop` 回落、反转测试/变异判别力）。
* C：流程与证据链（版本 0.7.4、全量重验、release/账本、P1/P2 优先级与可行性）。

### 1.5 真机验收

* `.deploy` 指被测提交 + 网关自检通过后，用户在飞书依次发：`/reset`、`/new`、一条真实普通消息；
* 验收三点：命令回复无 `✅ 已完成`/面板/状态头；真实回合页脚仍 `✅ 已完成`；Error/细节字号不回归。

### 1.3 待办

* [ ] 审计 A/B/C（≥3 路：前缀完整性/误杀风险、真实回合保真、测试判别力）
* [ ] 实现 + 定向 `-k V073` 实红 + run_fast
* [ ] 6 分片全量重验（新章 full_audit_at 指向本批冻结提交）
* [ ] 部署 `.deploy` + 网关自检 + 用户真机发 `/reset`、`/new`、真实消息三类验证

## 2. P1：长任务/多卡中间卡面板空白

* 复现条件与日志见 `docs/audits/v0.7.3/p4-long-task-blank-panel.md`。
* 代码事实：`_ld_panel(chat, report_empty=True)`（core/adapter.py:2491+）在 `panel.snapshot()`
  为空/无过程数据时仍可能返回空面板，作为回合状态色（绿/红/黄边）载体；长任务原生流回退/
  多回合交错时，终局整卡拿到的快照可能已被后续回合顶掉 ⇒ 展开空白。
* **候选方案 A（优先）**：adapter 层按 `(chat_id, bound_session_id)` 缓存「最后一次非空面板
  快照」；仅当终局帧当前快照无 `tools/rounds/reasoning` 时回退到缓存。需要：
  - 缓存命中条件（同 chat + 同 session、快照非空、时间窗）；
  - `panel.reset()` / `begin_turn` / 会话切换时清缓存，防跨回合陈旧；
  - 缓存不参与 CardKit 帧签名（只影响终局整卡），不改流式中间帧。
* **候选方案 B（兜底）**：终局无过程数据时**不出面板**；状态色改由页脚/其它载体表达——
  需先确认用户能接受「错误/中止回合无红/黄边」。
* 验收：复现脚本/夹具能稳定区分「空面板」与「无面板」；新增断言 + mutation（例如让缓存
  回退失效、让空快照仍渲染空面板）实红；真机长任务卡不再空白。
* **已实现（2026-09-23，最小范围，采纳 B 兜底）**：`_ld_panel` 终局帧（`report_empty=True`）
  若快照无 `tools/rounds/reasoning`，直接返回 `None`（不再渲染空 shell）；非空面板行为不变。
  测试 `test_v074_finalize_empty_panel_is_omitted_not_blank`；变异 `V074-2ah`（空 shell 回归）/
  `V074-2ai`（过度修正吞非空）均 full red-assert；未改 cardview/golden。真机长任务观察在 P4。

## 3. P2：show_reasoning 嵌套面板探针

* **结论（2026-09-23，冻结前）**：探针卡 `om_x100b640d4e092480de298ce9014a9a3` 已发；
  用户确认**内层能展开**（能看到「思考 · 1 · 1.2s」「思考 · 2 · 0.8s」）⇒ 不改 cardview、
  不重生成 golden，`show_reasoning` 默认 `false` 维持。

## 4. 发布与收尾

* 插件版本 `0.7.4`；CHANGELOG 新条目；发布说明 `docs/releases/v0.7.4.md`；
* 沿用 v0.7.3 的证据链纪律：v2 runner（先验片后 dry-run 再 `--write`、不带旧 fa allow-at）、
  release 读 `evidence-<fa>/`、artifact 全量 sha256、脚本 hash、git 锚定证据摘要。
* 清场与注册项按 `docs/plan-v0.7.3.md` §7 与新发现滚动更新。

## 5. v0.7.3 §7「defer v0.7.4」书面承诺复核（2026-09-23）

> 纪律：本表逐项给处置，不把旧 defer 标签原样带到 v0.7.5；关闭项若再被点名则重新立项。

| 遗留项 | v0.7.4 处置 | 理由 / 验收 |
| --- | --- | --- |
| 长任务/多卡中间卡面板空白 | ✅ 本批修复（最小范围） | `_ld_panel` 终局空 shell 守卫；V074-2ah/2ai 实红；真机 P4 观察 |
| `show_reasoning=true` 嵌套面板渲染 | ✅ 本批关闭 | 探针 `om_x100b640d4e092480de298ce9014a9a3` 用户确认内层可展开；不改 cardview/golden |
| 嵌套 `element_id` 单独 `partial_update` 探针 | defer v0.7.5 | 与「嵌套渲染」不同问题；需专门探针与断言，本批不扩面 |
| 表单容器形态（form/submit/form_value） | defer v0.7.5 | 需真机 submit 探针 + clarify E2E，独立批次 |
| 澄清卡 submitted/retry/30min TTL | defer v0.7.5 | 需状态机 + TTL 设计 |
| `panel_color_tags` 对结构化车道死开关 | defer v0.7.5 | 语义/观感决策，需用户拍板 |
| 字段白名单只覆盖面板树 | defer v0.7.5 | 需按官方 2.0 字段表扩展整卡白名单，可能触 cardview/golden |
| `_save_ledger` 丢 `_meta` / 断言内部改动不失效 | defer v0.7.5 | 账本工具深改会放大失效面；本批仍用 6 片 full + merge |
| 低层出站原语无统一留痕 | defer v0.7.5 | 需调用点枚举断言 + 灰度日志设计 |
| `seq += 1` 换号重试只被夹具保护 | defer v0.7.5 | 需构造「删除是这一帧最后一次写」场景 |
| `--shard` 结束打印 | defer v0.7.5 | 低风险增强；本批不动 merge/tools 以保 sha 一致 |
| 账本每条来源凭证（tree hash + 日志路径 + 行号） | defer v0.7.5 | 账本格式升级，属工具专项 |
| 图标②页脚四段图标 | defer v0.7.5 | 用户未拍板；需真机列宽探针 |
| 图标③澄清卡/status/config/降级提示图标 | defer v0.7.5 | 每个新位置需断言+变异+重验 |
| 无界等待/裸 join 全仓扫 | defer v0.7.5 | 需静态门禁设计（现存 3 处） |
| E2 过程/正文交错结构 | defer v0.7.5 | 渲染器结构调整，独立批次 |
