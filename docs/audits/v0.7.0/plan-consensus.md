# v0.7.0 计划统一结论（A/B/C 收敛 + 父级裁决）

- 计划：`docs/plan-v0.7.0.md`（本结论生效后重新冻结）
- 冻结基线：`29ecc83b38cc13577e82e180ca571031c74f71db`（v0.6.4）
- 参与：A（架构/证据）、B（用户可见/回归）、C（执行风险/反假绿）
- 轮次：3 份独立计划审计 → C 提出条件 → A/B 逐条收敛 → B 抛 `_adopt_final_text` 反例 → C 最终裁决 → 父级裁决 SLOC 绝对阈值
- 结论：**GO-WITH-CONDITIONS**。条件全部落为本文件的 P0.5/P1/P1.5/P2a/P2b/P3/P4 门禁；未满足时不进入下一阶段、不发布。

## 0. 优先级（统一）

**安全 > 不丢消息/不吞正文 > 不泄漏进度 > 外观不退化 > 行数/速度。**

任何数字门禁（行数、条数、耗时）与以上优先级冲突时，以优先级高的为准；数字只能记录、投影和解释，不能反过来逼实现删安全代码或注释。

## 1. 已达成一致

### 1.1 架构方向（A/B/C 一致）

正文唯一来源改为插件 `on_stream_delta(kind="text")` 累积；core `send_stream_frame` 文本只作刷新信号、finalize 兜底、无原生流回落。工具进度由 core 合并在帧里，本架构下 `finalize=False` 不再读 `text`，从结构上消除“每出现一种核心帧形状打一个补丁”。

删除对象（P2b）：`_CORE_PROGRESS_SEP/_CORE_PROGRESS_CURSOR/_CORE_PROGRESS_DEDUP_RE/_CORE_PROGRESS_HEAD_RE/_CORE_TOOL_VERBS/_CORE_PROGRESS_VERBS/_VERB_TO_TOOLS/_CORE_PROGRESS_STATUS/_FOR_VERBS`、`_strip_core_progress_cursor/_is_core_progress_header/_looks_like_core_progress_only/_strip_core_progress`、`_ld_body_text` 的剥帧逻辑，以及对应测试/变异/诊断。删除前后必须有 P2b 归档跑与 tombstone 跑证明。

### 1.2 finalize 规则（C 最终撤回“更长优先”，A 的 rsplit 撤回）

**绝不 `split/rsplit` 分隔符选正文，绝不拼接/排序，整段选一。**

```
core_text 非空  -> 用 core_text 整段（权威终稿；_adopt_final_text 可能整体替换且更短）
core_text 为空  -> 用 own 累积
```

- 分叉只记日志：`both_len`、`first_divergence`、`core_prefix_of_own`、`own_prefix_of_core`、`equal_len_divergence`；等长分叉取 core（投递权威）。
- own 可能因 1024 丢最旧而从中间缺块，长度不能证明完整性；core 是权威投递文本，非空即整段采用。
- **F4 异常重发帧是唯一例外路径**：core 帧确定性失败后会用同一段 interim 合成文本再发 `finalize=True`（`gateway/stream_consumer_transport.py:385-391`）。C 静态证实该合成文本可含未脱敏 terminal 命令/args：
  - `gateway/run_turn_runner.py:210` 直接取 `args["command"].rstrip()`；
  - `:215-217` verbose 全文、all/new 首行截 40 字符（截断不是脱敏）；
  - `:238` verbose 非 terminal 分支 `json.dumps(args, ...)` 入队；
  - `gateway/stream_consumer.py:241-244` 拼进帧尾。
- **处理（语义识别，不用形状猜测）**：`send_stream_frame` 帧失败时在本插件 stream state 记 `last_failed_frame=text` 并保留状态；紧接的 `finalize=True` 帧若与该失败帧文本相同，判定 F4 重发，**拒绝持久化该合成帧（返回 False）**，记 `finalize_resend_rejected` 证据。core 随后的同 tick `edit_message(finalize=False)` 在 own 模式下改为渲染严格绑定的 own 累积（不渲染传入的合成帧文本），最终 `edit_message(finalize=True)` 用 core 权威终稿补齐全文。F4 安全门禁：terminal 命令含密钥、verbose args JSON 含密钥时，最终卡正文不得出现原文。
- 若未来实测发现 F4 拒绝会导致正文丢失，回退顺序：core 全文（脏但完整）> 拒绝导致丢消息；不得静默丢正文。此分支未真机验证，列入 §4。

### 1.3 `on_stream_end`（A/B/C 一致：只能是第 8 观察钩子）

