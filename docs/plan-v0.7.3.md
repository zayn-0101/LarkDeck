# v0.7.3 计划（细节行字号 `x-small` + 系统提示去状态词 + 注释口径）

> 承接 v0.7.2（已发布：tag `v0.7.2` = `6fd68f3`）。本文件是**下一批的唯一执行依据**；
> 老规矩：**先出计划 → ≥3 路子代理对抗审计 → 收敛后才动手**；每阶段结束再过审计；
> 用户真机确认后才发布。

## 0. 用户 2026-09-22 拍板（原话 + 探针证据）

| 诉求 | 结论 | 证据 |
| --- | --- | --- |
| 工具名（绿框）想**更粗** | ❌ **做不到，保持现状**（用户：「粗体没办法了对吧，那就这样」） | 卡 5 被服务端整卡拒：`200621 unknown property, property: text_weight, path: ROOT -> body -> elements[1](tag: div) -> text(tag: plain_text)`；`markdown` 只有 `**粗**` 一档 ⇒ 无更强档 |
| 细节行（红框）想**更浅** | ❌ **保持 `grey`**（用户：「颜色三行一样」） | 卡 1 三行（`grey` / `#B0B0B0` / `#C8C8C8`）真机观感一致 ⇒ 十六进制被客户端忽略；**不再声称「grey 是最浅枚举」**（无官方出处，§6.6 降为弱证据；文档只写「实测十六进制无效」） |
| 细节行（红框）想**更小** | ✅ **采用 `text_size: "x-small"`**（用户：「有一个字号更小一点」，截图标红该行） | 卡 4（`x-small`）服务端 `code=0` 且真机确实更小；卡 3（`small`）观感与 `notation` 无差别 |

探针卡 id（留档）：更浅 `om_x100b641cf84808a4c02545fa7a8f16c` · 更粗
`om_x100b641cf85f84a0c2ebc28566514e6` · `small` `om_x100b641cf85134a0dd4367acb2996a0` ·
`x-small` `om_x100b641cf86580a0c21bcfe8d2894fb` · `text_weight`（被拒）— 未送达。

## 1. 变更清单（**待审计后冻结**）

| 文件 | 改动 | 约束 |
| --- | --- | --- |
| `core/cardview.py` | `_tool_detail_div` **line 分支**的 `text_size`：`PANEL_TEXT_SIZE` → `"x-small"`（emoji 分支的 `plain_text` 同步改） | 细节行是**独立元素** ⇒ 只影响它；`PANEL_TEXT_SIZE` 本身保持 `"notation"`（其它元素继续用） |
| `core/cardview.py` | `_tool_output_div`（Error / Result 代码块）：`text_size` → `"x-small"` | ⚠️ 该元素里 `**Error**` 标签与代码文本**同属一个元素** ⇒ 标签一起变小（用户已确认不拆元素） |
| `core/adapter.py` | **Design B**（§6.1）：`send()` 显式 turn 判定；`_ld_render_card(..., turn_card=)` 必填；`_ld_footer` 退纯格式器（删 `panel_snap` 兜底、默认 `turn_card=False` ⇒ None）；`_ld_frame_footer` 显式解析状态；非回合 `view.footer_enabled=False`（CardKit seed 例外） | 每个调用点逐一给结论；非 native 真终稿靠 `notify`/`expect_edits` 保住 ✅；判定打限流诊断日志 |
| `core/cards.py` | 注释口径：`x-small` 从「不在文档、别赌」改为「markdown 真机已验 + 其它宿主见宿主矩阵；未过退回 notation」 | 不改 `text_profile` 档位表 |
| `tests/test_units.py` | 三条新用例（B 给名）：`test_v073_detail_and_error_text_sizes_are_x_small` / `test_v073_footer_turn_scope_and_status_is_explicit` / `test_v073_non_turn_send_has_no_footer_element`；**并**修 5238/5251/5254/5258/5263/5265 等旧无参调用（改到 red-assert、不 crash） | **只新增用例名**；旧用例只改函数体，不许改名/删名 |
| `tests/mutate_check.py` | 新增 V073-1a/1b/1c/2a/2b/2c/2d（§6.2）；同步改 V4-57 两侧锚点与 V4-7/V4-17B/G2-3；顺手修 45s→90s 注释 | 每条 `-k` 完整模式实红；post-change 锚点唯一性逐一 grep |
| `tests/check_cardview.py` | 加 Error 块夹具 + `detail/error text_size=="x-small"` 断言 + `PANEL_TEXT_SIZE=="notation"` | 字段白名单分档不变（`div.icon` 可带 size、`markdown.icon` 不可） |
| `tests/write_golden_trace.py` | 首选给场景加一个 Error/Result 步骤（否则写明不扩理由） | 只改夹具场景，不改判定 |
| `tests/golden_cardkit_trace.json` | 重生成：既有 9 叶 `notation→x-small`（B-7 清单）+（若加 Error）新增叶；item2 预期 **0 叶**变化 | `--check` 必先一致；diff 逐条解释；有 0 之外的 item2 diff 即判 `turn_card` 误分类 |
| `tests/probe_text_size_hosts.py` | **新增**宿主矩阵探针：`markdown` / `div.text=lark_md` / `div.text=plain_text` 各一张，内含 notation vs x-small 对照，逐张打印 `code` | 任一宿主非 0 或肉眼不更小 ⇒ **该宿主退回 `notation`**（改回后同步断言/变异/夹具再重验） |
| `docs/` + `plugin.yaml` + `CHANGELOG.md` | 完成记录 + `docs/releases/v0.7.3.md` + `version: 0.7.3` + `[0.7.2]` 收口/开 v0.7.3 + README/plugin.yaml 写清「非回合无页脚 / 真回合不变 / 命令回复无页脚」 | 只追加，不改历史行（历史证据保留） |

## 2. 验证计划（出口判据，机械可核）

1. `py_compile`（venv 解释器）+ `run_fast --full`（8 步全 OK）+ `mutate_check --preflight` **全数绿**
   （= `MUTATIONS` + 12 `CONTROLS`，不再出现 510/511）；明确 **preflight 不是绿灯**（`AGENTS.md:491-496`）；
2. 7 条新变异 `V073-1a/1b/1c/2a/2b/2c/2d` **`-k` 完整模式实红且红在断言上**（贴输出；M4 必须走
   `turn_card=True` 侧，防被非回合早退遮蔽）；
3. 黄金夹具重生成 + diff 逐条解释：既有 **9 叶**细节行 `notation→x-small`（B-7 清单）+ 若扩场景的
   Error 叶；**item 2 预期 0 叶变化**（有 diff 即判 `turn_card` 误分类）；`helper_fp` 变 ⇒ 全量必然；
