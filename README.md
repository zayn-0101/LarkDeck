# LarkDeck

> **飞书里的回答会像打字机一样实时写出来，思考与工具调用收在卡片底部，随时展开。**
> LarkDeck 是基于飞书 CardKit 2.0 的 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 插件：不改 Hermes 源码，回答、过程与澄清交互都在同一张卡里完成。

[![version](https://img.shields.io/badge/version-0.7.9-blue.svg)](https://github.com/zayn-0101/LarkDeck/releases)
[![AH (Hermes Agent) 0.21.x](https://img.shields.io/badge/AH-0.21.x-blueviolet.svg)](https://github.com/NousResearch/hermes-agent)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[中文](README.md) | [English](README.en.md)

## 核心能力

- **一条回复一张卡**：回答还没出第一个字，卡片就先建好；正文逐字显示，工具进度不再单独刷消息。
- **过程面板（panel）**：推理与工具调用合并到卡片底部，可折叠；按第 N 轮思考（round）分段计时。
- **澄清交互卡（clarify）**：下拉、多选、输入框或按钮直接作答，不用手打选项。
- **状态与用量**：完成 / 失败 / 中止对应绿 / 红 / 黄边框；页脚显示耗时、模型与上下文用量。
- **双语界面**：卡片界面文案跟随飞书客户端语言；AI 生成的正文不翻译。
- **安全兜底**：卡片、传输或流式任一步失败，自动改用官方纯文本或编辑消息，消息不会丢。

## 快速开始

前置：Hermes Agent 0.21.x 已安装并运行，飞书 / Lark 应用凭据已配置（应用创建见[安装指南](INSTALL.md)）。

安装（默认软链到本仓库；NAS / 容器加 `--copy`）：

```bash
git clone https://github.com/zayn-0101/LarkDeck.git
cd larkdeck
./install.sh
```

启用插件，编辑 `~/.hermes/config.yaml`：

```yaml
plugins:
  enabled:
    - larkdeck
```

重启网关：

```bash
hermes gateway restart
```

验证：在飞书里给机器人发送 `/larkdeck status`，收到自检卡即接管成功：

```
🃏 larkdeck v<当前版本> · 传输 cardkit · 钩子 8/8 已挂
```

启动日志里会出现一行 `[larkdeck] 启动自检通过`。自检失败会打 `ERROR`，并保持官方适配器原样工作：卡片不生效，但飞书不会被弄坏。

升级：软链安装执行 `git pull && hermes gateway restart`。`--copy` 安装先 `git pull`，再把旧目录移开、重跑 `./install.sh --copy`，最后重启网关；脚本不会覆盖已有目标，完整命令见[安装指南](INSTALL.md#升级)。模块不会热重载，必须重启网关。

卸载：从 `plugins.enabled` 删除 `larkdeck`，再删除 `~/.hermes/plugins/larkdeck/`。插件没有改写 Hermes 源码，不存在残留注入。

## 功能一览

| 能力 | 用户得到什么 |
|---|---|
| 单卡流式 | 一轮回复只有一张主卡；正文逐字显示，工具进度合入同卡 |
| 过程面板 | 推理正文与工具步骤收在底部，按轮分段计时，可手动展开 / 收起 |
| 工具详情 | 每步显示动作、参数预览、耗时与状态；明显凭据片段自动打码 |
| 澄清交互卡 | 在卡片上直接选择或输入答案；成功原卡更新，失败弹提示且不覆盖已确认状态 |
| 回合状态 | 完成绿边 / 失败红边 / 中止黄边；页脚含耗时、模型与上下文用量 |
| 可选指标 | 页脚可再加缓存命中率、本回合 API 次数与首字节延迟 |
| 长回答分卡 | 单卡装不下时自动封卡续写，只写剩余内容，切点避开代码围栏 |
| 正文排版 | 清理游离的 `**`、把 H1–H3 降级为加粗，避免卡片里出现夸张大字 |
| 双语界面 | 卡片界面文案跟随客户端语言；AI 正文保持原文 |
| 安全兜底 | 任一步失败都改用官方纯文本或编辑消息，消息与内容不丢 |
| 自检命令 | `/larkdeck status` 随时查看接管状态与写卡失败记录 |

## 配置概览

`~/.hermes/config.yaml` 的最小示例：

```yaml
plugins:
  stream_reasoning_deltas: true   # 想看推理正文时开启；Hermes 默认关闭
  entries:
    larkdeck:
      settings:
        cards: true               # 总开关；关掉完全退回官方纯文本
        show_reasoning: auto      # 默认跟随 Hermes 的 /reasoning on|off
        streaming_print_ms: 15    # 打字机速度；0 关闭
        unified_panel: true       # 推理与工具合并为一个底部面板
```

环境变量 `LARKDECK_<KEY>` 可临时覆盖配置（如 `LARKDECK_CARDS=0`）。优先级：环境变量 > `config.yaml` > 默认值。

全部配置项、默认值、类型与场景示例见[配置参考](docs/guide/configuration.md)。查看本进程生效值用 `/larkdeck config`；改完文件用 `/larkdeck config reload` 热刷新。

## 常用命令

| 命令 | 作用 |
|---|---|
| `/larkdeck status` | 版本、生效传输、钩子、写卡与错误记录；没有记录时如实显示 |
| `/larkdeck config` | 只读查看本进程生效配置与来源 |
| `/larkdeck config reload` | 从 Hermes 配置重读；任一键失败则整次取消 |
| `/larkdeck help` | 命令用法 |
| `/reasoning on\|off` | Hermes 命令：开关推理正文；默认 `show_reasoning: auto` 约 1 秒内跟随 |

详细说明见[命令](docs/guide/commands.md)。

## 兼容与限制

- **环境**：面向 Hermes Agent 0.21.x（已在 0.21.1 / 0.21.4 上验证），且官方 `feishu` 平台必须在同一进程可用；与同样接管 `feishu` 或改写 Hermes 源码的插件不能共存。
- **失败时的行为**：卡片只是增强层。卡片发送、流式或交互任一步失败，会自动改用官方纯文本或编辑消息；那一刻没有卡片样式或动画，但消息不会丢。
- **客户端差异**：客户端不支持卡片 2.0 能力时，部分组件可能不渲染或降级；卡片界面文案跟随客户端语言，AI 正文与部分 markdown 文案（工具动作 / 状态词等）语言固定。能力边界与替代形态见[卡片能力](docs/guide/card-capabilities.md)。
- **推理正文**：需要 Hermes 开启 `plugins.stream_reasoning_deltas`（默认关闭）；未开启时过程面板只显示工具步骤，`/larkdeck status` 会说明原因。
- **命令时机**：在飞书网关里，生成回答期间发送的命令会排队到回合结束；CLI / TUI 中会立即执行。
- **统计口径**：页脚指标与 `/larkdeck status` 的记录是进程级累计，多会话并发时不区分会话；超长回答分卡后，`/stop` 只重绘最新一张卡。

## 文档

| 文档 | 用途 |
|---|---|
| [安装指南](INSTALL.md) | 安装、升级、卸载与回滚 |
| [快速开始](docs/guide/quickstart.md) | 五分钟发出第一张卡片 |
| [配置参考](docs/guide/configuration.md) | 全部配置项、默认值与示例 |
| [命令](docs/guide/commands.md) | 命令与参数说明 |
| [卡片能力](docs/guide/card-capabilities.md) | 支持与不支持的卡片能力 |
| [故障排查](docs/guide/troubleshooting.md) | 症状 → 根因 → 排查 → 修复 |
| [架构](docs/development/architecture.md) | 模块分层、钩子与传输设计 |
| [版本说明](docs/releases/README.md) | 每个版本的完整变更与说明 |
| [更新日志](CHANGELOG.md) | 用户可见变更列表 |
| [贡献指南](CONTRIBUTING.md) | 开发环境、测试与提交流程 |
| [许可证](LICENSE) | MIT 许可证 |

## 致谢

LarkDeck 在设计与实现过程中，受到了社区中多个优秀飞书 / Lark 卡片项目的启发，在此谨向以下项目及其作者致谢：

- [Cheerwhy/hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming)
- [Aowen-Nowor/hermes-lark-streaming](https://github.com/Aowen-Nowor/hermes-lark-streaming)
- [monkey2jack/aiduPOP](https://github.com/monkey2jack/aiduPOP)
- [techysy/hermes-fry-cards](https://github.com/techysy/hermes-fry-cards)
- [baileyh8/hermes-feishu-streaming-card](https://github.com/baileyh8/hermes-feishu-streaming-card)

同时感谢 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 与[飞书开放平台](https://open.feishu.cn/)提供的公开能力。

LarkDeck 为独立实现，与上述项目无隶属关系；相关名称与成果归各自作者所有。

## 贡献

欢迎提交 Issue 与 Pull Request；开始前请阅读[贡献指南](CONTRIBUTING.md)。

## 许可证

MIT，见 [LICENSE](LICENSE)。
