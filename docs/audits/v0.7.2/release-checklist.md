# v0.7.2 发布收尾清单（P6）

> 状态：**候选已部署待用户终验**。按 `docs/plan-v0.7.2.md` §2 的「P6 全量门禁范围」执行。
> 顺序固定：**用户终验 → 全量门禁 → 全量/分片变异 → push + tag + release → `.deploy` 指向 tag
> 提交 → 重启网关 → live 核对**。任何一步红都不进下一步。

## 0. 前置（已完成的部分）

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 代码冻结在候选提交 | ✅ | 生产代码 = `7e7a62d`（`.deploy` 同源）；其后的收口提交（`a6b73ae`）**只改测试/文档** ⇒ 生产代码零改动、无需重新部署 |
| 六支门禁 | ✅（本轮多次） | `run_fast.py --full`：全 OK，合计 ~10.4s |
| 阶段审计（A/B/C/C2 + 收口复核） | ✅ | `docs/audits/v0.7.2/audit-round1.md`（含绿变异、收口对照、第八节收口第二轮） |
| 真机探针 | 部分 | 加载指示「① 会动」（用户目视）；图标定版卡 `om_x100b64256c1470acdfadc4d33133fca`；嵌套面板探针 `om_x100b6427ec67d4a4de74424945f4ca0` |
| 变异账本 | ✅ | `--ledger-status` = **482/482 可跳过（真跑 482 + 继承 0）、待跑 0**；`full_audit_at=37d2ab1` 是账本 `_meta` 字段（`--ledger-status` 不打印它）；日志 `~/.larkdeck-scratch/v0.7.2-full-20260921/` |
| 候选部署 | ✅ | `.deploy` = `7e7a62d`，网关已重启，`启动自检通过`（**22:54:43**，行 33029；重启时刻 22:54:37） |

## 1. 用户终验（唯一待办）

请用户确认四件事：

1. 工具行 emoji（**修复后**的定版卡 `om_x100b64146cbc80a8c0230da27d9e9ef`）：区段符号不再复用
   （标题 🛠️ vs terminal 💻）、每个工具的 emoji 贴切、图标与文字对齐；
2. 工具步数 > 20 的长回合：卡片不掉成纯文本、无「⏳ Working —」灰气泡、折叠提示在；
3. 页脚 = `状态 · ⏱ 时长 · 🤖 模型 · ctx`，**没有 🔖**；
4. 建卡瞬间的加载指示：会动、无文字，首字到达即消失。

## 2. 全量门禁（六支，串行）

```bash
PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
cd /Users/Zayn/Code/larkdeck
$PY tests/run_fast.py --full          # 六支 + own_body + mutate --preflight（**首选**）
# 「逐支」等价替代（审计 C 修正：必须带 --require，否则 CLS 仓库缺席时会 SKIP 且 exit 0）：
$PY tests/test_units.py && $PY tests/check_override.py && $PY tests/check_hooks.py \
  && $PY tests/check_clarify_e2e.py && $PY tests/check_cardview.py \
  && $PY tests/check_cls_alignment.py --require \
  && $PY tests/check_own_body.py && $PY tests/mutate_check.py --preflight
```

判据：六支全绿（`check_cls_alignment.py` **必须**带 `--require` 在位运行，缺席即 FAIL；
不带 `--require` 的那条会打印 SKIP 并 exit 0 —— 那是**静默跳过**，不是通过）。
⚠️ 「逐支」清单要含 `check_own_body.py` 与 `mutate_check --preflight` 才与 `run_fast.py --full` 等价。

## 3. 变异验证（**增量优先**；协议见 `docs/verify-log.md`「09-21 协议变更」）

