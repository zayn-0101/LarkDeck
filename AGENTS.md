# AGENTS.md — larkdeck

> Hermes Agent 的飞书流式卡片插件。中文日常叫法「卡组」。
> 全局人格与安全红线见 `~/.hermes/SOUL.md`；本文件只加项目内规则。

## 这个项目的不变量（改动前必读）

1. **绝不修改 Hermes 源码**，绝不 monkeypatch。所有能力必须通过公开契约拿到：
   `ctx.register_platform()` 覆盖内置 `feishu` 平台 + 子类化官方适配器。
   任何"顺手 patch 一下"的改动都是设计错误，不是权宜之计。
2. **卡片是增强，不是替代。** 任何卡片路径失败都必须回落到 `super()` 的官方实现。
   宁可退回纯文本，也不能因为卡片报错而丢消息。改 `adapter.py` 时逐条保住这个性质。
3. **Hermes 私有接口只允许出现在 `compat.py`。** 目前依赖官方适配器的 6 个内部方法
   （`_feishu_send_with_retry` 等）。新增依赖一律先登记到 `compat.REQUIRED_ADAPTER_ATTRS`，
   再由 `probe_adapter_class()` 在运行时校验 —— 官方改名时要"自检报错"，不是静默失效。
4. **不假设版本。** Mac 与 NAS 都跑 Hermes 0.21.1（NAS 是 Docker 镜像内固定版本），
   但升级随时会发生。能力一律运行时探测，不写死版本号分支。
5. **卡片方言不可混用：要接点击的卡只能是 legacy 1.0。** 2.0 的 `behaviors` 回调
   到不了 `p2.card.action.trigger` 这个 WebSocket 处理器；反过来，1.0 的 `action`
   按钮行**嵌进 2.0 卡会被飞书拒绝**。所以澄清卡（待答 + 回填）走 `legacy_card()`，
   只有流式回复卡走 2.0。`cards.py` 的 docstring 与
   `tests/test_units.py::test_clarify_card_must_be_legacy_dialect` 一起锁住这条 ——
   混用不会报错，只会让按钮**静默失灵**，很难查。

## 目录

```
plugin.yaml        插件清单（kind: platform）
__init__.py        导出 register
adapter.py         覆盖层：LarkDeckMixin + build_adapter() + register() + 启动自检
cards.py           飞书卡片 JSON 构造（纯函数、无 I/O）—— 两种方言的边界在这里
i18n.py            双语文案（飞书原生 i18n_content）
compat.py          版本 / 能力探测 —— Hermes 私有名的唯一存放处
tests/             单测 + 真机覆盖验证 + 澄清卡端到端
```

## 键盘约定

- `LarkDeckMixin` 里的方法用 `_ld_` 前缀；卡片追踪状态挂在 `self._ld_state`。
- 卡片按钮的 value 一律带 `larkdeck_action` 键，由重写的 `_on_card_action_trigger` 拦截。
  不带这个键的点击必须原样交给 `super()`。
- 界面文案**只**从 `i18n.t()` / `i18n.i18n_text()` 取，不硬编码中文字符串。

## 验证

改完必须跑：

```bash
python3 tests/test_units.py       # 必须全绿
python3 tests/check_override.py   # 必须打印 OVERRIDE OK
```

`check_override.py` 在临时 `HERMES_HOME` 里真跑一遍 Hermes 的插件加载器，是唯一能
证明"注册表覆盖生效"的手段 —— 单测用替身，证明不了运行时行为。

## 部署

- **Mac**：`~/.hermes/plugins/larkdeck` 软链到本仓库（`./install.sh`），改代码即生效。
- **NAS**：`/opt/data/plugins/larkdeck`（`HERMES_HOME=/opt/data`，已 bind 挂载到宿主卷，
  容器重建不丢）。官方镜像的源码在 `/opt/hermes`，**非持久**，所以任何对源码的改动都会在
  重建时消失 —— 这正是本项目存在的理由。
- 线上两处都**还装着 HFC**，它在 NAS 上靠 `/opt/data/scripts/hermes-scheme2-bootstrap.sh`
  每次容器启动重新注入 8 个核心文件。切到 larkdeck 必须先拆掉那个脚本，否则它每次开机
  把 HFC 装回来、把注入写回去。

## 红线

- 不往仓库提交任何凭据、`config.yaml`、`.env`、日志或真实 chat_id / open_id。
  这是**公开仓库**。
- 不在 NAS 上做写操作而不先确认；破坏性操作前先说清范围与回滚点。
