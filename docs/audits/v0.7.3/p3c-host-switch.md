# v0.7.3 P3 重跑（Error 块换 markdown 宿主，commit 732cf88）

* 证据：`~/.larkdeck-scratch/v0.7.3/full-run-evidence.json`、`seed{1..6}.json`、`shard{1..6}.log`；
* 6 分片 rc=0；523 条 red-assert；12 名对照全绿；无 💥/🟢/❓；
* `full_audit_at=10914b6`、`full_audit_tree=eae2f0809e627cb1155b03b3114a970858ad79e6`（== `10914b6^{tree}`）、`tree_dirty=false`、`n_inh=0`；实测墙钟 **1228.5s**；
* P3 事后三路审计（证据链 / 反假绿 / 发布诚实性）全部 PASS（见 `docs/verify-log.md`）。