3b. **`x-small` 宿主矩阵探针**（A-3/B-8）：`tests/probe_text_size_hosts.py` 发 3 张独立卡
   （`markdown` / `div.text=lark_md` / `div.text=plain_text`），逐张打印服务端 `code`；任一 `code!=0`
   或用户目视不更小 ⇒ **该宿主退回 `notation`**（回退后同步改断言/变异/夹具并重验）；
4. 既有门禁同步修好：`check_hooks.py:772` + `test_units.py` 的六个无参 `_ld_footer` 调用点
   （5238/5251/5254/5258/5263/5265）必须显式传 `turn_card`/status，全绿且失败时仍是 red-assert；
5. 全量变异重验：6 分片完整模式、独立账本、坏 0；合并后 `full_audit_at == 当时的 HEAD`、`n_inh == 0`；
   实测墙钟写回（v0.7.2 那轮参考：499 条 ≈25 分钟，本轮 N 更大；机器空闲时更快）；
6. 真机探针卡：①「改前 / 改后」两行细节行对照 + 一个 Error 块（看可读性）；② 一张系统提示卡样例
   （无 ✅、无空页脚行）；③ 一张真实回合收尾卡（`✅ 已完成 · …` 一字不动）；④ 宿主/主题各一张
   （默认 `ap_lite` 之外至少一种）；用户目视确认；
7. 发布：用户终验 → **新建 `~/.larkdeck-scratch/release-v0.7.3.py`**（§6.4：TAG/notes 指向 v0.7.3、
   8 步门禁名字集合不变）→ `--check` 全绿 → `--go`（push/tag/`.deploy`/重启）。

## 3. 登记（本批不做，只留档）

* **`text_weight` 不存在**（`plain_text` 无字重字段）——「工具名更粗」永久关闭，除非升级卡版本。
* 十六进制颜色在真机被忽略 ⇒ 卡 2.0 的颜色就是枚举；「`grey` 是最浅档」**不再作为结论**（无官方出处），文档只留实测口径。
* **注释口径修正**：4 处历史口径（`core/context.py` / `core/i18n.py` / `core/adapter.py` /
  `tests/mutate_check.py`）+ `core/cards.py:220-222` 的 `x-small` 旧声明 —— 顺手改会动指纹 ⇒
  与本项**同一次**全量重验里一起做（逐字对照见 §6.7.1）。
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

**变更清单（已审计；实现口径以 §6.1 定版为准）**：

| 文件 | 改动 | 约束 |
| --- | --- | --- |
| `core/adapter.py` | **Design B（§6.1 定版）**：`_ld_footer` 退**纯格式器**（删 `panel_snap` 兜底、默认 `turn_card=False` ⇒ None）；`_ld_render_card(..., turn_card=)` 必填，非回合关 `footer_enabled`/`footer`（否则 `entity_skeleton` 会留空 footer 元素）；`_ld_frame_footer` 自己解析本回合状态；`send()` 按 `notify`/`expect_edits`/`_interim_send`/`guarded` 显式判定 | `edit_message` 恒 `turn_card=True`；非 native 真终稿靠 `notify`/`expect_edits` 保留 ✅；每个调用点逐一给「该有页脚 / 不该有」结论；判定打限流诊断日志（P4 grep 复核） |
| `tests/test_units.py` | 新增 B 的 3 条用例（§6.2 注：细节/错误块硬字面量、turn 侧恰好无 ✅、非回合 send 无 footer 元素）；**并**修 5238/5251/5254/5258/5263/5265 等旧无参调用 | 旧用例只改函数体；用例名新增（旧名不动），失败保持 red-assert |
| `tests/mutate_check.py` | 新增 7 条 V073 变异（§6.2）；同步重对 V4-57/V4-7/V4-17B/G2-3 锚点 | 每条 `-k` 完整模式实红；post-change 锚点唯一性 grep = 1 |
| `tests/golden_cardkit_trace.json` | 重生成：既有 9 叶 `notation→x-small` +（若扩场景的 Error 叶）；**item 2 预期 0 叶变化** | diff 逐条解释；item2 出 diff 即判 `turn_card` 误分类 |
| 真机探针 | 系统提示卡（无 ✅、无空 footer 行）+ 真实回合收尾卡（✅ 一字不动）+ 宿主矩阵/Error 可读性 | 用户目视；结论写 `docs/verify-log.md` |

**与本批第 1 项（细节行 `x-small`）合并做一次全量重验**（同一棵树上改完再跑 6 分片），发布为 **v0.7.3**。

### 4.1 用户补充确认（2026-09-22，逐字）

* 「**error 标签也要一起变小**」⇒ `_tool_output_div` **整元素**（`**Error**` 标签 + 代码文本）用
  `x-small`，**不拆元素**（放弃「标签保持原大小」那条备选）。
* 「**真实回合卡（正常回答）底部的 ✅ 已完成 是要保留的**，只是把 Hermes 的系统通知弄得干净
  清爽一点」⇒ 第 2 项只掐**非回合消息**（系统提示/网关通知等），**回合内的状态词一个都不动**；
  单测必须**同时**钉两侧（真实回合仍有 `✅ 已完成` + 系统提示无页脚）。
* 「**最近说的这些问题，一起改吧**」⇒ 本批把第 1 项 + 第 2 项 + 计划 §3 那 4 处代码注释历史口径
  **合并成一次全量重验**，发布 **v0.7.3**（不出 v0.7.2.1）。

---

## 5. 流程（用户 2026-09-22 重申「老规矩」；每条都有机械可核的出口）

