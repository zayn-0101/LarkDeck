# v0.7.3 P4 真机回退决定（宿主矩阵 C 不生效）

日期：2026-09-23 · 用户目视 + 截图 + 像素测量。
被测代码：`6207c3c`（回退前）→ 回退提交 `db58dc5`（Error 块回退 notation，重跑 P3）。

## 1. 系统提示卡（用户截图确认）

三张按 1/3→2/3→3/3 发的对照卡：

| 卡 | message_id | 结果 |
| --- | --- | --- |
| 1/3 改前（v0.7.2 从 `6fd68f3` 提取代码生产渲染）系统提示 | `om_x100b6406193fcca4ddcfdfb9970db9f` | 有绿色状态头 + 面板 + 页脚 `✅ 已完成 · Test Model · ctx…`（用户截图确认） |
| 2/3 改后系统提示 | `om_x100b640619337ca8dfa85d019ef2229` | 无状态头/面板/页脚/`✅`（用户截图确认） |
| 3/3 改后真实回合 | `om_x100b640616c6d8b0de2c17b37ce9714` | 无状态头，页脚 `✅ 已完成 · Test Model · ctx…` 保留（用户截图确认） |

结论：用户可见目标 ② 成立 —— 已知系统提示静默，真实回合 `✅ 已完成` 一字不动。

## 2. 宿主矩阵 x-small（用户目视 + 截图像素测量）

合并验证卡 `om_x100b64060aedaca8c4c3b8cbce81bca`（A/B/C 各上下两行：notation 对照 / 生产 x-small）。
用户反馈：C 上下看起来一样大；并发来截图。对截图做行高/字形带测量：

| 宿主 | notation 字形带 | x-small 字形带 | 结论 |
| --- | --- | --- | --- |
| A `markdown`（line 细节行） | 23 px | 19 px | **确实更小** ✓ |
| B `div.text=plain_text`（emoji 细节行） | 21 px | 17 px | **确实更小** ✓ |
| C `div.text=lark_md`（Error/Result 块） | 26 px | 26 px（行距同为 44 px） | **客户端忽略 `text_size`** ✗ |

C 另有焦点卡 `om_x100b64062dbdfca8c4297b234ace9ca` 供复核；用户目视同样认为上下一样大。

## 3. 处置（按计划 §6.10.10 回退规则）

* `core/cardview.py::_tool_output_div` 的 `text_size` 从 `"x-small"` 改回 `PANEL_TEXT_SIZE`（`notation`）；
  Error/Result 块不拆元素、灰色/图标/字段保持不变。
* 细节行两宿主（`markdown` / `plain_text`）继续 `x-small`（A/B 已证明确实更小）。
* 断言/变异同步：`test_v073_detail_rows_x_small_error_falls_back_to_notation`、
  `tests/check_cardview.py` Error 夹具改断言 `notation`；`V073-1c` 反向成
  「notation 被错误改成 x-small」并必须红。
* 文档：README/CHANGELOG/plugin.yaml/release notes 改为「Error 块回退 notation（无运行时自动回退）」。
* 因代码/测试/plugin 改动，P3 六分片全量在 `db58dc5` 重跑并重新盖章。

## 4. 用户终验仍待确认

* 本回退提交部署后的 Error 块视觉（应回到原 notation 大小、长栈可读）；
* `send 判定 turn=` 日志中真实用户消息`turn=True`、系统提示`turn=False`；
* 发布前 `.deploy == HEAD` 的 release `--check` 通过。
