# 更新日志（CHANGELOG）

本项目按版本倒序记录**用户可见**的变化。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> 每个条目的门禁数字都是**当次实测**抄下来的（本项目的纪律：`docs/lessons.md` 第四节）。
> 真机项目另附真机探针结果。

## [Unreleased]

计划中的下一个版本是 **v0.2.0**（首次发布）。分阶段方案与逐阶段证据见
[`docs/plan-v1.md`](docs/plan-v1.md)。

### 行为变化（**不动配置也会看到不同**）

- **流式帧的默认传输改成 CardKit 实体**（`native_transport: cardkit`）——卡片变成**真逐字打字机**
  （普通卡 + `message.patch` 只是「几个几个字地跳」）。想回旧路径：`native_transport: "patch"`。
  真机证据：生产路径探针（建实体 1 次 + 发实体卡 1 次 + 元素写入 6 次 + 收尾 patch 1 次，全 `code=0`）。
- **澄清卡默认方言改成 2.0**（`clarify_dialect: "2.0"`）——下拉 + 输入框；想回按钮式：
  `clarify_dialect: "1.0"`。真机证据：点击到达并解析出 clarify id，2.0 端到端全绿。

### 修复

- **平台 entry 不再丢字段**：`register_platform` 是**整条替换** `PlatformEntry`、不合并，
  而我们此前只透传 8 个字段 ⇒ 丢掉 `standalone_sender_fn`（**没有常驻网关时 cron 投递会失败**、
  `send_message` 工具同理）、`max_message_length=8000`（长回复不再分块）、
  `apply_yaml_config_fn`（`feishu.allow_bots` 静默失效）。现在键集从 dataclass 字段派生，
  并有「注册前后逐字段比对」的机械门禁（漏一个即红）。
- CardKit 传输补齐一批**会静默失灵**的路径：回复锚点（回答不再挂在提问下面）、
  写接口的限流退避、发实体卡的幂等 `uuid`、正文长大后的硬上限闸门、
  实体卡必须开 `streaming_mode`、`unified_panel: false` 时不再写不存在的元素 id。

### 内部（用户无感，但决定以后能不能持续改）

- CardKit 写入路径重构为**元素表驱动**（结构由数据决定，不再靠调用点上的 `if`），
  并用**冻结的 golden trace 夹具**证明成功路径逐字节不变。
- 变异验证器：**98 条**「撤掉修复必须变红」的定向变异 + 5 条对照，全绿才算过。

## [0.1.0] - 2026-09-13

首个可用的自我记录基线（**未单独发布 Release**，只打了一个 annotated tag 供对比与回退）。
包含：native 流式单卡、CardKit 逐字打字机、推理 + 工具统一面板、回合状态色、
2.0 澄清卡与回填、双语 i18n、上下文用量页脚、`/stop` 中止重绘，
且**全程不改 Hermes 源码、不 monkeypatch**（`register_platform` + 官方钩子）。
