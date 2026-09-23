# v0.7.5 计划（长任务心跳合卡 + 多余答案卡合并 + seed 面板防闪旧）

> 承接 v0.7.4（已发布：tag `v0.7.4` = `5a68f2a`）。来源：用户 2026-09-23 真机反馈两问：
> ① 长任务出现「两张答案卡 + 中间 `⏳ Working` 心跳卡」；② 追问瞬间先闪上一回合工具步骤再刷新。
> 老规矩：先出计划 → ≥3 路子代理对抗审计 → 收敛后才动手 → 每阶段过审计 → 6 分片全量重验 → 真机终验 → 发布。

## 0. 证据（2026-09-23 实测）

### 0.1 长任务心跳卡与两卡

* 日志（`~/.hermes/logs/agent.log`）：
  - 18:27:58 `send 判定 turn=False keys=['_interim_send']`；
  - 18:27:59 `出站=card mid=om_x100b640ed43e64a8c19bb21cf913d54 前置='⏳ Working — 3 min — iteration 11, …'`；
  - 之后 18:31/18:34/18:37/18:40/18:43 每 3 分钟 `出站=edit` 同一 mid；
  - 18:43:28 `finalize 分叉`、18:43:29 `回合自检 … 卡片=89d110`。
* 上游来源：`gateway/run_turn.py::_run_agent_notify_long_running`，默认每 180s
  （`HERMES_AGENT_NOTIFY_INTERVAL`）发一次；先 `edit_message`，拿不到 message id 时
  `adapter.send(metadata=_interim_metadata(...))` **新建消息**；本插件把它渲染成一张独立静默卡。
* 两张答案卡的旧结论已由 §0.1b 真机消息列表**推翻并勘误**：不是同轮多次 finalize，
  第二张是自动注入的新回合 C；v0.7.4 只修了终局空面板 shell，与本 incident 无关。

### 0.1b 真机消息列表复算（飞书 `im.v1.message.list`，2026-09-23 实测）

同 chat 按时间序（bot 卡 message_id 后缀）：

| 时间 | 消息 | 归属 |
|---|---|---|
| 18:18:16 | 用户「PBD-516 …」 | 回合 A 入站 |
| 18:18:20 | bot 卡 `…b5aea5a0` | A 的 native seed |
| 18:20:53/55 | `finalize 分叉` + `Suppressing normal final send` | A 同卡 edit 收尾，**只一张卡** |
| 18:24:56 | 用户「要做」 | 回合 B 入站 |
| 18:25:00 | bot 卡 `…84a89d110` | B 的 native seed（长任务主卡） |
| 18:27:59 | bot 卡 `…19bb21cf913d54` | B 的首条 `⏳ Working — 3 min` 心跳（本批次要消灭的中间卡） |
| 18:43:29 | `native 流式收尾 … 卡片=89d110` + `Suppressing normal final send` | B 的终稿 edit 回 `…84a89d110`，**同轮未产生第二张 finalize 卡** |
| 18:43:34 | `Watch pattern notification … 4 background processes completed` | 自动入站（不是 B 的 finalize） |
| 18:43:35 | bot 卡 `…09bb21465bf` | 自动入站触发的**独立回合 C** 的答案卡（response=569 chars） |

⇒ 用户看到的「两张答案卡 + 中间 Working」在本条证据里是：
**B 的答案卡 + 心跳卡 + C（后台进程完成通知触发的独立回合）的答案卡**。
**不是**同一个回合多次 finalize 产生的重复卡；把 C 合并/抑制会吞掉后台进程通知的真实回答。
P1 的“同轮多 finalize”仍需另外的证据；没有同轮证据前不允许实现跨回合合并。

### 0.2 追问闪旧工具步骤（插件侧怀疑）

* 新回合 **seed 帧**可能先于 `on_stream_start`/`begin_turn` 到达；seed 建卡时
  `_ld_panel(chat, report_empty=True)` 取到上一回合尚未清空的面板快照（tools/rounds），
  先把旧工具行画出来，随后新回合事件到达才刷新。
* 需要审计确认：`send_stream_frame` seed 分支、`_ld_streams` 回合状态、`panel.begin_turn`
  清空时序，以及最小修法（new seed 不取上一回合快照 / 只允许当前帧回合数据）。

