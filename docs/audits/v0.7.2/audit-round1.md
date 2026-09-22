# v0.7.2 阶段审计（第一轮 A/C + 收口记录）

**审计快照**：`c625562`（审计副本）→ 收口落在 `fe8f288` / `3064f81`（当前 `main`）。
三轮对抗审计分工（用户口径：每阶段 3 个，意见不一致要讨论到统一）：

| 审计 | 方向 | 结果 |
| --- | --- | --- |
| **A** | 代码正确性 / 计划前提 | ✅ 报告已收（见下「绿变异与前提证伪」） |
| **C** | 反假绿（绿变异） | ✅ 报告已收（7 条绿变异 + 5 条前提纠正） |
| **B** | 用户可见效果 | 第一轮失败（子代理异常）⇒ 已在 `3064f81` 上重跑 |

## 一、审计 A/C 的发现与收口（逐条）

| # | 发现 | 证据（审计侧命令/输出） | 收口 |
| --- | --- | --- | --- |
| 1 | **P2 前提错**：计划说「参考实现都是上传 spinner」，三家其实硬编码同一个 `img_key`、都没有上传代码 | `grep -n "_LOADING_IMG_KEY =" aiduPOP/cardkit/elements.py CLS/.../builder.py FC/.../builder.py` 三处同一串 | 计划 §0.2 改写；真机探针 → 用户「① 会动」⇒ 不做上传；`docs/audits/v0.7.2/loading-asset.md` |
| 2 | **P0 真根因**：`collapsible_panel` 直接子元素用了 `plain_text` ⇒ tools>20 时 300313 → 收尾 200621 → 核心回落 `send()`（14:44 灰气泡） | `git show c625562:core/cardview.py:235` + `agent.log` 31928–31936 | `9e8b498` 改 `markdown`；新增 `test_v4_33_long_turn_card_never_puts_text_nodes_inside_collapsible_panels`（**递归扫建卡实体+每次元素 batch+收尾 patch**）；变异 `V4-33` 实红 |
| 3 | **P1 变异失效**：`G2-10`/`Y20` 在口径翻转后与现行为等价（跑出来 🟢 21.6s / 11.8s） | `mutate_check -k G2-10` / `-k Y20` | 重新指向「又挂回短码」；两条现在实红 |
| 4 | **P1 第三个夹具**：`check_cardview.py` 只断言 v0.7.1 JSON 自己的 `footer_order`（含 `short_code`），生产表漂移看不见 | `tests/check_cardview.py:99-100` | 新增 `_assert_v072_contracts()`：生产表 vs 冻结契约 + 页脚契约 + **代码行不许出现短码 emoji**；v0.7.1 JSON 保留为历史（加注释说明已被推翻） |
| 5 | **P1 短码覆盖不全**：只扫局部页脚 ⇒ 把短码塞进 error 折叠提示/error 页脚五门禁全绿 | 审计 C 的 `P1-B` / `P1-C` 变异（全绿） | 新增 `test_v4_17b`（状态 × mid 全枚举扫**整卡 JSON** + 出站载荷全扫）；新增变异 `V4-17B`（error 折叠提示挂短码）实红 |
| 6 | **P2 删失败假绿**：① 不看返回码 ② await 前先标已删 ③ `300313` 一律当「已删掉」 | `P2-1`/`P2-2` 变异全绿 + 强制失败探针 | 代码：删除失败保持标志 + **换号重试**（同 uuid 撞 200770）+ `300313` 必须 msg 点名 `loading_hint` + 上限 3 次 + warning；用例 `test_v4_14c`（含「点名别的元素 ⇒ 不许当已删」反面）；变异 `V4-40`/`V4-41` 实红 |
| 7 | **P2 资产路径不可由假 CardKit 证明** | 全仓 `tests/ core/` 里 `img_key|custom_icon|image.create` 0 命中（当时） | 用**真机探针 + 用户目视**证明（`docs/audits/v0.7.2/loading-asset.md`）；仓库侧加字面量断言 `test_v4_14b`（`img_v` 前缀 + 静态回落 + 仍然无文字） |
| 8 | **P3 别名覆盖不完**：`exec → robot_outlined` 改坏仍五门禁全绿 | 审计 C 的 `P3B` 变异（全绿） | `test_v4_18` 改成读冻结契约 `docs/audits/v0.7.2/tool-icons.json` **全表逐条 + 顺序** + 归一化/前缀/子串反例；变异 `V4-44` 实红 |
| 9 | **P3 口径冲突**：`terminal` 不在 CLS 表里（实测 `_resolve_tool_descriptor("terminal")` → `None`），我们却把它当 CLS 别名 | 审计 A 实测（本次复核：CLS `_TOOL_DESCRIPTORS` 只有 `exec/bash/command/run`） | `ICON_ALIASES` 缩到 **28 条 = CLS 逐条**；`terminal` 单列 `ICON_ALIASES_LOCAL_EXTRA` **登记偏差**（Hermes 真实工具名 `tools/terminal_tool.py:1258`）；新增 `tests/check_cls_alignment.py` **直接解析 CLS 源码**比对（含顺序 + 前缀歧义 + 偏差登记校验）→ `CLS ALIGN OK` |
| 10 | **P3「偏上」结论未验证**：两侧 icon 对象字段完全相同，最可能是**文本换行** | 审计 A 逐字段对比表 | 探针作废重做：`tests/probe_icons.py`（**同一段文本、只变 icon** + 长文本臂），已发 `om_x100b6424d23e24a8c3368dfbdaad661`，待用户选版 |
| 11 | **P4 表单提交盲区**：`option/options/input_value` 只在**未嵌入 form** 时返回；form 内答案只在 `action.form_value[name]`；缩键变异全绿 | 审计 C 的 `P4-1` 变异（全绿）+ 官方文档 `card-callback-communication.md:41-49` | `_ld_normalize_value` 五键全量断言（含「不许整体合并」「value 优先」）；`check_clarify_e2e` 增**真 SDK payload 类**构造的表单提交场景（value 空 + form_value）→ 网关解除阻塞 + 回执 + 重复提交 toast；变异 `V4-45` 实红。**表单容器形态登记为残余**（计划 §3） |
| 12 | **P5 验收不可证伪**：`_log_outbound` 只覆盖 `send()` 两个 text 分支，卡片成功与 `edit_message` 无痕 | 审计 A 代码走查 + 审计 B 日志实录 | 补 `send()` 卡片成功 / `edit_message` 成功 / 回落到三处；限流 key 含 chat；`test_p5_outbound_log_records_every_egress_shape` + 3 条变异实红 |
| 13 | **流程：`-k` 只跑目标门禁违背「至少一门红」** | 审计 A（会把「只有非目标门禁抓得住」的变异误报 🟢） | `_run_gates_first_red`：目标门禁先跑 → 绿则继续 → **遇第一支红即停**；`_run_one_gate` 加 120s 硬超时 |
| 14 | **流程：秒级判读不可达** | 审计 A：单条 idle 28s / 重载 89s；2 分片 72.4s（1.8x）；4 分片无收益 | 三件套：`--preflight`（0.2–0.8s）+ `test_units.py --only <子串>` + first-red 判定；分片上限 2；P6 预算写进计划 §2 |
| 15 | **测试 flaky**：`test_cardkit_transport_writes_elements_and_falls_open` 的批次序列依赖时钟（4 分片并发 263/264） | 审计 A 实测 | 断言改为**规则**（首帧三件套、每帧 ≤1 笔装饰、序号连续、写数与账本跨源相等）；新增确定性用例 `test_ck_decor_is_not_rewritten_when_nothing_changed`（假页脚翻转） |

