# Phase 4 审计统一结论（v0.6.0 发布候选）

日期：2026-09-18
发布版本：`0.6.0`
基础提交：`bdedb9b29141a3aa303c4a8ff7241f2e6005b1da`
最终发布提交 / tree / tag：由 `refs/audit/cls-ui/phase4` annotated tag 记录
（tag 在提交后创建，因为 commit 不能包含自己的最终哈希）。

## 三路发布前审计

| 角色 | 初始裁决 | 已闭环条件 |
| --- | --- | --- |
| A 发布物完整性与证据链 | GO-WITH-CONDITIONS | phase-4 stale `check_override.log` 已在最终工作树重跑归档；新增 `RUN4-ATTESTATION.md` + `refs/audit/cls-ui/phase4`；README v0.5.0 指针已改；颜色测试顺序泄漏已修；最终工作树补跑全量 382/382 |
| B 用户可见效果/release notes | GO-WITH-CONDITIONS | Phase 3 三路审计补 `phase-3/consensus.md` 与 verify-log 行；新增 `CLS-33` 直接钉 default false；release notes 补彩色启用片段、i18n 固定中文边界、preflight 仅锚点、全量 run 归属；`plugins-compare.md` 加默认关闭/pending 注 |
| C 部署/发布状态机/反假绿 | GO-WITH-CONDITIONS | 不信任 `hermes gateway status` / restart 成功语，采用独立 PID/runs/log/`/larkdeck status` 判据；READY 后显式 push main + tag；`gh release create --draft` 校验后再 publish；部署中仍不得声明 live verified |

## 统一结论

- 发布物（代码/版本/CHANGELOG/release notes/门禁证据）在最终工作树上通过：
  `test_units 225/225`、override/hooks/clarify E2E 全绿、preflight 390/390（382+8）、
  **全量变异 run 4：EXIT=0，382/382 断言红，`🟢0 / 💥0 / ❓0`、非对照 ⚪0、对照 8/8**。
- “颜色默认 false”是已审计的发布路径：真机 `<font color>` 视觉未确认前不得默认开启；
  用户可显式 `panel_color_tags: true` 打开。
- 部署必须按独立判据验证（launchd pid/runs、gateway.pid、日志、`/larkdeck status` 版本），
  不得依赖 `hermes gateway status` 或 restart 的“✓”成功语。
- Release notes 必须保留 `pending`：真机颜色/header、ap_lite、text_profile、无网关 cron；
  不得声明 live verified。
- Phase 3 清场执行仍未闭环：等用户完整汇报后明确确认并记录凭证；当前零删除。

## 残余

- `AGENTS.md` 预算 ~92.8%，后续新增规则前先压缩。
- 审计 worktree / `/tmp` witness 保留；用户确认后再按逐对象清单清理。
- 并发负载下门禁仍可能假红（已登记），最终 gate 独占运行。
