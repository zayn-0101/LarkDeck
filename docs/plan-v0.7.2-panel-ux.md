# v0.7.2 批次 3 计划 v2：面板 UX 定版（展开策略 / 运行态动图 / 页脚纯文本）

> **决策来源**：2026-09-22 用户逐条拍板（A1 / B1 / C1 / D1′ / E1 + 文案逐字确认 + 两个默认项）。
> **本文件是本批唯一执行依据**。v1 已过**三路**对抗审计（A 技术可行性 / B 证据与省时 / C 文档流程），
> 本版把三路的高/中项全部折进来（§7 有逐条处置与"未采纳及理由"）。
> 老规矩：**每阶段结束 ≥3 路子代理对抗审计 → 讨论收敛（不投票）→ 才进下一步**；收敛后才 `commit`（本地）；
> 用户真机确认后才 `push + tag + release`。

## 0. 决策记录（逐字冻结；执行中不许悄悄改）

| 编号 | 决定 | 落点 |
| --- | --- | --- |
| **A1** | 面板**运行中展开、收尾折叠**；嵌套推理轮**当前轮展开、已结束轮折叠**；**中间帧不带 `expanded`**（仅 CardKit partial 车道，见 §5-R1）；`panel_expanded` 语义不变（false=收尾折叠 / true=收尾也展开）；新增 `streaming_panel_expanded`（默认 `true`；`false` = 回到全程折叠） | `core/{adapter,panel,cardview}.py`、`plugin.yaml` |
| **B1** | 面板标题行**保留 emoji**；页脚**去段前缀 emoji**（`⏱`/`🤖` + `footer_metrics` 的 `⚡`/`🔁`/`🐢`）改纯文本。⚠️ **状态词前缀 `✅`/`❌`/`⛔` 保留** —— B1 的逐字表里就有、用户确认过；审计 C 指出"去 emoji"这句话本身自相矛盾，故在此定口径为"**只去段前缀**" | `core/cards.py::footer_line`（**不改** `core/i18n.py` 的状态词） |
| **C1** | 工具行运行中状态色 → **`blue`**（**两张表同改**：`cardview.ToolStepView.status_style` + `cards._TOOL_STATUS_STYLES`） | `core/cardview.py`、`core/cards.py` |
| **D1′** | **自制 GIF**：同时用于 ① 工具行「运行中」图标 ② 正文前加载指示；源文件 + 生成脚本入库；用**我们自己的 app** 上传一次拿 `img_key`。**D1′ 于 2026-09-22 推翻** `docs/plan-v0.7.2.md` §0.2 的"不做上传、直接用共享 key"（旧 `loading-asset.md` 作历史留存，新增 `loading-asset-v2` 记录上传响应与 key） | `assets/`、`tools/`、`core/cardview.py` |
| **E1** | 保持「正文在上、面板在下」 | 不动 |
| 默认① | 运行中**保留** `Running` 词（配蓝色 + 动图） | 同 C1 |
| 默认② | 失败/超时/中止/跳过**保留词**（红/灰）；只有**成功**换成绿色 `✓` | 同 C1 |

### 0.1 逐字文案（真机可见；改一个字都要重跑全量验证）

| 场景 | 图标 | content（逐字） |
| --- | --- | --- |
| 工具行 · 运行中 | **自制动图**（`custom_icon`；`markdown.icon` **不带 `size`**） | `**terminal** · <font color='blue'>Running</font>` |
| 工具行 · 成功 | 灰色工具线性图标 | `**terminal** (25 ms) · <font color='green'>✓</font>` |
| 工具行 · 失败 / 超时 / 中止 / 跳过 | 灰色工具线性图标 | `Failed`（红）/ `Timed out`（红）/ `Cancelled`（灰）/ `Skipped`（灰）— 保留词 |
| 面板标题行 | — | 不变：`💭 思考 1.6s · 🛠️ 工具执行 · 5 步` |
| 页脚（基础） | — | `✅ 已完成 · 12.3s · DeepSeek V4.1 Flash · ctx 55.6k/1m · 5%`（**无 ⏱/🤖**） |
| 页脚（`footer_metrics: basic`） | — | 追加 `· cache 75% · api 7` |
| 页脚（`footer_metrics: full`） | — | 追加 `· cache 75% · api 7 · ttfb 0.4s` |
| 工具详情行 / 错误块 | `tool-indent_outlined` / `warning_outlined` | 不变 |
| 加载指示 | **同一张自制动图** | `div` + `custom_icon` + `text:" "`（形状不变，只换资产） |

