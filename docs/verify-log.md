# 全量变异验证的**留档账本**

这个文件存在的理由（2026-09-16 实测踩出来的）：

> 我一直把「留证」放在 `$TMPDIR/g1/*.log`，而 **macOS 会定期清理 `/var/folders/.../T/`** ——
> 2026-09-16 中午那些日志**连同两棵影子树一起没了**。于是交接单里若干处「留档在 `$TMPDIR/…`」
> 变成了**指路到空处**，正是本项目反复在治的那种病（「文档说了、实际没有」）。
>
> ⇒ 两条新规矩：
> 1. **跑验证的日志写到 `~/.larkdeck-scratch/`**（主目录下，不在 temp 里）；
> 2. **结论记进这个文件并提交** —— 它跟代码一起走，`git log` 能证明它什么时候被写的。
>
> ⚠️ 这个账本**不是**「跑绿过」的装饰：它记的是**哪一次跑、多少条、什么结论**，
> 而每一条都能用同一条命令复跑（`tests/mutate_check.py`，见 `AGENTS.md` 的「验证」一节）。

判定记号（与验证器的输出一致）：

| 记号 | 含义 |
|---|---|
| 🔴 | 断言失败 —— **这才算「被门禁抓住」** |
| 🟢 | 撤掉修复后四门禁仍全绿 ⇒ **断言没有判别力**（要修的是断言） |
| ⚪ | 对照项全绿（**符合预期**）；或「变异没生效」（**也算失败**，2026-09-16 起区分开） |
| 💥 | 只有崩溃（语法错/import 炸）⇒ **不算判别力证据** |
| ❓ | 锚点没找到 / 歧义 ⇒ **清单与源码脱节**，一律算红 |

---

## 记录

> ⚠️ 2026-09-16 中午之前的那几轮，**日志已被系统清理**；下表里那些行的数字来自当时的
> 工具输出（会话内记录），不是从日志里重新读出来的 —— 如实标注。

