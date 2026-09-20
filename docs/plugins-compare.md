# 同类飞书流式卡片插件横向对比（2026-09-15 更新）

> **2026-09-15 更新（R11 收尾后）**：按用户要求做了一次**重新对照**（四个独立子代理开采 + 我本人
> 逐条复核源码与真机凭据）。本次改三处：
> ① 第二节「我们」列有 **5 行写反了** —— 把**已经实现**的能力标成 ❌（更正说明见该表下方）；
> ② 第四节 ROI 中已落地的条目逐条标记；
> ③ 新增**第六节「路线对照与差距清单」** —— 把「它们有、我们没有」收敛成一份带证据、成本与
> 不变量冲突的清单，供决策用。

六家：`baileyh8/hermes-feishu-streaming-card`（HFC）· `Cheerwhy/hermes-lark-streaming`（CLS）·
`Aowen-Nowor/hermes-lark-streaming`（ALS）· `monkey2jack/aiduPOP`（AP）·
`techysy/hermes-fry-cards`（FC）· `BcubBo/lark-hls-v2`（HLS）。

**证据来源**：三路独立开采（codeload tarball / git clone 读真源码，逐条标 `[代码]` / `[README]`）+
**我们自己的真机实验**。凡与我们的实测冲突，**以我们的真机为准**并当场重验（见「重大更正」）。

> ⚠️ 这份文件里**没有**任何一家的 README 宣传当作事实。第三方 README 出错在本项目是有前科的：
> AP 的 README 把状态色映射写反（说「红=中止、黄=报错」，源码是 green=完成 / red=报错 /
> yellow=中止），我们的调研员还用截图做了像素验证。

## 一、一句话定位与「能不能抄」

| 项目 | 定位 | 接入方式 | 机制能不能抄 |
|---|---|---|---|
| **HFC** | sidecar 架构：Hermes 里只留最小 hook，卡片在**独立进程**渲染；~48k LOC | **安装期 AST patch Hermes 源码**（17 个 patch group + 源码 hash 校验）| ❌ 机制不可抄，**运维/可靠性思路可抄** |
| **CLS** | 进程内 CardKit 2.0 流式卡，靠 AST 注入取事件（15 个注入点）；v0.12.0 | **AST 注入** `gateway/run.py` | ❌ |
| **ALS** | CLS 的 fork（v0.7.0 起自述分叉），演进成「统一面板 + 全量交互 + 运维命令」；39★ | **运行时 monkeypatch**（原文 "Runtime monkey patching"）| ❌ 机制不可抄，**行为问题解可抄** |
| **AP** | ALS 的「泡波样式」定制独立项目；`kind: standalone` | **monkeypatch**（patch `GatewayRunner` 4 方法 / `AIAgent.run_conversation` / `FeishuAdapter` 4 方法）| ❌ |
| **FC** | 基于 CLS v0.12.0 **独立重写**，全押 CardKit 元素级流式 | **AST patch** | ❌ 机制不可抄，**元素预算调度可抄** |
| **HLS** | 与 AP 同源（AP 的上游），~18.8k LOC | **monkeypatch**（同批私有方法 + cron + `create_adapter`）| ❌ |

**共同点**：六家**全部**要改 Hermes 运行时，**没有一家**是 `register_platform` 合规插件。
但**「怎么改」必须分两类**（这一点极易说错，见 6.1）：
- **改磁盘源码**（HFC / CLS / FC）：`hermes update` 会**覆盖被改过的文件** ⇒ **每次升级都要重跑安装**；
- **只在运行时 monkeypatch**（AP / ALS / HLS）：升级**什么都不用做**，但内部结构一变就**静默失灵**。

**所以我们要抄的是它们解决过的「行为问题」，不是它们拿事件的方式。**

## 二、卡片效果：谁有谁没有（对我们有意义的部分）

