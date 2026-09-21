# LarkDeck v0.7.2 计划（用户 2026-09-21 口径修订）

> 由来：v0.7.1 发布后，用户真机复看提出 9 条反馈。其中 1/2/3/7/9 已在 v0.7.2 首批落地
> （提交 `afcad5b`、`31d211d`，门禁 263/263）。本计划收敛**剩余 5 条**，并遵守用户重申的流程纪律。

## 0. 用户明确口径（优先级高于此前任何内部决定）

1. **页脚不再有 🔖 短码** —— 用户从未要求过它（是 V2/V3 阶段内部审计 C1 加进来的）。
   页脚字段对齐同类插件（CLS/aiduPOP）：**状态 · ⏱ 时长 · 🤖 模型 · ctx 用量**。
   短码只保留在**日志自检行**（截图↔日志对齐仍可用，但用户可见处不出现）。
2. **加载指示对齐 aiduPOP `_loading_element`**：`div` + `custom_icon(img_key)` + `text: " "`
   —— **会动、无文字**。
   ⚠️ **2026-09-21 修订（原前提被证伪）**：计划原写「参考实现都是自己上传的」——审计 A/C 逐仓核对
   **不成立**：aiduPOP `cardkit/elements.py:103`、CLS `builder.py:23`、FC `builder.py:24`
   **硬编码同一个 key**，三家都没有 spinner 上传代码（它们的 `upload_image` 只服务正文远程图）。
   实机结果：**用户目视 ① 共享 key = 会动**（探针卡 `om_x100b643abd6394b0dfa26a200d65018`，
   记录见 `docs/audits/v0.7.2/loading-asset.md`）⇒ **不做上传**，直接用共享 key；
   只留一行可切的注入点 `cardview.set_spinner_img_key()`，key 为空时回落静态 `standard_icon`
   （宁可「不动」也不能让无效 asset 撞 300313 把整条装饰链打掉）。
   （跨应用复用本来没有官方支持——`im.v1.image.get` 234008「当前应用不是资源所有者」；
   本机能用是因为三家插件同属本机这一个 Hermes 应用。）
3. **步数折叠**：提示语已按用户口径（`…已折叠 N 条早期思考/工具记录`）；
   N 仍取 `min(配置, 20)`（元素预算推导），**CLS/FC 的 `max_steps=128` 是采集端跟踪上限，不是显示上限**（文档已记）。
4. **系统提示不出面板**（已做）；**流式 seed 不写正文**（已做，修「先显示上一条回复」）。
5. **图标**：与 CLS `streaming/tooluse.py` 别名表**逐条**对齐（含 `execute_code`/`code` 这类），
   并解决「图标比文字偏上」。
   * ⚠️ **2026-09-21 实测修订**：CLS 的 `Run command` 描述符只收 `exec/bash/command/run`，
     `_resolve_tool_descriptor("terminal")` **落兜底**；而 Hermes 的 shell 工具**就叫 `terminal`**
     （`tools/terminal_tool.py:1258`）。⇒ `ICON_ALIASES` 保持**逐条等于 CLS**（28 条，含顺序），
     `terminal` 单列 `ICON_ALIASES_LOCAL_EXTRA`，**登记为唯一有意偏差**
     （`docs/audits/v0.7.2/tool-icons.json::local_extra`）；`tests/check_cls_alignment.py`
     直接解析 CLS 源码比对（CLS 侧改动会红）。
   * 「偏上」的可验证假说（审计 A）：两侧 icon 对象**字段完全相同**（都无 size/margin、text_size
     都是 notation），所以不是字段差；最可能是**我们的行更长**（原始工具名 + 原始 ms）在手机宽度
     换行时图标顶对齐。⇒ 探针必须**同一段文本、只变 icon**（旧计划「甲 icon / 乙 emoji」
     没有隔离换行这个变量，作废重做）。
