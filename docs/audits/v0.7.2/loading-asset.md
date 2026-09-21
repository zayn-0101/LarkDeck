# P2 加载指示：资产与真机结论

**日期**：2026-09-21 · **阶段**：v0.7.2 P2 · **探针**：`tests/probe_loading.py`

## 结论（用户真机目视）

用户 2026-09-21 回话：**① 会动**。

对照卡发的是两张同位置元素（探针消息 `om_x100b643abd6394b0dfa26a200d65018`）：

| 版本 | 元素形状 | 用户看到 |
| --- | --- | --- |
| **① 共享 key** | `div` + `custom_icon(img_v3_02vb_496bec09-4b43-4773-ad6b-0cdd103cd2bg)` + `text:" "` | **会动** ✅ |
| ② 静态 `standard_icon` | `div` + `standard_icon(setting-inter_outlined)` | 不动（静态字节图） |

⇒ **采用 ①**：`core/cardview.py::SPINNER_IMG_KEY` 直接用三家共享 key，**不实现上传**。

## 为什么不做 `im.v1.image.create` 上传

v0.7.2 计划 P2 原先写「上传一次并缓存」（当时的假设是「参考实现都是这么做的」）。审计 C 逐仓核对
**证伪了这个前提**：aiduPOP `cardkit/elements.py:103`、CLS `builder.py:23`、FC `builder.py:24`
**三家硬编码的是同一个 key**，三家都没有 spinner 上传代码。既然共享 key 在真机会动，
上传只是多一条会在失败时把建卡写坏的路径（无效 asset ⇒ 300313 ⇒ 整条结构化装饰链掉回纯文本）。

为了「万一以后要换成自有资产」，仍保留一个**一行可切**的注入点：

* `cardview.set_spinner_img_key(key)` → 之后 `loading_hint_element()` 用注入的 key；
* key 为空 ⇒ 回落静态 `standard_icon`（宁可「不动」也不能把建卡写坏）。

## 元素形状（逐字段对齐 aiduPOP `_loading_element`）

```json
{"tag": "div", "element_id": "loading_hint",
 "icon": {"tag": "custom_icon", "img_key": "img_v3_02vb_...", "size": "16px 16px"},
 "text": {"tag": "plain_text", "content": " "}}
```

用户口径（2026-09-21 反馈 #4）：「正在加载上下文…」那条文案**不对** —— 要的是
**会动、无文字**的状态指示。所以 `text` 只有一个空格；**没有** i18n 文案节点。

## 门禁

* `test_v4_14`：建卡插入 → 形状逐字段（含「无文字」）→ 首字即删 → 收尾整卡 patch 不含它；
* `test_v4_14b`：资产契约（`img_v` 前缀）+ key 缺失时的静态回落（仍然无文字）;
* `test_v4_14c`：删除失败的**有限重试 + 换号**（同 uuid 重发会撞 200770）+ `300313` 视为「已删掉」；
* 变异：`V4-32`（首字后不删）、`V4-34`（删失败也清标志）、`V4-35`（拿不到 key 时不回落）必须实红。

> 假 CardKit **不校验资产**（`img_key` 在假客户端里只是一段字符串），所以「会动」这件事
> 只能由**真机探针 + 人工目视**证明 —— 本文件的结论就是那份记录。
