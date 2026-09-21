# 窗口 1 live 状态（V1+V2 structured canary）

- 时间：2026-09-21 10:18（Mac 本机）
- 软链：`~/.hermes/plugins/larkdeck -> /Users/Zayn/Code/larkdeck/.deploy`
  - `.deploy` HEAD = `0e0d1f9`（v0.7.1-visual，含 V0–V3）
- 配置：`plugins.entries.larkdeck.settings.visual_engine = structured`
  - 默认 legacy 未改；`card_status_header=true`、`show_reasoning=false`
  - 备份：`~/.hermes/config.yaml.bak.larkdeck-v071-*`
- 网关：launchd 托管，PID 92944（10:18:20 启动）
- 启动自检证据（`~/.hermes/logs/agent.log`）：
  - 10:18:18 `启动自检通过：Hermes 0.21.1 · feishu 平台已由 larkdeck 接管 · native 传输 cardkit · 钩子 8/8`
  - 10:18:24 `visual_engine=structured canary 已启用（默认仍 legacy）`
  - feishu websocket 已连接，home channel 启动通知已发
- 独立探针证据（不重启网关）：`tests/probe_render.py --structured-canary`
  - seed/update/finalize 三帧全部 True；含推理轮、terminal 工具、Result 块

## 窗口 1 待用户截图确认
1. 流式工具行：`div + standard_icon`、加粗动作、耗时、彩色状态词、22px 灰字细节
2. 同一张卡收尾：结构不回退 markdown、状态条蓝→绿、页脚顺序 状态/耗时/模型/ctx/短码
3. `/stop`：黄边/黄状态条、正文与短码不丢、结构不退化

## 回滚
```bash
# 方案 A：关 canary（保留 .deploy 代码、默认 legacy）
# 编辑 ~/.hermes/config.yaml 删除 plugins.entries.larkdeck.settings.visual_engine
hermes gateway restart

# 方案 B：恢复原配置与开发树软链
cp ~/.hermes/config.yaml.bak.larkdeck-v071-* ~/.hermes/config.yaml
ln -sfn /Users/Zayn/code/larkdeck ~/.hermes/plugins/larkdeck
hermes gateway restart
```

---

## 真机第一轮：用户截图 + 日志回收（2026-09-21 10:2x–10:40）

### ① 截图确认（structured canary 在真机上确实成立）

用户在 DM 里贴了一张**工具回合**的卡（`terminal · df -h`，回复锚点「帮我用终端看一下当前
磁盘占用 / 跑个 df -h」，对应 `agent.log` 10:21:56 那条入站消息）。逐项对照：

| 设计项 | 截图所见 | 判定 |
| --- | --- | --- |
| 卡级状态头（绿底 + ✅ 已完成 + 回复锚点） | 绿底、`✅ 已完成`、上行是回复引用 | ✅ |
| 面板外层（标题 `💭 思考 2.9s · 🛠️ 工具执行 · 1 步`、右侧箭头、圆角、绿边） | 全部在位 | ✅ |
| 工具行（`standard_icon` + `⚙️ terminal (347 ms) · Succeeded`） | 图标 + 粗体工具名 + 耗时 + 绿色 Succeeded | ✅ |
| 细节行（灰色 `↳ {"command": "df -h"}`） | 在位，缩进正确 | ✅ |
| Result 块（`**Result**` + 灰底代码块） | 在位 | ✅ |
| 页脚顺序（状态 → 时长 → 模型 → ctx → 短码） | `✅ 已完成 · 🧠 deepseek-flash · ctx 20.5k/1m · 2%` | ⚠️ **缺时长、缺短码** |
| reasoning 默认关闭（同轮摘要不出现） | 面板里没有 `💭 思考 · N` 嵌套轮 | ✅ |

结论：**V1–V3 的结构化渲染真机成立**；发现一个真缺口 —— `structured` 路径的页脚没有走
`_ld_frame_footer`（短码）也没有传 `started`（时长），所以两项都缺。列为 **V4.2**。

### ② 日志回收：`code=200770`（本阶段最重要的真机发现）

10:39 那个回合（「现在主模型是什么」）中间出现唯一一条装饰写失败：

```
2026-09-21 10:40:01,660 WARNING larkdeck: CardKit 装饰写入失败 code=200770（panel）
```

`200770` **不在任何已知码表里**（300309/300313/300317/300315/230020），日志当时也没留 `msg`
⇒ 读源码读不出来。于是做了两件不猜的事：

1. **字段级探针** `tests/probe_partial.py`（只建卡实体、不发消息）：把 `panel_partial` 拆成
   逐字段矩阵打给飞书 —— **全部 `code=0`**（含 2776 字节的完整载荷、只 header、只 elements、
   去 `vertical_spacing`/`border`/`expanded`、空 partial），幽灵元素对照得 `300313`。
   ⇒ **载荷本身合法**，不是字段问题、不是体积问题。