| 效果 | HFC | CLS | ALS | AP | FC | HLS | **我们** |
|---|---|---|---|---|---|---|---|
| 单卡流式（工具/推理合入同卡） | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **逐字打字机** | ❌ 无 CardKit | ✅ | ✅ | ✅ | ✅ | ✅ | ✅（CardKit 元素写入，实测） |
| 推理 / 工具折叠面板 | ✅ timeline | ✅ | ✅ 统一面板 | ✅ | ✅ 统一面板 | ✅ 嵌套批次 | ✅ 统一面板 |
| 回合状态色 | ✅ | ✅ | ✅ | ✅ `border.color` | ✅ header 色 | ✅ | ✅ `border.color` |
| token / 上下文 / 耗时页脚 | ✅ | ✅ | ✅ 字段最多 | ✅ 2D 矩阵 | ✅ 图形条 | ✅ | ✅（`post_api_request` 钩子，零 patch） |
| 2.0 澄清卡（`select_static`/`input`） | ✅ | ❌ 无按钮 | ✅ | ✅ | ✅ | ✅ | ✅（默认 2.0） |
| 双语 i18n | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/stop` 中止重绘 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **多卡拆分**（元素近 200 自动封旧卡开新卡） | ❌（28KB 上限硬门禁） | ✅ 阈值 180 | ✅ 195 | ✅ | ✅ 180 | ✅ 200−5 | ✅ **R4 卡链**（阈值=硬上限一半，封旧卡→开新卡） |
| **点击即回卡**（回调响应里直接换卡） | ✅ | ❌ | ✅ `CallBackCard` | ✅ | ✅ | ✅ | ✅ 回调响应内联换卡，**零额外 API**；仅真提交成功才换 |
| 澄清三态（pending→submitted→confirmed） | ✅ | ❌ | ✅ + 重试提交 | ✅ | ✅ | ✅ | ⚠️ 有回填，无「提交中」态（**有意**：换卡有不可逆风险）——⚠️ **AP 那个「重试提交」实测是三重死的代码**，见第六节 |
| **流式期间改结构/加元素** | ❌ | ✅ `batch_update` | ✅ | ✅ | ✅ | ✅ | ✅ 元素级/批量写**实测可用**（见「重大更正」）；但**结构在建实体时定死**，流式期间只改内容与样式 |
| 长文封卡分片（`insert_after` 追加元素） | ✅ 结构边界切 | ✅ | ✅ | ✅ 2400 字分片 | ✅ | ✅ 4000 字分片 | ✅ **机制不同**：封旧卡+开新卡（不往同卡追加元素），`ck_offset` 三车道渲染 |
| markdown 表格超限降级 | ✅ 字段列表 | ✅ 代码块 | ✅ 代码块 | ✅ 无损压扁 | ✅ 代码块 | ✅ 代码块 | ❌ |
| 图片：URL→上传→`img_key` 替换 | ✅ | ✅ | ✅ | ✅ `img` 元素 | ✅ | ✅ | ❌（依赖官方 `MEDIA:` 路径） |
| 撤回/删除后停止更新 | ✅ | ✅ 30min TTL | ✅ | ✅ 卡片 TTL | ✅ `unavailable_guard` | ✅ | ✅ `_WITHDRAWN_CODES`（`230011`/`99992354`）标死+清追踪+不补发 |
| 渠道活性监控（WS 静默断连可诊断） | ✅ `/health` | ❌ | ✅ `/aowen monitor` | ✅ status 卡 | ❌ | ❌ | ✅ `/larkdeck status` 顶部聚合诊断含**入站心跳相对年龄**（与 status.inbound 同源）；异常链路与失败计数带 ⚠️。**没有**自动阈值告警（年龄多大算断连需要真机数据，先不做猜测） |
| 运维/诊断命令（`doctor` / `status`） | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ | ✅ `/larkdeck status`（版本/传输/钩子 7/7 + **P2 聚合诊断** + 六条账本记录）+ `/larkdeck config`（只读视图 / `config reload` 热刷新；聊天侧无写入命令） |
| cron / 后台任务推卡片 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ **P3 已实现**：通过官方 `PlatformEntry.standalone_sender_fn` 接管，cron / `send_message` 在无网关进程也走卡片层；**带媒体的末块回落内置 sender，非末块仍可能走卡片路径**（见 §8） |
| 幂等投递（UUID + 有界重试 + 结果不明不重发） | ✅ 最完整 | ❌ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ **Partially**：CardKit 实体/元素写用确定性 uuid（限流重试幂等）；内置文本发送每次 `uuid.uuid4()` 是官方行为，覆盖它要碰私有名（见 §8 第 3 条） |
| 远端图片/非法属性被拒的兜底 | ✅ | ✅ | ✅ 300315 提取属性名 | ✅ | ✅ `200570` strip | ✅ | ⚠️ 元素 id 类码有专用车道（R5 + `300315` 内层码解析），**远端图片上传没有** |

> 说明：我们列里标 ⚠️/❌ 的**不代表人家做得更好**，而是「人家有、我们暂时没有」；
> 我们已实测的硬上限（128000 字节）比它们普遍假设的 28~30KB **宽 4 倍以上**，
> 它们的「预算算法」多数建立在一个**过于保守的假设**上，**不能照抄数值**。

> ⚠️ **2026-09-15 更正（重要）**：本表初版（09-13）的「我们」列有 **5 行与实际相反** ——
> 多卡拆分、点击即回卡、长文封卡分片、撤回/删除守卫、流式期间改结构 都被标成 ❌，
> 而这五项**都已经实现**（分别是 R4 卡链、澄清回填内联换卡、R4 封旧开新、
> `_WITHDRAWN_CODES`+标死车道、cardkit 元素级/批量写）。上表已按代码复核结果改正。
> **教训**：这张表是项目的对外门面，**过期的 ❌ 比空白更危险** —— 它会让我们自己
> 低估自己（本次用户就是因为「感觉差距很大」才追问路线问题，而其中一部分差距是这张表写出来的）。

## 三、重大更正：它们让我们发现我们自己写错了一条「硬约束」

**旧结论**（写在 `AGENTS.md` / README / 代码注释里）：「任何结构性写入
（`message.patch` / `card.update`）都会关闭流式会话」⇒ 结构必须在 `card.create` 时定死。

**起因**：AP 与 HLS 都在**流式进行中**调 `cardkit.card.batch_update`
（`add_elements` / `insert_before` / `partial_update_element` / `delete_elements`），
用完继续用 `card_element.content` 打字，还把 `300309` 当**瞬态可重试**码。两说不能同时成立。

**我们自己的真机实验**（建实体后每步紧跟一次 `content` 写，以「后一次写入的返回码」为判据）：

| 流式期间的操作 | 返回码 | 之后写 `content` |
|---|---|---|
| （基线）`card_element.content` | `0` | ✅ `0` |
| `card_element.patch`（局部更新） | `0` | ✅ `0` |
| `card.batch_update`（`partial_update_element`） | `0` | ✅ `0` |
| `card_element.create`（**新增元素**） | `0` | ✅ `0` |
| `card_element.update`（整元素替换） | `0` | ✅ `0` |
| `card.settings`（`streaming_mode: false`） | `0` | ❌ **`300309`** |

**正确结论**：关流式会话的是**整卡替换**与**显式关流式**；
**CardKit 自己的元素级 / 批量接口在流式期间可用且不关会话**。

第二个实验把它做成了**用户看得见的卡**：流式写 3 帧正文 → `card_element.create` 插入一个新元素
→ `card_element.patch` 把折叠面板边框改成 `yellow` → `card_element.patch` 更新面板正文 →
继续写正文（全 `code=0`）→ 只用 `settings(streaming_mode=false)` 收尾。

⚠️ **「接口收下了」≠「客户端画出来了」**：边框色与新增元素是否真的呈现在屏幕上**只有眼睛能判**。
这正是我们做这条实验的目的 —— 它决定了「阶段 10」值不值得做。

## 四、ROI 排序（我们的约束下）

> **2026-09-15 落地情况**：下面 🟢 表里的 **#1 #2 #3 #6 #7 全部已落地**
> （#1 元素级/批量写在流式期间可用 → 已成默认传输 `cardkit`；#2 多卡拆分 → R4 卡链；
> #3 撤回/删除守卫 → `_WITHDRAWN_CODES`；#6 `card.settings` 预览 → R7；
> #7 渠道活性 → R9 入站心跳）。**本节保留为当时的判断与出处**，不再当作待办列表读。
> 真正的待办已收敛到**第六节**。

### 🟢 推荐做（收益明确、纯本地实现、不动 Hermes）

| # | 事项 | 出处 | 为什么值得做 | 成本 |
|---|---|---|---|---|
| 1 | **流式期间用元素级接口**（`card_element.create` 加元素 / `patch` 更新面板 / 上状态色） | AP · HLS · FC · CLS | 直接补掉我们 cardkit 下**流式期间没有页脚/面板统计、状态色只在收尾才上**的观感缺口；**已真机实测可行**（见上一节）。附带好处：面板可以从「一个 markdown 字符串」拆成多个元素，工具步骤各占一行 | 中（要引入结构状态机 + sequence 管理） |
| 2 | **多卡拆分**（元素/字节接近上限时「先建新卡 → 再封旧卡」续流） | CLS · FC · AP · HLS | 我们目前超预算只能**丢装饰甚至丢正文**；长回合（多轮工具）必然撞上限。FC 的 `ELEMENT_THRESHOLD=180` + `estimate_answer_elements()` 每帧重估、CLS 的「倒序找 step 边界」都是现成算法 | 中高（最复杂的一条，但收益也最大） |
| 3 | **撤回/删除守卫**（命中 `231003`/`1000023`/`230011` 即停止更新 + TTL 缓存） | FC · CLS · AP | 用户撤回卡片后我们还在每帧 patch，白花配额还可能报错；实现只有「一张表 + 一个集合」 | 低 |
| 4 | **长文/表格的 markdown 卫生**（未闭合 `**`/``` 截断、标题降级、表格超限压扁） | HLS · AP · HFC | 流式期间每帧都可能处于「半个围栏」状态，用户看到的是 raw markdown；纯字符串处理 | 低 |
| 5 | **`300309`/`300317`/`300313` 的专用重试车道** | 全五家 | 我们已补限流退避；它们更细（竞态码 `300313` 单独 3×0.2s、关流式后改用 `partial_update_element` **续写正文**）| 低 |
| 6 | **卡片 `summary` 随进展更新**（`card.settings` 只写 `summary`，不关流式） | AP | 会话列表预览里能看到「正在生成 / 已完成」，几乎零成本 | 低 |
| 7 | **渠道活性自检**（入站心跳 + 一条日志） | ALS | ALS 的动机值得抄：lark-oapi WS 重连预算耗尽后 dispatch 线程**静默退出**，适配器仍报「已连接」，生产 2 个月断连 41 次**零日志**。我们已有启动自检，加一条「最近入站时间」就能自证 | 低 |
| 8 | **封卡时递归统计元素**（所有含 `tag` 键的对象，含嵌套）+ 留 margin | HLS · ALS | 我们现在的元素预算只看顶层，会**低估**（嵌套面板里的元素也要算）。这是纯口径修正，防止撞 `300312`/`300305` | 低 |

### 🟡 可以做但优先级低（收益取决于你的用法）

| 事项 | 出处 | 判断 |
|---|---|---|
| 点击即回卡（`CallBackCard`，回调响应里直接换卡） | ALS · AP | 省一次 API 调用、天然规避方言混用；但我们现在这条路径也能用（回填走 patch），**收益是「快一点」** |
| 澄清三态（提交中 → 已确认）+「重试提交」 | ALS | 观感提升；我们已经能正确回填，缺的是中间态 |
| `/model` 两级选择、`/new` 等命令卡 | HFC | 你如果在飞书里敲命令，这就很有用；否则零收益。**不改 Hermes 也能做**（`pre_gateway_dispatch` + 卡片回调） |
| 页脚字段扩展（cost 实报/估算、cache 命中率、api_calls） | ALS · AP | 我们页脚已有上下文用量；扩字段是「信息更多」，不是「缺能力」 |
| 图片 URL→上传→`img_key` | CLS · AP | 让 markdown 里的外链图能显示；但要管缓存与上传配额 |
| cron / 后台任务推卡片 | HFC · CLS | 我们的钩子面里没有 cron 路径；要做需要新的接入点 |
| 多 bot / 多 profile | HFC | 单机单 bot 场景下没用 |

### 🔴 不推荐 / 做不到（附原因）

| 事项 | 出处 | 为什么不行 |
|---|---|---|
| **整套事件获取机制**（AST 注入 / monkeypatch `GatewayRunner`、`AIAgent`、`FeishuAdapter`、cron、`create_adapter`） | 全部六家 | **违反不变量 1**。它们每次 Hermes 升版都要重对注入锚点（CHANGELOG 里全是「适配新版本」）。我们用 `register_platform` + 7 个官方钩子拿到了等价（甚至更稳）的数据源 |
| 「替身 vs 真身」类身份 hack | AP · HLS | 依赖 `hermes_plugins.feishu_platform.adapter` 私有模块路径；我们已用 `_discover_base_class()` 从注册表运行时解析，正道且更稳 |
| 它们的字节预算（28KB / 18000 / 20000） | HFC · AP · HLS | 我们**实测**硬上限 128000（160000 被拒 `230025`）。照抄会把能显示的内容白丢 4~7 倍 |
| 群成员 SQLite + 权限注入（`admin:<用户名>`） | HLS | 依赖 Hermes 私有消息字段 + 引入持久化，与「进程内只读快照」的哲学冲突；而且写库是新的故障面 |
| 动态二次元台词库 | HLS | 与我们的 i18n 双语体系冲突；且需要读插件目录 json |
| 可视化配置工作坊（`aidupop studio`：写 `~/.hermes/config.yaml`） | AP | 我们只经官方 `ctx.get_config()` **只读**；写回配置是越权，还会和 Hermes 自己的配置管理打架 |
| HFC 的 sidecar 架构 | HFC | 换来了「卡片渲染不阻塞网关」，代价是**多一个进程 + HTTP 协议 + 交付账本 + 运维命令体系**，整体复杂度远超我们的收益（我们要的效果它没有 CardKit 也给不了逐字） |

## 五、还没验的事（诚实登记）

