# Audit C archived evidence

- `REPORT.txt` is the original Phase 2 audit C report from the run-2 timepoint;
  its final verdict (`GO-WITH-CONDITIONS`) was superseded by run 3 and the
  phase-2 consensus.
- `results.jsonl` is an **incomplete 60/381** per-mutation harness sample, not
  the full 381-mutation result set. `progress.txt` records
  `60/381 elapsed=326.9s`.
- The authoritative full-run counters are in
  `docs/audits/cls-ui/phase-2/manifest.json`,
  `docs/audits/cls-ui/phase-2/logs/fullrun_phase2_run3.log` and
  `docs/audits/cls-ui/phase-2/consensus.md`.
- Four of the six hidden-ERROR mutations (CK9, R3-1, R8-4, G1-1) are not in
  this 60-item sample; P5a2 and SEQ4 are in the sample. They were independently
  reproduced by audits A and C and are covered by the run-3 targeted
  verification and strict classifier.
