# v0.7.3 计划（细节行字号 `x-small` + 系统提示去状态词 + 注释口径）

> 承接 v0.7.2（已发布：tag `v0.7.2` = `6fd68f3`）。本文件是**下一批的唯一执行依据**；
> 老规矩：**先出计划 → ≥3 路子代理对抗审计 → 收敛后才动手**；每阶段结束再过审计；
> 用户真机确认后才发布。

> ⚠️ **终态注记（2026-09-23，冻结提交 `a2290da`）**：本文件中「Error 块回退 `notation`」「换 `markdown` 宿主 + fenced code」都是中间态，**已被用户选定的形态③取代**：`markdown` + 逐行 inline code + `x-small` + 组件级 icon，不拆元素；P3 六分片已在 `a2290da` 重跑并重新盖章（`531/531` red-assert、12 对照、`tree_dirty=false`、`n_inh=0`）。详见 §6.16/§6.17、`docs/audits/v0.7.3/p4-error-inline-code.md`、`p3d-inline-code.md`。

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
| `core/cardview.py` | `_tool_output_div`（Error / Result 代码块）：~~`text_size` → `"x-small"`~~ **2026-09-23 真机回退为 `notation`**（C 宿主不生效，见 §6.16） | ⚠️ 该元素里 `**Error**` 标签与代码文本**同属一个元素**（不拆元素）；回退后标签与原字号一致 |
| `core/adapter.py` | **Design D**（§6.12）：`_ld_footer` 纯格式器（显式 status，缺省 fail-closed）；`send()` 默认回合 + `_ld_is_system_notice` 负清单（interim/已知系统提示 ⇒ 非回合）；预览 `status_locked`；非回合 panel/header/footer 全关；帧入口显式注入状态（structured `state["status"]` + legacy `frame_status`，`default_status` 只给 DEGRADE 收尾兜底）；非回合卡 `turn_card=False` 且 `/stop` 跳过 | 每个调用点逐一给结论（含 4039/SEQ3/R3-7/Y20）；判定打限流日志（P4 复核）；未登记前缀的新系统提示漏网要补清单并重跑 P3 |
| `core/cards.py` | 注释口径：`x-small` 从「不在文档、别赌」改为「markdown 真机已验 + 其它宿主见宿主矩阵；未过退回 notation」 | 不改 `text_profile` 档位表 |
| `tests/test_units.py` | 三条新用例（名字见 §6.10.6）；**并**修 §6.10.6 全表旧调用点（1533/1537…5306/5312、6860/6901、10914/10918 等） | **只新增用例名**；旧用例只改函数体，不许改名/删名；失败必须 red-assert 不 crash |
| `tests/mutate_check.py` | 新增 24 条 V073（§6.12.3 + §6.14 + §6.15；终态 32 条，见 §6.17）；同步重写 8 条旧锚点（G2-3/V4-7/V4-10/V4-17B/V4-57/SEQ3/R3-7/Y20）；`_is_full_run` 加 `if getattr(args,"shard",""): return False`；45s→90s 注释 | 每条 `-k` 完整模式实红；post-change 锚点唯一性 grep = 1 |
| `tests/check_cardview.py` | 加 Error 块夹具 + `detail text_size=="x-small"` / `error text_size=="notation"` 断言 + `PANEL_TEXT_SIZE=="notation"` | 字段白名单分档不变（`div.icon` 可带 size、`markdown.icon` 不可） |
| `tests/write_golden_trace.py` | **不改场景**（§6.10.8：扩 Error 涟漪大、收益低） | Error 由单测 + check_cardview 双覆盖 |
| `tests/golden_cardkit_trace.json` | 重生成：实测 **8 叶** detail `notation→x-small`（以实现后逐叶解释为准）；**item2 = 0 叶**（footer 叶 `✅ 已完成 · Test Model` 不变） | `--check` 必先一致；item2 出 diff 即判分类器误伤真回合 |
| `tests/probe_text_size_hosts.py` | **新增**宿主矩阵探针：`markdown` / `div.text=lark_md` / `div.text=plain_text`，各含同卡 notation 对照；**三主题各复跑**；Error 用真实长栈 | 单 host 被拒/不变小 ⇒ 该 host 退回 `notation`；Error 不可读 ⇒ 只 Error 块退回；结论写 verify-log |
| `docs/` + `plugin.yaml` + `CHANGELOG.md` | 完成记录 + `docs/releases/v0.7.3.md` + `version: 0.7.3` + `[0.7.2]` 收口/开 v0.7.3 + README/plugin.yaml 写清「非回合无页脚 / 真回合不变 / 命令回复无页脚」 | 只追加，不改历史行（历史证据保留） |

## 2. 验证计划（出口判据，机械可核）

1. `py_compile`（venv 解释器）+ `run_fast --full`（8 步全 OK）+ `mutate_check --preflight` **535/535**
   （523 变异 + 12 对照）；preflight 不是绿灯（`AGENTS.md:491-496`）；
2. 24 条新变异 `V073-1a…2aa` **`-k` 完整模式实红且红在 `test_units`**（贴输出）；
3. 黄金夹具重生成 + diff 逐叶解释：**实测 8 叶** detail `notation→x-small`；**item2 = 0 叶**
   （footer 叶不变）；helper_fp 变 ⇒ 全量必然；Error 不靠夹具（§6.10.8）；
3b. **`x-small` 宿主 × 主题探针**（§6.10.10）：3 host（markdown / `div.text=lark_md` /
   `div.text=plain_text`）× 3 主题（ap_lite/neutral/ap_bubble），Error 带 20–30 行真实栈；
   任一 host `code!=0`/不变小 ⇒ 该 host 退回 `notation`；Error 不可读 ⇒ 只 Error 块退回；
4. 既有门禁同步修好（§6.10.6 全表）：`check_hooks.py:772` + `test_units.py` 的
   `1533/1537、2241/2245、2321/2325、5147-5209、5229、5238-5267 各点、5306/5312、6860/6901、
   10914/10918` 必须显式传 `turn_card`/status，全绿且失败时仍是 red-assert；
5. 全量变异重验：6 分片完整模式、独立账本、坏 0；合并后 `full_audit_at == 当时的 HEAD`、`n_inh == 0`；
   实测墙钟写回（v0.7.2 那轮参考：499 条 ≈25 分钟，本轮 N 更大；机器空闲时更快）；
6. 真机探针卡：①「改前 / 改后」两行细节行对照 + 一个 Error 块（看可读性）；② 一张系统提示卡样例
   （无 ✅、无空页脚行）；③ 一张真实回合收尾卡（`✅ 已完成 · …` 一字不动）；④ 宿主×主题探针
   （3 host × 3 theme，见 §6.10.10）
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

**变更清单（已审计；实现口径以 §6.12 Design D 为准；§6.10/§6.1 仅作审计轨迹）**：

