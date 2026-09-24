# 架构

> 读者：第一次读 LarkDeck 代码的开发者。
> 读完这一页，你应该能回答三个问题：卡片层怎么接进 Hermes、过程数据从哪来、失败时往哪里退。

## 全景

LarkDeck 是 Hermes 的平台插件，只做两件事：

1. 通过 `ctx.register_platform()` 覆盖内置 `feishu` 平台；
2. 通过 `ctx.register_hook()` 订阅官方钩子，拿卡片需要的过程数据。

不修改 Hermes 源码，不 monkeypatch，不依赖内置适配器的目录结构。

```text
Hermes gateway
     │ 解析 feishu 平台
     ▼
platform_registry ─── 最后写入者胜 ───► LarkDeck 工厂
     │                                      │
     │ 内置工厂先造一个实例                 │ 问出内置适配器的真实类
     ▼                                      ▼
内置 FeishuAdapter 类 ──────────► merged_class(Mixin + 内置类)
                                            │
                     ┌──────────────────────┴──────────────────────┐
                     ▼                                             ▼
             适配器覆盖层 (adapter.py)                    官方观察钩子 (hooks.py)
        send / edit_message / send_clarify              只写内存、异常自吞、恒返回 None
        _on_card_action_trigger                                │
                     │                                         ▼
         ┌───────────┼────────────┐                 panel.py / context.py
         ▼           ▼            ▼                （面板数据 / 指标与账本）
    cards.py    cardview.py    i18n.py
   卡片 JSON   结构化元素树     界面文案
```

## 平台覆盖

`register()` 从注册表拿到内置 `feishu` 的 entry，再把工厂注册回去：

1. 先解析内置 entry。Hermes 新版本可能把 bundled 平台注册成延迟加载器，因此要避开
   插件加载线程里的注册表锁；`_resolve_builtin_platform_entry()` 负责这三条路径。
2. 内置 entry 的字段整条透传 —— `register_platform()` 是替换而非合并，少传一个字段
   等于关掉一项能力。
3. 工厂是 `build_adapter(base_factory, config)`：先造一个内置实例问出它的真实类，
   再用 `type("LarkDeckFeishuAdapter", (LarkDeckMixin, 内置类), {})` 直接构造。
   实例的内存布局和 MRO 都保持干净，零参 `super()` 正常工作。
4. 探测内置类必需接口；任何一步失败都返回原来的 `base_factory(config)`，飞书照常工作。

`merged_class()` 会剥掉可能已有的 LarkDeck 层，避免插件热重载时套娃 —— 混入层套两层会
让 `super()` 回落路径执行两遍。

## 模块职责

| 模块 | 职责 |
|---|---|
| `core/adapter.py` | 覆盖层本体：`LarkDeckMixin`、`merged_class()`、`build_adapter()`、`register()`；四条覆盖路径；native streaming（官方原生流式）状态机；CardKit / patch 两条传输；卡片追踪、`/stop` 中止重绘、澄清点击。 |
| `core/cards.py` | 纯函数卡片构建，无 I/O：两种澄清方言、卡片 JSON、页脚、截断与预算（元素数 / 字节数）、CardKit 实体卡骨架、面板 markdown、脱敏后的工具预览。 |
| `core/cardview.py` | 结构化元素树：`CardView` / `PanelView` / `ToolStepView` 等数据类与纯函数构造器；token、图标、字号表。只产出 dict，由 adapter 交给 CardKit API。 |
| `core/panel.py` | 面板数据层：推理轮、工具步骤、回合结局、正文累积；`chat_id → session_id` 归属与回退；按会话分桶、TTL / 容量淘汰、参数脱敏。 |
| `core/context.py` | 运行时指标与账本：模型名、上下文用量、缓存命中、首字节延迟（TTFB）；入站心跳、写卡帧数 / 失败数、掉回纯文本、错误码与运行时长。页脚和 `/larkdeck status` 都读它。 |
| `core/hooks.py` | 八个官方观察钩子的订阅与回调。只写内存、异常自吞、永不返回 directive；订阅清单以 `compat.OBSERVED_HOOKS` 为单一事实来源，自检与门禁都会核对。 |

辅助边界：`core/compat.py` 是 Hermes 私有接口名的唯一存放处；`core/i18n.py` 只放界面文案，
不翻译 AI 正文；`__init__.py` 只把 `register` 转发出去。

## 数据流

```text
官方钩子 ──► panel.py / context.py（进程内快照）──► adapter 渲染时读取
                                                        │
                                                        ▼
                                        cards.py / cardview.py ──► 飞书 API
```

钩子载荷只有 `session_id`；卡片路径只有 `chat_id`。`panel.bind_chat_session()` 用
`pre_gateway_dispatch` 观察到的映射做确定性归属，拿不到才退回「最近活跃会话」；
`context` 的页脚指标是进程级最后一次调用快照，这是零源码改写换来的取舍。

## 一张卡的生命周期

```text
send_stream_frame("") ──► 中间帧（累积全文）──► send_stream_frame(finalize=True)
        seed                     panel + body                   整卡收尾
```

1. **seed（空文本）**：建卡并记下 `message_id`。CardKit 车道建实体卡 + 发消息，
   元素骨架一次定死；patch 车道发一张普通卡片。此时正文为空，面板只有空壳。
   没有活跃流可收尾时返回 `False` 是正常路径，不算写卡失败。
2. **面板 / 正文更新**：每一帧的 `text` 是累积全文。正文的 own 来源是
   `on_stream_delta(kind="text")`，面板来自 `panel.snapshot()`，页脚来自 `context`。
   CardKit 期间只按 id 写元素内容；patch 期间整卡替换。帧按回合串行、按最小间隔节流。
3. **收尾**：`finalize=True` 时做 markdown 卫生、读权威结局（完成 / 报错 / 中止）给状态色，
   CardKit 用整卡 patch 关掉 `streaming_mode`；patch 同样整卡替换。随后清流状态与追踪。
   澄清边界是例外：识别到等待选择时不关流，原地写等待文案，点击后继续更新同一张卡。

任何一帧失败都返回 `False`，核心自动关掉本回合 native 并回落 `send/edit`。
这条 fail-open 链是官方契约：卡片是增强，绝不能因为卡片报错而丢消息。

## CardKit 与 patch 的边界

`native_transport` 决定本回合走哪条线，认不出的值按 patch 处理。

| | CardKit | patch |
|---|---|---|
| 默认 | `native_transport: "cardkit"` | 配置为 `"patch"` 或值无法识别 |
| seed | 建实体卡 + 发消息，固定元素骨架 | `send()` 发一张卡片 |
| 流式更新 | 只写元素内容 / batch_update，**不做整卡替换** | `message.patch` 整卡替换 |
| 收尾 | 整卡 patch，关闭流式会话并上最终状态色 | 整卡 patch |
| 失败 | 任一步失败 → 核心回落；元素级死法 → 同卡降级到 patch 车道；容量墙 → 封旧卡开新卡 | 瞬态码退避重试；否则核心回落 |

边界只有一条：**CardKit 流式期间不能整卡替换**，整卡替换会关闭流式会话，后续元素写会
得到 `300309`。需要结构变化时只能在 seed 建好，或另建一张卡。元素 id 一律从建卡 JSON
里抽出来，避免「卡里没有但代码以为有」的 300313 错误。
