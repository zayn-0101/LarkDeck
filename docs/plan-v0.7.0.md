# LarkDeck v0.7.0 架构重构计划

> **执行状态（2026-09-18）**：Phase 1（own 双路径 + 三方审计修复）与 Phase 1.5（默认 own、
> 用户桌面截图确认）已完成，快速门禁全绿。Phase 2（归档旧全量变异、删除 legacy/启发式、
> smoke ≤60）尚未执行，列为 v0.7.1 收口项。
>
> 本计划已过 3 个独立子代理对抗审计（A 架构/证据、B 用户效果、C 执行/反假绿），
> 分歧按「反例实验 > 安全优先级 > 父级书面仲裁」收敛，统一结论见
> `docs/audits/v0.7.0/plan-consensus.md`。执行结束后再各过 3 个独立子代理对抗审计；
> 不通过不发布。用户最终只做**一次**桌面静态截图确认。

## 1. 背景与问题

目标一直是：**自研、可控、稳定、简洁优雅**的飞书卡片插件；大路线（官方
`register_platform` + 官方 hooks，不改 Hermes 源码）没有变。

但截至 v0.6.4：

- `core/` 生产 Python 约 10,091 物理行（其中注释+docstring 4,207；冻结 SLOC 口径基线见 P0.5）；
- `tests/` 约 18,495 行，变异 393 条 + 7 对照，一次全量几十分钟到数小时；
- 真实问题反复：单行 terminal、多行搜索、空白累积 + 前导 `---` —— 每出现一种
  core 帧形状就打一个补丁。根因不是路线，而是**实现策略**：

  > 插件把 core 已经合成好的整帧文本当成渲染输入，再事后猜“哪段是进度、哪段是正文”。

  这个输入不是稳定契约：core 可以随时改变合成方式。于是补丁永远追不完。

## 2. 目标（v0.7.0，只做重构，不加新效果）

1. **正文来源改为插件自己的正文累积**：`on_stream_delta(kind="text")` 是我们和 core
   正文同源的公开观测点；正文只渲染这份累积，core 的 `send_stream_frame` 文本只当
   “需要刷新卡片”的信号、finalize 兜底、无原生流回落。工具进度从结构上不可能进入正文。
2. 删除所有“空累积形状猜测/前导分隔符/verb 精确表/光标”启发式补丁及其测试/变异。
3. 行数：先冻结唯一计数脚本 + 基线 + Phase 2 投影；删除启发式后 **SLOC 不高于基线**，
   物理 `wc -l` 只记录不设上限；禁止删注释/docstring/搬模块冲数（物理 ≤5,000 已证不可达）。
4. 变异双轨：日常 smoke ≤60 条且覆盖全部高风险族（4 分片并行目标 wall-clock ≤600s；
   顺序跑记录 ≤1,100s）；发布前在冻结候选上分片跑全量并留 expected-kill 与 sha。
5. 外观与功能不退化：面板折叠摘要、思考/工具行与颜色、字号层级、状态页脚、澄清、
   状态色边框、卡片链都保持。
6. 发布 v0.7.0、部署 Mac 网关，用户最终只做一次桌面截图确认。

非目标：不新增 CLS 之外的效果；不改 Hermes 源码；不改官方接管方式；不追求
“所有历史变异都保留”；不把 CardKit header/border 实时更新、Working 心跳算作已还原。

## 3. 目标架构

### 3.1 两套输入，职责分离

| 输入 | 来源 | 用途 |
|---|---|---|
| `body_accumulated` | `on_stream_delta(kind="text")` 增量拼接（严格 chat→session 绑定） | **正文唯一来源（非 finalize）** |
| `frame_signal` | `send_stream_frame(text, finalize, ...)` | 刷新信号、finalize 权威兜底、无原生流回落 |
| `panel_snapshot` | `pre_tool_call` / `post_tool_call` / `on_stream_delta(kind="reasoning")` / `on_session_end` | 面板、工具行、状态色、页脚 |
| `stream_end_reconcile` | `on_stream_end`（每次 API 调用） | 只做 own 缺尾对账/告警，不决定正文 |

### 3.2 帧路径语义

- `finalize=False`：
  - `body = own_accumulated`（严格绑定；为空时展示层给 `⏳ 正在生成…` 占位）；
  - **完全不读** `text` 里的正文/进度内容；
  - 面板/页脚照常从 hooks 快照渲染。