| 文件 | 改动 | 约束 |
| --- | --- | --- |
| `core/adapter.py` | **Design D（§6.12）**：`_ld_footer` 纯格式器；`_ld_send_is_turn` **默认回合 + `_LD_SYSTEM_NOTICE_PREFIXES` 已知提示负清单**；`status_locked` 锁预览；非回合 panel/header/footer 全关；帧状态显式注入（structured `state["status"]` + legacy `frame_status`） | 每个调用点逐一给结论；未登记的新系统提示暂按回合（已知限制写 README/verify-log）；判定打限流日志（P4 复核）；**无命令打点/无最近帧记忆** |
| `core/context.py` + `core/hooks.py` | **无运行时改动**（Design D 已删除命令打点/最近帧记忆；本批只修注释口径） | 不新增钩子状态；命令回复按默认回合保留页脚（有意） |
| `tests/test_units.py` | 3 条新用例 + §6.10.6 全表旧调用点更新 | 旧用例只改函数体；失败保持 red-assert 不 crash |
| `tests/mutate_check.py` | 24 条 V073 变异（§6.12.3 + §6.14 + §6.15）；重写 8 条旧锚点（G2-3/V4-7/V4-10/V4-17B/V4-57/SEQ3/R3-7/Y20）；`_is_full_run` 加 `if getattr(args,"shard",""): return False`（测试补 shard=""）；45s→90s 注释 | 每条 `-k` 完整模式实红；post-change 锚点唯一性 grep = 1 |
| `tests/golden_cardkit_trace.json` | 重生成：实测 8 叶 detail `notation→x-small`；**item2 = 0 叶变化** | diff 逐叶解释；item2 出 diff 即判分类器误伤真回合 |
| 真机探针 | 系统提示卡（无 ✅/无面板/无空 footer 行）+ 真实回合收尾卡（✅ 一字不动）+ 3 host × 3 theme + 长栈 Error 可读性 | 用户目视二值确认；结论写 `docs/verify-log.md` |

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
| **P0 规划** | 本文件冻结（范围 = §1 + §4 + 旧登记项处置 §7；含用户逐字确认 §4.1） | 文件入库 + 用户无异议 + 三路全量（§6.7-6.9）+ 两轮快速复核（§6.11/§6.13）逐条处置 + **Design D 沙箱全绿（§6.12.5）** | **≥3 路**×3 轮（技术可行性 / 用户可见效果与证据 / 流程与诚实性；第二轮 A2/B2/C2、第三轮 A3/B3/C3） |
| **P1 实现** | 按 §6.12 Design D 落地：cardview 三处 `x-small`；adapter 默认回合 + 系统提示负清单 + 显式 status；6 处注释口径（§6.7.1）；按 scratch 三脚本执行并人审 diff | `py_compile`（venv）全绿；`run_fast --full` 8/8；`--preflight` 535/535；调用点表（含 4039/SEQ3/R3-7/Y20）逐条结论 | **≥3 路**（判别力 / 协议不破坏 / 文档诚实） |
| **P2 断言与变异** | 8 条 v0.7.3 硬字面量用例 + 24 条 V073 变异（`-k` 全模式实红；终态 32 条，见 §6.17）+ 8 条旧锚点重写 + `check_cardview` Error 夹具/常量断言 + 黄金夹具重生成（8 叶 + item2 0 叶）+ 宿主×主题探针（切客户端主题复跑，§6.10.10 修正） | 每条新变异**实红且红在 `test_units`**；夹具 diff 逐叶解释；宿主矩阵逐张 `code` + 用户二值判读 | **≥3 路**（变异判别力 / 夹具覆盖 / 无自证循环） |
| **P3 全量重验** | 6 分片完整模式（独立账本、`-u` 不缓冲、起跑前清陈旧证据 + 校验工作树干净） | 每片坏 0、合并 `✅ N 条通过`（**N = 实际 `len(MUTATIONS)`**）、`full_audit_at` = 被测提交短码且其 tree == `full_audit_tree`、指纹路径自 fa 起未被改（允许其后只提交 docs/账本/新增探针）、`n_inh == 0`、`--ledger-status` N/N 待跑 0、**实测墙钟写回本文件** | **≥3 路**（证据链 / 时间与隔离 / 反假绿） |
| **P4 部署与真机探针** | `.deploy` 指被测提交 + 网关重启（有界等「启动自检通过」）+ 探针卡 4 项：细节行改前/改后、Error 块可读性、系统提示卡（无 ✅/无空 footer 行）、真实回合收尾卡（✅ 一字不动）；另 grep `turn=…` 日志复核分类 | 自检通过；用户目视回话；`turn` 日志证据、探针卡 id 写进 `docs/verify-log.md` | **≥3 路**（探针可判读 / 两侧覆盖 / 无误导） |
| **P5 发布** | 用户终验后 push + tag **v0.7.3** + `gh release` + `.deploy` 指 tag + 网关重启 | 发布脚本 `--check` 全绿后 `--go`；tag/`.deploy`/自检三处留痕 | **≥3 路**（发布完整性 / 坐标一致 / 文档与证据一致） |
| **P6 洁癖收尾（neat-freak）** | 文档 / 规则（AGENTS.md）/ 记忆 / 残留与代码现实对齐：`docs/verify-log.md`、`CHANGELOG.md`、`docs/releases/v0.7.3.md`、`docs/plan-*.md`、注册项（`text_weight` 不可用、十六进制颜色无效、注释口径 6 处、清场待办）、审计 worktree 与临时目录 | 全仓 grep 不再有「已过时/自相矛盾」的现行口径；残留清单给用户过目 | **≥3 路**（知识一致性 / 残留与清场 / 发布完整性复核） |

**纪律**：每阶段审计回来 → 逐条给处置（含**未采纳的理由**）→ 讨论收敛后才进下一阶段；
**禁止 sleep 轮询**（只用有界等待器）；全量重验期间**不碰任何文件**。

---

## 6. P0 审计收敛（三路对抗审计 → 逐条处置）

审计 agent：A 技术可行性 `7fe3d34b` · B 用户可见效果与证据 `98c88adb` · C 流程与诚实性 `92f41a3f`。

### 6.1 第 2 项定版口径（C-3 + A 路 2 条高阻断全量报告后冻结）：显式 turn 决策 + `_ld_footer` 退成纯格式器

> ⚠️ **本节（Design B）已被 §6.12 Design D 取代**（B/C 两版分类器先后被 A2/B2 与 A3/B3/C3 实测否掉；
> 保留作审计轨迹；实现以 §6.12 为准）。

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

> ⚠️ **变异编号已被 §6.12.3 取代**（12 条 V073-x）；本表保留作审计轨迹。

| # | 语义 | 硬字面量断言（文件/用例） | 新变异（old → new） |
| --- | --- | --- | --- |
| 1 | 细节行（line）`x-small` | `test_v073_detail_rows_x_small_error_falls_back_to_notation`：`tool_step_elements(step,"line")[0]["text_size"]=="notation"`（标题不动）、`[1]["text_size"]=="x-small"` | **V073-1a**：detail-line 块 `"text_size": "x-small"` → `"notation"`（与 V4-57 同锚点，V4-57 两侧同步改） |
| 2 | 细节行（emoji）`x-small` | 同用例：`tool_step_elements(step,"emoji")[1]["text"]["text_size"]=="x-small"` | **V073-1b**：plain_text detail 块的 `"text_size": "x-small"` → `"notation"` |
| 3 | Error/Result 块 `x-small` | 同用例：`els[2]["text"]["text_size"]=="x-small"`（`ToolStepView(error_block="boom")`） | **V073-1c**：lark_md 错误块的 `"text_size": "x-small"` → `"notation"` |
| 4 | `_ld_footer` 是纯格式器（回合侧也不许偷快照） | `test_v073_footer_turn_scope_and_status_is_explicit`：完成快照 + model/ctx、**不传 status** ⇒ `_ld_footer(chat_id="oc_v073", turn_card=True) == "Test Model · ctx 1k/10k · 10%"`（**无 ✅**）；`turn_card=False is None`；`(_ld_footer(..., started=…, status="completed", turn_card=True) or "").startswith("✅ 已完成 · ")` | **V073-2a**：guard 后加回 `status or (panel_snap.get("status") …)`（必须用 `turn_card=True` 的断言钉住，否则被早退遮蔽 ⇒ 假绿，B-1） |
| 5 | 非回合抑制不许丢 | `test_v073_non_turn_send_has_no_footer_element`：完成后 `send("Gateway online …")` ⇒ `body.elements` 中 **无 `element_id=="footer"`**、整卡 JSON 无 `✅ 已完成` | **V073-2b**：`if not turn_card: return None` → `if False: return None`（测试用带 model/ctx 的 `turn_card=False`，否则空页脚本就是 None、变异假绿） |
| 6 | 真实回合必须保留 `✅ 已完成` | 收尾帧/`edit_message(finalize=True)`/`send_stream_frame(finalize=True)` 页脚 `startswith("✅ 已完成 · ")`（assertion 带 `or ""` 防 None 崩溃） | **V073-2c**：guard 取反 `if turn_card: return None` |
| 7 | 非回合静态卡不能留空 footer 元素 | 同用例 5（B-2 实测：只让文本为 None 仍会写 `{"element_id":"footer","content":" "}`） | **V073-2d**：删掉 `_ld_render_card` 非回合分支的 `view.footer_enabled = False` |

