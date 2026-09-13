# LarkDeck

> 飞书 / Lark 流式卡片渲染器，给 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 用。
> 中文日常叫法：**卡组**。

一条回复从第一个 token 到最终答案，始终是**同一张卡片**，原地长出来。推理过程和工具调用收进底部一个可折叠面板，正文区保持干净。

---

## 它解决什么问题

Hermes 官方带的飞书适配器**只会发纯文本**，然后靠"编辑消息"来流式更新。所以：

- 没有卡片，没有折叠面板，没有按钮；
- 澄清提问（clarify）只能发文字，用户得手打选项；
- 想看推理过程 / 工具调用，只能被塞进正文里，正文被切得稀碎。

现在网上几个第三方方案（hermes-feishu-streaming-card、hermes-fry-cards、aiduPOP 等）能做出卡片，但代价是**改写 Hermes 源码**：往 `gateway/` 下 8 个核心文件里注入代码。Hermes 一升级，注入就被冲掉，得重装一遍；NAS 上还得额外挂一个"每次容器启动重新注入"的启动脚本。

LarkDeck 换了一条路：**不改源码，不 monkeypatch，升级不用重装**。

---

## 它是怎么做到不改源码的

三句话：

1. Hermes 的 `gateway/platform_registry.py` 对同名平台是**最后写入者胜**。LarkDeck 注册在官方 `feishu` 平台**之后**，于是被解析到的是 LarkDeck 的工厂。
2. LarkDeck 的工厂先问**官方工厂**要出真实的适配器类，再据此建一个子类
   `type("LarkDeckFeishuAdapter", (LarkDeckMixin, <官方类>), {})`，**直接构造它**。
3. 于是只需要覆盖四处：**发消息 / 改消息 / 澄清提问 / 卡片点击**。其余全部继承 ——
   鉴权、WebSocket、媒体、重试、限流都还是官方实现。

> 为什么不直接改 `instance.__class__`：CPython 除了要求内存布局一致，还要求新类的
> **直接基类链**一致。`(LarkDeckMixin, FeishuAdapter)` 的直接基类是 LarkDeckMixin 而非
> FeishuAdapter，所以即使布局数字完全一样，赋值依然抛
> `TypeError: __class__ assignment: object layout differs`。
> 直接构造子类没有这个约束，MRO 也更干净。

流式主路径是 Hermes 官方的 **native streaming 协议**：核心把整回合的正文与工具进度合成一条流、逐帧交给 `send_stream_frame()` 原地更新同一张卡 —— **一次回复只有一张卡**，工具调用不再另起消息。任何一帧失败，核心会自动回退到 **edit 传输**（`send()` 建卡 → `edit_message()` 连续更新 → `edit_message(finalize=True)` 封口，由 `REQUIRES_EDIT_FINALIZE = True` 驱动）—— 卡片永远只做增强，不会弄丢消息。

> 为什么不去 `import` 官方适配器模块：目录名、包名、加载方式都可能变。从注册表拿"上一个工厂"是唯一不依赖路径的接法。

---

## 卡片方言：1.0 与 2.0（改卡片前必读）

飞书卡片有两套互不兼容的写法，**混用不会报错，只会让按钮静默失灵**：

| | legacy 1.0 | schema 2.0 |
|---|---|---|
| 结构 | 顶层 `elements` | `config` / `header` / `body.elements` |
| 交互组件 | `{"tag": "action", "actions": [...]}` 按钮行，按钮**顶层** `value` | 组件**自己**带 `behaviors: [{"type":"callback","value":{...}}]`；组件可为 `button` / `select_static` / `multi_select_static` / `input` |
| 点击能到服务端吗 | ✅ 走 `p2.card.action.trigger` | ✅ **能**（组件级 `behaviors` 的 `value` 原样成为 `event.action.value`） |
| 流式（打字机） | ❌ | ✅ `streaming_mode` |
| 可折叠面板 | ❌ | ✅ `collapsible_panel` |
| `config.summary` | 不需要 | **流式时必带**，漏了通知栏空白 |
| `note` 小字脚注 | ✅ | ❌ **飞书已废弃** → 改用 `footnote()` |

所以本项目的分界是：

