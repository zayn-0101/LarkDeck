# 五分钟快速开始

本文面向第一次使用 LarkDeck 的用户：从零装好插件，在飞书里发出第一张卡片。

## 前提

- Hermes Agent 0.21.x 已安装并运行，官方飞书 / Lark 机器人已经能收发纯文本。
- 如果没有，先按 [INSTALL.md](../../INSTALL.md) 的环境要求与“准备机器人凭据”完成。

## 步骤

### 1. 安装插件

```bash
git clone https://github.com/zayn-0101/LarkDeck.git larkdeck
cd larkdeck
./install.sh
```

NAS / 容器用 `./install.sh --copy`。脚本不会删除已有目录；目标已存在时会停下并提示。

### 2. 最小配置

编辑 `~/.hermes/config.yaml`：

```yaml
plugins:
  enabled:
    - larkdeck
  stream_reasoning_deltas: true   # 可选：想看到第 N 轮思考（round）时开启
  entries:
    larkdeck:
      settings:
        cards: true
```

`cards: true` 是默认值，写出来是为了确认总开关；其余键全部走默认即可。完整清单见 [配置参考](configuration.md)。

### 3. 重启网关

```bash
hermes gateway restart
```

启动日志出现下面这一行就是接管成功（内容随环境变化）：

```text
[larkdeck] 启动自检通过：... feishu 平台已由 larkdeck 接管 ...
```

失败会打 `ERROR`，此时飞书仍由官方适配器发纯文本，不会弄坏消息。先看 [故障排查](troubleshooting.md)。

### 4. 验收：第一张卡片

1. 私聊机器人发 `/larkdeck status`。预期收到一张自检卡，首行类似 `🃏 larkdeck v<当前版本> · 传输 cardkit · 钩子 8/8 已挂`，并能看到“能力探测：已接管”。
2. 再发一句“你好”。预期只有一张卡片，文字在原地逐字出现，而不是新消息一条条刷出来。
3. 选一个会调用工具的请求（例如“列出当前目录的文件”）。预期工具步骤收在底部执行详情（panel）里，摘要类似 `💭 思考 … · 🛠️ 工具执行 · N 步`。
4. 等回答结束：面板边框变绿，页脚出现 `✅ 已完成 · <耗时> · <模型> · ctx …`。

## 验收没通过

| 现象 | 先做什么 |
|---|---|
| 机器人毫无反应 | 确认网关在运行、机器人凭据正确；先看私聊里有没有纯文本回复 |
| 收到纯文本而不是卡片 | 检查 `plugins.enabled` 是否含 `larkdeck`，重启网关；看启动日志的自检结果 |
| 命令没反应 | 飞书网关里，生成回答期间发的命令会排队到回合结束；等空闲再发，或在 CLI / TUI 里执行 |
| 卡片有但不更新 | 见 [故障排查](troubleshooting.md) 的“卡片不更新” |
| 看不到思考 | 确认 `plugins.stream_reasoning_deltas: true`，并在空闲时发 `/reasoning on`；详见 [配置参考](configuration.md) 的 `show_reasoning` |