## 1. 目标 / 非目标

**目标**：A1 + B1 + C1 + D1′ + 默认①② 落到生产代码、**硬字面量断言**、变异、夹具、文档，并做一次全量重验；顺手修掉审计 B 发现的**盖章漏洞**（ §2 mutate_check 行 / §5-R13）。

**非目标（登记 v0.7.3）**：B4 页脚多列图标（`plan-v0.7.2.md` §3 已登记）｜D5 自绘字形动画｜E2 过程/正文交错结构｜嵌套 `element_id` 单独 `partial_update` 探针｜**patch/降级整卡车道的 `expanded` 省略**（§5-R1）｜APFS COW 快照提速（审计 B 建议③）｜P4 澄清卡三态｜清场。

## 2. 变更清单（逐文件、逐改动点；来源=三路审计核对结果）

| 文件 | 改动 | 关键约束 |
| --- | --- | --- |
| `core/panel.py` | ① `_open_round_locked` 建轮加 `finalized=False`；② `_finalize_round_locked` 里 `current["finalized"]=True`；③ `snapshot()` 透出 `"finalized": bool(item.get("finalized"))` | ⚠️ `adapter.py:3676` **已经在读** `item.get("finalized")`（现在永远是 `False`）⇒ 这是**数据缺失**，不是新需求；只加字段，不改既有键 |
| `core/cardview.py` | ① 新增 `SPINNER_TOOL_IMG_KEY`（我们的 key；沿用 `spinner_img_key()` 名字与"空 key 回落 `standard_icon`"语义，**别改名** — 变异 V4-42 锚点）；② 运行中工具行走 `custom_icon`（`markdown.icon`，**不带 `size`**）；③ `status_style`：`running→("Running","blue")`、`ok/success→("✓","green")`；④ `panel_partial()` **固定省略 `expanded`**（不加可选参数）；⑤ `reasoning_panel(..., expanded: bool)` 入参；⑥ `panel_shell()` **保留** `expanded`（seed/收尾整卡用） | `_tool_title_div` 的 `markdown` return 原文尽量保持（变异 V4-46 锚点）；运行中分支单独加断言 |
| `core/cards.py` | ① `_TOOL_STATUS_STYLES` 同步 `("Running","blue")` / `("✓","green")`（与 cardview 同表，`test_units.py:12238` 强制相等）；② `footer_line` 去段前缀 + `cache/api/ttfb` 改纯文本；③ **不动** `_THEME_SYMBOLS` 键集（它还是 `theme_name` 白名单与 `tool_step` 图标选择） | `footer_line` 生产唯一调用点是 `adapter.py:2230` ⇒ 不会误伤面板标题（v1 那句写错了，已删） |
| `core/i18n.py` | **不改**（状态词 ✅/❌/⛔ 保留，见 §0-B1 口径） | 防"顺手去 emoji"越界 |
| `core/adapter.py` | ① `_DEFAULTS["streaming_panel_expanded"]=True`；② `_ld_cardview` **默认仍是终态语义**（`panel_expanded`）；③ **seed 两处**（seed 建卡 `:3888`、封卡切新卡 `:3255`）构 view 后显式 `view.panel.expanded = _cfg("streaming_panel_expanded")`；④ **所有** `panel_partial` 调用点（心跳 `:3604`、帧 `:3981`、seed 签名 `:3909`、封卡新卡签名 `:3290`）统一走省略语义；⑤ `ck_panel_sig` 与真正发出的 partial 同源 | 收尾整卡 / `/stop` / 静态 send / `_ld_note_text` / `_ld_render_card` 保持 `panel_expanded`（不动） |
| `plugin.yaml` | 新增 `streaming_panel_expanded`（boolean，默认 `true`）+ `panel_expanded` 描述改"收尾时是否展开"；顺手修 `:86`（工具行 emoji 内联旧描述）、`:118`（"青绿 Running"） | 配置 schema 三方一致（`tests/test_units.py:7834-7861`） |
| `README.md` | 样例加 `streaming_panel_expanded: true`（**恰好 8 空格缩进**，`test_units.py:7842` 硬要求）；功能表更新；修 `:220`（"+ 本卡短码"）、`:456-459`（旧描述） | |
| `assets/spinner-tool.gif` + `tools/make_spinner_gif.py` + `tools/upload_card_asset.py` | 资产真源 + 生成脚本 + 一次性上传脚本 | **不写进 `install.sh` FILES**（审计 C 核实 `install.sh:29-30` 只收 `*.py`/`plugin.yaml` 且排除 `./tools/*`；`assets/*.gif` 不入选） |
| `tests/test_units.py` | ① 钉新默认（`streaming_panel_expanded is True`）；② 改 `turquoise`/`Succeeded` 字面量；③ 新增**硬字面量**用例：seed entity `panel["expanded"] is True`、收尾整卡 `is False`、每个 panel partial `"expanded" not in`、运行中行 icon 精确等于 `{"tag":"custom_icon","img_key":SPINNER_TOOL_IMG_KEY}` 且无 `size`、成功行逐字、页脚三条逐字、`finalized` 两态；④ **2×2**（`panel_expanded` × `streaming_panel_expanded`）组合用例；⑤ `_full` 判据函数化后的两条用例（`target_only ⇒ 不盖章`） | 用例名只加不改（避免 400+ 条变异作废） |
| `tests/check_cardview.py` | ① `_check_icon`：`custom_icon` **必须**有 `img_key`；按宿主分档（`div.icon` 允许 `size`、`markdown.icon` 不允许）；② 夹具**加一条运行中工具行**（现在只有 ok/error，running 分支不过白名单）；③ 颜色/✓ 字面量更新；④ 直断言 `cardview.ToolStepView.status_style`（防两表一起回退绕过同表用例） | |
| `tests/check_hooks.py` | 黄金路径 `Succeeded` → `✓`；面板 partial 的 `expanded` 时机断言 | |
| `tests/mutate_check.py` | ① **修盖章漏洞**：`_full` 增加 `and not args.target_only`（并把判据抽成可测函数 `_is_full_run(args, picked, bad)`）；② 新增变异（§3-P4 列出 ID/文件/锚点/目标门禁）；③ `V4-46` 锚点若因重构失效需同提交重对齐 | 这是审计 B 的**高**发现，必须修 |
| `tests/golden_cardkit_trace.json` | 重生成（diff = 行为声明，逐条解释） | **helper 指纹变化 ⇒ 全量重验不可省** |
| `docs/`（`plan-v0.7.2.md` / `release-checklist.md` / `release-evidence.md` / `releases/v0.7.2.md` / `verify-log.md` / `handoff-route.md` / `AGENTS.md` / `CHANGELOG.md`） | 见 §5-R5 的同步清单（含**标 superseded**）；新增 `loading-asset-v2` | **历史行不改，只追加**；`docs/audits/v0.7.2/{tool-icons,footer-contract}.json` 属 `_HELPER_FILES` ⇒ 若改等同改夹具 |
| `~/.larkdeck-scratch/release-v0.7.2.py` | ① 旧数字 → 本批实测；② `TAG_MSG` 补 A1/B1/C1/D1′/E1；③ `run_fast --full` 校验**恰好 8 个 `[OK]` 且名字集合已知**；④ **新增 `_meta.full_audit_at == HEAD` + `n_assert==len(MUTATIONS)` + `n_inh==0` 校验**；⑤ `merge_ledger4` 依赖改硬失败（脚本固化进 `tools/` 或固定路径+sha） | 发布前 `--check` 必须覆盖以上 |

