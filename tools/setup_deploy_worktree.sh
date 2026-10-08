#!/usr/bin/env bash
# LarkDeck v0.7.1 部署 worktree 准备/切换脚本（默认只演练，不改软链、不重启网关）。
#
# 背景：~/.hermes/plugins/larkdeck 当前指向开发树；任何网关重启都会加载
# v0.7.1-visual 未完成代码。本脚本把 live 指向一个独立、已验证的部署 worktree。
#
# 用法：
#   tools/setup_deploy_worktree.sh --check                 # 只检查并打印计划（默认）
#   tools/setup_deploy_worktree.sh --create                # 创建 worktree（不动软链）
#   tools/setup_deploy_worktree.sh --apply                 # 创建 + 重指软链（需你确认）
#   tools/setup_deploy_worktree.sh --rollback              # 恢复上次软链备份（备份在 $HERMES_HOME/plugin-backups/）
#
# 默认部署路径：${HOME}/.hermes/deploy/larkdeck，release ref：origin/main
set -euo pipefail

DEV_REPO="${LARKDECK_DEV_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"
DEPLOY_DIR="${LARKDECK_DEPLOY_DIR:-${HOME}/.hermes/deploy/larkdeck}"
RELEASE_REF="${LARKDECK_RELEASE_REF:-origin/main}"
PLUGIN_LINK="${HERMES_HOME:-${HOME}/.hermes}/plugins/larkdeck"
# 备份**必须放在 plugins/ 之外**：Hermes 扫 plugins/*/ 时按插件清单里的 name 记账，同名目录
# 里目录名排序靠后的会**遮蔽**靠前的 —— 旧备份改名留在 plugins/ 里，新部署的软链就永远不生效，
# 而且没有任何自动告警（2026-10-08 真机事故，这个脚本正是成因之一：它以前造 <link>.bak.<ts>）。
BACKUP_ROOT="${HERMES_HOME:-${HOME}/.hermes}/plugin-backups"
BACKUP_LINK="${BACKUP_ROOT}/larkdeck-link.$(date +%Y%m%d%H%M%S)"
LEGACY_BACKUP_PREFIX="${PLUGIN_LINK}.bak."
MODE="${1:---check}"

log() { printf '[deploy] %s\n' "$*"; }
die() { printf '[deploy][ERROR] %s\n' "$*" >&2; exit 2; }

[ -d "$DEV_REPO/.git" ] || die "开发仓库不存在：$DEV_REPO"
cd "$DEV_REPO"

current_target="$(readlink "$PLUGIN_LINK" 2>/dev/null || true)"
log "当前软链：$PLUGIN_LINK -> ${current_target:-<不存在>}"
log "开发分支：$(git branch --show-current) HEAD=$(git rev-parse --short HEAD)"
log "部署目录：$DEPLOY_DIR"
log "release ref：$RELEASE_REF ($(git rev-parse --short "$RELEASE_REF" 2>/dev/null || echo '未知'))"

case "$MODE" in
  --check)
    if [ -d "$DEPLOY_DIR/.git" ] || [ -f "$DEPLOY_DIR/plugin.yaml" ]; then
      log "部署 worktree 已存在：$DEPLOY_DIR"
    else
      log "部署 worktree 尚不存在；运行 --create 创建（父目录需有写权限）。"
    fi
    log "演练结束：未创建目录、未改软链、未重启网关。"
    ;;
  --create)
    if [ -e "$DEPLOY_DIR" ]; then
      die "目标已存在，拒绝覆盖：$DEPLOY_DIR"
    fi
    mkdir -p "$(dirname "$DEPLOY_DIR")"
    git worktree add "$DEPLOY_DIR" "$RELEASE_REF"
    log "已创建部署 worktree：$DEPLOY_DIR"
    log "下一步（需你确认）：$0 --apply"
    ;;
  --apply)
    if [ ! -f "$DEPLOY_DIR/plugin.yaml" ]; then
      die "部署 worktree 不存在，先运行 --create：$DEPLOY_DIR"
    fi
    if [ -L "$PLUGIN_LINK" ] || [ -e "$PLUGIN_LINK" ]; then
      mkdir -p "$BACKUP_ROOT"
      cp -a "$PLUGIN_LINK" "$BACKUP_LINK" 2>/dev/null || true
      log "已备份原软链/路径到：$BACKUP_LINK（在 plugins/ 之外）"
    fi
    # 旧版脚本留在 plugins/ 里的备份是**活的遮蔽源**：它们与 larkdeck 同名，排序靠后的会赢。
    for stale in "${LEGACY_BACKUP_PREFIX}"*; do
      if [ -e "$stale" ]; then
        log "警告：plugins/ 里残留同名备份 $stale —— 它会遮蔽新部署！请搬到 $BACKUP_ROOT/ 后重启网关。"
      fi
    done
    ln -sfn "$DEPLOY_DIR" "$PLUGIN_LINK"
    log "软链已重指：$PLUGIN_LINK -> $DEPLOY_DIR"
    log "部署树版本：$(git -C "$DEPLOY_DIR" rev-parse --short HEAD)"
    log "现在才允许按计划重启网关；重启后发一张探针卡 + /larkdeck status 自检。"
    log "回滚：$0 --rollback 或 ln -sfn '$BACKUP_LINK' '$PLUGIN_LINK'"
    ;;
  --rollback)
    backup="$(ls -1t "${BACKUP_ROOT}/larkdeck-link."* 2>/dev/null | head -1 || true)"
    if [ -z "$backup" ]; then
      backup="$(ls -1t "${LEGACY_BACKUP_PREFIX}"* 2>/dev/null | head -1 || true)"
      [ -n "$backup" ] && log "警告：回滚用的是旧版脚本留在 plugins/ 里的备份 $backup —— 回滚后请把它移出 plugins/（同名目录会遮蔽插件）。"
    fi
    [ -n "$backup" ] || die "找不到软链备份：${BACKUP_ROOT}/larkdeck-link.*"
    ln -sfn "$(readlink "$backup")" "$PLUGIN_LINK"
    log "已回滚：$PLUGIN_LINK -> $(readlink "$PLUGIN_LINK")（来自 $backup）"
    log "按计划重启网关生效。"
    ;;
  *)
    die "未知参数：$MODE（支持 --check/--create/--apply/--rollback）"
    ;;
esac
