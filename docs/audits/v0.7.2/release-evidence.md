# v0.7.2 发布证据（一览）

> 提交：**本支最新提交**（收口：图标字段白名单 + 页脚模型显示名；全量账本冻结参考 `f97ce19`）
> · 部署：`.deploy` 指向该提交同源代码，网关已重启（`启动自检通过`）
> · 用户终验：**进行中**（见 §3 真机探针表；长回合「无灰气泡」已由用户确认）

## 1. 门禁（六支，全绿）

| 门禁 | 结果 | 耗时 |
| --- | --- | --- |
| `tests/test_units.py` | **283/283 passed** | ~4s |
| `tests/check_override.py` | `OVERRIDE OK`（真插件加载器，报 `larkdeck v0.7.2`） | ~1.8s |
| `tests/check_hooks.py` | `HOOKS OK` | ~3.7s |
| `tests/check_clarify_e2e.py` | `CLARIFY E2E OK`（含**真 SDK payload 类**的表单提交场景） | ~1.7s |
| `tests/check_cardview.py` | `CARDVIEW OK`（含独立字面量：图标表/真实工具名/spinner 三字段） | ~0.05s |
| `tests/check_cls_alignment.py --require` | `CLS ALIGN OK`（28 条逐条 + 顺序；缺席即 FAIL） | ~0.06s |
| `tests/run_fast.py --full` | 各步全 OK（六支 + `check_own_body` + 锚点预检 494/494），合计 **11.5s** | — |

## 2. 变异验证（**484 条全 red-assert**，`full_audit_at = f97ce19`）

* 账本 `tests/mutation-verdicts.json`：`--ledger-status` = `484/484 可跳过（真跑过 484 + 继承 0）；待跑 0 条`；
  `--delta --list` = 待跑 0 / 跳过 484；`--preflight` = 496/496（484 变异 + 12 对照）。
  ⚠️ `--ledger-status` **不打印** `full_audit_at` —— 它在账本 `_meta` 里（现为 `f97ce19`，
  逐段证据见 `_meta.full_audit_evidence`）；要看用
  `python3 -c "import json;print(json.load(open('tests/mutation-verdicts.json'))['_meta']['full_audit_at'])"`。
* 每条记录同时带**代码区域指纹**（锚点 ±15 行）与**测试侧指纹**（当年抓它的那批用例名，
  判定用「这些名字今天是否都还在」——删/改名会重跑，**加新用例不会**让 400+ 条作废）。
  ⇒ 不需要任何「继承」声明。
* 本轮实跑构成（**2026-09-22 晚，页脚模型显示名**）：**484 条全量直跑**（4 分片 121×4，
  **0 💥 / 0 🟢 / 0 ❓**，12 名对照全绿）+ 合并器现场重算三指纹 ⇒ `at` 全部 `f97ce19`。
  * **为什么又全量跑**：`core/context.py::display_model()` 是生产行为变更，而它会被
    `golden_cardkit_trace.json` 记录下来（`🤖 test-model` ⇒ `🤖 Test Model`）⇒ helper 指纹变化，
    按协议所有历史判定作废、必须重跑全量。新增变异 `V4-62`（退回 ID）/`V4-63`（别名不再优先）。
  * 代价：4 分片并行 **≈14 分钟**（16:25 → 16:39），无缺口补跑。
* 上一轮实跑构成（**2026-09-22 下午，P3.1 图标定版落地后的缺陷修复**）：**482 条全量直跑**（4 分片
  119+119+118+118；其中 8 条因机器负载 27 撞 45s 门禁超时被判 💥 ⇒ 定向 `-k` 复跑全部转
  red-assert，写 `seed5.json`）+ 合并器现场重算三指纹。
  * 起因：`core/cardview.py` 的 `markdown` 非法字段 `text_color` → 进 content 的
    `<font color='grey'>`；`div` 前缀图标挪到组件级；`tests/check_cardview.py` 新增字段白名单门禁
    + golden 夹具变化 ⇒ 全量重跑。
  * ⚠️ 起分片必须 `start_new_session=True`：14:31 那次用 `nohup … &` 起的进程在常驻 shell 被重置时
    一起被杀（跑到 ~98/121，无账本写入 ⇒ 已归档 `dead-1431/` 重跑）。
