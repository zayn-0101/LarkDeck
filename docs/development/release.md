# 发布流程

> 读者：维护者与执行发布的 AI coding agent。
> 结论先行：版本号唯一来源是 `plugin.yaml`；发布是本地固定顺序的脚本动作，没有 CI 代劳。

## 版本与变更记录

1. 只改 `plugin.yaml` 的 `version:`。README、CHANGELOG、release note 都不维护第二份版本常量。
2. `CHANGELOG.md` 按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 倒序维护：
   - 标题格式 `## [X.Y.Z] - YYYY-MM-DD 一句话主题`；
   - 只写**用户可见**的变化，一条一句话；证据和门禁数字放 `docs/releases/` 发布说明或
     内部归档，变更日志不展开过程细节；
   - 行为或默认值变化必须同提交更新 `README.md`、`plugin.yaml` 的 `config_schema` / 描述、
     CHANGELOG 三处；
   - 文档守卫 `tests/check_docs.py` 会检查 CHANGELOG 顶部版本与 `plugin.yaml` 一致。
3. 新建 `docs/releases/vX.Y.Z.md`，建议沿用已有结构：这一版解决什么 / 行为细节 /
   兼容与回退 / 门禁与证据 / 安装升级 / 发布结果。发布后把 tag、部署结果与真机验收补进去。

## 发布前检查清单

1. [ ] `plugin.yaml` 版本已升到本轮 tag，工作树没有未提交改动。
2. [ ] `CHANGELOG.md` 顶部是本版本，release note 已写好且安装 / 回退两步可照抄执行。
3. [ ] `$PY tests/run_fast.py --full` 恰好 9 步全 `[OK]`。
4. [ ] 改过断言：跑过 `tests/mutate_check.py --delta`；合并 / 变基 / 大改：跑过全量变异；
       验证账本无待跑条目。
5. [ ] 文档改动跑过 `tests/check_docs.py`（已并入第 3 步第 1 项）。
6. [ ] `git status` 干净，`origin/main` 是本地 HEAD 的祖先（可 fast-forward），`gh auth status`
       已登录。
7. [ ] `git tag -l vX.Y.Z` 为空；同名 tag 已存在时人工确认，脚本不覆盖。
8. [ ] `.deploy` 工作树干净，且已指向本次要发布的提交；用户真机终验已在同一提交上完成。
9. [ ] 没有提交凭据、`config.yaml`、`.env` 或可能含真实 ID / 凭据的日志。

## 执行发布

发布日执行器（默认 dry-run，只有 `--go` 才写远端和部署树）是**维护者本地脚本**，保存在
本机归档里，不随仓库发布；新版本复制后改 tag、提示语与必要路径即可。没有本地脚本时，
按下面的等价命令手工执行。

```bash
export TAG=vX.Y.Z
```

发布脚本按以下顺序执行，任何一步判据不满足就停：

1. **门禁与账本**：`tests/run_fast.py --full` 恰好 9 步 OK，变异锚点预检通过，验证账本
   达到全覆盖、无待跑条目。
2. **tag 与 push**：`git tag -a "$TAG" -m "<发布主题>"`，推送 `main` 和 tag。
3. **GitHub Release**：`gh release create "$TAG" --title "LarkDeck $TAG" --notes-file docs/releases/$TAG.md`。
4. **`.deploy` 指向 tag**：部署 worktree（Git 工作树）checkout 到 tag 对应提交，并断言 HEAD 一致。
5. **重启网关 + 自检**：`hermes gateway restart`，在 150 秒上限内等到一条**晚于重启时刻**的
   `启动自检通过` 日志，并确认网关进程仍在。
6. **live 核对**：请用户发一条真实消息，或跑一次 `tests/probe_render.py`，确认卡片形态。

手工等价命令（每一步都要先确认上一步成功）：

```bash
git tag -a "$TAG" -m "LarkDeck $TAG"
git push origin main
git push origin "$TAG"
gh release create "$TAG" --title "LarkDeck $TAG" --notes-file "docs/releases/$TAG.md"

git -C .deploy checkout -q "$(git rev-parse "$TAG^{commit}")"
git -C .deploy status --porcelain=v1     # 必须为空
hermes gateway restart
tail -n 50 ~/.hermes/logs/agent.log      # 找最新一条「启动自检通过」
pgrep -f "hermes_cli.main gateway run"   # 进程必须在
```

部署拓扑：开发树是 `~/Code/larkdeck`；`.deploy` 是同一仓库的一个 git worktree（Git 工作树）；
`~/.hermes/plugins/larkdeck` 软链到 `.deploy`，所以线上跑的是部署树，不是开发树。
NAS / 容器用 `./install.sh --copy`，其 `FILES` 清单手写，新增运行模块时必须同步。

## 回滚

按代价从低到高：

1. **只回退运行版本**（最快）：

   ```bash
   git -C .deploy checkout -q v<上一个 tag>
   hermes gateway restart
   ```

   重启后确认 `启动自检通过`，再发一条真实消息；`/larkdeck status` 应报回退后的版本与传输。

2. **回退安装软链**：如果发布时改过软链，可用部署脚本恢复备份：

   ```bash
   tools/setup_deploy_worktree.sh --rollback
   hermes gateway restart
   ```

3. **已发布的 tag / Release 不回写**：不要覆盖已推送的 tag。修复走新的补丁版本；
   确需删除远端 tag 或 Release 时，先说明范围、影响与回滚点，再执行破坏性命令。

回滚判据：网关起来后自检通过、`/larkdeck status` 的版本与传输符合预期、真实回合卡片
能正常流式收尾。三点缺一都算没回滚成功。
