# v0.7.2 发布证据（一览）

> 提交：**本支最新提交**（收口：`CAND-B2` 断言 + 测试有界等待；全量账本冻结参考 `7e7a62d`）
> · 部署：`.deploy` 指向 `7e7a62d` 同源代码（本轮只改测试/文档，**生产代码零改动**，无需重启）
> · 用户终验：**待用户真机确认**（四项见下）

## 1. 门禁（六支，全绿）

| 门禁 | 结果 | 耗时 |
| --- | --- | --- |
| `tests/test_units.py` | **279/279 passed** | ~2.8s |
| `tests/check_override.py` | `OVERRIDE OK`（真插件加载器，报 `larkdeck v0.7.2`） | ~1.8s |
| `tests/check_hooks.py` | `HOOKS OK` | ~3.7s |
| `tests/check_clarify_e2e.py` | `CLARIFY E2E OK`（含**真 SDK payload 类**的表单提交场景） | ~1.7s |
| `tests/check_cardview.py` | `CARDVIEW OK`（含独立字面量：图标表/真实工具名/spinner 三字段） | ~0.05s |
| `tests/check_cls_alignment.py --require` | `CLS ALIGN OK`（28 条逐条 + 顺序；缺席即 FAIL） | ~0.06s |
| `tests/run_fast.py --full` | 各步全 OK，合计 **10.3s** | — |

## 2. 变异验证（**475 条全 red-assert**，`full_audit_at = 7e7a62d`）

* 账本 `tests/mutation-verdicts.json`：`--ledger-status` = `475/475 可跳过（真跑过 475 + 继承 0）；待跑 0 条`；
  `--delta --list` = 待跑 0 / 跳过 475；`--preflight` = 487/487（475 变异 + 12 对照）。
  ⚠️ `--ledger-status` **不打印** `full_audit_at` —— 它在账本 `_meta` 里（现为 `7e7a62d`，
  逐段证据见 `_meta.full_audit_evidence`）；要看用
  `python3 -c "import json;print(json.load(open('tests/mutation-verdicts.json'))['_meta']['full_audit_at'])"`。
* 每条记录同时带**代码区域指纹**（锚点 ±15 行）与**测试侧指纹**（当年抓它的那批用例名，
  判定用「这些名字今天是否都还在」——删/改名会重跑，**加新用例不会**让 400+ 条作废）。
  ⇒ 不需要任何「继承」声明。
* 本轮实跑构成：增量 193 条（2 分片、目标门禁直跑）+ 升级补齐 278 条（把历史继承条目重跑一遍）
  + 定向复核若干（`--only`）。
* **收口后的全量刷新（2026-09-21 深夜）**：475 条全部在 `7e7a62d` 上有当日 `🔴` 证据 ——
  分片直跑 179 + 175，缺口 `--delta` 补跑 60 + 59（23:09 一次瞬时环境故障窗口的 120 条 💥，
  同一批快照重跑正常），定向复跑 `V1-2`（有界等待修复后）+ `CAND-B2`（新增）各 1；
  合并脚本按「🔴 名字并集必须覆盖全部条目 + 对照 ≥6 全绿 0 假红 + 三重指纹一致」才盖
  `full_audit_at`。详见 `audit-round1.md` 第八节；日志与工具在
  `~/.larkdeck-scratch/v0.7.2-full-20260921/`。
* 代价对比：旧口径全量 **60–90 分钟** ⇒ 本版 **~26 分钟**（分片直跑 ~18 分钟 + 缺口补跑 ~7 分钟）；
  下一版只跑区域/门禁变过的（秒级~分钟级）。
* 本轮由变异验证**揪出并修掉的 8 个真缺陷**（含收口复核新增的 `CAND-B2`）：
  见 `docs/audits/v0.7.2/audit-round1.md` 第六节与第八节。

## 3. 真机探针（只能由真机产生）