| 时间 | 变异条数 | 🔴 | 🟢 | ⚪ | 💥 | ❓ | 对照 | 结论 | 日志 |
|---|---|---|---|---|---|---|---|---|---|
| 09-15 23:20–00:18 | 310 | 310 | 0 | — | 0 | 0 | 8/8 | EXIT=0 ✅ | 已被清理 |
| 09-16 00:24–01:18 | 310 | 310 | 0 | — | 0 | 0 | 8/8 | EXIT=0 ✅ | 已被清理 |
| 09-16 01:35–02:35 | 310 | 310 | 0 | 8 | 0 | 0 | 8/8 | EXIT=0 ✅ | 已被清理 |
| 09-16 03:25–04:5x | 313 | 313 | 0 | 8 | 0 | 0 | 8/8 | EXIT=0 ✅ | 已被清理 |
| 09-16 04:16–05:0x | 313 | 313 | 0 | 8 | 0 | 0 | 8/8 | EXIT=0 ✅ | 已被清理 |
| 09-16 08:29–12:11（中断） | 315 | — | — | — | — | — | — | **未拿到结论** —— 跑到第 23 条时**应用重启把进程带走了**（不是代码卡住，见下节） | 丢失 |
| 09-16 13:02–14:13 | 315 | 315 | 0 | 8 | 0 | 0 | 8/8 | **EXIT=0 ✅**（日志里那句「全部 315 条变异都被门禁抓住 ✅」只在 `bad` 为空时打印 ⇒ 等价于退出码 0） | `~/.larkdeck-scratch/fullrun7.log` |
| **09-16 16:11–17:35** | **317** | **317** | 0 | 8 | 0 | 0 | **8/8** | **EXIT=0 ✅** —— 收在 `aa6571b` 的代码上（第五轮审计的 5 条缺口全部修完之后），含新增的 `PA-1`/`PA-2`。日志末行：`全部 317 条变异都被门禁抓住 ✅` | `~/.larkdeck-scratch/fullrun9.log`（49 KB） |
| 09-16 15:45–16:0x（**主动中断**） | 316 | — | — | — | — | — | — | **未拿到结论，且是故意的**：这一轮跑到第 ~50 条时，第五轮对抗审计报出**5 条缺口**（其中两条 medium 在我刚加的那格门禁里）⇒ 它验证的树马上就要改，**继续跑完只会得到一份过期的结论**。已 kill（pid 65238），改完重跑（见下一行）。日志 `fullrun8.log` 保留（0 字节，块缓冲没落盘） | `~/.larkdeck-scratch/fullrun8.log`（空） |
| **09-17 03:19–04:42** | **349** | **349** | 0 | 8 | 0 | 0 | **8/8** | **EXIT=0 ✅** —— 收在 v0.5.0 发布候选树（`79cf0c7` + 发布元数据）上；基线四门禁全绿（`test_units` 218/218）。末行：`全部 349 条变异都被门禁抓住 ✅` | `/tmp/fullrun10b.log`（P4 会话沙箱只允许写 `/tmp`；复跑命令同本节） |
| **09-17 09:3x（已 superseded）** | **4（定向 CLS）** | **4** | 0 | — | 0 | 0 | — | ~~EXIT=0 ✅~~ —— 这是**旧的 4 条**定向 run，基线数字 219/219；当前清单已扩到 CLS-1..9，见下方 Phase 0 行与 `docs/audits/cls-ui/phase-0/`。旧数字不得再引用 | `/tmp/mutCLS.log`（旧） |
| **09-17 Phase 0（计划审计冻结）** | **9（`-k CLS` 定向）** | **9** | 0 | — | 0 | 0 | — | **CONDITIONAL GO** —— 三路计划审计 GO-WITH-CHANGES + 第 4 方 D1 裁决（条件式 a）；venv 四门禁全绿（`test_units 223/223`）、preflight 366/366（358+8）、CLS-1..9 全 🔴；冻结快照 `refs/audit/cls-ui/phase0` / tree `efe612a8…`；未闭环 H1/D1 探针、H3/D2 兜底、H4 旧数字 | `docs/audits/cls-ui/phase-0/manifest.json` + `consensus.md` + `freeze.json` |
| **09-17 Phase 1（实现收口）** | **32（`-k CLS` 定向）** | **32** | 0 | — | 0 | 0 | — | venv 四门禁全绿（`test_units 225/225` ×2）、preflight **389/389（381+8）**、CLS-1..32 全 **断言红**（含 `success`/`timeout` 两条状态分支）；D2 英文动作词 + F1/F2/F3/F5 + D3 无色降级接线 + error footer + 小上限标签截断修复已落地；D1 按裁决转 c（生产无 header 代码） | `docs/audits/cls-ui/phase-1/`（`mutCLS_phase1c.log` + probe-header.md） |
| **09-17 14:05–16:47（Phase 2 run 1）** | **381** | **375** | 0 | 0 | **6** | 0 | **8/8** | **EXIT=1 ❌** —— 6 条被归类为「只有崩溃、没有断言失败」（`R7-D2`/`R9-22`/`R8-6`/`R12-6`/`P1b-8`/`P1b-9`）；测试把病态异常直接抛出，已改为断言失败（commit `76c0e68`） | `docs/audits/cls-ui/phase-2/logs/fullrun_phase2_failed_6crash.log`（sha256 `ec35bf55…`） |
| **09-17 16:57–18:24（Phase 2 run 2，已被 run 3 取代）** | **381** | **381** | 0 | 0 | 0 | 0 | **8/8** | **EXIT=0 ✅（口径不足）** —— 审计 C 发现 6 条变异在 test_units 内既有 FAIL 又有未捕获 `ERROR`，旧分类器仍记断言红；树 `755eda35…`。保留作历史，结论以 run 3 为准 | `docs/audits/cls-ui/phase-2/logs/fullrun_phase2_run2.log`（sha256 `6cbe3898…`） |
| **09-17 19:14–20:45（Phase 2 run 3，最终）** | **381** | **381** | 0 | 0 | 0 | 0 | **8/8** | **EXIT=0 ✅** —— 冻结树 `fd96f90f839f6621d83aff46006622d90ff27c1f`（commit `ecf11df`）；`_classify` 已把 test_units 行首 `ERROR ` 一律判 red-crash，6 条隐藏 ERROR 测试全部改成 AssertionError；control-only `-k` 改为 EXIT=2；preflight 389/389；末行「全部 381 条变异都被门禁抓住 ✅」 | `docs/audits/cls-ui/phase-2/logs/fullrun_phase2_run3.log`（sha256 `6cbe3898…`，64,517 B） |
| **09-17 21:xx–22:xx（Phase 3 neat-freak）** | — | — | — | — | — | — | — | **GO**（A 文档一致性、B 规则/记忆边界均无条件 GO；C 文档/证据 GO，清场执行 NO-GO 等用户确认）—— closeout `b94bedf`→`b41306d`→`4810d0b`；AGENTS 预算 92.8%、`.gitignore` 加固、phase-3 inventory/consensus/证据归档；零删除 | `docs/audits/cls-ui/phase-3/{consensus.md,inventory.md,logs/}` |
| **09-18 00:25–02:15（Phase 4 release 候选，最终）** | **382** | **382** | 0 | 0 | 0 | 0 | **8/8** | **EXIT=0 ✅** —— 颜色默认翻转 + release-gate 修正后的工作树（基于 `bdedb9b`）；四门禁全绿（`test_units 225/225`）、preflight 390/390（382+8）；末行「全部 382 条变异都被门禁抓住 ✅」；发布物见 `docs/releases/v0.6.0.md` | `docs/audits/cls-ui/phase-4/logs/fullrun_v0.6.0.log`（sha256 `70e28b6b…`，64,725 B） |
| **09-18 08:45–10:20（v0.6.1 修复 run）** | **383** | **383** | 0 | 0 | 0 | 0 | **8/8** | **EXIT=0 ✅** —— 修复“空累积时 core 进度帧把 terminal 代码块画进答案”；四门禁全绿（`test_units 226/226`）、preflight 391/391（383+8）；末行「全部 383 条变异都被门禁抓住 ✅」；发布物见 `docs/releases/v0.6.1.md` | `docs/audits/cls-ui/phase-4/v0.6.1/fullrun_v0.6.1.log`（sha256 `2dce07f4…`，64,968 B） |
| **09-18 14:09–14:57（v0.6.2 多行进度 + 默认颜色/字号 run）** | **391** | **391** | 0 | 0 | 0 | 0 | **7/7** | **六分片汇总 EXIT=0 ✅** —— 空累积多行进度逐行剥离；默认 `panel_color_tags=true` / `text_profile=compact`；`/stop` 判据带真实面板。四门禁绿（`test_units 226/226`）、preflight 398/398 (391+7)、六片 391/391 断言红、7 对照全绿、无 🟢/💥/❓/墙钟抖动；日志与 sha256 见 `docs/audits/cls-ui/phase-4/v0.6.2/`；真机视觉待用户截图 |