- `finalize=True`：
  - **core 文本非空 → 整段用 core**（`_adopt_final_text` 的权威终稿可能比 own 短，
    但它是投递权威；own 可能因队列 drop-oldest 缺中段，长度不能证明完整性）；
  - **core 为空 → 用严格绑定后的 own**；
  - **绝不 `split/rsplit` 分隔符选正文**；分叉只记 `both_len/first_divergence/前缀标记`；
  - 没有正文时写空（不保留占位）。
- F4 异常重发（core 帧确定性失败后用同一 interim 合成文本再发 `finalize=True`）：
  - 插件帧失败时在 stream state 记 `last_failed_frame`；紧接相同文本的 finalize 判定为 F4；
  - **拒绝持久化该合成帧（返回 False）**，记 `finalize_resend_rejected`；
  - core 同 tick 的 `edit_message(finalize=False)` 在 own 模式下渲染 own 累积（不渲染合成帧文本）；
  - 最终 `edit_message(finalize=True)` 用 core 权威终稿补齐全文；
  - 原因：静态读到 core 进度行可含未脱敏 terminal 命令/args（`run_turn_runner.py:210/238`），
    「难看但完整」不可接受；安全 > 完整。
- 原生流不可用/回落 `send()` / `edit_message`：finalize 仍按本节规则；卡片失败回落纯文本，
  方向与现在一致，绝不丢消息。

### 3.3 钩子滞后与队列（不可当作“最多滞后一帧”）

- `agent/plugin_stream_hooks.py` 每回调队列 1024、满时 drop-oldest；中间事件可能永久丢失。
- `on_stream_end` 与 `on_stream_delta` 是**不同队列**，跨 hook 无顺序保证；回调可能晚于
  finalize 到达。晚到只对账/记日志，**不二次渲染**、不覆盖 `last_rendered_body`。
- 末段不丢的主链是 finalize 的 core 全文优先；`on_stream_end` 只用于告警与 F4 判定辅助。

### 3.4 代码落点

- `core/panel.py`：新增严格绑定读取（`chat→session` 且 TTL 有效）；`on_stream_end` 对账
  存储与只读诊断；保留 `record_answer_delta`/`answer_state` 但 own 模式不再走 `_ANSWERS_LAST`。
- `core/hooks.py`：新增 `_on_stream_end` 订阅（P1 暂不入 `compat.OBSERVED_HOOKS`，P2a 再入）。
- `core/adapter.py`：新增 `body_source=legacy|own`（P1 默认 legacy，P1.5 默认 own）；
  `_ld_render_body(frame_text, chat, finalize)` 取代 `_ld_body_text` 的剥帧逻辑；
  stream state 新增 `session_id`、`last_rendered_body`、`last_failed_frame`；
  `/stop` 读 `last_rendered_body`；own 模式 interim `edit_message` 渲染 own。
- `core/cards.py`：占位只在 `answer_or_pending` 展示层，保持现状并加边界断言。
- P2b 删除：§1 列出的常量/函数/诊断及对应测试/变异；旧路径先归档后删除。

## 4. 分阶段实施

### P0.5：冻结与基线
- 冻结本计划 + `plan-consensus.md` + 变异清单 hash；
- 提交 `tools/count_core_sloc.py`，输出 `docs/audits/v0.7.0/sloc-baseline.json`（total/blank/
  comment/doc/SLOC/每文件）；
- 注册 `on_stream_end` 探测回调（不改 `OBSERVED_HOOKS`）；
- 记录四门禁基线、单条耗时、全量耗时；
- 产出 `docs/audits/v0.7.0/phase-0.5-baseline.log`。

### P1：正文来源双路径（默认 legacy，旧测试一条不动）
- 实现严格绑定读取、own 累积渲染、finalize 规则、F4 拒绝、`last_rendered_body`、
  `on_stream_end` 对账；
- `body_source=legacy|own`，默认 legacy；旧断言/旧 golden 不改；
- 新增定向测试（见 §5）；
- **G1**：legacy 与 own 分别跑四门禁（只参数化 smoke/own 专属集）；真 core
  `_compose_frame_content` 帧语料下非 finalize 正文无任何进度字节；legacy golden
  逐字节不变（`on_stream_end` 不计入 hook 数）；F4 密钥不泄漏测试。

### P1.5：默认切 own
- 默认改为 own；`/stop` 读 `last_rendered_body`（尊重 `ck_offset`，不重放封头段）；
- 无绑定严格 fail-open：非 finalize 占位、finalize 走 core；占位不落
  `summary/last_text/ck_offset/_MAX_TEXT_ENTRIES/_ld_note_text/last_rendered_body`；