6. **澄清卡**：按 aiduPOP 三态（pending/submitted/confirmed）实现**可提交**的多选/单选/输入。
   ⚠️ **2026-09-21 官方契约修订（审计 A 查证）**：`option/options/input_value` **只在组件未嵌入
   form 容器时**返回；一旦放进 form，答案只在 `action.form_value[组件 name]`（官方
   `card-callback-communication.md:41-49`；submit 按钮的 `action.value` 是**空**的）。
   ⇒ 现状：组件级 `behaviors`（无 form 容器）路径真机已通；**表单容器形态不在支持范围内**，
   要上 submit 按钮必须同时加 form 容器 + `form_action_type:"submit"` + 组件 `name` + 解析
   `form_value[name]`。这一条**登记为残余项**（见 §4），不假装已覆盖。
   aiduPOP 的 submitted 卡在 2.0 里塞了 1.0 的 `action` 行（会被飞书拒收）**不能照抄**，
   要照抄的是它的状态机（pending→submitted→confirmed + retry + 30min TTL + 去重锁）。

## 1. 分阶段执行（Goal 模式，每阶段一次对抗性审计）

> **执行状态（2026-09-21，提交 `69f37bd`）**：P0 ✅ / P1 ✅ / P2 ✅（用户目视「会动」）/
> P3 ✅（用户选版「乙 = 内联 emoji」，已落地 + 定版确认卡已发；真实工具名 0 落兜底）/
> P4 部分（表单提交契约已钉真 SDK e2e；三态/TTL 未做）/ P5 根因 ✅ + 上层留痕 ✅（低层原语待做）。