## 二、「五门禁全绿但行为坏」的绿变异（审计 C 原文）与收口后状态

| 绿变异（审计 C） | 收口后的判据 | 现状 |
| --- | --- | --- |
| `P1-B`：只在 legacy error 状态把短码挂回页脚 | `test_v4_17b` 全状态扫帧页脚+整卡 | 🔴 实红 |
| `P1-C`：把短码塞进 error 回合的 `collapsed_hint` | 新增变异 `V4-17B` | 🔴 实红 |
| `P2-1`：删除照发但不看返回码 | `test_v4_14c`（失败保持标志 + 下帧重试） | 🔴 实红（`V4-40`） |
| `P2-2`：await 前先标已删（永不重试） | 同上 | 🔴 实红（`V4-40` 覆盖） |
| `P2-④`：`img_key` 无效（假 CardKit 不校验资产） | `test_v4_14b` 字面量 + 真机探针 | 🔴 实红（`V4-42` 覆盖静态回落那半） |
| `P3B`：`exec → robot_outlined` | 冻结契约全表逐条 | 🔴 实红（`V4-44`） |
| `P4-1`：`form_value` 提取键缩到 2 个 | 五键全量断言 + 表单提交 e2e | 🔴 实红（`V4-45`） |

## 三、审计 A 的「命令/输出」纪律给本轮的落地

* 四元组证据（结论 / 命令 / 输出+exit code / commit+sha256）：本文件所有条目都能在
  `git show` 与 `tests/` 的用例里对上；命令与实测秒数见 `docs/plan-v0.7.2.md` §2。
* 每条「没问题」必须带证伪尝试 ⇒ 本轮新增变异 7 条（`V4-40..45` + `V4-17B`），
  加上重指向的 `G2-10`/`Y20` 与 P5 三条，共 12 条新/改变异全部实红。

## 四、审计 B（用户可见效果）与 C2（反假绿第二轮）

两轮都在 `3064f81` 上独立复现（B 用 `git archive` 解包只读副本；C2 用 `/tmp` 冻结拷贝）。
结论都是**需修改**，以下是发现 → 收口（收口落在 `69f37bd`）。

### 审计 B

| 发现 | 证据 | 收口 |
| --- | --- | --- |
| **阻断**：`_ld_fit_structured_card` 的 tier 无条件写 `panel_enabled/footer_enabled=True` ⇒ V4.15「无过程数据不出空面板」与 `unified_panel=false`/`footer=false` 被静默违反 | `snap_p4_empty_panel.py`：空快照 + 两开关 false，`send()` 捕获仍是 `["markdown","collapsible_panel","markdown"]` | tier 改「上限」语义（`bool(x and requested)`）；`test_v4_15_static_and_fit_lanes_never_reopen_disabled_panel_or_footer` + 变异 `V4-48` 实红 |
| `/stop` 重绘的结构化终态卡 `config.streaming_mode=true` | `snap_p1_stopdegrade.py` | 重绘分支置 false；`test_v4_50_...` + 变异 `V4-49` 实红 |
| **P3 真实工具名 29/42 落兜底**（含 `delegate_task`/`execute_code`/`memory`/`session_search` 等已启用 toolset） | `snap_p3_icons.py`：`REAL_TOOL_NAMES_COUNT 42 / FALLBACK_COUNT 29` | 本地扩展表扩到 33 条（每条带理由）⇒ 60 个真实名 **0 落兜底**；`test_v4_48b`（冻结清单）+ 变异 `V4-52` 实红 |
| **P5 出站留痕不覆盖** native 帧 / 澄清卡 / `/stop` / DEGRADE | 动态驱动 `_ld_send_card`/`_ld_update_card` → `outbound_logs=[]` | ⚠️ **部分收口**：本轮先补了 `send()` 卡片成功 / `edit_message` 成功 / 回落（+ 限流常量与同会话限流断言）；**低层原语（`_ld_ck_create`/`_ld_send_card`/`_ld_update_card`）的统一留痕仍待做**（登记为残余项） |
| P4 三态/TTL/retry/表单容器未实现 | 生产 2.0 卡 `has_form:false` | 与计划 §3 的残余登记一致；**不再**声称 P4 已完成 |
| `show_reasoning=true` 时嵌套 `collapsible_panel` 客户端渲染未验证 | 复现 20 个嵌套推理面板；仓库自己的 `plan-consensus.md:111` 也把它列为未验证 | 登记为残余项（默认 `show_reasoning=false` 规避），需真机长回合开启推理复验 |

