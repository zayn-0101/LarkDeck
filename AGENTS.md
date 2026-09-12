# AGENTS.md — larkdeck

> Hermes Agent 的飞书流式卡片插件。中文日常叫法「卡组」。
> 使用者文档在 `README.md`；从 HFC 迁移步骤在 `docs/switch-from-hfc.md`。
> 全局人格与安全红线见 `~/.hermes/SOUL.md`；本文件只加项目内规则。

## 不变量（改动前必读）

1. **绝不修改 Hermes 源码**，绝不 monkeypatch。所有能力通过公开契约拿：
   `ctx.register_platform()` 覆盖内置 `feishu` 平台 + 子类化官方适配器
   （`_discover_base_class()` 从注册表拿上一个工厂产出的官方类，再经 `merged_class()` 混入
   `LarkDeckMixin`）。任何"顺手 patch 一下"都是设计错误，不是权宜之计。
2. **卡片是增强，不是替代。** 任何卡片路径失败都必须回落到 `super()` 的官方实现。
   宁可退回纯文本，也不能因为卡片报错而丢消息。改 `adapter.py` 时逐条保住这个性质。
3. **Hermes 私有接口只允许出现在 `compat.py`。** 目前分三组登记：适配器必需 5 个
   （`REQUIRED_ADAPTER_ATTRS`，`probe_adapter_class()` 运行时校验，缺了拒绝覆盖）；
   点击回调路径 5 个（`CALLBACK_ADAPTER_ATTRS` + 实例属性 `CALLBACK_INSTANCE_ATTRS`，
   只探测上报 —— 缺了不致命但澄清按钮会静默失灵）；澄清网关内部结构
   （`_lock` / `_entries` / `mark_awaiting_text` / `resolve_gateway_clarify`，
   已封装成 `clarify_multi_select()` 等函数）。新增依赖一律先登记。
4. **不假设版本。** Mac 与 NAS 都跑 Hermes 0.21.1（NAS 是镜像内固定版本），升级随时会发生。
   能力一律运行时探测，不写死版本号分支。
5. **卡片方言不可混用：要接点击的卡只能是 legacy 1.0。** 2.0 的 `behaviors` 回调到不了
   `p2.card.action.trigger`；1.0 的 `action` 按钮行嵌进 2.0 卡会被飞书拒绝。澄清卡
   （待答 + 回填）走 `legacy_card()`，只有流式回复卡走 2.0。混用不报错，只会让按钮
   **静默失灵**。`tests/test_units.py::test_clarify_card_must_be_legacy_dialect` 锁住这条；
   元素级方言差异见 `README.md` 的表。

## 目录

```
plugin.yaml   插件清单（kind: platform，含 requires_env 与 config_schema 声明）
__init__.py   插件入口：只从 core.adapter 转发 register
core/         插件本体（Hermes 加载器以 hermes_plugins.larkdeck.core.* 命名空间加载）
  adapter.py    覆盖层：LarkDeckMixin + merged_class() + build_adapter() + register() + 启动自检
  cards.py      卡片 JSON 构造（纯函数、无 I/O）—— 两种方言的边界在这里
  i18n.py       双语文案（飞书原生 i18n_content）
  compat.py     版本 / 能力探测 —— Hermes 私有名的唯一存放处
  context.py    运行时指标（钩子写入 → 页脚读取的进程内全局快照）
  panel.py      面板数据层（推理 / 工具钩子写入 → 卡片面板读取；按会话分桶 + 最近活跃取用）
  hooks.py      官方钩子订阅（5 个观察型钩子）：只写内存、异常自吞、永不返回 directive
install.sh    安装脚本（默认软链；NAS 用 --copy，其 FILES 数组是手动的，新增模块要同步）
docs/         HFC 切换步骤、指标与钩子原理
tests/        见「验证」
```

## 约定

- `LarkDeckMixin` 的方法用 `_ld_` 前缀；卡片追踪状态挂在 `self._ld_state`。
- 卡片按钮 value 一律带 `larkdeck_action` 键，由重写的 `_on_card_action_trigger` 拦截；
  不带这个键的点击必须原样交给 `super()`。