- 订阅原因：对账 own 是否缺尾、F4 判定辅助、末段丢失告警。
- 不能做唯一事实源的依据：`agent/chat_completion_helpers.py:2243-2259` 每次 API 调用都会 fire，`final_text` 是单次 response content；未必是整回合文本；仍是 raw content（未过 gateway 后处理）；仍走 `agent/plugin_stream_hooks.py` 的 1024 丢最旧队列。
- 实现：回调只写状态字典 `(session_id, turn_id, iteration, finished, final_text)`；只记录 own 与 `final_text` 的 prefix/长度/分叉诊断；**不直接拼入正文、不触发重渲染、finalize 之后到达只记日志**。
- P0.5/P1 先注册探测但不加入 `compat.OBSERVED_HOOKS`（避免状态卡“钩子 N/7”变化破坏 G1 legacy golden）；P2a 再入 `OBSERVED_HOOKS` 并按枚举 diff 重生成 golden。TODO 与 inventory 同步登记。

### 1.4 绑定、占位、`/stop`（A/B/C 一致）

- 新增严格绑定读取：only `chat_id -> session_id` 且 TTL 有效才返回 own；无绑定时 own 视为空。
  - `finalize=False`：正文 = own（可为空）→ 展示层 `answer_or_pending` 给唯一占位；
  - `finalize=True`：core 非空走 core，core 空走（严格绑定的）own；
  - `progress_lines_in_body` 与 own 的关系在 P1 明确写入文档（默认 false；own 模式不读 core 帧）。
- `_ANSWERS_LAST` 回退在 own 模式禁用；legacy 模式保持旧行为直到 P2b。
- 占位只存在展示层：不得进入 `config.summary`、`last_text`、`ck_offset`、`_MAX_TEXT_ENTRIES`、`_ld_note_text`、`last_rendered_body`。
- `/stop`：新增独立 `last_rendered_body`（每张当前卡**实际写出的 visible slice**，尊重 `ck_offset`）；不改 `last` 的 throttle/相等语义。`/stop` 只读 `last_rendered_body`，无则按严格绑定取 own；无绑定不得回退 `_ANSWERS_LAST`。变异覆盖首帧建实体、元素写、patch 三支；“/stop 退回读 `state['last']`”必须红。
- 流状态新增 `session_id`；帧/切卡/`/stop` 校验 chat→session 归属，中途变更则 fail-open 到 core 或占位，禁止把新会话累积接到旧卡 `ck_offset`。
- 长答案卡链：`last_rendered_body` 存当前卡 visible slice，`/stop` 不得把整段 `last` 重放进最新卡。

### 1.5 行数与门禁（父级裁决）

A 测 SLOC=4,659、B 测 4,929、C 测 4,659；物理行均为 10,091。差异根因未定位，**先冻结唯一计数脚本，再定数字**：

1. P0.5 提交 `tools/count_core_sloc.py`（C 的口径：`splitlines`；blank=空行；comment_only=`lstrip().startswith('#')`；docstring 行由 `ast.walk` 收集 Module/ClassDef/FunctionDef/AsyncFunctionDef 的首个 `Expr(Constant(str))` 的 `lineno..end_lineno`；SLOC=total-blank-comment-doc）。
2. 记录基线（physical、blank、comment、doc、SLOC、每文件）到 `docs/audits/v0.7.0/sloc-baseline.json`。
3. P1 完成、P2a 删除前做 Phase 2 投影；**P2b 门禁 = 删除启发式后 SLOC 不高于基线，并给出 before/after 表**；物理 `wc -l` 只记录不设绝对上限（删光注释/docstring 后仍有 5,884 行，物理 ≤5,000 不可达；不得为凑数删注释/docstring/搬模块）。
4. 若投影允许，建议目标 SLOC ≤4,600（A 估净落点 4,520–4,580）；不满足时按第 3 条如实报告，不阻塞发布。

### 1.6 变异双轨与防误删（A/B/C 一致）

- **日常 smoke ≤60 条**，必须由机器可读 inventory 证明覆盖 B 列的 13 类高风险族（finalize 不吞尾、进度不入正文、/stop、无绑定/跨会话、占位生命周期、卡片失败回落、卡链/offset、颜色字号、页脚/边框、澄清、session 预览、finalize 卫生、CardKit 打字机/关流）+ C 的 F4 安全族。60 条若单进程 15–17s/条必超 600s：采用 4 分片并行后 wall-clock ≤600s；顺序跑则记录 ≤1,100s，不得为压时间删保护。
- **发布全量**：冻结候选上 active+archive 分片跑，EXIT=0、红数=freeze 前 `expected-kill` 清单、0 崩溃/绿/漏；单独记录每片 wall-clock。
- **归档跑**：P2b 删除任何旧代码/测试/变异前，先在冻结 base 上跑一遍全量旧变异（393+7），EXIT=0 且红数=393，日志+sha 入 `docs/audits/v0.7.0/`。
- **inventory**（P2a 产出）：id、文件、锚点 hash、期望门禁、类别、真实事故出处（CHANGELOG/verify-log/真机日期）、最后全量结果 sha、keep/daily/archive/rewrite、替代者 id、删除理由。无“事故/不变量”字段不得删除。
- 一票否决族（任何一族不得删到 0）：fail-open/接管；安全脱敏；回调/澄清静默；卡片字节/元素墙/写入预算；配置/兼容探测；正文累积与 finalize（含会话归属/无绑定）。
- 任何删除必须有等价或更强的替代变异；没有替代只能 archive，不能 delete。

