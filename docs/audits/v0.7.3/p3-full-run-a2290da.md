# v0.7.3 P3 全量证据摘要（冻结提交 `a2290da`）

本文件把当轮 runner 的机械证据（sha256/时间/片级 rc/环境指纹）钉进 git 对象；
原始 log/seed/inventory 在 `~/.larkdeck-scratch/v0.7.3/evidence-a2290da/`，
即使 scratch 被覆盖，也可用本文件逐文件对账。

* `full_audit_at=a2290da`、`full_audit_tree=869a68f64fa4e2b2d6572a751767afc926da113b`（== `a2290da^{tree}`）、
  `tree_dirty=false`、`n_inh=0`；
* 6 片 rc：`{'1': 0, '2': 0, '3': 0, '4': 0, '5': 0, '6': 0}`；墙钟 **1753.4s**（14:25:57→14:55:11）；
* `531/531` red-assert、12/12 对照绿；v2 runner 逐片校验
  「选中条数 / 红名集合 / 声明门禁 ∈ 断言红 / 对照 2 条绿灯 / seed 名字集合+verdict+at」；
* 合并 dry-run 通过后才 `--write`，且不带旧 fa `--allow-at`：`merge_dry_rc=0`、
  `merge_rc=0`、`merge_tail` 仅含 merge_ledger4 自身输出；
* 账本 sha256（盖章写入时）：`f3f61643cfc58273c6217bc712e39716afdc11c2584ede7aed5fe2d2a121f4aa`；
* 环境指纹：`python=3.11.15`、`merge_tool_sha256=7351b042b54a32ee5580732b5e58599e63a4dd4fa489f9c9f09a541ef2a8b742`、
  `mutate_check_sha256=dfe1e8cf9ec0e95b99da33d7cc2075f849e5d766d61895536bdee7ba09500b92`、`larkdeck_env=[]`、
  `cls_repo_head=abdc93ca00ecbc56e13df82e223efcbef6aa81f0`；
* 发布脚本 sha256（写入本文件时；runner env_fp 因 scratch 路径 bug 为空，release 用本行兜底）：`release_script_sha256_at_write=e13ed930bed53a0055579d65bd193b7e7c553f19fd9f4411ec3e56afa8313ea2`；
* 工具快照：`tools/run_full_v2.py`（sha256 `8a19bc923b082976c3adda696ac22c5312517d27992b3cc991efd14bf31e454e`）、
  `tools/release-v0.7.3.py`（sha256 `6ba1d0ff35b11a7cc106e13484409110fc600f1f23b42da0a84972d2e943b9ce`）。