| **09-21 21:2x（v0.7.2 增量收口，**非全量**）** | **471** | **193 实跑** + 277 继承 | **0** | **0** | 5（后全部修掉） | 0 | 11/11 | **EXIT=0 ✅（按新协议：增量 + 周期全量）** —— 见下方「09-21 协议变更」。实跑部分：77 条（2 分片、目标门禁直跑）+ 116 条（补跑 + 复核）。**发现并修掉 5 个真缺陷**：① `G2-3`/`V2-1` 两条变异的替换串**本身就是语法错误**（💥 什么都没验）；② `R9-15`（正常路径被记成写卡失败）**无断言**；③ `V0-7`（degraded 回合重入结构化帧）**无断言**；④ `V4-25`（结构化正文渲染 `⏳ 正在生成…`）**无断言**；⑤ `icon_emoji` 的兜底**急切求值**（`ICON_FALLBACK` 被改就 KeyError）。另把 4 条**等价变异**移进对照（`V0-1`/`V0-15`/`V0-17`/`V3-3`）。 | 分片日志 `/tmp/mut-s{1,2}.log` + `/tmp/mut-final.log`（会话内） |
| **09-21 22:0x–22:4x（补齐门禁侧指纹）** | **278** | **278** | 0 | 0 | 0 | 0 | 10/10 | **EXIT=0 ✅** —— `--upgrade-inherited` 把 277 条「只有代码区域指纹」的继承条目重跑一遍（2 分片、目标门禁直跑、45s 上限）⇒ 账本变成 **470 条 red-assert、0 条 inherited、0 条缺门禁指纹**。两片末行：「全部 139 条变异都被门禁抓住 ✅」/ 另一片仅对照 🟢（预期） | `/tmp/up-s{1,2}.log`（会话内） |
| **09-21 23:0x（终审后收口 + 账本终态）** | **474** | **474 实跑 + 0 继承** | 0 | 0 | 0 | 0 | 11/11 | `--ledger-status`：**474/474 可跳过、待跑 0**。三路发布前终审（A/B/C）共 4 条真缺陷 + 4 条协议逃逸全部收口（`panel_expanded` 被吞 / 页脚被面板绑死 / 降级安全网漏收尾帧 / helper 指纹缺失 / **锚点指纹取错位置 22/474**（R7-16 实证「delta 报已验但门禁红」）/ `expect==""` 挡 full / `--seed-inherited` 洗白 / 超时文案）；每条跳过判据升级为**三重指纹**，`--preflight` 增「指纹锚点必须含完整 old」守卫（换回坏实现时精确拦下 22 条）。 |
| **09-21 23:0x–23:3x（收口复核后：全量直跑刷新 + 缺口补跑）** | **475** | **475（分片直跑 179+175、缺口 `--delta` 60+59、定向 1+1）** | 0 | 24/24（恢复段 12 次对照全绿；故障窗口另 **6 个对照**失败 —— 日志里的 12 行是 per-case 行 + 结论行被同一正则重复计数，按工件剔除） | **63**（分片 2 瞬时故障 62 + 缺口段 `V1-2` 1；分片 1 的 58 条是**没跑到**、不是 💥） | 0 | 每个进程 6/6（共 12） | **EXIT=0 ✅**：`--ledger-status` = **475/475 可跳过（真跑过 475 + 继承 0）；待跑 0**；`--delta --list` = 待跑 0 / 跳过 475。**两件事**：① 新增真绿变异 `CAND-B2`（`_panel_has_data` 丢 `tools` ⇒ 纯工具回合整块吞掉执行面板）⇒ 补断言 + 入账本，两层断言都实测有判别力；② 揪出**验证盲点**：`V1-2` 的整支 `test_units` 被用例里的**无界 `await started.wait()`** 挂到 45s 门禁超时（💥 ⇒ 「没有证据」）⇒ 新增 `_await_event` 有界等待（两处），修后 V1-2 **4.7s** 判红并记账。23:09 一次瞬时环境故障窗口内 120 条被记 💥（分片 1 进程被误杀于 179/237、分片 2 尾部 62 条），同一批快照重跑全部正常 ⇒ 按工件处理，缺口用「把 120 条从种子账本删掉 + `--delta`」补跑。合并脚本（当晚用 v3；**B 路审计实测证明它可被伪造日志绕过** —— 已补加固参考实现 `merge_ledger4.py`：head 必须真实 commit + 现场重算三指纹 + `at` 不无条件覆盖 + 对照按 12 个名字核）按当日证据才盖 `full_audit_at=7e7a62d`；缺口用的是**公共种子 354 条**（474 − 120，`gap_seed_common.py`）；`CAND-B2`/`V1-2` 两条在干净树 `396f0ae` 上补了可追溯复跑（`at` 已改成 `396f0ae`，其余 473 条为 `7e7a62d`） | `~/.larkdeck-scratch/v0.7.2-full-20260921/`（8 份日志 + 脚本 + `README.md`：真实执行序列） |
| **09-21 22:4x（最终账本，历史）** | 471 | **470 实跑 + 0 继承** | 0 | 0 | 0 | 0 | 10/10 | `--ledger-status`：`471/471 可跳过（本轮真跑过 470 + 继承 0）；待跑 0 条`；每条都同时带**代码区域指纹**与**门禁文件指纹** —— 上一行的残余风险已关闭 | `tests/mutation-verdicts.json` |
| **09-22 14:18–14:5x（P3.1 图标定版落地后的缺陷修复：全量重验）** | **482** | **482（4 分片 119+119+118+118；其中 8 条先判 💥，定向 `-k` 复跑后全部转 red-assert）** | 0 | 对照 12/12（每片 3 条，全绿） | **8**（机器负载 27 下门禁撞 45s 硬超时；已定向复跑转红并记账） | 0 | 4/4 进程 | **EXIT=0 ✅** —— 冻结树 `37d2ab1`：`--ledger-status` = **482/482 可跳过（真跑 482 + 继承 0）、待跑 0**；`--delta --list` = 待跑 0 / 跳过 482；`--preflight` = **494/494**（482 变异 + 12 对照）；`_meta.full_audit_at=37d2ab1`（evidence = 「482 名恰好 + 现场三指纹一致 + 日志 🔴 并集覆盖 + 12 名对照全绿」）。**起因**：真机探针踩出 `markdown.text_color` 与 `div.text.icon` 两个非法字段（服务端 `200621` **整卡被拒** ⇒ 长回合掉纯文本「灰气泡」，P0 级）⇒ 修 + 新增**面板元素字段白名单**门禁 + 变异 `V4-60`/`V4-61`；golden 夹具在 helper 指纹里 ⇒ 按协议全量重跑（见 `audits/v0.7.2/audit-round1.md` §8.7）。⚠️ 首次用 `nohup … &` 起的分片被常驻 shell 的重置在 ~98/121 处带走（无账本写入）⇒ 归档 `dead-1431/`，改 `start_new_session=True` 重跑 | `~/.larkdeck-scratch/v0.7.2-fix-20260922/`（4 分片日志 + `rerun.log` + `seed1–5.json` + `merge.sh`/`launch.py`/`wait_shards.py`/`rerun_crashes.sh`；`dead-1431/` = 被带走那轮） |
| **09-22 16:25–16:39（页脚模型显示名：`display_model` 从 ID 改成模型名）** | **484** | **484（4 分片 121×4；0 💥 / 0 🟢 / 0 ❓）** | 0 | 对照 12/12（每片 3 条，全绿） | 0 | 0 | 4/4 进程 | **EXIT=0 ✅** —— 冻结树 `f97ce19`：`--ledger-status` = **484/484 可跳过（真跑 484 + 继承 0）、待跑 0**；`--delta --list` = 待跑 0；`--preflight` = **496/496**（484 变异 + 12 对照）；`_meta.full_audit_at=f97ce19`（evidence = 「484 名恰好 + 现场三指纹一致 + 日志 🔴 并集覆盖 + 12 名对照全绿」）。**起因**：用户 2026-09-22 口径「显示现在的好像是模型 ID，我想要做成显示模型名」⇒ `core/context.py::display_model()` 改成「别名优先（配置 / `~/.hermes/model_aliases.json`）+ 确定性格式化（token 表 / 版本号 / 参数量 / 日期戳）」；golden 夹具随之变化（`🤖 test-model` ⇒ `🤖 Test Model`）⇒ helper 指纹变化，按协议全量重跑。新增变异 `V4-62`（退回 ID）/`V4-63`（别名不再优先）；`test_units` 283/283 | `~/.larkdeck-scratch/v0.7.2-modelname/`（4 分片日志 + `seed1–4.json` + `launch.py`/`wait_shards.py`/`merge.sh`；`launch.py` 已固化 `start_new_session=True`） |