### 审计 C2（绿变异）

| 绿变异 | 为什么五门禁全绿 | 收口 |
| --- | --- | --- |
| **G1** `exec → robot_outlined`（生产表 + 我们的冻结 JSON 同时改） | 两边自比；`check_cls_alignment.py` 当时**不在五门禁**、`run_fast` 也不含它，且 CLS 缺席即 SKIP | ① `check_cardview` 增**独立字面量**（exec/bash/command/run/read/... + 本地扩展真实名 + spinner 三字段）；② `check_cls_alignment` **纳入正式门禁**（`GATE_ORDER` 第六支，`_PASS/_FAIL` 标记补齐）；③ 加 `--require`：CLS 缺席即 FAIL（`run_fast` 走 `--require`） |
| **G2** `SPINNER_IMG_KEY → img_v3_FAKE` + 同步重生成 golden 夹具 | `test_v4_14b` 的「字面量」是 `startswith("img_v")` + 与生产常量自比 | 同 ①，spinner key 与 loading 元素三字段写成**独立字面量** |
| **G3** `_log_outbound` 限流 `30s → 3600s` | p5 用例只用 4 个**不同** chat，从不触发同会话限流 | 抽 `_OUTBOUND_LOG_INTERVAL_S = 30.0` + 常量字面量断言 + 同会话限流/窗口过后恢复断言；变异 `V4-51` 实红 |

C2 另有两条「判定力是假的」观察，如实记下：
* `seq += 1` 的「换号重试」专用断言**无判别力**（中间面板写把 seq 推高了）——
  真正抓住 `seq += 0` 的是黄金夹具；夹具若同步重生成，这条就无覆盖（残余风险）。
* `test_v4_14c` 的 `assert warned` 是**单点**（删它 + 删生产 warning ⇒ 该用例仍绿）。

## 五、尚未收口（如实登记）

* **低层出站原语**（`_ld_ck_create` / `_ld_send_card` / `_ld_update_card`）的统一留痕未做
  （B 的残余）：这些路径发出去的东西目前只能靠上层 `send/edit` 的痕迹间接判断。
* 「图标偏上」探针已发待用户选版；长回合真机复验待用户（离线版判据已就绪）。
* 表单**容器**形态、澄清卡 `submitted`/retry/`confirmed`/TTL 未实现（计划 §3 残余项）。

## 六、第三轮：增量验证（用户质疑「每次跑一两小时的全量矩阵」后的协议变更）

用户 2026-09-21 直接质疑全量矩阵的耗时。处置与实测数字见 `docs/verify-log.md` 的
「09-21 协议变更」；这里只记**增量跑本身揪出的缺陷**（它们以前被「每次都跑全量」的仪式掩盖了）。

| # | 缺陷 | 为什么老办法看不见 | 收口 |
| --- | --- | --- | --- |
| 1 | 变异 `G2-3` 的替换串**只抄了语句第一行** ⇒ 留下两行悬空实参 ⇒ 语法错误 | 五门禁只报「💥 只有崩溃」，而 💥 **不算判别力证据**却也没被当失败追 | 锚点改成完整语句；`-k G2-3` 实红 |
| 2 | 变异 `V2-1` 的替换串漏了续行 `_cfg_raw("text_profile"))` ⇒ `unexpected indent` | 同上（💥） | 补齐续行；实红 |
| 3 | `R9-15`：「没有活跃流可收尾」这个**正常路径**被记进 `frame_fail` 账本 | `/larkdeck status` 的「写卡失败」会虚高，而当时**只钉了掉回纯文本那本账** | 在 `/stop` 那条用例里补 `frame_fail_count == 0`；实红 |
| 4 | `V0-7`：`engine_stamp=degraded` 之后**又回到结构化帧**（降级安全网失效） | 4 处 `engine_stamp` 断言都在「怎么降级」，没人断言「降级之后不再用元素通道」 | 新增 `test_v0_7_degraded_turn_never_reenters_the_structured_frame_path`；实红 |
| 5 | `V4-25`：结构化正文元素渲染 `⏳ 正在生成…` | 占位符只在 **legacy** 渲染器 `cards.reply_card` 上有断言 | 新增结构化车道用例（正文元素 + 出站载荷都不许出现占位文案）；实红 |
| 6 | `icon_emoji` 的兜底 `ICON_EMOJI[ICON_FALLBACK]` 是**急切求值** ⇒ 兜底被改就 KeyError（把 fail-open 拖成 120s 超时） | 只在「兜底真的被改」时才炸，而当时没有那条变异 | 改成惰性 `ICON_EMOJI.get(ICON_FALLBACK, "🔧")` |

另有 4 条**等价变异**（`V0-1`/`V0-15`/`V0-17`/`V3-3`）从 `MUTATIONS` 移进 `CONTROLS`：
它们要么只改一条退休告警、要么动的是**结构化渲染里从未被读**的 `result_block` 字段 ——
以前每次全量都报 🟢「断言没有判别力」，那是在拿等价变异考门禁。

**这一段的方法论**（值得沿用）：
* 全量的**信息价值**只在「这段区域变过吗」⇒ 区域指纹（±15 行）+ `inherited(ref)`（可审计的继承）；
* 目标门禁直跑判**红**有效、判**绿**不记账（绿的留给完整模式复核）⇒ 不牺牲「至少一门红」契约；
* **继承只对生产代码区域做指纹、不对测试套件做** —— 残余风险由**周期性全量直跑**兜底
  （`full_audit_at` 为空/过期即补跑，不阻塞发布；本轮已在低负载后台启动一次）。