### 1.7 阶段顺序与门禁（A/B/C 一致）

| 阶段 | 内容 | 门禁 |
|---|---|---|
| P0.5 | 冻结本结论+计划+变异清单 hash；提交计数脚本与基线；注册 `on_stream_end` 探测（暂不入 OBSERVED_HOOKS）；记录四门禁基线/耗时 | 基线 JSON + 四门禁可复现 |
| P1 | 新增 own-accumulation +严格绑定 + finalize 规则 + F4 拒绝 + `last_rendered_body`，`body_source=legacy|own` **默认 legacy**；旧测试一条不动、旧断言不改 | G1：legacy/own 各自跑四门禁（只参数化 smoke/own 专属集）；真 core `_compose_frame_content` 帧语料非 finalize 无进度字节；legacy golden 逐字节不变（`on_stream_end` 不计入 hook 数）；新增测试全绿 |
| P1.5 | 默认切 own；/stop 读 `last_rendered_body`；无绑定严格 fail-open；占位不落账；F4 edit_message 保护 | G2：四门禁；开关可回退 legacy；own 用户可见 matrix（finalize 逐字节不丢、/stop 干净+变色、无绑定、占位边界、卡片失败回纯文本、长卡链不重放、F4 密钥不泄漏）；真机 canary 一次含工具回合；分叉/拒绝计数上状态卡 |
| P2a | 只改调用点/新增 inventory，不删旧代码；golden 按有意变更重生成并人工审 diff；`on_stream_end` 入 OBSERVED_HOOKS | G2 全绿 + inventory schema 与事故映射评审 |
| P2b | 先归档跑全量旧变异留 sha，再删除启发式/旧测试/旧变异；提交 SLOC before/after 投影 | G3：归档 EXIT=0；四门禁；smoke 60（并行 ≤600s 或顺序 ≤1,100s）；替代变异全红；SLOC 不高于基线 |
| P3 | 收口验证：真实核心帧 corpus、/stop、队列溢出、无绑定、F4 安全、legacy/own golden | 全部门禁 EXIT=0，日志/sha 入 audits |
| P4 | 发布 v0.7.0、部署 Mac 网关、用户一次桌面截图 | 截图确认“可以”前不宣称修复完成 |

## 2. 最终裁决记录（给反对意见留痕）

- **A 的 rsplit 建议**：撤回。B/C 反例成立，且 A 自己指出 `_adopt_final_text` 可能用权威清理后更短的终稿整体替换 `_accumulated`。裁决：禁止任何分隔符切片。
- **A/C 的“分叉取更长”**：C 最终撤回，改 core 非空整段优先。裁决依据：`gateway/stream_consumer.py:660-672` 权威替换可让 core 更短；`plugin_stream_hooks.py` drop-oldest 让 own 更长也不能证明更完整；A 的 `core_prefix_of_own` 日志标记纳入分叉日志。
- **C 的 F4“难看但完整”**：B 要求先排除密钥泄漏；C 补查后确认会泄漏；裁决改为“F4 拒绝持久化合成帧 + own 模式 interim edit 不渲染 core 合成文本 + core final fallback 保证全文”。
- **C 的 SLOC ≤4,500 / 物理 ≤9,000**：撤回具体数字，改为“冻结脚本 + 基线 + Phase 2 投影 + SLOC 不高于基线”；物理只记录。
- **B 的 10 分钟**：接受为 smoke 并行目标；顺序全量单独计时，不因时间删保护。
- **A 的 golden 冲突反例**：成立。P1 不把 `on_stream_end` 加入 `OBSERVED_HOOKS`，P2a 再入并重生成。
- **异议**：无未解决高风险异议。以上均为父级书面裁决，可被真机反例推翻；推翻需新证据并重开统一轮。

## 3. 未验证清单

- 所有测试/门禁/变异本轮均未运行；计划阶段结论均为静态读码 + 计数 + 构造示例。
- `on_stream_end` 运行期频率、队列丢事件行为、与 finalize 的真实先后未实测。
- F4 拒绝策略的实际 fallback 送达内容、密钥不泄漏未真机验证。
- slot/耗时 15–17s/条为历史 verify-log 推算，非本次实测。
- B 与 C 的 SLOC 差异根因未定位；以冻结脚本为准。
- CardKit header/border 实时更新、Working 心跳仍为未验证探针项，不计入 v0.7.0 已还原。
