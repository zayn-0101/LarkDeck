# LarkDeck 文档风格规范

本规范适用于 README、INSTALL、`docs/guide/`、`docs/development/`、`docs/releases/` 和 CHANGELOG。
目标只有四个词：**简洁、清晰、易懂、有品味**。

## 1. 三层读者

| 层 | 目录 | 读者 | 内容 |
|---|---|---|---|
| 用户文档 | `README.md`、`INSTALL.md`、`docs/guide/` | 安装和使用 LarkDeck 的人 | 是什么、怎么装、怎么配、常见问题 |
| 开发文档 | `CONTRIBUTING.md`、`docs/development/`、`AGENTS.md` | 参与开发和发布的人 | 架构、测试、发布流程、代码规则 |
| 内部归档 | `docs/internal/`（本地） | 维护者 | 已交付的规划、审计过程、历史证据；**只在维护者本机保留，不随仓库发布** |

**规则：用户文档不出现内部审计编号、变异条数、handoff、轮次黑话、历史纠错记录。**
这些内容一律放本机 `docs/internal/`，对外文档只给一句话说明，不给链接（链接在远端必然失效）。

根目录保留 GitHub 与开发工具常用的 README、安装、贡献、更新日志和许可证入口；详细手册
按读者归入 `docs/guide/`、`docs/development/` 与 `docs/releases/`。`docs/README.md` 是完整
文档索引，不为追求路径整齐而搬动根目录入口。

## 2. 写作规则

- 一段一个意思，长句拆开。用户文档单节不超过 60 行。
- 结论先行：每篇开头一句说明「给谁看、解决什么」。
- 命令必须可直接复制；必要时在下一行写预期输出。
- 配置项必须给「默认值 + 作用 + 场景示例」；表格只用于对照，不塞长解释。
- 排障统一用「症状 → 根因 → 排查 → 修复 → 预防」。
- 变更日志一条一句话；证据和门禁数字放 release note；过程材料留在本机 `docs/internal/`，不进仓库。
- 不堆 emoji，不用营销口号，不写「全网首创」「史上最强」这类词。
- 术语首次出现给中英对照，之后统一用中文：澄清卡（clarify）、执行详情（panel）、第 N 轮思考（round）。

## 3. 格式约定

- 标题层级：# → ## → ###，不跳级；标题用名词或动宾短语。
- 代码块标语言：`bash`、`yaml`、`json`、`python`。
- 文件链接用相对路径；所有随仓库发布的文档都不链接 `docs/internal/`（它不在仓库里）。
- 截图必须写完整 alt；演示优先用真实状态截图，不用纯装饰图。
- 版本号以 `plugin.yaml` 为唯一来源；README 徽章、CHANGELOG 与发布说明同步引用该版本，不能各自决定版本。

## 4. 文档守卫

`tests/check_docs.py` 负责机械检查：

1. `plugin.yaml` 版本、README 徽章版本、CHANGELOG 顶部版本一致；
2. 随仓库发布的文档，相对链接全部可达；
3. 随仓库发布的文档不得链接 `docs/internal/`；用户文档连提都不提；
4. README 行数超限（默认上限 260 行）、SECURITY / 行为准则仍留占位邮箱时失败。

改文档后先跑 `tests/check_docs.py`，再跑完整门禁。