## 七、发布前终审（A/B/C，2026-09-21 深夜）

三路终审都在 `95af186` 的**冻结副本**上独立复现（原仓库当时被并发写入者推进，审计纪律正确）。
结论：**各自都找到真问题**，逐条收口如下。

### 审计 B（用户可见效果）→ 1 条配置缺陷

| 发现 | 证据 | 收口 |
| --- | --- | --- |
| `panel_expanded=true` 在 **structured** 引擎被静默吞掉（96 个配置组合里唯一的不一致） | `p5b.py`：`view.panel.expanded=True / panel_shell.expanded=False / panel_partial.expanded=False`，而 legacy 正常 | `panel_shell(view, *, expanded=None)` 改为默认取 `view.expanded`；新增 `test_v4_51` + 变异 `V4-53` 实红 |
| 五件事本体（页脚/加载/图标/长回合/澄清）+ 96 组合矩阵其余项 | 逐条命令与输出见其报告 | 通过（记录在 `release-evidence.md`） |
| 嵌套面板真机渲染 | 结构证据 + 真机探针已发 | 待用户回话（不得写成已验证） |

### 审计 A（代码正确性）→ 3 条（1 条未采纳，理由见下）

| 发现 | 证据 | 收口 |
| --- | --- | --- |
| 账本「测试侧指纹」不含 helper/fixture ⇒ 只改 `tests/write_golden_trace.py` 就能让 `CK25` 变绿而 `--delta` 仍跳过 | 反例四元组完整 | 新增 `_helper_fp()`（`write_golden_trace.py` + golden 夹具 + 两份契约 JSON），记账与跳过都要求它一致 |
| `--upgrade-inherited` / full 模式被 `MUTATIONS` 里一条 `expect==""` 的条目挡住（`full_audit_at` 永远刷不上；且构造出「只有 inherited 也刷戳」的伪造路径） | `-k 'C-对照：structured'` exit 1；stub 反例 | 该条目移入 `CONTROLS`；`_shape_error` **禁止** `MUTATIONS` 出现空 expect；`_full` 要求「跑满所有有 expect 的变异」 |
| 45s 超时提示写死「>120s」 | grep | 抽 `_GATE_TIMEOUT_S`，文案用它 |
| **建议把 V4.15「无数据不出面板」也用到 native 终态 / 切卡 / `/stop`** | probe：空回合 finalize 仍带 `collapsible_panel` | ⚠️ **未采纳**（分歧记录）：回合卡的面板是**状态色的唯一载体**（`card_status_header` 默认关）⇒ 摘掉它 = 用户看不到完成绿/出错红/中止黄，正是 `test_stop_redraw_paints_an_empty_turn_yellow` 记录的用户口径「状态改了、卡片没变、还不报错」。V4.15 的适用范围明确为**静态回退车道**（系统提示/命令回复这类「不属于某一回合」的卡）。**修改**：`_panel_has_data()` 抽成公共判据并注明适用范围；新增 `test_v4_15b` 把「终态留住面板（带状态色）」钉成显式断言（native 收尾绿边 + `/stop` 黄边）。 |

### 审计 C（反假绿）→ 2 条绿变异 + 3 条协议逃逸

| 发现 | 证据 | 收口 |
| --- | --- | --- |
| **绿变异 G1**：`view.footer_enabled = bool(footer_on and requested_panel)` ⇒ `panel=false + footer=true`（页脚默认开）时连页脚一起丢，六门禁全绿 | 行为探针：baseline `['answer','footer']` vs mutant `['answer']` | `test_v4_15` 增**混合档**断言（③b）；变异 `V4-54` 实红 |
| **绿变异 G2**：degraded 安全网写成 `... and not finalize` ⇒ 降级回合的**收尾帧**又回结构化元素通道，六门禁全绿 | `degraded + finalize=True`：baseline `structured_called=False` vs mutant `True` | `test_v0_7` 对 `finalize in (False, True)` 各跑一遍；变异 `V0-7b` 实红 |
| **协议逃逸 H1**：`_anchor_region` 只看 old 的**第一行** ⇒ 22/470 条记录的指纹取到别处（`R7-16` 实证：`--delta` 报「已验」而门禁实际红） | 完整四元组 | `_anchor_region` 改为按**完整 old** 定位；`_delta_split` 跳过前先跑 `_anchor_problem`（脱节/歧义一律重跑）⇒ 那 22 条已全部重跑并实红 |
| **协议逃逸 H2**：`MUTATIONS` 里 `expect==""` 的条目永远不跑却被计入「已跳过」 | `--delta -k 'C-对照'` exit 0；ledger 只有 470 条而 status 报 471 | 同审计 A 的一条：移入 `CONTROLS` + `_shape_error` 禁止 + `_full` 覆盖校验 |
| **协议逃逸 H3**：`--seed-inherited` 可以给「ref 清单里根本还没有的变异」盖章（蛰伏路径） | 构造 | `_seed_inherited` 增加资格校验：该 ref 的 `mutate_check.py` 里必须已有同名条目 |

### 收口自验（主执行方在 `936a87f` 上复跑）

* **helper 指纹（审计 A-①）**：在 `/tmp` 拷贝里只把 `tests/write_golden_trace.py` 的 `main()`
  改成立刻 `return 0`（**不动任何门禁文件、不动生产代码**）⇒ `--delta -k CK25` 从「跳过 1 条」
  变成 **「待跑 1 条」**，并当场把 `CK25` 判成 **🟢 断言没有判别力**、退出码 1 ——
  即「改坏 helper 让黄金夹具门禁失效」这条路径**已经不会再被账本误判成已验**。
