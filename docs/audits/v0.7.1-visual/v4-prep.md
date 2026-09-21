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

## 用户闸门后的执行命令清单
```bash
PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
cd /Users/Zayn/Code/larkdeck

# 0) 部署 worktree + 软链（需目录写权限；默认只演练）
tools/setup_deploy_worktree.sh --check
tools/setup_deploy_worktree.sh --create
tools/setup_deploy_worktree.sh --apply     # 之后才允许重启网关

# 1) 窗口 0：字号探针（不重启）
$PY tests/probe_render.py --font-size
# 你确认后：把 visual-tokens.json 的 body_text_size_pending_probe 写回选定值

# 2) 窗口 1：V1+V2（一次重启 + structured/header 开）
#    配置 visual_engine=structured；重启网关；跑工具回合并截图：
#    ① 流式工具行 ② 同卡收尾 ③ /stop 黄边
# 3) 窗口 2：V3（一次重启 + show_reasoning=true）
#    ④ 展开嵌套轮 + Result/Error ⑤ false 同回合只留摘要
# 4) 窗口 3：V4 最终（默认切 structured + 删 legacy 后一次重启）
#    ⑥ ≥20 步长回合 ⑦ 最终桌面截图 + /larkdeck status
#    注意：默认切换代码尚未执行，需用户最终截图确认后由父代理改默认并 commit

# 5) 发布里程碑（仅在发布三审 + 用户最终截图 + 全量门禁后）
# git checkout main && git merge --ff-only v0.7.1-visual
# git push origin main
# git tag -a v0.7.1 -m "tree <sha> expected-kill <log sha>"
# git push origin v0.7.1
```

## 必须由用户决定/执行
- 部署 worktree 创建与软链重指（沙箱不允许父代理自动创建目录）；
- 窗口 0 探针卡与 `normal_v2` token 选择；
- 网关重启/回滚时机（当前 detached PID 95960）；
- 默认 `visual_engine=legacy → structured` 的最终切换；
- 发布里程碑 push / annotated tag / release（仅在用户最终截图后）。