| 产物 | sha256 | bytes |
| --- | --- | --- |
| `fixed_log1` | `b17c65a63a97a78c6574e4e13b02ae12ba1453fe44fff28150d6877c7906a51e` | 15476 |
| `fixed_log2` | `1c8991a237c594e73b9f2b752bb59eded3d196951ba56142841336a9262a3c00` | 15287 |
| `fixed_log3` | `916fc2c49a3afab2b7e299c29b39044a3bc917eebf4f19c600f15afb8c3ba439` | 15379 |
| `fixed_log4` | `7f0f46d1f8ff683cb7e66925fce6dc24d47a8fa410f17e0bbe3865a9c947ed79` | 15343 |
| `fixed_log5` | `35244ad6eab3f4159c911550f227a37cbd97c4d65b0459582119ee4011ccb504` | 15388 |
| `fixed_log6` | `db936fe1d1ff0fe2cd2f8308ea1f7d7b562559d144d0706e79ff653229cf0752` | 15327 |
| `fixed_seed1` | `5360843a19f3b897103bdff93bae58353bb0ecb14d06630114289bfb984677ba` | 1253550 |
| `fixed_seed2` | `b5d1af24d1fdbf433681f7cd3a72ce6e260b3d5f144dc631a0a16fbebd1b4ea5` | 1331309 |
| `fixed_seed3` | `7179e2c89ffa8b60188930f0692c16ad51c449046903957fe696865f083118a9` | 1300152 |
| `fixed_seed4` | `79712ec56bfc66af8c8035127aef3b3b5b3beab749c8e997e0c9dfa2b90dd6db` | 1346864 |
| `fixed_seed5` | `cae9e9aa27d4bb96e5b8e3cc080077c87015e9212270401133910d10ba8b4b58` | 1331311 |
| `fixed_seed6` | `396123ba6f80ce7809a598c960def09432d9f248309087e730f74d6b948444c7` | 1284471 |
| `inventory1` | `1b2918ddbb8161af1495ca9f298b9c3ae2a1b1d984bf26baca212d8afb3f122e` | 25625 |
| `inventory2` | `dc70bdad7c7fbb20650192268947e569560a0c7206345ba079b8517c3850f664` | 25484 |
| `inventory3` | `74e0391c99ec64afc1b2ac20d7869c08aecad31b102de58cbb687f62d7c536a6` | 25502 |
| `inventory4` | `21a26871f9cab7ebe1f9dd9467309fca5a1585880056d762a7149a52ffded917` | 25503 |
| `inventory5` | `0c9da6c6b771424c483749f3bc105b6eb85215ab42bad33c126cd01aa7dfbbe5` | 25531 |
| `inventory6` | `4ee8df4c3fbe2873d88816771bbe94fd606ebb870d01fc0a984a6a493d9e8f75` | 25339 |
| `log1` | `b17c65a63a97a78c6574e4e13b02ae12ba1453fe44fff28150d6877c7906a51e` | 15476 |
| `log2` | `1c8991a237c594e73b9f2b752bb59eded3d196951ba56142841336a9262a3c00` | 15287 |
| `log3` | `916fc2c49a3afab2b7e299c29b39044a3bc917eebf4f19c600f15afb8c3ba439` | 15379 |
| `log4` | `7f0f46d1f8ff683cb7e66925fce6dc24d47a8fa410f17e0bbe3865a9c947ed79` | 15343 |
| `log5` | `35244ad6eab3f4159c911550f227a37cbd97c4d65b0459582119ee4011ccb504` | 15388 |
| `log6` | `db936fe1d1ff0fe2cd2f8308ea1f7d7b562559d144d0706e79ff653229cf0752` | 15327 |
| `seed1` | `5360843a19f3b897103bdff93bae58353bb0ecb14d06630114289bfb984677ba` | 1253550 |
| `seed2` | `b5d1af24d1fdbf433681f7cd3a72ce6e260b3d5f144dc631a0a16fbebd1b4ea5` | 1331309 |
| `seed3` | `7179e2c89ffa8b60188930f0692c16ad51c449046903957fe696865f083118a9` | 1300152 |
| `seed4` | `79712ec56bfc66af8c8035127aef3b3b5b3beab749c8e997e0c9dfa2b90dd6db` | 1346864 |
| `seed5` | `cae9e9aa27d4bb96e5b8e3cc080077c87015e9212270401133910d10ba8b4b58` | 1331311 |
| `seed6` | `396123ba6f80ce7809a598c960def09432d9f248309087e730f74d6b948444c7` | 1284471 |

## 残余风险（来自证据链审计 C）

* `tests/mutation-verdicts.json` 仍在 release 漂移白名单的 `:(exclude)` 里；本轮用
  「fa..HEAD 只能有一个 commit 改账本且必须是盖章 commit」+ 上述 sha256 表缓解；
* 发布脚本已改为：读 `evidence-<fa>/` 的 seed/log、删除 3 个硬编码旧 ref 与自读旧 fa 的
  `--allow-at`、只允许 `--allow-at <fa>`、校验 `full_audit_at` 是 7-40 位 commit 码；
* 窗口内「改了再改回」在机械上无法发现，依赖冻结窗口内无人写仓（本轮 runner 前后双查
  HEAD/tree/status，且账本未被半成品污染）。