- **澄清交互卡**：待答卡与点击后的*回填卡*必须**同方言**（混用会让回填那一帧被飞书静默
  丢弃，表现为"点了没反应"）。当前默认 **1.0**（按钮，真机跑通）；2.0 版
  （`select_static` / `multi_select_static` / `input` + 组件级 `behaviors`）也已实现，
  由配置 `clarify_dialect` 选择 —— 默认值是等**真机点一次**确证后再翻的。
- **流式回复卡 → 2.0。** `streaming_mode` 和统一面板的 `collapsible_panel` 是 2.0 独有能力。

> **历史上这里写过「2.0 的回调到不了服务端」—— 那是错的**（2026-09-12 更正，
> 见 `AGENTS.md` 不变量 5）：当时那张「2.0 澄清卡」用的是**没有 `behaviors`** 的 1.0 按钮，
> 属于"2.0 卡里放 1.0 组件"，而不是「2.0 回调不可用」。
> `tests/test_units.py::test_clarify_card_must_be_legacy_dialect` 锁的是**当前默认实现
> 别被误改**，不代表 2.0 不可行。

### 元素级方言差异（实测，非文档推断）

飞书对卡片**不做静态校验** —— 不支持的字段是*发送时*才拒。所以下面这些只能靠真发一次卡才知道：

| 元素 | 1.0 | 2.0 | 依据 |
|---|---|---|---|
| `note` | ✅ | ❌ | 飞书返回 `230099 / ErrCode 200861 · cards of schema V2 no longer support this capability`；2.0 的小字脚注改用 `markdown` + `text_size: "notation"`（即 `footnote()`） |
| `action`（按钮行） | ✅ | ❌ | 1.0 的 `action` 容器嵌进 2.0 卡会被拒（`230099`）；2.0 里要用**组件级** `behaviors` |
| `collapsible_panel` | ❌ | ✅ | 2.0 专属 |
| 顶层文本元素 | `lark_md` / `plain_text` | 只认 `markdown` | 2.0 的 `body.elements` 里直接放 `lark_md` 或 `plain_text` 都会被拒（`ErrCode 200621`）；`plain_text` 只在嵌套位置有效（collapsible 标题、按钮 text） |
| `i18n_content` | ✅ | ✅ | 文本元素级字段，两个方言均实测接受；1.0 的 header title 与按钮 text 也接受且生效（2026-09-12 互换实验确证） |

**结论：本地单测只能验结构，验不了合法性。** 改完卡片必须跑 `tests/probe_render.py` ——
用真凭据把探针卡发到自己的飞书 DM，看飞书返回的 `code`（它会自动先删掉上次发的探针卡，
不留垃圾）。`note` / `action` 两条已在本地被 `test_dialect_element_exclusivity` 锁死。

---

## 功能