## 1. P0：心跳 Working 合入主卡面板（审计收敛版）

### 1.1 审计结论与设计改动

* A 路审计否决了「`send()` 返回 active 主卡 mid，让上游后续 `edit_message` 走插件」：
  上游 edit 不带 `_interim_send` 元数据（`run_turn.py:3761-3763`），插件只能靠文本猜；
  一旦特判漏掉，通用 `edit_message` 会对主卡做整卡 patch：
  own 漂移时正文被清空、CardKit 流式会话被关闭（300309）、finalize/`/stop` 后的在飞心跳
  会把终态卡翻回 processing；返回主卡 mid 还会被 `_cleanup_msg_ids` 记录（未来实现
  `delete_message` 就会删主卡）。
* **收敛设计：心跳 `send()` 永远返回 `success=True, message_id=""`**。上游拿不到 id，
  下一拍只会继续调 `send()`，**永不进入 edit 路径**；所有效果都由 `send()` 内部完成。
* 有 active structured 主卡时，把 Working 文本写进主卡 **collapsible_panel header title**
  （`<note> · <原摘要>`），不碰 answer；无 active 主卡时复用**一张**专用静默卡（用户目标：
  「无 active 卡时只建一张并复用」），同样不返回 mid。
* 非 Working 的 `_interim_send`（inactivity warning / commentary）保持 v0.7.4 行为：
  仍发静默卡；**绝不**一刀切。

### 1.2 判定与目标选择

* `_LD_WORKING_PREFIX = "⏳ Working — "`（上游 `run_turn.py:3757` 的稳定字面量）+
  `_interim_send is True`；generic 模式文案任意，本批次明确不支持，仍走 v0.7.4 静默卡
  （在 README/发布说明登记为已知限制）。
* `_ld_hb_candidates(chat)`：只从 `_ld_streams` 选 `chat_id==chat`、`engine=="structured"`、
  `engine_stamp!="degraded"`、有 `card_id`/`message_id` 的流；返回 key 列表。
  `len != 1`（0 或多个并发 session）⇒ fail-closed：不写面板、不建卡，返回 suppressed 结果。
* 专用卡表 `_ld_hb_cards: Dict[str, Tuple[str,float]]`（chat → (mid, 创建时刻)），
  有界 + TTL；`_ld_recent_final: Dict[str,float]` 记录最近一次真实终态/`/stop` 时刻，
  心跳落在安静窗口内时抑制，避免 finalize 后在飞心跳补出中间卡。

### 1.3 行为

1. `send()` 在任何 `guarded`/content 改写之前识别人工心跳：
   * 恰一条 active structured 流：
     - 若 panel 可用（`unified_panel=true` 且有 `ck_has_panel`、未 `ck_panel_missing`）：
       在 `_ld_card_lock(key)` 内重读 state，设置 `hb_title`，计算 `panel_partial`（含 header
       标题合并），seq=`_ck_seq(state)+1`，写 `_ld_ck_partial("panel")`；成功回写
       `ck_seq/ck_panel_sig/hb_title` + `_ck_window_note`，失败只记 `ck_panel_missing/dead`
       并 no-op（绝不 `_ld_stream_fail`，绝不整卡 patch，300313 不是车道死法）。
     - panel 不可用/degraded：只抑制 + 限流日志，不写任何卡。
   * 0 条 active 流且不在 `_ld_recent_final` 安静窗口：用 `_ld_hb_cards[chat]` 中已有的
     专用卡整卡 patch 更新内容；没有则建一张静默非回合卡（turn_card=False）并登记。
     **后续每拍复用同一张**，绝不每 180s 新建。
   * 多条 active、helper 异常、卡更新失败：一律返回 suppressed，绝不落回 `super().send`
     或 `_ld_render_card` 新建中间卡。
   * 返回 `SimpleNamespace(success=True, message_id="", error=None)`；`_ld_state` 不新增。