```bash
PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
cd /Users/Zayn/Code/larkdeck
$PY tests/mutate_check.py --preflight              # 0.2–0.8s，先对锚点（目标 494/494）
$PY tests/mutate_check.py --ledger-status          # 只报覆盖率（目标：482/482、待跑 0）
# ⚠️ `--ledger-status` **不打印** full_audit_at（它在账本 `_meta` 里），要看就：
$PY -c "import json;print(json.load(open('tests/mutation-verdicts.json'))['_meta']['full_audit_at'])"
$PY tests/mutate_check.py --delta --list           # 预期「待跑 0 / 跳过 482」；有新增才真跑
$PY tests/mutate_check.py --delta --update-ledger  # 只跑「区域变过 / 新增」的（秒~分钟级）
```

⚠️ **只有 `_meta.full_audit_at` 为空 / 过期时才需要全量直跑**（v0.7.2 已在 `37d2ab1` 上做过：
482/482、`full_audit_at=37d2ab1`）。真要补一次时的口径（2026-09-22 实测：4 分片并行 ≈15 分钟；
负载高时可能有若干条被判 💥（45s 门禁超时）⇒ 定向 `-k` 复跑写进 `seed5.json` 再合并 ≈3 分钟）：

```bash
PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
cd /Users/Zayn/Code/larkdeck
# ⚠️ 分片必须**各写各的账本**（`LARKDECK_LEDGER_PATH`）：`_save_ledger` 是非原子 write_text，
#    两片同时写同一份 tests/mutation-verdicts.json 会丢更新（2026-09-21 归档日志实测：
#    414 条 vs 413 条，各自的 +60/+59 都没写全）。
# 分片并行（2 片；用**普通模式**，不要 --target-only —— 它判绿不记账、要再补一趟）：
LARKDECK_LEDGER_PATH=/tmp/f1-ledger.json \
  $PY -u tests/mutate_check.py --shard 1/2 --update-ledger > ~/.larkdeck-scratch/f1.log 2>&1 &
LARKDECK_LEDGER_PATH=/tmp/f2-ledger.json \
  $PY -u tests/mutate_check.py --shard 2/2 --update-ledger > ~/.larkdeck-scratch/f2.log 2>&1 &
# ⚠️ `--shard i/n`（n≥2）不会自己盖 `full_audit_at`（`_full` 要求 picked == 全量 482 条）
#    ⇒ 必须按**当日证据**合并：
#    「日志里 🔴 名字并集覆盖全部条目 + 对照 ≥6 全绿且 0 假红 + 三重指纹一致」才可盖章；
#    异常退出（💥 成片）时把缺口从种子账本删掉再 `--delta` 补跑。
#    ⚠️ 合并/校验：`merge_ledger3.py` 是当晚实际用的脚本，**已被审计 B 证明可被伪造日志
#       绕过**（不重算现场指纹、不校验 head、无条件覆盖 at）⇒ 只作历史证据保留；
#       用加固版参考实现 `merge_ledger4.py`（head 必须真实 commit / 现场重算三指纹 /
#       at 不无条件覆盖 / 对照按 12 个名字核），它仍**不能**证明日志真伪 —— 能力边界写在
#       脚本 docstring 与归档 README 里。
#    公共种子的真实生成步骤见 `gap_seed_common.py`（删掉并集缺口 = 公共种子，两片共用）。
# 最后复核：$PY tests/mutate_check.py --ledger-status   # 必须 482/482、待跑 0
```

判定：`🟢` = 断言没判别力；`⚪` = 变异没生效；`💥` = 只有崩溃、不算证据；`❓` = 锚点脱节。
四种都必须清零（对照项除外）才算这一版验完。**分片上限 2**（4 分片无收益，且会引入负载假红：
2026-09-21 实测 2 片同时跑时，曾出现一次瞬时环境故障（分片 2 的 62 条被记 💥 + 分片 1 的
58 条没跑到 = 缺口 120）—— 同一批快照重跑全正常）。

## 4. 打 tag / push / release

```bash
cd /Users/Zayn/Code/larkdeck
git tag -a v0.7.2 -m "LarkDeck v0.7.2：页脚去短码 / 加载指示会动无文字 / 图标逐条对齐 CLS + 真实工具名覆盖 / 澄清卡表单契约 / P0 灰气泡根因"
git push origin main --follow-tags
gh release create v0.7.2 --title "LarkDeck v0.7.2" --notes-file docs/releases/v0.7.2.md
```

