# Phase 3 neat-freak inventory (final, phase3d)

日期：2026-09-17
范围：`/Users/Zayn/Code/larkdeck` 主工作树 + 与本项目直接相关的 worktree/临时对象。
方法：`neat-freak/scripts/audit-inventory.sh` + 规则链阅读 + 代码/运行态/文档/记忆/工作区交叉核对。
解释器：`/Users/Zayn/.hermes/hermes-agent/venv/bin/python3`（3.11.15）。

## 六面事实矩阵

| 事实面 | 状态 | 证据与动作 |
| --- | --- | --- |
| 代码 | `verified-current` | Phase 1 产品实现冻结于 `refs/audit/cls-ui/phase1e`（commit `0bf59525…`）；Phase 2 只改测试/证据，最终全量 run 3 冻结于 commit `ecf11df` / tree `fd96f90f…`。四门禁 225/225 + preflight 389/389。 |
| 运行态 | `pending`（Phase 4 必须 live verify） | Hermes `v0.21.1`；插件软链 `~/.hermes/plugins/larkdeck -> /Users/Zayn/code/larkdeck`；网关 detached 进程 PID 94952/94949 在跑旧模块，launchd service 未加载。Phase 4 要 `git pull` + `hermes gateway restart`，再核对网关日志/`/larkdeck status`/真机颜色。 |
| 文档 | `changed-and-verified` | CLS 布局、i18n 边界、颜色/header pending、run3 数字与代码一致；`docs/handoff-route.md` 的 `fullrun9 317/317` 与 §13.3「7 项未开工」已按 `plugins-compare.md §7.8` 更新注修正；`docs/verify-log.md` 登记 run 1/2/3；相对链接 0 缺失。 |
| 规则 | `changed-and-verified`（附预算 warning） | 项目 `AGENTS.md` 已修掉「审计日志必须 force-add」与「不提交日志」的矛盾（后者限定为可能含凭据/真实 ID 的运行日志）；补齐严格分类器、control-only `-k`、留档口径、关键性质单独成条四条现役规则。文件 62,510 B → 60,803 B；Codex `project_doc_max_bytes=65536`，占 92.8%，作为 **warning** 登记，后续新增规则前必须先压缩。全局规则只读核验，无冲突；项目 red lines 的日志例外边界已写明（不是简单加严）。 |
| 记忆 | `not-applicable` / `generated-read-only` | `~/.codex/memories` 由宿主生成；`grep -Ril larkdeck` 0 命中，本次未写入。不直接编辑生成记忆。 |
| 工作区 | `pending`（closeout 已提交并 push；仅清场待用户确认） | 主工作树在 closeout 提交前为 dirty（本轮 8 个文档/ignore 修改 + 未跟踪 phase-3 目录），现已提交并同步远端；`git worktree list` 当前为 **9 行（主 + 8 linked，含 phase3b 审计树）**。`phase3c`（commit `f5c4618…` / tree `d60d164…`）是前一版快照；终版为 main 上的 closeout 提交及其 `refs/audit/cls-ui/phase3d` tag（不新增 worktree），auditC README 与 inventory 的最后三行文书修正已包含。清场候选见下，删除动作等用户完整汇报后确认。 |

## 运行时与发布状态

- `implemented`：Phase 1–3 closeout 已提交并 push；远端 main 以 `git log origin/main`
  为准（本 inventory 修正前为 `b41306d`；Phase 3 提交不再“随后”）。
- `locally verified`：四门禁 + preflight + 全量 run3；未 live verify 网关。
- `pushed/merged`：产品与证据提交已到 origin/main；Audit refs 仅本地（不 push）。
- `deployed/live verified`：**未开始** —— 网关仍跑旧模块（Phase 4 处理）。
- 真机颜色与 header A1→A2 视觉：**pending**，Release 硬门禁未过。
- 授权链：用户原始请求明确包含「提交 commit 并在合适的时候 push 到云端和提交发行版」，
  Phase 4 的 push/tag/GitHub Release 属该授权范围；删除 worktree/临时对象仍需用户在本
  完整汇报后明确确认（inventory 只列候选，不执行删除）。

## 清场候选（逐对象，未执行删除）