## 3. 阶段与出口判据（每阶段：**3 路**对抗审计 → 讨论收敛 → 才进下一步）

| 阶段 | 内容 | 出口判据（硬、可机械核对） | 审计方向 |
| --- | --- | --- | --- |
| **P0** | 决策冻结（§0） | 用户已逐条拍板 | — |
| **P1** | **计划审计** | 3 路跑完；每条发现给出处置；**未采纳必须写理由**；收敛判据 = GO / GO-WITH-CHANGES / NO-GO | A/B/C（**三路均已回**，见 §7） |
| **P2** | 资产：生成 → **挑图探针卡** → 用户选定 → 上传 → key 常量 | `assets/spinner-tool.gif` 入库；上传 `code==0` 且**不是**复用旧 key；`image_key` 写进常量；生成脚本可复跑；真机"会动"归 P6 | 归属/可复现/GIF 格式（二值透明、纯色实心、自建调色板） |
| **P3** | 生产代码（§2 前 6 行） | `/Users/Zayn/.hermes/hermes-agent/venv/bin/python3 -m py_compile <改动文件>`（**指明解释器**）；`-k` 定向用例绿；**六支门禁绿**（`--preflight` **不算**绿灯）；`install.sh --copy` 真跑（`HERMES_HOME=/tmp/ldinstall`） | 判别力 + 协议不破坏（`expanded` 省略、`ck_panel_sig` 同源、白名单分档） |
| **P4** | 断言 / 变异 / 夹具 | 每条新变异 `-k`（**完整模式**，见 §4）**实红**（贴输出）；§0.1 + §2 test_units③ 的**硬字面量断言**全部就位；`write_golden_trace.py --check` 一致；**夹具 diff 逐条解释**（A1/C1/B1/D1′ 各是哪几处） | 变异是否真有判别力（**不许只靠夹具**）；锚点是否失效 |
| **P5** | 验证：六支 + `run_fast --full` + 全量矩阵 | `--ledger-status` = N/N 可跳过、待跑 0；**`_meta.full_audit_at == HEAD`**；`n_inh == 0`；日志 🔴 并集覆盖 + 12 名对照全绿（`merge_ledger4` 现场重算三指纹）；**记录实测墙钟秒数** | 证据链：三指纹 / 对照名 / `at` 口径 / 分片隔离 / 盖章漏洞已修 |
| **P6** | 部署 + 真机探针 | `.deploy` 指新提交；网关重启有界等待 `启动自检通过`；**一张多行探针卡**（§8，必须含 `show_reasoning=true` 的嵌套轮）+ **手动展开语义实验**；用户回话 | 探针是否覆盖全部新行为、可判读 |
| **P7** | 收尾 + 发布 | 顺序 = **收尾审计（3 路）→ 用户终验 → 门禁复核 → `release-v0.7.2.py --check` → `--go`**；文档/坐标同步（§5-R5）；本计划 §7 全部收敛 | 文档与证据一致性、发布完整性 |