1. ~~流式期间新增元素 / 改边框色「客户端真的画出来了吗」~~ **✅ 2026-09-13 已由用户肉眼确认**
   （实验卡截图）：正文之后出现了**流式期间用 `card_element.create` 插入的新元素**
   （「🧩 这一行是流式期间插入的新元素」），折叠面板「执行详情」是**黄色边框**
   （流式期间用 `card_element.patch` 改的），展开后内容是**流式期间用 `card_element.patch`
   更新的面板正文**（「工具 terminal ✅ 1.2s · bash ✅ 0.4s」），且面板展开/收起正常。
   ⇒ **元素级写入在客户端是真的会重绘的**，「流式期间加元素 / 改面板 / 上状态色」这条路成立，
   推荐 #1 升级为**最该做的一条**。
2. ~~元素级接口的**配额/限流**未知~~ **✅ 已量（R7）**：卡级上限 10 次/秒，我们每帧
   元素写预算 2 次 + 限频预览；R11-B1 另加 1 秒滑窗守卫（超限时让出那次装饰 batch，
   正文与预览永不跳过）。
3. `card.settings` 的 `summary` 形状：我按 `{"config": {"summary": "字符串"}}` 传被拒
   （`300122 failed to unmarshal for Summary, type: string`）⇒ 它要的是 i18n 对象
   `{"content": ...}`（我们 `cards._summary_of` 就是这个形状）。**这条已由返回码确认，不算未验。**

## 六、路线对照与差距清单（2026-09-15）

**起因**：用户看到 aiduPOP 的截图后追问「是不是我们路线选错了？我们这条路是不是真做不到？」
本节就是那次追问的答案：**逐条查证「能不能做」，并把结论分成「路线限制」与「我们没做」两类。**

### 6.1 六家路线与升级代价（**必须分两类，混为一谈就是错的**）

要分清两件事：**「改磁盘上的源码」**（`hermes update` 会把改动覆盖掉 ⇒ **每次升级都要重跑安装**）
与**「只在运行时 patch 内存」**（升级**什么都不用做**，但内部结构一变就**静默失灵**）。

| 类型 | 谁 | 拿事件的方式 | 升级后要做什么 | 坏的时候什么样 |
|---|---|---|---|---|
| **A. 安装期改磁盘源码** | HFC / CLS / FC | AST 注入 Hermes 源码 —— CLS/FC 把 `run.py` 备份成 `run.py.hermes_lark.bak` 再改写（`patcher.py`，目标 `<HERMES_HOME>/hermes-agent/`）；HFC 用 `install/patcher.py` + manifest + recovery | **必须重跑 install**。FC 的 INSTALL 原文表格：「`Hooks gone after Hermes update`｜Hermes update **overwrote patched files**｜`Re-run verify + install`」；CLS 的 Update 段同样是 `uninstall`（remove old injection）→ `verify` → `install` | **明确**：`verify` 报 `Incompatible`（「锚点变了」）或钩子直接消失 |
| **B. 运行时 monkeypatch** | AP / ALS / HLS | 插件 import 时在内存里替换 `GatewayRunner` / `AIAgent` / `FeishuAdapter` 的私有方法（AP 另有 2 条轮询线程赌加载时机） | **不用重装**（这是它们相对 A 类的**真实优势**） | **静默失灵**：私有方法改名/挪窝 ⇒ 只打一条 WARNING ⇒ **卡片整条链路消失**，不报错也不回落；要等作者发新版 |
| **C. 官方插件契约** | **我们** | `ctx.register_platform()` + 7 个**官方**钩子 + 子类化官方适配器（`~/.hermes/plugins/` 软链） | **什么都不用做**（Hermes 源码从未被我们碰过） | **部分 fail-open**：消息永不丢（退纯文本）；**但「如实上报」是夸大的** —— 已核实 7 条静默路径（§7.6）；且官方成文契约**不覆盖适配器方法**（§7.4） |

> ⚠️ **2026-09-15 更正（本文件初版写错了）**：本节初版写成「换任何一家都要接受每次
> `hermes update` 后重装」—— **那句对 B 类是错的**。B 类（AP/ALS/HLS）升级后**确实什么都不用做**。
> 「每次升级都要动手」只适用于 **A 类**（HFC/CLS/FC），而且 FC 那条是它**自己的 INSTALL 原文**写的。
>
> 🔴 **2026-09-16 二次更正（上面那次更正留下的两条「优势」也都不成立）**：本节曾写
> 「我们相对 B 类的优势只剩 ① 不依赖任何私有名 ② 一旦不适配必定退化成纯文本并留痕」。
> 经四路独立复核（见 **§7**）：**① 不成立**（我们登记了 **8 组 19 个名字，其中 11 个是下划线私有接口**
> —— AST 实测，见 §7.2）；
> **② 比声称的弱得多**（B 类同样会退成纯文本；而我们的「留痕」主要落在被动日志里，
> 用户可见渠道只有「钩子 N/7」一项）。**两条优势一条都不成立** —— 逐条判决与依据在 §7.2。

### 6.2 两条被截图误导的前提（都已复核）

1. **截图里的 `(Recommended)` 不是 AP 的功能** —— 它是 **Hermes 核心**加的
   （`tools/clarify_tool.py:16 RECOMMENDED_LABEL`，注释写明 "presentation only"）。
   AP 全仓 grep `Recommended` **零命中**；**我们同样免费得到**。⇒ 此项**无差距**。
2. **AP 的澄清「三态卡 + 重试提交」是三重死的代码**（子代理用它的真模块实测）：
   ① 它的点击入口 `_handle_card_action_event` 的**返回值被核心丢弃**
   （核心把 `_on_card_action_trigger` 当绑定方法注册，类属性 patch 打不进去）；
   ② 换卡要用的 SDK 类从 `lark_oapi.api.cardkit.v1` 导入 ⇒ 实测 `ImportError` ⇒ 响应恒为 `None`；
   ③ 即使前两条修好，`_submitted_card_response` 传 `choices=` 而构造器不收 ⇒ `TypeError` 被 except 吞掉
   ⇒ 回落原生 ⇒ 一次点击变成注入会话的 `/card {...}` 合成命令。
   它**忽略 `resolve_gateway_clarify` 的返回值** ⇒ 超时后补点会刷出「已确认 + 已选择 X」的**假确认**；
   它全仓**一条 toast 都没有**，失败时屏幕上什么都没有。
   ⇒ 截图里「点一下跳已确认」是这条死路的**症状**（1 秒后由服务端 `update_card` 换卡 + 硬编码 `sleep(1.0)`），
   **不是它比我们能干**。
   —— 反过来说，**用户看截图时的直觉在「效果」上是对的，只是机制判错了**：真正那一处是我们的问题，
   见 6.3 第 1 条。

### 6.3 差距清单（按性价比排序，含证据 / 成本 / 碰哪条不变量）

> **2026-09-15 晚更新：下面 1–5 条已经全部做完**（澄清卡选项外显 + 脚注按方言 + 工具参数脱敏 +
> 卡片短码 + status 三条）。本节保留为**当时的判断与出处**，不再当作待办读。
> ⚠️ 其中第 3 条的现状与本节原文相反 —— 原文写「我们 `core/panel.py` 是 raw JSON 预览，
> 全仓 `redact` **零命中**」，那个描述**现在已经是假话**（脱敏已实现，且审计又挖出并修掉了
> 三处漏脱：`KEY="值"`、`Cookie:` 头、家目录规则截断 URL）。