> 三条新单测名（B 路 Q1，全部**只新增**）：`test_v073_detail_rows_x_small_error_falls_back_to_notation` /
> `test_v073_footer_turn_scope_and_status_is_explicit` / `test_v073_non_turn_send_has_no_footer_element`；
> 可选 `check_cardview` 加固：`assert detail["text_size"] == "x-small"` + Error 夹具 +
> 生产常量 `PANEL_TEXT_SIZE == "notation"`（A-4：现有白名单只放行任意 `text_size`，抓不住悄悄退回）。
> 新变异 7 条（V073-1a/1b/1c/2a/2b/2c/2d）全部 `test_units` 门禁；old 锚点在 P1 改完后**重新 grep**
> 保证唯一（B 路 Q2 表：post-unique 必须 = 1）。

### 6.3 冻结与盖章纪律（C-4/C-5/C-6/C-7，高/中）

> ⚠️ 计数/锚点以 **§6.14/§6.15** 为准（**523 变异 / 535 锚点 / 8 条重写 / 动态 `_fa`**）；
> §6.10.7-§6.10.9 是第二轮历史口径。

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
| 5 | Q1 三条最小硬字面量断言（给出精确用例名/断言） | **采纳** | §6.2 行 1-5 + 注：`test_v073_detail_rows_x_small_error_falls_back_to_notation` / `test_v073_footer_turn_scope_and_status_is_explicit` / `test_v073_non_turn_send_has_no_footer_element` |
| 6 | Q2 变异表 M1-M7（old 锚点 count=1、M4 必须走 turn 侧） | **采纳** | §6.2 的 V073-1a/1b/1c/2a/2b/2c/2d；P1 完成后重新 grep 保证 post-unique = 1 |
| 7 | Q3 golden：item1 恰好 9 叶 `notation→x-small`；无 Error 覆盖；item2 应 0 叶变化 | **采纳 + 加强** | 9 叶清单逐条解释；item2 若有 diff 即判 turn_card 误分类；Error 覆盖按 §6.3/A-4 决定（扩夹具或写明 + 双覆盖） |
| 8 | Q4 用户可见：host×theme 探针、Error 可读性、`text_profile=large` 不放大 x-small、澄清卡不受影响 | **采纳** | §2 3b + §6.5 + P4 探针清单；`large` 限制写进 README |
| 9 | Q5 发布脚本逐行参数化 + 120/150 低项 | **采纳** | §6.4 第一条逐行清单（含 15 行计数、139/148 审计路径、165/167-168 scratch、169/173 allow-at、210 notes、18 行 120→150） |

**P0 收敛结论（第三轮后）**：A/B/C 三路全量报告 + 两轮复核共 9 份，全部逐条处置
（§6.7-§6.9、§6.11、§6.13）。设计最终定版 = **§6.12 Design D**：`_ld_footer` 纯格式器、
`_ld_send_is_turn` **默认回合 + 已知系统提示负清单**、非回合静默卡（panel/header/footer 全关）、
帧状态显式注入（structured `state["status"]` + legacy `frame_status`）。
**Design D 已在干净沙箱 clone 上完整验证**（§6.12.5；D1/D2/D3 复核后的收口计数与新增用例见 §6.14）。
下一步：把验证过的脚本落到仓库（P1，人审 diff）→ P1 阶段 3 路审计 → P2 断言/变异/夹具/宿主探针。

> ⚠️ **第四轮修订**：以上第二轮结论里的 Design C 又被 A3/B3/C3 否掉（命令标记误杀真回合、
> 默认非回合让非 native 真终稿丢 ✅）。**最终定版 = §6.12 Design D**，其沙箱证据见 §6.12.5
> （历史第二轮沙箱：run_fast 8/8、test_units 294/294、preflight 523/523、12 条 V073 全 red；
> 终态数字见 §6.17：test_units 299/299、preflight 543/543、V073 系列 32 条全 red）。
> ⚠️ **修订（2026-09-22 深夜）**：A/B/C 全量报告回来后又跑了第二轮只读复核（A2 `68f8f2f1` /
> B2 `4ec4599b` / C2 `b72bf8d0`）。三路一致指出 **Design B 的 `send()` 分型不可实现**
> （`notify` 不是回合专属、存在无标记真终稿、命令回复也带 `notify`）。设计随后改版为 Design C，
> 又被第四轮否掉 ⇒ **最终以 §6.12 Design D 为准**；§6.1-§6.3、§6.10 保留作审计轨迹。

---

### 6.10 第二轮复核收敛：**Design C**（历史；已被 Design D 取代，仅作审计轨迹）

> ⚠️ **本节 Design C 已被第四轮复核否掉（A3/B3/C3）⇒ 定版见 §6.12 Design D**；保留作审计轨迹。

#### 6.10.1 Design B 被实测否掉的两头

* **通知/命令会被误判成回合**：`/larkdeck` 与核心 slash 命令的回复由 base 当「最终回复」发，
  被 `_mark_notify_metadata` 打上 `notify=True`（`gateway/platforms/base.py:135-139, 3938-3960,
  3120-3122`；`gateway/run_inbound.py:984-990, 1136-1144, 1204-1206`）⇒ 只看 `notify` 会把
  `/larkdeck status` 也判成回合、继续挂 ✅/页脚（推翻 §6.1.5/§6.1.8 的承诺）。
* **真终稿会漏判**：`run_startup.py:384-386`（重启后重投递 recovered final，metadata 仅 thread_id）、
  `run_turn.py:3381-3383 → run_notifications.py:315-347`（queued-lane final fallback）、
  `stream_consumer.py:481-498`（boundary fallback `adapter.send(chat_id, finalize_text)` 无 metadata）
  都不带 `notify/expect_edits` ⇒ 默认非回合会丢 ✅。
* 结论：**单靠 upstream metadata 判不出「回合」**；必须加「命令回复打点 + 本回合最后一帧内容认领」。

#### 6.10.2 Design C 判定顺序（`_ld_send_is_turn(chat_id, content, metadata, guarded)`）

1. `metadata["_interim_send"] is True` ⇒ **非回合**（中途播报，最高优先级，先掐）；
2. 本 chat 刚派发过 slash 命令（`pre_gateway_dispatch` 打点，TTL 60s；非命令入站立刻清除）
   ⇒ **非回合** —— 命令回复即使带 `notify=True` 也不出页脚；
3. `guarded=True`（own 种子失败窗口，本回合首帧）⇒ **回合**；
4. `metadata["notify"] is True`（终稿）或 `metadata["expect_edits"] is True`（可编辑预览）⇒ **回合**；
5. 内容与本回合**最后一帧文本**一致（新维护的 `_LD_RECENT_TURN_TEXT`，TTL 120s；boundary/queued
   回落终稿没有 metadata 标记）⇒ **回合**；