2. 面板状态机（R9）：
   * state 增 `hb_title: str` 与 `ck_has_panel: bool`（seed/切卡时扫 card JSON 的 `element_id=="panel"`；
     现有 `_ck_elems` 只收三个 stream id，不含外层 panel，不能拿它判断）。
   * `_ld_cardview` / 帧路径 / `_ld_heartbeat_tick` 统一 `_ld_apply_hb_title(view, state)`：
     有 `hb_title` 就把 note 合并进 panel header title（i18n 节点逐语言合并）。
   * 结构化帧：`finalize=True` 或 `text != state["last"]`（真实新正文到达）时先清
     `hb_title`；finalize 终卡绝不带 Working。
   * `_ld_ck_split` 的新 state 不继承 `hb_title/ck_panel_sig`（新卡由下一拍心跳重写）。
3. 不新增 `edit_message` 心跳特判：由于上游永远拿不到 mid，正常路径不会调用；
   但为防御旧版本/异常缓存，`edit_message` 对 `finalize=False` 且内容命中
   `_LD_WORKING_PREFIX` 的调用做 **no-op 成功**，绝不回落 `super()`（R2/R3 防线）。
4. 账本/日志口径：心跳面板写**不计入** `context.note_frame_ok`（它记的是消息帧，不是心跳装饰），
   新增 30s 限流日志：候选数、选中 mid 短码、written/unchanged/missing/dead/quiet。
   这一口径写进 README 的「写卡帧数」说明，避免用户以为状态页漏记。

### 1.4 测试/变异

* 硬字面测试：
  1. active 主卡上连续两次心跳 send：无新卡、无 `SUPER.send`、`create` 数不变、
     `_ld_state` 不新增、返回 `success=True/message_id==""`；
  2. 心跳后 panel header 含逐字 Working，`_ld_heartbeat_tick` 返回 `unchanged`（不擦回普通标题）、
     `ck_seq` 严格 +1、answer 元素零写入、无 `message.patch`；
  3. helper 抛异常/`_ld_ck_partial` 失败：无 fallback 新卡、仍返回 suppressed；
  4. `_ld_ck_partial` 得 300313：只标 `ck_panel_missing`，不清 `card_id/ck_seq`、不 note_frame_fail；
  5. finalize / `/stop` 后心跳：零 patch、零元素写、终态卡不被翻回；
  6. 同 chat 两条 stream：两边都零写；
  7. 无 active 流连续两拍：只建一张专用静默卡，第二拍更新同一 mid；
  8. `⚠️ No activity…` + `_interim_send=True`：仍走 v0.7.4 静默卡；
  9. 上游决策循环复刻（`run_turn.py:3761-3775`）：两拍只有两次 send，`_heartbeat_msg_id`
     始终为 None，edit 哨兵零调用；
  10. 真实终稿内容恰好以 `⏳ Working —` 开头但 `finalize=True`/非 `_interim_send`：不被吞。
* 变异：删 Working 分支；返回主卡 mid；心跳走 `_ld_render_card/_ld_update_card`；
  helper 外层无 try/异常落 fallback；候选用 newest-wins 或扫 `_ld_state`；
  不持久化 `hb_title`；finalize 不清 `hb_title`；一刀切抑制所有 `_interim_send`；
  不检查 `ck_has_panel`；专用卡复用表删掉。

## 2. P1：多余答案卡合并/抑制（证据已改写范围）

* 2026-09-23 真机证据（§0.1b）显示：该次「两张答案卡」的第二张是 18:43:34
  `Watch pattern notification`（4 个后台进程完成）触发的**独立回合 C**，不是同一回合的
  第二张 finalize 卡。跨回合合并/抑制会吞掉后台通知的真实回答 ⇒ **禁止实现**。
* 因此本批次 P1 收敛为：
  1. **不做任何跨回合合并/抑制**；release notes/README 写清「不同入站回合各自一张卡；
     自动追问（Watch pattern notification / 后台进程完成）也是独立新回合」；
  2. 新增回归测试锁「新回合必须新建卡，禁止 edit 上一回合终卡」；
  3. 同轮 boundary/fallback 多卡是上游设计（boundary 测试 `test_approval_boundary.py` 佐证），
     插件侧缺同轮身份标记，**转上游接口建议**：`_metadata_for_send` 加 `_stream_turn_id`
     或 `get_stream_message_id` 钩子后再评估；本批次只记录，不实现。
* 判据：真实回答不丢字节、不互相覆盖；任何合并逻辑都必须能区分「同轮 boundary」
  与「新入站回合」，并有用例证明不会跨回合串卡。

