# 测试与门禁

> 读者：改动 LarkDeck 后需要给出「真的验证过」证据的人（人类开发者或 AI coding agent）。
> 结论先行：本仓库没有跑完整插件门禁的 CI（它依赖真实 Hermes、CLS 源码与维护者本机归档），也没有 lint / formatter；公开 CI 只跑 `tests/check_docs.py` 这一道零依赖的文档守卫（`.github/workflows/check-docs.yml`）。**插件改动仍以本节的脚本为准**。

## 运行环境

一律用 Hermes 自带的解释器：

```bash
PY="${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python3"
$PY tests/run_fast.py            # 日常快速检查
$PY tests/run_fast.py --full     # 9 步门禁
```

为什么不用系统 `python3`：系统解释器缺少 `lark_oapi`，CardKit 相关用例会得到与真实原因
无关的**假红** —— 它们的前提断言「拿不到 SDK 就 fail-open（失败即回落，不阻塞消息）」
被顺带满足。

为什么不用 `pytest` 入口：这些脚本必须用各自的 `__main__` runner 跑。
`pytest -q tests/test_units.py` 的 logging / 全局状态会让一批 CardKit 与账本用例出现
既有失败，同一份代码换回 `python3 tests/...` 就正常。看到 pytest 失败先换入口，
不要当成代码回归。每个脚本自己测试的是哪份代码也有自证：测试会拒绝加载仓库之外的同名模块。

## 快速入口

| 命令 | 用途 |
|---|---|
| `python3 tests/run_fast.py` | 并行跑最快的 4 步，目标 30 秒内；任一步非 0 则整体非 0。 |
| `python3 tests/run_fast.py --full` | 再串行补上 5 步；输出必须**恰好 9 个 `[OK]`**。 |
| `python3 tests/run_fast.py --serial` | 默认 4 步也串行，排查调度问题时用。 |

`run_fast.py` 只做进程调度和计时，不修改源码。输出 `[FAIL]` 即失败；发布脚本还会核对
门禁步骤名集合必须恰好是已知的 9 个，少跑一步或在输出里漏了步骤同样失败。

## 九个门禁分别验什么

| # | 命令 | 验什么 | 通过标志 |
|---|---|---|---|
| 1 | `$PY tests/check_docs.py` | 文档一致性：`plugin.yaml` / README 徽章 / CHANGELOG 顶部版本一致，相对链接可达，用户文档不引用内部归档，README 不超长。 | `DOCS OK` |
| 2 | `$PY tests/test_units.py` | 零网络、零 Hermes 依赖的纯单测：类切换与 MRO、卡片 JSON 与双语字段、四条主路径的失败回落、指标边界、面板数据层。 | `N/M passed` |
| 3 | `$PY tests/check_own_body.py` | 用真实核心 `_compose_frame_content` 造帧：own 模式非收尾正文只来自插件累积；收尾整段选一，绝不按分隔符切片。 | `OWN BODY GATE OK` |
| 4 | `$PY tests/mutate_check.py --preflight` | 变异清单的锚点对账：每条原文在目标文件里恰好出现一次。0.1 秒级，**不是**变异验证本体。 | `全部锚点存在且唯一` |
| 5 | `$PY tests/check_override.py` | 真 Hermes 插件加载器：临时 `HERMES_HOME` 里注册 larkdeck，确认 `feishu` 解析到 LarkDeck 工厂；同时验证配置桥接与 entry 字段透传。 | `OVERRIDE OK` |
| 6 | `$PY tests/check_hooks.py` | 真钩子派发器：八个观察钩子都被登记；真实载荷能流进面板 / 页脚数据层；`pre_tool_call` 返回值全是 `None`；不启用插件时对照组为空。 | `HOOKS OK` |
| 7 | `$PY tests/check_clarify_e2e.py` | 从平台注册表取**真实内置适配器类**做澄清端到端：发送 → 点击 → 真 waiter 解除阻塞；1.0 / 2.0 方言各自成立、回填同方言。全程不连飞书。 | `CLARIFY E2E OK` |
| 8 | `$PY tests/check_cardview.py` | 视觉 golden：token / 状态色 / 图标 / 工具名表的字面量断言，实体卡元素顺序与 content 逐字 round-trip，结构化元素树 golden。 | `CARDVIEW OK` |
| 9 | `$PY tests/check_cls_alignment.py --require` | 交叉门禁：直接解析 CLS 源码里的 `_TOOL_DESCRIPTORS`，和生产图标别名表逐条、按顺序比对；本地扩展必须确实不在 CLS 表内。CLS 仓库缺席时 `--require` 直接 FAIL。 | `CLS ALIGN OK` |