---

## 09-21 协议变更：**不再每一版都跑 60–90 分钟的全量矩阵**

用户当天直接质疑「又在跑这种需要一两个小时的所谓全量变异矩阵吗？就没有省时一点、聪明一点的办法吗」。
实测确认两件事：

1. **跑得慢有三个可修的工程原因**（都不是「门禁本身慢」）：
   * 每条变异的快照把 `.deploy`（部署 worktree，内含整份源码）一起拷了 ⇒ 每份白多 ~40%；
   * 每条变异的快照目录**只在整轮结束时**才清 ⇒ 磁盘占用 O(N)（实测两分片各涨到 1.6GB）；
   * 大量变异的目标门禁是 `test_units`，而它一次就是 3–8 秒。
   前两条已修（`_prepare` 排除 `.deploy` + 每条判完即删上一代）⇒ `/tmp` 占用从 1.6GB/分片降到 **10MB** 常量。
2. **全量矩阵的信息价值只在「这段代码区域变过吗」** ⇒ 引入**区域指纹账本**：
   * `--delta`：只跑「锚点区域（±15 行 + old/new）指纹变了 / 从未验过」的变异；
   * `--seed-inherited <ref>`：区域与某个已全绿的 ref **逐字节相同**的变异标 `inherited(ref)`
     （**可审计**：账本里写明继承自哪个 ref —— 它不是「跑过了」）；
   * `--target-only`：只跑声明的目标门禁（快 3–5 倍）；**判绿不写账本**（绿的留给完整模式复核）⇒
     不牺牲「至少一门红」契约；
   * `--ledger-status`：只看覆盖率；`full_audit_at` = 最后一次**全量直跑**的提交。
   **残余风险（已关闭）**：只见证「生产代码区域」有个洞 —— 某条继承变异若当年只被一个
   **后来被改写**的用例抓住，今天可能已经绿了。修法：每条记录**同时记门禁文件自身的指纹**
   （`gate_fp`），`--delta` 只在「代码区域 **和** 门禁文件**双指纹**都对上」时才跳过；
   历史继承条目用 `--upgrade-inherited` 重跑一次补齐（2026-09-21 实测：278 条、2 分片、约 14 分钟）。
   本版最终账本 = **475 条 red-assert + 0 条 inherited + 0 条缺指纹**、待跑 0 条、
   `full_audit_at=7e7a62d`（2026-09-21 深夜：分片直跑 179+175 🔴；23:09 瞬时环境故障窗口里
   分片 2 的 **62 条**被记 💥、分片 1 另有 **58 条没跑到**（合计缺口 120，用**公共种子 354 条**
   + `--delta` 补跑 60+59 🔴），再定向复跑 2 条（`CAND-B2`/`V1-2` 两条的 `at=396f0ae`）⇒
   475/475，逐段证据在账本 `_meta.full_audit_evidence`）⇒ 不再依赖继承。
   ⚠️ **2026-09-22 两次再刷新**：账本现为 **484 条**（475 → 480 是 P3.1 图标批次；480 → 482 是修
   `markdown.text_color`/`div.text.icon` 两个非法字段时新增 `V4-60`/`V4-61`；482 → 484 是页脚模型
   显示名新增 `V4-62`/`V4-63`），`full_audit_at=f97ce19`、`--ledger-status` = 484/484、待跑 0；
   实测口径 = **4 分片并行 ≈14–15 分钟**（下午那轮另有 8 条负载 💥 定向 `-k` 复跑 ≈3 分钟；
   晚那轮 0 💥）。两轮的日志分别在 `~/.larkdeck-scratch/v0.7.2-fix-20260922/` 与
   `~/.larkdeck-scratch/v0.7.2-modelname/`。分片起跑**必须** `start_new_session=True`
   （用 `nohup … &` 起的会被常驻 shell 的重置带走）。
   总耗时对比：旧口径 60–90 分钟全量 ⇒ 本版 **增量 ~12 分钟 + 升级补齐 ~14 分钟**；
   全量直跑（2 分片并行）**实测 ≈18 分钟**、缺口补跑 ≈7 分钟；
   之后每版只跑区域/门禁变过的（秒级~分钟级），`full_audit_at` 仍按周期刷新。
   ⚠️ **`--shard i/n`（n≥2）不会自己盖 `full_audit_at`**（`_full` 要求 picked == 全量条数，
   n≥2 时必然不等）：分片账本必须按「当日 🔴 名字并集覆盖全部条目 + 对照**全 12 个名字** +
   **现场重算**三指纹」合并后才可盖章（本轮实际用 `merge_ledger3.py`，但它**可被伪造日志绕过**
   —— 已补加固参考实现 `merge_ledger4.py` 并登记 v0.7.3 固化进 `tools/`）。

