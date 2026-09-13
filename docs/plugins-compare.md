# 同类飞书流式卡片插件横向对比（2026-09-13）

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

**共同点**：六家**全部**要改 Hermes 运行时（AST 注入或 monkeypatch），**没有一家**是
`register_platform` 合规插件。这正是本项目的立项理由（Hermes 一升级，注入就被冲掉；
NAS 上还得挂「每次开机重新注入」的脚本）。**所以我们要抄的是它们解决过的「行为问题」，
不是它们拿事件的方式。**

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
| **多卡拆分**（元素近 200 自动封旧卡开新卡） | ❌（28KB 上限硬门禁） | ✅ 阈值 180 | ✅ 195 | ✅ | ✅ 180 | ✅ 200−5 | ❌ **只有降载档位** |
| **点击即回卡**（回调响应里直接换卡） | ✅ | ❌ | ✅ `CallBackCard` | ✅ | ✅ | ✅ | ❌ |
| 澄清三态（pending→submitted→confirmed） | ✅ | ❌ | ✅ + 重试提交 | ✅ | ✅ | ✅ | ⚠️ 有回填，无「提交中」态 |
| **流式期间改结构/加元素** | ❌ | ✅ `batch_update` | ✅ | ✅ | ✅ | ✅ | ❌ 见「重大更正」 |
| 长文封卡分片（`insert_after` 追加元素） | ✅ 结构边界切 | ✅ | ✅ | ✅ 2400 字分片 | ✅ | ✅ 4000 字分片 | ❌ 单卡 + 截断 |
| markdown 表格超限降级 | ✅ 字段列表 | ✅ 代码块 | ✅ 代码块 | ✅ 无损压扁 | ✅ 代码块 | ✅ 代码块 | ❌ |
| 图片：URL→上传→`img_key` 替换 | ✅ | ✅ | ✅ | ✅ `img` 元素 | ✅ | ✅ | ❌（依赖官方 `MEDIA:` 路径） |
| 撤回/删除后停止更新 | ✅ | ✅ 30min TTL | ✅ | ✅ 卡片 TTL | ✅ `unavailable_guard` | ✅ | ❌ |
| 渠道活性监控（WS 静默断连可诊断） | ✅ `/health` | ❌ | ✅ `/aowen monitor` | ✅ status 卡 | ❌ | ❌ | ❌ |
| 运维/诊断命令（`doctor` / `status`） | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ 只有启动自检日志 |
| cron / 后台任务推卡片 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| 幂等投递（UUID + 有界重试 + 结果不明不重发） | ✅ 最完整 | ❌ | ⚠️ | ⚠️ | ⚠️ | ⚠️ | ⚠️ 本次刚补 uuid |
| 远端图片/非法属性被拒的兜底 | ✅ | ✅ | ✅ 300315 提取属性名 | ✅ | ✅ `200570` strip | ✅ | ❌ |

> 说明：我们列里标 ⚠️/❌ 的**不代表人家做得更好**，而是「人家有、我们暂时没有」；
> 我们已实测的硬上限（128000 字节）比它们普遍假设的 28~30KB **宽 4 倍以上**，
> 它们的「预算算法」多数建立在一个**过于保守的假设**上，**不能照抄数值**。

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

1. **流式期间新增元素 / 改边框色「客户端真的画出来了吗」** —— 只有眼睛能判。实验卡已留在 DM。
   若成立 ⇒ 推荐 #1 变成「最该做的一条」；若不成立（例如飞书只在收尾渲染结构）⇒ #1 降级。
2. 元素级接口的**配额/限流**未知：我们只知道它们不关会话，不知道每秒能打多少次。
   真要做 #1，得先按 `--rate-limit` 那套办法量一遍。
3. `card.settings` 的 `summary` 形状：我按 `{"config": {"summary": "字符串"}}` 传被拒
   （`300122 failed to unmarshal for Summary, type: string`）⇒ 它要的是 i18n 对象
   `{"content": ...}`（我们 `cards._summary_of` 就是这个形状）。**这条已由返回码确认，不算未验。**