| 功能 | 状态 |
|---|---|
| 流式卡片（一回合一张卡，工具进度合入同卡） | ✅ 走 Hermes 官方 native streaming 协议；失败自动回落到内置发送 |
| 统一面板（推理 + 工具合并为一个可折叠底部面板） | ✅ 数据来自官方钩子（`on_stream_delta` / `pre_tool_call` / `post_tool_call`）；推理流需开启 Hermes 侧 `plugins.stream_reasoning_deltas` |
| 澄清交互卡（按钮点击直接作答，不再手打选项） | ✅ |
| 模型别名（可选：把 `deepseek-flash` 显示成你认得出来的名字；默认关闭） | ✅ |
| 双语 UI（跟随飞书客户端语言） | ✅ 真机确证（2026-09-12）：互换实验证明客户端按 `i18n_content` 选语言 |
| 即时响应：首帧早于首个 token（native seed 帧）+ 等待期占位 + 可关的「处理中」表情 | ✅ 真机实测 seed 帧建卡 `code=0`；等待期正文区显示 i18n 占位「⏳ 正在生成…」（收尾帧不带，避免空答案停在「正在生成…」）；`reactions: false` 可关掉飞书那侧相当于「输入提示」的表情（**默认保持 Hermes 行为 = aiduPOP 的做法**，它的测试明确断言「reaction 拦截保持禁用」，见 `docs/plan-6-effects.md` §9） |
| **回合状态色**：完成绿边 / 报错红边 / 中止黄边 | ✅ 数据来自官方 `on_session_end`（每回合一次）；颜色画在面板边框上 |
| **推理按轮分段**（`第 N 轮 · 6.2s`；一轮 = 一段连续推理，被正文或工具打断） | ✅ |
| 面板标题行：模型名 · 轮数 · 工具数 · 耗时 | ✅ 卡片级 header 已去掉，信息全在面板头（观感更接近 aiduPOP） |
| 页脚：上下文用量（模型/耗时已并入面板标题） | ✅ 真机渲染已确认 |
| 上下文用量三样式（纯文字 / 图形条 / 数字+条） | ✅ 真机渲染已确认 |
| 推理文本 / 工具结果上限 + 元素溢出保护 | ✅ |
| 打字机逐字显示 | ✅ **已实现，且是默认**：`native_transport: cardkit`（CardKit 实体 + `card_element.content` 逐帧写元素 = **真逐字**）—— 真机 + **用户肉眼**双重确认：普通卡 `message.patch` 只是「几个字几个字」地跳，CardKit 才是一个字一个字往外冒；`--cardkit-prod` 走**生产路径**实测建实体 1 次 + 发实体卡 1 次 + 元素写入 6 次 + patch 收尾 1 次全 `code=0`。翻默认的前置条件是「一轮对抗审计 + 真机生产路径探针」，两者都过了（`docs/plan-6-effects.md` 第十一路审计）。任何一步失败自动 fail-open 回落到 edit/send —— **宁可有一次没有动画的卡，也绝不丢消息**。想回到旧路径（字几个几个跳，但稳定）就配 `native_transport: patch`；自测用 `tests/probe_render.py --cardkit-prod` |

> 每项效果**验证到什么程度**（本地门禁 / 真机 API / 肉眼）见
> [`docs/plan-6-effects.md`](docs/plan-6-effects.md) 的「6 项效果的实施完成度」表 ——
> 那里区分了「API 说了算」与「只能人看」两类，别把前者当成后者已通过。

**界面文案双语**用的是飞书卡片原生的 `i18n_content`：服务端只发一份卡片，每个文本元素同时带中文和英文，客户端按自己的语言设置挑一份渲染。AI 生成的正文**不翻译**。

---

## 版本与升级

当前版本见 `plugin.yaml` 的 `version`（启动自检与页脚都能看到）。变更记录在
[`CHANGELOG.md`](CHANGELOG.md)，**默认值与行为的变化**在那里单列。

**首次安装**：见下一节。
**升级**：Mac 上是软链安装 ⇒ `git -C <仓库> pull` 之后 **`hermes gateway restart`**
（网关进程内加载的模块**不会热重载**，不重启就还在跑旧代码）。NAS 上是 `--copy` 安装 ⇒
拉取后重跑 `install.sh --copy` 再重启。
**回退**：`git checkout <tag>` 后重启；`v0.1.0` 是上一版的基线 tag。

---

## 安装

```bash
git clone https://github.com/zayn-0101/larkdeck.git
cd larkdeck
./install.sh
```

然后把 `larkdeck` 加进 `~/.hermes/config.yaml`：

```yaml
plugins:
  enabled:
    - larkdeck
```

重启网关即可。启动日志里会有一行自检结果（下面是 0.21.1 实测原文）：

```
[larkdeck] 启动自检通过：Hermes 0.21.1 · feishu 平台已由 larkdeck 接管 · 钩子 post_api_request/on_stream_start/on_stream_delta/pre_tool_call/post_tool_call/pre_gateway_dispatch/on_session_end
```

如果自检失败，会打 `ERROR` 并**保持官方适配器原样工作** —— 卡片不生效，但飞书不会被弄坏。

### 卸载

从 `plugins.enabled` 里删掉 `larkdeck`，再删 `~/.hermes/plugins/larkdeck/` 即可。没有任何源码被改过，所以不存在"卸载后残留注入"。

---

## 配置

在 `~/.hermes/config.yaml` 里：