## 4. 省时方案（**已按审计 B 修正**；含对既有冻结清单的显式作废）

> 审计 B 结论（`19ee94be`）：**`--target-only` 在现有矩阵上几乎没有提速空间** —— 默认模式本来就是
> "先跑声明的目标门禁、红了即停"（`mutate_check.py:3166-3185`），且账本 484 条的 `gate` 全部等于声明
> `expect`（450 test_units / 12 cardview / 11 override / 11 hooks）⇒ 完整模式每条本来也只跑 1 支；
> `--target-only` 只对"目标门禁抓不住、别的门禁抓得住"的绿变异有意义，而那类**不允许记账**
> （判绿不写账本）⇒ 让合并更容易 FAIL。**所以本批不用 `--target-only`。**

| 措施 | 说明 |
| --- | --- |
| **6 分片 + 完整模式（照常 first-red）** | 唯一诚实的提速来源是并行。审计 B 算式：每条 ≈5.0s（快照 0.1–0.3s + 1 支门禁加权均值 4.8s）；6 片 ≈81 条/片 ⇒ 单片 ≈7.5–8.5 min（轻载）；按 4 片实测 14 min 折算 **≈9–11 min**（有其它负载时 45s 硬超时可能复现上一轮 8 条 💥，最坏 15–20 min）⇒ **目标 ≤11 min，实测填回本表** |
| **确认/作废冻结清单** | `release-checklist.md:68`（"用普通模式，不要 `--target-only`"）→ **确认维持**（与 B 结论一致）；`:87-89`（"分片上限 2"）→ **作废**：改为"6 片（实测依据=本批墙钟秒数）；出现 💥 则回退 4/3 片并如实记录" |
| 失败回退 | 💥/❓ ⇒ 降到 4→3 片重跑缺口（同一套 `--allow-at` 合并），**如实记录**，不得掩饰 |
| 一次改完只跑一次全量 | 文案已逐字冻结（§0.1）；中途改文案 = 额外一次全量（记录在案） |
| 迭代期只跑 `-k` + `--preflight` | 秒级；**`--preflight` 不是绿灯**（`AGENTS.md:491-496`），提交前仍跑 `run_fast --full` |
| 夹具先 `--check` 再重生成 | diff 即行为声明 |
| 探针合并成一张卡 | §8；省用户往返 |
| 分片跑的同时写文档 / 备探针 | 互不写同一文件 |
| 审计 3 路并行 | 固定 3 路（用户重申"只高不低"），收敛靠讨论 |
| **更正 1**："docs 不触发指纹" | 只对**除 `_HELPER_FILES` 与 `check_*.py` 之外**的 docs 成立。`_HELPER_FILES` = `tests/write_golden_trace.py`、`tests/golden_cardkit_trace.json`、`docs/audits/v0.7.2/tool-icons.json`、`docs/audits/v0.7.2/footer-contract.json`；`check_override/check_hooks/check_cardview` 无 `def test_` ⇒ `_gate_cases` 退化整文件 sha，**改它们会让由该门禁抓住的条目 `gate_fp` 失效**（本批属全量的一部分） |
| **更正 2**："不新增写卡次数" | `finalized` 翻转会改 `elements` ⇒ `ck_panel_sig` 变 ⇒ **轮边界那一帧可能多一次面板写**（生产里标题耗时每帧也在变，通常合并）；P4 的"元素写次数不变"用例必须**冻结时钟、无其它变化**下测 |
| **不采用（审计 B 补充）** | ① `--target-only` 不分片 / `--shard 1/1` 盖章（漏洞 §2 已修）；② `--delta` 当全量（本轮 golden 变 ⇒ 484 全 todo，省不了）；③ `-k` 子集或只跑 `check_cardview` 就发布；④ 跳过 `run_fast --full`；⑤ 分片共用同一 `LARKDECK_LEDGER_PATH`（并发丢更新）；⑥ 脏树/边跑边改后合并；⑦ 手工把未跑/绿条目补成 fresh 或伪造日志；⑧ `--seed-inherited` 洗白；⑨ 删改 12 条对照或去掉 `--allow-at` 硬凑合并 |
| **合并命令（B 给出，dry-run 后加 `--write`）** | `merge_ledger4.py --head $(git rev-parse --short HEAD) --base tests/mutation-verdicts.json --fresh $D/seed{1..6}.json --log $D/shard{1..6}.log --allow-at f97ce19 --allow-at d4ad9ce` |