| 优先级 | 做什么 | 证据 | 成本 | 会碰的不变量 |
|---|---|---|---|---|
| **1** | **2.0 澄清卡把选项文本外显**（下拉之上加一份 markdown 列表，与 `_clarify_options` **同源**：编号/去重/`(Recommended)` 一致） | 我们 `core/cards.py:1445` 只有 `md(问题)+下拉`（**已本人复核**）；AP `cardkit/special.py:157-166` 用 markdown 列表外显；`docs/plan-6-effects.md:148` 本就写了，**实现时漏了（无记录）** | **S** | 不变量 5：只能用 2.0 元素，**绝不塞 1.0 `action` 行**；列表文本必须与提交值同源 |
| **2** | **脚注按方言拆两条**（默认 2.0 卡上不许再说「点按钮」） | `core/i18n.py:42` 现文案「点按钮，或直接回复文字都行」而该卡**无按钮**（**已本人复核**）；兄弟实现 `_ld_typing_text_key` 已按方言分流 | **XS** | 无（i18n-only）；单测要同时钉两种方言 |
| **3** | **工具参数脱敏**（token / `KEY=value` / `Authorization` / cookie / api_key / 路径只留 basename） | 我们 `core/panel.py` 是 raw JSON 预览，全仓 `redact` **零命中**；AP 已做（`state/tooluse.py:62-108`） | **M** | `pre_tool_call` 是 **fail-closed** ⇒ 必须微秒级、有界；要配变异条目防「按分隔符切」式假绿 |
| **4** | **卡片 trace id**（页脚印 msg_id 后 6 位 + 日志带同码） | AP `cardkit/elements.py:656-707` + `state/session.py:100-103`；我们现在**无法把用户截图与日志对齐** | **S–M** | 界面文案走 i18n；页脚降载档位；`_ld_send_card` 收口点已有 message_id ⇒ **不需要新增私有名** |
| **5** | **`/larkdeck status` 补三条**：uptime / API 错误码 top-N / **回落纯文本次数** | AP `aowen/__init__.py:447-531`；我们账本只有三条 | **M** | AGENTS「status 四条纪律」：没记录写「无记录」、只统计真写出去的、口径是帧不是次、记账挂低层收口点；**新计数必须同时进变异清单** |
| ~~6~~ | ~~探针 ⑮：**2.0 `button` + 组件级 `behaviors`** 真机点一次~~ **✅ 2026-09-16 已完成** | AGENTS 不变量 5 的「组件可以是 `button`」**只有官方文档证据，无真机点击证据**（探针只覆盖 `select_static`/`input`）——**2026-09-16 16:12 真机点到了**：日志 `探针点击到达 ✅ tag=button … value={'kind': 'button', 'larkdeck_probe': True}` ⇒ **这一格成立**，`button` 与其余三格同级；AP 自己也没测过（它在 e2e 注释里承认「V2 里的 action 会被拒 `230099`，所以这张卡从不真发」） | **M**（需用户点一次） | 这是对不变量 5 的**证据补强**，做完顺手把那句措辞改准（区分「文档已证」/「真机已证」） |
| 7 | 澄清卡的 `question`/选项做 **markdown escape** | 我们 `core/cards.py:1445` 直插；AP `special.py:150` 有 `_escape_md` | **S** | 转义只用于**显示**，回给核心的 `value` 必须是原文 |
| 8 | 判据函数自身的**双向负向对照**（判据必须抓得住坏样本、不误伤好样本） | AP 唯一一条自觉的判别力测试 `tests/test_version_single_source.py:89-97` | **S** | 无（这是我们变异清单思想的正向补充） |
| 9 | 陈旧澄清卡**自愈**（回合结束/超时后把未回答的卡标灰） | 双方都没有；核心超时即 pop entry，**无人通知适配器** | **L** | 要新增追踪 + 一次额外写卡；收益不确定 ⇒ **先不做** |

### 6.4 明确判定为「我们更好」，不要看漏

点击链的每一次真实失败都有话说（5 条 toast 分流、**失败绝不换卡**以防不可逆的「已确认退回待答」）·
只有真提交成功才回填（我们拿 `resolve_gateway_clarify` 的返回值当判据，AP 忽略它）·
插件侧**零状态**（答案与 `clarify_id` 都在回调载荷里 ⇒ 进程重启/TTL 都不影响）·
**多选原生支持**（AP 多选为零）· **不吞别人的卡**（非本插件点击一律交回 `super()`，AP 全压掉）·
超长回答**不丢字**（R4 卡链按 `ck_offset` 三车道）· **写入配额有守卫**（R11-B1 滑窗）·
判别力：**267 条变异** vs AP **零变异基础设施**（它对「点击→换卡」这条链**一个断言都没有**，
1001 个用例证明不了它）。

### 6.5 取证声明（区分「我复核过」与「子代理报告的」）

- **本人当场复核**：选项不外显（`cards.py:1445` 实读）· 脚注「点按钮」文案（`i18n.py:42` 实读）·
  `(Recommended)` 出自核心（核心源码 + AP 全仓 grep）· AP 澄清三态的三重死（子代理实测，结论我已接受）
  ⚠️ **2026-09-16 更正：最后这条措辞过重** —— 复核实为「**三态变两态**」：硬锁「已确认」那半条是通的，
  软锁态才是死的（两处独立死因）。修正版见 **§7.5**。其余复核项不变：
  · README/本表 5 行漂移（逐条 grep 复核实现存在）· **六家的接入方式分 A/B 两类**（读真源码：
  CLS/FC 的 `patcher.py` 把 `run.py` 备份成 `.hermes_lark.bak` 后改写磁盘；HFC 的
  `install/patcher.py`；AP/ALS/HLS 无任何源码写入、只在 import 时 monkeypatch）·
  FC/CLS 的 INSTALL 原文（「升级覆盖被 patch 的文件 ⇒ 重跑 install」）。
- **来自子代理报告、我未逐行复核**：AP 的 `/aowen monitor` 与 trace id 的具体字段、
  AP 的脱敏实现细节、ALS 的观测项。这些在动手做第 4/5 条时应**先复核目标文件再实现**。
- **仍待补**：HLS 的全功能矩阵报告（最后一个子代理）。
  ⚠️ 2026-09-16：HLS 那一列的取证**仍未跑完**，但 §7 的结论不依赖它（本轮只审了 AP 与我们两家）。

---

## 七、2026-09-16 独立复核：路线 A / B / C 的「优势」逐条判决

### 7.0 为什么要重做一遍

用户在 2026-09-16 指出两件事，**都成立**：

1. **§6.1 的结论是「造 larkdeck 的 agent」自己写的** —— 它有立场；而其中最关键的几条
   （「AP 的澄清三态是三重死的代码」）**是它派子代理查的、它自己承认未逐行复核**（§6.5）。
2. 用户**从未做过技术评估**：当时是在描述想要的效果，路线由 agent 提议、用户接受。
   把「用户接受」记成「用户决定」，等于拿一份没有技术依据的判断去支撑技术结论。

⇒ 本轮**不引用任何既有结论**，四路独立取证后重写判决。
**结论推翻了本文件 §6.1 的两条正文，也推翻了复核过程中我自己的一个矩阵**（7.3），两条都留痕。

### 7.1 取证方式与它的边界

| 路 | 做了什么 | 工具调用 | 边界 |
|---|---|---|---|
| A | aiduPOP 全部 **18 个补丁点**的动机 × 官方等价物 | 22 | 只审 AP；三类划分含主观性 |
| B | aiduPOP 效果清单 × 可达性（**真跑 import**、核签名、查测试覆盖） | 29 | 只审 AP |
| C | larkdeck 静默失灵对抗审计（私有名 × 后果） | 24 | 只审我们 |
| D | Hermes 官方契约与失效检测机制 | 28 | 只审 Hermes |
| 本人 | 对 A–D 的**载荷结论逐条二次核实**（下文标「已复核」者即此项） | —— | —— |

- 固定的 aiduPOP 版本：**commit `37a9783`（v2.5）**，可复跑。
- 四路统一硬约束：**不许再派下级子代理 / ≤30 次工具调用 / 只读 / README 与注释不算证据**
  （模板出自 `docs/handoff-route.md` §7 那次取证中止的教训）。
- ⚠️ **子代理报告不是圣旨**：本轮**推翻了 B 的一条载荷结论**（7.5 末尾），
  **推翻了本人的一个矩阵**（7.3）。
- ⚠️ **留痕（提交层面）**：本节的正文（§7 全部，217 行）被**并发写入的另一个会话**
  误并进了提交 `836d7e4` —— 那条的提交信息写的是「verify-log: 记入 fullrun9」，
  **完全没提本次审计**。内容无误、已推送；`bdde6f1` 的提交信息里补了这件事，
  `docs/handoff-route.md` §13.4 记了纪律。**未改写历史**（已推送，改写需 force-push，收益不抵风险）。

### 7.2 判决：「我们相对 B 类的优势」——**一条都不成立**

| 曾声称的优势 | 判决 | 依据 |
|---|---|---|
| **不用重装** | ❌ 不算优势 | B 类也不用（只对 A 类成立） |
| **不依赖私有名** | ❌ **不成立** | `core/compat.py` AST 实测：**8 组 19 个名字，其中 11 个是下划线私有接口** |
| **不会静默失灵** | ❌ **不成立** | 我们**自己**审出 **7 条静默路径**（7.6），而且**代码注释里早就写着**「静默失灵」 |
| **失效必退纯文本** | ⚠️ 半真 | B 类同样退纯文本（补丁没打上 ⇒ 核心跑原版 ⇒ 消息照发）。**唯一真属于我们的**是 REQUIRED 组缺失时的「拒绝接管 + ERROR 日志」 |
| **失败留痕、可查** | ❌ 比声称的弱得多 | 探测结论只进**被动日志**；用户可见的 `/larkdeck status` **一个 probe 键都不打**，只有「钩子 N/7」（7.6） |
| **不互踩、卸载干净** | ✅ **成立** | 注册表是槽位覆盖（last-writer-wins）；monkeypatch 是改共享类，两家会互相蚀掉对方。**但只有同时装两个抢飞书平台的插件时才重要** |
| **267 条变异 / 四门禁** | ✅ 成立且价值最大 | **但这是纪律，不是路线** —— B 类同样可以有测试，只是 AP 那条链上一条都没有（7.5） |

> **一句话**：「路线 C 更安全」**不能由路线本身推出来**。真正起作用的是
> 「**失效能不能被你看见**」，而那是**建出来的**，不是选出来的。

### 7.3 上游到底改没改过？（39 个 tag × 6 个月，实测）

方法：在 `~/.hermes/hermes-agent` 里，对**两条路线各自依赖的每一个名字**，
跨 **39 个 tag（2026-03-12 → 2026-09-11）**做全树 grep。

**结果：两条路线依赖的名字，在整个窗口里没有一个被改名或删除。** 唯一的变化是**新增**
（`_card_response` 在 v2026.9.7 才出现、`lookup_by_session_key` 在 v2026.8.13 才出现）。

