# P7 发布 runbook（v0.7.2 面板 UX 批次）—— 只剩这几步

**前置状态（2026-09-22 收尾时）**：`HEAD=ca111b8`（树干净）· `.deploy=15d0e9f`（已部署、网关自检通过）·
账本 `tests/mutation-verdicts.json`：`full_audit_at=adea7cb`、499/499 red-assert、继承 0 ·
发布前检查（`release-v0.7.2.py --check`）除 `1c`（`.deploy` 未指最终提交）外全绿。

**唯一未完成的人肉格**：真实回合卡最底部的页脚（`✅ 已完成 · X.Xs · 模型名 · ctx …`）。
未验前不得写成「已验证」。

> ⚠️ 动手前先确认发布参数：`/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
> ~/.larkdeck-scratch/release-v0.7.2.py --check`（只读）。它会跑一遍 `run_fast --full` +
> 账本三查 + 合并器现场重算，**任何一项红都不要继续**。

## 命令（顺序固定，逐条看输出）

```bash
PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
cd /Users/Zayn/Code/larkdeck

# 0) 最终确认（只读）：门禁 8/8 · 锚点 511/511 · 账本 499/499 · 1a/1b 绿
$PY ~/.larkdeck-scratch/release-v0.7.2.py --check

# 1) 把 .deploy 指到**最终提交**并重启网关（有界等待「启动自检通过」）
$PY ~/.larkdeck-scratch/v0.7.2-panelux/deploy_probe.py

# 2) 发布（push main + tag v0.7.2 + gh release + .deploy 指 tag + 重启自检）
$PY ~/.larkdeck-scratch/release-v0.7.2.py --go

# 3) 收尾（文档/规则/残留与代码现实对齐 = neat-freak 口径）
#    · 把发布时间线（tag/commit/时间/门禁读数）追加进 docs/verify-log.md
#    · 计划 §7 的 P6/P7 行改成「已收敛」，把「真实回合页脚」那一格的**真实状态**写清
#    · git commit + push（文档提交不影响账本：发布脚本会核「自 full_audit_at 起指纹路径未被修改」）
```

## 发布脚本会挡住的几件事（故意的，别绕过）

| 检查 | 挡什么 |
| --- | --- |
| 0) 工作树必须干净 | 边改边发 |
| 1) `run_fast --full` 恰好 8 步全 OK | 门禁没跑全 / 被改名 |
| 1) 账本 `499/499 可跳过、待跑 0` | 拿旧账本当证据 |
| 1a) `full_audit_at` 是 HEAD 祖先 + **指纹路径未被修改** + 继承 0 | 用别的树上的结论充数 |
| 1b) `merge_ledger4` 现场重算三指纹 + 日志覆盖 + 12 名对照全绿 | 伪造日志/缺口没补 |
| 1c) `.deploy == HEAD` | **先发布、后部署**（顺序反了等于发一个没在真机验过的提交） |

## 发布后的真机核对（1 分钟）

* 重启后日志里应出现 `[larkdeck] 启动自检通过：…`；
* 发一条真实消息 ⇒ 卡片最底部有页脚、面板运行中展开（动图 + 蓝 `Running`）、收尾折叠（`✓` 绿）；
* 把这一格的结论回填 `docs/verify-log.md`（这就是「真实回合页脚」那格的收口）。