## 5. 风险与未知（含三路审计发现定级）

* **R1（高，A）整卡替换车道**：`expanded` 省略只在 CardKit partial 生效。**审计 A（P3）把范围核实得更宽**，以下路径**中途**仍会带 `expanded`：
  * native 失败回落 `send`/`edit_message` → `_ld_render_card`（用终态语义 `panel_expanded`；实测 `panel_expanded=true, streaming=false` 时中途整卡 outer/nested 都是展开的）；
  * `_ld_structured_degrade`（卡级死法 → 整卡 patch）；
  * `_ld_ck_split` 的**封旧卡**那一次整卡 patch（旧卡是终态，**这一条是有意的**）；
  * 非 native 的纯 `send/edit` 流式。
  **决定：本批只覆盖 CardKit partial 车道**（实现与门禁都在那一层），其余如实写进文档，彻底解决登记 v0.7.3。
* **R1b（低，A）内外层手动手势都不持久**（v0.7.3 登记）：`panel_elements` 每帧按 `finalized` 重写**内层**轮（`cardview.py:616`）⇒ 用户手动展开某个已结束轮，下一帧会被收回；当前轮手动收起同理（下一帧写回 `expanded=True`）。A1 的方向如此；本批只登记 + 在 P6 探针里让用户看到这一点（§8-7）。
* **R1c（低，A）回合结束后的迟到推理**（v0.7.3 登记）：`record_turn_end` 之后同回合若再来一段 `reasoning`（跨钩子无顺序保证），`_open_round_locked` 会再开一个 `finalized=False` 的新轮 ⇒ 终态卡上又出现一个展开轮。本批不修（需要钩子时序语义，登记）。
* **R1d（中，A）`native_transport: patch` 在 structured 下是死配置**（既有问题）：`_ld_stream_frame` 对 structured 直接进 CardKit 路径、不读 `_ld_transport()` ⇒ README「patch 可回退」不成立。本批**只改文档口径**（登记 v0.7.3 要么恢复真回退、要么删掉这个键）。
* **R2（高，A/C）双表同源**：`cardview.status_style` 与 `cards._TOOL_STATUS_STYLES` 同提交改；`check_cardview` **直接断言 cardview 侧**。
* **R3（高，A）P4 不许只靠夹具**：夹具只证明 `code==fixture`，不证明 `fixture==决策` ⇒ 必须落 §0.1 的硬字面量断言。
* **R4（高，C）提速与冻结清单冲突**：§4 已按 B 结论重写；发布脚本补 `full_audit_at == HEAD` 校验。
* **R5（高，C）登记文件与新计划矛盾**（同步清单，只追加/标 superseded）：`docs/plan-v0.7.2.md:9/13-21/57/62/84/103/106`｜`release-checklist.md:3-5/11/16/25/50-51/58/68/73/87-89`｜`release-evidence.md:3/17/21-22/69/75`｜`releases/v0.7.2.md:3/14/35-40/88/149-157`｜`handoff-route.md:672-678`｜`AGENTS.md:160-163/219/288-290`｜`CHANGELOG.md:21/34-36/80`；新增 `loading-asset-v2`。
* **R6（中，A）白名单**：`custom_icon` 必须有 `img_key`；`markdown.icon` 不允许 `size`（宿主分档）；夹具加运行中行。
* **R7（中，A）签名口径**：4 个 `ck_panel_sig` 点与真正发出的 partial 同源（断言 `sent_partial == json.loads(ck_panel_sig)` 且不含 `expanded`）。
* **R8（中，A）`_ld_cardview` 七处调用点**：只动 seed 两处；其余保持 `panel_expanded`；每类一发断言。
* **R9（中，A）GIF 格式**：GIF 只有 1-bit 透明 + 调色板 ⇒ "纯色实心 + 阈值二值 alpha + 自建调色板"（v1/v2 实测彩点，v3 通过）；**挑图在上传之前**。
* **R10（中，C）`core/panel.py` 数据缺失**：`finalized` 必须由建轮/收尾生产并在 `snapshot` 透出。
* **R11（低，A）锚点**：`spinner_img_key()` 不改名/不搬（V4-42 锚点）；`_tool_title_div` 的 markdown return 原文尽量保持（V4-46 锚点），必要时同提交重对齐；`div` 兜底必须用户目视后再切。
* **R12（低，C）D1′ 失败路径**：上传失败/回落时保留静态回落（`spinner_img_key()` 空 key ⇒ `standard_icon`），**不写坏建卡**；上传脚本失败让 P2 明确红。
* **R13（高，B）盖章漏洞**：`mutate_check.py:3600-3605` 的 `_full` 没排除 `target_only` ⇒ `--target-only` 不分片全红时会**直接盖 `full_audit_at`**，而从未跑非目标门禁。**本批必修**（§2 mutate_check 行）+ 两条单测。
* **R14（中，B）合并容错/`at`**：fresh 条目 `at=HEAD`；未覆盖旧条目 `at=f97ce19/d4ad9ce` ⇒ 合并必须显式 `--allow-at f97ce19 --allow-at d4ad9ce`；HEAD 必须是**真 commit + 干净树**（`merge_ledger4` 只验 commit 存在，不验脏树）。
* **R15（中，B）对照锚点存活**：12 条对照里 6 条落在 `core/adapter.py` ⇒ 改 adapter 后必须确认 6 条锚点存活（`--preflight` 先跑，秒级；断一条就白跑 10 分钟）。
* **R16（中，B）冻结清单**：跑前把代码+测试+golden **一次性 commit**，`git status --porcelain` 必须空；跑中冻结 HEAD/工作树/`tests/*.py`/`tests/golden_cardkit_trace.json`/`docs/audits/v0.7.2/{tool-icons,footer-contract}.json`/`tests/mutation-verdicts.json`/`.deploy`；分片账本与日志隔离在 `$D/seed{i}.json`、`$D/shard{i}.log`，**绝不共用**。

