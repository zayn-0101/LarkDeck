# LarkDeck v0.7.1 V0 统一结论（config 登记 + token 冻结 + 门禁基础设施）

- 计划：`docs/plan-v0.7.1-visual.md` §9，v2.2 SHA256
  `cee0715305149f76b17a2f841a57a3a8197817eac2932dc998fd4bbdb00d2477`
  （仅把 `run_fast` 实测耗时修正为默认 ~7.5s / `--full` ~30s）。
- **V0 审计对象（最终）**：tree `22e465fd6a937e97dc662471cbb38cba097faae8`
  （代码+文档，不含 `freeze-V0.json` 自身；导出
  `/private/tmp/audit-v0-22e465fd6a937e97dc662471cbb38cba097faae8/larkdeck`）。
- V0 代码提交：`aaeb6c1`（config 键、token 表、check_cardview、mutate 分片、freeze_tree）；
  后续为 metadata-only 提交（manifest/consensus）。
- 最终 manifest：`docs/audits/v0.7.1-visual/freeze-V0.json`（metadata commit `b2e0d99`），
  记 `tree=audit_target_tree=export tree=22e465fd…`、`worktree_commit=clean`、
  plan v2.2 SHA、参考源哈希、gate_logs；审计 tree 不含 manifest 文件 ⇒ 无自指。
- 旧配对（`e5ad3a`/`1d1929`）按 §9.8 元数据豁免归档，不再作为审计对象。

## 交付物
- 三配置键：`visual_engine=legacy`、`card_status_header=true`、`show_reasoning=false`；
  `_DEFAULTS`/plugin.yaml/README/AGENTS/CHANGELOG/check_override 同步；生产入口读取，
  非默认/未实现值 WARNING（300s/键限流）；`/larkdeck config` 标“已登记，V1–V4 才生效”。
- `docs/audits/v0.7.1-visual/visual-tokens.json`：面板 token、14 个 CLS 图标 token、
  状态色、footer 顺序、锚点、未验证项；`body_text_size_pending_probe` 待窗口 0 真机探针。
- `tests/check_cardview.py`：token/状态/图标/锚点字面量 + 生产 `_TOOL_STATUS_STYLES` 同源核对 +
  legacy entity card element id/content 逐字；已接入 `mutate_check._run_gates` 与 `run_fast --full`。
- `tests/mutate_check.py`：`--shard i/n`、`--list`、`--inventory`；`-k` 未命中 exit 2；
  空分片合法 exit 0；inventory 带 shard/filter/totals；V0-1..17 定向变异。
- `tools/freeze_tree.py`：`git write-tree` + `git stash create` worktree_commit +
  staged/unstaged/untracked 哈希 + gate_logs + 非 git 干净报错。
- `tests/run_fast.py`：默认三件/`--full` 七门禁；`PYTHONDONTWRITEBYTECODE=1`。

## 证据
- `run_fast --full`：7/7 绿，`test_units 243/243 passed`。
- `mutate_check --preflight`：417/417 锚点可用（410 变异 + 7 对照）。
- `mutate_check -k V0-1` … `-k V0-17`：全部断言红并留日志
  （V0-5/6/7/15/16/17 = 生产调用点删除；V0-1/2/3/10 = 配置告警/pending_visual；
  V0-4/8/9/13/14 = token 漂移；V0-11/12 = 生产状态色/删键）。
- 默认 legacy 行为逐字节不变：`golden_cardkit_trace.json` 与 V0 前一致；
  显式默认三键下 runtime == golden；check_hooks 生产路径黄金序列绿。

## 三方审计
- A2（代码/架构）：**GO**（配对闭环；freeze_tree/check_cardview/生产调用点条件闭合）。
- B2（用户可见/回归）：**GO**（默认 legacy 逐字节不变；README/config no-op 标注闭环；
  外部条件仅部署 worktree、窗口 0 探针）。
- C2（反假绿）：F2/F3 已闭环（删生产状态键 = AssertionError；freeze_tree 非 git exit 2 +
  worktree/unstaged/untracked 哈希）；最终配对条件在 manifest-free tree `22e465fd` 上闭环
  （manifest.tree == export tree，无自指），待其最后一行确认。

## 外部条件（用户决定，非代码阻断）
1. 独立部署 worktree + 软链重指：`~/.hermes/plugins/larkdeck` 当前指开发树，任何重启前必须处理
   （推荐 `/Users/Zayn/.hermes/deploy/larkdeck`；沙箱不允许父代理新建目录）。
2. 窗口 0：`normal` vs `normal_v2` 探针卡 1 张（独立进程、不重启），人眼判读后写回 token。
3. 网关重启/回滚时机：当前 detached PID 95960；按 §9.1 checklist，经用户同意后执行。