| 阶段 | 内容 | 出口判据（硬） | 对抗审计 |
| --- | --- | --- | --- |
| **P0 规划** | 本文件冻结（范围 = §1 + §4 + 旧登记项处置 §7；含用户逐字确认 §4.1） | 文件入库 + 用户无异议 + **三路全量报告逐条处置（§6）后，再跑 3 路只读快速复核 Design B（聚焦 §6.1/§6.2/§6.3），无高阻断才进 P1** | **≥3 路**（技术可行性 / 用户可见效果与证据 / 流程与诚实性）—— 2026-09-22 起跑（`7fe3d34b` / `98c88adb` / `92f41a3f`），全量报告见 §6.8/§6.9/§6.7；复核 A2/B2/C2 另报 |
| **P1 实现** | `core/cardview.py`（细节行 line+emoji、Error 块 = `x-small`）、`core/adapter.py`（Design B：显式 turn 判定 + `_ld_footer` 纯格式器 + 非回合 `footer_enabled=False`）、6 处注释口径（§6.7.1） | `py_compile`（venv）全绿；`_ld_footer`/`_ld_render_card`/`_ld_frame_footer` 的**每个调用点**逐一核对并留「该有/不该有页脚」表；`--preflight` 全数绿（不是 510/511） | **≥3 路**（判别力 / 协议不破坏 / 文档诚实） |
| **P2 断言与变异** | B 的 3 条硬字面量用例 + 7 条 V073 变异（`-k` 全模式实红）+ 既有锚点 V4-57/V4-7/V4-17B/G2-3 同步改 + `check_cardview` Error 夹具/常量断言 + 黄金夹具重生成（9 叶解释 + item2 预期 0 叶）+ **宿主矩阵探针**（未过的宿主退回 notation） | 每条新变异**实红且红在断言上**；夹具 diff 逐条解释；宿主矩阵逐张 `code` 打印、用户目视 | **≥3 路**（变异判别力 / 夹具覆盖 / 无自证循环） |
| **P3 全量重验** | 6 分片完整模式（独立账本、`-u` 不缓冲、起跑前清陈旧证据 + 校验工作树干净） | 每片坏 0、合并 `✅ N 条通过`（**N = 旧 499 + 新增 − 合并/重对后的净数，以实际 `len(MUTATIONS)` 为准**）、`full_audit_at == 被测提交`、`n_inh == 0`、`--ledger-status` N/N 待跑 0、**实测墙钟写回本文件** | **≥3 路**（证据链 / 时间与隔离 / 反假绿） |
| **P4 部署与真机探针** | `.deploy` 指被测提交 + 网关重启（有界等「启动自检通过」）+ 探针卡 4 项：细节行改前/改后、Error 块可读性、系统提示卡（无 ✅/无空 footer 行）、真实回合收尾卡（✅ 一字不动）；另 grep `turn=…` 日志复核分类 | 自检通过；用户目视回话；`turn` 日志证据、探针卡 id 写进 `docs/verify-log.md` | **≥3 路**（探针可判读 / 两侧覆盖 / 无误导） |
| **P5 发布** | 用户终验后 push + tag **v0.7.3** + `gh release` + `.deploy` 指 tag + 网关重启 | 发布脚本 `--check` 全绿后 `--go`；tag/`.deploy`/自检三处留痕 | **≥3 路**（发布完整性 / 坐标一致 / 文档与证据一致） |
| **P6 洁癖收尾（neat-freak）** | 文档 / 规则（AGENTS.md）/ 记忆 / 残留与代码现实对齐：`docs/verify-log.md`、`CHANGELOG.md`、`docs/releases/v0.7.3.md`、`docs/plan-*.md`、注册项（`text_weight` 不可用、十六进制颜色无效、注释口径 6 处、清场待办）、审计 worktree 与临时目录 | 全仓 grep 不再有「已过时/自相矛盾」的现行口径；残留清单给用户过目 | **≥3 路**（知识一致性 / 残留与清场 / 发布完整性复核） |

**纪律**：每阶段审计回来 → 逐条给处置（含**未采纳的理由**）→ 讨论收敛后才进下一阶段；
**禁止 sleep 轮询**（只用有界等待器）；全量重验期间**不碰任何文件**。

---

## 6. P0 审计收敛（三路对抗审计 → 逐条处置）

审计 agent：A 技术可行性 `7fe3d34b` · B 用户可见效果与证据 `98c88adb` · C 流程与诚实性 `92f41a3f`。

### 6.1 第 2 项定版口径（C-3 + A 路 2 条高阻断全量报告后冻结）：显式 turn 决策 + `_ld_footer` 退成纯格式器

**两版错判据都被实测否掉**：

* C 前版（`started` 空且无 `status`）被 A 路实测：`send()` 里 `_ld_render_card(status="completed")` 会经
  `_ld_cardview` **自算**一份带状态的页脚；`_ld_footer(chat_id)` 返回 `None` 只是让
  `_ld_render_card` **保留 view 自己那份**（`if footer:` 在 `core/adapter.py:2516-2518`）⇒
  系统提示卡照样出 `✅ 已完成`；
* 若把快照兜底**无条件**删掉，A 路实测真实回合 `edit_message(finalize=True)` 会丢 `✅`：调用方
  `_ld_frame_footer(state)`（有 `started`、无 `status`）是**非空**值，会覆盖 view 里带状态的那份。

**定版口径（A 路全量报告后收敛）**：`turn_card` 是 **send 层的显式决策**；`_ld_footer` 退回**纯格式器**
（不再自己读 panel 快照），回合状态由调用方**显式解析**后传入 —— 这样「非回合抄到陈旧状态」与
「真回合丢状态」在结构上都不可能出现：

1. `_ld_footer(chat_id="", started=None, status=None, *, turn_card: bool = False)`：
   * `turn_card=False`（**默认 fail-closed**）⇒ 入口立刻 `return None`（连模型/ctx 段都不算）；
   * `turn_card=True` ⇒ 只认**显式** `status`（**删掉 `panel_snap` 兜底**，`adapter.py:2233-2235` 的读取整段移除）；
     状态缺失就只出耗时/模型/ctx —— 真回合的状态由 2/3/4 显式传入；
   * 直接调用 `_ld_footer()`（不传 `turn_card`）⇒ `None`：A 路最小硬字面量之一，同时逼所有调用点显式表态。
   * **B 路配套断言（防变异被早退遮蔽）**：完成快照 + model/ctx 就绪时，
     `_ld_footer(chat_id, turn_card=True)`（**不传 status**）必须恰好等于
     `"Test Model · ctx 1k/10k · 10%"`，**不含** `✅` —— 否则「把 `panel_snap` 兜底加回来」的变异会因
     非回合早退而假绿。
2. `_ld_render_card(..., *, turn_card: bool)`（**必填**）：
   * `turn_card=False` ⇒ 建好 view 后强制 `view.footer_enabled = False`、`view.footer = None`，
     **不**调 `_ld_view_status` 的快照覆盖，也不渲染面板/状态头（系统提示/命令回复 = 静默消息卡）。
     ⚠️ B 路实测：只让 `_ld_footer` 返回 `None` **不够** —— `entity_skeleton` 会写
     `view.footer or " "`（`cardview.py:680-683`）留一个**空页脚元素** ⇒ 必须关 `footer_enabled`
     （`_ld_fit_structured_card` 尊重它 2469-2475），并且单测断言**连 footer 元素 id 都不存在**；
     CardKit **seed 建卡**不受影响（结构建卡定死，回头还要写元素，必须保留 footer 槽位）。
   * `turn_card=True` ⇒ 现状（`_ld_view_status` 快照覆盖 + caller footer 覆盖都保留）。
   * `edit_message()`（含 `finalize=False`）恒 `turn_card=True`；`send()` 按第 3 条显式给值。
   * `_ld_frame_footer(state, *, turn_card=True)`：**自己解析**状态（`state["status"]` 优先，否则本回合
     panel 快照）后显式传 `_ld_footer(..., status=..., turn_card=True)` —— 5 个 legacy/降级帧
     （3871 / 4281 / 4382 / 4437 / 4480）与 `edit_message(finalize=True)` 都走这里，收尾 ✅ 不丢。
