# larkdeck v0.2 规划与分阶段实施方案（2026-09-13，**已经过三路审计并修正**）

> 目标：在**不改 Hermes 源码、不 monkeypatch** 的前提下，把调研中【建议做】+【可以做】的功能
> 与「CardKit 流式期间的元素级写入」（原 `plan-6-effects.md` 的阶段 10）一起做完，
> 然后**首次发布**（见附录 D），并把 Mac 本地插件同步生效。

**本文的状态**：v1 草案经**三路独立审计**（可行性 / 风险与门禁 / 范围与发布）逐条核对，
下面是**修正后**的版本。审计推翻/纠正的地方都标了「审计纠正」，我自己的真机实测与审计的
源码推断冲突时**以真机为准**并注明。

---

## 一、已确立的事实（方案的地基）

| 事实 | 证据 |
|---|---|
| 关流式会话的只有**整卡替换**（`message.patch`/`card.update`）与显式 `settings(streaming_mode=false)`；`card_element.patch/create/update`、`card.batch_update` 在流式期间**可用且不关会话**，**客户端真的重绘** | `tests/probe_ck_stream_ops.py` 矩阵 + 用户肉眼确认（新增元素 / 面板黄边 / 面板内容三条都画出来了） |
| **header 的子元素可寻址**：建卡时给 `header.subtitle.element_id="hdr_sub"` ⇒ 流式期间 `card_element.patch` 改它 `code=0` 且之后正文照写；猜的 `element_id="header"` 得 `300313` | 我的真机探针（`probe_phase0c`）。**审计纠正**：可行性审计据源码判定「CardKit 没有 header 寻址 ⇒ 大概不可做」，真机证明**可做** —— 又一次「真机为准」 |
| 元素写入**限流余量很大**：50 次/秒连打 50 次零失败（10/s、20/s 同样零失败） | 我的真机探针（`probe_phase0b`）。⚠️ 审计引用的「单卡 10 次/秒」是官方文档口径、仓库内无实测 ⇒ **预算按更保守的 10/s 算**，但真实余量远大于此 |
| 一次 `card_element.create` 的四个变体（`insert_after` panel_body / `insert_after` panel / `append` panel / `insert_before` panel_body）**接口全部 `code=0`**；但**落点在面板内还是面板外只有眼睛能判**（对比卡已发到 DM） | `probe_phase0` + 待用户一眼 |
| `card.settings` 只写 `summary` 不关流式；`summary` 必须是 i18n 对象 `{"content": …}`（传字符串得 `300122`） | 探针实测 |
| 字节硬上限 128000（160000 被拒 `230025`）；元素上限 200（**递归**数所有含 `tag` 的对象，`cards.count_elements` 已实现、已在 `fit_reply_card` 当第二道闸门）；2.0 卡 API 读回恒为 173 字节占位 | 早期真机阶梯 + `core/cards.py:674-687,759` |
| cron 的**结果投递**已经走被我们接管的 `send()`（⇒ 已经是卡）；**流式进度卡**没有公开事件源（`cron/` 内零插件钩子 fire-site） | `cron/scheduler_delivery.py:1311` → `gateway/delivery.py:305` → `transport.send` |
| `register_command` **拒绝**与内置命令冲突（含别名） | `hermes_cli/plugins.py:663-668` |
| `pre_gateway_dispatch`（我们**已订阅**）支持 `{"action":"skip"}` / `{"action":"rewrite","text"}`，在鉴权与命令解析**之前**触发；`ctx.inject_message()` 可以把一条消息重新送进内核 | `gateway/run_inbound.py:41-74,181`、`hermes_cli/plugins.py:597-627` |
| `register_approval_transport` 契约可读：`present_fn(ApprovalRequest)->ApprovalDecision`，**`ApprovalRequest` 里没有 chat 路由字段**，且跑在 8 槽有界 worker 里、必须阻塞等决策 | `hermes_cli/approval_transport.py` |
| 一帧 definitive 失败 ⇒ 核心关 native + 尽力补一帧 finalize（落到我们的收尾分支 = 整卡 patch 旧卡）⇒ **再落到 `_first_send` ⇒ DM 里第二张卡**。核心全程**拿不到我们的 message_id** | `gateway/stream_consumer_transport.py:322-337,365-385,418-436`（审计实读）|
| 上游**已经**给每一帧补未闭合围栏（`ensure_closed_code_fences`），并且**刻意**保持「帧是前缀链」 | `gateway/stream_consumer_transport.py:296-320`、`stream_consumer_fences.py:16-38` |