## 6. 回退开关

| 想要 | 动作 |
| --- | --- |
| 回到"全程折叠" | `streaming_panel_expanded: false` |
| 一直展开 | `panel_expanded: true` |
| 工具行不用动图 | 常量置空 ⇒ `spinner_img_key()` 回落 `standard_icon`（R12） |
| 页脚恢复符号 | 保留 `footer_line` 旧分支开关（P3 实现时留显式参数，便于回退） |

## 7. 审计与收敛记录

| 阶段 | 路数 / agent id | 发现（编号+严重度） | 处置 | 未采纳及理由 | 收敛判据 | 审计时 HEAD |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | A `2dbf247f`（已完成） | H1 `panel.py` 缺 `finalized`；H2 双表同改；H3 patch 车道；H4 夹具假绿；M5 白名单；M6 七调用点；M7 签名；M9 页脚范围；M10 写卡表述 | 全部折进 §2/§3/§5（H1→§2 panel.py、H2→§2 cards/cardview、H3→§5-R1、H4→§3-P4 硬字面量、M5→§2 check_cardview、M6→R8、M7→R7、M9→§0-B1 口径 + §2 cards、M10→§4 更正 2） | 无；M8 定级由"高"降为"P2 验收项" | GO-WITH-CHANGES | `047e708` |
| P1 | C `344b1e37`（已完成） | A1 漏心跳/切卡；C1 只改一张表；B1 只做一半；提速与冻结冲突；"docs 不触发指纹"过宽；登记文件矛盾；流程链缺口；发布脚本校验缺口；AGENTS/README 旧口径 | 折进 §2（adapter 四点 / 双表 / 页脚范围 / 发布脚本）、§3（P7 顺序 / P4 变异清单要求 / 探针 `show_reasoning`）、§4（更正 1/2 + 不采用清单）、§5-R4/R5 | **B1 保留 ✅/❌/⛔**（用户逐字确认过），把"去 emoji"限定为"只去段前缀" ⇒ 记 §0-B1 | GO-WITH-CHANGES | `047e708` |
| P1 | B `19ee94be`（已完成） | ① `--target-only` ≈零收益；② target-only 与 merge 兼容性差；③ **`_full` 未排除 `target_only`（盖章漏洞）**；④ 对照/gate_fp 前提；⑤ `at`/`--allow-at` 陷阱；⑥ 耗时算式（≈5.0s/条、6 片 7.5–8.5min 轻载、最坏 15–20min） | §4 全面重写（**不用 `--target-only`**、6 片完整模式、失败回退、时间预估与来源）；§2 增加 `mutate_check.py` 修 `_full` + 两条单测；§5 新增 R13/R14/R15/R16；§4 不采用清单补 9 条 | 未采纳：②"8 分片"（并行度↑触发 45s 超时，收益不稳）；③"APFS COW 快照"（属工具改动，收益 1–1.5min，登记 v0.7.3 候选） | GO-WITH-CHANGES | `047e708` |

