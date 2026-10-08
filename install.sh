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
BACKUP_DIR="$HERMES_HOME/plugin-backups"
ALLOW_SHADOW=0
FIX_SHADOW=0
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
  --fix-shadow    把「会遮蔽本次安装的同名目录」搬到 $HERMES_HOME/plugin-backups/（只 mv，不删）
  --allow-shadow  已知情并接受同名目录遮蔽风险，仍继续安装（会打印警告与验证命令）
  -h, --help      显示本帮助

同名目录遮蔽：Hermes 扫描 $HERMES_HOME/plugins/ 下的插件目录、按清单里的 name 记账，
后扫到的同名目录覆盖先扫到的（目录名字典序更大的赢）。把旧版本改名留在 plugins/ 里，
新版本就永远不会被加载，而且没有任何提示。
EOF
}

_ORIG_ARGS="$*"        # 出路命令要照抄用户原来的参数（解析后 $* 已被 shift 空）
while [ $# -gt 0 ]; do
  case "$1" in
    --copy)   MODE="copy";  shift ;;
    --link)   MODE="link";  shift ;;
    --target) TARGET="${2:?--target 需要参数}"; shift 2 ;;
    --fix-shadow)   FIX_SHADOW=1; shift ;;
    --allow-shadow) ALLOW_SHADOW=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; usage; exit 2 ;;
  esac
done

echo "Hermes home : $HERMES_HOME"
echo "安装目标    : $TARGET"
echo "模式        : $MODE"
echo

mkdir -p "$PLUGINS_DIR"

# ── 同名目录遮蔽门禁 ────────────────────────────────────────────────────────
# 为什么必须在这里拦：Hermes 用 sorted(iterdir()) 扫 plugins/*/plugin.yaml，注册表按清单里的
# **name** 记账 ⇒ 后扫到的同名目录**覆盖**先扫到的。用户按旧文档把旧版本改名成
# `larkdeck.bak-20261008` 留在 plugins/ 里 ⇒ 加载的还是旧版本，升级静默失效
# （2026-10-08 真机事故：磁盘是 0.7.16、实际跑 0.7.15、日志零提示）。
# 纪律：不删任何东西；搬要显式 --fix-shadow；认不出来的目录一律**不动**并如实报告。
#
# ⚠️ 扫描结果**装进数组**，不做 TSV/字符串序列化：目录名里可能有制表符、换行、全角括号
# （`larkdeck（v2`）—— 任何分隔符都能被目录名伪造，也会把反解目录名弄错（P2-3 审计实测：
# 带制表符的 WIN 行会被 awk 丢掉 ⇒ 静默装上；带 `（v` 的目录会被 sed 截断成 larkdeck ⇒ mv 失败）。
# 读清单字段：真 YAML / json（与运行时 adapter 侧**同源**）。退出码是语义，不是随手值：
#   0 = 读到了（输出为空 ⇒ 这个键确实不存在 ⇒ 调用方按 Hermes 语义回退目录名）
#   2 = **无法判定**（文件读不到 / 没有 yaml 库 / 没有 python3）⇒ 调用方归 UNKNOWN（fail-closed）
#   3 = 不是插件（解析失败 / 顶层不是映射 / json 不是合法 portable 清单）⇒ 调用方跳过
# 为什么不能用 sed 猜（P2-1 审计实测 8 类静默漏判）：锚点 `&a`、别名 `*n`、重复 name、
# 块标量 `|-`/`>-`、标签 `!!str`、BOM、单引号键、整份缩进 —— 都会让 sed 读不出/读错，
# 而读不出时若回退目录名，就会被当成「不是本插件」**静默放行**（Hermes 其实加载了它）。
# 找一个**带 PyYAML** 的 python3（真 YAML 解析只认它）：先 PATH 上的，再 Hermes 自带 venv。
# 没有它时**不猜**：退回到「严格兜底」读法，任何可疑写法一律 exit 2（无法判定 ⇒ 拒绝安装）。
_PY=""
command -v python3 >/dev/null 2>&1 && _PY="python3"
_PY_YAML=""
if [ -n "$_PY" ] && "$_PY" -c 'import yaml' >/dev/null 2>&1; then _PY_YAML="$_PY"; fi
for _cand in "$HERMES_HOME/hermes-agent/venv/bin/python3" "/opt/hermes/.venv/bin/python3"; do
  if [ -z "$_PY_YAML" ] && [ -x "$_cand" ] && "$_cand" -c 'import yaml' >/dev/null 2>&1; then
    _PY_YAML="$_cand"
  fi