* **再上一轮（2026-09-21 深夜，历史）**：473 条在 `7e7a62d` 上有当日 `🔴` 证据 ——
  分片直跑 179 + 175，缺口 `--delta` 补跑 60 + 59（23:09 瞬时故障窗口：分片 2 的 **62 条 💥** +
  分片 1 的 **58 条没跑到** = 缺口 120，用公共种子 354 条补跑；同一批快照重跑正常），
  另两条 `CAND-B2`/`V1-2` 在干净树 `396f0ae` 上定向复跑（`at=396f0ae`，日志
  `rerun-*-396f0ae.log`）；
  合并/校验按「🔴 名字并集必须覆盖全部条目 + 对照 **12 个名字**全绿 0 失败 + **现场重算**
  三指纹一致」才盖
  `full_audit_at`。详见 `audit-round1.md` 第八节；日志与工具在
  `~/.larkdeck-scratch/v0.7.2-full-20260921/`。
* 代价对比：旧口径全量 **60–90 分钟** ⇒ 2026-09-21 版 **~26 分钟**；2026-09-22 下午版 **~20 分钟**
  （分片 ~15 + 💥 复跑 ~3）；2026-09-22 晚版 **~14 分钟**（4 分片、零缺口）。下一版只跑
  区域/门禁变过的（秒级~分钟级），`full_audit_at` 仍按周期刷新。
* 本轮由变异验证**揪出并修掉的 8 个真缺陷**（含收口复核新增的 `CAND-B2`）：
  见 `docs/audits/v0.7.2/audit-round1.md` 第六节与第八节。

## 3. 真机探针（只能由真机产生）

| 那一格 | 结论 | 证据 |
| --- | --- | --- |
| 加载指示共享 `img_key` 会不会动 | **① 会动**（用户目视） | 对照卡 `om_x100b643abd6394b0dfa26a200d65018`；记录 `loading-asset.md` |
| 工具行图标「偏上」 | 用户选**乙（emoji 内联）**，且「丙 没有换行」 | 三臂卡 `om_x100b6424d23e24a8c3368dfbdaad661`；`probe_icons.py` |
| 定版确认（emoji 选型 + 对齐） | **第一轮反馈：标题 🛠️ 与 terminal 行撞符号、整体不够好** ⇒ 已改成「区段符号不复用 + 同 token 按名字精化」（见 `releases/v0.7.2.md` §4.1、`audit-round1.md` §8.5）；第二轮用户口径改为**统一灰色线性图标**（CLS 观感）+「应用到更多场景」⇒ P3.1 落地（`markdown` 前缀图标 + 详情行/错误块/折叠提示）；**用户 2026-09-22：「图标我看了下，基本都可以」**（唯一追问是面板标题文案与页脚的对比，已用四家源码对照回答；面板标题经用户拍板**保持现状**） | 修复前 `om_x100b64256c1470acdfadc4d33133fca` / 重发 `om_x100b642891ea30b0c4ed8f26a631531`；**线性定版卡** `om_x100b6414825f3ca8c339ed0a7cef3e9`（三臂）；**落地确认卡** `om_x100b64159f75b0a0c2f35ecdf3f0d36`；**长回合 25 步卡** `om_x100b641635da808cc11b25152858806` |
| 长回合（>20 工具步）不掉纯文本 / 无灰气泡 | **用户 2026-09-22 确认：「长回合的话，没有出现纯文本『⏳ Working —』气泡了」** ✅（此前探针已把该路径上的非法字段全部打掉：`markdown.text_color` / `div.text.icon` ⇒ 真机 `200621` 整卡被拒，见 `audit-round1.md` §8.7） | 离线判据：`test_v4_33` + `V4-60`/`V4-61` 变异实红 + 面板字段白名单门禁；真机：用户实测 |
| 页脚：不再有 🔖 短码 | **用户 2026-09-22：「不再有 🔖了，这个是正常的吗？」→ 是（你 9-21 的口径）**；用户自己的回合里页脚可见 ✅ | `test_v4_17b` 扫整卡与出站载荷 |
| 页脚：`🤖` 后面是**模型名**不是 ID | **待用户目视**（用户口径：「显示现在的好像是模型 ID，我想要做成显示模型名」） | 探针卡 `om_x100b6417897c010cc4385fad759428a`（`probe_footer_model.py`，含 `ID → 显示名` 对照表 + 生产 `footer_line()` 逐字输出） |
| `show_reasoning=true` 的**嵌套** `collapsible_panel` 客户端渲染（仓库自己的 `plan-consensus.md:111` 列为未验证） | **待用户回话**（第一张卡用户「记不清了」⇒ 已重发） | 生产渲染器输出：首张 `om_x100b6427ec67d4a4de74424945f4ca0`；**重发** `om_x100b641787b9bca0c39cc70479d9390`（`probe_nested_panel.py`） |

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
