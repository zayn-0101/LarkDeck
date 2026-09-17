# Phase 2 run 3 attestation

本轮全量变异输出本身与 run 2 逐字节相同（sha256
`6cbe38989de14386258346756058b77beca35d7407b140e267fbf80cdfa92ceb`，
64,517 B），原因是审计 C 的 6 条隐藏 ERROR 修复只把未捕获异常转成
`AssertionError`，每条变异在门禁汇总层面的状态行没有变化。为避免日志
无法区分 run 2 与 run 3，这里记录 run 3 的不可混淆元组：

- command: `cd /tmp/phase2c/larkdeck && PYTHONDONTWRITEBYTECODE=1
  PYTHONUNBUFFERED=1 /Users/Zayn/.hermes/hermes-agent/venv/bin/python3
  tests/mutate_check.py`
- commit: `ecf11df032b1cf586e17dedbabba32f87db90bac`
- tree: `fd96f90f839f6621d83aff46006622d90ff27c1f`
- ref: `refs/audit/cls-ui/phase2-run3` →
  `f385164b8685c569d9adf3b0512c0650f4d569a8`
- start/end UTC: `2026-09-17T11:14:41Z` / `2026-09-17T12:45:00Z`
- exit: `0`
- counts: 🔴381 / 🟢0 / 💥0 / ❓0 / 非对照⚪0 / 对照⚪8
- log: `logs/fullrun_phase2_run3.log`；`--preflight` 389/389（381+8）
- run-2 predecessor: commit `76c0e68` / tree `755eda35`
- strict classifier change: `tests/mutate_check.py` treats a `test_units.py`
  line-start `ERROR ` as `red-crash` even when `FAIL  ` lines are present.
- six hidden-ERROR fixes: `P5a2`, `SEQ4`, `CK9`, `R3-1`, `R8-4`, `G1-1`.

`/tmp/phase2c/fullrun_phase2.{raw,meta,exit}` is the external witness used
during the audit; the durable copy is this file plus the committed log and
`manifest.json`/`freeze.json`.
