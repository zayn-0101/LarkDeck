# 贡献指南

> 读者：人类开发者与 AI coding agent。
> LarkDeck 是 Hermes 的飞书卡片插件；目标是把「能装、能改、能验证、能回滚」写成固定流程。

## 开发环境

```bash
git clone https://github.com/zayn-0101/LarkDeck.git
cd larkdeck

# 软链安装：改代码后重启网关即可生效
./install.sh
hermes gateway restart

# 用 Hermes 自带解释器跑门禁（系统 python3 缺 lark_oapi 依赖，会假红）
PY="${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python3"
$PY tests/run_fast.py
```

首次启动后可在飞书里发 `/larkdeck status`，确认平台已接管、钩子已订阅。
改卡片前先读 `docs/STYLE.md` 与 `AGENTS.md` 的「不变量」一节。

## 开发约束

- 不修改 Hermes 源码，不 monkeypatch。所有能力走公开契约：`ctx.register_platform()`、
  `ctx.register_hook()`、子类化官方适配器。
- Hermes 私有接口名只允许出现在 `core/compat.py`，并登记用途与缺失时的行为。
- 任何卡片路径失败都必须回落到官方 `super()` 实现；宁可退回纯文本，也不能丢消息。
- 新增配置项同时写进 `adapter._DEFAULTS` 与 `plugin.yaml` 的 `config_schema`；
  行为、默认值和文档在同一提交里更新。
- 界面文案走 `core/i18n.py`，不硬编码中文字符串；AI 正文不翻译。

## 跑门禁

```bash
$PY tests/run_fast.py            # 日常快速检查，目标 30 秒内
$PY tests/run_fast.py --full     # 9 步，发布前必须恰好 9 个 [OK]
```

不要用 `pytest` 入口跑这些脚本：pytest 的 logging / 全局状态会让部分 CardKit 与账本用例
出现既有失败，用各脚本自己的 `python3 tests/...` runner 才是受支持路径。
门禁的分工、通过标志与变异规则见 [测试与门禁](docs/development/testing.md)。

改动任何断言后，必须跑 `$PY tests/mutate_check.py --delta`；合并 / 变基 / 大改后跑全量
`$PY tests/mutate_check.py`。只跑 `--preflight` 不算验证。

## 提交信息

项目实践接近 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)：

```text
<type>(<scope>): <一句话说清改了什么>
```

| type | 用途 |
|---|---|
| `feat` | 用户可见的新能力 |
| `fix` | 用户可见的缺陷修复 |
| `test` | 测试、变异、断言 |
| `docs` | 文档与发布说明 |
| `refactor` | 不改变行为的重构 |
| `chore` | 版本、依赖、发布机械动作 |
| `merge` | 分支合并 |

- 标题用中文或中英混排均可，一句话内说清「改了什么」，细节放正文或提交说明；
- 一个提交只做一件事；代码、门禁、文档在同一提交里对齐；
- 不提交凭据、`config.yaml`、`.env`、真实 chat_id / open_id 或可能含凭据的日志 ——
  这是公开仓库。

## 文档风格

统一遵守 [文档风格规范](docs/STYLE.md)：简洁、清晰、易懂、有品味。用户文档不写内部
审计编号、变异条数、handoff 或历史实验叙事；证据链接到 release note 或内部归档。
改动 `README.md` / `docs/` 后先跑文档守卫 `$PY tests/check_docs.py`（也是 `run_fast` 第 1 步），再跑完整门禁。

## 发布

版本号唯一来源是 `plugin.yaml`；发布动作、tag / GitHub Release、`.deploy` 部署与回滚步骤
见 [发布流程](docs/development/release.md)。没有走完该流程前，不要手工推 tag。

## 报告问题

- **Bug / 功能请求**：开 GitHub Issue，附最小复现、Hermes 版本、`/larkdeck status` 输出与
  相关日志片段（先脱敏）。
- **安全漏洞**：不要开公开 Issue，按 [SECURITY.md](SECURITY.md) 的私下渠道上报。