⚠️ **我最初那版矩阵是错的（先自曝）**：我按名字全树搜，于是漏掉了「**搬家**」这类改动。
本轮窗口里**真的发生过一次搬家** —— 2026-09 的模块拆分（Agent D 查到的 PR #102117）把
`_deliver_result` 的**定义**从 `cron/scheduler.py` 挪到了 `cron/scheduler_delivery.py:1595`。
**但两条路线都没被打坏**：拆分刻意在 `cron/scheduler.py:3854` 保留了再绑定
（`from cron.scheduler_delivery import _deliver_result, ...`），而**三个真实调用点全在
`cron/scheduler.py`**（:1113 / :2767 / :2845）⇒ AP 打在那个绑定上的补丁**拦得到**（已复核）。

⇒ **两条路线在这个窗口里都没有被上游改动打坏。** 「私有名改名 ⇒ 静默失灵」是一条
**尚未被观测到的假设**，对 A / B / C 都适用；把 B 类的风险说成必然，不诚实。

### 7.4 Hermes 的成文契约覆盖到哪 —— 这条**同时**削弱我们和 B

契约确实成文（`website/docs/developer-guide/plugins/index.md:106-163`）：
**只能加法演进**；**已文档化的 `PluginContext` 方法不被删除或改名**；钩子载荷只能**加** kwarg；
弃用政策是「替代物 + 每进程一次警告 + 旧行为再支持两个 minor」。

**但它覆盖不到我们真正站着的那块地**：

- ❌ **平台适配器的方法** —— 包括**文档点名教插件覆写**的 `BasePlatformAdapter._keep_typing`、
  `interrupt_session_activity`（`adding-platform-adapters.md:469-477`、`:546`）—— **没有一句
  稳定性承诺**。⇒「子类化官方适配器 + 覆写下划线方法」是**文档点名的习惯用法，不是契约承诺**。
  **我们和 AP 站在同一块没有承诺的地上。**
- ❌ **钩子名**：`register_hook` 对未知名字是 **warn-and-store**（`hermes_cli/plugins.py:893-895`、
  `:904-915`）⇒ 上游删/改钩子名，插件**照常加载、回调永不触发**，只留一条 warning。
- ❌ **私有名**：`COMPAT_MANIFEST.md:32-35` 明说 `_foo` 从来不是任何表面的一部分。
- ❌ **插件无法声明「我要求 Hermes X.Y」**：`api_version` 解析了但**全树无消费者**
  （唯一读取处 `plugin_dev.py:233-235` 只查 `< 1`）；合法字段表里 `hermes` / `depends` 只是**保留名**。
- ⚠️ **失效检测只覆盖一种失效**：PLUGIN-COMPAT 层只认**旧 import 路径**（AST 扫描）——
  **私有属性改名 / 钩子改名 / 行为变化一律发现不了**；而且它是**面向用户**的
  （banner / `hermes doctor` / `hermes plugins compat`），不是自动通知作者的通道。
- ✅ 官方**明确反对** monkeypatch（`plugins/AGENTS.md:7-15`「Plugins never touch core」；
  `features/plugins.md:452-457` 称 `ctx.platform_actions` 是 "the sanctioned alternative to
  monkeypatching an adapter"）—— **但只是态度，没有任何强制**（能力层自己声明不是沙箱）。

⇒ **`register_platform` 是官方推荐的接入姿势（有文档、有 last-writer-wins 语义），
但「我们的接入面被契约保护」是假的。** 这条对 C 的叙事是实质削弱。

### 7.5 aiduPOP：射程去了哪、效果兑现了多少

**A：18 个补丁点分三类** ——【拿事件】**10** /【改行为】**4** /【自我可靠性】**4**。

- **【拿事件】10 条全部有官方等价物**：5 条是在 `GatewayRunner` 上「注入钩子」
  （NORMALIZE / START / COMPLETE / ABORT / 后台任务），对应我们的 7 个官方钩子；
  5 条是替换 agent 的实例回调，对应 `on_stream_delta` / `on_interim_message` /
  `pre|post_tool_call`。**铁证**：官方 `on_interim_message` 的 kwarg `already_streamed=`
  （`agent/stream_delivery.py:202`）与 AP 包装器读的 kwarg **逐字相同**
  （`patching/callbacks.py:134`）—— 同一个事件，两条取法。
- **【自我可靠性】4 条不产生任何用户效果**，纯粹为了让上面那些补丁**生效**：包装
  `platform_registry.create_adapter`（`patching/__init__.py:702`）、兜底
  `_authorization_adapter`（`:308`）、直补 `AIAgent.run_conversation`（`:772`）、
  包装 `run_conversation`（`:392`）。**这 4 条正是在手工重造「子类化」免费提供的性质**：
  官方 `register_platform` 的 `adapter_factory` 让**实例由插件自己的工厂造**
  （`gateway/platform_registry.py:380`），类在任何实例存在前就定死；而 AP 是在
  `orig_create_adapter(...)` **返回之后**才动类，于是必须叠三套互保
  （`_patched_feishu_classes` 集合、`_authorization_adapter` 兜底、send/send_clarify 内的
  on-demand repatch）。而 AP 全仓 `register_platform` **零命中** —— 它根本没用那条 API。
- **【改行为】4 条确实全部产生用户可见效果**（send 转卡、edit 转卡、send_clarify 转交互卡、
  点击消化 + 拦 `/card`）⇒ **纠正 §6 曾经的说法**：不能说「射程没换成效果」。准确说法是：
  **效果是真的，但拿这 4 个效果不需要 monkeypatch**（子类化同样拿得到）；白花的是另外 **14** 条。

**B：效果兑现率** —— 逐条验了 import / 签名 / 调用点：

- **澄清链是「三态变两态」**（纠正 §6.2「三重死」的措辞）：**硬锁「已确认」那半条是通的**
  （`_schedule_confirm_card` 的 import、签名、`update_card` 调用点全对得上，happy path ✅）；
  **软锁「已提交 + 重试」态有两处独立死因、永远到不了用户**：
  ① `patching/adapter.py:722` 的 import 路径错（**我实测**：那两个名字在
  `lark_oapi.api.cardkit.v1` 里**不存在**，官方自己在 `plugins/platforms/feishu/adapter.py:72`
  是从 `lark_oapi.event.callback.model.p2_card_action_trigger` 取的）；
  ② 更致命：**核心丢弃 `_handle_card_action_event` 的返回值**
  （`plugins/platforms/feishu/adapter.py:2083` 起 `self._submit_on_loop(...)`，
  然后 `return self._card_response()`，无 card）⇒ **即使 import 修好也看不见**。
- **假确认是真的**：`resolve_gateway_clarify` 的 `bool` 返回值在**9 个调用点全部被丢弃**
  （`:799/813/820/864/886/920/941/977/998`）⇒ 澄清已失效 / 超时 / 重复点击时，卡片照样翻成
  「已确认」，而 agent 根本没收到答案。
- **「点击 → 换卡」这条链零测试**：`_schedule_confirm_card` 在它整个 `tests/` 里**零引用**；
  `build_clarify_confirmed_card` 只被当**纯 JSON 构造器**断言。
- 🔴 **cron 出卡：我推翻了子代理 B 的结论**（已复核）。B 报告称「`_deliver_result` 在 0.21.1
  里不存在 ⇒ AttributeError 被吞 ⇒ 不可达」。**实测：该属性运行时存在**，补丁装得上，
  且三个真实调用点全走这个绑定 ⇒ **它拦得到**。**此条不成立，不采纳**（见 7.3）。
- **找不到任何一条「aiduPOP 做得到、larkdeck 做不到」的飞书卡片效果。** 唯一真独有的是
  **Studio 本地配置网页**（`studio/server.py`），不是卡片效果。

### 7.6 larkdeck 自己的静默失灵清单 —— **本轮最该被记住的一节**

（Agent C 出品；标「已复核」的由本人二次核实）

**两条自检渠道实际是什么**：
- 启动自检 = **一行 logger**（成功 INFO / 失败 ERROR），落在 `~/.hermes/logs/agent.log`
  （真机已验：`gateway.log` 里几乎没有 `[larkdeck]` 行）。
  **`adapter.py` 的 `SELFCHECK` 在生产代码里没有任何读者**（3 处全是写；读者只有
  `tests/check_override.py`）—— **已复核**。
- `/larkdeck status` **（P1a 之前）** = `adapter.py:3902` 一行真相：
  `"\n".join([header, 口径说明] + _context.status_lines())` ⇒ **当时只有 6 个账本计数 +
  「钩子 N/7」；`compat.probe_report` 的键一个都没上卡** —— **已复核**。
- ✅ **2026-09-16 P1a 更新（实现已过门禁）**：`probe_report` 的 10 个契约键已按
  「状态 / 缺失 / 探测契约」三行摘要上到 `/larkdeck status`；`build_adapter()` 存进程级只读
  快照，状态卡区分「未探测」「已接管」「必需接口缺失」「覆盖层构造失败」「报告缺键」。
  ⇒ 下表 **S1 已解决**（`session_attribution_ok` 现在会显示）；**S7 已加静态 best-effort
  源码字面量探测（P1b，「信号契约」行 + False 时 WARNING），但运行期派发仍未验证、残余盲区仍在**；
  **S2、S6 仍未解决**。

