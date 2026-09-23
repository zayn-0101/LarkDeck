# Error/Result 块宿主切换：markdown + x-small（2026-09-23 真机）

## 背景

原实现：`core/cardview.py::_tool_output_div` 用 `div` + `text.tag=lark_md` + `text_size="x-small"`。
2026-09-23 真机对照（用户截图）：

* 候选 A `markdown` + notation：代码块字号不变；
* 候选 B `markdown` + x-small：代码块与 `**Error**` 标签一起变小，11–19 行栈仍可读、不溢出；
* 旧 `div.text=lark_md` + x-small：服务端 `code=0` 但客户端忽略 `text_size`（与 notation 同大）。

候选卡：`om_x100b6407fb0468a0df9a8617dab7f93`；用户结论「红框字号变小了，绿框字号没变」。

## 决定与实现

* `_tool_output_div` 改为返回 **`markdown` 组件**：`content=f"**{label}**\n```\n{block}\n```"`、
  `text_size="x-small"`、组件级 `icon`（Error → warning / Result → 代码块图标，`emoji` 模式不加图标）。
* 不拆元素：标签与代码文本同属一个 markdown 元素，整块一起变小；灰色/图标/字段白名单不变。
* 细节行（markdown / plain_text 两宿主）保持 `x-small`；`PANEL_TEXT_SIZE` 仍 `notation`。

## 断言 / 变异 / 夹具

* `test_v073_detail_and_error_rows_x_small`：detail（line/emoji）`x-small`、Error 元素
  `tag=markdown`、`text_size=x-small`、组件级 `icon` 不丢；标题 `notation`；
* `tests/check_cardview.py` Error 夹具同步改断言；
* `V073-1c`：把 Error 块 `x-small` 改回 `notation` 必须实红；
* golden 夹具无 Error 场景，保持 8 叶 detail 变化 + footer 叶不变（无需重生成）。

## 提交与重验

* 代码提交 `732cf88`；
* 因 `core/`、`tests/`、`plugin.yaml` 改动，P3 六分片在该提交重跑并重新盖章；结果见
  `docs/audits/v0.7.3/p3c-host-switch.md`（新增文件）与 `docs/verify-log.md`。
