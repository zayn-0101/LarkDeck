# LarkDeck v0.6.1 fix attestation

Patch: keep core tool progress out of the answer area when the model has not
written any text yet (2026-09-18 real-device screenshot: `🖥 terminal` code
block was rendered as the answer).

- base commit: `d335241d9d9c088f35d0cddecaef6fcda7cdf2b7`
- worktree diff sha256: `6678345673a1d8a6201c281a554d0afba123a94f0777e74b5e2b3f8b23deaf53`
- full run command: `/Users/Zayn/.hermes/hermes-agent/venv/bin/python3 tests/mutate_check.py`
- result: EXIT=0, 383/383 assertion-red, 0 crash/green/missing/noop, 8/8 controls
- full log: `fullrun_v0.6.1.log`
  (sha256 `2dce07f49e3986a9a584cec1bebd2206aeb7bc968fa26d1ad3967b034a5654e7`)
- unit tests: 226/226
- preflight: 391/391 anchors (383 + 8)
- gate logs and hashes: see `manifest.json`

The fix strips only a conservative progress shape with an empty accumulated
answer and an open tool window; finalize frames and ambiguous shapes remain
fail-open.