* **锚点守卫（审计 C-H1）**：把 `_anchor_region` 换回「只看首行」的坏实现 ⇒ `--preflight`
  精确拦下 **22/474** 条（与终审 C 的发现数量一次对上）；换回现役实现 ⇒ preflight 干净。
* **`expect==""`（审计 A-②/C-H2）**：`--preflight` 干净；`--ledger-status` 的 474/474 与
  `tests/mutation-verdicts.json` 里 `verdict=red-assert` 的条数一致。

### 收口复核（两个复核员，分别在 `90f6bb1` 快照上）

**A/B 收口复核：6/6 通过**（逐条带四元组与证伪），并留下 2 条非阻断残留 —— 均已修：
* `mutate_check.py` 里 `_run_one_gate` 的 docstring 还写「120s 硬超时」⇒ 改成引用 `_GATE_TIMEOUT_S`（45s）；
* `--ledger-status` 先于形状校验执行 ⇒ 清单里混入 `expect==""` 时它会报**虚高覆盖率**（而 `--preflight`
  会拒）；已让 `--ledger-status` 先跑 `_shape_error` 并在有问题时 exit 2。

**C 收口复核：5/5 通过**，另外找到 2 条新绿变异：
* `CAND-A`「`panel_shell` 显式 `expanded=False` 被 `view.expanded` 覆盖」⇒ 复查发现**根本没有任何
  调用方显式传这个形参**（终审 B 的修复之后它就成了死 API）⇒ 直接把形参删掉，不留「看着能覆盖、
  其实没人用」的接口。
* `CAND-B`「静态空面板判据丢掉 `reasoning`」⇒ 数据层实测：`panel.record_reasoning()` 会**立刻产生
  一个推理轮**，所以「`reasoning` 非空而 `rounds` 为空」**不可达** ⇒ 该变异是**等价变异**，
  移进 `CONTROLS` 并把结论固化（顺带在 `test_v4_15` 里留了一条「只有推理」的断言作为文档）。

### 三条终审一致确认的「假绿」新形态（值得写进 `lessons`）

1. **「配置开关被另一个开关绑死」**（G1）：混合档（A=false + B=true）没人测 ⇒ 一个 `and` 写错就静默丢字段。
   ⇒ 断言必须覆盖**每个开关的独立生效**，而不只是「全开/全关」。
2. **「安全网只覆盖一半路径」**（G2）：`degraded` 只在非 finalize 生效 ⇒ 收尾帧漏网。
   ⇒ 判据要在**所有分支**上验证，不能只测最常走的那条。
3. **「指纹锚点取错位置」**（H1）：跳过判据依赖的锚点定位若只按首行匹配，就会在**另一个同形代码块**上算指纹 ⇒ 跳过永远成立。
   ⇒ 凡「用位置算指纹」的地方，必须用**完整原文**定位，并在跳过前复核锚点唯一。

---

## 八、收口复核第二轮：新增绿变异 CAND-B2 + 一条「无界等待」盲点 + 全量账本刷新（2026-09-21 深夜）

### 8.1 CAND-B2（真绿变异，已收口；账本 475 条）

终审 C 的收口复核员在 `90f6bb1` 快照上直接构造出：`core/adapter.py::_panel_has_data` 的判据
`tools or rounds or reasoning` 改成 `rounds or reasoning`（**丢掉 `tools`**）⇒ 六门禁全绿，
而**纯工具、无推理正文**的回合走静态 `send()`/`edit_message()` 会把执行面板**整块吞掉**
（工具名/状态/耗时，连面板这个状态色唯一载体一起消失）—— 这正是 V4.15「空面板冗余」的
**镜像错误方向**：用户看不到过程。

收口：

* `test_v4_15_static_and_fit_lanes_never_reopen_disabled_panel_or_footer` 增加「纯工具」块：
  先直接断言 `_panel_has_data(chat) is True`（秒级定位），再走真实 `send()` 断言卡里
  `panel` 在、且 `df -h` **真的在卡里**（防止「有面板但是空壳」的断言）。
  两层都实测过判别力（去掉直接判据后，出卡断言仍然红：`['answer','footer']`）。
* 变异清单新增 `CAND-B2`（目标门禁 `test_units`）⇒ 清单 **475 条**；`--preflight` = 487/487。
* 判据侧说明：「丢掉 `reasoning`」是**等价变异**（`record_reasoning()` 立刻产生推理轮 ⇒
  `reasoning` 非空必伴随 `rounds` 非空），已登记在 `CONTROLS`；`test_v4_15` 里那条
  「只有推理」的断言是**语义固化**，不是判别力来源（注释已改写，免得后人误判）。

### 8.2 变异清单里的一条盲点：测试的**无界等待**会把断言红伪装成 💥

`V1-2`（短路 `panel partial` 分支）的定向用例 0.4s 断言红，但**整支** `test_units` 挂 >60s
⇒ 45s 门禁超时 ⇒ `mutate_check` 记 💥「只有崩溃」⇒ 一条**有判别力的变异**被记成「没有证据」
（历史账本里它靠一条手工 `--only` 的 `note` 续命）。

根因不在被测代码，而在用例：`test_v4_1_inflight_frame_write_is_not_raced_by_heartbeat`
里的 `await started.wait()` 是**无界**的 —— 帧路径既然不再写 panel，`started` 就永远不 set。

收口：新增 `_await_event(event, timeout, what)`（`asyncio.wait_for` + 超时转
`AssertionError`），两处无界等待（上述用例 + `test_v4_1_heartbeat_inflight_blocks_frame_and_keeps_seq_unique`）
改用它。修后：V1-2 整支 **4.7s** 判红并计入账本（`red-assert`）。

**独立复核（2026-09-21 对抗审计 A，只读原仓 + `/tmp` 沙箱）**：

