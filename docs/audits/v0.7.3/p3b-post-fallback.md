# v0.7.3 P3 重跑（真机回退后，commit db58dc5）

* 证据：`~/.larkdeck-scratch/v0.7.3/full-run-evidence.json`（与 `.db58dc5.json` 相同）、`seed{1..6}.json`、`shard{1..6}.log`；
* 6 分片 rc=0；523 条 red-assert；12 名对照全绿；无 💥/🟢/❓；
* `full_audit_at=db58dc5`、`full_audit_tree=b15070a29fe84247849f3ce0ee6b0d5e503a1dbb`（== `db58dc5^{tree}`）、`tree_dirty=false`、`n_inh=0`；实测墙钟 **1443.9s**；
* 合并备注：base 账本含改名前的 `V073-1c` 旧键 ⇒ 自带 merge 因名字集合多 1 拒绝；剔除旧键后用同一 `tools/merge_ledger4.py --write` 通过（独立审计确认：无缺条、无重复、三指纹现场重算一致）；
* P3 事后三路审计：证据链（deepseek-flash）、反假绿（glm-5.3-flash）、发布诚实性（qwen3.8-flash）全部 PASS；发布诚实性同时发现 README 配置注释与 TAG_MSG 旧文案、已在本 commit 修正；
* ⚠️ 本目录的 `README.md` 记录的是回退前 `65c1c5f` 的一轮，按发布脚本指纹漂移规则**不再改写**，以本文件与 `docs/verify-log.md` 为准。
