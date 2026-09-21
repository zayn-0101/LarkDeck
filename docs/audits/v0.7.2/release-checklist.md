# v0.7.2 发布收尾清单（P6）

> 状态：**候选已部署待用户终验**。按 `docs/plan-v0.7.2.md` §2 的「P6 全量门禁范围」执行。
> 顺序固定：**用户终验 → 全量门禁 → 全量/分片变异 → push + tag + release → `.deploy` 指向 tag
> 提交 → 重启网关 → live 核对**。任何一步红都不进下一步。

## 0. 前置（已完成的部分）

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 代码冻结在候选提交 | ✅ | `git rev-parse --short HEAD`（`6797cb8`，代码与 `a3cf1ef` 同源；其后仅文档） |
| 六支门禁 | ✅（本轮多次） | `run_fast.py --full`：全 OK，合计 ~10.4s |
| 阶段审计（A/B/C/C2） | ✅ | `docs/audits/v0.7.2/audit-round1.md`（含绿变异与收口对照） |
| 真机探针 | 部分 | 加载指示「① 会动」（用户目视）；图标定版卡 `om_x100b64256c1470acdfadc4d33133fca` |
| 候选部署 | ✅ | `.deploy` = `a3cf1ef`，网关已重启，`启动自检通过`（2026-09-21 19:43:28） |

## 1. 用户终验（唯一待办）

请用户确认四件事：

1. 工具行 emoji 与文字对齐（定版确认卡 `om_x100b64256c1470acdfadc4d33133fca`）；
2. 工具步数 > 20 的长回合：卡片不掉成纯文本、无「⏳ Working —」灰气泡、折叠提示在；
3. 页脚 = `状态 · ⏱ 时长 · 🤖 模型 · ctx`，**没有 🔖**；
4. 建卡瞬间的加载指示：会动、无文字，首字到达即消失。

## 2. 全量门禁（六支，串行）

```bash
PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
cd /Users/Zayn/Code/larkdeck
$PY tests/run_fast.py --full          # 六支 + own_body + mutate --preflight
# 或逐支：
for g in test_units check_override check_hooks check_clarify_e2e check_cardview \
         check_cls_alignment; do $PY tests/$g.py; done
```

判据：六支全绿（`check_cls_alignment.py` **必须**在位运行，缺席即 FAIL）。

## 3. 变异验证（**增量优先**；协议见 `docs/verify-log.md`「09-21 协议变更」）

```bash
$PY tests/mutate_check.py --preflight              # 0.2–0.8s，先对锚点
$PY tests/mutate_check.py --ledger-status          # 看覆盖率（目标：待跑 0 条）
$PY tests/mutate_check.py --delta --update-ledger  # 只跑「区域变过 / 新增」的（秒~分钟级）
# 若基线里没有可继承的全绿记录（`_meta.full_audit_at` 为空/过期），后台补一次全量直跑：
$PY -u tests/mutate_check.py --shard 1/2 --target-only --update-ledger > /tmp/f1.log 2>&1 &
$PY -u tests/mutate_check.py --shard 2/2 --target-only --update-ledger > /tmp/f2.log 2>&1 &
# 两片跑完后合并账本，并把两片里的 🟢（target-only 判绿不算证据）用完整模式复核：
$PY tests/mutate_check.py --delta --update-ledger   # 绿的仍未被记账 ⇒ 会被这条自动挑出来
```

判定：`🟢` = 断言没判别力；`⚪` = 变异没生效；`💥` = 只有崩溃、不算证据；`❓` = 锚点脱节。
四种都必须清零（对照项除外）才算这一版验完。分片上限 2（4 分片无收益且会引入负载假红）。

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
git -C .deploy checkout -q "$(git rev-parse v0.7.2^{commit})"
git -C .deploy log --oneline -1        # 必须 = tag 提交
/Users/Zayn/.hermes/hermes-agent/venv/bin/hermes gateway restart
```

## 6. live 核对（重启后 90s 内有界等待，不 sleep）

```bash
pgrep -f "hermes_cli.main gateway run"
grep -n "启动自检通过" ~/.hermes/logs/agent.log | tail -1
grep -n "feishu connected\|已连接\|ws.*connected" ~/.hermes/logs/agent.log | tail -2
```

判据：进程在、自检行时间戳晚于重启时刻、飞书 WS 已连。随后请用户发一条真实消息复看卡片
（这一步属于 live verified，不能拿「进程在」代替）。

## 7. 收尾（neat-freak 第二阶段）

* 清场对象（**待用户确认**，未确认前一个都不动）：
  * 8 个 `/tmp` 下的审计 worktree（`git worktree prune` + 删目录；其中若干仍存审计证据日志）；
  * `docs/audits/cls-ui/phase-*/**.log`（已被 `*.log` 忽略，本地留档）；
  * 本地 probe 状态文件 `.probe_*.json`（`gitignore` 已覆盖，可留）。
* 记忆：本项目规则未授权写记忆 ⇒ 标 `generated-read-only / not-applicable`。