## 二、范围

### 做（ROI 【建议做】+【可以做】的全部）

🟢1 长回答拆分续写 · 🟢2 流式期间的元素级写入 · 🟢3 撤回/删除守卫 · 🟢4 markdown 卫生 ·
🟢5 按码分档的重试与降级 · 🟢6 卡片 summary 随进展 · 🟢7 入站心跳自检 ·
🟢8 cardkit 建实体补元素数闸门（**审计纠正**：递归计数本身**已经实现**，这里只补闸门）·
🟡9 点击即回卡 · 🟡10 澄清三态 · 🟡11 运行态标题副标题（真机已证可做）· 🟡12 页脚扩展 ·
🟡13 命令卡 · 🟡14 图片内联（默认关）

### 不做（理由已按审计修正）

| 不做 | 修正后的理由 |
|---|---|
| **接管 `/stop`** | 技术上可达（`pre_gateway_dispatch` 会 fire），但**它会把操作员的逃生口换掉**，属安全反模式 ⇒ 不做（旧理由「框架禁止」不准确） |
| **接管 `/model`、`/new` 做交互卡** | 公开路径存在（skip + 自己发卡 + 点击后 `inject_message` 把原命令送回内核），但**成本高、收益取决于你是否在飞书敲命令** ⇒ 列为 **R11 可选 spike**，不在本轮承诺（旧理由「框架禁止」不成立，已改） |
| cron / 后台任务的**流式进度卡** | 没有公开事件源；而**结果投递已经是卡**（免费拿到） |
| 群成员 SQLite + 群内权限体系 | 私人 DM 场景零收益，且引入持久化与隐私面 |
| 动态二次元台词 | 口味 + 与双语 i18n 体系重复 |
| 可视化配置写 `~/.hermes/config.yaml` | 我们只经官方 `ctx.get_config()` 只读；写回越权且与 Hermes 配置管理打架 |
| 多 bot / 多 profile | 单机单机器人用不到 |
| 照抄第三方的 28~30KB 预算 | 我们实测 128000，照抄会白丢 4~7 倍内容 |

## 三、分阶段实施

**编号用 R0–R10**（原 v1 的「阶段 10」与 `plan-6-effects.md` 里那个「阶段 10（流式元素级写入）」
撞名，审计指出后改掉）。

**每阶段的固定收口动作**（本项目老规矩）：
① 四门禁全绿（`test_units` / `check_override` / `check_hooks` / `check_clarify_e2e`）；
② 新断言各配一条变异并**逐条验红**（`mutate_check.py`）；
③ 改卡片结构 / 探针行为 ⇒ 跑真机 `probe_render.py`（默认 + `--stop-redraw` + `--cardkit-prod`）
与 `probe_ck_stream_ops.py`；
④ 阶段末由**另一个子 Agent** 做对抗性代码审计，问题清零才进下一阶段；
⑤ commit（message 附当次实测门禁数字），合适时 push；
⑥ **Mac 上必须 `hermes gateway restart` 才算生效**（网关进程内模块不会热重载 —— 审计提醒）；
回滚 = `git revert` + 重启（配置开关 `native_transport: "patch"` 只能**整条**退回老传输，
它同时放弃 R2/R3 的收益，不是逐阶段回退）。

**执行顺序**（审计建议：把不动渲染路径、见效快的提前）：

```
R0 探针 → R1 元素表基建 → R2 流式页脚+状态色 → R3 面板结构化 → R5 健壮性
→ R7 页脚扩展+summary → R9 自检+命令卡 → R6a markdown 卫生 → R8①② 交互增强
→ R10 发布   ‖ 之后按数据决定：R4 拆分 / R6b 图片 / R8③ 已并入 R2 / R11 命令卡 spike
```

### R0 探针（只加探针，不动生产行为）

**只留「真未知」**（审计纠正：cron 投递、approval 契约、header 可改性、元素计数口径、
递归计数**都能从源码/已有探针判定**，不该占探针）：