⚠️ 上表里「—」表示当时**还没有那个分类**（`⚪ 变异没生效` 是 2026-09-16 才加的）。

**读这张表要注意**：`🔴 == 变异条数` 且 `🟢 0 / 💥 0 / ❓ 0` 才是全绿。
⚠️ 其中 `⚪` 那一列若**大于对照条数**，说明有变异被判成「没生效」——那也是**失败**，不是通过。
⚠️ Phase 2 起审计日志被 `.gitignore` 的 `*.log` 忽略，入库必须 `git add -f`；run 3 与 run 2
输出逐字节相同（同一 sha256），因此额外用 `docs/audits/cls-ui/phase-2/RUN3-ATTESTATION.md`
记录 run id、tree、commit、EXIT，避免「同 hash 不能自证是哪一轮」。

---

## 真机证据（不是变异跑，但同样要留档：这些结论**只能由真机产生**）

| 时间 | 那一格 | 证据（原文照抄 `~/.hermes/logs/agent.log`） |
|---|---|---|
| 09-13 08:59:53 | 2.0 `select_static`（探针 ⑫） | `[larkdeck] 探针点击到达 ✅ tag=select_static option='opt_a' …` |
| 09-13 09:11:10 | 2.0 `select_static`（复点） | 同上 |
| **09-16 16:12:37** | **2.0 `button` + 组件级 `behaviors`（探针 ⑮ —— 效果债的最后一格）** | `[larkdeck] 探针点击到达 ✅ tag=button option=None input_value=None value={'kind': 'button', 'larkdeck_probe': True} … token=True` |

⚠️ 探针 ⑮ 的判定协议是**点击之前**写死的（`docs/handoff-route.md` §12），所以这一行的结论
不是事后挑的解释：`tag=button`（飞书报的组件类型）+ `value={'kind': 'button', …}`（**我们自己的
回声载荷**，证明组件级 `behaviors` 里的 `value` 原样到了 `event.action.value`）两条同时成立
⇒ **「2.0 按钮能把点击送到服务端」= 实测事实**（此前只有官方文档）。
⚠️ 用户当时的原话是「**界面上没有任何反馈**」—— 那是**预期**：这张探针卡刻意不带任何改卡逻辑，
它唯一的产物就是上面那行日志（卡片上也写着「应产生一条日志」）。

---

## 复跑方法

```bash
cd <仓库>
/Users/Zayn/.hermes/hermes-agent/venv/bin/python3 tests/mutate_check.py \
  > ~/.larkdeck-scratch/fullrun-$(date +%m%d-%H%M).log 2>&1
echo "EXIT=$?"        # 必须 0
```

⚠️ **`cmd | tee log | tail` 会吃掉退出码**（管道的退出码是 `tail` 的）——
2026-09-15 我因此把一次红跑读成了绿。**只看 `EXIT=`，或直接看验证器自己那句
「全部 N 条变异都被门禁抓住 ✅」。**

---

## ⚠️ 起跑方式：**必须脱离会话**（2026-09-16 实测）

一次全量要 60–90 分钟。**应用一重启，普通后台任务就被带走** —— 2026-09-16 12:11 实测：
跑到第 23 条时应用重启，进程没了，快照停在 `mut22`，而日志是空的（0 字节）。
我当时误判成「某条变异把门禁挂住了」，**实际不是** —— 把那条变异（`S1`）单独复现，
四个门禁 12 秒跑完、`test_units` 196/200 正常变红。**是进程被带走了，不是代码卡住。**

⇒ 起跑方式（关键只有一句 `start_new_session=True`）：

```python
import subprocess, pathlib
log = pathlib.Path.home() / ".larkdeck-scratch" / "fullrun.log"
with open(log, "wb") as fh:
    subprocess.Popen(
        ["/Users/Zayn/.hermes/hermes-agent/venv/bin/python3", "tests/mutate_check.py"],
        cwd="<仓库>", stdout=fh, stderr=subprocess.STDOUT,
        start_new_session=True)      # ← 新会话：父进程死了它也继续跑
```

**怎么判断它还在跑**（不靠 `ps`，也不靠应用的任务面板）：

```bash
A=$(ls -dt /var/folders/*/*/T/larkdeck-mut-* 2>/dev/null | head -1)
ls "$A" | wc -l          # 快照目录数在长 ⇒ 活着；几分钟不动 ⇒ 死了或被带走了
wc -c < ~/.larkdeck-scratch/fullrun.log
```

⚠️ **日志是块缓冲的**：跑完之前 `grep` 可能读到 0 行，**别把「日志为空」当成「没在跑」** ——
看**快照目录数**（它每条变异都会新建一个）才是可靠的心跳。


## 2026-09-22 · v0.7.2「面板 UX 定版」批次（A1/B1/C1/D1′，P3–P4 流水）

**计划**：`docs/plan-v0.7.2-panel-ux.md`（执行契约）· **P1 三路计划审计**：`2dbf247f`/`19ee94be`/`344b1e37`。

**P3 三路对抗审计（生产代码）**：A `6228d4d8` · B `9b0b5a01` · C `f1723fab` ⇒ 逐条处置见计划 §7
「P3」表。两条**高**项当场修掉：

* B-F1：`mutate_check.py` 的 `finally` 里盖章 ⇒ 一条 `Ctrl-C` 也能写出
  `full_audit_at=HEAD` + `entries=0`。修法＝`finally` 只写增量、全量章挪到两个循环都跑完之后。
  复验：`sigint_probe.py`（在「基线自校验」后 0.5s 发 SIGINT）⇒ 账本**没有** `full_audit_at` ✓。
* B-F2 / A-⑥：`_ld_ck_split` 的第二个 seed 点（封旧卡→开新卡）删掉后六支门禁全绿。修法＝
  新增 structured 车道专用用例 `test_v072_a1_seal_split_new_card_is_expanded` + 变异 `V4-75`。

**同一轮修掉的其它确认项**：回合结束/中止不定稿（A-①，`record_turn_end`/`mark_stopped` 补
`_finalize_round_locked`，V4-77/V4-78）；幂等闸门之后才切轮（A-②，V4-76）；`running` 行不拼耗时段
（C-13）；`check_hooks` 空页脚缩进（C-15）；README/plugin.yaml 的 D1′ 假声明（C-1/2）等。

