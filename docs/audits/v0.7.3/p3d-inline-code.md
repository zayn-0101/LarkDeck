# v0.7.3 P3 重跑（Error 形态③，冻结提交 a2290da）

* 代码链：`4350ace` 实现形态③（markdown + 逐行 inline code + x-small）→ `9a1c0be` 注释/描述对齐 → `b9c01e5`/`821a17d`/`a2290da` 审计收口（V073-1d..1k + 性能门禁 CPU-time/墙钟双口径），`-k V073-1` 11/11 实红）；
* v2 runner：先逐片校验 rc / 选中条数 / 红名集合 / 每行「声明门禁 ∈ 断言红」/ 对照绿灯 / seed verdict+at，全过才 merge；合并**不带旧 fa `--allow-at`**，不允许继承盖章；
* 证据：`~/.larkdeck-scratch/v0.7.3/evidence-a2290da/`（6 log + 6 seed + 6 inventory + `sha256.txt` + `full-run-evidence.json`，逐文件 sha256 已写入 evidence JSON）；
* 6 分片 rc=0；**531/531 red-assert**；12/12 对照绿；无 💥/🟢/❓/对照变红/归属漂移；
* `full_audit_at=a2290da`、`full_audit_tree=869a68f64fa4e2b2d6572a751767afc926da113b`（== `a2290da^{tree}`）、`tree_dirty=false`、`n_inh=0`；实测墙钟 **1753.4s**（14:25:57→14:55:11）；`--preflight 543/543`、`-k V073` 32/32 red；
* P3 事后三路对抗审计：
  - 反假绿（deepseek-flash）：逐行 contract / icon token 互换 / margin / emoji 分支四个假绿，已用多行硬字面量 + V073-1d..1k 关闭；
  - 文档一致性（glm-5.3-flash）：README 版本状态、CHANGELOG/release notes 缺门禁与 inline code 措辞、verify-log `turn=True` 口径，已修；
  - 证据链（qwen3.8-flash）：固定名覆盖、merge 先盖章后校验、`--allow-at` 自我扩白、归属漂移，本轮 v2 runner 逐条加固；残余风险（`tests/mutation-verdicts.json` 被排除在漂移白名单外）由发布前人工 `git diff fa..HEAD -- tests/mutation-verdicts.json` 复核。