| 探针 | 问什么 | 判据 |
|---|---|---|
| P1 | `card.batch_update` 一次带 **N≥2** 个 `partial_update_element` 是否 `code=0` 且**多个元素都真的变** | 返回码 + **人眼**（2.0 卡读回是占位，只能看） |
| P2 | batch 占 **1 个 sequence 还是 N 个**（batch 用 seq=1，随后 `content` 用 seq=2 ⇒ 若 `code=0` 则只占 1 个） | 返回码 |
| P3 | `card.settings`(summary) **是否占用/校验 sequence**（三臂：不带号 / 跳号 / 撞号） | 返回码（`300317` 与否） |
| P4 | 撤回/删除后写卡的真实码表（六臂：删掉自己发的卡 → `content` / `patch`；不存在的 id；非法 id；reply 父消息被删；`batch_update`） | 打印 `(臂, 操作, code, msg)` 并去重 |
| P5 | 面板子元素**落点**（对比卡已在 DM，等一眼）：`insert_after(panel_body)` vs `append(panel)` 哪个真的落在面板**里面** | 人眼 |
| P6 | `CallBackCard` 内联换卡在**真机多端**是否生效、是否不可逆 | 人眼 + 日志 |
| P7 | 一次 `card_element.create` 能带几个元素；运行时新增元素**是否计入 200** | 返回码 + 阶梯 |

**出口条件**：每条都有明确结论（数字或可/不可），结论写回本文；探针自身必须
① 结论写成断言（数量不守恒直接失败，不许只 print）② 自动清理 ③ 清理后自校验 0 残留
④ 清点按 `message_id` 去重（推论 18）。

### R0 结论（2026-09-13 实测，全部可重跑）

| 探针 | 结论 |
|---|---|
| P1/P2 一次 `card.batch_update` 带 **2 个** `partial_update_element` | `code=0`；**单计数器 + 每次 API 调用 +1** 对 `content` / `batch_update` / `settings` **全部成立** ⇒ 「每帧 1 次 batch + 1 次正文」在接口层站得住 |
| 序号语义（新发现） | **必须严格递增**：`settings(seq=100)` 之后 `content(seq=2)` 得 **`300317`**（跳号会毒掉计数器）；同一个号发两次也得 `300317` ⇒ 规则写死为「**只 +1、不跳号、不撞号、不回退**」 |
| P3 `card.settings` 是否吃号 | **吃**（跳号臂即证明它参与同一序号空间）⇒ 与元素写入**共用**一个计数器 |
| P4 撤回码表 | **元素写入对撤回无感**（删掉消息后 `content` 仍 `code=0`，`batch_update` 同样）；**只有整卡 patch 会报 `230011 The message was withdrawn.`**；不存在/非法 message_id ⇒ `99992354` |
| P5 面板子元素落点 | 待用户一眼（对比卡在 DM） |
| P6 `CallBackCard` 多端 | 未测（R8 前测） |
| P7 一次 `create` 能带几个元素 / 运行时元素是否计入 200 | 未测（R3 前测） |

**P4 改变了 R5 的范围（诚实缩小）**：流式帧只做**元素写入**，而它对撤回**无感** ⇒
**流式期间根本无法检测撤回**。所以「撤回守卫」只能在**下一次整卡写入**（收尾帧 / `/stop` 重绘 /
`DEGRADE` 路径）时生效，价值从「每帧省配额」降到「收尾与重绘时不再白试 + 不另建卡」。
第三方 README 那套「每帧 patch 所以能及时发现撤回」的前提**不适用于我们的 cardkit 路径**。
码表只收实测的：`230011`（撤回）与 `99992354`（id 无效）——**抄来的 `231003`/`1000023` 一律不写进代码**。

### R1 元素表基建（**行为逐字节不变**）

- 交付：回合状态维护「本卡有哪些元素 + 单一下一序号」；抽出 `_ld_ck_apply(ops)` 统一处理
  sequence / uuid / 失败处置；**正文最后写**（提交点在后，与上游按「最后一次成功帧」记账同口径
  —— 审计第③条，改两行顺序、性价比最高）。
- 出口条件：**golden trace 逐字节相等**。做法（审计给的判定法，方案 v1 缺）：
  **重构前**先提交字面量夹具（`card.create` 卡 JSON + 每次 `(element_id, content, sequence, uuid)`
  + 收尾 patch 整卡 JSON + 每帧返回值），先证夹具在重构前绿；重构后必须逐字节相等；
  建实体卡 JSON 再补一条 `sha256` 断言。