```yaml
plugins:
  entries:
    larkdeck:
      settings:
        cards: true              # 用卡片渲染回复（关掉则完全退回官方纯文本行为）
        native_streaming: true   # 一回合一张卡（工具进度合入同卡）；关掉退回逐段新消息
        clarify_cards: true      # 澄清用交互卡
        clarify_dialect: "2.0"   # 澄清卡方言：2.0 下拉+输入框（默认，真机点击已验证）/ 1.0 按钮（旧路径，仍然可用）
        native_transport: cardkit # 流式帧传输：cardkit（默认，**真逐字打字机**；买它的代价见「已知限制」）/ patch（旧路径，整卡替换，字是几个几个跳）
        unified_panel: true      # 推理 + 工具合并为一个底部面板
        panel_expanded: false    # 面板默认展开（默认收起）
        streaming_print_ms: 15   # 客户端打字机的逐字间隔（毫秒，只对流式帧有效）；0 = 关闭；超出 [1,2000] 退默认并留 WARNING
        reactions: true          # 在用户消息上打「处理中」表情（飞书的「输入提示」）；关掉更接近 aiduPOP 的观感
        footer: true             # 页脚（只放上下文用量）
        show_model: true         # 面板标题里显示模型名
        context_style: text      # 上下文用量样式：text | bar | both
        model_aliases: ""        # "真名=显示名, 真名2=显示名2"
        max_reasoning_chars: 1200   # 推理文本上限（超出截断并留痕；写 0 视为用默认值，不是不设限）
        max_tool_result_chars: 600  # 单条工具步骤行上限；面板显示的是参数预览，预览已被截到 80 字符，所以这项现实里几乎不会触发
        max_panel_steps: 30         # 面板最多保留多少步（超出保留最近的；写 0 视为用默认值）
        context_max_override: 0     # 非 0 时钉住上下文上限（探测不准时兜底）
```

键名、默认值与类型声明在 `plugin.yaml` 的 `config_schema` 里（写错类型 Hermes 会告警，
不会阻止加载）。插件在 `register()` 时经官方 `ctx.get_config()` 读入这些设置。

也可以用环境变量临时覆盖，如 `LARKDECK_CARDS=0`、`LARKDECK_CONTEXT_STYLE=bar`
（优先级：环境变量 > config.yaml 设置 > 默认值）。

面板里的**推理过程**依赖 Hermes 侧开关（官方默认关闭）：

```yaml
plugins:
  stream_reasoning_deltas: true   # 允许插件订阅推理增量；不开就只有工具步骤
```

工具步骤不受这个开关影响。面板、状态色与页脚的数据全部来自官方钩子，不拦截核心代码。

面板标题行与上下文数字**来自官方钩子**，不是拦截核心源码。回合状态色来自
`on_session_end` —— 它虽然叫 session，实际**每回合触发一次**，载荷是官方的
`completed` / `failed` / `interrupted`。判定优先级是 `interrupted > failed > completed`
（官方 `completed` 的表达式不含 `interrupted`，先看它会把中止显示成绿色完成）。
原理、载荷键名与踩过的坑见 [`docs/metrics-and-hooks.md`](docs/metrics-and-hooks.md)。

**中止（`/stop`）那条路要特别说明**：中止后核心不会再有收尾帧（stream consumer 直接
放弃本回合、native 模式下也是空操作），所以插件覆盖了 `interrupt_session_activity`，
在被通知时**自己把那张卡重绘成黄边**，然后照常把中止交给内核。

---

## 测试

```bash
PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3   # 用 Hermes 自带解释器最贴近线上；系统 python3 也能跑（零第三方依赖）

$PY tests/test_units.py            # 纯单测，零网络零 Hermes 依赖
$PY tests/check_override.py        # 对真实 Hermes 安装验证平台覆盖 + 插件配置桥接
$PY tests/check_hooks.py           # 对真实钩子派发器验证指标采集是否接通
$PY tests/check_clarify_e2e.py     # 澄清卡端到端：发送 → 点击 → 网关解除阻塞
$PY tests/mutate_check.py          # 变异验证器：撤掉每条修复必须变红（改了断言必跑）
$PY tests/probe_render.py          # 真发卡片到自己的飞书 DM，验飞书接不接受（要凭据）
```