**P1 收敛结论：GO-WITH-CHANGES**（三路发现全部处置；两条更正已写进 §4；一条口径澄清见 §0-B1；两个 v0.7.3 登记已加）。

### P3（生产代码）三路对抗审计 —— 全部已回、逐条处置

| 路 | agent id | 发现（编号+严重度） | 处置 |
| --- | --- | --- | --- |
| A 协议/语义 | `6228d4d8` | ①（中）`finalized` 从不在回合结束/中止落地 ⇒ 纯推理回合与 `/stop` 的终态卡里嵌套轮仍展开、耗时继续涨；②（中）`record_tool_started` 在幂等闸门**之前**切轮 ⇒ 重复 `tool_call_id` 会把在写的推理轮提前定稿；③（中）R1 范围比计划写的宽（`_ld_render_card` / 降级车道 / split 封旧卡也带 `expanded`）；④（中）`native_transport: patch` 在 structured 下是死配置（既有问题，README 的"可回退"不成立）；⑤（中）真机跑的是旧 `.deploy`；⑥（低）V4-66 名实不符（只锚 seed 第一处）；⑦（中）"手动展开是否保留"无自动化证据（merge 语义）；⑧（低）D1′ 仍是过渡态；⑨（低）内外层手动手势都不持久 | ①②**已修**（`panel.py`：回合结束/中止定稿；幂等命中先 return 再切轮）+ 两条新用例 + V4-77/V4-78/V4-76；⑥**已修**（改名 + 新增 V4-75 + structured 车道专用用例 `test_v072_a1_seal_split_new_card_is_expanded`）；③⑨**登记**（见 §5-R1 扩写 + v0.7.3）；④⑦**折进 §8 探针规格**（补 `/stop`、内层手势两格）；⑤⑧是**既定状态**（P6 才重指 `.deploy`；资产待用户选定） |
| B 证据/判别力 | `9b0b5a01` | F1（**高**）`finally` 里盖章 ⇒ Ctrl-C/未捕获异常也能盖 `full_audit_at`（实测 SIGINT 0.5s ⇒ `full_audit_at=HEAD` + `entries=0`）；F2（**高**）split 第二处 seed 无任何门禁（删掉那行六支门禁全绿） | F1**已修**（`finally` 只写增量；全量章挪到「变异循环 + 对照循环都跑完且零缺陷」之后；`sigint_probe.py` 复验：中断后账本无 `full_audit_at` ✓）；F2**已修**（V4-75 + 专用用例） |
| C 文档/口径 | `f1723fab` | 15 条：2 高（README/plugin.yaml 把 D1′ 写成已上线）、8 中（注释口径 / 新默认未钉 / split 无门禁 / 页脚旧描述四处 / AGENTS 旧状态词 / P7 文档同步 / 资产与发布脚本行未实现 / 账本过期）、5 低（running 带脏 duration 会拼出耗时段；check_cardview 历史标注；check_hooks 缩进把空页脚变成"渲染异常"；其它） | 高 2 条 + 中 6 条 + 低 3 条**已修**（含 `test_declared_defaults_are_an_explicit_decision` 新增两条默认断言、`running` 不拼耗时段 + 断言、`check_hooks` 缩进、历史标注）；其余（P7 文档同步、资产行）按阶段进行 |