6. 其余（网关系统提示 / 后台任务 / 媒体播报 / 未识别消息）⇒ **非回合**（fail-closed）。

**回合 status 显式定义**（B2-2）：

* `notify` / `guarded` / 内容认领 ⇒ `_ld_view_status(chat_id, default="completed")`
  （error/stopped 快照照常保留 ⇒ ❌/⛔，成功/无结局 ⇒ ✅）；
* `expect_edits` 预览（未收尾）⇒ `"processing"`，且 `_ld_render_card(status_locked=True)`
  锁死状态 —— 预览**绝不提前挂 ✅**（防快照里的上一个 ok 把预览染绿）。

**命令打点**：`core/hooks.py::_on_pre_gateway_dispatch` 对 `event.is_command()` 调
`_context.note_command_inbound(chat_id)`，否则 `clear_command_inbound`；只观察、恒返回 None、
绝不干预分发（`MessageEvent.is_command()` 是官方 API）。

**已知边界（写进 README/verify-log，不吹）**：我们**没**见过 native 流的重启后 recovered final
（无任何本地流记忆）按未知消息处理 ⇒ 不挂页脚；媒体 caption / 后台 watcher 直发同理。
P4 用 `turn=…` 限流日志覆盖真实流量复核，若发现误伤再补判据（要重验就必须重跑 P3）。

#### 6.10.3 非回合静态卡 = 静默消息卡（B2-1/A2-4）

`_ld_render_card(..., turn_card=False)` 在 `_ld_cardview` 之后强制：

```python
view.panel_enabled = False      # 旧回合面板可留 1800s（panel.py:57）⇒ 必须显式关
view.header_enabled = False     # card_status_header=true 时否则会渲染「✅ 已完成」
view.footer_enabled = False     # 只把 footer 置空不够：entity_skeleton 会写 view.footer or " "
view.footer = None
```

CardKit **seed 建卡**不受影响（结构建卡定死、之后还要写元素，footer 槽位必须保留）。
测试必须预置 stale panel + `card_status_header=true`，断言整卡只有 `answer`（无 panel/header/footer）。

#### 6.10.4 收尾竞态（B2-3/A2-3）

`_ld_frame_footer(state, *, turn_card=True, default_status="")` 自己解析状态：
`state["status"]` → 本回合 panel 快照；只认 `ok/completed/error/stopped` 四个值，其余
（processing/缺失）用 `default_status` —— **不脑补**。

* `edit_message(finalize=True)`：先算 `turn_status = _ld_view_status(chat_id, default="completed")`，
  同时传给 `_ld_render_card(status=turn_status)` 与 `_ld_frame_footer({"status": turn_status})`；
  非 finalize 用 `"processing"`；
* native 收尾整卡（`adapter.py:4281`）：`_ld_frame_footer(state, default_status="completed")`；
* `_ld_ck_split` legacy 封旧卡：`default_status="completed"`（与结构化分支同语义）；
* 其它帧路径（3871/4039/4382/4437/4480）保持 `default_status=""`（未收尾不挂状态词）。

测试：`panel.status` 为空/processing 时 finalize 仍有 ✅；`panel.status=error` 时仍 ❌。

#### 6.10.5 调用点全表（补 A2-5 / B2-8）

> ⚠️ 下表行号是 P1 落地**前**的旧快照，已随实现漂移；**以函数/调用点符号名为准**，
> 不要再按行号审计（P1 实现后真实行号见 `core/adapter.py` 的符号搜索）。

* `_ld_footer` 直接调用：`1727`（自检 ⇒ `_ld_frame_footer({"chat_id":…})`）、`2203`（定义）、
  `2668`（send ⇒ 按 6.10.2）、`3271`（封旧卡 ⇒ frame footer + `default_status="completed"`）、
  `3785`（view 基础页脚 ⇒ `turn_card=True`）、`4236`（legacy seed ⇒ `turn_card=True`、不传 status）。
* `_ld_frame_footer`：`2016 / 2739 / 3871 / **4039（补）** / 4281 / 4382 / 4437 / 4480 / 4825`
  —— 全部回合路径，逐个核对；`4039` 是 CardKit 每帧 footer 实物写入（默认 transport），
  默认 True 但**必须在表里**。
* `_ld_render_card`：`2665`（send）、`2735`（edit）。
* `_ld_cardview` 8 个 caller：`2026`（诊断）/ `2509`（透传）/ `3261`/`3285`（切卡）/ `3629`（心跳）/
  `3925`/`3986`（帧）/ `4829`（/stop）—— 全为回合或由 `_ld_render_card` 决定，已核。
* 澄清卡 `4885-4888` 不经 footer；`5877` 是 docstring，实际 `/larkdeck` 回复走 `send()`（由命令打点覆盖）。

#### 6.10.6 测试同步（A2-6 / B2-6 / C2-2；⚠️ 历史口径：命令打点/最近帧认领已被 Design D 删除，最终清单见 §6.14）

* 旧用例（**只改函数体，不改/删名字**）：`test_units.py` 的
  `1533/1537、2241/2245、2321/2325、5147-5209、5229、5238/5239、5243-5247、5251、5254、5258、
  5263、5265、5267、5306、5312、6860、6901、10914/10918`；
  `check_hooks.py:772` 显式 `status="completed", turn_card=True`；
  `probe_render.py:228/994` 的无参 `_ld_footer()` 同步补 `turn_card=True`。
* `send()` 驱动真实回合的旧用例补 `metadata={"notify": True}`（`test_units.py` 7 处 + `raw2` 1 处）。
* 新用例 3 条（B2 命名建议，采纳）：`test_v073_detail_rows_x_small_error_falls_back_to_notation` /
  `test_v073_footer_turn_scope_and_status_is_explicit` /
  `test_v073_non_turn_send_has_no_footer_element`（覆盖：stale panel+header 静默、命令回复静默、
  notify 终稿 ✅、expect_edits 预览无 ✅、最近帧内容认领、interim 无 footer、finalize 竞态兜底）。
* 失败性质：改完必须是 **red-assert**，不得 `None.startswith`/`TypeError` 式 red-crash；
  `5254/5265/5312` 原来是「`or ""` 假绿」，必须显式传参后仍有判别力。

#### 6.10.7 变异与锚点（C2-3/5/6/7 + A2-7；⚠️ 历史口径：2g/2h 已随 Design D 删除，最终 24 条见 §6.15）

* **新变异 12 条**（全部 gate `test_units`，`-k` 完整模式实红）：`V073-1a`（line 细节）、`V073-1b`
  （emoji 细节）、`V073-1c`（Error 块）、`V073-2a`（快照兜底加回）、`2b`（非回合早退失效）、
  `2c`（回合侧被误杀）、`2d`（空 footer 元素）、`2e`（忽略 notify/expect_edits）、`2f`（分类器恒
  回合）、`2g`（命令打点失效）、`2h`（最近帧认领失效）、`2i`（interim 不再排除）。
  每条 old 锚点按 P1 后的实现逐字冻结；`2a` 必须用 turn 侧断言（防早退遮蔽）。
* **5 条旧锚点同步重写**（不是「失效窗」）：`G2-3`（`_ld_frame_footer` 的新 return）、`V4-7`、
  `V4-17B`（`base_footer` 新格式）、`V4-10`（`status_locked` 行）、`V4-57`（detail 两侧 `x-small`）。
* **计数**：499 + 12 = **511 变异**；preflight = 511 + 12 = **523/523**；账本/merge 为 **511/511**。
  release 脚本里 ledger 与 preflight 两个数字要分开写，并断言
  `preflight_total == len(MUTATIONS) + len(CONTROLS)`。
