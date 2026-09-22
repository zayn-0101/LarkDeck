# v0.7.3 对抗审计与全量变异收口

冻结提交：`65c1c5f`（P1 最终提交；前序 `095826a` 为第一批落地）。

## P0 规划复核（Design D 定版前）

- D1 `d3e888d0`：VS16 `♻️ Gateway` 漏网、`/stop` 过滤无行为断言、C3-11/12 声称的永久变异不存在。
- D2 `5bb9d5af`：通知前缀漏项（后台任务/`[IMPORTANT:`/Goal/compression 等）、探针缺 `code!=0` 非零退出与 N1/N2、文档需写已知限制。
- D3 `6eab7167`：`/stop` 过滤真假绿、`edit_message` `default_status` 冗余、计划残留 Design C、release 脚本缺失、P3 未跑。

## P1 落地后复核（commit `65c1c5f`）

- A 路（技术正确性，deepseek-flash）：
  - **高** `edit_message()` 硬编码 `turn_card=True`，上游心跳会把非回合卡重新装饰 ⇒ 已按 tracked `turn_card` 分支渲染；
    新增 `test_v073_edit_message_respects_non_turn_tracking` + 变异 `V073-2z`。
  - **中** 非回合 `send()` 清掉同 chat 真回合卡的 `last_text`，turn→notice→/stop 找不到卡 ⇒ 已只对回合卡清；
    新增 `test_v073_stop_redraw_survives_later_notice` + 变异 `V073-2aa`。
  - **中** 负清单漏 `gateway/run_busy.py` 六条忙碌提示 ⇒ 已登记 `⏩/↪/⏳ Subagent working/⏳ Compressing context/⏳ Queued/⚡ Interrupting`；
    测试逐条断言；fenced 更新块故意不登记（防误杀代码块开头的真终稿），写 README 已知限制。
  - **中** `state = {**state, "status": status}` 的测试无判别力 ⇒ 删除隐藏注入，footer 元素写/degrade 显式传 status；
    `test_v073_structured_finalize_injects_frame_status` 增加 footer 元素写断言 + 变异 `V073-2y`。
  - **中** legacy element-channel footer 未注入 `frame_status` ⇒ 已补。
- B 路（用户可见/证据，glm-5.3-flash）：
  - **高** release notes 指向不存在的 verify-log 宿主矩阵证据 ⇒ 探针已发 11 张卡（全 `code=0`）并写 `docs/verify-log.md`；
    真机目视待用户回话，未回话前不写成验证通过。
  - **高** 文档把「未过宿主退回 notation」写得像自动回退 ⇒ README/plugin.yaml/cards 注释改为条件式：无运行时自动回退，
    某 host 被拒/不变小必须改代码并重跑全量。
  - **中** 「Error/Result 块」表述过宽（生产仅 Error 分支可达）⇒ 后续文档按「Error（Result 分支共用元素）」收紧。
- C 路（流程/诚实性/发布，qwen3.8-flash）：
  - release 脚本 8/8 要求已实现；`.deploy` dirty 只 log 不 fail 的历史缺口已改为 `--check` 也硬失败；补 CHANGELOG 收口断言。
  - 计划旧计数（514/526/5、523/511/12）会在提交 D 收口到 523/535/24；AGENTS/CHANGELOG 过期标注同步修。

## P3 全量变异（6 分片）

- 冻结提交 `65c1c5f`；6 分片独立账本/日志，`--update-ledger`，坏 0（无 💥/🟢/❓）；
- 合并 `tools/merge_ledger4.py --write`：**523/523 red-assert**、`full_audit_at=65c1c5f`、
  `full_audit_tree=779d67eb05492809db1946e51e03097cac57e22f`（== `65c1c5f^{tree}`）、`tree_dirty=false`、`n_inh=0`；
- **实测墙钟 886.1s**（02:36:17→02:51:09；已写回计划 §6.15）；证据 `~/.larkdeck-scratch/v0.7.3/full-run-evidence.json`。
