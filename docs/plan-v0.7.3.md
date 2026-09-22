# v0.7.3 计划（第 1 项：细节行字号 `x-small`）

> 承接 v0.7.2（已发布：tag `v0.7.2` = `6fd68f3`）。本文件是**下一批的唯一执行依据**；
> 老规矩：**先出计划 → ≥3 路子代理对抗审计 → 收敛后才动手**；每阶段结束再过审计；
> 用户真机确认后才发布。

## 0. 用户 2026-09-22 拍板（原话 + 探针证据）

| 诉求 | 结论 | 证据 |
| --- | --- | --- |
| 工具名（绿框）想**更粗** | ❌ **做不到，保持现状**（用户：「粗体没办法了对吧，那就这样」） | 卡 5 被服务端整卡拒：`200621 unknown property, property: text_weight, path: ROOT -> body -> elements[1](tag: div) -> text(tag: plain_text)`；`markdown` 只有 `**粗**` 一档 ⇒ 无更强档 |
| 细节行（红框）想**更浅** | ❌ **保持 `grey`**（用户：「颜色三行一样」） | 卡 1 三行（`grey` / `#B0B0B0` / `#C8C8C8`）真机观感一致 ⇒ 十六进制被客户端忽略；`grey` 已是枚举里最浅 |
| 细节行（红框）想**更小** | ✅ **采用 `text_size: "x-small"`**（用户：「有一个字号更小一点」，截图标红该行） | 卡 4（`x-small`）服务端 `code=0` 且真机确实更小；卡 3（`small`）观感与 `notation` 无差别 |

探针卡 id（留档）：更浅 `om_x100b641cf84808a4c02545fa7a8f16c` · 更粗
`om_x100b641cf85f84a0c2ebc28566514e6` · `small` `om_x100b641cf85134a0dd4367acb2996a0` ·
`x-small` `om_x100b641cf86580a0c21bcfe8d2894fb` · `text_weight`（被拒）— 未送达。

## 1. 变更清单（**待审计后冻结**）

| 文件 | 改动 | 约束 |
| --- | --- | --- |
| `core/cardview.py` | `_tool_detail_div` 的 `text_size`：`PANEL_TEXT_SIZE` → `"x-small"` | 细节行（参数/预览）是**独立元素** ⇒ 只影响它，不影响工具名/状态词 |
| `core/cardview.py` | `_tool_output_div`（Error / Result 代码块）的 `text_size`：同改 `"x-small"` | ⚠️ 该元素里 `**Error**` 标签与代码文本**同属一个元素** ⇒ 标签会一起变小（不拆元素）。若审计认为该拆，改成「标签一个元素 + 代码一个元素」 |
| `tests/test_units.py` | 硬字面量：细节行/错误块的 `text_size == "x-small"`；工具行其它部分的 `text_size` 不变 | 只加/改相关用例名，避免 500 条变异作废 |
| `tests/mutate_check.py` | 新增变异：细节行退回 `notation` / 错误块退回 `notation` | 每条 `-k` 完整模式实红 |
| `tests/check_cardview.py` | 字段白名单/夹具同步（若涉及） | 分档规则不变（`div.icon` 可带 size、`markdown.icon` 不可） |
| `tests/golden_cardkit_trace.json` | 重生成（工具细节行/错误块的 `text_size` 变了） | `--check` 必先一致、diff 逐条解释 |
| `docs/` | 本项完成记录 + 发布说明 | 只追加，不改历史行 |

## 2. 验证计划（出口判据，机械可核）

1. `py_compile` + `run_fast --full`（8 步全 OK）+ `mutate_check --preflight`（锚点全可用）；
2. 新增变异 `-k` 完整模式**实红**（贴输出）；
3. 黄金夹具重生成 + diff 逐条解释（**只应出现** 细节行/错误块的 `text_size` 变化）；
4. 全量变异重验：6 分片完整模式、独立账本、坏 0；合并后 `full_audit_at == 当时的 HEAD`、`n_inh == 0`；
   实测墙钟写回（v0.7.2 那轮参考：499 条 ≈25 分钟，机器空闲时更快）；
5. 真机探针卡：一张，含「改前 / 改后」两行细节行对照 + 一个错误块，用户目视确认；
6. 发布：用户终验 → `release-v0.7.2.py` 相应改 ref（或新建 v0.7.3 脚本）→ tag/`.deploy`/重启。

## 3. 登记（本批不做，只留档）

* **`text_weight` 不存在**（`plain_text` 无字重字段）——「工具名更粗」永久关闭，除非升级卡版本。
* 十六进制颜色在真机被忽略 ⇒ 卡 2.0 的颜色就是枚举，`grey` 是最浅档。
* 4 处**代码注释**里的历史口径（`core/context.py` / `core/i18n.py` / `core/adapter.py` /
  `tests/mutate_check.py`）—— 顺手改会动指纹 ⇒ 与本项**同一次**全量重验里一起做。
* 清场（审计 worktree / 临时目录）待用户点头。

---

## 4. 第 2 项（用户 2026-09-22 新报）：**系统提示卡不许带「已完成」标识**

**现象**（用户截图，22:00）：`Gateway online — Hermes is back and ready.` 与
`Gateway restarting — Your current task will be interrupted…` 两种**系统提示卡**底部都挂着
`✅ 已完成`（用户：「Hermes 的系统提示，这些不要加这种已完成标识」）。其中 `restarting` 那张
尤其错 —— 那一刻回合是被**打断**的，却写「已完成」。

**机制（已定位到代码，不是猜测）**：`core/adapter.py::_ld_footer()` 的状态词有一段**兜底** ——
`status_text = _ld_status_text(status or (panel_snap.get("status") …))`，即调用方不传 `status` 时
去读 `panel.snapshot()` 里**上一个回合**留下的状态。系统提示卡不属于任何回合，但紧跟在
「刚完成的回合」之后 ⇒ 抄到陈旧的 `✅ 已完成`。

**变更清单（待审计）**：

| 文件 | 改动 | 约束 |
| --- | --- | --- |
| `core/adapter.py::_ld_footer` | ① **删掉 `panel_snap` 状态兜底**（状态只认显式传入）；② 「非回合消息」⇒ 返回 `None`（不渲染页脚）：判据 = `started` 为空 **且** 无显式 `status` | 流式/收尾/心跳/`/stop` 全部**显式传** status 或 started ⇒ 不受影响；`_ld_note_text` / `_ld_render_card` 等调用点逐一核 |
| `tests/test_units.py` | 新增：一次完整回合 → 紧接着一条「系统提示样式」的 `send()` ⇒ 断言卡片**无 footer 元素 / 无状态词**；并断言**流式收尾卡仍有** `✅ 已完成`（防误伤） | 用例名新增（旧名不动） |
| `tests/mutate_check.py` | 新增变异：把状态兜底加回 `_ld_footer` ⇒ 必须红 | `-k` 完整模式实红 |
| `tests/golden_cardkit_trace.json` | 若夹具场景受影响则重生成（diff 逐条解释） | 预期**不**受影响（夹具里都是回合内） |
| 真机探针 | 下一次网关重启/系统提示卡截图确认无 `✅ 已完成` | 用户目视 |

**与本批第 1 项（细节行 `x-small`）合并做一次全量重验**（同一棵树上改完再跑 6 分片），发布为 **v0.7.3**。