**门禁（本工作树，`run_fast --full`）**：8 步全绿 —— `test_units 290/290`、
`CARDVIEW OK`、`HOOKS OK`、`OVERRIDE OK`、`CLS ALIGN OK`、`OWN BODY GATE OK`、
`mutate_preflight 511/511 锚点可用`（499 条变异 + 12 条对照）。

**新变异（完整模式实红，逐条贴输出）**：`V4-64…V4-78`（D1′ 自制动图 / 中间帧不带 expanded /
两处 seed 的 streaming 展开 / 收尾仍走 panel_expanded / 嵌套轮折叠 / `finalized` 数据 /
页脚两条 B1 反面 / `_is_full_run` 漏 target_only / 两表对等 ×2 / 切卡第二处 / 幂等切轮 /
回合结束与 `/stop` 定稿），另重对齐 `V0-11`/`V0-12`/`V4-42`/`T4`/`CLS-31`。

**黄金夹具**：`write_golden_trace.py --check` 一致；重生成后的 diff＝28 处叶子差异，逐条对应
A1（3 处 partial 少 `expanded`、entity `expanded` false→true）、C1（`turquoise`→`blue`）、
默认②（`Succeeded`→`✓`）、D1′（`standard_icon`→`custom_icon` + `img_key`）、B1（页脚去 `🤖`），
无未解释差异。

**真机协议前提（P6 前移做）**：`tests/probe_manual_collapse.py` —— 建卡（`expanded=true`）→
请用户手动收起 → 发一帧生产形态 partial（内容变、**不带** `expanded`）→ 看是否保持收起。
卡 `om_x100b6412c77ddca8c3b79b34234a6ec`（2026-09-22 发）。

**真机结论（用户 2026-09-22 目视 + 截图）：手动收起**能保住**。** 用户把外层面板手动收起后，
我们发了一帧**生产形态**的 partial（标题文字变了、载荷字段 = `border/elements/header/vertical_spacing`、
**不含 `expanded`**，`code=0`）⇒ 面板**仍保持收起**（截图：箭头朝下、内容隐藏、标题已是「第 2 帧」）。
⇒ **A1 的核心协议前提成立**：`partial_update_element` 的省略**不会**把用户手动收起的状态顶开。
（内层轮的行为另记：我们每帧会按 `finalized` 写内层 `expanded`，所以手动展开已结束轮会被收回 ——
这是已知并登记 v0.7.3 的限制，探针卡里只做观察。）

## 2026-09-22 · P5 全量变异重验（`adea7cb`）

* **6 分片完整模式**（不用 `--target-only`）：499 条变异 + 12 条对照，独立账本/日志，`-u` 不缓冲；
* 结果：**每片 83–84 条 `🔴 断言失败`、坏 0（无 💥/🟢/❓）**，合计 **499**；
* 合并（`tools/merge_ledger4.py`，脏树硬失败 + 记 tree hash）：`✅ 499 条通过（现场重算三指纹 +
  日志覆盖 + 12 名对照全绿）`、`at` 取值全 = `adea7cb`、`full_audit_tree=567f47ec58f8`；
* `--ledger-status`：**499/499 可跳过 · 待跑 0 · 继承 0**；全部 `verdict=red-assert`；
* **实测墙钟 ≈25 分钟**（20:40→21:04；本机同时有桌面/harness 负载 ⇒ 比计划 §4 的 9–11 min 慢，
  已如实写回 §4）；
* 过程中两次**负载假坏**的处置：① 首轮 4 片因我在起跑后提交文档 ⇒ `at` 与 HEAD 不符，审计 B2 判无效，
  整轮停掉重跑；② 6 片并行时 `test_units` 墙钟 47–53s 撞 45s 门禁超时 ⇒ 10 条被记 `💥`（只有崩溃、
  无断言文本）。⇒ 门禁硬超时**上调 45s→90s**（上调是消假坏；下调才会把真红变 💥），重跑后坏 0；
* 真机（P6）与发布（P7）见下文。

## 2026-09-22 22:00 · 发布（P7）

* 用户终验：**通过**（真实回合卡截图：面板收尾折叠 + 标题 `💭 思考 52.0s · 🛠️ 工具执行 · 8 步` +
  页脚 `✅ 已完成 · 52.0s · DeepSeek V4.1 Flash · ctx 32.9k/1m · 3%` —— 页脚那一格从「待验」转为**已验证**）。
* `release-v0.7.2.py --check`：门禁 8/8 · 锚点 511/511 · 账本 499/499 · 1a（`full_audit_at=adea7cb`
  是 HEAD 祖先、指纹路径未改、继承 0）· 1b（`✅ 499 条通过`）全绿。
* `deploy_probe.py`：`.deploy` → `6fd68f3` + 网关重启 + `启动自检通过`。
* `release-v0.7.2.py --go`：push `main` + tag **`v0.7.2`** + GitHub Release
  （https://github.com/zayn-0101/larkdeck/releases/tag/v0.7.2）+ `.deploy` 指 tag 提交 `6fd68f3` +
  网关重启（自检通过，21:00:38 一行、网关进程 2 个）。
* 追加决定：**A1 展开时序保持不变**（用户复确认；理由与代价见
  `docs/audits/v0.7.2/p6-live-verification.md` 的「追加决定」一节）。

## 2026-09-23 · v0.7.3 宿主矩阵探针（真机目视完成：A/B 通过，C 选定形态③）

