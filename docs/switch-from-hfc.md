# 从 HFC 切到 LarkDeck

**状态（2026-09-12 核实）**：**Mac 已完成切换**，**NAS 未核实**（当时从 Mac 连不上
`192.168.0.200:5667`）。剩下的活只在 NAS 那边。

顺序不能反：**先卸载 HFC，再装 LarkDeck**。反过来做会导致同一时间两个插件都想
接管 `feishu` 平台。

---

## 为什么必须卸载 HFC，不能共存

HFC 做的是**改写 Hermes 源码**：往下面 8 个文件里注入代码。

```
gateway/platforms/base.py
gateway/run_turn.py
gateway/run_turn_runner.py
gateway/run_inbound.py
gateway/run_busy.py
gateway/run_startup.py
gateway/run_notifications.py
cron/scheduler_delivery.py
```

它同时还会占住 `feishu` 平台名。LarkDeck 虽然在注册表层面赢（后写者胜），但 HFC 留在
核心文件里的注入会先一步拦住消息，两边会打架。所以只能二选一。

---

## Mac（已完成）

2026-09-12 核实并收尾：8 个核心文件里已无 HFC 注入，`plugins.enabled` 里只有 larkdeck，
启动自检通过；HFC 包与 `hermes-feishu-card` CLI 也从 venv 卸载了，
`.hermes_feishu_card_recovery.lock` 一并清掉（插件发现数 61 → 60，少的就是它）。
下面是当时的执行顺序，留作记录/复用。

```bash
# 1) 卸载 HFC（必须用它自带的 CLI，pip uninstall 会留下核心文件里的注入）
hermes-feishu-card cli uninstall --hermes-dir ~/.hermes/hermes-agent --yes
# 具体入口以 `hermes-feishu-card --help` 为准

# 2) 核实注入已被回滚：下面两个目录都不该再出现 hermes_feishu_card
grep -rl hermes_feishu_card ~/.hermes/hermes-agent/gateway ~/.hermes/hermes-agent/cron

# 3) 装 LarkDeck
cd ~/code/larkdeck && ./install.sh

# 4) 把 larkdeck 加进 ~/.hermes/config.yaml 的 plugins.enabled，删掉 hermes-feishu-card
# 5) 重启网关，看启动日志里的 [larkdeck] 自检行
# 6) 注入回滚之后再卸 python 包：pip uninstall hermes-feishu-streaming-card
```

**代价（已知并接受）**：`medflow-job-notifier` 借用 HFC 的 cron 投递卡能力给飞书任务发完成卡片。
包卸载后那条路走 `ImportError` 兜底 —— 飞书任务通知从卡片退化为**纯文本**（不报错、不丢消息，
只是样式变差）。要拿回卡片能力：把该通知改接 larkdeck，或按下条回滚 HFC。

**回滚**：`~/.hermes/backups/hfc-4.4.5-<时间戳>/` 是卸载前做的**离线包**（含说明），
或在线 `pip install hermes-feishu-streaming-card==4.4.5`。回滚后必须 `hermes gateway restart`。

---

## NAS（Docker，待做）

NAS 上 HFC 不是官方镜像自带的，而是靠一个第三方启动脚本每次容器重建时重新装回去：

```
宿主：/vol1/@appdata/1Panel/1panel/apps/hermes-agent/hermes-agent/data/scripts/hermes-scheme2-bootstrap.sh
容器：/opt/data/scripts/hermes-scheme2-bootstrap.sh
```

**这个脚本不处理掉，HFC 每次开机都会被装回来。** 顺序：

1. **先备份**这个脚本（不要直接删）—— 万一要回滚。
2. 停容器。
3. 在宿主上把该脚本移到别处（例如 `.../scripts/disabled/`），使容器启动时不再找到它。
4. 启容器。此时 HFC 不再被重装，但上一次注入的核心文件**还在镜像层里** ——
   `/opt/hermes` 是镜像内的，不是挂载卷，重启容器不会清掉。
5. 用 HFC 的 CLI 在容器内卸载一次，回滚那 8 个文件。
6. 把 LarkDeck 放到 `/opt/data/plugins/larkdeck`（`HERMES_HOME=/opt/data`，已 bind 挂载
   到宿主卷，容器重建不丢）。
7. 改 `/opt/data/config.yaml` 的 `plugins.enabled`：删 `hermes-feishu-card`，加 `larkdeck`。
8. 重启容器，确认自检通过。

### 一个更干净的选项

既然 `/opt/hermes` 是镜像内的，被 HFC 改过的那一份**已经不可信**了。想彻底干净，
可以直接重建容器（`docker compose up -d --force-recreate`，镜像不变）—— 镜像层会重新
铺开，8 个文件回到官方原版，然后在干净的底子上装 LarkDeck。

---

## 什么时候可以删那个 bootstrap 脚本

确认 LarkDeck 在 NAS 也跑稳（至少经历一次网关重启 + 一次容器重建）之后，再把备份的
`hermes-scheme2-bootstrap.sh` 和宿主上的 HFC 源码目录一并清掉。