2. **并发探针** `tests/probe_concurrent.py`：同一张卡上同时发两份写，三种冲突各一轮 ——

   | 冲突形状 | 返回 |
   | --- | --- |
   | 同 seq + 同 uuid + 不同内容 | **`200770` · `ErrMsg: this UUID has been recently consumed;`** |
   | 同 seq + 不同 uuid | `300317` · `sequence number compare failed` |
   | 不同 seq + 不同 uuid（纯并发，乱序到达） | `300317` · `sequence number compare failed` |

根因：`panel` 是**唯一会被两条路径写的元素** —— 帧路径（`_ld_stream_frame_structured`）与工作心跳
（`_ld_heartbeat_loop`）。两条路径都从同一份 `state` 里算 `seq = _ck_seq(state) + 1`，而 uuid 由
`(card_id, seq)` 推出（`ld-{card_id}-s{seq}`）⇒ 同一毫秒会发出两份 (seq, uuid) 相同、内容不同的写。

⚠️ 比 200770 更糟的是对照那一格：并发**乱序到达**拿到 `300317`，而它在
`_CARD_DEATH_DECOR_CODES` 里 ⇒ 会被判成**卡级死法、整卡降级**（打字机没了，回落 patch 车道）。

### ③ 收口（V4.1）

* `_ld_card_lock(key)`（回合级异步写锁，键 = `chat:turn_id`）+ `_ld_card_lock_drop`；
  帧路径整帧体在锁内（`_ld_stream_frame_structured` → `..._locked` 外壳）。
* 心跳：**撞上锁就跳过这一拍**（不排队），拿到锁后**重读** state（序号必须新鲜）；
  一拍心跳的四种结局收口成 `_ld_heartbeat_tick` 的返回值（`skip/stop/unchanged/wrote`）。
* 装饰失败日志**带上服务端 `msg`**（真机上「码是分类、msg 才是事实」）。
* 单测 3 条（`test_v4_1_*`，其中并发揉进「写出去了还没回来」的确定性窗口）；
  变异 `V4-2..V4-6` 五条 **5/5 全部变红**（`mutate_check.py -k V4-`）。

---

## 部署与 V4.2 真机复核（2026-09-21 11:39–11:45）

用户同意后执行（顺序即纪律：先换代码、再重启、最后才看结果）：

```
git -C .deploy checkout --detach ccde909     # .deploy 是 detached worktree，软链不动
hermes gateway restart                        # 11:39 / 11:40 两次启动，最终单实例
```

**启动证据（`agent.log`）**

```
11:40:07  INFO larkdeck: [larkdeck] 启动自检通过：Hermes 0.21.1 · feishu 平台已由 larkdeck 接管 ·
          native 传输 cardkit · 钩子 post_api_request/on_stream_start/on_stream_delta/on_stream_end/
          pre_tool_call/post_tool_call/pre_gateway_dispatch/on_session_end · /larkdeck 命令已注册
11:40:07  INFO hermes_plugins.feishu_platform.adapter: [Feishu] Connected in websocket mode (feishu)
11:40:07  INFO gateway.run: ✓ feishu connected
```

* 单实例：`pgrep -f "hermes_cli.main gateway run"` ⇒ 1 个真进程（+1 个 stderr 包装器）；
* 载入版本：`.deploy` 的 `git rev-parse --short HEAD` = `ccde909`（V4.1 + V4.2 都在里面，
  `grep -c "_ld_card_lock\b|_ld_heartbeat_tick|footer=f\"{base_footer}"` 三处都在）；
* 唯一的报错是重启瞬间的 websocket `ConnectionClosedOK`（旧连接正常关闭），不是插件问题。

**真机探针**（`tests/probe_render.py --structured-canary`，走部署树、发真卡）：

```
structured canary: seed=True update=True finalize=True
```

这一次的配置是 `visual_engine=structured` + `show_reasoning=true` ⇒ 卡片里应能同时看到
**嵌套推理 A 形态**与**新页脚**，是「窗口 2」的现成样本。

**离线转储工具**（新增 `tests/dump_structured_card.py`，零网络/零副作用）：把「最终那张卡」
的 JSON 直接打出来，省掉一次真机往返。实测输出：

| 模式 | 页脚 | 元素数 | 字节数 |
| --- | --- | --- | --- |
| `show_reasoning=true` | `✅ 已完成 · ⏱ 12.0s · 🔖 m_ck_1` | 31 | 3372 |
| `show_reasoning=false`（默认） | 同上 | 27 | 2676 |

JSON 逐项核对：面板标题 `💭 思考 0.0s · 🛠️ 工具执行 · 3 步`（**无模型名**）、工具行
`div(icon=standard_icon: app-default_outlined / setting_outlined / file-link-text_outlined,
text=lark_md: **terminal** (347 ms) · <font color='green'>Succeeded</font>)`、细节行与
Result/Error 行都是**独立 div + margin 22px**、推理轮是嵌套 `collapsible_panel`
（`reasoning_0_panel` / `reasoning_0_text`）、面板 `border.color=green` + `5px` 圆角、
`streaming_mode=false`。⇒ 与冻结计划里的元素树蓝图逐条一致。