## 3. P2：seed 帧面板防闪旧（审计收敛版）

### 3.1 审计结论

* 根因坐实：`GatewayStreamConsumer` 每回合新建、开局先发空 seed（`stream_consumer.py:523-526`），
  而 `on_stream_start → panel.begin_turn` 在 API 调用外包才 enqueue；两条钩子队列无序
  （`plugin_stream_hooks.py:3-5`）⇒ seed 可早于面板清空。结构化 seed 的
  `_ld_cardview()` 无条件读 `_panel.snapshot(chat)`，把上一回合 tools/rounds/reasoning
  画进新实体卡；`on_stream_start` 后首帧签名变化才刷新 ⇒ 用户看到「先闪旧、再刷新」。
  legacy CardKit seed 与 patch seed 同样有旧快照。
* consumer `turn_id` 与 hook agent `turn_id` 是两套命名空间（`panel.py:968-975` 明令禁止 join），
  seed 时刻**无法判定“当前回合归属”**；唯一正确的 fail-closed 语义是：
  **seed 一律只建空面板壳、不画任何过程数据**。
* 审计 B 明确反对在 seed 时刻清 panel 状态（会误清同回合/live 数据、破坏 `/stop` 重绘与
  插件重启恢复），也反对本次引入 per-turn epoch 守卫（收益不确定、会误杀同回合
  clarification/lazy re-seed 的当前面板；P0 后真机仍闪再量时差评估）。
* 不读旧快照 ≠ 摘元素：结构化仍建外层 `panel`；legacy 空串仍建
  `panel + panel_body + panel_tools`；`_ld_ck_split` 第二处建卡保持全量 snapshot（当前回合数据）。

### 3.2 落点

* `_ld_cardview(..., include_process: bool = True)`：False 时 `snap = {}`，不调
  `_panel.snapshot`；标题自然回落到 `panel.title`（执行详情），面板元素照建。
* 结构化 seed：`_ld_cardview(..., include_process=False)`（`_ld_stream_frame_structured_locked`
  state=None 分支）。
* legacy CardKit seed：`panel_text, panel_tools_text = "", ""`（不再调 `_ld_panel_parts`）。
* patch seed：`panel=None`（不调 `_ld_panel`；首帧整卡时再画当前 panel）。
* 其余 live 帧、`/stop` 重绘、`send()`/`edit_message()` 降级路径、`_ld_ck_split` 全不动。
* seed 只读不写：不调 `panel.reset()`、不改任何 panel 状态。

### 3.2b 计划外加固：面板闸门（seed 后、begin_turn 前）

* 审计 B/A 实测：seed 成功即启动 **3s 面板 tick**，而 `on_stream_start→begin_turn` 在首个 API
  调用才 enqueue；真机时序（18:24:57 入站 → 18:25:08 首个 API call）足以让 tick 先读到上一
  回合 snapshot，把旧工具画回新卡。仅修 seed 不够。
* 最小安全修法（已实现）：只在 **tick 与上游心跳合卡** 两条装饰路径上加 `_ld_panel_is_stale`
  闸门；seed 时记 snapshot 的 hook `turn_id` + 时间，同回合 boundary 重开（`_ld_seed_is_reseed`）
  不开闸；turn_id 变化/清空或 TTL 30s fail-open。跳过一拍只损失耗时跳秒，不丢内容。
* **live 帧不做 capture-only 闸门**：审计 B 指出若 `begin_turn` 发生在 seed 之前，capture-only
  会把当前回合面板误判为旧数据、抑制到 TTL；无上游同命名空间 turn marker 前不做。首个 live
  帧与 `on_stream_start` 的毫秒级队列竞态作为**已知限制**登记（tick 的秒级主因已消除）。
* split / DEGRADE 直接读 snapshot 的路径不在闸门内；它们同样只在 live 帧/长首帧竞态下可能
  闪旧，与上一条同一残余，不单独修。

### 3.3 测试/变异