3. `send()` 的回合判定 `_ld_send_is_turn(metadata, guarded)`（**默认非回合**）：
   * `guarded=True`（own 种子失败窗口，本回合首帧）⇒ 回合；
   * `metadata["notify"] is True`（上游 `_metadata_for_send(final=True)` 的终稿标记）⇒ 回合；
   * `metadata["expect_edits"] is True`（可编辑预览）⇒ 回合；
   * `metadata["_interim_send"] is True`（上游 `_interim_metadata` / fallback commentary：**中途**播报、
     不是终稿）⇒ **非回合**（不挂 ✅）；
   * 其余（网关系统提示 / 命令回复 / 背景任务 / 媒体播报）⇒ **非回合**。
   * **不采用按通知前缀白名单**：前缀随上游文案变、且覆盖不到 DB/kanban/goal 通知；改为「默认非回合 +
     仅显式回合标记放行」。`reply_to`/面板数据只作**日志诊断**（非回合判定时若 panel 尚有数据，
     限流打一条 `非回合判定: send(...)` 便于事后审计误伤），**不作判据**。
4. `edit_message()` 两个分支（`finalize` 与非 `finalize`）**恒为回合** ⇒ `turn_card=True`；
   `_ld_frame_footer(state, turn_card=True)` —— 真实回合的 `✅ 已完成` 一个字不动。
5. **逐一核对调用点**（P1 出口，每处写「该有页脚 / 不该有」+ 显式参数）：`1727`（自检：显式解析本回合
   状态 + `turn_card=True`，不许变「页脚=无」）/ `2016`（显式 stopped，True）/ `2668`（按第 3 条）/
   `2739` + `3871 / 4281 / 4382 / 4437 / 4480`（走 `_ld_frame_footer`）/ `3271` / `3785`（view 基础页脚，
   view 内部传 True）/ `4236`（seed，True）/ `4825`（/stop，显式 stopped）/ 4885-4888 澄清卡（不经 footer）/
   `5877-5879` `/larkdeck` 回复（经 `send()` ⇒ 非回合、静默，README/plugin.yaml 明写）。
6. **既有测试同步改**（A/B 两路：只加新测试不够）：`tests/check_hooks.py:772`（started-only 却断言 ✅ ⇒
   改为显式 `status`/`turn_card=True`）；`tests/test_units.py` 的 `5238 / 5251 / 5254 / 5258 / 5263 / 5265`
   （B 路点名的六处，默认 fail-closed 后不传参会让 `5251/5258` 的 `None.startswith` 变 **red-crash**）、
   `1533/1537、2241/2245、2321/2325、5147-5209、5229、6860/6901、10914/10918` 逐处修成显式 `turn_card`/
   显式 status，**保持断言失败（red-assert）而不是崩溃**。**只改函数体，不改/删用例名**（账本按名字子集判失效）。
7. `send()` 分类的证据要在真机复核（A 路要求）：每条判定打一条**限流** `logger.info`
   （`turn=… guarded=… keys=…`）；P4 真机探针时 grep 日志，确认「真实终稿带 notify/expect_edits、
   系统提示无标记」。若发现无标记的真终稿 ⇒ 停下来补判据，再决定是否重跑 P3。
8. **非 native 真终稿与命令回复的口径**（B 路 4）：`stream_consumer_transport._first_send` 带
   `notify`/`expect_edits`（上游实证），所以非 native 真终稿仍有 ✅/model/ctx；`/larkdeck status|config|help`
   回复本就是命令卡、不是回合 ⇒ 无页脚是**有意的用户可见变化**，必须在 README + `plugin.yaml` 描述里
   写清（真实回合不变；命令回复/系统提示不再带页脚）。

### 6.2 断言与变异清单（C-2，高；每条都要进 6 片全量）

| # | 语义 | 硬字面量断言（文件/用例） | 新变异（old → new） |
| --- | --- | --- | --- |
| 1 | 细节行（line）`x-small` | `test_v073_detail_and_error_text_sizes_are_x_small`：`tool_step_elements(step,"line")[0]["text_size"]=="notation"`（标题不动）、`[1]["text_size"]=="x-small"` | **V073-1a**：detail-line 块 `"text_size": "x-small"` → `"notation"`（与 V4-57 同锚点，V4-57 两侧同步改） |
| 2 | 细节行（emoji）`x-small` | 同用例：`tool_step_elements(step,"emoji")[1]["text"]["text_size"]=="x-small"` | **V073-1b**：plain_text detail 块的 `"text_size": "x-small"` → `"notation"` |
| 3 | Error/Result 块 `x-small` | 同用例：`els[2]["text"]["text_size"]=="x-small"`（`ToolStepView(error_block="boom")`） | **V073-1c**：lark_md 错误块的 `"text_size": "x-small"` → `"notation"` |
| 4 | `_ld_footer` 是纯格式器（回合侧也不许偷快照） | `test_v073_footer_turn_scope_and_status_is_explicit`：完成快照 + model/ctx、**不传 status** ⇒ `_ld_footer(chat_id="oc_v073", turn_card=True) == "Test Model · ctx 1k/10k · 10%"`（**无 ✅**）；`turn_card=False is None`；`(_ld_footer(..., started=…, status="completed", turn_card=True) or "").startswith("✅ 已完成 · ")` | **V073-2a**：guard 后加回 `status or (panel_snap.get("status") …)`（必须用 `turn_card=True` 的断言钉住，否则被早退遮蔽 ⇒ 假绿，B-1） |
| 5 | 非回合抑制不许丢 | `test_v073_non_turn_send_has_no_footer_element`：完成后 `send("Gateway online …")` ⇒ `body.elements` 中 **无 `element_id=="footer"`**、整卡 JSON 无 `✅ 已完成` | **V073-2b**：`if not turn_card: return None` → `if False: return None`（测试用带 model/ctx 的 `turn_card=False`，否则空页脚本就是 None、变异假绿） |
| 6 | 真实回合必须保留 `✅ 已完成` | 收尾帧/`edit_message(finalize=True)`/`send_stream_frame(finalize=True)` 页脚 `startswith("✅ 已完成 · ")`（assertion 带 `or ""` 防 None 崩溃） | **V073-2c**：guard 取反 `if turn_card: return None` |
| 7 | 非回合静态卡不能留空 footer 元素 | 同用例 5（B-2 实测：只让文本为 None 仍会写 `{"element_id":"footer","content":" "}`） | **V073-2d**：删掉 `_ld_render_card` 非回合分支的 `view.footer_enabled = False` |