**静默路径（用户看不见）**：

| # | 路径 | 后果 | 核实 |
|---|---|---|---|
| S1 | `session_attribution_ok` **算了没人读**（`compat.py:187` 产出；`_log_probe_report` 不打它） | 面板绑错会话时**显示别人的面板**，零信号 | ✅ |
| S2 | `CALLBACK_INSTANCE_ATTRS`（`_loop`/`_client`）**整组没有 probe 键**（`probe_report` 不读它） | `_loop`（`adapter.py:3562`）裸访问 ⇒ 点击丢弃 | ✅ |
| S3 | `_client` 缺失 ⇒ `supports_native_streaming` 静默 False | **卡片永不出现、账本零记录、无日志**；状态卡显示「写卡：无记录」 | 子代理 |
| S4 | `format_tool_event` 是**死杠杆**（0.21.1 生产路径无人调用，我们注释里已承认） | 「正文干净」全压 `_strip_core_progress`，其唯一的洞会**不可逆**地留进度行或吞正文 | ✅ |
| S5 | 面板数字脏值 ⇒ 整块面板消失（只剩一条 INFO） | 用户只看到卡上没有面板 | 子代理 |
| S6 | 「写卡失败」与「掉回纯文本」同点同帧各 +1（`context.py:537-543`） | 排障读数**无判别力** | 子代理 |
| S7 | `interrupt_session_activity` 的**核心查找名**（`gateway/run_agent_cache.py:415`）是探测盲区 | 改名 ⇒ `/stop` 后卡片**永久停在生成态**；P1b 已加静态源码字面量探测 + status「信号契约」行 + False 时 WARNING，但**运行期派发仍未被它验证** | 子代理 + P1b |

**注释 vs 代码不符（「文档比代码乐观」的自家版本）**：

- `compat.py:29-32` 写 `edit_message`「有则用、无则退回内置」：覆盖体两处
  `super().edit_message(...)`（`adapter.py:1896`、`1918`）**都在 try 之外** ⇒ 基类若真没有它，
  **只会抛、退不回内置** —— **已复核**。
  （口径校正：这是**异常**不是静默，且 `edit_message` 是无下划线的公开名，风险等级低于 S1–S7。）
- `compat.py:34-37` 写点击「缺了不致命（回落给内置实现）」：真实失败形态下
  （`_on_card_action_trigger` 改名）**我们的覆盖体根本不被调用**，「回落」不成立。
- `compat.py:151-159` 写「门禁、启动自检的告警都从 `PROBE_REPORT_KEYS` 派生」：
  `session_attribution_ok` 在契约里却无人消费。

### 7.7 判决（替换 §6.1 的旧结论）

1. **「我们相对 B 类的优势」一条都不成立**（7.2）。用户 2026-09-16 的判断是对的。
2. **「B 类的效果更好」不成立**：找不到任何一条只有 B 能做出来的**卡片效果**（7.5）；
   B 真正多出来的射程 **14/18** 花在「重造公开契约已有的东西」与「让自己补丁生效」上。
3. **两条路线站在同一块地上**：官方成文契约**不覆盖适配器方法**，两边都靠「习惯用法」（7.4）。
4. **两条路线在这个窗口里都没被上游打坏**（7.3）—— B 类的风险是**尚未观测到的假设**，
   不该被当作既成事实来推销 C。
5. **真正决定卡片三个月后还灵不灵的，是「失效能不能被你看见」**：这个能力我们目前
   **只有「钩子 N/7」一项**真正上到了用户可见渠道（7.6）⇒ **可以修，而且是本轮性价比最高的一件事。**
   > ✅ **2026-09-16 P1a 更新**：上面这句「只有钩子 N/7」已过时——`probe_report` 的 10 键摘要
   > 现在也上了 `/larkdeck status`；但 S2/S6/S7 仍未解决，所以「失效可见性」还不是满分。

### 7.8 登记：需要修的（**登记不等于开工**，等用户点单）

| 优先级 | 项 | 依据 |
|---|---|---|
| 高 | 把 `probe_report` 的结论打到 `/larkdeck status` 卡上——**P1a 已完成，见下方更新注** | 7.6 |
| 高 | S7：`interrupt_session_activity` 核心查找名——**P1b 已加静态探测；运行期派发仍未验** | 7.6 |
| 中 | S1：`session_attribution_ok` 算了不报 | 7.6 |
| 中 | S2：`CALLBACK_INSTANCE_ATTRS` 整组补探测键 | 7.6 |
| 中 | `compat.py` 三处「注释比代码乐观」的句子改准（或让代码兑现注释） | 7.6 |
| 低 | S6：两个计数拆开，恢复判别力 | 7.6 |
| 低 | 7.3 的矩阵脚本沉淀成 `tests/` 里的一个探针（上游搬家/改名时跑得出读数） | 7.3 |

> ✅ **2026-09-16 P1a 更新**：第 1 行「`probe_report` 上卡」**已完成**（10 键摘要 +
> 未探测/已接管/必需接口缺失/覆盖层构造失败/报告缺键）；第 3 行 S1 随之上卡解决。
> **P1b 更新**：第 2 行 S7 已加静态 best-effort 源码字面量探测 + `/larkdeck status`
> 「信号契约」行 + False 时 WARNING，但**运行期派发仍未验证、残余盲区仍在**；
> 第 4 行 S2、第 6 行 S6、第 7 行矩阵探针**仍未做**；第 5 行 `compat.py` 的乐观注释
> 也要按实际情况继续校正。
>
> ✅ **2026-09-17 P2 更新**：`theme` 默认 `ap_lite`（AP-lite 抽象 emoji，`neutral` 可回旧观感，
> `ap_bubble` 可选但默认不启用，避免过度卡通）；`/larkdeck status` 顶部加两行**聚合诊断**
> （能力/链路 + 运行/账本，含入站年龄与失败计数，有明确异常才带 ⚠️，不写「正常/健康」）；
> `/larkdeck config` 提供只读视图与 `ctx.get_config()` 热刷新（异常全有全无；不是文件系统事务）；
> **聊天侧没有写入命令**（handler 拿不到发送者身份，无法安全授权）—— 写配置走官方 Hermes CLI /
> 配置文件，再 `config reload` 生效。
>
> ✅ **2026-09-17 CLS 观感更新**（读了 `Cheerwhy/hermes-lark-streaming` 源码后按用户截图落地，
> 不照抄机制）：面板摘要行 `💭 思考 1.6s · 🛠️ 工具执行 · 3 步`；展开后正文里有
> `💭 思考` / `🛠️ 工具执行` 两个灰色分区小标题；工具行 = 图标 + 加粗动作名 + 耗时
> （`25 ms` / `1.2 s`）+ 带颜色的状态词（`Succeeded` 绿 / `Running` 青绿 / `Failed`、`Blocked` 红），
> 命令或 skill 名另起一行灰色小字；被 80 字符截断的 JSON 预览走有界 key 提取，不再丢细节、
> 也不倒原文。CLS 用 `lark_md` + 动态 icon 元素；我们是固定 5 元素结构 + emoji +
> `markdown` 里的 `<font color>`，所以这是**行为问题解的对齐**，不是机制复刻。
>
> ⚠️ **v0.6.0 发布边界**：`<font color>` 真机三类消费者视觉未确认，因此默认
> `panel_color_tags: false`（状态词/灰色标题/细节行无色降级）；上面描述的彩色状态词需在
> 配置中显式打开。无色路径仍保留状态词文字与面板边框色。

**未决**：**路线本身不必换**（7.7 第 2/3/4 条：B 没有效果优势，两边同样没有契约保护）。
但「**公开契约覆盖了我们要的东西**」这个叙事**不能再讲**（7.4）。


## 八、P3 六家候选的 go / no-go 实验记录（2026-09-17）

> 结论先行：**只落地了 cron / 无网关 standalone sender**；其余五条都有**可复跑的反证或明确阻塞条件**，
> 按用户要求**如实登记，不硬做**。判据优先级仍是真机 > 官方源码 > 本地代码 > 推理。

