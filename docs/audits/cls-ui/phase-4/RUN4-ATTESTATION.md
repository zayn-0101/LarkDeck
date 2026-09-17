# Phase 4 run 4 attestation (v0.6.0 release candidate)

Release fixes were applied on top of commit `bdedb9b` in the main worktree
(`HEAD=bdedb9b29141a3aa303c4a8ff7241f2e6005b1da`,
worktree diff sha256 `ab1ed372a3cef9b9b318f63ceaf1c0891350a424adbe8de3f543e1b897d15948`).
The full mutation run then executed against that dirty worktree, so it covers
the exact release content that is committed next (logs are inert for the test
inputs).

- command: `cd /Users/Zayn/Code/larkdeck && PYTHONDONTWRITEBYTECODE=1
  PYTHONUNBUFFERED=1 /Users/Zayn/.hermes/hermes-agent/venv/bin/python3
  tests/mutate_check.py`
- start/end local: `2026-09-18 00:25:24` / `2026-09-18 02:15:30` (+0800)
- exit: `0`
- counts: `🔴 382` / `🟢 0` / `💥 0` / `❓ 0` / non-control `⚪ 0` / controls `⚪ 8`
- log: `logs/fullrun_v0.6.0.log`
  (sha256 `70e28b6b9c8a7d76cecad78ba188ff6cd7629e0c1b082e88215c46a531c9ab31`,
  64,725 bytes)
- expected mutations: 382 (CLS-33 added to pin the `panel_color_tags` default)
- preflight: 390/390 anchors (382 + 8)

Final gate logs in this directory:

| log | sha256 |
| --- | --- |
| `test_units.log` | `9d02e5dcbd06a30ae2a2e04f0a011db414c55ea5940b0a568586f716e43ae1e8` |
| `check_override.log` | `08fe421e55f0148c6c4c41f8dbbe1dbb662d47ddeaad9718ec328861c163cd7f` |
| `check_hooks.log` | `29aee5aace6f41f8d67e2ddb61df56b1be21c20a9299edf7b030abc4ee96c832` |
| `check_clarify_e2e.log` | `e4662421b0597be2a6d7709f4b08b082ab23b834ab5441d81f9d0030a85cb8c6` |
| `preflight.log` | `1702ba34b63f66d6b504c25760222399fc4df88fec9d264a96aaf2121a5ed44e` |

The final release commit/tree hash is recorded in the annotated tag
`refs/audit/cls-ui/phase4`, created after the commit because a commit cannot
contain its own hash. `check_hooks.py` runs with an explicit
`panel_color_tags: true` temp config to keep the coloured assertion; the
shipped default `false` is pinned by `test_units.py` + mutation `CLS-33`.