> 三条新单测名（B 路 Q1，全部**只新增**）：`test_v073_detail_and_error_text_sizes_are_x_small` /
> `test_v073_footer_turn_scope_and_status_is_explicit` / `test_v073_non_turn_send_has_no_footer_element`；
> 可选 `check_cardview` 加固：`assert detail["text_size"] == "x-small"` + Error 夹具 +
> 生产常量 `PANEL_TEXT_SIZE == "notation"`（A-4：现有白名单只放行任意 `text_size`，抓不住悄悄退回）。
> 新变异 7 条（V073-1a/1b/1c/2a/2b/2c/2d）全部 `test_units` 门禁；old 锚点在 P1 改完后**重新 grep**
> 保证唯一（B 路 Q2 表：post-unique 必须 = 1）。

### 6.3 冻结与盖章纪律（C-4/C-5/C-6/C-7，高/中）

* **全量不可避免**：golden 夹具含细节行的 `text_size:"notation"`（`_HELPER_FILES`）⇒ 重生成后
  `helper_fp` 变 ⇒ `_delta_split` 令**全部 499+ 条 todo** ⇒ **禁 `--delta`、禁 `--seed-inherited`、
  禁 `--target-only`**（三条都是省时通道；本轮只用 6 分片 full 模式，独立账本）；
* 起跑前：**工作树干净 + 固定 HEAD**、清陈旧 `seed*/shard*`、每片独立 `LARKDECK_LEDGER_PATH`；
  **跑中禁 commit**（v0.7.2 曾因起跑后提交作废一轮）；分片跑**永不盖章**，只在 merge 时盖；
* merge：`--allow-at adea7cb`、现场三指纹、日志 🔴 并集、**12 名对照全绿**、`n_assert == len(MUTATIONS)`
  （加了变异后 **N > 499**）、`tree_dirty=false`、记 `full_audit_tree`；
* 改 `tests/check_cardview.py` ⇒ 14 条 `gate=check_cardview` 的整文件 `gate_fp` 失效；
  `test_units` 的 463 条用**用例名子集** ⇒ **只许新增用例名，不许改名/删名**；
* 预计失效窗：`R7-1/R7-2/T3`（`adapter.py:2229/2230/2250`）、`CLS-25`（`i18n.py:20-51`）、
  `G2-11`（`4156-4187`）、`R9-9`（`4215-4245`）、`V4-72`（`mutate_check.py:2764`，只要不动
  `_is_full_run` 正文就存活）⇒ 改完**先 `--preflight`**（且明确：**preflight 不是绿灯**，
  `AGENTS.md:491-496`）；`py_compile` 必须用 venv 解释器
  `/Users/Zayn/.hermes/hermes-agent/venv/bin/python3`。
* **锚点连带失效（B-3 / A-4，必须同步改 old/new 两侧）**：
  * `V4-57`（`mutate_check.py:2664-2677`）的 old/new 都含 detail 返回里的 `"text_size": PANEL_TEXT_SIZE`
    ⇒ 两侧改成 `"x-small"`，否则 `--preflight` 直接找不到锚点；
  * `_ld_footer` / `_ld_render_card` 加 `turn_card=` 会碰 `V4-7`（`2427-2429`）、`V4-17B`（`2552-2554`，
    源 `adapter.py:3785`）、`G2-3`（`1603-1605`，源 `adapter.py:2203-2205`）⇒ 三处一并重对锚点；
  * 新变异 `V073-1a` 与 `V4-57` 同源块 ⇒ 两个 old 文本必须都**唯一**、且 old/new 上下文能区分两个语义
    （一个改尺寸、一个删图标），别把彼此吃掉；
  * P1 改完 `--preflight` 必须**全数绿**（= `MUTATIONS` + 12 `CONTROLS`；不再出现 v0.7.2 的 510/511），
    但仍牢记 **preflight 不是绿灯**（`AGENTS.md:491-496`）。

### 6.4 发布机制（C-12/C-13，中）

* 新建 `~/.larkdeck-scratch/release-v0.7.3.py`（B-9 逐行清单）：脚本名/doc（2、10-12）、
  **计数**（15 的 `499/499`、`511/511` 换成 P2 后的实测 N/N）、`TAG`（36）、`TAG_MSG`（37-40）、
  审计证据路径（139/148 → `docs/audits/v0.7.3/`，需在 P5 前建好）、scratch 目录（165 → `v0.7.3-*`）
  与 `seed*/shard*`（167-168）、`--allow-at`（169 注释 + 173 元组，含当前 `_meta.full_audit_at=adea7cb`）、
  notes 路径（210 → `docs/releases/v0.7.3.md`）；顺手修低项：脚本 18 行写「等 120s」但代码等 150s；
  8 步门禁**名字集合不变**；
* `deploy_probe.py` 的部署标记与探针名换成 v0.7.3（别再用 `SPINNER_TOOL_IMG_KEY` 当标记）；
* **顺序**：一次 commit `C`（代码+测试+夹具+注释+发布说明）→ preflight → 6 片 full on `C`
  （跑中禁 commit）→ merge ⇒ `full_audit_at=C`、`n_inh=0` → 账本/文档 commit `D`
  （不得碰 `core/`、`tests/`、`plugin.yaml`、`docs/audits/v0.7.2`）→ `.deploy` 指 `C`/最终提交
  （**必须先于打 tag**）→ 用户终验 → `--check` → `--go`（push/tag/release/`.deploy` 指 tag/重启）
  → 最后 post-release 文档；**tag 必须落在已部署且 `.deploy==HEAD` 的提交上**。

### 6.5 诚实性修正（C-8/C-9/C-10/C-11，高/中）

* **4 处注释现为假**（逐条登记，改时抄原文）：`core/context.py:374`（页脚上 🤖）、
  `core/i18n.py:28`（`Succeeded`/`Running`/`Failed` 旧口径）、
  `core/adapter.py:4180-4181 + 4233`（「页脚带短码 / 第二帧起」）、
  `tests/mutate_check.py:3013 + 3019`（写 45s，实际 `_GATE_TIMEOUT_S=90.0`）；
* `core/cards.py:221-222` 声称 `x-small` 不在官方文档 ⇒ 改成「2026-09-22 真机探针：`markdown` 宿主
  服务端 `code=0` 且确实更小；其它宿主（`div.text=lark_md` / `plain_text`）**待 P2 宿主矩阵探针**」
  + 卡 3/卡 4 证据；**探测未过的宿主一律退回 `notation`**（A-3/B-8）；