| # | 候选 | 出处 | 实验 / 证据 | 结论 |
|---|---|---|---|---|
| 1 | **markdown 表格超限降级** | AP / HLS / HFC | 官方核心 `plugins/platforms/feishu/adapter.py:3468-3477` **明确保留表格为 markdown `post`**：注释与 #26841 记录显示「按表格强制转 text」的旧分支被删掉，因为那会让用户看到 pipe-and-dash 原文；核心没有「单卡最多 N 表」的限制。我们的字节闸门/元素墙已覆盖真实的 128000 字节风险；`docs/plan-v1.md` R6a 也记过「不做表格降载」（语义会变、收益不抵风险） | **NO-GO**（无实测到的失败面；不做语义改写） |
| 2 | **cron / 后台任务推卡片** | HFC / CLS | 官方 `PlatformEntry.standalone_sender_fn` 是对外契约字段；内置 Feishu sender 自己 new 官方适配器（`plugins/platforms/feishu/adapter.py:4130`）⇒ cron 永远纯文本。P3 用 `_make_standalone_sender()` 把它换成经我们的 `_factory` 构造的合并适配器，并在发送前用 `compat.ensure_standalone_client()` 补官方同款 SDK client（真机审计复现过漏这一步 ⇒ `Not connected` ⇒ 文本投递整条丢）。文本走卡片、失败 fail-open 到内置；**带媒体附件的那一块回落内置**（附件不丢；分块长文里非末块仍可能走卡片路径，卡片硬异常会在末块附件前停下）。`check_override.py` 真实加载器核对 entry 字段被有意覆盖为可调用卡片 sender，且 SDK client 能真的建出来 | **GO · 代码级已实现；无网关 cron 真机投递待验** |
| 3 | **结果不明保护（不重发）** | HFC 最完整 | ✅ 我们的 CardKit 实体/元素写已有**确定性 uuid**（`ld-msg-{card_id}` / `ld-{card_id}-{element}-{seq}`），限流重试幂等；⚠️ 但**卡片首发也走**内置 `_ld_send_card` → 官方 `_feishu_send_with_retry` → `_send_raw_message`，后者在 `plugins/platforms/feishu/adapter.py:3600/3612` 每次尝试 `uuid.uuid4()` ⇒ 「已送达但响应丢失」时官方重试可能重复；`send()` 的 fail-open 还可能造成卡片+文本双份。修复要覆盖官方私有 `_send_raw_message`，与「私有名只在 `compat.py`」冲突且会改变核心文本路径 | **PARTIAL · 登记残余**（CardKit 元素写幂等；消息级首发/文本均继承官方非幂等重试；等上游） |
| 4 | **流式期间动态结构写**（每工具一个元素） | AP / HLS / FC / CLS | 早前真机已证 `card_element.create` 在流式期间可用（§5）；但当前卡片是**固定 5 元素结构**，动态加元素要新状态机 + 元素预算 + sequence/失败回收，属于 `docs/plan-r11.md` 的 Phase C 前置 | **DEFER**（条件：Phase C 前置缺口补齐） |
| 5 | **图片 URL→上传→`img_key`** | CLS / AP | 官方核心只保证 `MEDIA:` 文本路径；卡片元素要 `im.v1.image.create` + `img_key` 缓存 + 上传配额/失败回落，属于新的网络/存储故障面；当前没有用户点单 | **DEFER**（无点名需求） |
| 6 | **`/model` 两级选择 / `/new` 命令卡** | HFC | 需要介入网关命令分发/状态，超出当前「只读观察型 7 钩子 + 适配器覆盖」范围；且需要用户实际在飞书里用这些命令才有收益 | **NO-GO（当前范围）** |

> 边界说明：**自动定时 cron 仍需要 gateway 进程在跑**（ticker 只挂在 gateway）；standalone
> 车道覆盖 `hermes cron run` 与无常驻网关进程时的 `send_message`。真机 `hermes cron run`
> 无网关投递（卡片渲染、失败回落、媒体混合）本次未验证，已在 README/CHANGELOG 如实标注。

---

## 九、血统盘点：每个继任者修了前任什么、自己独有什么（2026-09-20）

> **起因**：用户在做「逐个装一遍」的实测，要求把这条线上每个插件**相对前任**的贡献盘清楚。
> **与 §8 的分工**：§8 回答「**哪些能力值得我们抄**」；本节回答「**谁修了谁的什么、谁独有什么**」。
> **取证纪律**（沿用本项目规矩）：每条标来源 —— 【各家自述】= 其 README/CHANGELOG/CUSTOMIZATIONS；
> 【我复核】= 我读过它的代码；【实测】= 我们真的装上跑过（§9.4）。**不把第三方 README 当事实。**

### 9.1 血统与机制

```
CLS   Cheerwhy/hermes-lark-streaming          【A 类：AST 注入改 Hermes 源码】
 ├─ v0.7.0 分叉 ─► ALS   Aowen-Nowor/hermes-lark-streaming   【B 类：改成 monkeypatch】
 │                   ├─► aiduPOP  monkey2jack/aiduPOP        【B 类】
 │                   └─► HLS      BcubBo/lark-hls-v2         【B 类】
 └─ v0.12.0 重写 ─► fry-cards  techysy/hermes-fry-cards      【A 类：仍用 AST 注入】

HFC   baileyh8/hermes-feishu-streaming-card    【A 类 + sidecar】← 独立一支，不在这棵树上
```

| 关系 | 出处（原文，【各家自述】） |
|---|---|
| **HLS ← ALS** | HLS 的致谢：「本插件基于 Aowen-Nowor 的飞书流式卡片插件 fork 而来」 |
| **aiduPOP ← ALS** | **ALS 自己的 README**：「PyPI 上的 `hermes-lark-streaming`（2.x，"aiduPOP"）不是我们发布的 —— 第三方衍生品，**forked from this repo** with an extra theme layer，却签了原作者的名字」 |
| **ALS ← CLS** | ALS README 把 Cheerwhy 那个称为「**the upstream plugin**」，并警告两者**互不兼容**（装之前要先卸掉对方） |
| **fry-cards ← CLS** | fry-cards README：「本项目基于 Cheerwhy/hermes-lark-streaming **v0.12.0** 独立开发」 |
| **ALS 换了机制** | 【我复核】ALS/HLS **无 `patcher.py`、无 `.hermes_lark.bak` 写入** ⇒ 确认只 monkeypatch；CLS/fry-cards 两者都有 ⇒ 确认改源码。「ALS 基于 CLS」与「CLS 是 A 类、ALS 是 B 类」**同时成立**（分叉后换了取事件的方式） |

### 9.2 逐个盘

#### ① CLS —— 这条线的源头（能力基线）

- **基线能力**【各家自述】：流式卡片（打字机）· 思考过程 · 工具调用面板 · CardKit v2.0 · 终态卡片（token/耗时/上下文）· 卡片样式配置 · 消息撤回保护 · 图片解析 · 中断处理 · cron 卡片推送 · 后台任务卡片推送 · 中英双语。
- **历史贡献**【各家自述 CHANGELOG】：cron 卡片推送（修 #15）· `panel_expanded` 配置（修 #28）· 后台任务卡片推送（修 #38）。
- 🔴 **【实测】在 0.21.1 上装不了**：`verify` 报 `Cannot find _handle_message_with_agent in run.py`，**退出码 1**。它盯的是**拆分前**的 `gateway/run.py`；上游 issue **#105**（2026-09-03 开）**至今未修**。

#### ② ALS（← CLS v0.7.0 分叉）

**修了前任什么**

| 问题 | CLS 的状态 | ALS 的解法【各家自述】 |
|---|---|---|
| **元素溢出必撞 `300305`** | 无上限 ⇒ 长思考必溢出 | `max_tool_steps` / `max_reasoning_rounds`（默认 20/20）+ 折叠摘要行 + **封卡时递归统计全部元素、超 195（200−5）就裁最旧的面板子项**；**答案 / 页脚 / 错误面板永不裁** |
| **升级要重装** | AST 改磁盘源码，`hermes update` 会覆盖 | **换成 monkeypatch**（不改磁盘）—— fork 后最大的一处改动 |

**独有特色**：**统一面板**（推理+工具合并；Header 显示模型/轮次/工具数/耗时/上下文）· **`/aowen` 运维命令**（help/status/monitor/config reload，**直接回卡不经 AI**）· **渠道活性自检**（`record_inbound()` + monitor 显示「最近入站」，治 lark-oapi WS **静默断连** —— 它自述生产 2 个月断连 41 次**零日志**）。

- 🔴 **【实测】网关启动死锁**（详见 §9.4）。机制【我复核】：`plugin/__init__.py:237` 在**注册时同步**调 `apply_patches()`，而它会 import Hermes 模块 ⇒ 与插件发现线程构成 **import 竞态**。
  ⚠️ **诚实标注一处矛盾**：ALS v1.8.2 的 CHANGELOG **自称**「生产 patch 面 100% 兼容」0.21.1，并称做过「行为级验证 41 项检查」。**我们这台机器上实测撞上了死锁**。两者都是数据点 —— 说明这是**竞态**（取决于线程交错），不是确定性失败。
  ⚠️ **旁证**：**aiduPOP 的 README 明写它修过这个 P0**（「修复 `model_tools` 模块级导入与后台插件发现线程之间的 `import_lock` 死锁……将 `apply_patches()` 改为异步守护线程延迟执行」）⇒ 这个坑在**下游被修过，上游没修**。

#### ③ aiduPOP（← ALS v1.6.0）—— 文档最扎实的一个

它有 `docs/CUSTOMIZATIONS.md`，**9 项定制，每项都写 Problem / Solution**【各家自述】：

