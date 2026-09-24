# 安装与升级 LarkDeck

本文面向首次安装、升级、卸载或回滚 LarkDeck 的 Hermes 用户：用最短路径把插件接到官方飞书 / Lark 适配器，并验证到第一张卡片。

## 环境要求

- **Hermes Agent 0.21.x**，`hermes --version` 能正常输出；已在 0.21.1 和 0.21.4 验证。
- 官方 `feishu` 平台在同一进程启用。LarkDeck 是叠加层，官方平台不可用时不会生效。
- 一个飞书 / Lark 机器人应用（App ID + App Secret），已开启机器人能力。
- macOS / Linux，或 NAS / 容器；安装需要 `bash` 和 `git`。不需要额外 Python 包，LarkDeck 复用 Hermes 的 Python 环境。

### 准备机器人凭据

推荐用 Hermes 官方向导，它会写入 `~/.hermes/.env` 并配置连接方式：

```bash
hermes gateway setup
```

选择飞书 / Lark，按提示扫码创建或手动输入 App ID 与 App Secret。国际版把 `FEISHU_DOMAIN` 设为 `lark`。生产环境建议同时设置 `FEISHU_ALLOWED_USERS` 白名单。手动创建应用的入口在飞书开放平台或 Lark 开放平台；步骤以 Hermes 官方飞书配置文档为准。

## 安装

`install.sh` 只做三件事：把插件放进 `$HERMES_HOME/plugins/larkdeck`、校验运行文件清单、打印下一步。它不会删除或覆盖任何已有目标。

### 路径一：一行脚本

```bash
git clone https://github.com/zayn-0101/LarkDeck.git larkdeck && cd larkdeck && ./install.sh
```

默认是 `--link`（软链到当前仓库，改代码立即生效）。NAS / 容器不想留软链，把末尾换成 `./install.sh --copy`。

### 路径二：手动 git

```bash
git clone https://github.com/zayn-0101/LarkDeck.git larkdeck
cd larkdeck
./install.sh --help          # 查看当前版本支持的参数
./install.sh                 # 开发机：软链（等同 --link）
./install.sh --copy          # NAS / 容器：复制运行文件
```

可用参数只有 `--link`、`--copy`、`--target <dir>`、`-h/--help`。自定义 Hermes 目录用 `HERMES_HOME=/path/to/.hermes ./install.sh --copy`。脚本检测到目标已存在时会停下，请你自行处理。

### 路径三：交给 AI Agent

把下面整段发给能操作这台机器的 Agent：

```text
请安装 LarkDeck（Hermes 的飞书卡片插件），遵守以下步骤：
1. 先确认 `hermes --version` 输出 0.21.x；不满足就停下来告诉我。
2. `git clone https://github.com/zayn-0101/LarkDeck.git /tmp/larkdeck`。目录已存在就换一个，不要删除已有目录。
3. `cd /tmp/larkdeck && ./install.sh`。NAS / 容器改用 `./install.sh --copy`；不要加其他参数。
4. 编辑 `~/.hermes/config.yaml`：把 `larkdeck` 加进 `plugins.enabled` 列表，保留原有内容；改前备份文件。
5. 运行 `hermes gateway restart`。
6. 查看网关日志（通常是 `~/.hermes/logs/agent.log`）里最后一行以 `[larkdeck] 启动自检` 开头的记录；失败就把原文发给我，不要删除 `~/.hermes/plugins/larkdeck`。
7. 汇报：安装模式、插件目录、自检结果；如果开启了 `plugins.stream_reasoning_deltas: true`，也说明。
```

## 启用插件

编辑 `~/.hermes/config.yaml`，把 `larkdeck` 追加进已有列表（不要新建一个 `plugins:` 覆盖原来的）：

```yaml
plugins:
  enabled:
    - larkdeck
  stream_reasoning_deltas: true   # 可选：想看到第 N 轮思考（round）时开启