前四个是**门禁**（必须全绿）；`mutate_check.py` 是**验证门禁自己有没有判别力**的元门禁：
清单里每条变异 = 一处「把某条修复撤掉」的定向改动，判定标准是**至少一个门禁变红**。
**撤掉修复还全绿 = 那条断言没有判别力** —— 这是本项目头号缺陷类型（见 `docs/lessons.md`
推论 6~8）。它的由来：第七路审计拿 50 条定向变异各跑一遍，**23 条撤掉修复后门禁仍然全绿**。
`-k <子串>` 只跑一部分；快照目录必须叫 `larkdeck`，否则会 import 到未变异的基线（曾导致假绿）。

`check_hooks.py` 走的是核心真正使用的派发器（`hermes_cli.lifecycle.invoke_hook`），
载荷用 Hermes 自己的 `CanonicalUsage` 生成，并带一组**对照组**（不启用插件时钩子必须为空）。
它专门盯住一个不报错的坑：插件加载器装在 `hermes_plugins.larkdeck.core.*` 命名空间下，
测试里若用 `import larkdeck.core.context` 会拿到**第二个模块对象**，读写状态对不上。

`check_clarify_e2e.py` 用的是从平台注册表里取出来的**真内置适配器类**，只把最底层
`_feishu_send_with_retry` 换成捕获器，**不连飞书、不发网络请求**。它能抓到桩类抓不到
的问题：签名不匹配、私有原语改名、覆盖没生效、返回类型不对。

`probe_render.py` 是唯一**连真实飞书**的测试，也是唯一能判定卡片合法性的手段 ——
它会先删掉自己上次发的探针卡再发新的。只验渲染，**不验点击**（点击路由要平台被
LarkDeck 接管后才生效）。带参数的几种模式各答一个只有真机能答的问题：`--typing`
（打字机 A/B 肉眼比）、`--bytes`（字节上限阶梯，create + patch 都打）、`--elements`
（元素数阶梯，官方硬上限 200）、`--rate-limit`（连续 patch 的限流实证）、
`--stop-redraw`（**中止重绘的真机端到端**：正文超降载预算但发得出去时，`/stop` 必须真的
把那张卡重绘成中止色 —— 它用真适配器跑生产路径，**两条路径都跑**（非 native 的 `send`+patch、
以及真 native 流式 `send_stream_frame` 建卡→若干帧→`/stop`），断言**载荷里有颜色**
而不只是「发了 patch」）、
`--clean-only` / `--no-clean`（清理控制）。

---

### 升级安全性：实测过一次真实的上游改动

Hermes **已经改过一次**内置飞书适配器的加载方式：它现在把内置适配器当**插件**加载
（`FeishuAdapter` 的模块名从 `plugins.platforms.feishu.adapter` 变成
`hermes_plugins.feishu_platform.adapter`）。我们**没有改一行代码、也没有重装**，
接管照旧成立 —— 因为基类是从平台注册表**运行时取**的，不是硬编码导入：

```
LarkDeckFeishuAdapter → LarkDeckMixin → FeishuAdapter → BasePlatformAdapter
   （我们）              （我们）        hermes_plugins.feishu_platform.adapter（上游）
启动自检通过：Hermes 0.21.1 · feishu 平台已由 larkdeck 接管
```

这条性质由 `tests/check_override.py` 守着（它用**真插件加载器**跑一遍再问注册表
「现在 feishu 解析到谁」）—— 上游再怎么挪文件，它都会在启动那一刻告诉我们有没有接上。

## 已知限制