* 其它 ±15 窗指纹失效**不穷举**（实测只有上述 5 条锚点断），以 P1 后实际 fingerprint 为准；
  golden helper_fp 必变 ⇒ 全量不可避免（禁 `--delta/--seed-inherited/--target-only`）。
* `--preflight` 不是绿灯（`AGENTS.md:491-496`）；`_is_full_run` 显式加 `not args.shard`
  （C2-6）；分片账本必须 `--update-ledger` 各写各的 `LARKDECK_LEDGER_PATH`。

#### 6.10.8 golden 夹具决策（C2-8 / A-4）

**保留现有场景、不扩 Error 步骤**：扩 Error 会改 header 步数（2→3）并新增/重排多批 decor/final
叶，远超「9+1」，整体重冻结成本高于收益；Error 改由
`test_v073_detail_rows_x_small_error_falls_back_to_notation`（硬字面量三层）+ `check_cardview` Error 夹具
双覆盖，并在本文件写明「golden 不覆盖 Error」这一事实。
实测 item1 diff = 8 个叶（detail 行 notation→x-small；B 报的 9 恰是上一版场景计数，以实现后
实际 diff 为准逐叶解释）；**item2 = 0 叶变化**（footer 叶仍是 `✅ 已完成 · Test Model`）。

#### 6.10.9 发布机制修订（C2-4/5/6/7）

* `--allow-at` **保留动态 `_fa`**（merge 后 `full_audit_at=C`，发布在 D 上跑）：`adea7cb` 只作历史
  补充，不得硬编码顶替动态值；
* release 计数（历史行，当前以 §6.15 为准）：ledger **511/511**、preflight **523/523**；步骤 0 断言
  `docs/releases/v0.7.3.md` 存在、`plugin.yaml` version == TAG；`--check` 对 `.deploy != HEAD`
  直接失败（不只打日志）；
* 1a 追加：`_meta.tree_dirty is False`、`_meta.full_audit_tree == git rev-parse <full_audit_at>^{tree}`
  （C3-9：发布在 D 上跑，不能拿 HEAD 树比；保留 fa 祖先与指纹路径禁 MD 漂移）、逐条 `verdict=="red-assert"`
  且 `at==full_audit_at`、`len(entries)==len(MUTATIONS)`；
* 低项：脚本 18 行 120s→150s。

#### 6.10.10 探针修订（B2-4/5）

`tests/probe_text_size_hosts.py` 三张卡各出**同卡 notation 对照**，且 **ap_lite / neutral / ap_bubble
三主题各复跑**（不能只「主题各一张」）：① `markdown` 生产 detail(line)；
② `div.text=plain_text` emoji detail；③ `div.text=lark_md` **真实 Error**（`**Error**` + 20–30 行
栈 + 一条超长行）。用户看三件事：`code==0`、x-small 确实更小且不换行溢出、Error 仍可读（二值）。
回退规则：单 host 被拒/不变小 ⇒ **该 host 全部退回 `notation`**；Error 能发但不可读 ⇒ **只 Error
块**退回 `notation`；两者都要同步改断言/变异/夹具再重验。**2026-09-23 已执行：C（`div.text=lark_md`）
真机目视与 `notation` 同大 ⇒ Error 块回退，commit `db58dc5`，见 §6.16。**设备/客户端/主题写进 `docs/verify-log.md`。

#### 6.10.11 文档与措辞（B2-6/9）

* README/plugin.yaml 补：工具详情行 / Error-Result 块 = 字面量 `x-small`，**不随
  `text_profile` 放大**（`apply_text_profile` 只补缺省值，`cards.py:285-288`）；未过宿主矩阵探针
  的宿主退回 `notation`；非回合消息**无面板/状态头/页脚**（不只是无 footer）；
* 把 §6.1/§6.2 里「结构上不可能」「非 native 终稿仍有 ✅」等强于证据的话改成有条件表述，
  条件 = 6.10.2 的判定顺序 + P4 日志复核；
* `plugin.yaml` version 在 commit C 里 bump；CHANGELOG `[0.7.2]` 收口 + 开 v0.7.3。

#### 6.10.12 沙箱验证（本轮已做，证据在 scratch）

在 `~/.larkdeck-scratch/sandbox-v073/larkdeck` 的干净 clone 上按上述实现（脚本：
`~/.larkdeck-scratch/v0.7.3-apply_core.py` / `-apply_tests.py` / `-apply_mut.py`）：

* `run_fast --full` = **8 步全 OK**，`test_units 293/293 passed`；
* `--preflight` = **523/523**（511 变异 + 12 对照）；
* `-k V073`：**12 条全部 red-assert（expected test_units）**；重写后的
  `V4-57/V4-7/V4-17B/V4-10/G2-3` 也全部 red-assert；
* golden 重生成：detail 叶 `notation→x-small`、footer 叶 `✅ 已完成 · Test Model` **不变**。
* ⇒ P1 在仓库落地时按这三个脚本执行（仍要人审 diff），随后 P1/P2 阶段审计再跑。


#### 6.11 第二轮三路逐条处置（A2 `68f8f2f1` · B2 `4ec4599b` · C2 `b72bf8d0`）

> ⚠️ 本节为第二轮历史处置：其中「命令打点 / 最近帧内容认领」已被 §6.12 Design D 推翻；
> 当前实现/发布口径以 §6.12 + §6.14 为准。

| 路 | 编号 | 原文摘要 | 处置 | 落点 |
| --- | --- | --- | --- | --- |
| A2 | 1 | notify 非回合专属、命令回复被误判 | **采纳（高）** | §6.10.2 命令打点；§6.10.3 静默卡；新变异 2g |
| A2 | 2 | 无标记真终稿漏判（boundary/queued/startup） | **采纳（高）**：boundary/queued 用最近帧内容认领；startup recovered/无流记忆路径**如实登记为不挂页脚**，P4 日志复核 | §6.10.2 第 5 条 + §6.10.8 边界 |
| A2 | 3 | §6.2 七条变异没打分类器 | **采纳** | §6.10.7 新增 2e/2f/2g/2h/2i（12 条） |
| A2 | 4 | 非回合只关 footer、旧面板泄漏 | **采纳** | §6.10.3 三项全关 + 测试预置 stale panel/header |
| A2 | 5 | 调用点漏 `4039`、`3271` status 未定、8 个 `_ld_cardview` caller 未列 | **采纳** | §6.10.5 |
| A2 | 6 | 测试红/绿性质与漏点（5306/5312/6562/6566/probe_render） | **采纳** | §6.10.6 |
| A2 | 7 | 锚点精确文本与 V073 唯一性 | **采纳** | §6.10.7（5 条重写 + 12 条逐字锚点 + post-grep） |
| B2 | 1 | 非回合 panel/header 泄漏 | **采纳（高）** | §6.10.3（与 A2-4 合并） |
| B2 | 2 | send 真终稿 status 未定义（丢 ✅ / 预览假 ✅） | **采纳（高）** | §6.10.2 status 段 + `status_locked` + 新用例 |
| B2 | 3 | 收尾 ✅ 依赖 hook 时序 | **采纳（高）** | §6.10.4 `default_status` + 显式 turn_status + 竞态断言 |
| B2 | 4/5 | Error 可读性无回退；host×theme 探针不足 | **采纳** | §6.10.10（3 host × 3 theme、长栈 Error、二值判读 + 单 host/单块回退） |
| B2 | 6 | README/plugin.yaml 缺 x-small/非回合口径 | **采纳** | §6.10.11 |
| B2 | 7 | interim/后台/媒体口径与 queued final | **采纳**：interim/后台/媒体无页脚**明写**；queued 有流记忆则认领、无则登记边界 | §6.10.2 边界 + README |
| B2 | 8 | 调用点漏 4039 | **采纳** | §6.10.5 |
| B2 | 9 | 措辞强于证据 | **采纳** | §6.10.11 条件化表述 |
| C2 | 1 | V073-2a 前置必须完整 | **采纳** | §6.10.6/§6.10.7：完成快照 + footer/show_model/model_aliases/context_max_override；turn 侧断言 |
| C2 | 2 | 漏 5306/5312/6562/6566 | **采纳** | §6.10.6 |
| C2 | 3 | preflight 实际断 5 条、其余是窗口失效 | **采纳**：实测 5 条（G2-3/V4-7/V4-10/V4-17B/V4-57）；窗口失效不穷举 | §6.10.7 |
| C2 | 4 | `--allow-at` 不能硬编码 adea7cb | **采纳** | §6.10.9（保留动态 `_fa`） |
| C2 | 5 | 计数 511/523 而非 506/518 | **采纳** | §6.10.7/§6.10.9（断言两数分开） |
| C2 | 6 | merge/release 门禁名不副实 | **采纳** | §6.10.9 追加 tree_dirty/tree hash/verdict/at 检查；`_is_full_run` 加 `not args.shard` |
| C2 | 7 | 发布顺序缺口（notes/plugin version/--check） | **采纳** | §6.10.9 步骤 0 + `--check` 硬失败 |
| C2 | 8 | golden 不扩 Error 更稳、item2 无机械门禁 | **采纳** | §6.10.8（保留场景 + 单测/check_cardview 双覆盖 + footer 叶断言） |
| C2 | 9 | helper_fp 强制全量、分片需 `--update-ledger` | **采纳** | §6.10.7 末条 |
| C2 | 10 | 计划残留/不一致 | **采纳** | 本条 + §6.10 全节；§6.1-§6.3 标注为被取代的审计轨迹 |