* `tests/probe_text_size_hosts.py --send`（生产代码渲染，直接 SDK 发到 `FEISHU_HOME_CHANNEL`）：
  **11 张卡全部 `code=0`**。
  - A `markdown`（line 细节行）× ap_lite/neutral/ap_bubble：`om_x100b641fbacfe8b0c4cee59f31cb6c7` / `om_x100b641fbaecb0a4c4a585431153fcf` / `om_x100b641fba8da4a0ddcb1eb5bc0bc40`；
  - B `div.text=plain_text`（emoji 细节行）× 三主题：`om_x100b641fbac390a8df3b85b8eb88fca` / `om_x100b641fbae728a4dda6d6fac814a19` / `om_x100b641fba84c8a0c44b97b9a308587`；
  - C `div.text=lark_md`（Error 20+ 行栈 + 超长行）× 三主题：`om_x100b641fbada24a4c10f513cf14bba8` / `om_x100b641fbaf93ca8c42a49218f08ac5` / `om_x100b641fba9f30a0c16a23ea8ffaf90`；
  - N1 已知系统提示静默卡：`om_x100b641fba9664a4c3f303c401a41bb`（本地断言无 header/panel/footer）；
  - N2 真实回合卡：`om_x100b641fbaa8a8a0c2ecd6c33ddac0e`（本地断言必须有 `✅ 已完成`）。
* **服务端结论**：三宿主都接受 `x-small`，未出现 `200621`/拒收。
* **真机目视结论（2026-09-23 用户截图 + 像素测量）**：A `markdown` 23→19px、B `div.text=plain_text` 21→17px ⇒ **确实更小**；C `div.text=lark_md` 26→26px（行距同为 44px） ⇒ **客户端忽略 `text_size`**。
* **处置（最终）**：C 旧宿主 `div.text=lark_md` 与 fenced 代码块都确认客户端固定/忽略字号；按用户选定形态③改用 **`markdown` + 逐行 inline code + `x-small`**（候选卡 `om_x100b6400934d8900c16c222e85b90e7`，用户确认「明显更小且可读」）⇒ 最终冻结提交 `a2290da`，`**Error**` 标签与每行代码一起变小；细节行两宿主继续 `x-small`。
* 回退规则：若某 host 被拒或不变小 ⇒ **该 host 退回 `notation`**（改代码 + 断言/变异/夹具 + 重跑 P3）；当前代码**没有运行时自动回退**。
* `send 判定 turn=` 日志复核：`.deploy=8474418`（代码即 `db58dc5`）+ 网关重启（09:49:29 自检通过）后，已确认系统提示 `turn=False`（09:49:22/09:49:39）与中途播报 `turn=False keys=['_interim_send']`（10:28:46）；真实用户消息走**原生 CardKit streaming**、不经过 `adapter.send()`，因此不会产生 `send 判定 turn=True` 日志；该分类日志只覆盖非原生/通知/命令通道。真实回合的 `✅ 已完成` 由真机目视确认（见上文 before/after 卡）。

## 2026-09-23 02:51 · v0.7.3 P3 全量变异（冻结提交 `65c1c5f`；⚠️ 已被 09:16 的 `db58dc5` 重跑取代，见文末）

* 6 分片并行、独立账本/日志、坏 0（无 💥/🟢/❓）；合并 `tools/merge_ledger4.py --write`：
  **523/523 red-assert**、`full_audit_at=65c1c5f`、
  `full_audit_tree=779d67eb05492809db1946e51e03097cac57e22f`（== `65c1c5f^{tree}`）、
  `tree_dirty=false`、继承 0；
* **实测墙钟 886.1s**（02:36:17→02:51:09）；`--preflight 535/535`；`-k V073` 24/24 red；
* 证据：`~/.larkdeck-scratch/v0.7.3/full-run-evidence.json`、`seed{1..6}.json`、`shard{1..6}.log`、
  `docs/audits/v0.7.3/README.md`；
* P3 事后三路审计：证据链（deepseek-flash）/ 反假绿（glm-5.3-flash）/ 发布诚实性（qwen3.8-flash）
  全部 PASS，无 false green；`.deploy` 仍为 `6fd68f3`，P4 部署与真机目视待执行。

## 2026-09-23 · v0.7.3 P4 改前/改后真机对照

* 1/3 旧版（`6fd68f3` 提取代码生产渲染）系统提示：`om_x100b6406193fcca4ddcfdfb9970db9f`，有状态头 + 面板 + 页脚 `✅ 已完成 · Test Model · ctx…`（用户截图）。
* 2/3 新版系统提示：`om_x100b640619337ca8dfa85d019ef2229`，无状态头/面板/页脚/✅（用户截图）。
* 3/3 新版真实回合：`om_x100b640616c6d8b0de2c17b37ce9714`，页脚 `✅ 已完成 · Test Model · ctx…` 保留。
* 字号焦点卡：`om_x100b64062dbdfca8c4297b234ace9ca`（B/C 两组上下对照）；合并验证卡：`om_x100b64060aedaca8c4c3b8cbce81bca`（A/B/C 三组）。
* 回退决定与像素测量见 `docs/audits/v0.7.3/p4-real-device-fallback.md`。

## 2026-09-23 09:40 · v0.7.3 P3 重跑（db58dc5，真机回退后）

* 6 分片并行、独立账本/日志、坏 0；合并 `tools/merge_ledger4.py --write`：**523/523 red-assert**、
  `full_audit_at=db58dc5`、`full_audit_tree=b15070a29fe84247849f3ce0ee6b0d5e503a1dbb`
  （== `db58dc5^{tree}`）、`tree_dirty=false`、继承 0；
* **实测墙钟 1443.9s**（09:16:00→09:40:04）；`--preflight 535/535`；`-k V073` 24/24 red；
* 合并备注：刷新后的 base 账本仍带 `V073-1c` 旧名 ⇒ 自带 merge 先因「名字集合多 1」拒绝；
  剔除旧键后用**同一** `tools/merge_ledger4.py --write` 合并通过（只名字集合问题，无缺条/指纹失败）；
* P3 事后三路审计（证据链 / 反假绿 / 发布诚实性）全部 PASS；详见 `docs/audits/v0.7.3/p3b-post-fallback.md`。

## 2026-09-23 · v0.7.3 P4 Error 宿主切换

* 候选 A `markdown`+notation vs 候选 B `markdown`+x-small：用户截图确认 B 代码块与 `**Error**` 标签一起变小、11–19 行栈可读；旧 `div.text=lark_md`+x-small 与 notation 同大。
* 候选卡 `om_x100b6407fb0468a0df9a8617dab7f93`（2026-09-23 10:37 用户回话）。
* 代码 commit `732cf88`：`_tool_output_div` 改返回 `markdown` 组件（组件级 warning 图标、fenced content、`text_size=x-small`）；断言/变异同步；P3 六分片在该提交重跑。
* ⚠️ 该中间形态已被取代：真机复核发现飞书 fenced 代码块字号固定，最终改为 `markdown` + **逐行 inline code** + `x-small`（形态③，冻结提交 `a2290da`），见本文件「Error 形态③」一节。

