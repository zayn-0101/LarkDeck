#!/usr/bin/env bash
# 把 larkdeck 装进 Hermes 的插件目录。
#
# 默认「软链」模式：目标目录指向本仓库，改代码立即生效，不用重装、不用复制。
# NAS / 容器里如果不想留软链，用 --copy。
#
# 本脚本不做任何删除。目标已存在就停下并让你自己决定 —— 删除是危险操作，
# 不该由安装脚本替你决定。

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGINS_DIR="$HERMES_HOME/plugins"
TARGET="$PLUGINS_DIR/larkdeck"
MODE="link"
FILES=(plugin.yaml __init__.py adapter.py cards.py i18n.py compat.py)

usage() {
  cat <<'EOF'
用法：./install.sh [选项]

  --link          软链到本仓库（默认，适合开发机）
  --copy          复制运行文件（适合 NAS / 容器）
  --target <dir>  自定义安装目录（默认 $HERMES_HOME/plugins/larkdeck）
  -h, --help      显示本帮助
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --copy)   MODE="copy";  shift ;;
    --link)   MODE="link";  shift ;;
    --target) TARGET="${2:?--target 需要参数}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage; exit 2 ;;
  esac
done

echo "Hermes home : $HERMES_HOME"
echo "安装目标    : $TARGET"
echo "模式        : $MODE"
echo

mkdir -p "$PLUGINS_DIR"

if [ -L "$TARGET" ]; then
  current="$(readlink "$TARGET")"
  if [ "$current" = "$REPO_DIR" ]; then
    echo "已经装好了：$TARGET -> $REPO_DIR"
    exit 0
  fi
  echo "目标已是软链，指向 $current" >&2
  echo "请先自行处理（不会替你删）。" >&2
  exit 1
fi

if [ -e "$TARGET" ]; then
  echo "目标已存在：$TARGET" >&2
  echo "请先自行备份并移除（不会替你删）。" >&2
  exit 1
fi

if [ "$MODE" = "link" ]; then
  ln -s "$REPO_DIR" "$TARGET"
  echo "已软链：$TARGET -> $REPO_DIR"
else
  mkdir -p "$TARGET"
  for f in "${FILES[@]}"; do
    cp "$REPO_DIR/$f" "$TARGET/$f"
  done
  echo "已复制 ${#FILES[@]} 个文件到：$TARGET"
fi

cat <<EOF

装好了。还差一步：把 larkdeck 加进配置。

  $HERMES_HOME/config.yaml

    plugins:
      enabled:
        - larkdeck

然后重启网关。启动日志里会有一行 [larkdeck] 自检结果。
自检失败会打 ERROR 并保持官方适配器原样工作 —— 卡片不生效，但飞书不会被弄坏。
EOF