done

# 读清单字段。退出码是语义，不是随手值：
#   0 = 读到了（输出为空 ⇒ 这个键确实不存在 ⇒ 调用方按 Hermes 语义回退目录名）
#   2 = **无法判定**（读不到 / 没有 python3 / 没有 PyYAML 且写法可疑）⇒ 调用方归 UNKNOWN（fail-closed）
#   3 = 不是插件（解析失败 / 顶层不是映射 / json 不是合法 portable 清单）⇒ 调用方跳过
# 为什么不用 sed 猜（P2-1 实测 8 类静默漏判）：锚点 `&a`、别名 `*n`、重复 name、块标量 `|-`/`>-`、
# 标签 `!!str`、BOM、单引号键、整份缩进 —— sed 读不出时若回退目录名，就变成「不是本插件」**静默放行**。
_manifest_field() {
  if [ -z "$_PY" ]; then return 2; fi
  case "$1" in
    *plugin.json) "$_PY" -c '
import json, sys
try:
    d = json.loads(open(sys.argv[1], "rb").read().decode("utf-8-sig"))
except OSError:
    sys.exit(2)
except Exception:
    sys.exit(3)
if not isinstance(d, dict) or not d.get("$schema"):
    sys.exit(3)
v = d.get(sys.argv[2])
print(v if isinstance(v, str) else ("" if v is None else str(v)))
' "$1" "$2" 2>/dev/null ;;
    *)
      if [ -n "$_PY_YAML" ]; then
        "$_PY_YAML" -c '
import sys
try:
    raw = open(sys.argv[1], "rb").read()
except OSError:
    sys.exit(2)
try:
    import yaml
    d = yaml.safe_load(raw.decode("utf-8-sig"))
except ImportError:
    sys.exit(2)
except Exception:
    sys.exit(3)
if not isinstance(d, dict):
    sys.exit(3)
v = d.get(sys.argv[2])
print(v if isinstance(v, str) else ("" if v is None else str(v)))
' "$1" "$2" 2>/dev/null
      else
        # 严格兜底：只认列 0 的 `key: value` 简单标量；可疑写法（锚点/别名/标签/块标量/引号/
        # 重复键/BOM/缩进/值在下一行）一律 exit 2 —— **宁可拒绝安装，也不静默放过一个遮蔽**。
        "$_PY" -c '
import sys
try:
    raw = open(sys.argv[1], "rb").read()
except OSError:
    sys.exit(2)
if raw.startswith(b"\xef\xbb\xbf"):
    sys.exit(2)
try:
    text = raw.decode("utf-8")
except UnicodeDecodeError:
    sys.exit(2)
key = sys.argv[2]
found = None
for line in text.splitlines():
    if not line.strip() or line.lstrip().startswith("#"):
        continue
    if line[0] in " \t":
        # 缩进行：可能是嵌套映射（Hermes 不算 name），也可能是**整份缩进的合法清单**
        # （Hermes 会算 name）—— 分辨不了 ⇒ 只要出现 name 键就报「无法判定」（fail-closed，
        # 且 UNKNOWN 只会拒绝、绝不会被 --fix-shadow 搬走）。P2-1-3。
        stripped = line.strip()
        if (stripped.startswith("name:") or stripped.startswith(chr(34) + "name" + chr(34))
                or stripped.startswith(chr(39) + "name" + chr(39))):
            sys.exit(2)
        continue
    if line.startswith("---") or line.startswith("..."):
        sys.exit(2)
    if ":" not in line:
        sys.exit(2)
    k, sep, v = line.partition(":")
    k = k.strip()
    if k[:1] in (chr(34), chr(39), "&", "*", "!", "|", ">", "[", "{"):
        sys.exit(2)
    if not sep or (v[:1] not in ("", " ")):
        sys.exit(2)
    if k != key:
        continue
    if found is not None:
        sys.exit(2)
    v = v.strip()
    if v[:1] in ("&", "*", "!", "|", ">", "[", "{", chr(34), chr(39)) or v.startswith("#"):
        sys.exit(2)
    found = v.split(" #", 1)[0].strip()