### 6.12 第四轮复核（A3/B3/C3）→ 定版 **Design D**（默认回合 + 已知系统提示负清单）

> 第三轮沙箱把 C 的两条高阻断修掉后又过了一轮三路只读复核；三路一致证明
> 「默认非回合 + 命令打点/最近帧认领」仍不可发布（细节见 §6.13）。定版改成更简单的 D。

#### 6.12.1 为什么 C 被否

* ⚠️ 以下命令打点/最近帧机制**已在 Design D 删除**（保留作审计轨迹）：
  命令打点按 chat 留 60s：skill/alias 等「fall-through 真回合」会被强制非回合（丢 ✅/面板），
  hooks 接线本身也没有测试/变异覆盖（C3-1）；inline 命令与非命令控制回复边界不可靠。
* 默认非回合让**非 native 真终稿**（无 notify/expect_edits、无本地流记忆）静默丢 ✅；现有
  6 类 `/stop` 回落用例全断（B3-2/3/6）。「最近帧认领」又引入生产者未覆盖（C3-2）、sanitize
  失配（A3-5）、split 尾段失配（A3-8）三处假绿。
* `guarded+expect_edits` 预览被当成终稿（A3-8/B3-1）、`edit_message` 收尾竞态（A3-1/B3-2）、
  legacy/DEGRADE 收尾（A3-6/B3-3）等在 C 里只修了一半。
* 结论：判「非回合」应**只靠「这条内容是不是已知系统提示」**，其余一律按回合。

#### 6.12.2 Design D 定版规则

1. `_ld_send_is_turn(chat_id, content, metadata, guarded)`（名字保留）：
   * `metadata["_interim_send"] is True` ⇒ 非回合（中途播报）；
   * `_ld_is_system_notice(content)` 命中已知前缀 ⇒ 非回合；
   * **其余一律回合**（默认回合）—— 非 native 真终稿、boundary/queued 回落、命令/控制回复
     （带 notify）保持现状，真实回合状态词一个字不动。
2. `_ld_is_system_notice` 负清单（行首匹配；来源 = `gateway/run_notifications.py`、
   `run_shutdown.py` 的实际文案；新增系统提示先登记再靠 P4 日志复核）：
   `♻ Gateway` / `⚠ Gateway` / `⚠ Session database` / `✅ Hermes update` / `❌ Hermes update` /
   `⚠ Cron job` / `✅ Background task` / `❌ Background task` / `[Background process` /
   `[IMPORTANT:` / `⏳ Gateway` / `⚕ **Update needs your input:**` / `◐ Session reset` /
   `⚠ Agent session` / `⚠ Context compression aborted` / `ℹ Configured compression` /
   `ℹ Context compression deferred` / `📬 No home channel` / `⚠ Subagent` /
   `⏳ Goal` / `⏸ Goal` / `🚫 Goal` / `✓ Goal`；匹配前先剥 VS16（U+FE0F），
   真实上游两种写法（`♻ Gateway` / `♻️ Gateway`）都收。
3. 回合 status 显式化：
   * `send()`：`expect_edits and not notify` ⇒ `processing` + `status_locked=True`；其余 ⇒
     `_ld_view_status(chat_id, default="completed")`（error/stopped 照常保留）；
   * `edit_message()`：`turn_status = _ld_view_status(default="completed") if finalize else "processing"`，
     同时传给 `_ld_render_card(status=..., status_locked=not finalize)` 与
     `_ld_frame_footer({..., "status": turn_status})`（显式状态，不再依赖 `default_status`）；
   * native/legacy/patch/degrade 帧：入口解析 `frame_status`（finalize 缺省 completed）；结构化
     路径 `state = {**state, "status": status}`，所有 footer 调用带显式状态。
4. `_ld_footer` 仍是纯格式器（显式 status，缺省 `turn_card=False` ⇒ None）；非回合静态卡强制
   `panel_enabled/header_enabled/footer_enabled=False`、`footer=None`（CardKit seed 例外保留槽位）。
5. 非回合卡 `_ld_track(..., turn_card=False)`；`_ld_redraw_stopped` 的 `_ld_state` 回落候选只取
   `turn_card is not False` 的卡（`/stop` 不再把系统提示涂成 ⛔）。
6. **不做** per-chat 命令标记、不新增共享状态；判定日志 `_LD_TURN_DECISION_LOGGED` 有 256 上限。

#### 6.12.3 锚点与计数（沙箱实测）

* 旧锚点重写 **8 条**：G2-3 / V4-7 / V4-10 / V4-17B / V4-57 / **SEQ3 / R3-7 / Y20**
  （后三条由帧 footer 显式状态改动触发）。
* 新变异 **12 条** V073（定版基础；D1/D2/D3 收口再增 9 条，P1 第二轮再增 3 条，最终 24 条见 §6.15）：
  1a/1b/1c（x-small 三宿主）、2a（快照兜底加回）、2b（非回合早退失效）、
  2c（回合侧误杀）、2d（空 footer 元素）、2e（负清单失效）、2f（默认改回非回合）、2i（interim 不排除）、
  2j（预览状态不锁）、2o（非回合标成回合）。全部 `test_units` red-assert。
* 计数（定版后、P1 第二轮审计前）：499 + 21 = **520 变异**；preflight = **532/532**；
  最终收口改为 **523 变异 / 535 锚点 / 523 条全量**（见 §6.15）。

#### 6.12.4 测试与夹具

* 新增/强化：`test_v073_detail_rows_x_small_error_falls_back_to_notation`、
  `test_v073_footer_turn_scope_and_status_is_explicit`（含收尾 default_status 竞态）、
  `test_v073_non_turn_send_has_no_footer_element`（两条真实系统提示静默 + 默认回合 ✅ +
  expect_edits 无 ✅ + interim 静默 + `turn_card=False` 标记）、
  `test_v073_edit_message_finalize_keeps_status_and_preview_never_completes`。
