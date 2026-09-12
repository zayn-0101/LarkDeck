# 踩坑记录与开工索引

> 这是**跨会话记忆**：只记「不写下来就会重踩」的东西，以及「动手前该读哪份文档」。
> 不是第二份 `README.md`（功能与用法在那边），也不是第二份 `AGENTS.md`（不变量与约定在那边）。
> 机制细节一律放指针，不在这里复述。

## 一、动手前先查这张表

| 要改什么 | 先读 | 改完必跑 |
|---|---|---|
| 卡片结构 / JSON 字段 | `README.md` 的「卡片方言」两张表 | `tests/probe_render.py`（真发卡到你的飞书 DM） |
| 发送 / 更新 / 流式帧 | `core/adapter.py` 顶部 docstring | 四个门禁（见 `AGENTS.md`「验证」） |
| 钩子 / 指标 / 面板 | `docs/metrics-and-hooks.md` | `tests/check_hooks.py` |
| Hermes 私有接口名 | `core/compat.py`（唯一存放处） | `tests/check_override.py` |
| 配置项 | `_DEFAULTS` 与 `plugin.yaml` 的 `config_schema`（两处必须同步） | `tests/check_override.py` 的配置桥接断言 |
| 装到新机器 | `docs/switch-from-hfc.md` | 启动日志里的 `[larkdeck]` 自检行 |

## 二、这个项目的头号失败模式是「静默」

不报错的失败比报错的失败贵得多。已经踩过的静默坑：

- **按钮点了没反应** —— 1.0 的 `action` 容器混进 2.0 卡，飞书拒收但不报错；
- **折叠面板退化成普通行** —— `header.title` 塞了 `lark_md`，或者没给 `icon`
  （没有 icon 就没有展开控件，面板变成一个永远打不开的抽屉）；
- **配置写了不生效** —— 键路径写错就等于没有读取路径，YAML 全部空转；
- **钩子没挂上** —— 只是页脚少两段、面板空着，卡片照常渲染，从表面看不出来；
- **每一帧卡片更新都失败** —— `message.update` 拒收 `interactive`，而失败会**回落**成内置
  文本编辑：消息照样发出去，只在 WARNING 级留一行，很容易以为一切正常。

**推论：每加一层能力，都要顺手给它配一个「能自证」的东西。** 要么是门禁里的一条断言，
要么是限流的诊断日志（现有例子：`面板无数据` / `pre_tool_call 钩子触达` / `指标已接入`）。
不要拿「看起来是好的」结案。

## 三、两个验证盲区

- **`install.sh --copy` 的 `FILES` 数组是手写的。** 新增模块漏同步会装出一个「少了指标采集
  和钩子订阅」的插件 —— 而**默认软链模式永远测不出来**，只有 NAS 会中招。
- **配置键有两处真相**：文档写的路径、代码真正读取的路径。曾经文档写
  `plugins.larkdeck.*`，代码里却没有任何读取路径，静默空转。`check_override.py`
  目前是唯一端到端断言这件事的地方。

## 四、交付约定（此前只活在 commit message 里）

每个 commit 的 message 末尾附一行**实测**门禁结果：

```
测试：63/63 · OVERRIDE OK · HOOKS OK · CLARIFY E2E OK
```

改过卡片结构就再加 `probe_render 真机 N 卡 code=0`。只写跑过的；没跑就写没跑。