- **必须和官方适配器同一进程**：官方 `feishu` 平台被禁用时，LarkDeck 无处附着。
- **依赖官方适配器的内部方法**：发送/编辑路径 3 个必需（`_feishu_send_with_retry` 等，启动自检校验，缺了**拒绝覆盖**并保持内置行为）+ 1 个可选（`edit_message`，有则用、无则退回内置）；点击回调路径 5 个类属性 + 2 个实例属性（`_on_card_action_trigger`、`_card_response`、`_client` 等）；信号型 1 个（`interrupt_session_activity`，缺了「中止后卡片不变色」）；处理生命周期 1 个（`_reactions_enabled`，缺了 `reactions: false` 静默失效）；澄清网关内部结构（`_lock` / `_entries` / `entry.multi_select` / `mark_awaiting_text` / `resolve_gateway_clarify`）。全部集中登记在 `compat.py`（分七组 + 会话归属公开面）：`probe_adapter_class()` 守必需项，`probe_report()` 的完整快照在启动时打进日志（缺点击回调会提级 WARNING 并写明「澄清按钮会静默失灵」）—— 官方哪天改了名字，日志会直接说出来，而不是静默失效。
- ~~**`i18n_content` 的元素级支持需真机确认**~~ → **已实测确认**（2026-09-12）：
  文本元素同时带 `content` 与 `i18n_content`，1.0 与 2.0 卡均被飞书接受；
  1.0 的 header title 与按钮 text 也接受且生效。**互换实验**（把 `zh_cn` 分支里放英文）
  在中文客户端上显示出英文，证明客户端确实按 `i18n_content` 选语言，而不是永远读默认值。
  兜底仍然安全：客户端不认时回落到 `content`，不会让卡片发不出去。
  **注意 AI 生成的正文不翻译**，双语只覆盖界面文案。
- 下一步要做什么（分阶段方案，已经过三路审计）：[`docs/plan-v1.md`](docs/plan-v1.md)
- 与同类插件的横向对比（六家、含各自特色与我们可借鉴的部分、ROI 排序）：[`docs/plugins-compare.md`](docs/plugins-compare.md)
- 与 hermes-feishu-streaming-card（HFC）**不能共存**：两边都要接管 `feishu` 平台，且 HFC 还改了源码。切换步骤见 [`docs/switch-from-hfc.md`](docs/switch-from-hfc.md)。
- **页脚数据是进程内全局的**：钩子记录的是「最近一次 API 请求」，多会话并发时所有卡片共享同一份快照。单用户单会话无影响；真要按会话隔离，得从钩子载荷里的 `session_id` 分桶，目前没做。
- **面板归属**：钩子载荷只有 `session_id`、没有 chat_id。归属靠 `pre_gateway_dispatch` 观察到的
  `chat_id -> session_id` 映射（确定性），**拿不到映射时才退回「最近活跃会话」**。
  退回的窗口是**新会话的第一回合**（那一刻 session 还没落库）、会话映射过期（24h）、以及
  老版本 Hermes；这些窗口里多会话并发可能短暂显示另一个会话的推理/工具（正文不受影响）。
- **打字机的动画本身没有自证手段**：`streaming_print_ms`（默认 15）会把
  `streaming_config` 带在**流式帧**上（飞书接受它，实测 create/patch 都是 `code=0`），
  但「客户端会不会逐字打」是纯客户端行为，**API 返回码看不到**。而且有三条证据倾向于
  「`im.v1.message.patch` 拿不到这个动画」：官方文档没说清是哪种写入 API、**Hermes 上游
  两个 PR 都把这个效果绑在 CardKit 流式接口上**、同类项目 lark-hls-v2 的注释也要求
  「第一次推送必须用 `card_element.content`」。**这件事最后是用户肉眼定的案**（甲几个字几个字
  跳、乙一个字一个字冒），所以默认传输已经翻成 `cardkit`：`streaming_print_ms` 在 cardkit 下
  是 no-op（见下一条），而 `patch` 路径上的它只是「无害但没用」。自测方法：
  `tests/probe_render.py --typing`（两张卡交替长大 12 秒，一眼看得出哪张在逐字）、
  `--cardkit-prod`（走**生产代码路径**验整链）。
  **另一条传输的实现依据**（2026-09-13）：`tests/probe_render.py --cardkit` 把
  `cardkit.v1.card.create`（建卡片实体）→ `im.v1.message.create`
  （`{"type":"card","data":{"card_id":…}}`）→ `cardkit.v1.card_element.content`
  （按 `sequence` 逐帧写元素）→ `cardkit.v1.card.settings`（`streaming_mode: false` 收尾）
  整条链跑通，**每一步都是 `code=0`**；SDK 就位、Hermes 自身完全没用 CardKit（不冲突）。
  它同时发一张**公平的对照卡**（同样节奏、同样切分，但走 `message.patch` + `streaming_config`），
  两张并排留在 DM 里 —— 哪张逐字、哪张整段跳，一眼就能定「打字机是否需要 CardKit 实体」。
  在那之前不实现它：这是本项目「先量，不猜」的规矩，也是当初把它列为「确证后再付的复杂度」的原因。
  **现在它已经实现并且是默认传输**：设计边界与真机结果写在
  `docs/plan-6-effects.md` 的「阶段 9 实施记录」里（只换 native 流式帧的传输、
  任何一步失败都 fail-open 回落、翻默认前先过对抗审计 + 真机生产路径探针）。
  取值超出 `[1, 2000]` 毫秒会被退回默认 15ms，并在日志里留一条限流 WARNING
  （写错配置不会静默 —— 「想要最慢」却得到「最快」是必须能查出来的）。
