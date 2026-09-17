# Phase 2 审计统一结论（全量变异验证）

日期：2026-09-17
最终冻结：commit `ecf11df032b1cf586e17dedbabba32f87db90bac`
/ tree `fd96f90f839f6621d83aff46006622d90ff27c1f`
本地 ref：`refs/audit/cls-ui/phase2-run3`（annotated tag
`f385164b8685c569d9adf3b0512c0650f4d569a8`）
解释器：`/Users/Zayn/.hermes/hermes-agent/venv/bin/python3`（3.11.15）

## 过程

1. run 1（tree `2be37dcd`）：EXIT=1 —— 6 条旧测试让病态异常穿透
   （`R7-D2`/`R9-22`/`R8-6`/`R12-6`/`P1b-8`/`P1b-9`），被判 crash-only；
   修复见 commit `76c0e68`。
2. run 2（tree `755eda35`）：EXIT=0、381/381 断言红；但审计 C 独立重跑
   全部 381 条 `test_units` 后发现 6 条变异（`P5a2`/`SEQ4`/`CK9`/`R3-1`/
   `R8-4`/`G1-1`）在同一门禁里既有 `FAIL` 又有未捕获 `ERROR`，旧分类器
   仍记断言红，`crash=0` 口径过强。
3. run 3（tree `fd96f90f`，commit `ecf11df`）：`_classify` 改为
   `test_units` 行首 `ERROR ` 一律 `red-crash`；上述 6 条测试把病态异常
   转成 `AssertionError`；`-k` 只命中对照改为 EXIT=2；`R8-6` 改用真实
   plain-access 变体。结果：EXIT=0、🔴381、🟢0、💥0、❓0、非对照⚪0、
   对照 8/8；preflight 389/389（381+8）。

## 三路最终裁决

| 角色 | 裁决 | 要点 |
| --- | --- | --- |
| A 证据链/可复现 | **GO** | run3 日志、manifest/freeze、ref/tree/worktree、preflight 全部独立对账；6 条隐藏 ERROR 在 run2/run3 树 before/after 逐条复现；R8-6 真实污染路径已由后续用例抓到 |
| B 用户效果/发布 | **Phase 2 GO；Release NO-GO** | Phase 2 零生产行为改动（仅 tests/ + docs/证据）；golden 未变；颜色/header/Phase 3/4 未闭环，Release 边界不变 |
| C 反假绿/测试判别力 | **GO** | 四项条件全部独立复现闭环：ERROR 分类、control-only `-k`、R8-6 保真度、manifest/verify-log 口径；未发现新的假绿路径 |

## 统一结论

- **Phase 2 通过，可进入 Phase 3 neat-freak。** 全量硬条件在 run3 上成立，
  关键修复均有独立复现。
- **Release 仍 NO-GO**，直到 Phase 3 收口 + Phase 4 真机 visual 完成。
  颜色若不能确认渲染，发布前必须把 `panel_color_tags` 默认改为 `false`
  并重跑四门禁 + 定向变异；D1 c 的 header 边界与其他 pending 写入 release notes。

## 转入 Phase 3 的打磨项

1. run3 日志与 run2 逐字节相同（sha256 `6cbe3898…`），日志本身无法自证
   run id/tree/EXIT；应以 repo 内 attestation 文件或 per-run manifest 补足。
2. `docs/handoff-route.md:670` 仍写 `fullrun9 317/317`，需更新/标注历史。
3. `*.log` 被 `.gitignore` 忽略，审计日志必须 `git add -f`；在文档中显式登记。
4. 项目 `AGENTS.md` 62,510 B / Codex `project_doc_max_bytes=65,536`，余量
   约 3 KB；按 neat-freak 预算把历史叙事迁往 `docs/`，只留现役约束。
5. 并发审计负载下偶发一次 `test_units` 224/225（假红方向，随后 225/225）；
   后续全量门禁保留逐门禁原始 stdout 以便定位。