**P3 收敛结论：GO-WITH-CHANGES**（A/B 的高项与 C 的两条高项全部落地并复验；A③④⑦⑨ 登记或折进 P6 探针；无 NO-GO 项）。

## 8. P6 探针卡规格（一张卡，多行；用户只看/回一句）

1. 运行中的工具行（动图 + 蓝 `Running`）；
2. 成功的工具行（灰图标 + 绿 `✓`）；
3. 失败/超时的工具行（红字 + 保留词）；
4. 页脚三态（基础 / `basic` / `full` 各一行）；
5. **动图对照**：现役（借来的）vs 我们的（P2 已选定则只放选定那张 + 标注）；
6. **展开态面板**（**必须 `show_reasoning=true`** 才有嵌套轮：当前轮展开、已结束轮折叠）；
7. **手动展开实验**（文字说明 + 两张卡）：seed 展开 → 请用户手动收起 → 我们发一帧"有内容变化但无 `expanded`"的 partial → 请用户回"是否保持收起"（验证 §5-R1/R7 的省略语义）。⚠️ **这是本批唯一无法自动化、也是最重要的协议前提**（审计 A：仓库内只有「去 expanded 的载荷 code=0」这一条证据，不证明 merge 语义）⇒ 必须真机做完并记录用户原话。
8. **回合结束的定稿行为**（审计 A 的 ①）：一张 **`show_reasoning=true` + 纯推理**（没有工具、没有正文）的卡，收尾后那个"第 1 轮"应该是**折叠**的；再给一张 **`/stop` 中止**的卡（`panel_expanded=false` 与 `true` 各一张更好）—— 请用户回「嵌套轮是收起还是展开」。
9. **内层手势**（审计 A 的 ⑨，登记项，只观察不改）：请用户在一个**已结束**的嵌套轮上点一下展开，然后等我们发下一帧 ⇒ 回一句「它是否又自己收起来了」（用来证实 R1b 的登记描述与真机一致）。
10. **`native_transport: patch` 的死配置**（审计 A 的 ④，只观察）：探针卡里附一行说明（`patch` 在 structured 下无效），避免用户以为切这个键能回退。
