# LarkDeck v0.6.2 fix attestation

Date: 2026-09-18 (+08). Base commit: `ce7b51a`.

## Scope

1. Empty-accumulated native frames now strip core's **multi-line** tool-progress
   block (friendly verbs / tool names / terminal fences / `(×N)` / cursor /
   `Working` status), cross-validating friendly verbs against the same session's
   tool snapshot and requiring terminal to be running for header-less fences.
   Model prose (`💡 Note:` / `📖 Reading list` / CJK heads / Markdown markers)
   fails open.
2. Default `panel_color_tags: true` and `text_profile: compact`
   (body `normal`, panel/footer `notation`), with a documented no-colour escape.
3. `/stop` feasibility predicate now builds the **real panel** path (not only
   `status_shell`) and keeps the explicit hard-limit check after
   `apply_text_profile`.

## Gate evidence (frozen worktree)

- `tests/test_units.py`: 226/226 passed.
- `tests/check_override.py`: OVERRIDE OK.
- `tests/check_hooks.py`: HOOKS OK (includes real core multi-line frame).
- `tests/check_clarify_e2e.py`: CLARIFY E2E OK.
- `tests/mutate_check.py --preflight`: 398/398 anchors (391 mutations + 7 controls).
- Full mutation suite: 391/391 assertion-red, 0 green / 0 crash / 0 missing /
  0 noop, 7/7 controls green; no `test_args_preview` wall-clock flake,
  no `225/226`, no Traceback. Six-shard logs in this directory; sha256 in
  `manifest.json`.

## Residual boundaries (must be repeated in user-facing docs)

- `finalize=True` frames are never stripped: if a deterministic frame failure
  makes core resend the same composed text as finalize
  (`gateway/stream_consumer_transport.py`), progress lines can remain in the
  final body. This is the deliberate "never irreversibly swallow the answer"
  trade-off.
- A curated verb added by a newer Hermes that is not yet in
  `_CORE_TOOL_VERBS` fails open: that one progress line may stay visible, but
  model text is never swallowed.
- `<font color>` / `config.style.text_size` are confirmed by official Card 2.0
  markdown docs, but the actual PC/mobile visual still needs the user's
  real-device screenshot.

## Deploy record

- release commit `2ff79ed`; annotated tag `v0.6.2`;
  GitHub Release: <https://github.com/zayn-0101/larkdeck/releases/tag/v0.6.2>
- Mac gateway restarted 2026-09-18 15:00 (+08) via `hermes gateway restart`;
  new gateway pid `58200`, state `running`, Feishu `connected` at 15:00:50;
  larkdeck self-check passed at 15:00:48 with hooks 7/7.
- Real-device visual verification of the answer area, `<font color>` and the
  12px panel/footer text profile is still **pending the user's screenshot**.
