# Phase 2 run 1 failure record (superseded)

Run 1 command: `cd /tmp/phase2/larkdeck && /Users/Zayn/.hermes/hermes-agent/venv/bin/python3 tests/mutate_check.py`

- Tree: `2be37dcdd1124351c14e07aea48b6478f72299df` (phase1e evidence ref)
- Result: EXIT=1
- `🔴 断言失败` = 375, `🟢 全绿` = 0, `💥` = 6, `❓` = 0,
  non-control `⚪` = 0, control `⚪` = 8.
- Raw log: `logs/fullrun_phase2_failed_6crash.log` (sha256
  `ec35bf5545e50f88885fb8eadd899b05324b20c4cf601113885c3f4367fb4392`).

Six mutations were classified as crash-only instead of assertion-red:

`R7-D2`, `R9-22`, `R8-6`, `R12-6`, `P1b-8`, `P1b-9`.

Root cause: the affected tests let the pathological exception escape
(`OverflowError`, `RuntimeError`, `AttributeError`, `TypeError`, `KeyError`),
so the hardened classifier correctly refused to count the crash as evidence.
Fixed in commit `76c0e68` by converting those escapes into `AssertionError`
with the original exception preserved in the message, and by re-anchoring the
`R8-6` mutation to the re-indented line.

Targeted verification after the fix: all six `-k <name>` runs ended EXIT=0
with `🔴 断言失败` and were the only red gate; `test_units.py` is 225/225 and
`--preflight` is 389/389 (381+8). The full rerun is `run 2`.

## Audit C conditions and run 3

Phase 2 audit C found that the old `_classify` could record a gate as
`red-assert` when `test_units.py` output contained both an unrelated
`FAIL  ` and an uncaught line-start `ERROR `. Six mutations hid such an
ERROR (P5a2, SEQ4, CK9, R3-1, R8-4, G1-1); run 2's `crash:0` was therefore
only a gate-level count, not a test-level guarantee.

Commit `ecf11df` closes the conditions:

- `_classify` now returns `red-crash` for any `test_units.py` output with a
  line-start `ERROR `, even alongside FAIL lines;
- control-only `-k` now exits 2 instead of printing `0 mutations caught`;
- R8-6 uses the real plain-access staticmethod variant instead of the
  impossible `__func__` spelling;
- the six hidden-ERROR tests now convert the pathological exception to
  `AssertionError` while preserving its original message.

Run 3 on tree `fd96f90f839f6621d83aff46006622d90ff27c1f` (commit `ecf11df`)
finished EXIT=0 with 381/381 assertion-red, 0 crash, 0 green, 0 missing
anchor, 0 non-control no-op and 8/8 controls green. Its raw log is
`logs/fullrun_phase2_run3.log` (sha256 `6cbe3898...`); the byte-identical
hash to run 2 is expected because the six fixes remove hidden ERRORs without
changing the per-mutation gate status lines.
