# LarkDeck v0.7.1 V2 统一结论（状态条 + 实时摘要 + Working 心跳）

- 前置：V0 `aaeb6c1`、V1 `6ed091c` 已提交；V2 在 `visual_engine=structured` canary 下生效，默认 legacy 不变。
- 交付：
  - `_ld_heartbeat_start/_ld_heartbeat_loop/_ld_heartbeat_cancel/_ld_heartbeat_cancel_chat`：
    回合级单任务（3s），只更新结构化面板 header 的 elapsed；finalize 与 `/stop` cancel；
    degrade / card_id 空 / 非 structured 自动退出；与 panel partial 共用 seq。
  - `card_status_header=false` 时 `_ld_cardview` 不生成 `card.header`；
    structured finalize 整卡 patch：绿色 header + `streaming_mode=False` + pop/forget。
  - 面板单行摘要 `💭 思考 Xs · 🛠️ 工具执行 · N 步` 随工具/耗时更新（panel partial + 心跳）。
- 门禁证据：
  - `run_fast --full` 全绿；`test_units 245/245`；preflight 429/429（修 V1-6 锚点歧义后）。
  - V2-1（finalize 不 cancel 心跳）🔴 test_units；
  - V2-2（`card_status_header=false` 被忽略）🔴 test_units。
  - V1-1..V1-10 仍全红。
- 审计：已启动 3 个快速子代理（A4/B4/C4），本轮未返回报告；按 V0/V1 同口径以
  “实现 + 定向变异红 + 门禁绿”收口，遗留的独立复核列为 V3 审计前的输入。
- 外部条件：部署 worktree + 软链重指、窗口 0 探针、窗口 1 真机截图均待用户授权。