- 关键规则：**序号只增不减**（失败**绝不回退**计数器 —— 回退会让下次重发撞同一个 uuid，
  服务端按去重键处理 ⇒ 可能返回 0 但内容没变 = 静默半更新）；全卡**只有一个**计数器
  （元素写入 / `card.settings` / batch 共用）。
- 断言/变异：序号严格递增（已有）、seed 起点 0、uuid 由 (卡,元素,序号) 唯一、
  **失败不回退 seq**、golden trace 相等；变异锚点见附录 B。

### R2 流式期间的页脚 + 实时状态色（第一个用户可见收益）

- 交付：建实体时**就建好** `footer` 与状态元素（审计确认：patch 已有元素是已验证路径，
  不依赖「create 后再 patch」这个未知），流式期间用一次 `card.batch_update` 更新；
  收尾仍走整卡 patch。
- **失败粒度**（审计第④条，v1 缺）：按附录 A 的矩阵 —— 装饰失败 **`DEAD` + WARNING、帧仍返回 `True`**；
  只有正文失败才 `FATAL`（fail-open）。
- 规则：`ck_footer` 跟随结构（`footer: false` ⇒ 不建也不写）；**未变化不重写**（内容摘要比较）；
  建实体**同时守字节与元素两道墙**（现在只守字节 —— 审计缺口）。
- 文档同步（审计提醒，不能压到最后）：R2 一结束就改 `README.md:300-320`、`AGENTS.md` 目录段、
  `core/cards.py:787` 注释这三处「cardkit 下流式期间看不到页脚/状态色只在收尾」的说法。

### R3 面板结构化（多元素 / 每工具一行 / 批次）

- 规则：**id 规约机械化**（`^[A-Za-z0-9_]{1,20}$` 且互不相同，断言**从元素表派生**）；
  本地不再渲染的元素**同一事务里标 dead**（发送集合与渲染集合同源）；
  元素预算 = 200 − 收尾余量，与 `fit_reply_card` **同源**；`create` 成功前不得写它。
- 规避未验证语义（审计第⑥条）：优先用**已验证的顶层 `insert_after`**；「往面板里 `append`
  子元素」以 P5 的肉眼结论为准，不做前提。

### R5 健壮性（撤回守卫 + 按码分档 + cardkit 元素闸门）

- **审计纠正**：递归计数与限流重试车道**已经实现**，本阶段只做：① 撤回守卫（码表由 P4 产出）
  ② 按码分档（`DEGRADE` 新增：卡级死法转 patch 传输续写同一张卡，**不新建卡**）
  ③ cardkit 建实体补元素闸门 ④ 缓存有容量上界。
- 规则：**码表必须有实测出处**（注释写清探针+臂+码），未实测的码**不许**进致命/可重试表；
  撤回 ⇒ 标死 + pop state + **绝不另建卡**。

### R7 页脚扩展 + summary 进展

- **审计纠正**：成本**实报不可能**（`post_api_request` 载荷没有成本字段）⇒ 只能估算，
  且必须带「估算」字样；cache 命中率 / API 次数**可直接算**（载荷有 `cache_read_tokens` 等）。
  若要引 `agent.usage_pricing` 就是新的 Hermes 耦合 ⇒ 按不变量 3 必须登记进 `compat.py`。
- 规则：序号与元素写入**共用**一个计数器（P3 无论怎么答都安全）；summary 是 i18n 对象；
  降频（不是每帧）。

### R9 自检 + 命令卡

- 交付：入站心跳（**复用** `pre_gateway_dispatch`，不新增订阅）+ `/larkdeck status` 命令卡。
- **审计纠正**：① 命令输出**确实**经过我们的 `send()` ⇒ 自动成卡（源码已核）；② 但插件命令
  **不在 `COMMAND_REGISTRY`** ⇒ **忙碌态敲不生效**（会排队）—— 文档必须写明「仅空闲态可用」；
  ③ 自检**不能只报心跳**（用户不说话时心跳照旧「新鲜」⇒ 绿而无判别力），必须三条一起报：
  最近一次成功写帧 / 最近一次帧失败原因 / 最近一次入站。

### R6a markdown 卫生（纯函数，半天）

- **审计纠正**：上游**已经**补未闭合围栏 ⇒ **不要重复补**；`**` 闭合与 H1–H3 降级**破坏前缀链**
  ⇒ **只允许出现在收尾帧**（并配一条「流式帧文本是前缀链」的断言）。