**仍未完成（挡住发布）**：用户窗口 1 的**中途截图**与 **`/stop` 黄边截图**；V4 阶段三方
对抗审计收敛；翻默认 `visual_engine=structured`；终验截图；push/tag/release。

---

## V4.4–V4.6：三方审计回报后的收口（2026-09-21 12:xx）

三个对抗审计（并发正确性 / 规格一致性 / 测试反假绿）回报后，**用户窗口 1 的截图**与审计
一起暴露了 6 类真问题。逐条与处置：

| # | 症状（谁发现） | 根因 | 处置 |
| --- | --- | --- | --- |
| 1 | `/stop` 回复卡还是旧 markdown 面板：工具名 `Load skill`/`Search`/`Run command`、无 22px 缩进、页脚无时长与短码（**用户截图 4**） | `send()`/`edit_message()` 两条**非流式回落车道**从没接过结构化元素树 | 新增 `_ld_render_card()`：引擎是 structured 就用同一棵 `entity_skeleton`（超预算退回旧渲染器），`send`/`edit_message` 两处接入（V4.4） |
| 2 | `show_reasoning=false` 时推理正文照样上卡（审计 B 高-1；同一张截图也看得到推理轮） | 三个 legacy 渲染器只「读」配置不「用」配置 | `cards.panel_rounds_markdown/unified_panel` 增 `include_text` 语义：false ⇒ **只留 `💭 思考 · 1.6s` 摘要行**，正文一个字不进面板；三条 legacy 车道（含 DEGRADE）全部接上（V4.4，golden 夹具同步声明变更） |
| 3 | 失败回合的收尾卡照样**绿头绿边**（审计 B 高-2：`error` 色在结构化下不可达） | 帧路径写死 `status = "completed" if finalize else "processing"`，没有把面板快照的 `ok/error/stopped` 映射过来 | 新增 `_ld_view_status()`；帧路径 / 静态车道 / 收尾整卡都按快照结局着色，并加**端到端**门禁（失败回合 finalize ⇒ `header.template == "red"`、面板边框红）（V4.5） |
| 4 | **结构化 + 默认 `body_source=own` 时打字机等于没有**：中间帧正文元素一直写 `⏳ 正在生成…`，只有收尾靠 core 终稿兜底（审计 A 中-1；**用户截图 2 正是这个形态**） | 结构化 seed 状态不写 `answer_gen`，delta 之后世代守卫每帧都判「漂移」按空正文 fail-open；legacy 有补种分支、structured 漏了 | 结构化路径补上与 legacy 同形的**世代补种**，并加门禁（structured+own 中间帧必须写累积正文，不许占位符）（V4.6） |
| 5 | 正文/页脚拿到卡级死法（`300309`/`300317`）时**不降级**：直接掉 native、卡冻在流式态；面板那条缝则是不落账、下一帧重号再 patch 一遍（审计 A 高-4 / 中-5） | 码表判断只在面板分支里，且 DEGRADE 早返回不写 `ck_seq`/`card_id` | DEGRADE 抽成单点 `_ld_structured_degrade()`，面板/页脚/正文三处共用；成功即落 `ck_seq=seq`、`card_id=""`（账本切 patch 车道）（V4.6） |
| 6 | 心跳把「写失败」伪装成 `unchanged`、把「卡级死法」伪装成 `stop`：无日志、不落账、每 3 秒静默重试同号（审计 A 中-3） | `_ld_heartbeat_tick` 的返回值语义与事实不符 | 拆出 `failed`（限流 WARNING 带 msg）与 `dead`（落 `ck_degrade`/`engine_stamp` 并让帧路径接手），循环只在 `stop/dead` 退出（V4.6） |

同批修掉的还有：面板 partial 的 **uuid 命名空间**与 `card.settings` 分开（`-p` vs `-s`，审计 A 中-2
实测过同卡同 seq 撞出重复 uuid）；`/stop` 重绘**拿同一把回合锁**、锁被持有时不摘表、帧收尾
**不复活已被 pop 的回合**（审计 A 中-6）；工具状态表与 `cards._TOOL_STATUS_STYLES` 对齐
（`blocked/timeout` 红、词不再漂移，审计 B 中-5）；结构化也尊重 `unified_panel` /
`panel_expanded` / `max_panel_steps` / `max_reasoning_chars` / `max_tool_result_chars` /
`streaming_print_ms`，且收尾/停止/切卡的整卡 patch 都补上 `apply_text_profile`（审计 B 中-4）；
心跳的面板标题与帧路径统一口径（墙钟 + 真实步数，审计 B 中-3）。

**门禁**：`test_units` 259/259；`run_fast --full` 全绿（含 `mutate_preflight` 锚点全唯一）；
新增变异 V4-10…V4-21 覆盖上表每一条（`mutate_check.py -k V4-`）。