| 那一格 | 结论 | 证据 |
| --- | --- | --- |
| 加载指示共享 `img_key` 会不会动 | **① 会动**（用户目视） | 对照卡 `om_x100b643abd6394b0dfa26a200d65018`；记录 `loading-asset.md` |
| 工具行图标「偏上」 | 用户选**乙（emoji 内联）**，且「丙 没有换行」 | 三臂卡 `om_x100b6424d23e24a8c3368dfbdaad661`；`probe_icons.py` |
| 定版确认（emoji 选型 + 对齐） | **待用户回话** | `om_x100b64256c1470acdfadc4d33133fca`（`probe_icons_final.py`，生产渲染器输出） |
| 长回合（>20 工具步）不掉纯文本 / 无灰气泡 | **待用户复验** | 离线判据已绿：`test_v4_33_long_turn_card_never_puts_text_nodes_inside_collapsible_panels` |
| `show_reasoning=true` 的**嵌套** `collapsible_panel` 客户端渲染（仓库自己的 `plan-consensus.md:111` 列为未验证） | **待用户回话** | 生产渲染器输出已发：`om_x100b6427ec67d4a4de74424945f4ca0`（`probe_nested_panel.py`） |

## 4. 安装路径（`--copy` / NAS）

* 发布前实测抓到 `install.sh` 的 FILES **漏 `core/cardview.py`**，且对账门禁把 `tools/` 与
  `.deploy/` 也算运行文件 ⇒ 必然 exit 1。已修 + 加断言 + 变异 `INST-1`。
* 真跑过：`HERMES_HOME=/tmp/ldinstall bash install.sh --copy` → **11 个运行文件**全拷到（含
  `cardview.py` 20559 bytes）。

## 5. 对抗审计

| 轮次 | 方向 | 结论 |
| --- | --- | --- |
| 计划/执行 A | 代码正确性、计划前提 | 需修改 → 已收口（P2 前提被证伪、P5 根因改写、`terminal` 偏差） |
| 执行 B | 用户可见效果 | 需修改 → **阻断项**（fit 覆盖配置/空面板）已修 |
| 执行 C / C2 | 反假绿 | 需修改 → 7 条绿变异全部收口 + 门禁侧指纹补齐 |
| **发布前终审 A/B/C** | 代码正确性 / 用户可见 / 反假绿（在 `95af186` 冻结副本上） | 三路都报「需修改」⇒ **4 条真缺陷 + 4 条协议逃逸全部收口**（详见 `audit-round1.md` 第七节）：`panel_expanded` 被吞、页脚被面板绑死、降级安全网漏收尾帧、账本 helper 指纹缺失、锚点指纹取错位置（22/470 条）、`expect==""` 条目挡 full、`--seed-inherited` 洗白、超时文案。1 条**未采纳**（终态摘空面板，与状态色载体冲突）并记了分歧 |
| **收口复核**（A/B closures + C closures 且再找新绿） | 在 `90f6bb1`/`9a037ad` 上重跑 | **A/B 6/6、C 5/5 全部通过**；C 另找出 1 条真绿变异 `CAND-B2`（`_panel_has_data` 丢掉 `tools` ⇒ 纯工具回合整块吞掉面板）⇒ 已补断言 + 入账本；B 的 `panel_expanded` 死形参（`CAND-A`）直接删除；`CAND-B`（丢 `reasoning`）确认为等价变异移入 `CONTROLS`。复核自验还揪出一条**验证盲点**：`V1-2` 的整支 `test_units` 因用例里的**无界等待**挂到门禁超时（💥 记成「没有证据」）⇒ 新增 `_await_event` 有界等待，修后 4.7s 判红并记账。详见 `audit-round1.md` 第八节 |

## 6. 待用户确认（发布闸门）

1. 定版确认卡 `om_x100b64256c1470acdfadc4d33133fca` 的观感；
2. 长回合真机复验（不掉纯文本、无 `⏳ Working —` 灰气泡、折叠提示在）；
3. 页脚无 🔖（`状态 · ⏱ 时长 · 🤖 模型 · ctx`）；
4. 建卡瞬间加载指示「会动、无文字」。

四项确认后执行：`git push origin main --follow-tags` → `tag v0.7.2` → `gh release create` →
`.deploy` 指到 tag 提交 → 重启网关 → live 核对（命令见 `release-checklist.md`）。
