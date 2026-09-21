# LarkDeck v0.7.1 V1 统一结论（结构化 canary）

- 计划 §9（plan v2.2 SHA `cee0715…`）；V0 已提交 `aaeb6c1`。
- V1 audit tree：`91b104cc1e3c420b524716e22da1c029dac13137`
  （当前 staged index 减 `freeze-V1.json`；含 `core/cardview.py`、`_ld_stream_frame_structured`、
  V1-* 锚点、check_cardview structured golden）。
- 导出：`/private/tmp/audit-v1-91b104cc1e3c420b524716e22da1c029dac13137/larkdeck`。
- manifest：`docs/audits/v0.7.1-visual/freeze-V1.json`（tree/audit_target_tree/export_dir=91b104c，
  plan SHA、gate_logs、pairing note）。

## 三方审计
- A（代码/架构）：GO-WITH-CONDITIONS — partial_element 顶层无 tag/text_size、answer 最后写、
  seq 共用严格 +1、/stop/finalize/卡链 seed 结构化、默认 legacy 分派未变。
- B（用户可见）：GO-WITH-CONDITIONS — 工具行/推理面板/间距 token 与 CLS 一致（padding 8px 已补）；
  默认 legacy 逐字节不变（golden 夹具）；/stop 黄边/短码/正文保留。
- C（反假绿）：NO-GO（修复前）→ 条件：check_cardview 双 main、顺序/跨帧 seq 假绿、
  V1 manifest 配对。已全部修复并实测红。

## 条件闭环
- check_cardview 双 main 删除，`_assert_structured_builder` 真跑；V1-4/5/7 红。
- 测试补 seed 顺序/footer、answer 在 panel partial 之后、跨帧 seq 唯一严格递增；
  V1-6（seq 重置）红。
- DEGRADE 同卡 legacy patch + engine_stamp；V1-10 红 + fault-injection 测试。
- finalize 同卡整卡 patch（绿色 header + streaming_mode=False）+ pop/forget；V1-8/9 红。
- `card_status_header=false` 接线；padding 8px；外层 title grey；footer 元素/逐帧更新；
  切卡重置 ck_dead/ck_decor；plan_ops 死代码删除。
- V1-1..V1-10 全部实测红；`run_fast --full` 全绿；test_units 245/245；preflight 427/427。

## 边界与外部条件
- V1 仅 `visual_engine=structured` 显式 canary；默认 legacy 不变，不得据此重启网关。
- 真机视觉（嵌套面板渲染、header partial、standard_icon、normal_v2）未验；窗口 0/1 前需用户授权。
- 部署 worktree 未建；live 软链仍指开发树。