```

保存后重启网关：

```bash
hermes gateway restart
```

插件模块不会热重载；不重启就还在跑旧代码。

## 验证

1. 插件目录已就位：

   ```bash
   ls -ld "${HERMES_HOME:-$HOME/.hermes}/plugins/larkdeck"
   ```

   开发机应该是软链，NAS / 容器应该是真实目录。

2. 启动日志有自检通过：

   ```bash
   grep '\[larkdeck\] 启动自检' ~/.hermes/logs/agent.log | tail -1
   ```

   预期（版本、Hermes 版本与钩子列表随环境变化）：

   ```text
   [larkdeck] 启动自检通过：Hermes 0.21.x · feishu 平台已由 larkdeck 接管 · native 传输 cardkit · 钩子 post_api_request/on_stream_start/on_stream_delta/on_stream_end/pre_tool_call/post_tool_call/pre_gateway_dispatch/on_session_end · /larkdeck 命令已注册
   ```

3. 在飞书里私聊机器人 `/larkdeck status`，预期收到自检卡，首行类似：

   ```text
   🃏 larkdeck v<当前版本> · 传输 cardkit · 钩子 8/8 已挂
   ```

   卡片还会给出聚合诊断、能力探测与六条进程级记录；出现 `⚠️` 时按卡片读数处理。

4. 给机器人发一句“你好”。预期只有一张卡片，回答在原地流式出现；回合结束后面板边框变绿，页脚显示状态、耗时、模型与上下文用量。

自检失败时日志会打 `ERROR`，飞书仍由官方适配器以纯文本工作，不会把消息弄坏。处理见 [故障排查](docs/guide/troubleshooting.md)。

## 升级

先确认安装方式：`ls -ld ~/.hermes/plugins/larkdeck` 是软链就是 `--link`，是目录就是 `--copy`。

```bash
# 软链安装
git -C <仓库目录> pull && hermes gateway restart
```

```bash
# 复制安装：README 的升级命令是 git pull 后重跑 ./install.sh --copy；
# 当前脚本不覆盖已有目标，所以要先备份并移开旧目录，再重跑。
git -C <仓库目录> pull
mv ~/.hermes/plugins/larkdeck ~/.hermes/plugins/larkdeck.bak-$(date +%Y%m%d%H%M%S)
cd <仓库目录> && ./install.sh --copy
hermes gateway restart
```

升级不会动 `~/.hermes/config.yaml`。升级后按上面的“验证”再走一遍。

## 卸载

```bash
# 1) 从 ~/.hermes/config.yaml 的 plugins.enabled 里删掉 larkdeck
# 2) 备份后移除插件目录
mv ~/.hermes/plugins/larkdeck ~/.hermes/plugins/larkdeck.removed-$(date +%Y%m%d%H%M%S)
hermes gateway restart
```

目录是软链时，`mv` 只移走链接本身，源仓库不受影响。确认不再需要后，自行删除备份即可。LarkDeck 没有改写 Hermes 源码，卸载后不存在注入残留。

## 回滚

按已发布的 tag 回退，再重启网关：

```bash
git -C <仓库目录> fetch --tags
git -C <仓库目录> checkout <tag>
hermes gateway restart
```

复制安装回滚时，checkout 后在仓库目录重跑 `./install.sh --copy`（先按“升级”一节备份并移开旧目录），再重启网关。回滚只改插件代码；`~/.hermes/config.yaml` 里的 LarkDeck 配置保留，新版本新增的键会被旧版忽略。

## 常见安装错误

| 症状 | 根因 | 修复 | 预防 |
|---|---|---|---|
| `目标已存在：...` | 之前装过，或目标目录 / 软链残留 | 备份后自行移走目标再重跑；脚本不会替你删 | 安装前先 `ls -ld ~/.hermes/plugins/larkdeck` |
| `目标已是软链，指向 ...` | 软链指向另一个仓库副本 | 确认要保留哪一份，自行移除旧软链后重跑 | 一台机器只保留一份安装 |
| `Permission denied` / 无法创建目录 | `$HERMES_HOME` 不属于当前用户，常见于曾用 sudo 安装 | 修正目录属主，或用 `HERMES_HOME` 指向可写目录；不要用 sudo 跑 `install.sh` | 用运行网关的同一个用户安装 |
| `install.sh 的 FILES 清单与仓库实际运行文件不一致` | 仓库多了 / 少了运行模块，或改过 `install.sh` | 用干净 clone 安装；开发者在同步文件清单后重试 | 不要从正在手工改运行文件的目录安装 |
| 启动日志：`没有找到内置 'feishu' 适配器工厂` | 官方 `feishu` 未启用，或未在同一进程注册 | 启用官方飞书平台并重启网关；确认 Hermes 是 0.21.x | 不要让其他插件禁用或抢占 `feishu` |
| Hermes 0.21.4 下插件加载超时、飞书退回纯文本 | 旧版插件与延迟平台加载流程互等 | 升级 LarkDeck 到当前版本并重启网关；不要用 `plugins.load_timeout_seconds: 0` 绕过 | 跟随当前发布版本 |
| 装了插件但回复还是纯文本 | 没重启网关，或 `plugins.enabled` 漏加 `larkdeck` | 补配置后 `hermes gateway restart`；用 `/larkdeck status` 确认已接管 | 每次改启用项后重启并验证 |
| `/larkdeck` 敲了没反应 | 老版 Hermes 没有命令注册接口，或命令是在生成回答期间发的、被排队 | 等回合结束再试；CLI / TUI 里可直接执行。仍不行就看自检卡里的命令注册说明 | 升级 Hermes 与 LarkDeck |
| 配置文件改了但行为没变 | 改的是官方文件，插件进程仍是旧值；启用项和代码升级本来就需要重启 | 在飞书发 `/larkdeck config` 看来源与提示，再 `/larkdeck config reload`；启用项改动重启网关 | 改完先 reload，再确认生效值 |

更多卡片、点击与显示问题见 [故障排查](docs/guide/troubleshooting.md)。