* `CHANGELOG.md:17` 仍是 `[Unreleased] - v0.7.2`（已 tag）⇒ 改 `[0.7.2]` + 开 v0.7.3；
  `plugin.yaml` 的 `version` 要 bump；README/plugin.yaml 补「非回合不再出页脚（含系统提示与
  `/larkdeck status|config|help` 命令回复）、真实回合（native 收尾/心跳、`/stop`、非 native 终稿）不变」；
* 另记（B-8 用户可见）：`text_profile=large` **不会**放大字面量 `x-small`（`apply_text_profile` 只补
  缺省值，`cards.py:285-288`）⇒ README 的 `text_profile` 说明里加一句；Error/堆栈可读性列进 P4 探针。
* `tests/test_units.py` 里短码旧口径 docstring（`6536 / 6781 / 6841 / 1593`）顺手改。

### 6.6 登记卫生（C-15/C-16，中）

* 证据强度标签：`text_weight`（`200621` + path + 探针脚本 `tests/probe_style_candidates.py`，**强**）；
  十六进制颜色（卡 id + 用户目视「三行一样」，**中**：无 payload/截图哈希）；
  「`grey` 最浅枚举」（**弱**：无官方出处）⇒ **不作为结论**，文档只写「实测十六进制无效」；
* **清场清单**（待用户点头）：① 审计 subagent 的 worktree（`git worktree list` 里非主工作树的条目）；
  ② `/tmp/larkdeck`（~13M）；③ `~/.larkdeck-scratch/v0.7.2-*` 旧临时目录。
  **验收**：清完后 `git worktree list` 只剩主工作树、上述路径 `test ! -e`、`pgrep -f` 无残留进程
  （清场前后各贴一次输出）；
* **旧登记项处置**：`docs/plan-v0.7.2.md` 里标「登记 v0.7.3」的一批 —— 逐项处置见 **§7**
  （关闭 / 完成于 v0.7.2 / defer v0.7.4 + 理由），本批范围经用户 2026-09-22 重新拍板为
  §1 + §4 + 注释口径，未列入的本批不做。

---

### 6.7 C 路 16 条逐条处置（C 报告基线 `e7f7e06`，收到 2026-09-22 22:5x）

> C 路先到；A/B 全量报告也已到，分别见 **§6.8 / §6.9**（均按「采纳 / 部分 / 不采纳 + 落点」逐条处置）。

| # | C 原文摘要 | 处置 | 落点 / 理由 |
| --- | --- | --- | --- |
| 1 | 无阶段审计表/日志 | **采纳** | §5 表格 P0–P6 + 每阶段 ≥3 路 + 硬出口；处置追加式：P0=本 §6，P1–P6 各追加「阶段审计收敛」小节，不覆盖历史 |
| 2 | 断言/变异不是出口判据、缺两侧语义 | **采纳** | §6.2 七行（细节行 line/emoji、错误块、纯格式器、非回合抑制、回合保留、空 footer 元素），每条进 6 片 full |
| 3 | `started/status` 判据不健全 | **采纳（高）** | §6.1 改显式 `turn_card`；§4 变更清单同步改（已改） |
| 4 | 冻结/矩阵纪律缺 | **采纳** | §6.3：干净树+固定 HEAD、清陈旧 seed/日志、每片独立账本、跑中禁 commit、分片不盖章、merge 三指纹/12 对照/`n_assert==len(MUTATIONS)`/`tree_dirty=false`；**禁 `--delta`/`--seed-inherited`/`--target-only`**；`py_compile` 用 venv 解释器 |
| 5 | 夹具含 `notation` ⇒ 全量必跑 | **采纳** | §6.3 第一条；N>499，delta/继承不可能 |
| 6 | `check_cardview` 整文件指纹 / `test_units` 名字子集 | **采纳** | §6.3：改 `check_cardview` ⇒ 14 条 `gate_fp` 失效；`test_units` **只新增**用例名，不改/删 |
| 7 | 具体失效窗 R7-1/R7-2/T3/CLS-25/G2-11/R9-9/V4-72 | **采纳** | §6.3；改完先 `--preflight`（且明示**不是绿灯**） |
| 8 | 4 处注释现为假 | **采纳** | §6.5 + §6.7.1 逐字原文/新口径 |
| 9 | `cards.py:221-222` 与 `x-small` 冲突 | **采纳** | §6.5：加「明确档位 + 真机探针」限定；卡 3/卡 4 证据入库 |
| 10 | CHANGELOG:17 / plugin version / README / 标题 | **采纳** | 标题已改；CHANGELOG `[0.7.2]` 收口 + 开 v0.7.3、`plugin.yaml` version bump、README/plugin.yaml 注明「非回合无页脚、真实回合不变」（约定在 commit C/D 落，见 §6.4） |
| 11 | `test_units` 短码旧 docstring | **采纳** | §6.5：`1593 / 6536 / 6781 / 6841` 只改文字口径；不动断言、不动用例名 |
| 12 | 新建 `release-v0.7.3.py` | **采纳** | §6.4：`TAG=v0.7.3`、notes=`docs/releases/v0.7.3.md`、新 scratch/seed/shard、8 步门禁名字集合不变 |
| 13 | 发布顺序 | **采纳** | §6.4：C（代码+测试+夹具+注释+发布说明+`plugin.yaml` version）→ preflight → 6 片 full on C → merge（`full_audit_at=C`、`n_inh=0`）→ D（账本/文档；不碰 `core/tests/plugin.yaml/docs/audits/v0.7.2`）→ `.deploy` → 终验 → `--check`/`--go`；**tag 落在已部署提交** |
| 14 | 注释只给文件名、无原文 | **采纳** | §6.7.1 给 `file:line` + 原文摘抄 + 新口径 |
| 15 | 证据强度 | **采纳** | §6.6：text_weight=强；hex=中；「grey 最浅枚举」=弱 ⇒ **不再作为结论** |
| 16 | 清场无清单/旧登记无处置 | **采纳** | §6.6 清场清单+验收；旧「登记 v0.7.3」逐项见 **§7** |

#### 6.7.1 历史口径注释逐字对照（P1 改时照抄）

