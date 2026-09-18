# v0.7.0 Phase 1 / 1.5 执行审计结论（A/B/C + 父级裁决）

- 基线：`29ecc83` (v0.6.4)
- 计划统一结论：`plan-consensus.md`
- 审计对象：Phase 1 own 双路径实现（默认后来切 own）
- 审计结论：A/B/C 首轮均为 **NO-GO / GO-WITH-CONDITIONS**；阻断项全部指向同一批问题。
- 父级裁决：不重开大规模变异；按「修复→冻结→快速门禁」推进，真机一次截图做最终确认。

## 1. 三方封锁项与修复状态

| 编号 | 审计发现 | 状态 | 证据/测试 |
|---|---|---|---|
| HIGH-1 | native 中途帧失败后 core `_send_or_edit → _first_send(composed)` 仍会把合成进度/密钥写进正文 | 已修 | wrapper 对**任意** non-finalize 失败帧开 `_ld_seed_failures` 窗口；`send()` own 模式只渲 own/占位；`test_own_seed_failure_guard...`、`test_send_stream_frame_records_failed_frame...` 含 fallback send 断言 |
| HIGH-2 | F4 守卫未按 body_source 门控，legacy 失败路径从 1 张脏卡变 2 张 | 已修 | `_ld_stream_frame` F4 分支要求 `body_source=="own"`；`test_legacy_f4_path_keeps_old_in_place_finalize_behavior` |
| HIGH-3 | 主帧路径未做 session/turn 漂移校验；turn 两套命名空间不可比；split 丢 session | 已修 | 新增正文桶 `gen` 世代；seed 存 `session_id/answer_gen`；`_ld_body_text` 接 `stream_state`；`_ld_ck_split` 用 `{**state}` 保真；`begin_turn/note_turn` 换回合清旧答案桶；测试覆盖 session 漂移、同会话新回合、空 stored、split 字段 |
| MEDIUM-4 | finalize 按旧 `ck_offset` 切片，core 权威终稿变短时切空丢尾 | 已修 | own 模式 `tail_offset > len(display)` 回退整段；生产接线测试把 `ck_offset` 设 999 后断言终稿仍写入 |
| M8 | `/stop` 生产调用点变异存活（测试只直调 helper） | 已修 | 新增 `test_stop_production_path_reads_last_rendered_body_not_raw_frame` 驱动 `_ld_redraw_stopped_keys` |
| M10/M11 | production 调用点整体退回 legacy / finalize 改 own 优先仍全绿 | 已修 | 新增 `test_own_production_stream_path_never_renders_core_progress_and_finalize_uses_core` 走真实 `send_stream_frame` seed→interim→finalize 字节断言 |
| M13 | require_binding 忽略 TTL 仍全绿 | 已修 | 新增 TTL 过期测试 |
| X1/X3 | on_stream_end 漏挂/回调被换 lambda 门禁抓不住 | 已修 | on_stream_end 并入 `SUBSCRIPTIONS` + `compat.OBSERVED_HOOKS`；check_hooks 用真实 `invoke_hook` 派发并断言快照落账 |
| 证据不一致 | phase-1 旧日志/SHA 与工作树对不上 | 已处理 | 旧证据废弃；新证据写入 `phase-1.5/`，记录源码 SHA |

## 2. P1.5 默认切换

- `core/adapter.py::_DEFAULTS["body_source"]`、`plugin.yaml`、README 默认已改为 **own**。
- `legacy` 只作过渡回退；`test_units.py` 的旧用例由 harness 在每条执行前显式设回 legacy，
  own 专属用例自行 `configure(body_source="own")`。P2b 删除 legacy 后同步重写/归档旧用例。

## 3. 本轮快速门禁证据（默认 own）

见 `docs/audits/v0.7.0/phase-1.5/`：

- `test_units.log`：241/241 passed
- `check_override.log`：OVERRIDE OK
- `check_hooks.log`：HOOKS OK（含 on_stream_end 派发落账）
- `check_clarify_e2e.log`：CLARIFY E2E OK
- `check_own_body.log`：OWN BODY GATE OK
- `source-sha.txt`：冻结源码 SHA
- `summary.txt`：退出码汇总

未跑（按用户要求停止扩大变异/审计）：393+7 全量变异、7 条 C 变异重放、真机。

## 4. 未验证与发布条件

- 真机：CardKit 打字机、颜色/字号、F4 真实频率、on_stream_end 真实先后、长卡链 `/stop`。
- 全量变异与归档跑尚未执行；P2b 删除启发式前必须补。
- 发布前需：Mac 网关重启加载 own；用户一次桌面截图确认；随后提交/tag v0.7.0。
