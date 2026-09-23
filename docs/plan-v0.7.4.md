# v0.7.4 计划（系统/命令提示静默 + 长任务面板 + 推理面板探针）

> 承接 v0.7.3（已发布：tag `v0.7.3` = `1cc6ecc`）。本文件是下一批的唯一执行依据。
> 老规矩：先出计划 → ≥3 路子代理对抗审计 → 收敛后才动手 → 每阶段结束再过审计 →
> 6 分片全量重验 → 部署真机终验 → 用户终验后发布 → neat-freak 收尾。

## 0. 来源与证据

| 项 | 来源 | 证据 |
| --- | --- | --- |
| P0 Hermes 系统/命令回复仍带 `✅ 已完成` | 用户 2026-09-23 真机截图 | `/reset` mid `om_x100b640c239740bcc2b83b679166f11`；日志 `send 判定 turn=True keys=['notify']`；内容 `✨ 会话已重置！重新开始。…` |
| P1 长任务/多卡中间卡面板空白 | 用户 2026-09-23 反馈 | `docs/audits/v0.7.3/p4-long-task-blank-panel.md`（卡片/消息 id 与日志） |
| P2 `show_reasoning=true` 嵌套面板真机渲染 | 历史挂起项 | `docs/plan-v0.7.3.md` §7；默认 false 无用户可见风险 |

## 1. P0：系统/命令提示静默（本批核心）

### 1.1 事实约束

* 上游 `notify=True` 是**所有最终回复**的通用标记（`gateway/platforms/base.py` 注释
  「Final content gets notify=True」；non-native 真实终稿同标记）⇒ **不能据此判非回合**，
  否则真实回合会丢 `✅ 已完成`。
* 上游命令回复的本地化头来自 `~/.hermes/hermes-agent/locales/{zh,en}.yaml`，例如：
  `reset.header_default/header_new/header_titled`、`resume.list_header`、`reload_mcp.header/
  confirm_prompt/cancelled`、`reload_skills.header/failed`、`stop.stopped/stopped_pending`、
  `reasoning.*` 等。
* 判据仍是「默认回合 + 已知系统/命令前缀负清单」（Design D）；新增前缀必须来自上游
  可枚举字面量，不猜模型输出。

### 1.2 拟定范围（待审计收敛）

* 扩展 `core/adapter.py::_LD_SYSTEM_NOTICE_PREFIXES`：覆盖上述本地化命令头（中英）。
* 是否把本插件自诊卡（`/larkdeck status|config`，前缀 `🃏 larkdeck`）也纳入静默：待拍板/审计。
* 测试：`test_v073_non_turn_send_has_no_footer_element` 增加新前缀硬字面量；
  真实回合 non-native 终稿（带 notify）必须保留 `✅ 已完成` 的反向用例。
* 变异：至少一条「移除新前缀 ⇒ 测试实红」；真回合保留侧已有 2c/2e 体系，按需补。
* 黄金夹具预计不受影响（前缀判断在 send 分类，不进 cardkit trace 场景）。

### 1.3 待办

* [ ] 审计 A/B/C（≥3 路：前缀完整性/误杀风险、真实回合保真、测试判别力）
* [ ] 实现 + 定向 `-k V073` 实红 + run_fast
* [ ] 6 分片全量重验（新章 full_audit_at 指向本批冻结提交）
* [ ] 部署 `.deploy` + 网关自检 + 用户真机发 `/reset`、`/new`、真实消息三类验证

## 2. P1：长任务/多卡中间卡面板空白

* 复现条件与日志见 `docs/audits/v0.7.3/p4-long-task-blank-panel.md`。
* 方案二选一（待复现后定）：无过程数据时沿用本回合最后一次非空面板；或不出面板（状态色载体另寻）。
* 需要新增/调整断言与变异，避免「长回合面板空白」回归。

## 3. P2：show_reasoning 嵌套面板探针

* 默认 false 无用户可见风险；发一张嵌套面板探针卡，用户目视后决定是否纳入渲染/默认值。

## 4. 发布与收尾

* 插件版本 `0.7.4`；CHANGELOG 新条目；发布说明 `docs/releases/v0.7.4.md`；
* 沿用 v0.7.3 的证据链纪律：v2 runner（先验片后 dry-run 再 `--write`、不带旧 fa allow-at）、
  release 读 `evidence-<fa>/`、artifact 全量 sha256、脚本 hash、git 锚定证据摘要。
* 清场与注册项按 `docs/plan-v0.7.3.md` §7 与新发现滚动更新。
