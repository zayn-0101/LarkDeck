# LarkDeck

> **飞书回复会逐字显示，工具步骤收在卡片底部的可折叠面板。**
> Hermes 提供推理增量且启用推理显示时，面板也可显示推理内容。LarkDeck 是基于飞书
> CardKit 2.0 的 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 插件，不修改
> Hermes 源码。主回复在卡内流式更新；长回答会接续到后续卡片，澄清问题使用独立交互卡。

[![version](https://img.shields.io/badge/version-0.7.12-blue.svg)](https://github.com/zayn-0101/LarkDeck/releases)
[![AH (Hermes Agent) 0.21.x](https://img.shields.io/badge/AH-0.21.x-blueviolet.svg)](https://github.com/NousResearch/hermes-agent)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[中文](README.md) | [English](README.en.md)

![LarkDeck 头图：左侧是项目名与四项能力（流式打字机、过程面板、澄清交互卡、状态与用量），右侧是飞书里一张完成态的天气问答卡片，含表格、可折叠执行详情与页脚用量。](assets/readme-hero.png)

## 功能

- **流式回复与长回答续写**：正文在主卡内逐字增长；单卡装不下时自动续到新卡，不重放已显示内容。
- **过程面板（panel）**：工具步骤收在底部，可折叠并按轮计时；启用推理显示且 Hermes 提供推理增量时，也会显示推理内容。参数预览会截短并遮盖明显的凭据片段。
- **澄清交互卡（clarify）**：用下拉、多选、输入框或按钮直接作答；澄清卡独立于主回复卡。
- **状态与用量**：完成 / 失败 / 中止对应绿 / 红 / 黄边框；页脚显示耗时、模型与上下文用量。
- **双语界面**：支持本地化的卡片界面文案跟随飞书客户端语言；AI 生成的正文不翻译。
- **失败回退**：卡片路径无法继续时交回 Hermes 官方纯文本发送或编辑；部分 CardKit 错误可
  降级为同卡整卡替换。消息被撤回或删除后，是否补发由 Hermes 核心决定。
- **正文排版**：清理游离的 `**`，并将 H1–H3 降级为加粗，避免卡片正文出现过大的标题。

## 快速开始

前置：Hermes Agent 0.21.x 已安装并运行，飞书 / Lark 应用凭据已配置（应用创建见[安装指南](docs/guide/installation.md)）。

安装（默认软链到本仓库；NAS / 容器加 `--copy`）：

```bash
git clone https://github.com/zayn-0101/LarkDeck.git larkdeck
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

验证：在飞书里给机器人发送 `/larkdeck status`，收到一张结论卡（`平台接管` / `钩子` 均为 `✅`）即接管成功：

```
🃏 larkdeck v<当前版本> · 传输 cardkit
```

需要更细的能力探测与六条记录时，发送 `/larkdeck status --detail`。

启动日志里会出现一行 `[larkdeck] 启动自检通过`。自检失败会打 `ERROR`，并保持官方适配器原样工作：卡片不生效，但飞书不会被弄坏。

升级：软链安装执行 `git pull && hermes gateway restart`。`--copy` 安装先 `git pull`，再把旧目录移开、重跑 `./install.sh --copy`，最后重启网关；脚本不会覆盖已有目标，完整命令见[安装指南](docs/guide/installation.md#升级)。模块不会热重载，必须重启网关。

卸载：从 `plugins.enabled` 删除 `larkdeck`，再删除 `~/.hermes/plugins/larkdeck/`。插件没有改写 Hermes 源码，不存在残留注入。

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

当前结构化引擎始终用 CardKit：`native_transport: patch` 不会切换传输，`panel_color_tags: false` 也不会关闭结构化面板颜色。这两个键对该路径无效（no-op）；需要停用逐字动画时设 `streaming_print_ms: 0`。

## 常用命令

| 命令 | 作用 |
|---|---|
| `/larkdeck status` | 运行概览：版本、传输配置、接管、钩子、推理显示与失败计数；没有记录时如实显示 |
| `/larkdeck status --detail` | 完整诊断：能力探测、适配器与契约细节、六条进程级记录 |
| `/larkdeck config` | 只读查看本进程生效配置与来源 |
| `/larkdeck config reload` | 从 Hermes 配置重读；任一键失败则整次取消 |
| `/larkdeck help` | 命令用法 |
| `/reasoning on\|off` | Hermes 命令：开关推理正文；默认 `show_reasoning: auto` 约 1 秒内跟随 |

详细说明见[命令](docs/guide/commands.md)。

## 兼容与限制

- **环境**：面向 Hermes Agent 0.21.x（已在 0.21.1 / 0.21.4 上验证），且官方 `feishu` 平台必须在同一进程可用；与同样接管 `feishu` 或改写 Hermes 源码的插件不能共存。
- **失败时的行为**：无法继续卡片更新时，插件会把可回退的发送或编辑交给 Hermes
  官方实现；部分 CardKit 错误会在同一张卡上降级为整卡替换。消息被撤回或删除时，是否补发
  由 Hermes 核心决定。
- **客户端差异**：客户端不支持卡片 2.0 能力时，部分组件可能不渲染或降级；卡片界面文案跟随客户端语言，AI 正文与部分 markdown 文案（工具动作 / 状态词等）语言固定。能力边界与替代形态见[卡片能力](docs/guide/card-capabilities.md)。
- **推理正文**：需要 Hermes 开启 `plugins.stream_reasoning_deltas`（默认关闭）；未开启时过程面板只显示工具步骤，`/larkdeck status --detail` 会说明原因。
- **命令时机**：在飞书网关里，生成回答期间发送的命令会排队到回合结束；CLI / TUI 中会立即执行。
- **统计口径**：页脚指标与 `/larkdeck status` 的记录是进程级累计，多会话并发时不区分会话；超长回答分卡后，`/stop` 只重绘最新一张卡。

## 文档

README、贡献、更新日志、许可证、安全政策和行为准则等常用入口保留在仓库根目录；安装与
使用手册和开发文档按读者归入 `docs/guide/`、`docs/development/`。完整目录见
[文档地图](docs/README.md)。

## 致谢

LarkDeck 在设计与实现过程中，受到了社区中多个优秀飞书 / Lark 卡片项目的启发，在此谨向以下项目及其作者致谢：

- [Cheerwhy/hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming)
- [Aowen-Nowor/hermes-lark-streaming](https://github.com/Aowen-Nowor/hermes-lark-streaming)
- [BcubBo/lark-hls-v2](https://github.com/BcubBo/lark-hls-v2)
- [monkey2jack/aiduPOP](https://github.com/monkey2jack/aiduPOP)
- [techysy/hermes-fry-cards](https://github.com/techysy/hermes-fry-cards)
- [baileyh8/hermes-feishu-streaming-card](https://github.com/baileyh8/hermes-feishu-streaming-card)

同时感谢 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 与[飞书开放平台](https://open.feishu.cn/)提供的公开能力。

LarkDeck 为独立实现，与上述项目无隶属关系；相关名称与成果归各自作者所有。

## 贡献

欢迎提交 Issue 与 Pull Request；开始前请阅读[贡献指南](CONTRIBUTING.md)。

## 许可证

MIT，见 [LICENSE](LICENSE)。
