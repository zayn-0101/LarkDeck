# LarkDeck v0.7.1 V4 发布准备（等待用户授权）

## 已完成的自动化部分
- 结构化面板预算：工具步/推理轮各保留最近 20，超出加「…更早的 N 步/轮已折叠」；
  标题步数按**真实总数**显示，不再少报；near-limit fixture（25 步）通过，
  `entity_skeleton` 递归元素数 ≤180。
- V4-1 变异（trim 关闭）实测红；`run_fast --full` 全绿，test_units 247/247，
  preflight 433/433。
- V0–V3 全部本地提交（见 git log）；默认仍 `visual_engine=legacy`，未重启网关。

## Release notes 草稿（v0.7.1）
- 新增 `visual_engine`（默认 legacy；structured 为结构化元素树 canary）
- 新增 `card_status_header`（状态条：处理中蓝 / 完成绿 / 停止黄 / 出错红；false 可隐藏）
- 新增 `show_reasoning`（默认 false；false 时只保留 `💭 思考 Xs` 摘要）
- structured 下：工具行 `div+standard_icon`、22px 细节缩进、推理 A 形态嵌套折叠面板、
  面板单行摘要、Working 心跳、Result/Error 块（截断 600 + 脱敏）
- 默认 legacy 输出逐字节不变；structured 需显式开启，未授权前不切换默认

## 最终用户验收清单（需用户执行/确认）
1. 窗口 0：`normal` vs `normal_v2` 探针卡 1 张（独立进程，不重启），确认后写回 token。
2. 窗口 1（V1+V2，一次重启 + structured/header 开）：
   ① 流式工具行（小图标/22px/状态词）② 同卡收尾结构不变 ③ `/stop` 黄边+结构不退化。
3. 窗口 2（V3，一次重启 + show_reasoning=true）：
   ④ 展开嵌套轮 + Result/Error ⑤ false 同回合只留摘要；P3 嵌套探针眼睛确认并入。
4. 窗口 3（V4，默认切 structured + 删 legacy 后一次重启）：
   ⑥ ≥20 步长回合 ⑦ 最终发布确认 + `/larkdeck status`。
5. 每窗口前置：定向变异红 + 改动面分片绿；失败同窗口修复重看；看完恢复默认配置。

## 必须由用户决定/执行
- 独立部署 worktree + `~/.hermes/plugins/larkdeck` 软链重指（沙箱不允许自动创建目录）；
- 窗口 0 探针卡与 `normal_v2` token 选择；
- 网关重启/回滚时机（当前 detached PID 95960）；
- 默认 `visual_engine=legacy → structured` 的最终切换；
- 发布里程碑 push / annotated tag / release（按计划仅在发布三审 + 用户截图后执行）。