- 界面文案只从 `i18n.t()` / `i18n.i18n_text()` 取，不硬编码中文字符串。
  i18n 只覆盖界面文案，AI 生成的正文不翻译。
- 插件配置路径是 `plugins.entries.larkdeck.settings.<key>`，由 `register()` 里的
  `_apply_ctx_settings()` 经官方 `ctx.get_config()` 读入。Hermes **从不**调用
  `configure()`（它只是自有运行时入口，单测在用）。取值优先级：环境变量
  `LARKDECK_<KEY>` > config.yaml settings > `_DEFAULTS`。新增配置项必须同时加到
  `_DEFAULTS` 和 `plugin.yaml` 的 `config_schema`；未接入业务的配置项要在 README 标 no-op。
- 运行时只 import 标准库与 Hermes 环境；不新增第三方依赖。
- 页脚指标是进程内全局（钩子记「最近一次 API 请求」），多会话并发共享同一快照；
  要按会话隔离得从钩子载荷的 `session_id` 分桶（未做）。
- 面板数据策略与页脚不同：`panel.py` 按 `session_id` 分桶、快照时取「最近活跃」——
  流式/工具钩子载荷没有 chat_id，卡片渲染时无法自证归属，多会话并发可能短暂串台。
  钩子回调纪律源自 `pre_tool_call` 是 **fail-closed**（回调卡住会阻止工具执行）：
  只写内存、微秒级返回、异常自吞、**永不返回 directive**。

## 验证

```bash
python3 tests/test_units.py        # 纯单测，零网络、零 Hermes 依赖，必须全绿
python3 tests/check_override.py    # 真跑 Hermes 插件加载器（临时 HERMES_HOME），必须打印 OVERRIDE OK
python3 tests/check_hooks.py       # 真钩子派发器验证指标采集 + 面板数据层，必须打印 HOOKS OK
python3 tests/check_clarify_e2e.py # 澄清卡端到端，必须打印 CLARIFY E2E OK
```

没有 CI / lint / formatter，这四个脚本就是全部验证。系统 `python3` 跑不动时用 Hermes
自带解释器 `/Users/Zayn/.hermes/hermes-agent/venv/bin/python3`。

- `check_override.py` 是唯一能证明「注册表覆盖生效」的手段 —— 单测用替身，证明不了运行时行为。
  它还负责验证插件配置桥接：临时 config.yaml 里写 `plugins.entries.larkdeck.settings`，
  断言 `_CONFIG` 生效且未配置的键保持默认。
- 测试里要用加载器那份 `context` 模块（`hermes_plugins.larkdeck.core.context`）；
  直接 `import larkdeck.core.context` 会拿到第二个模块对象，读写状态对不上（`check_hooks.py` 盯这个）。
- **改 `cards.py` 或任何卡片结构后必须跑 `tests/probe_render.py`**：唯一连真实飞书的测试
  （从 `~/.hermes/.env` 读凭据，把探针卡真发到自己的飞书 DM：功能卡 + 双语互换实验 +
  页脚样式对照，自动先清理上次的探针卡）。
  本地单测只能验结构，卡片合法性由飞书 API 返回码说了算。它只验渲染，不验点击。

## 部署

- **Mac**：`~/.hermes/plugins/larkdeck` 软链到本仓库（`./install.sh`），改代码即生效。
- **NAS**：`/opt/data/plugins/larkdeck`（`HERMES_HOME=/opt/data`，bind 挂载，容器重建不丢）。
  官方镜像源码在 `/opt/hermes`，**非持久** —— 这正是本项目存在的理由。
- 两处**仍装着 HFC**，NAS 上靠 `/opt/data/scripts/hermes-scheme2-bootstrap.sh` 每次容器
  启动重新注入 8 个核心文件。切到 larkdeck 必须先拆掉那个脚本，否则每次开机把 HFC 装回来。

## 红线

- 不往仓库提交任何凭据、`config.yaml`、`.env`、日志或真实 chat_id / open_id。这是**公开仓库**。
- 不在 NAS 上做写操作而不先确认；破坏性操作前先说清范围与回滚点。