* 判别力两层都在：丢 `tools` 的变异被直接判据（`test_units.py:13026`）抓住；把直接判据删掉后，
  出卡断言（`13032` 的 `panel` 在 / `13034` 的 `df -h` 在）仍然红 ⇒ 断言非空洞。
* 反方向也非恒真：① 静态车道无条件摘面板 ⇒ 红在「只有推理轮」那条（`13014`）；
  ② 面板在但工具行被渲染丢掉 ⇒ 红在 `13034` 的 `df -h` 断言。
* `_await_event(2.0s)` 余量实测：两条用例各连跑 25 次 0 失败（单次 0.18–0.27s，事件实际等待
  ≤0.5ms ⇒ 约 4000 倍余量）；2x/4x CPU 过订阅（8 核上 16/32 个忙进程）各 10 次仍 0 失败。
  同用例后半段不读 `_panel_has_data`/`_CONFIG`，`finally` 恢复配置并 `panel.reset()` ⇒ 无串扰。
* 同类盲点残余（登记 v0.7.3）：3 处裸 `await release.wait()` + 无界 **task join**
  （`await frame`/`await tick`/`await holder`）；当前 475 条语料中**没有**能让它们挂死的变异，
  但未来引入 async 死锁时会以「45s 超时 ⇒ 💥」的形式掩盖断言红。

### 8.3 全量账本刷新（475/475，`full_audit_at=7e7a62d`）

* 两个分片在冻结树 `7e7a62d` 上**直跑**（不用增量）：`fullA1.log` 179🔴、`fullA2.log` 175🔴。
* 23:09 出现一次**瞬时环境故障**（约两分钟内所有门禁运行秒失败 ⇒ 被记 💥；同一批快照现在
  重跑正常 ⇒ 环境工件，不是代码问题）：分片 1 进程被误杀于 179/237（**58 条没跑到**），
  分片 2 尾部 **62 条**记 💥（其中含 **6 个对照**失败；日志里因 per-case 行 + 结论行被同一
  正则计成 12 行 —— 审计 B 纠正），两者合计 = 缺口 **120** 条。
* 缺口用 `--delta` 补跑：从**公共种子账本**（474 − 120 = **354** 条，`gap_seed_common.py`）出发，
  `--shard 1/2`、`2/2` 各跑 60 条 ⇒ `gapA1.log` 60🔴、`gapA2.log` 59🔴（+1💥 V1-2）、
  两片各 6 名对照全绿（共 12 名，与 `CONTROLS` 名单一致）。
* 定向复跑：`V1-2`（`v12b.log`，有界等待修复后 1🔴）、`CAND-B2`（`candb2b.log` 1🔴）；
  审计 B 要求可追溯后，又在**干净树 `396f0ae`** 上各复跑一次
  （`rerun-v12-396f0ae.log` / `rerun-candb2-396f0ae.log`，结论一致）—— 这两条的 `at`
  因此记为 `396f0ae`，其余 473 条为 `7e7a62d`（两分片直跑所在的树）。
* 当晚实际用的合并脚本 `merge_ledger3.py` 被审计 B 实测证明**可被伪造日志绕过**（不重算现场
  指纹、不校验 head、无条件覆盖 `at`）。已补**加固版参考实现** `merge_ledger4.py`：
  head 必须是真实 commit / 对现场树重算 `fp`+`helper_fp`+`gate_fp`（与 `_delta_split` 同口径）/
  `at` 不无条件覆盖（须 ∈ {head} ∪ 显式允许的旧 ref）/ 对照按 **12 个名字**核（不是数行数）；
  在现账本上 dry-run 通过：`✅ 475 条通过（现场重算三指纹 + 日志覆盖 + 12 名对照全绿）`。
  ⚠️ 能力边界写进脚本 docstring：**伪造日志文本仍能骗过任何「文本 → 账本」的合并器**；
  真正的修法是每条记 tree hash + 来源日志 + 行号、由 `mutate_check` 自己产出（登记 v0.7.3）。
  合并后：`--ledger-status` = **475/475 可跳过（真跑过 475 + 继承 0）；待跑 0 条**；
  `--delta --list` = 待跑 0 / 跳过 475。
* 证据与工具归档：`~/.larkdeck-scratch/v0.7.2-full-20260921/`（8 份日志 + `gap_seed_common.py`
  + `gap_seed.py`（更早版本，未参与当晚执行）+ `launch_gap.py` + 两个有界等待器 +
  `merge_ledger3.py`/`merge_ledger4.py` + `README.md` 记录真实执行序列）。
* 遗留（登记 v0.7.3）：① `--shard i/n`（n≥2）不会自己盖 `full_audit_at`，分片账本必须按
  「当日证据 + 现场三指纹」合并（把 `merge_ledger4.py` 固化进 `tools/`）；② 每条 entry 记
  **tree hash + 来源日志 + 行号**（含 `_head_short()` 记录脏树标记），让 `at` 不再只是
  HEAD 字符串；③ 分片 `--update-ledger` 各写各的 `LARKDECK_LEDGER_PATH`（同一文件并发写会丢更新）。

### 8.4 三路对抗审计的判决与处置（2026-09-21 深夜，收口后）

在 `fe52ed8`/`098ce04` 上并行跑三路（**只读原仓 + `/tmp` 副本**，禁全量矩阵）：