| 对象 | 类型 | 处置判定 | 依据 |
| --- | --- | --- | --- |
| `/tmp/audits/cls-ui/larkdeck` @0bf5952 | git worktree | 用户确认后可 `git worktree remove` | clean；HEAD 可由 `refs/audit/cls-ui/phase1e` 到达 |
| `/tmp/larkdeck-phase1` @aec70aa | git worktree | 用户确认后可 `git worktree remove` | clean；HEAD 由 `refs/audit/cls-ui/phase1` 保留 |
| `/tmp/ldbase/larkdeck` @ac3b25e | git worktree | 用户确认后可 `git worktree remove` | clean；HEAD 是 tag `v0.5.0` 提交 |
| `/tmp/phase2/larkdeck` @0bf5952 | git worktree | 用户确认后可 `git worktree remove` | run1 失败证据已入库（sha `ec35bf55…`） |
| `/tmp/phase2b/larkdeck` @76c0e68 | git worktree | 用户确认后可 `git worktree remove` | run2 证据已入库；逐门禁 stdout 已复制到 `phase-3/evidence/phase2b_gates/` |
| `/tmp/phase2c/larkdeck` @ecf11df | git worktree | 保留到 Release 后，再确认 | run3 external witness 与 Phase 4 可能复跑；meta/exit 已归档 |
| `/tmp/audits/cls-ui-phase3/larkdeck` @ffe53dc | git worktree | 保留到 Phase 3 合并/审计确认后，再确认 | Phase 3 旧快照工作树 |
| `/tmp/audits/cls-ui-phase3b/larkdeck` @68f2a71 | git worktree | 保留到 Phase 3 合并/审计确认后，再确认 | Phase 3b 快照工作树（终版 ref 不新增 worktree） |
| `/tmp/phase2c/fullrun_phase2.{raw,meta,exit,stdout,stderr}` | 外部见证 | 保留到 Release 后 | raw 与 repo log 同 sha；meta/exit 已复制入库 |
| `/tmp/phase2b_gates/*.log` | run2 逐门禁 stdout | 已归档后可确认删除 | 已复制到 `docs/audits/cls-ui/phase-3/evidence/phase2b_gates/` |
| `/tmp/auditC-alltest/{REPORT.txt,results.jsonl,progress.txt}` | 审计 C 原始报告 + **60/381 中途** harness 结果 | 已归档后可确认删除 | 已复制到 `docs/audits/cls-ui/phase-3/evidence/auditC/`；README.md 标注时点与不完整性 |
| `/tmp/auditA`、`/tmp/audit-c*`、`/tmp/auditC-*`、`/tmp/audit_b_*`、`/tmp/audit_parent` 等 | 突变实验过程树 | 待用户确认 | 含非 git 实验 blob，非产品 lane；需逐对象复核 |
| `/tmp/fullrun10b.log`、`/tmp/mutCLS*.log`、`/tmp/phase0-units.log`、`/tmp/probe_header_partial.py` | 历史文档引用的临时文件 | 保留/标注，不纳入本次删除候选 | 被 CHANGELOG / verify-log / 审计文档引用 |
| 根 `.pytest_cache/`、`__pycache__/`、worktree 内 pycache | 可再生缓存 | 用户确认后可清 | 均被 `.gitignore` 覆盖，不在任何 tree |
| 根 `.probe_*.json`（3 个，含真实 35 位 `om_` message_id / card_id） | 本地探针状态 | 待用户确认；**不得提交** | `.gitignore` 覆盖；含真实飞书对象 ID，清场前单独标注 |
| `design/` | 他项目产物（DeepSeek iDesign） | 范围外，不清理 | manifest 显示非 larkdeck；只读提及 |
| `/tmp/audit_b2.png`、`/tmp/audit_bottom.png` | 他项目截图 | 范围外，不清理 | 内容为医药合规表，与 larkdeck 无关 |
| `refs/audit/*`（本地保留，随每个终版 tag 增加） | 本地审计 refs | **保留** | 未 push 的冻结证据；清场前不得删/GC |

⚠️ 禁止把 `/tmp/phase2*` 当作一个 glob 删除：该 pattern 同时命中活 worktree、run3 外部见证、
taghash 和 run2 逐门禁日志。worktree 一律走 `git worktree remove`，删后复核
`git worktree prune --dry-run`。

## 已归档到 repo 的证据

- run3 外部见证副本：`evidence/phase2_run3/fullrun_phase2.meta.txt`、`...exit.txt`。
- run2 逐门禁 stdout：`evidence/phase2b_gates/`。
- 审计 C 原始报告 + **60/381 中途** harness 结果与 progress：
  `evidence/auditC/{REPORT.txt,results.jsonl,progress.txt,README.md}`。
  README 明确 REPORT 是 run2 时点、results 只有 60 条；381 的权威计数在
  `phase-2/manifest.json` 与 run3 日志。
- run3 日志本体：`docs/audits/cls-ui/phase-2/logs/fullrun_phase2_run3.log`
  （sha256 `6cbe3898…`）；`RUN3-ATTESTATION.md` 记录 run id/tree/commit/EXIT。

## 未闭环 / warning

1. `AGENTS.md` 60,803 B / 65,536 = **92.8%**；后续新增规则前必须先压缩。
2. `<font color>` 真机渲染与 header A1→A2 视觉仍 `pending`；Phase 4 硬门禁未过，Release 继续 NO-GO。
3. 运行态未 live verify：网关旧模块；Phase 4 重启后才算 deployed/live verified。
4. `.gitignore` 加固已完成：新增 `.env.*`（保留 `!.env.example`）、`config.yml`、`*.bak`；
   `config.yaml`/`.env`/`*.key`/`*.pem`/`*.log` 原有覆盖不变。
5. Claude Code 当前只加载全局 `~/.claude/CLAUDE.md`，项目级公开仓库/日志红线的可见性有限；
   如后续要用 Claude 改此项目，需补项目 `CLAUDE.md` 或导入（当前平台以 Codex 为主，登记为低风险）。
6. `docs/audits/cls-ui/phase-3/writecheck.tmp`（1 字节 `x`）是本次写入能力检查产生的
   本地残留，**不纳入 closeout 提交**；按删除红线等用户确认后清理。
7. 授权留痕：用户原始请求（2026-09-17）明确包含「提交 commit 并在合适的时候 push 到
   云端和提交发行版（不要攒一堆再提交）」；Phase 4 的 push/tag/Release 在该范围内。
   删除 worktree/临时对象仍需用户看完整汇报后的明确确认。