| 位置 | 现文（原文摘抄） | 新口径（意图） |
| --- | --- | --- |
| `core/context.py:374-375` | 「已知厂商/系列 token → 固定大小写（键是小写 token）。页脚上 `🤖` 后面那串从『原始模型 ID』改成『模型名』时…」 | 模型显示名格式化（`show_model` 用），**页脚 v0.7.2 起已无 `🤖` 段前缀**；本表不负责任何页脚前缀 |
| `core/i18n.py:28-29` | 「2026-09-17 CLS 观感：…状态 → 带颜色的词（Succeeded / Running / Failed）由 cards.tool_step 给」 | 现状：完成 `✓`(绿) / 运行中 `Running`(蓝) / 失败·拦截·超时仍是词；由 `cards.status_style` 与 `cardview.status_style` **两表对等**给，不是三词口径 |
| `core/adapter.py:4180-4182` | 「⚠️ 这里**故意**用 `_ld_footer()`…短码是『本卡的 id 后 6 位』…**从下一帧起**…页脚就带上短码了」 | v0.7.2 起页脚**没有短码**；保留 `_ld_footer()` 的理由只剩「建卡帧还没有 `message_id`/`card_id`，不能走依赖 id 的 `_ld_frame_footer(state)`」 |
| `core/adapter.py:4233-4234` | 「⚠️ 同理…建卡那一帧还没有 id ⇒ 只能是基数页脚；短码从第二帧起才有」 | 同上：删「短码」叙事，改「建卡帧没有 id ⇒ 用不依赖卡 id 的 `_ld_footer()`」 |
| `tests/mutate_check.py:3013` | 「跑单支门禁（**有界**：`_GATE_TIMEOUT_S`（45s）硬超时…）」 | `（90s）` |
| `tests/mutate_check.py:3019-3020` | 「120s 时实测有变异把整轮拖到小时级（用户明确要求提速）⇒ 45s」 | 改「45s 在 v0.7.2 六片并发下误伤 10 条（💥）⇒ 90s；随后全量实测坏 0」 |
| `core/cards.py:220-222` | 「只用官方文档列出的值…曾经的 `x-small` 不在文档枚举里…不能拿真机卡片赌」 | 「`markdown` 宿主 2026-09-22 真机 `code=0` 且更小；`div.text=lark_md` / `plain_text` 宿主见 P2 宿主矩阵探针，未过则退回 `notation`」 |
---

### 6.8 A 路逐条处置（技术可行性全量报告，2026-09-22；基线 `e7f7e06`，未改仓库）

| # | A 原文摘要 | 处置 | 落点 / 理由 |
| --- | --- | --- | --- |
| 1 | Item 2 原文不达标 + 误伤真回合（实测双向） | **采纳（阻断，先冻结）** | §6.1 定版 Design B：`send` 层显式 turn 决策 + `_ld_footer` 退纯格式器（不读快照）+ 非回合 `view.footer_enabled=False`；§6.2 行 4-7 与 7 条新变异钉死 |
| 2 | 逐调用点：1727 恒 None、2739 丢状态、3871/4281/4382/4437/4480、`/larkdeck` 5877、check_hooks:772 + 4 组单测 | **采纳** | §6.1 条 5/6：`_ld_frame_footer` 自己解析状态；1727 显式解析状态 + True；六处单测（含 B 补的 5251/5258/5263）+ check_hooks:772 同步修；澄清卡/2016/4825 已核安全 |
| 3 | `x-small` 宿主矩阵未验（markdown 已验；`div.text=lark_md`、`plain_text` 未验） | **采纳（高）** | §2 新增 **3b 宿主矩阵探针**（3 张独立卡 + 逐张 `code`；不通过该宿主退回 `notation`）；P4 真机再复核（含第二客户端/主题维度）；上线后 grep `200621`/`卡片发送未成功` |
| 4 | check_cardview 抓不住 text_size；V4-57 锚点会断；golden 无 Error 块只覆盖 detail 9 叶 | **采纳** | §6.2 注 + §6.3 锚点连带失效：check_cardview 加 Error 夹具 + `detail/error text_size` 断言 + `PANEL_TEXT_SIZE=="notation"`；V4-57 两侧改 `x-small`；golden **首选**给场景加一个 error 步骤（若改动过大则不改夹具，但必须写明并由单测 + check_cardview 双覆盖） |
| 5 | cards.py:220-222 与实现冲突；22px 缩进/图标 size/Error 可读性低风险 | **采纳（部分）** | §6.5/§6.7.1 改 cards.py 口径；缩进/图标/可读性不做代码改动，列进 P4 真机探针清单（用户不接受再回退宿主） |
| L1 | `_ld_footer(chat_id, started=123.0, status=None)` 完成快照后不得含 ✅；无参必须 None | **采纳** | §6.2 行 4（Design B 结构上成立；`turn_card` 默认 False ⇒ 无参 None） |
| L2 | 完成后 `send("Gateway online …")` 整卡无 footer 元素、无 ✅ | **采纳** | §6.2 行 5/7（含 B-2 的空元素问题；两条系统提示各一条） |
| L3 | `send_stream_frame(finalize)` / `edit_message(finalize)` / static 真回答保留 ✅ | **采纳** | §6.2 行 6；`_ld_frame_footer` 显式解析状态 |
| L4 | `tool_step_elements` 硬字面量 + check_cardview Error/常量 | **采纳** | §6.2 行 1-3 + 注（B 给了精确测试名） |
| L5 | 新变异：fallback 加回 / 非回合页脚加回 / 收尾 status 去掉 / detail+output 退回 notation | **采纳** | §6.2 的 V073-1a/1b/1c/2a/2b/2c/2d（7 条，全部 `test_units`，`-k` 完整模式实红） |

### 6.9 B 路逐条处置（用户可见效果 / 证据 / 反假绿全量报告，2026-09-22；基线 `08ac549` 同代码）