* 既有同步：`1533/1537、2241/2245、2321/2325、5147-5209、5229、5238-5267、5306/5312、6860/6901、
  10914/10918` 显式 `turn_card`/status；`check_hooks.py:772`；`probe_render.py:228/994`（P1 一起改）；
  markdown 卫生三处「精确相等」断言改为「正文在 / 原文不在」（footer 元素现在可能带状态词，
  legacy 路径不保证 `element_id`）。
* **新断言必须 crash-safe**（第四轮实测）：新用例最初用 `line[1]["text_size"]` 直取；`V4-46`
  变异把工具行改成元素级 `div.icon` 后结构变化 ⇒ KeyError，门禁记 `💥 只有崩溃` 而非 red-assert。
  已改为「长度断言 + `.get()` 取值」（test_units 与 check_cardview 两处），复跑 `-k V4-46` 为
  red-assert；后续新断言一律遵守。
* 夹具：item1 = **8 个顶层 JSON 叶变化**（= 9 处 detail 元素；实体/终稿字符串内各含 1/2 处），
  footer 叶 `✅ 已完成 · Test Model` 两侧都在（item2 = 0）；不扩 Error 场景（单测 + check_cardview
  双覆盖）；helper_fp 变 ⇒ 全量必须。

#### 6.12.5 沙箱验证（定版 + D 收口证据）

干净 clone + 脚本：`run_fast --full` **8/8**、`test_units` **≥294/294**、
`--preflight` **535/535**（终态 543/543，见 §6.17）、`-k V073` **24 条全 red-assert（test_units；终态 32 条）**、8 条重写锚点 `-k` 全 red、
golden 顶层 8 叶变化且 footer 叶不变；新增 `/stop` 行为断言与 structured/legacy 收尾状态注入用例。
（D1/D2/D3 收口后的精确数字以 §6.15 为准，P3 全量按最终 523 条跑。）

#### 6.12.6 残余边界（写 README/verify-log）

* 未登记前缀的新系统提示仍按回合出页脚 ⇒ P4 `send 判定 turn=` 日志复核；漏网即补清单
  （补清单必须重跑 P3，不许口头放过）。
* 命令/控制回复带 `notify` ⇒ 保留页脚/状态词（**有意**；用户只要求系统提示干净）。
* 长回合 split 封旧卡仍写 completed（v0.7.2 既有行为）⇒ 登记 v0.7.4，不混入本批。
* 媒体 caption 走 `send_image/send_document`，旁路 `send()`，无卡片状态词变化。

### 6.13 第四轮三路逐条处置（A3 `b0be1216` · B3 `112dd816` · C3 `2af08e7a`）

| 路 | 编号 | 摘要 | 处置 | 落点 |
| --- | --- | --- | --- | --- |
| A3 | 1/2/3 | edit 收尾竞态 / 预览泄漏 / legacy+degrade 收尾 | **采纳** | §6.12.2 条 3 |
| A3 | 4/5/6 | 命令标记误杀 / 调用点漏 4039 等 / 测试漏点 | **采纳**：命令标记整段删除；调用点表 + probe_render 同步 | §6.12.1/§6.12.2/§6.12.4 |
| A3 | 7/8 | startup 无记忆终稿 / guarded 早判 | **采纳**：默认回合 ⇒ 无记忆终稿也保 ✅；guarded 分支删除 | §6.12.2 条 1 |
| A3 | 9/10/14 | recent 规则宽 / 模块状态裂脑 / 日志无界 | **采纳**：recent 机制删除；日志 256 上限 | §6.12.2 条 6 |
| A3 | 11/12/13 | check_cardview/probe_render 漏 / 注释 B / 计数 | **采纳** | §6.12.3/§6.12.4（check_cardview 留 P2） |
| B3 | 1/2/3 | guarded 预览 / edit 竞态 / legacy 收尾 | **采纳** | §6.12.2 条 1/3 |
| B3 | 4/5/6 | 命令 fall-through / 非回合被追踪 / 封旧卡 completed | **采纳 4/5；6 登记 v0.7.4** | §6.12.2 条 5 + §7 |
| B3 | 7/8 | 探针未落仓无回退 / split 后缀失配 | **采纳**：探针 P2 落仓跑；后缀失配随 recent 机制删除而消失 | §6.12.4 + 探针清单 |
| B3 | 9/10 | 文档/版本未落 / 主题名义化 | **采纳** | P1/P6 + §6.10.10 修正为「切客户端主题重跑并写 verify-log」 |
| C3 | 1/2/3 | hooks 接线假绿 / recent 生产者假绿 / guarded 假绿 | **采纳**：三处机制删除，问题随代码消失 | §6.12.2 |
| C3 | 4/5 | 计数不一致 / `_is_full_run` 漏 shard | **采纳**：最终 523/535（见 §6.15）；`_is_full_run` 加 `if getattr(args,"shard",""): return False`（测试 SimpleNamespace 补 shard=""） | §6.12.3 + P1 |
| C3 | 6/7/8 | golden 口径 / check_cardview / probe_render | **采纳**：8 叶=9 元素；后两者 P1/P2 | §6.12.4 |
| C3 | 9/10 | release tree 比对对象 / 脚本缺口 | **采纳**：比对 `git rev-parse <full_audit_at>^{tree}`；notes+版本步骤 0、`--check` 硬失败、120→150、版本 `lstrip("v")` | §6.10.9 修正 |
| C3 | 11/12 | merge 硬编码 REPO / 变异未钉 status/header/default | **采纳（D2 复核升级）**：文档写明只在生产仓跑；永久变异补 2p（/stop 过滤行为）、2q（status_locked）、2r（default_status）、2s/2t（非回合 panel/header）、2u/2v/2w（帧状态显式注入）、2x（VS16 归一），见 §6.14 | §6.12.3 + §6.14 |


---

### 6.14 D1/D2/D3 复核收口（2026-09-22 深夜）

> D1 `d3e888d0` / D2 `5bb9d5af` / D3 `6eab7167` 三路只读对抗复核（沙箱副本手工变异 + 全门禁）。
> 本节是 §6.10-§6.13 之后**唯一生效**的收口口径；与前面历史内容冲突时以本节为准。

**已采纳的修复**

1. 负清单补齐真实上游文案并**匹配前剥 VS16**：新增 `⏳ Gateway`、`⚕ **Update needs your input:**`、
   `◐ Session reset`、`⚠ Agent session`、`[IMPORTANT:`、Goal 系列等；删除来源不明的裸 emoji
   `🏁`/`📣`/`🔔`/`⚠ Hermes`。新用例逐条断言真实上游字面量（免 VS16/带 VS16 两种写法都覆盖），
   并新增 `V073-2x`（去掉 VS16 归一会让 `♻️ Gateway` 漏网）。
2. `/stop` 过滤成立但原先无行为断言（D3 手工变异六门禁全绿）⇒ 系统提示卡断言
   `_ld_redraw_stopped` 返回 False 且**零 patch**，真回合卡对照必须重绘 1 次；新增永久变异 `V073-2p`。
3. C3-11/12 声称的永久变异补齐：`2q`（预览 `status_locked`）、`2r`（`_ld_frame_footer`
   `default_status`）、`2s`/`2t`（非回合 panel/header）、`2u`（结构化收尾状态解析）、
   `2v`（legacy `frame_status` 注入）、`2w`（edit_message footer 显式 status）、`2x`（VS16 归一）
   ——共 9 条新变异。
4. 新增两条硬字面量用例：`test_v073_structured_finalize_injects_frame_status`、
   `test_v073_legacy_finalize_injects_frame_status`（panel 快照为空时收尾仍必须有 `✅ 已完成`）。
