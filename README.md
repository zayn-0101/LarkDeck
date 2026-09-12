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

流式本身走的是 Hermes 官方给卡片类平台设计的 **edit 传输**（DingTalk AI Card 用的同一条路）：`send()` 建卡 → `edit_message()` 连续更新 → `edit_message(finalize=True)` 封口，由 `REQUIRES_EDIT_FINALIZE = True` 驱动。配置里 `streaming.transport: edit` 就是它。

> 为什么不去 `import` 官方适配器模块：目录名、包名、加载方式都可能变。从注册表拿"上一个工厂"是唯一不依赖路径的接法。

---

## 卡片方言：1.0 与 2.0（改卡片前必读）

飞书卡片有两套互不兼容的写法，**混用不会报错，只会让按钮静默失灵**：

| | legacy 1.0 | schema 2.0 |
|---|---|---|
| 结构 | 顶层 `elements` | `config` / `header` / `body.elements` |
| 按钮 | `{"tag": "action", "actions": [...]}` | `behaviors` |
| 点击能到服务端吗 | ✅ 走 `p2.card.action.trigger` | ❌ `behaviors` 是客户端交互 |
| 流式（打字机） | ❌ | ✅ `streaming_mode` |
| 可折叠面板 | ❌ | ✅ `collapsible_panel` |
| `config.summary` | 不需要 | **流式时必带**，漏了通知栏空白 |
| `note` 小字脚注 | ✅ | ❌ **飞书已废弃** → 改用 `footnote()` |

所以本项目的分界是：

- **要接点击的卡 → 整条链路 1.0。** 澄清交互卡的*待答卡*和点击后*回填卡*都必须是 1.0 ——
  回填卡混成 2.0 会被飞书**静默丢弃**，表现为"点了没反应"。
- **流式回复卡 → 2.0。** `streaming_mode` 和统一面板的 `collapsible_panel` 是 2.0 独有能力。

这条规则由 `tests/test_units.py::test_clarify_card_must_be_legacy_dialect` 锁住。

### 元素级方言差异（实测，非文档推断）

飞书对卡片**不做静态校验** —— 不支持的字段是*发送时*才拒。所以下面这些只能靠真发一次卡才知道：

| 元素 | 1.0 | 2.0 | 依据 |
|---|---|---|---|
| `note` | ✅ | ❌ | 飞书返回 `230099 / ErrCode 200861 · cards of schema V2 no longer support this capability` |
| `action`（按钮行） | ✅ | ❌ | 1.0 的 `action` 容器嵌进 2.0 卡会被拒，点击永远到不了服务端 |
| `collapsible_panel` | ❌ | ✅ | 2.0 专属 |
| `i18n_content` | ✅ | ✅ | 文本元素级字段，两个方言都实测被接受 |

**结论：本地单测只能验结构，验不了合法性。** 改完卡片必须跑 `tests/probe_render.py` ——
用真凭据把三类卡发到自己的飞书 DM，看飞书返回的 `code`（`tests/probe_render.py` 会
自动先删掉上次发的探针卡，不留垃圾）。`note` / `action` 两条已在本地被
`test_dialect_element_exclusivity` 锁死。

---

## 功能

| 功能 | 状态 |
|---|---|
| 流式卡片（同一张卡原位更新） | ✅ |
| 统一面板（推理 + 工具合并为一个可折叠底部面板） | ✅ |
| 澄清交互卡（按钮点击直接作答，不再手打选项） | ✅ |
| 模型别名 | 计划中 |
| 双语 UI（跟随飞书客户端语言） | ✅ 机制已实现，待真机确认 |
| 脚注（耗时 / 工具次数） | ✅ 语言无关（纯数字符号） |
| 上下文进度条 | 计划中 |
| 推理面板上限 / 元素溢出保护 | 计划中 |

**界面文案双语**用的是飞书卡片原生的 `i18n_content`：服务端只发一份卡片，每个文本元素同时带中文和英文，客户端按自己的语言设置挑一份渲染。AI 生成的正文**不翻译**。

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

重启网关即可。启动日志里会有一行自检结果：

```
[larkdeck] 自检通过：feishu 平台已由 larkdeck 接管（Hermes 0.21.1）
```

如果自检失败，会打 `ERROR` 并**保持官方适配器原样工作** —— 卡片不生效，但飞书不会被弄坏。

### 卸载

从 `plugins.enabled` 里删掉 `larkdeck`，再删 `~/.hermes/plugins/larkdeck/` 即可。没有任何源码被改过，所以不存在"卸载后残留注入"。

---

## 配置

在 `~/.hermes/config.yaml` 里：

```yaml
plugins:
  larkdeck:
    cards: true            # 用卡片渲染回复（关掉则完全退回官方纯文本行为）
    i18n: true             # 界面文案双语
    unified_panel: true    # 推理 + 工具合并为一个底部面板
    clarify_cards: true    # 澄清用按钮卡
```

也可以用环境变量临时覆盖，如 `LARKDECK_CARDS=0`。

---

## 测试

```bash
python3 tests/test_units.py            # 纯单测，零网络零 Hermes 依赖
python3 tests/check_override.py        # 对真实 Hermes 安装验证平台覆盖是否生效
python3 tests/check_clarify_e2e.py     # 澄清卡端到端：发送 → 点击 → 网关解除阻塞
python3 tests/probe_render.py         # 真发卡片到自己的飞书 DM，验飞书接不接受（要凭据）
```

`check_clarify_e2e.py` 用的是从平台注册表里取出来的**真内置适配器类**，只把最底层
`_feishu_send_with_retry` 换成捕获器，**不连飞书、不发网络请求**。它能抓到桩类抓不到
的问题：签名不匹配、私有原语改名、覆盖没生效、返回类型不对。

`probe_render.py` 是唯一**连真实飞书**的测试，也是唯一能判定卡片合法性的手段 ——
它会先删掉自己上次发的探针卡再发新的。只验渲染，**不验点击**（点击路由要平台被
LarkDeck 接管后才生效）。

---

## 已知限制

- **必须和官方适配器同一进程**：官方 `feishu` 平台被禁用时，LarkDeck 无处附着。
- **依赖 6 个官方适配器的内部方法**（`_feishu_send_with_retry` 等）。全部集中登记在 `compat.py` 里，并用 `probe_adapter_class()` 在运行时校验 —— 官方哪天改了名字，自检会直接报出来，而不是静默失效。
- ~~**`i18n_content` 的元素级支持需真机确认**~~ → **已实测确认**（2026-09-12）：
  文本元素同时带 `content` 与 `i18n_content`，1.0 与 2.0 卡均被飞书接受。
  兜底仍然安全：客户端不认时回落到 `content`，不会让卡片发不出去。
  **注意 AI 生成的正文不翻译**，双语只覆盖界面文案。
- 与 hermes-feishu-streaming-card（HFC）**不能共存**：两边都要接管 `feishu` 平台，且 HFC 还改了源码。切换步骤见 `docs/`。

---

## 许可

MIT