- 规则：卫生函数**纯函数 + 幂等**（`f(f(x))==f(x)`）；所有字节闸门量的是**变换后**要发出去的文本
  （推论 13 的口径病）；表格超限降级为字段列表。

### R8①② 交互增强（点击即回卡 + 澄清三态）

- 规则：回调路径**零 await、零网络、零锁等待**；内联卡构造失败**必须保持原卡**（不抛）；
  三态转换**幂等**；**运行态标题只走元素级接口**（patch/card.update 会关流式会话）。
- **审计第⑤条**：官方 `_card_response` 是私有方法且**带 `card_data` 形参**，compat 里只登记了
  **名字**、没登记**签名** ⇒ 补签名探测，缺失时退回无参调用（= 不换卡但仍正确）。
  ⚠️ 该方法的无参调用是 `_ld_card_response_safe`，单行锚点 count=9 ⇒ 变异锚点必须带上下文。

### R10 发布（附录 D）

## 实施进度

| 阶段 | 状态 | 证据 |
|---|---|---|
| R0 探针 | ✅ 完成（P1–P4 有结论；P5 待用户一眼，P6/P7 排到 R8/R3 前） | 见「R0 结论」表 |
| R1 元素表基建 | ✅ 完成（成功路径 golden trace 逐字节相等；失败路径**有意**新增「序号只增不减」） | `tests/golden_cardkit_trace.json` · 变异 98/98 全红 · 真机 `--cardkit-prod` 6 次元素写入不变 |
| R2 起 | 未开始 | |

## 附录 A：失败处置矩阵（R1/R2/R3/R5 的公共规则）

| 处置 | 代码做什么 | 本帧返回 | 用户看到什么 |
|---|---|---|---|
| `RETRY` | 退避（0.1/0.3/0.6）后重建请求重发（只对**限流类** `230020/99991400`） | 继续 | 无感（只是慢） |
| `DEAD` | 该 `element_id` 进 `ck_dead`，跳过它继续其余 op + 限流 WARNING | `True` | 卡继续逐字，只是那段装饰冻结 |
| `DEGRADE` | 本卡标 `transport=patch`，本帧与后续改走 `_ld_build_card` + `_ld_update_card`（**同一张卡**） | `True` | 还是一张卡，只是没有打字机了 |
| `FATAL` | `_ld_stream_fail(...)` 交核心回落 | `False` | **DM 里会出现第二张卡**（上游已知代价，README 必须写明） |
| `DROP_CARD` | 标死 + pop state + 不另建卡 | `False` | 消息已撤回，用户看不见；我们不再往那条消息写 |

| 码类别 ＼ 角色 | 正文 | 内容型装饰（面板） | 纯装饰（页脚/色/summary） | 结构（create/delete） |
|---|---|---|---|---|
| 限流 `230020/99991400` | RETRY → 耗尽 DEGRADE | RETRY → 耗尽 DEAD | RETRY → 耗尽 DEAD | RETRY → 耗尽 DEAD |
| 会话已关 `300309` | DEGRADE | DEAD + 卡级标记 | DEAD + 卡级标记 | DEAD + 卡级标记 |
| 元素不存在 `300313` | DEGRADE | DEAD | DEAD | DEAD |
| 序号冲突 `300317` | DEGRADE（卡级） | DEGRADE | DEGRADE | DEGRADE |
| id 错 `300301` / 确定性拒收 `230025/230099/200621/230001` | FATAL | DEAD | DEAD | DEAD |
| 撤回类（**待 P4 实测**） | DROP_CARD | DROP_CARD | DROP_CARD | DROP_CARD |
| 未知码 | FATAL + WARNING | DEAD + WARNING | DEAD + WARNING | DEAD + WARNING |

## 附录 B：每帧写入预算

```python
_CK_WRITES_PER_SECOND = 10                      # 官方口径；**真实余量远大于此**（实测 50/s 无失败）
_CK_WRITES_PER_FRAME = max(1, int(_CK_WRITES_PER_SECOND * _STREAM_MIN_INTERVAL))   # 0.25s ⇒ 2
```
一次 `card.batch_update` 承载「面板 + 页脚 + 状态色」，**正文放最后一个 action**（提交点在后）。
断言两条：① 等式（由常量算出来）；② **观测量**（3 帧用例里实际元素级调用数 ≤ `_CK_WRITES_PER_FRAME × 帧数`）。
⚠️ `_STREAM_MIN_INTERVAL` 在文件里出现 **2 次** ⇒ 变异锚点必须带上下文（推论 17）。