5. `edit_message` 的 `default_status` 是冗余参数（D3 低项）：已从该调用点删除，
   `default_status` 仅保留给 DEGRADE 收尾调用点（`V073-2r` 钉住）。
6. 探针 `tests/probe_text_size_hosts.py`：任一张 `code!=0` ⇒ 退出码非 0；新增 N1 已知系统提示卡
   （本地断言无 header/panel/footer）与 N2 真实回合卡（断言必须有 `✅ 已完成`）；发送前提示记录
   客户端主题/设备。
7. 文档：README「已知限制」新增默认回合 + 负清单口径（未登记新提示仍会出 ✅，P4 日志复核）；
   release notes 同口径；本计划 §6.10/§6.13 保留为历史审计轨迹，实现/发布口径统一到 §6.12 + §6.14。

**最终计数（P1 落地后以 `--preflight` 实测为准）**

* §6.14 收口时 `MUTATIONS` = 499 + 21 = **520**；随后 P1 第二轮审计（A 路）再补 3 条 ⇒ **523**；
  对照 12 ⇒ preflight **535/535**；账本/merge **523/523**（§6.15 实测）；
* 24 条 V073 = 1a/1b/1c + 2a…2f,2i,2j,2o…2x + **2y/2z/2aa**；全部 `test_units` red-assert（终态 32 条，见 §6.17）；
* golden 顶层 8 叶变化（9 处 detail 元素）+ footer 叶不变；P3 必须用最终 523 条重跑 6 分片。

---

### 6.15 P1 第二轮审计与 P3 全量实测（最终生效）

**P1 第二轮审计（commit `65c1c5f`）处置**

* A 路高/中：`edit_message` 尊重 tracked `turn_card`（心跳不再装饰静默卡）、`_ld_track` 只清回合卡
  `last_text`（turn→notice→/stop 仍可重绘）、负清单补 `run_busy.py` 六条忙碌提示、footer 元素写/degrade
  显式传 status（删除隐藏 `state["status"]` 注入）、legacy element-channel 注入 `frame_status` —— 全部已修，
  新增永久变异 `V073-2y` / `2z` / `2aa`；
* B 路高：`docs/verify-log.md` 补齐宿主矩阵证据（11 张卡全 `code=0`；真机目视待用户回话），
  README/plugin.yaml/cards 注释去除「未过宿主自动退回 notation」的暗示（写明无运行时自动回退）；
* C 路：release 脚本 `.deploy` dirty 改为 `--check` 也硬失败，补 CHANGELOG 收口断言；
  本计划旧计数在 commit D 收口到 **523 变异 / 535 锚点 / 24 条 V073**；发布终态为 **531 变异 / 543 锚点 / 32 条 V073**（§6.17）。

**P3 六分片全量（冻结提交 `db58dc5`；`65c1c5f` 为回退前一轮）**

* 6 分片并行、独立账本/日志、`--update-ledger`，坏 0（无 💥/🟢/❓）；合并
  `tools/merge_ledger4.py --write`：**523/523 red-assert**、`full_audit_at=db58dc5`、
  `full_audit_tree=b15070a29fe84247849f3ce0ee6b0d5e503a1dbb`（== `db58dc5^{tree}`）、
  `tree_dirty=false`、继承 0；
* **实测墙钟 1443.9s**（09:16:00→09:40:04）；`--preflight 535/535`；`-k V073` 24/24 red；
* 证据：`~/.larkdeck-scratch/v0.7.3/full-run-evidence.json`、`seed{1..6}.json`、`shard{1..6}.log`、
  `docs/audits/v0.7.3/README.md`；
* 发布机制（生效）：`tree_dirty is False`、`full_audit_tree == git rev-parse <fa>^{tree}`、
  每条 `verdict=red-assert` 且 `at=fa`、`len(entries)==len(MUTATIONS)`、`.deploy==HEAD`（`--check`
  同样硬失败）、150s 有界自检。

---

### 6.16 P4 真机验证与 Error 块形态③（markdown + 逐行 inline code + x-small）

* 系统提示对照（用户截图确认）：1/3 旧版卡（`6fd68f3` 提取代码）有状态头+面板+页脚 `✅ 已完成`；2/3 新版系统提示卡无状态头/面板/页脚/✅；3/3 新版真实回合卡保留 `✅ 已完成`。目标 ② 成立。
* x-small 宿主矩阵（用户目视 + 截图行高测量）：A `markdown` 23→19px、B `div.text=plain_text` 21→17px ⇒ 确实更小；C `div.text=lark_md` 26→26px（行距同为 44px） ⇒ 客户端忽略 `text_size`。
* 处置（最终）：`div.text=lark_md` 与 fenced 代码块都确认客户端固定/忽略字号；按用户截图
  选定 **形态③ = `markdown` + 逐行 inline code + `x-small`**（候选卡 `om_x100b6400934d8900c16c222e85b90e7`，用户确认「明显更小且可读」）⇒ commit `a2290da`，
  `**Error**` 标签与每行代码一起变小，长行可折行、不再依赖 fenced 代码块。
* 证据：`docs/audits/v0.7.3/p4-error-host-switch.md`、`docs/audits/v0.7.3/p4-real-device-fallback.md`
  （历史回退轮）、`docs/verify-log.md` 的 2026-09-23 宿主矩阵/P4 条目。

---

### 6.17 Error 形态③ 的 P3 重验 + 长任务空白面板登记

**P3 六分片（冻结提交 `a2290da`，Error 块形态③ inline code）**

* 6 分片并行、独立账本/日志、坏 0；v2 runner 先验片后合并、不带旧 fa `--allow-at`：
  **531/531 red-assert**、`full_audit_at=a2290da`、`full_audit_tree=869a68f64fa4e2b2d6572a751767afc926da113b`
  （== `a2290da^{tree}`）、`tree_dirty=false`、继承 0；
* **实测墙钟 1753.4s**（14:25:57→14:55:11）；`--preflight 543/543`；`-k V073` 32/32 red；
* 证据：`~/.larkdeck-scratch/v0.7.3/evidence-a2290da/`（6 log + 6 seed + 6 inventory + sha256）、
  `full-run-evidence.json`、`docs/audits/v0.7.3/p3d-inline-code.md`。

**长任务/多卡时中间卡执行详情面板空白（登记 v0.7.4）**

* 用户 2026-09-23 反馈：长任务最终收到两张答案卡 + Working 卡，第一张卡的面板展开空白；
  用户确认只在长任务/多卡时出现。证据与消息 id 见 `docs/audits/v0.7.3/p4-long-task-blank-panel.md`。
* 初步判定：原生流回退/多回合交错时最终整卡拿到的 panel 快照可能已被后续回合顶掉，
  `_ld_cardview` 仍按状态色载体保留空面板；与本批 x-small / 系统提示改动无直接因果。
* 处置：**登记 v0.7.4**（复现 + 「无过程数据时沿用本回合最后一次非空面板或不出面板」二选一），
  本批不修、不阻塞发布；`README` 已知限制同步写明。

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
| 长任务/多卡时中间卡执行详情面板空白（原生流回退/多回合交错） | **defer v0.7.4** | 2026-09-23 用户反馈（消息 id/日志见 `docs/audits/v0.7.3/p4-long-task-blank-panel.md`）；初步判定与本批无因果；复现 + 「无过程数据时沿用本回合最后一次非空面板或不出面板」二选一 |

**收尾纪律**：以上「defer v0.7.4」是**书面承诺**，v0.7.4 计划必须先复核本表；关闭项若再被用户点名，
重新立项而不是翻旧账。
