# 从 HFC 切到 LarkDeck

给之前装过 [hermes-feishu-streaming-card](https://github.com/baileyh8/hermes-feishu-streaming-card)（下称 HFC）的人：两者不能共存，必须先卸载 HFC，再装 LarkDeck。

顺序不能反。反过来做，会有一段时间两个插件同时抢 `feishu` 平台。

## 为什么不能共存

- HFC 改写 Hermes 源码：往 `gateway/` 与 `cron/` 的 8 个文件里注入代码。
- 两者都占用 `feishu` 平台名，HFC 的注入还会在平台分发之前拦消息。
- LarkDeck 是纯插件叠加层，不改 Hermes 源码；只要 HFC 的注入还在，两边就会互相打架。

## 切换步骤

```bash
# 1) 用 HFC 自带 CLI 卸载（不要只用 pip uninstall，它不会回滚源码注入）
hermes-feishu-card cli uninstall --hermes-dir ~/.hermes/hermes-agent --yes

# 2) 确认注入已回滚：下面两个目录不应再出现 hermes_feishu_card
grep -rl hermes_feishu_card ~/.hermes/hermes-agent/gateway ~/.hermes/hermes-agent/cron

# 3) 安装 LarkDeck
git clone https://github.com/zayn-0101/LarkDeck.git
cd LarkDeck && ./install.sh

# 4) 编辑 ~/.hermes/config.yaml：plugins.enabled 里删掉 hermes-feishu-card、加上 larkdeck

# 5) 重启网关，确认日志出现 [larkdeck] 启动自检通过

# 6) 注入回滚并确认无误后，再卸载 Python 包
pip uninstall hermes-feishu-streaming-card
```

`hermes-feishu-card` 的具体子命令以 `hermes-feishu-card --help` 为准。安装与验证的完整步骤见 [INSTALL.md](../../INSTALL.md)。

**回滚**：卸载前应留有备份（如 `~/.hermes/backups/hfc-<版本>-<时间戳>/`）；没有备份时可按原版本 `pip install hermes-feishu-streaming-card==<版本>`，再 `hermes gateway restart`。

**注意 cron 投递**：如果有任务借用 HFC 的卡片投递能力，卸载后它们会退化为纯文本（不报错、不丢消息）。要保留卡片，把这些任务改接到 LarkDeck。

## NAS / 容器

如果 HFC 是通过第三方 bootstrap 脚本在容器重建时自动装回来的，必须先处理脚本，否则每次重建都会重新装回：

1. 备份该脚本（例如宿主上的 `.../scripts/hermes-scheme2-bootstrap.sh`），再把它移出启动路径。
2. 停容器，在容器内用 HFC 的 CLI 卸载一次，回滚源码注入。
3. 重建容器。容器内 `/opt/hermes` 属于镜像层，只有重建才能回到干净的原版文件。
4. 把 LarkDeck 放到挂载卷（`HERMES_HOME/plugins/larkdeck`），改 `config.yaml` 的 `plugins.enabled`。
5. 重启容器，确认自检通过。至少经历一次网关重启和一次容器重建后，再删除备份的 bootstrap 脚本。

排查见 [故障排查](troubleshooting.md)。