## 5. `.deploy` 指向 tag 提交 + 重启网关

```bash
cd /Users/Zayn/Code/larkdeck
git -C .deploy checkout -q "$(git rev-parse --verify v0.7.2^{commit})"
git -C .deploy log --oneline -1        # 必须 = tag 提交
/Users/Zayn/.hermes/hermes-agent/venv/bin/hermes gateway restart
```

## 6. live 核对（重启后 90s 内有界等待，不 sleep）

```bash
pgrep -f "hermes_cli.main gateway run"     # 期望 **2 个 PID**：stderr_timestamp wrapper + 网关本体
grep -n "启动自检通过" ~/.hermes/logs/agent.log | tail -1
grep -n "feishu connected\|已连接\|ws.*connected" ~/.hermes/logs/agent.log | tail -2
```

判据：进程在（**一个逻辑网关 = wrapper + child 两个 PID**，别按 1 个判）、
自检行时间戳晚于重启时刻、飞书 WS 已连。随后请用户发一条真实消息复看卡片
（这一步属于 live verified，不能拿「进程在」代替）。
⚠️ 改完 `.deploy` 后再复核一次：`grep -rn '当前发布版' . --exclude-dir=.deploy` 应为空
（`.deploy` 是冻结旧副本，任何 `grep .` 式门禁都要显式排除它）。

## 7. 收尾（neat-freak 第二阶段）

* 清场对象（**待用户确认**，未确认前一个都不动；2026-09-21 清理预览已核对）：
  * **8 棵审计 worktree**（全部 clean）：`/private/tmp/audits/cls-ui{,-phase3,-phase3b}/larkdeck`、
    `/private/tmp/phase2{,b,c}/larkdeck`、`/private/tmp/ldbase/larkdeck`、`/private/tmp/larkdeck-phase1`；
    它们的 commit 由 `refs/audit/cls-ui/*`（15 条本地 ref）保住 ⇒ 删树不丢 commit；
    审计证据日志（`docs/audits/cls-ui/**` 共 **59 份 `.log` 文件**，该目录 tracked 文件共 97）已在
    `main` 里 ⇒ 无唯一未集成内容。
  * `$TMPDIR` 影子树残影：**2026-09-21 实测曾达 22 个 / ≈1.5 GB，现已清 0**（正常路径每条判完即删，
    只有被杀/崩溃会留）；`/private/tmp` 另有 **3 个 09-18 的 `larkdeck-mut-*`**（各 4.2 MB）与
    `$TMPDIR` 里 27 个其它 `larkdeck-*` 测试临时目录（≈40 MB）待清（`cleanup-phase2.sh` 已在
    dry-run 中列出主体对象，这 3+27 个小对象建议一并加进去）。
  * `/tmp` 旧审计导出：`/tmp/larkdeck` 是指向 `/tmp/audit-v062-B` 的**软链地雷**（它让
    「父目录在 /tmp 的仓库」跑门禁时 `import larkdeck` 解析到别人的树 —— 2026-09-21 实测踩到；
    已随 neat-freak 第一阶段移除该软链）；另有 `/tmp/audit-v1-*`、`/tmp/audit-v0-*`
    （`docs/audits/v0.7.1-visual/*.md` 引用的导出路径）。
  * 冗余本地分支 `v0.7.1-visual`（0 个 main 之外的提交，已完全并入 main）。
  * 仓库根忽略残留：`__pycache__/`、`.pytest_cache/`；`.probe_*.json`（探针状态，**保留**）。
  * 本项目**自己的**会话残留（2026-09-21：`/tmp/candb2`、`/tmp/v12probe`、`/tmp/*.log` 副本等）
    已随本轮收尾清理；证据副本在 `~/.larkdeck-scratch/v0.7.2-full-20260921/`。
* 记忆：本项目规则未授权写记忆 ⇒ 标 `generated-read-only / not-applicable`。
