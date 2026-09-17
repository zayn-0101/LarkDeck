# Phase 1 Evidence Pack (phase1e)

This pack supersedes `phase1c` (whose logs were not force-added and whose
`freeze.json` was stale) and `phase1d` (which referenced final-tree replay logs
that were still outside the frozen tree). `phase1e` force-adds `RUN-ATTESTATION.md`
and all six replay logs, so the pack is self-contained.

## What was run

Gates run in a clean worktree detached at `f57b45c8c6887528650b140892a7270b29d319d5`
(tree `8f8f09102271859aefc400005f6402ae788156da`), with
`/Users/Zayn/.hermes/hermes-agent/venv/bin/python3` (3.11.15), sequentially:

| gate | result | log |
| --- | --- | --- |
| `tests/test_units.py` (run 1) | EXIT=0 · 225/225 passed | `logs/test_units_phase1c_1.log` |
| `tests/test_units.py` (run 2) | EXIT=0 · 225/225 passed | `logs/test_units_phase1c_2.log` |
| `tests/check_override.py` | EXIT=0 · OVERRIDE OK | `logs/check_override_phase1c.log` |
| `tests/check_hooks.py` | EXIT=0 · HOOKS OK | `logs/check_hooks_phase1c.log` |
| `tests/check_clarify_e2e.py` | EXIT=0 · CLARIFY E2E OK | `logs/check_clarify_e2e_phase1c.log` |
| `tests/mutate_check.py --preflight` | 389/389 anchors (381 mutations + 8 controls) | `logs/preflight_phase1c.log` |
| `tests/mutate_check.py -k CLS` | EXIT=0 · 32/32 assertion-red | `logs/mutCLS_phase1c.log` |

The two mutations added after the previous review are `CLS-31` (`success`
status style) and `CLS-32` (`timeout` status style); both print
`🔴 断言失败` in the final targeted run. Correct targeted commands are
`-k CLS-31` / `-k CLS-32` (the hyphen is required; `-k CLS31` matches nothing
and exits 2 by design).

A second replay was executed directly on the phase1d tree; its six logs are
packaged here as `logs/phase1d_*.log` and `logs/mutCLS_phase1d_final.log`. The
mutation and preflight outputs are deterministic and byte-identical to the
canonical phase1c logs (same SHA256; see `manifest.json` `log_aliases`).

## Why the final tree is covered

The final tree differs from the evidence-run tree only in audit evidence files,
`CHANGELOG.md`, and `docs/verify-log.md` (count text). `manifest.json` records
`source_identity`: `core/`, `tests/`, and `plugin.yaml` are byte-identical
between the two trees. The four gates and the mutation runner read those code
paths only; no test reads the audit log or changelog text.

## Self-reference

A Git commit cannot contain its own final commit/tree hash. `manifest.json`
points at `refs/audit/cls-ui/phase1e`; the annotated tag object for that ref is
created after the commit and carries the resolved commit/tree hashes, the
replay-log hashes and the manifest hash, so the immutable verification tuple
lives outside the commit without relying on the commit knowing itself.