| 定制 | **它解决的根因**（原文摘） |
|---|---|
| **稳定模型显示**（v22.2） | `threading.local()` **不跨 `asyncio.create_task` 边界** ⇒ 任务 A 写的模型、任务 B 读到空 ⇒ 模型名闪烁/消失。改成模块级全局 dict `_model_cache` |
| **Phase 2 回滚保护**（v22.3） | 飞书 `batch_update` 是**原子的** —— 删一个不存在的元素（`300314`）会**回滚整批** ⇒ 答案与面板**都建不出来**。改成精确跟踪 `existing_elements` + 命中时丢弃陈旧跟踪立即重试 |
| **Adapter 三类身份解析**（v1.5.4） | 治「类身份可能不是我以为的那一个」 |
| Clarify 卡片投递修复（v1.6.0） | — |
| 面板位置 / 页脚移除 / 模型格式 | 答案在上、面板在下；模型移到面板 header |
| 卡片失败自动重建 | API 失败时缓存失效 + 重试 |
| **泡波样式** | 主题层 `cardkit/theme.py` + emoji 工具图标 |
| **`aidupop studio`** ⭐ | **可视化配置工作坊**（本地 Web、1:1 卡仿真预览）—— **本清单里唯一别人都没有的** |
| **Markdown 防爆引擎** | 表格超限无损压成 `Table N · Row M`；长文断层保护不腰斩代码块 |

- ✅ **【实测】能跑**（补丁全绿、真的发出卡片）。
- 缺陷【实测】：澄清链的 import 写错模块（实测 `ImportError`）⇒ **软锁态不可达**；且**不看提交结果** ⇒ 超时后补点会刷**假确认**（详见 §7.5）。

#### ④ HLS（← ALS）

**独有特色**【各家自述】—— **三项是这条线上别人都没有的**：

| 特色 | 说明 |
|---|---|
| **动态台词系统** ⭐ | Fisher-Yates 洗牌队列（一轮不重复、同源间隔 ≥5）+ **场景检测**（greeting/thinking/battle/victory/defeat/seal）+ 台词库 JSON |
| **群成员管理** ⭐ | 群消息自动入库 + 每 5 分钟飞书 API 同步 + **按群隔离** `(open_id, chat_id)` + open_id↔user_id 自动互绑 + 限流保护 |
| **用户权限系统** ⭐ | `admin:<用户名>` 注入 `source.user_name`（AI 可直接识别权限）+ SQLite 缓存 + 三级优先级 `manual > auto > api` |
| 嵌套折叠面板 / 批次分组 | 每轮推理独立面板；已完成推理按批次（默认 10 轮/组）合并以降低高度 |
| 答案质量优化 | 流式阶段标题降级 + 未闭合 markdown 截断，消碎片闪烁 |

- ⚠️ **未实测**。静态检查【我复核】：关键 patch 名字在 0.21.1 上**都在**（`add_reaction`/`delete_reaction` 缺，但有 `_add_reaction`/`_remove_reaction` 兜底）⇒ 大概率可跑。
- ⚠️ **预判带同族缺陷**【我复核】：`interceptors/adapter.py:725` 是**与 ALS/aiduPOP 完全相同的导错 import** ⇒ 澄清软锁态大概率同样失效。

#### ⑤ fry-cards（← CLS v0.12.0 独立重写）

它 README 有**官方三层差异表**【各家自述】：

**✨ 新增功能 5 项**（括号内是它写的 CLS 状态）

| 功能 | 上游（CLS）状态 |
|---|---|
| **统一面板** | ❌ 推理面板独立散排，多轮对话卡片冗长 |
| **上下文进度条** | ❌ 仅 footer 纯文本百分比，无开关（它做了 `text`/`bar`/`text_bar` 三模式） |
| **推理面板上限** | ❌ 无限制，长思考必溢出 |
| 模型名截断 | ❌ 全称显示（移动端换行） |
| 模型别名 JSON | ❌ 无（`~/.hermes/model_aliases.json`，改完即生效） |

**🔧 关键修复 4 项（都是 CLS 的坑）**

- **`300305` 超限强制拆卡恢复** —— 不再永久卡「处理中」
- **远程图片 URL 过滤** —— 规避 CardKit 的 `200570 invalid image keys`
- **完成态 Duplicate ID 修复** —— 上游完成态用**固定的 `reasoning_text`** 会与流式阶段元素冲突 ⇒ **卡片卡 loading**；改成 `text_el_id` 全链路复用 + 带索引唯一 ID
- **seal 失败清理 loading 图标**

**🏛️ 内部稳定性重构**：session 生命周期统一入口（幂等）· FlushController 竞态防护 · `mark_failed(reason=)` 可追溯 · 单一回落决策点。

- ✅ **【实测】能跑**。代价：**A 类**，改 **6 个 Hermes 源码文件**（406 行），`hermes update` 后要重跑 `install`（详见 `SWAP-RECORD.md`）。

#### ⑥ HFC（baileyh8，**独立一支**）

- **定位完全不同**：badge 写着 **Sidecar-only** —— 卡片在**独立常驻进程**渲染，Hermes 里只留最小 hook；17 个 patch group + 源码 hash 校验【各家自述】。
- **它列的「解决什么问题」**【各家自述，适用场景表】：只看到最终文本看不到过程 · 运行中不断冒 `Working`/压缩提示/skill loading · **话题里卡片发了但 timeline 不更新** · 授权/选择/模型切换要手工回编号 · **Hermes 升级后不知道 hook 是否兼容**（`doctor --explain` 展示 `version_source`/`hook_strategy`/`compatibility`/anchors）。
- **特色**：运行态 Header 实时显示当前工具动作 · 主答案/过程分区 · **`/model` 与 Hermes CLI 同源的两级选择** · `/hfc status|doctor|monitor` 运维卡 · 长内容按**结构边界**拆分 · **升级与服务安全**（认证 hello/heartbeat、strict repair 不自动重启、`lark-oapi` 版本体检）· **V4.2 起裸 `/update` 维护确认卡**（确认后自动跑官方 `hermes update` 并恢复 hook）。
- **规模**：676★、**取证时当天仍在提交**、~48k LOC。
- **代价**：**最重** —— 常驻进程 + 服务管理 + 独立配置文件 + 完整性账本。**用户暂不测。**

### 9.3 一页总结

| 插件 | 路线 | 修了前任什么 | 独有特色 | 实测 |
|---|---|---|---|---|
| **CLS** | A | —（源头） | cron 卡片、后台任务卡片 | ❌ **装不上** |
| **ALS** | B | 元素溢出必撞 300305；升级要重装 | 统一面板、`/aowen` 命令、渠道活性自检 | ❌ **网关卡死** |
| **aiduPOP** | B | 模型名闪烁（`threading.local` 跨 task 失效）、300314 整批回滚、类身份漂移 | **studio 可视化配置**、泡波主题、Markdown 防爆 | ✅ 能跑 |
| **HLS** | B | 继承 ALS | **动态台词**、**群成员管理**、**权限系统** | ⚠️ 未测 |
| **fry-cards** | A | CLS 的推理面板散排、完成态 Duplicate ID 卡 loading、300305 不恢复 | 上下文进度条三模式、模型别名 | ✅ 能跑 |
| **HFC** | A+sidecar | 流式漏字/乱序、话题卡不更新、**升级后 hook 兼容不确定** | sidecar 架构、升级维护确认卡、运维卡 | ⚠️ 暂不测 |
| **larkdeck**（我们） | **C** | —（不在这棵树上） | 官方契约接入、267+ 条变异门禁 | ✅ 现役 |

### 9.4 我们自己的装机实测记录（本节证据强度的来源）

| 插件 | 装过 | 结果 | 证据 |
|---|---|---|---|
| CLS | 试装（pip + verify） | ❌ 拒绝 | `verify` 退出码 1；22 个分支全无模块化路径 |
| **ALS** | **装了**（`hermes plugins install`） | ❌ **网关启动死锁** | 进程 CPU 0%、零日志、`gateway.pid` 未写；`sample` 抓到主线程 `rlock_acquire`；飞书断连约 3 分钟；已回滚 |
| fry-cards | 装了 | ✅ 工作 | 16 hook installed；6 文件 406 行；卸载后**逐字节还原**（sha256 对账 7/7） |
| aiduPOP | 装了 | ✅ 工作 | 补丁全绿 + 真发出卡片 |
| HLS | 未装 | — | 静态检查通过 |
| HFC | 未装 | — | — |
| larkdeck | 现役 | ✅ | 启动自检「钩子 8/8 · feishu 平台已由 larkdeck 接管」 |

完整时间线与回滚路径：`~/.larkdeck-scratch/fry-swap/SWAP-RECORD.md`（**不在仓库里** —— 含本机路径与真实 ID）。

### 9.5 三条值得记住的规律

1. **每个继任者都在修「上游撞飞书硬限制」的坑** —— `300305`（元素 200 上限）、`300314`（原子回滚）、完成态 Duplicate ID、`200570`（远程图片 URL）。**这是这条路线的共同战场**，也是它们相对彼此的真正增量所在。
2. **唯一没人解决的是澄清链** —— ALS 那一支（**ALS / aiduPOP / HLS 三个**）带**同一个导错的 import**（`from lark_oapi.api.cardkit.v1 import P2CardActionTriggerResponse, CallBackCard`，实测 `ImportError`）。aiduPOP 修了死锁、修了模型闪烁、修了原子回滚，**却没修它**。
3. **往上追溯源版本是错的方向** —— 祖先在 0.21.1 上全都不能用（CLS 装不上、ALS 死锁），0.21 支持都在**下游的 fork / 重写**里（fry-cards、aiduPOP）。

