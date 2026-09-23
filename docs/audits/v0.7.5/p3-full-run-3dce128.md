# v0.7.5 P3 全量变异证据（fa = `3dce128`）

- 冻结提交：`3dce128`（HEAD，工作树 clean）· tree `70bbb6f442733d516fbccd2727bef7fd8b5ca13d`
- run_id：`3dce128-20260923-221159` · 墙钟 **1282.4s**（22:11:59→22:33:21）
- 规模：**577/577 red-assert** + 12/12 对照绿；🟢0 / 💥0 / ❓0
- 分片：6 片各 rc=0（96/96/96/95/95/95 变异 + 每片 2 对照），seed 条数/名字集合/声明门禁/首红归属全部通过
- 账本 `_meta`：`full_audit_at=3dce128`、`full_audit_tree=70bbb6f...`、`tree_dirty=false`、`n_inh=0`、
  `full_audit_evidence` 机械核对通过；条目数 577 全 `red-assert`、`at=3dce128`
- 合并：`merge_ledger4.py --write` dry-run + write 均 rc=0；`at 取值：[3dce128]`
- 开跑前：`run_fast.py --full` 8/8（sha256 `53b1d09396b6…`）；`--preflight 589/589`（577+12）
- 环境指纹：python 3.11.15 · mutate_check `4b542038f8f5…` · merge_ledger4 `7351b042b54a…`
  · runner `59b5acf9d088…` · release `0d5bfb5a2c4e…` · CLS repo head `abdc93ca00ec…`
- 证据：`~/.larkdeck-scratch/v0.7.5/evidence-3dce128/`（6 log + 6 seed + 6 inventory +
  `full-run-evidence.json` + `sha256.txt`），另有带头 `full-run-evidence.3dce128.json`。
- 说明：V075 共 38 条；其中 V075-2/3 是 legacy/patch **rollback/test-forced** 车道
  （生产 `_ld_visual_engine()` 恒为 structured，structured 下 `native_transport: patch` no-op），
  只作回退合同证据；其余 36 条落在生产 structured / 降级 / 防御路径。