| 阶段 | 内容 | 验收（硬指标） | 状态 |
| --- | --- | --- | --- |
| **P0 折叠提示元素类型**（计划外·已修） | `c625562:core/cardview.py:235` 把折叠提示写成 `collapsible_panel` 的 `plain_text` 直接子元素 ⇒ tools>20 时 `300313` ⇒ 收尾 `200621` ⇒ 核心回落 `send()`（**14:44 灰气泡的真根因**）。已改成 `markdown` | 变异 `V4-33`（改回 `plain_text`）实红；真机长回合复验 | ✅ 已修 + 变异红 |
| **P1 页脚** | 去掉两处页脚短码；重写钉短码的用例 + 重跑黄金夹具；短码保留在自检日志 | 全五门禁绿；变异「又挂回短码」实红（`G2-10`/`Y20`/`V4-17B`）；`test_v4_17b` 扫**整卡 + 出站载荷** | ✅ |
| **P2 加载指示** | `loading_hint_element()` = aiduPOP `_loading_element`（`custom_icon(共享 key)` + `text:" "`，会动无文字）；首字即删；删除失败**换号重试**、`300313` 只按 msg 判定、上限 3 次 | 探针目视「会动、无文字」✅；变异 `V4-32/40/41/42/43` 实红 | ✅ |
| **P3 图标** | 主表 28 条逐条等于 CLS（含顺序）+ `terminal` 登记偏差；`tests/check_cls_alignment.py` 直连 CLS 源码；A/B 探针（**同一文本、只变 icon**）确认「偏上」 | 对照表逐条 ✅ + CLS 对齐门禁 OK；变异 `V4-44` 实红；用户回话选定探针版本 | 代码 ✅ / 探针待发 |
| **P4 澄清卡** | 三态可提交（pending/submitted/confirmed）；组件级 `behaviors` 路径已通；表单容器（`form_value[name]`）**未实现**——登记为残余项 | `check_clarify_e2e` 覆盖「选中/多选/输入/**表单提交** → 回执 → 重复点击 toast」✅；真机点一次成功（已达成）；retry/TTL 待做 | 部分 |
| **P5 灰气泡** | 真根因是 P0（不是内核 `Working` 文本）；出站留痕覆盖 `send()` 卡片成功 / 卡失败回落 / `edit_message` 成功 / 回落 | 长回合（tools>20）重放：卡 JSON 无 `plain_text` 子元素、日志无 `300313`/`200621`、`/larkdeck status` 掉回纯文本计数不增；留痕三处有测试 + P5×3 变异实红 | 根因 ✅ / 长回合复验待做 |
| **P6 收尾** | `/neat-freak` 洁癖收尾；README/AGENTS/CHANGELOG/handoff/plan 口径同步；`v0.7.2` tag + release；Mac 本机插件同步生效 | 「全量门禁」范围见 §2 末；用户终验；tag 已推；`.deploy` = tag 提交且网关已重启 | 进行中 |

## 2. 流程纪律（用户重申，必须遵守）

* **不用 `sleep` 轮询**：等待用「有界等待器」（`job_output(wait=true)` / python 条件等待，命中即返回）。
  ✅ 已落地：`check_clarify_e2e` 的 6 处 `await asyncio.sleep(0.3)` 已删（handler 是同步的）
  ⇒ 该门禁 4.2s → **1.98s**；`mutate_check._run_one_gate` 加 **120s 硬超时**。
* **变异测试提速**（实测数字 + 方法，2026-09-21）：
  * 单条变异 idle 28s / 重载 89s；6 条串行 130.7s；**2 分片 72.4s（1.8x，是上限）**；
    4 分片无收益且会让基线 flaky（时钟敏感断言已修 + 新增确定性用例）；
  * `--preflight` 0.2–0.8s；`test_units.py --only <子串>` 供单条判读；
  * **判定策略（本轮落地）**：先跑声明的目标门禁 ⇒ **绿了再按其余门禁继续跑，遇第一支红即停**。
    既保住「至少一门红」的契约（旧 `only` 模式会把「只有非目标门禁抓得住」的变异误报绿，审计 A），
    又不必为秒级判红的那类变异跑满六支；**阶段/发布收尾用 `--delta`（三重指纹跳过未变区域）+ 周期性全量直跑**。
* **每阶段**：执行 → 3 个子代理对抗审计（代码正确性 / 用户可见效果 / 反假绿）→ 分歧必须讨论到统一 →
  本地 commit（消息里写清行为变更与证据）。⚠️ 每阶段的两类产物缺一不可：
  ≥1 条「五门禁全绿但行为坏」的**绿变异**（盲区证据）+ ≥1 条「补断言后该变异变红」的对照。
* **发布**：`v0.7.2` tag + push + release 在用户终验后执行；随后把 `.deploy` 重指到 tag 提交并重启网关。
* **门禁支数**：正式门禁 = **六支**（原五支 + `check_cls_alignment.py`；审计 C2 的 G1 实测
  图标表可被「生产表 + 冻结 JSON 同时改」绕过 ⇒ CLS 直连必须进列，且 `run_fast` 走 `--require`）。
* **P6「全量门禁」的范围与预算**（审计 A：全矩阵在负载机器上约 6.5h，必须写清口径）：
  1. **六支**门禁全量各跑一次、全绿；2. `mutate_check --delta`（三重指纹：代码区域 × 用例名集合 ×
  helper/fixture）+ 周期性全量直跑背景刷新账本（`--ledger-status`：475/475、待跑 0；**
  2026-09-21 实测**：全量直跑分 2 片并行 ≈18 分钟，另加缺口补跑 ≈7 分钟）；
  3. ≥1 条真机探针记录（本轮：加载指示「会动」+ 澄清点击 + 长回合无灰气泡 + 图标选版）。
  `--preflight`、`-k` 局部子集、单独 `check_cardview` **都不能**当最终绿。
  ⚠️ **2026-09-21 协议修订**（用户质疑全量矩阵耗时）：全量直跑**不再每版必跑** —— 见
  `docs/verify-log.md`「09-21 协议变更」：`--delta` 只跑区域变过的、`--seed-inherited <ref>`
  标可审计的继承、`--target-only` 快跑（判绿不记账）、`full_audit_at` 周期性后台刷新。

## 3. 残余项（**登记在案**，不许在阶段绿报告里抹掉）

| 残余项 | 责任人 | 需要的证据 | 截止 |
| --- | --- | --- | --- |
| 表单容器形态（`form` + `form_action_type:"submit"` + 组件 `name` + `form_value[name]` 解析）未实现 | 本仓 | 真机点一次 submit 按钮 + `check_clarify_e2e` 覆盖 | v0.7.3 |
| submitted/retry/30min TTL 三态未做（现为 pending → 原地换成已答复卡） | 本仓 | 真机 + e2e | v0.7.3 |
| 长回合（tools>20）真机复验未做 | 真机 | 日志无 `300313`/`200621` + 卡片有折叠提示 | **v0.7.2 发布前（用户终验）** |
| `show_reasoning=true` 的嵌套 `collapsible_panel` 客户端渲染未验证（默认 false 规避） | 真机 | 探针已发 `om_x100b6427ec67d4a4de74424945f4ca0`（「内层面板能展开吗」），**等用户目视结论** | v0.7.3 |
| 低层出站原语（`_ld_ck_create`/`_ld_send_card`/`_ld_update_card`）无统一留痕 | 本仓 | 调用点枚举断言 + 灰度日志 | v0.7.3 |
| `seq += 1` 的换号重试只被黄金夹具保护（C2：夹具同步重生成即失守） | 本仓 | 构造「删除是这一帧最后一次写」的场景断言 | v0.7.3 |
| `--shard i/n`（n≥2）分片跑**不会自己盖** `full_audit_at`（`_full` 要求 picked == 全量条数，n≥2 时必然不等）⇒ 分片账本必须按「当日 🔴 名字并集覆盖 + 对照**全 12 个名字** + **现场重算**三指纹」合并后才可盖章（2026-09-21 的 v3 合并脚本被审计 B 实测证明可被伪造日志绕过 ⇒ 已补加固参考实现 `merge_ledger4.py`）；另注意分片 `--update-ledger` **要各写各的 `LARKDECK_LEDGER_PATH`**（同一文件并发写会丢更新，归档日志实测 414 vs 413） | 本仓 | 把 `merge_ledger4.py` 固化进 `tools/`（含分片账本参数与自检），并在 `--shard` 结束时打印「本次只覆盖 i/n，`full_audit_at` 需合并后才可盖」 | v0.7.3 |
| 账本的**来源凭证**只在 `at`（= `_head_short()` 的 HEAD 短码，**不看脏树**）与 `_meta.full_audit_evidence`（叙述）里；合并脚本可无条件覆盖 `at`，且「伪造日志 → 盖章」无法从文本层防住。2026-09-21 审计 B 实测：CAND-B2 在 `7e7a62d` 的清单里根本不存在、`at` 却写成 `7e7a62d`（已在 `396f0ae` 干净树补跑并把这两条 `at` 改对） | 本仓 | 每条 entry 记 **tree hash（含 dirty 标记）+ 来源日志路径 + 行号**，由 `mutate_check` 自己产出；`--ledger-status` 能按来源过滤；合并器不再需要「凭日志文本盖章」 | v0.7.3 |
| 测试里可能仍有**无界等待**：`await <Event>.wait()`（一旦被测分支被变异短路，整支门禁挂到 45s 超时 ⇒ 记 💥「没有证据」而不是断言红）；**更强的一类是无界 task join**（`await frame` / `await tick` / `await holder`）—— 一个让帧路径 async 死锁的变异会以同样方式把「断言红」变成 💥。本轮只修了实测踩到的两处；2026-09-21 对抗审计 A 实测仍有 3 处裸 `await release.wait()`（`test_units.py:11730/11786/12346`，当前无语料能挂死）+ 上述 join 类 | 本仓 | 全仓扫 `await \w+\.wait()` **与** `await <Task>` 裸 join，统一改 `_await_event`/`asyncio.wait_for`；再加静态门禁（发现无界等待/join 即 FAIL） | v0.7.3 |

## 4. 风险与已知坑（沿用 v0.7.1 教训）

* 钉短码的 7 条用例是「行为变更声明」的一部分，**必须同提交改**，不许先改代码后补测试。
* 结构化卡结构在建卡时定死 ⇒ 加载指示只能「建卡插入 + 中途删元素」，不能事后加。
* 双语节点撑大卡体积 ⇒ 字节墙两套用例（legacy / structured）各自车道跑，不许混。
* `ck_elems` 是白名单，新元素（`loading_hint`）不在里面 ⇒ 状态判断用显式标志。