- **`cardkit` 传输下有两个配置是 no-op**：`streaming_print_ms`（打字机由
  `card_element.content` 带来，实体卡不带 `streaming_config`，拧它没有任何效果）与
  `panel_expanded`（实体卡支持，但面板内容在 cardkit 下是**一个** markdown 子元素，
  展开态由建实体时的 `expanded` 决定；改配置要等下一次建卡才生效）。`unified_panel: false`
  在两条传输下都关得掉面板。
- **`cardkit` 传输的取舍**：它换来真正的逐字打字机，代价是三条硬约束 ——
  ① 卡片**结构在建实体时定死**（流式期间只按 `element_id` 写内容；**整卡替换**
  `message.patch`/`card.update` 会关闭流式会话。⚠️ 更正：CardKit 的**元素级/批量**接口
  `card_element.patch` / `card_element.create` / `card.batch_update` 实测在流式期间**可用**
  且不关会话，只是我们当前实现还没用它们）；所以那个折叠面板里的内容在 cardkit 模式下是**一个 markdown 字符串**
  （不是多个元素），观感一致但元素更少；② 面板边框的**状态色在收尾那一帧**才上（那时流式
  本来也结束），所以流式期间是灰边、结束才变绿/黄/红；③ 序号必须单调递增。
  失败的分档（2026-09-13 R5 起，逐条真机实测过）：**建实体失败 / 拿不到 SDK / 超硬上限 /
  正文写入拿到确定性拒收码** ⇒ 这一帧返回 `False`，核心随即**停用本回合的 native 流式**、
  改用普通发送/编辑（用户看到的仍然是一张卡，只是不再逐字；再不行才回落官方纯文本）；
  **元素通道拿到卡级死法**（`300309` 会话已关 / `300313` 元素不存在 / `300317` 序号冲突）
  ⇒ **降级成整卡 `message.patch` 续写同一张卡**（下面那条），不掉纯文本也不多出第二张卡；
  **消息被撤回/删除**（`230011`）⇒ 这一帧失败、插件不再往那条消息写（是否重新送达由核心决定，
  所以 DM 里**可能出现**一条新消息 —— 那是核心的回落，不是插件在补发）。
  **消息永远不会因为卡片出错而丢。**
  ⚠️ 注意「回落」的准确含义：它**不是**「自动换成 `patch` 传输继续流式」，而是掉出 native
  流式这条路（排查「卡片怎么变成一条条消息了」的人容易在这里被带偏）。
- **cardkit 下流式期间实时更新的东西与不更新的东西**（2026-09-13 更新）：
  ① **页脚**（`footer: true` 的上下文用量）**现在流式期间就会更新** —— 建实体时就把页脚元素
  建进卡里，之后每帧随正文一起刷新（`footer: false` 时不建、也一次都不写它的 id）；
  ② 与之同理，面板内容（推理轮 / 工具步骤）整个回合都在更新；
  ③ **仍然只在收尾那一帧出现的**：面板标题里的**模型 / 轮数 / 工具数 / 耗时**统计行
  —— 它是面板的 header 文本，改它属于结构性写入（会关流式会话），所以只能等收尾。
- **建实体时同时守两道墙**：整卡 JSON 超过飞书硬上限（128000 字节）或**递归**元素数超过 200
  （真机实测：递归 200 收下、204 拒收，码 `300305`），当场放弃 CardKit 这一帧、回落给核心 ——
  因为结构建实体时就定死，超了就是整张卡被拒（不像 `patch` 路径那样能分级丢掉面板）。