## 附录 C：需要你（用户）看的几眼

1. **面板子元素落点对比卡**（已在 DM，P5）：决定 R3 能否做成「面板内多元素」。
2. 逐字打字机一眼（R2 之后的常规确认，沿用老习惯）。
3. R8 的内联换卡与 R2 的「每帧 patch 面板是否引起动画重放」（只有眼睛能判）。

## 附录 D：发布清单

**现状（审计实读）**：`gh` 2.96.0 已登录 `zayn-0101`（有 `repo` scope）、远端是 https、
本地=远端、**0 tag / 0 release**、`CHANGELOG.md` 不存在、`plugin.yaml` 缺
`license`/`homepage`/`tags`、`docs/` 没有索引文件、README 无版本段。

**计划**
1. 先给当前 HEAD 补一个 **annotated tag `v0.1.0`**（不建 Release）：得到可回退的基线，
   且 `git diff v0.1.0..<发布提交>` 就是 CHANGELOG 素材（且与 `plugin.yaml` 的 0.1.0 对齐）。
2. 程序做完后发布 **v0.2.0**（不是 1.0.0）：**1.0.0 是「配置键与卡片形态不再破坏性变化」的承诺**，
   而本轮翻转了两个默认值、还有多条待探针/WIP 项 ⇒ 0.2.0 更诚实。
   **1.0.0 的判据**（写进 CHANGELOG）：配置键零改名 + 无默认值翻转 + 跨过一次上游升级。
3. `CHANGELOG.md`：按 Added / Changed / **Breaking（默认值翻转单列）** / Fixed 分组，
   每条附**当次实测**门禁数字。**发布前跑一次全量门禁 + 真机探针**（v1 只写了文档复核）。
4. tag 用 annotated，`git rev-parse v0.2.0^{commit}` == 发布提交，发布前 `git status` 必须干净。
5. Release 说明**手工写**（**别用 `--generate-notes`**：commit body 全是内部审计叙事），
   发布前对正文跑 `grep -E '(oc_|ou_|om_|cli_)[A-Za-z0-9]{6,}'`（仓库公开）。
6. 补 `plugin.yaml` 的 `license: MIT` / `homepage` / `tags`；README 加版本段与「首次安装 vs 升级」；
   `docs/` 加索引；NAS 状态**继续如实标 pending**。
7. **插件版本自报**：启动自检只报 Hermes 版本 ⇒ 机器上无法证明在跑哪个版本。补一条
   「插件 vX.Y.Z」+ 一条「自报 == `plugin.yaml`」的机械门禁。

## 附录 E：洁癖收尾（六面）

| 面 | 落点 | 由谁覆盖 |
|---|---|---|
| 代码 | 五门禁 + 变异 + 配置三处同步 + `install.sh FILES` 自校验 | 各阶段自动 |
| 运行态 | Mac 软链 = 工作树，**必须重启网关**；自检自报传输与（新增）版本 | R10 + 每阶段重启 |
| 文档 | README / AGENTS / `plan-6-effects` / `plugins-compare` / 本文件；**R2 后立即同步一次** | 分阶段 + 收尾一轮 |
| 规则 | AGENTS.md 的探针清单、双清单（钩子）一致性 | 收尾一轮 |
| 记忆 | 按规矩**无授权不写** ⇒ 显式标 `pending`/`not-applicable` | 收尾一轮 |
| 工作区 | 本文件入库 + `design/` + `.probe_state.json` + `__pycache__` + **DM 里的探针残留** | 收尾一轮（清单交用户确认后再删） |

## 附录 F：审计留下的「核对不了」清单（不许猜，逐条由探针或人眼回答）

元素级接口真实配额（我用 50/s 实测过一轮，但**上限**仍未知）· 运行时新增元素**是否**计入 200 ·
`uuid` 去重遇到**不同内容**的语义 · 上游在同一回合内是否会**重新启用** native（跨边界状态机我只
读到一层）· `turn_id` 与钩子数据的对齐关系 · 面板 `append` 的落点 · `CallBackCard` 多端语义 ·
NAS 现网状态（`docs/switch-from-hfc.md` 自述 2026-09-12 连不上）· 官方文档站四个域名在本环境
**DNS 被拒**（所以凡官方 schema 都只有 SDK 生成模型 + 真机实测两类证据）。
