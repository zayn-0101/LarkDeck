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
