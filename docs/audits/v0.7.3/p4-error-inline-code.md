# Error/Result 块最终形态：markdown + 逐行 inline code + x-small（形态③）

## 事实链

1. 旧 `div.text=lark_md` + `x-small`：服务端 `code=0`，客户端**忽略** `text_size`，与 notation 同大。
2. 换 `markdown` 宿主 + fenced code + `x-small`：普通文本与 `**Error**` 标签变小，但
   **飞书 fenced 代码块字号固定**，代码栈仍大（用户截图：① notation 与 ② 代码块行高/字号一致）。
3. 候选形态③：`markdown` + **每行一个 inline code span** + `x-small`；inline code 跟随
   `text_size` 缩放。候选卡 `om_x100b6400934d8900c16c222e85b90e7`，用户确认「② 明显更小且可读」。

## 实现

* `core/cardview.py::_inline_code_lines()`：逐行包 `` `line` ``；行内含反引号时用双反引号定界；
  空行用 `` ` ` `` 占位。
* `_tool_output_div`：`tag=markdown`、`content=f"**{label}**\n{inline lines}"`、
  `text_size="x-small"`、组件级 `icon`（Error → warning / Result → 代码块图标）、
  `margin="0px 0px 0px 22px"`。
* 不拆元素：`**Error**` 标签与代码行同属一个 markdown 元素，一起变小；代价是失去整块代码
  背景/横向滚动条，长行可折行 —— 已由用户目视接受。

## 代码链

* `4350ace`：`_tool_output_div` 落地形态③（逐行 inline code）；
* `9a1c0be`：冻结前对齐 `plugin.yaml` 描述与 `core/cards.py` 注释；
* `b9c01e5`：审计 A 假绿加固第一批（多行硬字面量 + V073-1d..1i 六条 mutation）；
* `821a17d`：审计 A 跟进第二批（emoji 分支独立 content/margin + V073-1j）；
* `fbb05ac`：复杂度门禁改 CPU-time/取最小值（6 分片并发下 test_units 不再抖）；
* `a2290da`：`_args_preview` 恢复墙钟 min-of-8（fail-closed 回调）+ 新增 V073-1k sleep 回归对照；
* 全量 P3 在 `fbb05ac` 重跑并重新盖章，证据见 `p3d-inline-code.md`。

## 断言 / 变异（审计 A 后）

* `test_v073_detail_and_error_rows_x_small`：Error 元素 `tag=markdown`、`text_size=x-small`、
  `icon` 存在、content 不含 ``` 且含 inline code；细节行 line/emoji 均 x-small；标题 notation。
* `test_v073_inline_code_lines_split_per_line`：`"a\nb" == "`a`\n`b`"`、空行 `` ` ` ``、
  行内反引号双定界、Error 多行内容逐行、icon token/margin 硬字面量、emoji 分支无 icon、
  `ICON_ERROR/ICON_RESULT/ICON_HINT_MORE/ICON_DETAIL` 字面量。
* `tests/check_cardview.py` Error fixture 同步断言（含 `text_color`/`text_weight` 负断言）。
* 新增 V073-1d..1k：逐行退化、`PANEL_TEXT_SIZE` 漂移、Error/Result token 互换、margin 删除、
  emoji 仍挂图标、`text_size` 删除、emoji 分支独立退化、`_args_preview` sleep 墙钟回归；`-k V073-1` 完整第一支红模式 **11/11 实红**（无 crash）。

## 重验

* 冻结提交 `a2290da`；v2 runner 先逐片校验 rc/选中条数/红名集合/声明门禁 ∈ 断言红/对照绿灯/
  seed verdict+at，再 merge，且不带旧 fa `--allow-at`；
* 证据：`~/.larkdeck-scratch/v0.7.3/evidence-a2290da/`（6 log + 6 seed + 6 inventory + sha256）。
