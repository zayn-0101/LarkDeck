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

## 四、尚未收口（如实登记）

* **用户可见效果（审计 B）** 与 **反假绿第二轮（C2）** 正在 `3064f81` 上重跑 ⇒ 结论待补。
* 「图标偏上」探针已发待用户选版；长回合真机复验待用户（离线版判据已就绪）。
* 表单**容器**形态、澄清卡 `submitted`/retry/`confirmed`/TTL 未实现（计划 §3 残余项）。