| 路 | 方向 | 判决 | 处置 |
| --- | --- | --- | --- |
| **A** | CAND-B2 断言判别力 / `_await_event` 抖动 / 同类盲点 | **两处改动本身通过**；A5 同类残余报「需修改」 | 实测入档 §8.2（25+25 次 0 失败、事件实际等待 ≤0.5ms、2×/4× 过订阅仍 0 失败；两层断言 + 反方向两变异都实测红）。A5（3 处裸 `await release.wait()` + 无界 **task join**）登记 v0.7.3，含「未来 async 死锁会把断言红伪装成 45s 💥」这一更强形态 |
| **B** | 账本与证据链完整性 | B1/B2/B3/B6 字面通过；**B4/B5/附加 需修改** | ① `_meta.full_audit_evidence` 两处数字错（💥 62 不是 120；对照失败 **6 个**不是 12 行）⇒ 已更正；② `CAND-B2`/`V1-2` 的 `at=7e7a62d` 来源不成立（前者在 7e7a62d 不存在、后者依赖 a6b73ae 的有界等待）⇒ 两条改记 `396f0ae`，并在**干净树**上补可追溯复跑（`rerun-*-396f0ae.log`）；③ 当晚的合并脚本 v3 **可被伪造日志绕过**（不重算现场指纹/不校验 head/无条件覆盖 `at`）⇒ 补加固参考实现 `merge_ledger4.py`（现场重算三指纹 + head 必须真实 commit + 对照按 12 个名字核 + `at` 不覆盖），并把它对现账本的 dry-run 结果写进归档 README；④ 归档的 `gap_seed.py` 与实际执行不符（真实用的是**公共种子 354 条**）⇒ 补 `gap_seed_common.py` 并在 README 记录真实序列。⚠️ v4 仍**不能**证明日志真伪 —— 该边界写进脚本 docstring 与 README，真正修法（每条记 tree hash + 来源行号）登记 v0.7.3 |
| **C** | 现役文档 / 发布清单可执行性 | **需修改**（1 条 P1 + 7 条 P2/P3） | 全部已改：§2 逐支循环补 `--require`（原会静默 SKIP）并补 `check_own_body`/`preflight`；自检时间 22:54:38→**22:54:43**；分片账本并发写丢更新（414 vs 413）⇒ 各写 `LARKDECK_LEDGER_PATH`；`--shard`「恒 False」→「**n≥2**」；影子树行改「曾达 22 个/1.5GB、已清 0」+3/27 个小残影；网关 PID 判据注明 2 个 PID；`--ledger-status` 与 `_meta.full_audit_at` 表述拆开；README 已发布列表补齐；`.deploy` 旧副本的复核项写进 §6 |

### 8.6 P3.1 图标定版：官方线性图标 + 文本前缀 + 扩展到更多场景（2026-09-22）

**用户口径（两轮）**：①「emoji 当然很好，看起来很丰富，但未免有点花里胡哨 —— 颜色都不统一；
我给你发的应该是 CLS 的截图吧，**统一的颜色看起来比较高级**」；②「CLS 应该只在一部分场景下用了
这些图标，对吧？我想我们的插件应该**应用到更多的场景里，更全面一些**」。

**横评（`tests/probe_icons_layout.py`，卡 `om_x100b6414825f3ca8c339ed0a7cef3e9`）**：三臂同文字、
同 token（`setting_outlined`）、同灰，只变放法；用户选**乙**；我按截图**逐像素**量三个包围盒
（图标图形 vs 加粗文字）得：甲 `div.icon` 图标高 **3.0px**、乙 `markdown.icon` **0.0px**、
丙 `column_set` 1.0px 但横向空 **179px**。⇒ 放法 = **`markdown` 前缀图标**（官方 2.0 文档名），
风格 = **线性 `_outlined` + `color:"grey"`**。

**官方来源核对（只读）**：图标来自飞书客户端内置图标库（卡片 JSON 只带 token，客户端本地渲染）；
官方枚举页 `open.feishu.cn/document/feishu-cards/enumerations-for-icons`（纯 md 版可脚本化），
我拉全量数出 **854 线性 / 287 面性 / 13 彩色**（去重 1154；面性是线性的子集，175 个同名配对）。
规则：线性/面性单色可设 `color`（官方颜色枚举 + 深浅色主题各一套）、彩色 13 个颜色写死；
**token 必须与官方完全一致，否则不渲染**（所以候选 token 逐个查证后才写进代码）。

**落地**：`TOOL_ICON_BY_ALIAS` + `tool_icon_token()`（渲染层精化；CLS 28 条 token 表不动）、
`markdown` 前缀图标放法、`tool_row_icon: "line"|"emoji"` 配置开关；「更全面」批次把同一批图标
用到**详情行 / 错误块标题 / 折叠提示**。断言：`test_v4_46`（重写）/`V4-56`（两路可切 + 未知值回落）/
`V4-57`（三处新图标 + 白名单 `_VERIFIED_LINEAR_TOKENS` 38 个）；变异：`V4-46`（改回 `div.icon`）、
`V4-56`（token 不精化）、`V4-57/58/59`（三处各丢一个图标）。golden 夹具同步重生成（diff = 行为声明）。

**验证**：`--preflight` 492/492；5 条新/改变异 `-k` 逐条实红；因 golden 夹具在 helper 指纹里，
按协议触发**全量重跑**（480 条，2 分片）。P3.2/P3.3（面板标题拆列 / 页脚逐段图标 / 澄清卡与命令卡）
已登记 v0.7.3。

### 8.5 P3 图标复验：区段符号被工具行复用 + 同 token 挤多语义（用户真机截图，2026-09-21）

**用户反馈原文**：「头两张图是我之前给你发过别的插件的截图，第三张图是我们自研插件的截图，
我看目前图标不一致。而且第三张图里红框圈出来的地方，**面板顶部标题的图标和下方使用命令行
命令的图标是同一个**，这个不合理。如果我们和别的插件的这个图标不一致，我觉得也 OK，
但**要比别的看起来更好**。」

**根因（两处，都是「token 表全绿但观感坏」）**：

1. **区段符号被复用**：14 个 CLS token 里 `setting_outlined` 一格塞了
   exec / bash / command / run / terminal / execute / process / setup / close —— 渲染时全部
   变成 🛠️，而 🛠️ 同时是**面板标题**（工具执行）的区段符号 ⇒ 用户一眼看到「标题 = terminal 行」。
