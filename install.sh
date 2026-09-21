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
FILES=(plugin.yaml __init__.py
       core/__init__.py core/adapter.py core/cards.py core/cardview.py core/i18n.py
       core/compat.py core/context.py core/hooks.py core/panel.py)

# ── 门禁：FILES 是**手写**清单，漏同步会装出一个残缺插件（少了指标采集或钩子订阅），
# 而默认软链模式**永远测不出来** —— 只有 NAS / --copy 会中招。所以这里把清单与
# 仓库里真实的运行文件对一遍，不一致就直接拒绝运行。
# ⚠️ 排除三处**非运行文件**（2026-09-21 实测补）：
#   * `./tests/*`  —— 门禁/探针，不进插件；
#   * `./tools/*`  —— 开发用脚本（SLOC/冻结树），不是运行模块；
#   * `./.deploy/*` —— 部署 worktree（里面有整份源码副本）⇒ 不排除的话这个门禁
#     **只要部署目录存在就必红**（v0.7.2 发布前实测抓到的阻断项）。
_actual="$(cd "$REPO_DIR" && find . \( -name '*.py' -o -name 'plugin.yaml' \) \
           -not -path './tests/*' -not -path './tools/*' -not -path './.deploy/*' \
           -not -path '*/__pycache__/*' \
           | sed 's|^\./||' | sort)"
_declared="$(printf '%s\n' "${FILES[@]}" | sort)"
if [ "$_actual" != "$_declared" ]; then
  {
    echo "✗ install.sh 的 FILES 清单与仓库实际运行文件不一致："
    diff <(printf '%s\n' "$_declared") <(printf '%s\n' "$_actual") \
      | grep -E '^[<>]' \
      | sed 's/^< /  清单里有但仓库没有: /; s/^> /  仓库有但清单没列: /'
    echo "  新增/删除模块后必须同步 FILES（--copy 模式靠它）。"
  } >&2
  exit 1
fi

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
  # 比 inode（``-ef``），不比字符串也不比 realpath：macOS 默认大小写不敏感，软链里记的
  # 可能是 ~/code 而 pwd 给的是 ~/Code —— 同一个目录，字符串比较会误判成「指向别处」，
  # 而 ``pwd -P`` 保留输入时的大小写、也救不了。``-ef`` 比的是设备 + inode，两者通吃。
  if [ -e "$current" ] && [ "$REPO_DIR" -ef "$current" ]; then
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
    mkdir -p "$TARGET/$(dirname "$f")"   # core/ 子目录（相对路径）要先建出来
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