| # | B 原文摘要 | 处置 | 落点 / 理由 |
| --- | --- | --- | --- |
| 1 | 变异 #3 被非回合早退遮蔽 ⇒ 假绿 | **采纳** | §6.1 条 1 的「B 配套断言」+ §6.2 行 4：`turn_card=True` 无 status 必须恰好无 ✅；V073-2a 因此可红 |
| 2 | 文本返 None ≠ 无 footer 元素（`view.footer or " "` 空行） | **采纳** | §6.1 条 2：非回合 `footer_enabled=False` + 单测断言 `element_id=="footer"` 不存在；**CardKit seed 例外**（结构定死保留槽位） |
| 3 | 锚点连带失效：V4-57、V4-7、V4-17B、G2-3；6 处单测可能 red-crash | **采纳** | §6.3 锚点连带失效条 + §6.1 条 6；改完 preflight 全数绿，单测修到 red-assert（不 crash） |
| 4 | 全量 `send()` 判非回合会误伤非 native 真终稿与 `/larkdeck` 回复 | **采纳（区分 + 文档）** | §6.1 条 3/8：非 native 终稿带 `notify`/`expect_edits` ⇒ 仍保留；`/larkdeck` 命令回复**有意**无页脚并写进 README/plugin.yaml；P4 grep 日志复核 |
| 5 | Q1 三条最小硬字面量断言（给出精确用例名/断言） | **采纳** | §6.2 行 1-5 + 注：`test_v073_detail_and_error_text_sizes_are_x_small` / `test_v073_footer_turn_scope_and_status_is_explicit` / `test_v073_non_turn_send_has_no_footer_element` |
| 6 | Q2 变异表 M1-M7（old 锚点 count=1、M4 必须走 turn 侧） | **采纳** | §6.2 的 V073-1a/1b/1c/2a/2b/2c/2d；P1 完成后重新 grep 保证 post-unique = 1 |
| 7 | Q3 golden：item1 恰好 9 叶 `notation→x-small`；无 Error 覆盖；item2 应 0 叶变化 | **采纳 + 加强** | 9 叶清单逐条解释；item2 若有 diff 即判 turn_card 误分类；Error 覆盖按 §6.3/A-4 决定（扩夹具或写明 + 双覆盖） |
| 8 | Q4 用户可见：host×theme 探针、Error 可读性、`text_profile=large` 不放大 x-small、澄清卡不受影响 | **采纳** | §2 3b + §6.5 + P4 探针清单；`large` 限制写进 README |
| 9 | Q5 发布脚本逐行参数化 + 120/150 低项 | **采纳** | §6.4 第一条逐行清单（含 15 行计数、139/148 审计路径、165/167-168 scratch、169/173 allow-at、210 notes、18 行 120→150） |

**P0 收敛结论**：A/B/C 三路的高阻断项全部采纳并落到 §6.1-§6.9；设计在 A/B 实测后由「C 前版」演进为
**Design B**（`_ld_footer` 纯格式器 + send 层显式 turn + 非回合关 `footer_enabled`），属于实质变更 ⇒
在 P1 动手前再跑 **一轮 3 路只读快速复核**（聚焦 §6.1/§6.2/§6.3，基线为本次提交），复核无高阻断才进 P1。

---

## 7. 旧「登记 v0.7.3」逐项处置（`docs/plan-v0.7.2.md` §3）

> 本批范围经用户 2026-09-22 重新拍板 = §1 + §4 + 注释口径；下表把上一批写「截止 v0.7.3」的遗留项一次
> 结清（关闭 / 完成于 v0.7.2 / **defer v0.7.4** + 理由），避免发布后标签变假。

| 遗留项 | 处置 | 理由 / 证据 |
| --- | --- | --- |
| 表单容器形态（`form`/submit/`form_value`） | **defer v0.7.4** | 与三项无耦合；需真机 submit 探针 + `check_clarify_e2e` 覆盖，属独立批次 |
| 澄清卡 submitted/retry/30min TTL 三态 | **defer v0.7.4** | 需状态机 + TTL 设计；同理 |
| 长回合（tools>20）真机复验 | **关闭（已完成）** | 25 步长回合卡 `om_x100b641635da808cc11b25152858806`；`release-checklist.md` 记「长回合无灰气泡（用户确认）」 |
| `panel_color_tags` 对结构化车道是死开关 | **defer v0.7.4**（拟定 re-scope：要么 cardview 吃开关，要么删键） | 语义决策 + 观感变更，不与本批 x-small 混批 |
| 字段白名单只覆盖面板树 | **defer v0.7.4** | 属门禁增强；需按官方 2.0 字段表逐 tag 登记整卡（header/footer/answer/降级车道） |
| `_save_ledger` 丢 `_meta` / 断言内部改动不失效 | **defer v0.7.4**；本批不触发 | 本批 P3 只用 6 片 full + `merge_ledger4.py`（现场三指纹 + `full_audit_tree` + 12 对照），不走 `--delta --update-ledger`；深改账本工具会放大本批失效面 |
| `show_reasoning=true` 嵌套面板真机渲染 | **保持挂起（待用户回话）**；未回话则转 v0.7.4 | 默认 `false` 无用户可见风险；重发卡 `om_x100b641787b9bca0c39cc70479d9390`（`probe_nested_panel.py`） |
| 低层出站原语无统一留痕 | **defer v0.7.4** | 需调用点枚举断言 + 灰度日志设计 |
| `seq += 1` 换号重试只被夹具保护 | **defer v0.7.4** | 需构造「删除是这一帧最后一次写」场景断言；本批夹具必重生成，专项放 v0.7.4 更稳 |
| 分片合并工具固化 + `--shard` 结束打印 | **部分完成**：`tools/merge_ledger4.py` 已进仓并完成 v0.7.2 P5 合并；**`--shard` 结束打印 defer v0.7.4** | 本批 P3 复用同一工具；结束打印是低风险增强，时间允许可顺手做（不新增断言/变异） |
| 账本来源凭证（每条 tree hash + 日志路径 + 行号） | **部分完成**：merge 记 `full_audit_tree`/`tree_dirty`/三指纹；**每条来源凭证 defer v0.7.4** | 属账本格式升级；本批用 12 对照 + 现场指纹重算加固 |
| 图标更全面①面板标题区段图标 | **关闭** | 用户 2026-09-22 拍板「emoji 用现状吧」（面板标题保持）；P3.1 已把线性图标扩到详情行/错误块/折叠提示 |
| 图标更全面②页脚四段图标（B4） | **defer v0.7.4** | 用户未拍板；需先真机探针调列宽/折行（早先 179px 空隙） |
| 图标更全面③澄清卡 / `status`/`config` / 降级提示图标 | **defer v0.7.4** | 非本批；每个新位置都要「断言 + 变异 + 重验」 |
| 无界等待/裸 join 全仓扫 | **defer v0.7.4** | 现测仍有 3 处裸 `await release.wait()`（`test_units.py:11882/11938/12501`）；需静态门禁设计 |
| D5 自绘动画（Unicode 字形 + 定时重写） | **关闭** | 用户已选自研 GIF（D1′，「就它」）；D5 是被取代的备选 |
| E2 过程/正文交错结构 | **defer v0.7.4** | 正文分段 + 渲染器结构调整，独立批次 |
| 嵌套 `element_id` 单独 `partial_update` 探针 | **defer v0.7.4** | 需真机探针；与「嵌套面板渲染」同一张待回话卡 |
| 加载动图资产主题化 | **关闭** | D1′ 自研资产已定版；彻底主题化只剩 D5 且已关闭 |

**收尾纪律**：以上「defer v0.7.4」是**书面承诺**，v0.7.4 计划必须先复核本表；关闭项若再被用户点名，
重新立项而不是翻旧账。