2. **同 token 挤多语义**：60+ 个真实工具名只有 14 个 emoji 可选（`memory` 与 `glob`/`drive`
   共用 📁、`cronjob` 与 `todo` 共用 ✅、`image`/`skills`/`ha` 共用 🧩…），观感上「不一致」。

**收口**：

* 新增**渲染层** `cardview.TOOL_EMOJI_BY_ALIAS` + `tool_emoji(name, token)`：
  ① 区段符号（🛠️/💭）不许出现在工具行（terminal/exec/bash/command/run/execute ⇒ 💻、
  process ⇒ ⚙️、setup ⇒ 🔌、close ⇒ ✖️）；② 按名字精化（memory 🧠、cronjob ⏰、
  session 🕘、image 🎨、video 🎬、speech/text 🗣️、vision 👁️、send ✉️、react 👍、
  show_tip 💡、clarify ❓、computer 🖱️）。匹配规则与 token 解析同源（精确或 `alias_` 前缀）；
  **token 表仍是 CLS 对齐的唯一真相**，`check_cls_alignment` 只读 token 表 ⇒ 仍全绿。
* 断言：新 `test_v4_55`（真实工具名逐条不许落区段符号 + 覆盖表精化程度 ≥15 种 + terminal 家族
  ≥4 种符号 + 老调用点 `tool_emoji("", token) == icon_emoji(token)`）；`test_v4_46` 改钉
  两层（token 级 🛠️ / 行级 💻，并断言行内不出现 🛠️）；`test_v4_48b` 同步补行级断言。
* 变异：新增 `V4-55`（撤掉按名字覆盖 ⇒ `tool_emoji` 退回 token 表 ⇒ 该用例实红）。
* 夹具：golden trace 同步重生成，diff 即行为声明 —— 5 处 `🛠️ **terminal**` → `💻 **terminal**`。
* 复验：`--delta` 因 helper 指纹（golden 夹具）变化触发**全量 476 条重跑**（2 分片），
  跑完再报；账本随后按「现场重算三指纹 + 日志覆盖 + 12 名对照全绿」合并。

### 8.7 P3.1 落地真机复验踩出的 `200621`：`markdown` 上不能写 `text_color`（2026-09-22）

**怎么发现的**：P3.1 改完先发真机探针（`tests/probe_icons_final.py`：5 行工具 + 详情行 +
错误块 + 折叠提示），**第一发就被服务端拒**：

```
code=230099 … ErrCode: 200621; ErrMsg: msg: [parse card json err … value: [unknown property,
property: text_color, path: ROOT -> body -> elements -> [1](tag: collapsible_panel)
-> elements -> [0](tag: markdown)]
```

**两处同源缺陷（都是 P0 级：服务端对未知字段是「整卡被拒」，不是忽略，且一次只报一个）**：

1. **`markdown`（富文本）没有 `text_color` 字段** —— 官方 2.0 字段表只有
   `tag / text_align / text_size / icon / href / content`（+ 公共 `element_id` / `margin`）。
   命中两处：
   * 工具**详情行**（P3.1 新增）—— 影响面只在新批次；
   * **折叠提示**（2026-09-21 那次 P0 修复引入）—— 只在工具步数 > `max_steps`（默认 20）时出现，
     也就是**长回合必炸**；它和用户反馈 #4「卡片 + 灰色气泡」是**同一种故障**，说明那次 P0
     修复只修了一半（元素类型从 `plain_text` 换成 `markdown`，字段却带上了新的非法值），
     而当时只用单测验证过、没有真机验证。
   合法写法：灰色写进 `content` = `<font color='grey'>…</font>`（官方富文本「彩色文本样式」与
   `lark_md` 语法表；`div.text` 的 `text_color` 也**只对 `plain_text` 生效**）。
2. **`div.text` 里没有 `icon`** —— 官方 2.0 普通文本组件（`tag: div`）里 `icon` 是**组件级**
   「前缀图标」（`text` 子对象只有 tag/element_id/content/text_size/text_color/text_align/lines）
   ⇒ 错误块的前缀图标从 `node["text"]["icon"]` 挪到 `node["icon"]`。

**修法**：`cardview._grey()`（一处、docstring 带官方出处）替换两处非法 `text_color`；错误块图标
挪到组件级。

**门禁**：`check_cardview._assert_panel_element_fields()` —— 面板元素树**字段白名单**（按官方三页
字段表登记：rich-text / plain-text(组件 tag = `div`) / collapsible-panel）。规则：未登记 tag 直接红、
`text` 里不许有 `icon`、`markdown`/`lark_md` 不许有 `text_color`、`plain_text` 才允许 `text_color`，
且**一次报出所有越界字段**（跟着服务端试字段要一轮一轮发真机卡，太慢也太吵）。

**变异**：新增 `V4-60`（把灰色写回 `text_color`）、`V4-61`（图标挂回 `text`），两条都由
`check_cardview` 实红；`V4-33` / `V4-57` / `V4-59` 锚点随行为改动重对齐。

**复验**：修复后探针卡发送成功（`om_x100b64159f75b0a0c2f35ecdf3f0d36`）；golden 夹具 diff =
行为声明（详情行/折叠提示 content 变 `<font color='grey'>…</font>`）。因夹具在 helper 指纹里，
按协议触发**全量重跑**（482 条 = 480 + 新增 2，4 分片）。

**教训（并入 `lessons`）**：本地门禁过去只钉「**我们想要的形状**」，没钉「**服务端收不收这个字段**」；
而字段写错的代价是**整卡**被拒，还偏偏只在长回合（`collapsed_hint`）才走到 ⇒ 单测全绿、真机照炸。
字段白名单比断言更早、更全；本轮先做面板树，全卡（header/footer/answer/降级车道）登记 v0.7.3。