* 测试：
  1. 旧回合注入 `OLD_TOOL_V075`/`OLD_REASONING_V075` + `record_turn_end(ok)`，bind 后发新 seed；
     断言 `calls["entity"][0]` JSON 不含旧工具/旧推理/旧摘要，且走新建卡不是 patch 旧 mid；
  2. seed 后 `panel.begin_turn(新 hook turn)` + `NewTool_V075` + 首帧：最新 panel partial 含新工具、
     不含旧工具；
  3. seed 仍保留 CardKit 面板结构（结构化有外层 `panel`；legacy 有 `panel/panel_body/panel_tools`），
     防止用「整块不建面板」作弊；
  4. seed 前后 `panel.snapshot` 的 turn_id/tools/reasoning 逐字相等（不误清状态）；
  5. `_ld_ck_split` 第二张实体仍含当前工具（不误伤切卡）。
* 变异：M1 结构化 seed `include_process=False→True`；M2 legacy seed 改回 `_ld_panel_parts`；
  M3 patch seed 改回 `_ld_panel`；M4 seed 把 panel 元素摘掉；M5 seed 分支插 `panel.reset()`；
  M7 split 第二张也清空。
  ⚠️ **车道登记**：M2（legacy CardKit seed）/M3（patch seed）是 **rollback/test-forced 车道**
  —— 生产 `_ld_visual_engine()` 恒为 structured（legacy 配置已退役），structured 下
  `native_transport: patch` 也是 no-op；两条只在单测 monkeypatch/`_LD_ENGINE_OVERRIDE` 时可达。
  其余 V075 变异落在生产 structured / 降级 / 防御路径上。
* 黄金夹具：需要重生成（`python3 tests/write_golden_trace.py`）。预期 diff：首张 entity card
  不再含 seed 前的旧工具/旧标题，panel 头为空壳「执行详情」；首次 panel partial/loading_hint
  删除记录移到第一个正文帧；final patch 基本不变。diff 必须逐条写进 commit 说明
  ——「seed 不再画任何过程快照」是显式行为变更。

## 4. 审计收敛记录（2026-09-23）

* A 路（心跳合卡安全）：GO-WITH-CONDITIONS。否决“返回主卡 mid 让上游 edit 走插件”；
  收敛为返回空 mid、只在 `send()` 内合入 active structured panel；无 active 时两张方案
  （抑制 vs 唯一专用卡）中，用户目标明确选“只建一张并复用”，本批次按此实现并文档登记。
* B 路（seed 防闪旧）：P0-only。三个 seed 分支一律空面板壳、不读旧快照、不摘元素、
  不清状态；**不引入全局 per-turn epoch 守卫**，只在 seed 后的 tick/上游心跳两条装饰路径加
  窄闸门（§3.2b）；live 帧抢跑登记为已知限制；黄金夹具按预期重生成。
* C 路（多卡合并）：证据否定 incident 的同轮多 finalize 说法；禁止插件侧跨回合合并；
  只加“新回合必须新建卡”回归测试并转上游接口建议。
* 未决/转上游：上游加 `_stream_turn_id` 或 `get_stream_message_id` 后再评估同轮 fallback
  合卡与 generic 模式心跳；本批次不做。

## 5. 流程与发布

* ✅ ≥3 路对抗审计完成（见 §4）；审计发现的 F1–F10 已修，并补测试/变异。
* ✅ 实现完成（P0 心跳合卡、P2 seed 空壳 + tick/心跳面板闸门；P1 证据否定不做）。
* ✅ 测试：`tests/test_units.py` **331/331**；新增 `test_v075_*` **30 条**；
  `mutate_check -k V075` **完整四门禁模式 38/38 red-assert**；`--preflight` **589/589**
  （变异 577 + 对照 12）；`run_fast.py --full` 8/8；黄金夹具 `--check` 一致。
* ✅ 6 分片全量盖章：冻结 `3dce128`，**577/577 red-assert**、12/12 对照、🟢0/💥0/❓0、
  `tree_dirty=false`、`n_inh=0`、墙钟 **1282.4s**；证据
  `~/.larkdeck-scratch/v0.7.5/evidence-3dce128/`、`docs/audits/v0.7.5/p3-full-run-3dce128.md`。
* ✅ `.deploy` 到 stamp 提交 `7ccffa0` + 网关自检 23:21:31 通过。
* ✅ 用户真机终验：长任务心跳只进主卡面板、追问不闪旧、真实回合 ✅ 不变。
* ✅ 发布 v0.7.5：tag `7ccffa0`、GitHub Release、`.deploy` 指 tag；neat-freak 收尾进行中。
