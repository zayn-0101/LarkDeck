# Phase 1 final-tree replay attestation (packaged in phase1e)

`refs/audit/cls-ui/phase1e` is the final Phase 1 evidence ref. It supersedes
`refs/audit/cls-ui/phase1d` by force-adding the final-tree replay logs and this
attestation file, which the previous pack referenced but had not committed.
Old provenance refs are left immutable for history.

## Verification tuple

- final frozen ref: `refs/audit/cls-ui/phase1e` (annotated tag; resolve with
  `git rev-parse refs/audit/cls-ui/phase1e^{commit}` / `^{tree}`)
- predecessor evidence ref: `refs/audit/cls-ui/phase1d`
  (tag `98dfec0e90ba6bad6f39986308bc362d6a14add1`,
  commit `94d017d33958fac030cf6711108ce3dfa07340d7`,
  tree `e252dca55de90c88920f04eb5b57a5bf0755b911`)
- canonical mutation run ref: `refs/audit/cls-ui/phase1c`
  (commit `f57b45c8c6887528650b140892a7270b29d319d5`,
  tree `8f8f09102271859aefc400005f6402ae788156da`)
- baseline: `ac3b25e7d1127c2e3968cfa3ef72404067064b9b`
- interpreter: `/Users/Zayn/.hermes/hermes-agent/venv/bin/python3` (3.11.15)

## Runs

### Canonical run (phase1c evidence tree)

| gate | result | in-tree log |
| --- | --- | --- |
| `tests/test_units.py` (two runs) | EXIT=0 · `225/225 passed` | `logs/test_units_phase1c_1.log`, `logs/test_units_phase1c_2.log` |
| `tests/check_override.py` | EXIT=0 · `OVERRIDE OK` | `logs/check_override_phase1c.log` |
| `tests/check_hooks.py` | EXIT=0 · `HOOKS OK` | `logs/check_hooks_phase1c.log` |
| `tests/check_clarify_e2e.py` | EXIT=0 · `CLARIFY E2E OK` | `logs/check_clarify_e2e_phase1c.log` |
| `tests/mutate_check.py --preflight` | `389/389 可用（变异 381 + 对照 8）` | `logs/preflight_phase1c.log` |
| `tests/mutate_check.py -k CLS` | EXIT=0 · `全部 32 条变异都被门禁抓住 ✅` | `logs/mutCLS_phase1c.log` |

### Final-tree replay (phase1d tree = same core/tests/plugin.yaml bytes)

| gate | result | in-tree log |
| --- | --- | --- |
| `tests/test_units.py` | EXIT=0 · `225/225 passed` | `logs/phase1d_test_units.log` |
| `tests/check_override.py` | EXIT=0 · `OVERRIDE OK` | `logs/phase1d_check_override.log` |
| `tests/check_hooks.py` | EXIT=0 · `HOOKS OK` | `logs/phase1d_check_hooks.log` |
| `tests/check_clarify_e2e.py` | EXIT=0 · `CLARIFY E2E OK` | `logs/phase1d_check_clarify_e2e.log` |
| `tests/mutate_check.py --preflight` | `389/389 可用（变异 381 + 对照 8）` | `logs/phase1d_preflight.log` |
| `tests/mutate_check.py -k CLS` | EXIT=0 · `全部 32 条变异都被门禁抓住 ✅` | `logs/mutCLS_phase1d_final.log` |

The two runs are deterministic and byte-identical for the mutation and
preflight logs, so the same SHA256 appears under both names:

- `logs/mutCLS_phase1c.log` == `logs/mutCLS_phase1d_final.log`
  (sha256 `65babb48f5b319c2c46856751672aa0a6450913ea9078a09899c5157070193d3`)
- `logs/preflight_phase1c.log` == `logs/phase1d_preflight.log`
  (sha256 `8ad63c59ffb14505a2952dd4ea6fd0be70ac8d92491b4871538851388309cd04`)

## Correct targeted commands

The mutation names contain a hyphen: use `-k CLS-31` / `-k CLS-32` (without
the hyphen `-k CLS31` matches nothing and exits 2 by design). The canonical
batch command is `-k CLS` (32/32).

## Self-reference

A Git commit cannot contain its own final commit/tree hash. The final tuple is
therefore recorded in the annotated tag object for `refs/audit/cls-ui/phase1e`,
which is created after the commit and carries the resolved commit/tree hash,
the replay-log hashes and the manifest hash.
