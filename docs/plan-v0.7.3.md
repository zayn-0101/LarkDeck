# v0.7.3 计划（细节行字号 `x-small` + 系统提示去状态词 + 注释口径）

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
| **P0 规划** | 本文件冻结（范围 = §1 + §4；含用户逐字确认 §4.1） | 文件入库 + 用户无异议 | **≥3 路**（技术可行性 / 用户可见效果与证据 / 流程与诚实性）—— 已于 2026-09-22 起跑（`7fe3d34b` / `98c88adb` / `92f41a3f`），逐条处置写回本节 |
| **P1 实现** | `core/cardview.py`（两处 `text_size`）、`core/adapter.py`（去掉状态兜底 + 非回合不出页脚）、4 处代码注释历史口径 | `py_compile` 全绿；`_ld_footer` 的**每个调用点**逐一核对「显式传 status/started 与否」并留结论 | **≥3 路**（判别力 / 协议不破坏 / 文档诚实） |
| **P2 断言与变异** | 硬字面量断言（细节行/错误块 `x-small`；真实回合仍有 `✅ 已完成`；系统提示无页脚）+ 新变异（各自完整模式 `-k` 实红）+ 黄金夹具重生成（diff 逐条解释） | 每条新变异**实红且红在断言上**；夹具 diff **只应**出现 `text_size` 相关变化（若夹具不变，要写出「为什么不变」） | **≥3 路**（变异是否真有判别力 / 夹具是否覆盖 / 有无自证循环） |
| **P3 全量重验** | 6 分片完整模式（独立账本、`-u` 不缓冲、起跑前清陈旧证据 + 校验工作树干净） | 每片坏 0、合并 `✅ 499 条通过`、`full_audit_at == 被测提交`、`n_inh == 0`、`--ledger-status` 499/499 待跑 0、**实测墙钟写回本文件** | **≥3 路**（证据链 / 时间与隔离 / 反假绿） |
| **P4 部署与真机探针** | `.deploy` 指被测提交 + 网关重启（有界等「启动自检通过」）+ **一张改前/改后对照卡**（细节行两档 + 一个 Error 块 + 一张系统提示卡样例） | 自检通过；用户目视回话；把结论写进 `docs/verify-log.md` | **≥3 路**（探针是否可判读 / 是否覆盖两侧 / 有无误导） |
| **P5 发布** | 用户终验后 push + tag **v0.7.3** + `gh release` + `.deploy` 指 tag + 网关重启 | 发布脚本 `--check` 全绿后 `--go`；tag/`.deploy`/自检三处留痕 | **≥3 路**（发布完整性 / 坐标一致 / 文档与证据一致） |
| **P6 洁癖收尾（neat-freak）** | 文档 / 规则（AGENTS.md）/ 记忆 / 残留与代码现实对齐：`docs/verify-log.md`、`CHANGELOG.md`、`docs/releases/v0.7.3.md`、`docs/plan-*.md`、注册项（`text_weight` 不可用、十六进制颜色无效、4 处注释、清场待办）、审计 worktree 与临时目录 | 全仓 grep 不再有「已过时/自相矛盾」的现行口径；残留清单给用户过目 | **≥3 路**（知识一致性 / 残留与清场 / 发布完整性复核） |

**纪律**：每阶段审计回来 → 逐条给处置（含**未采纳的理由**）→ 讨论收敛后才进下一阶段；
**禁止 sleep 轮询**（只用有界等待器）；全量重验期间**不碰任何文件**。

---

## 6. P0 审计收敛（三路对抗审计 → 逐条处置）

审计 agent：A 技术可行性 `7fe3d34b` · B 用户可见效果与证据 `98c88adb` · C 流程与诚实性 `92f41a3f`。

### 6.1 **必须更正的实现口径（C-3，高）**：第 2 项的判据不是「started/status 缺省」

原判据（§4 表）有洞：系统提示卡走 `send()`，而 `_ld_render_card` 会用
`_ld_cardview(status="completed")` **自算**状态，`_ld_view_status` 又用 panel 快照覆盖 ⇒ 提示卡
即使「没有回合上下文」也可能拿到显式 status。**改为显式回合标志**：