## 2026-09-23 · v0.7.3 已知问题登记（v0.7.4）

* **长任务/多卡时中间卡执行详情面板空白**（用户 2026-09-23 反馈，确认只在长任务/多卡出现）：
  用户消息 `om_x100b6407c4b7b4a0b18b539eee9f04a`（10:25:43）后卡片 1 `om_x100b6407c47450a4c2486c55ca4e9ff`（10:25:48）面板展开空白，Working 卡 `om_x100b6407db3f98b4c1198e72899932d`（10:28:47），最终答案卡 `om_x100b6407d5fc2ca8c125c5be7c07dd3`（10:30:11）；日志有 `卡片正文世代漂移` 与 `finalize 分叉`。
* 初步判定：原生流回退/多回合交错时最终整卡拿到的 panel 快照被后续回合顶掉，空面板仍作为状态色载体保留；与本批 x-small/系统提示改动无直接因果。
* 处置：登记 v0.7.4，本批不修、不阻塞发布；详情见 `docs/audits/v0.7.3/p4-long-task-blank-panel.md`。

## 2026-09-23 · v0.7.3 Error 形态③（逐行 inline code）

* 真机事实：`div.text=lark_md` 与 markdown fenced code 两种宿主的 `text_size` 对代码块都不生效；
* 用户选定候选 ③：`markdown` + 每行一个 inline code span + `x-small`；候选卡 `om_x100b6400934d8900c16c222e85b90e7`（用户确认「② 明显更小且可读」）；
* 代码链：`4350ace` 实现 → `9a1c0be` 注释/描述对齐 → `b9c01e5`/`821a17d`/`a2290da` 审计收口；最终冻结提交 `a2290da`、P3 六分片在该提交重跑；
* 证据：`~/.larkdeck-scratch/v0.7.3/evidence-a2290da/`（6 log + 6 seed + 6 inventory + `sha256.txt`）；v2 runner 逐片校验 rc / 选中条数 / 红名集合 / 声明门禁 ∈ 断言红 / 对照绿灯 / seed verdict+at，全过才 merge，且不带旧 fa `--allow-at`；
* **531/531 red-assert**、12/12 对照绿、无 💥/🟢/❓/对照变红/归属漂移；`full_audit_at=a2290da`、`full_audit_tree=869a68f64fa4e2b2d6572a751767afc926da113b`、`tree_dirty=false`、`n_inh=0`、墙钟 **1753.4s**（14:25:57→14:55:11）；`--preflight 543/543`、`-k V073` 32/32 red；
* P3 事后三路对抗审计：反假绿（deepseek-flash）发现逐行契约/icon token 互换/margin/emoji 四个假绿，已用多行硬字面量 + V073-1d..1k 关闭；文档一致性（glm-5.3-flash）发现 README 版本状态、CHANGELOG/release notes 缺门禁与 inline code 措辞、verify-log `turn=True` 口径，已修；证据链（qwen3.8-flash）发现固定名覆盖、merge 先盖章后校验、`--allow-at` 自我扩白、归属漂移等，本轮用 v2 runner 加固；残余风险（账本自身被排除在漂移白名单外）由发布前人工 `git diff fa..HEAD -- tests/mutation-verdicts.json` 复核。

## 2026-09-23 · v0.7.3 发布结果 + 用户真机反馈（v0.7.4 登记）

* 发布：`release-v0.7.3.py --go` 成功 —— main 推送、tag **v0.7.3** = `1cc6ecc`、
  GitHub Release https://github.com/zayn-0101/larkdeck/releases/tag/v0.7.3、
  `.deploy` = `1cc6ecc`、网关重启自检通过 15:51:22；发布前 `--check` 全绿（run_fast 8/8、
  preflight 543/543、531/531 red-assert、full_audit_at=a2290da、tree_dirty=false、n_inh=0）。
* **用户真机反馈（15:59）**：`/reset` 回复卡仍显示页脚 `✅ 已完成`
  （mid `om_x100b640c239740bcc2b83b679166f11`；内容 `✨ 会话已重置！重新开始。 ◆ Model: …`）。
* 根因（日志实证）：`send 判定 turn=True guarded=False keys=['notify']` —— `/reset` 的最终回复
  与真实模型 non-native 终稿**同样带 `notify=True`**；v0.7.3 只登记了部分系统提示前缀，
  未覆盖 Hermes 本地化命令头（`✨ 会话已重置` / `✨ 新会话已启动` / `✨ Session reset` /
  `✨ New session started` 等）⇒ 被当回合卡渲染。
* 处置（用户 2026-09-23 拍板）：**并入 v0.7.4**，不挪 v0.7.3 tag、不单独热修；v0.7.4 扩展
  已知系统/命令前缀清单（reset/new、resume、reload-*、stop、reasoning 等）并补硬字面量
  测试 + 变异，真实回合 ✅ 保留回归。范围与审计见 `docs/plan-v0.7.4.md`。
* 同时登记 v0.7.4：长任务/多卡中间卡面板空白、`show_reasoning=true` 嵌套面板真机探针。

## 2026-09-23 · v0.7.4 P3 全量（冻结提交 5b95374）

* 6 分片 rc 全 0；**539/539 red-assert**、12/12 对照绿；无 💥/🟢/❓/对照变红/归属漂移；
* `full_audit_at=5b95374`、`full_audit_tree=340d88b10a6552ec084f63810c707bd672e9f062`（== `5b95374^{tree}`）、`tree_dirty=false`、`n_inh=0`、墙钟 **1398.9s**（17:38:59→18:02:18）；
* 证据：`~/.larkdeck-scratch/v0.7.4/evidence-5b95374/`（6 log + 6 seed + 6 inventory + sha256.txt）、`docs/audits/v0.7.4/p3-full-run-5b95374.md`。