- **每帧的逻辑写预算是 2 次**（装饰一次 `card.batch_update` + 正文一次 `card_element.content`）：
  卡级写入上限按官方口径 10 次/秒，而帧节流窗口是 0.25s ⇒ 每帧最多 2 次写。装饰（面板 + 页脚）
  合并成**一次** batch；**正文最后写**（提交点在后，与核心「按最后一次成功帧记账」同口径）。
  装饰**内容没变就不发那次 batch**（面板/页脚往往好几帧不动）⇒ 稳态下每帧只写 1 次。
  ⚠️ 这是**逻辑写**口径，**不是**「每帧最多 2 次 HTTP 调用」：撞限流时同一个请求会退避重发
  （0.1/0.3/0.6，最多 4 次尝试，`uuid` 不变所以幂等）⇒ 单帧最坏 **8 次调用、约 2 秒**。
  之所以选「重试」而不是「早失败」：这一帧失败会让核心停用本回合的流式卡片，退化成纯文本。
  装饰写失败**不会**让整帧失败（标死 + 留 WARNING，卡片继续逐字）；只有正文失败才 fail-open。
  卡级死法（`300309` 会话已关 / `300313` 元素不存在 / `300317` 序号冲突）**不会**让你掉回纯文本：
  插件会**降级成整卡 `message.patch`**，继续更新**同一张卡**（不再逐字，但卡片与内容都在，
  也不会多出第二张卡）—— 装饰批量与正文写入**两条通道**撞上卡级死法都会触发降级。
  唯一的例外是 `300313` 落在**装饰**上：那只是某一个装饰元素在卡里没了（服务端会在 msg 里
  **点名它**），正文元素是另一个 id、照样写得进去 ⇒ 只标死被点名的那个、**保住打字机**。
  装饰失败的标死粒度：**服务端点名的 id 确实是这一批里的元素** ⇒ 只标死那一个；否则**整批标死**
  （msg 形状不止一种：点名上下文、id 被截断都会让「按名字标死」标错人，所以宁可保守）。
  代价是「本回合内装饰冻结」，但**收尾帧的整卡替换会一次补齐**（面板 / 页脚 / 状态色）。
- **面板的更新时机由核心决定**：Hermes 自己就跳过「文本没变」的中间帧
  （`gateway/stream_consumer_transport.py`：`if not finalize and text == self._last_sent_text`），
  所以**纯推理阶段（正文还是空的）根本不会有帧**，面板只能在正文开始增长之后才更新。
  长回答前的长时间思考期间，卡片会停在「⏳ 正在生成…」+ 首帧那一刻的面板状态 ——
  这是核心的帧策略，不是插件的取舍（我们无法凭空让核心多发帧）。
- **状态色的可信范围**：颜色来自 `on_session_end`（每回合一次，含非流式路径）。
  `/stop` 那一帧由插件自己重绘（核心不会再有收尾帧）。若某回合连一次流式帧都没有
  （没建卡），自然也没有卡可上色 —— 那时看到的仍是官方纯文本。
  正文超过字节预算（40000）时，状态色由 `cards.status_shell()` 保住（只带边框色的小面板，
  ≈545 字节，见上一条）；只有正文贴近飞书硬上限（128000）时才会为了「发得出去」放弃它。
  真机自测：`tests/probe_render.py --stop-redraw`。
- **卡片级 header 已去掉**（决策 D2）：模型名/轮数/工具数/耗时全在面板标题行。
  面板被关（`unified_panel: false`）或正文超过字节预算（40000）时，**这些信息会消失，
  但状态色不会** —— 超预算档会保留一个只带边框色的小面板（`cards.status_shell`）。
  这条是 2026-09-13 真机实测补出来的：早先那一档把面板整块摘掉，于是「正文发得出去、
  卡片也在，但 `/stop` 之后不变色」（载荷里连颜色都没有）。代价约 545 字节；
  正文大到贴近飞书硬上限（128000）时才会放弃这个色块，那时优先保发送成功。
  正文永远完整（见上面的降载策略）。真机自测：`tests/probe_render.py --stop-redraw`。

---

## 许可

MIT