* 在 `send()/edit_message()` 里按「这条消息是不是某个回合的产出」传下 `turn_card: bool`（收尾帧=真，
  静态发送/系统提示/命令回复=假）；
* `_ld_footer(..., turn_card=...)`：**非回合**一律 `return None`（整段不出页脚）；
* `_ld_view_status` / `_ld_render_card` 的 **panel 快照状态覆盖**只在 `turn_card=True` 时生效；
* `_log_turn_selfcheck`（`adapter.py:1727`）自己那条「页脚=…」的日志：回合自检要显式传
  `status`/`turn_card`，别因为改这里而永远打「页脚=无」；
* **逐一核对调用点**：`1727 / 2668 / 3271 / 3785 / 4236 / 4825` 与
  `tests/test_units.py:1533-1539 / 2241-2246 / 2321-2326 / 5238`，每处写「该有页脚 / 不该有」的结论。

### 6.2 断言与变异清单（C-2，高；每条都要进 6 片全量）

| # | 语义 | 硬字面量断言（文件/用例） | 新变异（old → new） |
| --- | --- | --- | --- |
| 1 | 细节行 `x-small` | 细节行元素 `text_size == "x-small"` | `"x-small"` → `"notation"` |
| 2 | Error/Result 块 `x-small` | 同上（该元素） | 同上（该元素） |
| 3 | 状态兜底不许回来 | 一次完成回合后发提示卡 ⇒ **无页脚** | `_ld_footer` 里加回 `panel_snap.get("status")` |
| 4 | 非回合抑制不许丢 | 同上（无 footer 元素） | 去掉 `turn_card` 判据 |
| 5 | **真实回合必须保留** `✅ 已完成` | 收尾卡断言 `startswith("✅ 已完成")` | 把 `turn_card` 判据写反（回合卡也抑制） |

### 6.3 冻结与盖章纪律（C-4/C-5/C-6/C-7，高/中）

* **全量不可避免**：golden 夹具含细节行的 `text_size:"notation"`（`_HELPER_FILES`）⇒ 重生成后
  `helper_fp` 变 ⇒ `_delta_split` 令**全部 499+ 条 todo** ⇒ **禁 `--delta`、禁 `--seed-inherited`**；
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

### 6.4 发布机制（C-12/C-13，中）

* 新建 `~/.larkdeck-scratch/release-v0.7.3.py`：`TAG=v0.7.3`、`TAG_MSG` 补本批三项、
  notes = `docs/releases/v0.7.3.md`、`--allow-at adea7cb`、新 scratch 目录 `seed{1..6}/shard{1..6}`；
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
* `core/cards.py:221-222` 声称 `x-small` 不在官方文档 ⇒ 使用处**必须加限定**（只在明确档位启用）
  + 附真机探针证据（本次卡 3/卡 4）；
* `CHANGELOG.md:17` 仍是 `[Unreleased] - v0.7.2`（已 tag）⇒ 改 `[0.7.2]` + 开 v0.7.3；
  `plugin.yaml` 的 `version` 要 bump；README/plugin.yaml 补「非回合不再出页脚、真实回合不变」；
* `tests/test_units.py` 里短码旧口径 docstring（`6536 / 6781 / 6841 / 1593`）顺手改。

### 6.6 登记卫生（C-15/C-16，中）

* 证据强度标签：`text_weight`（`200621` + path + 探针脚本 `tests/probe_style_candidates.py`，**强**）；
  十六进制颜色（卡 id + 用户目视「三行一样」，**中**：无 payload/截图哈希）；`grey` 最浅枚举
  （**弱**：未附枚举出处，需在实现时补官方链接或改写为「实测十六进制无效」）；
* **清场清单**（待用户点头）：审计 worktree、`/tmp/larkdeck`、`~/.larkdeck-scratch/v0.7.2-*` 临时目录；
* **旧登记项处置**：`docs/plan-v0.7.2.md` 里标「登记 v0.7.3」的一批（表单容器 / 澄清卡 TTL /
  `panel_color_tags` 对结构化无效 / 全卡字段白名单 / ledger 来源凭证 / 无界等待 / B4 页脚多列图标 /
  D5 自绘 / E2 交错结构 / 嵌套 partial 探针）**必须逐项写 defer 或 re-scope + 理由**，
  否则发布后「登记 v0.7.3」这个标签就是假的。