- **G2**：四门禁 + 开关可回退 + own 用户可见 matrix（finalize 逐字节不丢、/stop 干净变色、
  无绑定、占位边界、卡片失败回纯文本、长卡链不重放、F4 安全）+ 真机 canary（一次带工具回合）
  + 分叉/拒绝计数上状态卡。

### P2a：收口调用点，不删旧代码
- 所有调用点只走新入口；`on_stream_end` 入 `OBSERVED_HOOKS`；
- 生成机器可读 mutation inventory（事故出处、锚点 hash、keep/daily/archive/rewrite、替代者）；
- golden trace 按有意变更重生成并人工审 diff；
- 记录 Phase 2 SLOC 投影。

### P2b：先归档，后删除
- 在冻结 base 上跑全量旧变异（393+7），EXIT=0、红数=393、日志+sha 归档；
- 删除 §1 启发式与对应测试/变异，删除 legacy 路径（v0.7.x 内不得长期两套真相）；
- **G3**：归档跑 EXIT=0；四门禁；smoke ≤60（并行 ≤600s，顺序记录 ≤1,100s）；
  替代变异全红；SLOC 不高于基线；一票否决族任一不得删到 0。

### P3：收口验证
- 真实核心帧 corpus（terminal/搜索/Working/光标/多行/前导分隔符/空白累积）；
- `/stop`、队列溢出丢事件、无绑定、F4 安全、legacy/own golden、卡片失败回落；
- 全部日志/sha 入 `docs/audits/v0.7.0/`。

### P4：发布与部署
- 更新 CHANGELOG、release notes、plugin.yaml → 0.7.0；
- Conventional Commit + tag + GitHub Release；
- `hermes gateway restart` 部署 Mac；
- 用户在桌面飞书做唯一一次最终截图确认（正文/面板/颜色/字号），明确说“可以”后才宣称完成。

## 5. 验收标准

1. 非 finalize 正文只可能来自 own 或占位：不出现任何 core 进度行、分隔线、terminal 围栏；
2. finalize 与回落路径正文一个字不丢（core 非空整段采用；core 空回 own）；
3. F4 已知降级路径的 terminal 命令/args 密钥不出现在最终卡正文；
4. `/stop` 渲染最后**已渲染的 visible slice**，不出现进度、不重放封头段；
5. 无绑定/绑定漂移不串会话、不串答案、不串工具名单；
6. 占位不落任何账本/摘要/offset/last_text 字段；
7. 删除启发式后 SLOC 不高于冻结基线，注释/docstring 保留；物理 wc-l 只记录；
8. smoke ≤60、四门禁；发布全量分片 EXIT=0、红数=expected-kill、0 崩溃/绿/漏；
9. Mac 网关加载 0.7.0；用户桌面截图确认面板/颜色/字号与正文干净，并明确说“可以”。

## 6. 风险与回退

| 风险 | 缓解 | 回退 |
|---|---|---|
| own 钩子落后/丢中段 | finalize core 全文优先；on_stream_end 只告警 | core 兜底；F4 走 core final fallback |
| F4 合成帧含敏感 terminal 内容 | 拒绝持久化 + interim edit 不读 core 文本 | 若拒绝导致丢正文，改 core 脏但完整并升级脱敏 |
| 无绑定/绑定漂移串会话 | 严格绑定 + stream state session_id 校验 | fail-open 占位/core |
| 删启发式后出现新帧形状 | 正文非 finalize 不读帧，天然免疫 | 无需补形状 |
| 精简变异漏掉真实不变量 | inventory + 一票否决族 + 归档跑 + 替代变异 | 线上问题补变异后重新 release |
| 颜色/字号真机不渲染 | 保留 `panel_color_tags:false` 降级 | 配置关闭 |
| golden/自检计数变化 | P1 不入 OBSERVED_HOOKS，P2a 枚举 diff 重生成 | 人工审 diff 后冻结 |

## 7. 审计与记录

- 计划审计：A/B/C 三份报告 + 统一结论（`docs/audits/v0.7.0/plan-consensus.md`）。
- 执行审计：P1/P1.5/P2/P3 各过 3 个独立子代理，分歧讨论至统一后写
  `docs/audits/v0.7.0/exec-consensus.md`。
- 所有门禁/全量日志、计数基线、mutation inventory、sha256 归档到 `docs/audits/v0.7.0/`。
- 用户可见结论与截图要求写入 release notes。
