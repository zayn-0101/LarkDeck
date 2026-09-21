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