第 1–3 步和变异预检可以随手跑；第 5–7 步需要真实 Hermes 安装，第 8、9 步依赖冻结契约
或外部 CLS 源码。日常用 `run_fast.py --full` 把它们串起来。任何一步都要求「必须打印自己的
通过标志」，而不只是退出码为 0。

## 变异验证

`tests/mutate_check.py` 是本仓库「先写变异，再写断言」的落点。清单里每一条变异都是
一处「撤掉某条修复」的定向改动；判定标准是**至少一个门禁真的断言失败**。

- **red-assert**：门禁确实跑起来（打印了自己的收尾语），输出里出现该门禁的失败标记
  （如 `AssertionError` / `FAIL`），且退出码非 0。只有这样才算「这条断言有判别力」，
  才会写进验证账本。
- **red-crash**：进程崩溃、语法错误、导入失败或超时。它没有跑到断言，**不算证据**。
- **green**：撤掉修复后所有门禁还是绿的 ⇒ 这条断言没有判别力，必须补强。
- **锚点问题**：原文找不到、出现多次、或落在用例不会经过的分支上 ⇒ 一律算失败；
  「跑不到」绝不允许当成「通过」。

```bash
$PY tests/mutate_check.py --preflight      # 只对账锚点，快
$PY tests/mutate_check.py -k <子串>        # 只跑一部分，排查用
$PY tests/mutate_check.py --delta          # 改了断言后：只跑区域变过的 + 新增的
$PY tests/mutate_check.py                  # 全量；合并、变基、大改后必须跑
$PY tests/mutate_check.py --ledger-status  # 看覆盖率与待跑条目
```

注意：`--preflight` 通过只说明锚点还在，**不代表树跑得起来、门禁是绿的**。
它有意不受 `-k` 影响，是前置筛子，不是发布绿灯。

## 文档守卫

`tests/check_docs.py`（`run_fast` 第 1 步）机械检查：`plugin.yaml` 版本、README 徽章版本、
CHANGELOG 顶部版本一致；随仓库发布的文档相对链接可达；这些文档不得链接 `docs/internal/`
（内部归档只在维护者本机，不随仓库发布）；README 超长或仍留占位邮箱时报错。
改任何文档后先单独跑它，再跑完整门禁。

## 本地归档与冻结契约

- `docs/internal/`（规划、审计、handoff、横向调研、验证日志）**只在维护者本机保留**，
  已加入 `.gitignore`。`test_units.py` 里的计划进度表用例、`mutate_check.py` 里针对
  `plan-v1.md` 的变异都依赖它：公开 clone 上这些条目会**打印 `⏭️` 跳过并计数**，
  不会静默当通过；维护者本机行为与以前一致。
- 门禁要用的冻结数据（`visual-tokens.json`、`tool-icons.json`、`footer-contract.json`）
  是发布契约不是过程材料，放在 `tests/fixtures/`，随仓库发布。

## 真机探针与本地门禁的分工

| | 本地门禁 | 真机探针 |
|---|---|---|
| 范围 | 结构、契约、回落逻辑、钩子接线 | 客户端渲染、点击到达、飞书返回码、限流 |
| 网络 | `test_units.py` 零依赖；`check_override` / `check_hooks` / `check_clarify_e2e` 用真 Hermes 但不连飞书；其余本地 | 从 `~/.hermes/.env` 读凭据，把卡发到自己的 DM |
| 判定 | 断言与退出码 | 返回码；少数只能靠肉眼（打字机动画、真实渲染） |
| 性质 | 每次改动都要跑 | 手工按需跑，不是门禁，不放进自动流程 |

常用探针：

- `tests/probe_render.py`：综合渲染探针，从 `~/.hermes/.env` 读凭据把探针卡发到自己的 DM。
  改 `cards.py` 或任何卡片结构后必须跑。
  `--typing` / `--bytes` / `--elements` / `--rate-limit` / `--stop-redraw` / `--button-2`
  分别回答动画、字节上限、元素上限、连续 patch 限流、`/stop` 重绘与 2.0 按钮点击。
- `tests/probe_ck_stream_ops.py`：回答 CardKit **流式进行中**做各类写入会不会关会话，
  `--capacity-codes` 验证容量错误的包装码与内层码。判据全部是返回码，不需要肉眼。
- `tests/probe_clarify_click.py`、`tests/probe_concurrent.py` 等：具体交互或并发形状的验证。

两条纪律：探针消息的 `uuid` 每次都不同；清理**只按 `message_id`**（探针自己的账本或
`card_id` 精确匹配），绝不按「最近 N 分钟」盲删。