print(found if found is not None else "")
' "$1" "$2" 2>/dev/null
      fi
      ;;
  esac
}

_REPO_NAME=""
for _c in "$REPO_DIR/plugin.yaml" "$REPO_DIR/plugin.yml"; do
  if [ -f "$_c" ]; then
    _REPO_NAME="$(sed -n 's/^name[[:space:]]*:[[:space:]]*//p' "$_c" | head -1 | tr -d '\r' \
                  | sed 's/[[:space:]]*#.*$//' | sed 's/^["'"'"']//; s/["'"'"']$//' | sed 's/[[:space:]]*$//')"
    break
  fi
done
if [ -z "$_REPO_NAME" ]; then
  echo "✗ 读不到本仓库的插件清单（plugin.yaml）里的 name，无法做同名目录检查。" >&2
  exit 1
fi
_TARGET_BASE="$(dirname "$TARGET")"
_TARGET_NAME="$(basename "$TARGET")"

# 三个桶：WIN = 会遮蔽本次安装；LOSE = 同名但排序在前（本次不影响）；UNKNOWN = 无法判定。
_SHADOW_WIN_NAMES=(); _SHADOW_WIN_VERS=(); _SHADOW_LOSE_ROWS=(); _SHADOW_UNK_NAMES=()

_scan_shadow_dirs() {
  # 只有「直接装在 $PLUGINS_DIR 下」才受扁平遮蔽影响：分类子目录的注册键是 <分类>/<目录名>，
  # 不参与同名遮蔽；装到 plugins/ 之外时更不该去动 plugins/ 里的目录（P2-2 必-4）。
  [ "$_TARGET_BASE" = "$PLUGINS_DIR" ] || return 0
  [ -d "$_TARGET_BASE" ] || return 0
  for _d in "$_TARGET_BASE"/*/; do
    [ -d "$_d" ] || continue
    if [ -e "$TARGET" ] && [ "$_d" -ef "$TARGET" ]; then continue; fi   # 自排除：比 inode，不比字符串
    _dn="$(basename "$_d")"
    # 目录本身读不到（权限 / 属主不同）⇒ **无法判定**：我们读不到不代表网关读不到（NAS 上常见）。
    if [ ! -r "$_d" ]; then _SHADOW_UNK_NAMES+=("$_dn"); continue; fi
    _mf=""
    for _c in plugin.yaml plugin.yml; do
      if [ -f "$_d$_c" ]; then _mf="$_d$_c"; break; fi
    done
    if [ -z "$_mf" ] && [ -f "$_d/plugin.json" ]; then
      # Hermes 只认**合法 portable 清单**（要有 $schema）：非法 json 它直接跳过 ⇒ 不能当插件处理，
      # 否则 `--fix-shadow` 会把一个 Hermes 根本不加载的目录搬走（P2-2 必-3b）。
      # `$schema` 只查**非空**（不查精确 URL）：宁可把 Hermes 会跳过的目录当插件拦住（fail-closed），
      # 也不能因为将来 schema URL 变了就静默放过一个真遮蔽（fail-open 正是本版要修的病）。
      if [ -z "$_PY" ]; then
        _SHADOW_UNK_NAMES+=("$_dn"); continue    # 没有 python3 ⇒ 无法判定（P2-1-2）
      fi
      # 合法性（是不是 Hermes 认的 portable 清单）**只在 _manifest_field 里判一次** ——
      # 两处都判的话，任何单点变异都变成等价改动（门禁测不出来），也没必要。
      _mf="$_d/plugin.json"
    fi
    [ -n "$_mf" ] || continue        # 连清单都没有 ⇒ 不是插件目录（Hermes 也跳过它）
    if [ ! -r "$_mf" ]; then _SHADOW_UNK_NAMES+=("$_dn"); continue; fi
    _rc_nm=0
    _nm="$(_manifest_field "$_mf" name)" || _rc_nm=$?
    _rc_vr=0
    _vr="$(_manifest_field "$_mf" version)" || _rc_vr=$?
    if [ "$_rc_nm" = "3" ] || [ "$_rc_vr" = "3" ]; then
      continue                     # 不是插件（解析失败 / 顶层非映射 / json 缺 $schema）
    fi
    if [ "$_rc_nm" != "0" ] || [ "$_rc_vr" != "0" ]; then
      _SHADOW_UNK_NAMES+=("$_dn"); continue    # 读不到 / 没有 yaml 库 / 没有 python3 ⇒ 无法判定
    fi
    if [ -z "$_nm" ]; then
      # **键确实不存在**时 Hermes 才回退目录名（实测 `zlark/plugin.yaml` 无 name 也能注册）；
      # 键存在但读不出已经在上面的退出码里归入「无法判定」了（P2-1-1：那是 fail-open 的入口）。
      _nm="$_dn"
    fi
    if [ "$_nm" != "$_REPO_NAME" ]; then
      continue
    elif [[ "$_dn" > "$_TARGET_NAME" ]]; then
      _SHADOW_WIN_NAMES+=("$_dn"); _SHADOW_WIN_VERS+=("${_vr:-?}")
    else
      _SHADOW_LOSE_ROWS+=("${_dn}（v${_vr:-?}）")
    fi
  done
  return 0
}
_scan_shadow_dirs || true

# 装了也不会被加载：`--target` 指到 $HERMES_HOME/plugins 之外时先说清楚（别让人白装）。
case "$_TARGET_BASE/" in
  "$PLUGINS_DIR"/*) ;;   # 分类子目录（plugins/<分类>/<目录>）：Hermes 会加载，不用提示
  *)
    echo "提示：安装目标在 ${PLUGINS_DIR} 之外（${_TARGET_BASE}）—— Hermes 只扫描 ${PLUGINS_DIR}，本目标不会被加载。" >&2
    ;;
esac

if [ "${#_SHADOW_LOSE_ROWS[@]}" -gt 0 ]; then
  echo "提示：plugins/ 下还有同名目录（排序在 $_TARGET_NAME 之前，本次不会被它遮蔽）：" >&2
  _i=0
  while [ "$_i" -lt "${#_SHADOW_LOSE_ROWS[@]}" ]; do
    echo "  - ${_SHADOW_LOSE_ROWS[$_i]}" >&2
    _i=$((_i + 1))
  done
  echo "  建议一并搬到 $BACKUP_DIR/（同名目录留着迟早出事）。" >&2
fi

if [ "${#_SHADOW_WIN_NAMES[@]}" -gt 0 ]; then
  echo "⚠️  检测到会遮蔽本次安装的同名目录（Hermes 按清单 name 记账，目录名字典序更大的赢）：" >&2
  _i=0
  while [ "$_i" -lt "${#_SHADOW_WIN_NAMES[@]}" ]; do
    echo "  - ${_SHADOW_WIN_NAMES[$_i]}（v${_SHADOW_WIN_VERS[$_i]}）" >&2
    _i=$((_i + 1))
  done
  if [ "$FIX_SHADOW" = "1" ]; then
    mkdir -p "$BACKUP_DIR"
    _ts="$(date +%Y%m%d%H%M%S)"
    _i=0
    while [ "$_i" -lt "${#_SHADOW_WIN_NAMES[@]}" ]; do
      _dn="${_SHADOW_WIN_NAMES[$_i]}"
      _dest="$BACKUP_DIR/$_dn-$_ts"
      _k=2
      while [ -e "$_dest" ]; do _dest="$BACKUP_DIR/$_dn-$_ts-$_k"; _k=$((_k + 1)); done
      mv "$_TARGET_BASE/$_dn" "$_dest"
      _SHADOW_MOVED=1
      echo "已搬走（只 mv，不删）：$_TARGET_BASE/$_dn -> $_dest"
      _i=$((_i + 1))
    done
    _SHADOW_WIN_NAMES=(); _SHADOW_WIN_VERS=(); _SHADOW_UNK_NAMES=()
    _scan_shadow_dirs || true
  fi
fi

# 无法判定必须**先说出来**：否则 WIN 分支的 exit 3 会把 UNKNOWN 一起吞掉（P2-3 审计实测）。
if [ "${#_SHADOW_UNK_NAMES[@]}" -gt 0 ]; then
  echo "⚠️  无法检查这些同级目录（读不到插件清单，可能是权限/属主不同）：" >&2
  _i=0
  while [ "$_i" -lt "${#_SHADOW_UNK_NAMES[@]}" ]; do
    echo "  - ${_SHADOW_UNK_NAMES[$_i]}" >&2
    _i=$((_i + 1))
  done
  echo "  我们读不到不代表网关读不到 —— 它们可能遮蔽本插件，请人工确认。" >&2
fi

if [ "${#_SHADOW_WIN_NAMES[@]}" -gt 0 ] && [ "$ALLOW_SHADOW" != "1" ]; then
  echo "" >&2
  echo "✗ 拒绝安装：上面的同名目录会遮蔽本次安装，装了也不会生效（这是 2026-10-08 真机事故的成因）。" >&2
  echo "  出路（任选一条）：" >&2
  printf '    1) ./install.sh%s --fix-shadow      # 自动把它们搬到 %s/（只 mv，不删）\n' "${_ORIG_ARGS:+ $_ORIG_ARGS}" "$BACKUP_DIR" >&2
  printf '    2) mkdir -p "%s" && mv "%s/目录名" "%s/"\n' "$BACKUP_DIR" "$_TARGET_BASE" "$BACKUP_DIR" >&2
  printf '    3) ./install.sh%s --allow-shadow    # 已知情、接受风险，仍继续\n' "${_ORIG_ARGS:+ $_ORIG_ARGS}" >&2
  exit 3
fi

if [ "${#_SHADOW_UNK_NAMES[@]}" -gt 0 ] && [ "$ALLOW_SHADOW" != "1" ]; then
  echo "" >&2
  echo "✗ 拒绝安装：上面的目录无法判定，不能排除它们遮蔽本插件的可能。" >&2
  printf '    出路：确认它们无关后重跑，或显式 ./install.sh%s --allow-shadow\n' "${_ORIG_ARGS:+ $_ORIG_ARGS}" >&2
  exit 3
fi

if [ "$ALLOW_SHADOW" = "1" ] && { [ "${#_SHADOW_WIN_NAMES[@]}" -gt 0 ] || [ "${#_SHADOW_UNK_NAMES[@]}" -gt 0 ]; }; then
  echo "⚠️  --allow-shadow：已知情继续安装。装完请按下面的验证步骤确认实际加载的版本。" >&2
fi

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

if [ "${_SHADOW_MOVED:-0}" = "1" ] && [ -e "$TARGET" ]; then
  # 真实事故现场就是这样：plugins/larkdeck（新版）已在，遮蔽来自 larkdeck.bak-…。
  # 搬完遮蔽就解除了 —— 此时**不能**再报「目标已存在 ⇒ 失败」（用户会以为没修好，
  # 而新进程 `hermes plugins list` 其实已经是新版；P2-2 必-2）。
  echo "✓ 遮蔽已解除（上面搬走的目录只 mv、没有删除）。" >&2
  echo "  已有目标未覆盖：$TARGET" >&2
  echo "  下一步：hermes gateway restart，然后按下面的验证步骤确认实际加载的版本。" >&2
  exit 0
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
# 两种模式都要打印：下面「必须与上面「自检版本」一致」对 --link 同样成立（neat-freak 中-2）。
echo "自检版本    : $(sed -n 's/^version[[:space:]]*:[[:space:]]*//p' "$REPO_DIR/plugin.yaml" | head -1)"

cat <<EOF

装好了。先确认实际生效的是这一份（必须与上面「自检版本」一致）：

  hermes plugins list | grep larkdeck
  grep '\[larkdeck\] 启动自检' "$HERMES_HOME/logs/agent.log" | tail -1   # 行末有「插件 v<版本> @ <目录>」
  在飞书里发 /larkdeck status —— 卡片首行版本必须一致

（装在容器里就在容器内、用网关同一用户与同一 HERMES_HOME 跑这三条。）

还差一步：把 larkdeck 加进配置。

  $HERMES_HOME/config.yaml

    plugins:
      enabled:
        - larkdeck

然后重启网关。启动日志里会有一行 [larkdeck] 自检结果。
自检失败会打 ERROR 并保持官方适配器原样工作 —— 卡片不生效，但飞书不会被弄坏。
EOF
