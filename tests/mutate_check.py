"""变异验证器：每条**新加的断言**都必须能证明「把对应的修复撤掉就变红」。

用法::

    python3 tests/mutate_check.py            # 只跑清单里的变异
    python3 tests/mutate_check.py -k P1      # 名字里含 P1 的

为什么要有它（本项目的反复踩坑）：
  * 第七路审计实测 50 条变异里 **23 条四个门禁全绿** —— 断言写着、但对该变异没有判别力；
  * 更要命的是阻断项本身：`_MAX_TRACKED_TEXT` 被同文件另一句赋值覆盖，
    而回归用例的 fixture 恰好卡在被覆盖后的阈值上 ⇒ 那条「修复」从未真正生效。
所以规矩是：**先写变异，再写断言**；撤掉修复必须红。

实现要点（踩过的坑）：
  * 快照目录**必须叫 `larkdeck`** —— `check_override.py` 把仓库父目录加进 PYTHONPATH，
    目录名不对的话 `import larkdeck` 会解析到**未变异的基线**（曾导致 18 条假 GREEN）；
  * 每个变异放在**独立父目录**下，避免 `__pycache__` 串味；跑之前清一次。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: (名字, 相对文件, 原文, 替换成, 期望其中哪个门禁变红)
#: 期望值只用于打印对照；判定标准是「至少一门红」。
MUTATIONS = [
    # ---- P1 阻断：阈值被覆盖 / 阈值不再贴着飞书硬上限 ------------------------ #
    ("P1-阈值被后面的旧赋值覆盖（历史阻断项的形态）", "core/adapter.py",
     "_MAX_TEXT_ENTRIES = 16",
     "_MAX_TEXT_ENTRIES = 16\n_MAX_TRACKED_TEXT = _cards.CARD_BYTE_BUDGET",
     "test_units"),
    ("P1-阈值高到超过飞书硬上限", "core/adapter.py",
     "_CARD_BYTES_OVERHEAD = 1024", "_CARD_BYTES_OVERHEAD = -100000",
     "test_units"),
    ("P1-阈值退回降载预算", "core/adapter.py",
     "_MAX_TRACKED_TEXT = _FEISHU_CARD_BYTE_LIMIT - _CARD_BYTES_OVERHEAD",
     "_MAX_TRACKED_TEXT = _cards.CARD_BYTE_BUDGET",
     "test_units"),
    # ---- P8：日志单位口径 ---------------------------------------------------- #
    ("P8-日志传字符数", "core/adapter.py",
     "        size = len(body.encode(\"utf-8\", \"ignore\"))",
     "        size = len(body)",
     "test_units"),
    # ---- P2：越界静默 -------------------------------------------------------- #
    ("P2-越界告警降级成 debug", "core/adapter.py",
     '    logger.warning("[larkdeck] streaming_print_ms 配置有问题：%s —— 已退回 %dms"',
     '    logger.debug("[larkdeck] streaming_print_ms 配置有问题：%s —— 已退回 %dms"',
     "test_units"),
    ("P2b-坏值（转不动）不再报警", "core/adapter.py",
     "    if value is None and raw is not None:",
     "    if False:",
     "test_units"),
    # ---- P3：config_schema 解析漏判 ------------------------------------------ #
    ("P3a-加一个含连字符的 yaml-only 键", "plugin.yaml",
     "  cards:\n", "  brand-new-key:\n    type: boolean\n    default: true\n  cards:\n",
     "test_units"),
    ("P3a-加一个含数字的 yaml-only 键", "plugin.yaml",
     "  cards:\n", "  brand2:\n    type: string\n    default: \"x\"\n  cards:\n",
     "test_units"),
    ("P3b-把一个键挪到别的键之后", "plugin.yaml",
     "  cards:\n", "  reactions-x:\n    default: true\n  cards:\n",
     "test_units"),
    ("P3c-type 与代码默认值不符", "plugin.yaml",
     "  streaming_print_ms:\n    type: integer",
     "  streaming_print_ms:\n    type: boolean",
     "test_units"),
    ("P3d-整数默认值写成带引号", "plugin.yaml",
     "    default: 15\n", "    default: \"15\"\n", "test_units"),
    ("P3e-整数默认值写成浮点", "plugin.yaml",
     "    default: 15\n", "    default: 15.0\n", "test_units"),
    ("P3f-布尔默认值写成 1", "plugin.yaml",
     "  cards:\n    type: boolean\n    default: true",
     "  cards:\n    type: boolean\n    default: 1",
     "test_units"),
    ("P3g-删掉一个键", "plugin.yaml",
     "  reactions:\n    type: boolean\n    default: true\n", "", "test_units"),
    # ---- P4：M22 归因 + 直接打被保护的那层 ----------------------------------- #
    ("P4-删掉 _positive 的 nan/inf 半段", "core/cards.py",
     "    return number == number and number not in (float(\"inf\"), float(\"-inf\")) and number > 0",
     "    return number > 0", "test_units"),
    ("P4-streaming_config 不再夹取", "core/cards.py",
     "    if not 1 <= value <= PRINT_FREQUENCY_MAX_MS:\n        value = DEFAULT_PRINT_FREQUENCY_MS",
     "    if False:\n        value = DEFAULT_PRINT_FREQUENCY_MS",
     "test_units"),
    # ---- P5：probe_report 形状 + 两条 WARNING -------------------------------- #
    ("P5a-probe_report 不再上报 missing_reactions", "core/compat.py",
     "    report[\"missing_reactions\"] = [n for n in REACTION_ADAPTER_ATTRS if not _has(cls, n)]",
     "    report[\"missing_reactions\"] = []", "check_override"),
    ("P5a2-probe_report 删掉 missing_reactions 键", "core/compat.py",
     "    report[\"missing_reactions\"] = [n for n in REACTION_ADAPTER_ATTRS if not _has(cls, n)]",
     "    pass", "check_override"),
    ("P5b-删掉 reactions 那条 WARNING", "core/adapter.py",
     "    if missing_reactions:\n        logger.warning", "    if False:\n        logger.warning",
     "test_units"),
    ("P5c-两条 WARNING 的列表串了", "core/adapter.py",
     "\"`reactions: false` 会静默失效（「处理中」表情照旧）\", missing_reactions)",
     "\"`reactions: false` 会静默失效（「处理中」表情照旧）\", missing_signal)",
     "test_units"),
    ("P5d-报告缺键时不再报警", "core/adapter.py",
     "    if absent:\n        logger.warning(\"[larkdeck] 能力探测报告缺少",
     "    if False:\n        logger.warning(\"[larkdeck] 能力探测报告缺少",
     "test_units"),
    ("P5e-REACTION_ADAPTER_ATTRS 不再登记 _reactions_enabled", "core/compat.py",
     "REACTION_ADAPTER_ATTRS: Tuple[str, ...] = (\n    \"_reactions_enabled\",\n)",
     "REACTION_ADAPTER_ATTRS: Tuple[str, ...] = ()",
     "test_units"),
    # ---- 第十四轮（真机 --stop-redraw 抓到的）状态色载体 --------------------- #
    ("S1-超预算档不再补状态小面板", "core/cards.py",
     "        if card_bytes(with_shell) <= FEISHU_CARD_BYTE_LIMIT:",
     "        if False:",
     "test_units"),
    ("S2-小面板不受硬上限约束", "core/cards.py",
     "        if card_bytes(with_shell) <= FEISHU_CARD_BYTE_LIMIT:",
     "        if True:",
     "test_units"),
    ("S3-status_shell 不认状态色", "core/cards.py",
     "    if not color or color == BORDER_NEUTRAL:",
     "    if not color or color == BORDER_NEUTRAL or True:",
     "test_units"),
    ("S4-status_shell 丢掉状态文字", "core/cards.py",
     "    return collapsible(title, [md(_i18n.t(_STATUS_TEXT_KEYS.get(status, \"panel.title\")))],\n                       expanded=False, border_color=color)",
     "    return collapsible(title, [], expanded=False, border_color=color)",
     "test_units"),
    # ---- 第八路审计的 43 条变异里「全绿」的那些，逐条补上门禁 ------------------ #
    ("A01-硬上限改到远超实测包络", "core/cards.py",
     "FEISHU_CARD_BYTE_LIMIT = 128000", "FEISHU_CARD_BYTE_LIMIT = 999999",
     "test_units"),
    ("A03-余量放大到 50000（窗口被压小）", "core/adapter.py",
     "_CARD_BYTES_OVERHEAD = 1024", "_CARD_BYTES_OVERHEAD = 50000",
     "test_units"),
    ("A05-余量缩到实测开销 298", "core/adapter.py",
     "_CARD_BYTES_OVERHEAD = 1024", "_CARD_BYTES_OVERHEAD = 298",
     "test_units"),
    ("A07-阈值改成手写死值", "core/adapter.py",
     "_MAX_TRACKED_TEXT = _FEISHU_CARD_BYTE_LIMIT - _CARD_BYTES_OVERHEAD",
     "_MAX_TRACKED_TEXT = 127000",
     "test_units"),
    ("A08-正文只留 1 份", "core/adapter.py",
     "_MAX_TEXT_ENTRIES = 16", "_MAX_TEXT_ENTRIES = 1",
     "test_units"),
    ("B14-超限改成截断正文", "core/adapter.py",
     "            body = """, "            body = body[:1000]",
     "test_units"),
    ("C18-上限改成字面量 2001（与 cards 分叉）", "core/adapter.py",
     "    high = _cards.PRINT_FREQUENCY_MAX_MS", "    high = 2001",
     "test_units"),
    ("C24-坏值不再报警", "core/adapter.py",
     '        _log_print_ms_once(f"{raw!r} 不是可用的数字", default)',
     "        pass",
     "test_units"),
    ("D24-删掉一个键的 type 声明", "plugin.yaml",
     "  streaming_print_ms:\n    type: integer\n", "  streaming_print_ms:\n",
     "test_units"),
    ("D25-对照：default 加行内注释（合法 YAML，应当全绿）", "plugin.yaml",
     "    default: 15\n", "    default: 15  # 毫秒\n",
     "test_units"),
    ("D26-default 改折叠标量", "plugin.yaml",
     "    default: 15\n", "    default: >\n      15\n",
     "test_units"),
    ("D27-解析器不再响亮失败", "tests/test_units.py",
     "            raise _unsupported(line)\n        if re.match(r\"^ {4,}- \", line):",
     "            continue\n        if re.match(r\"^ {4,}- \", line):",
     "test_units"),
    ("D28-段尾顶格行静默 break", "tests/test_units.py",
     "            if re.match(r\"^[A-Za-z_][A-Za-z0-9_.-]*:\", line):\n                break\n            raise _unsupported(line)",
     "            break",
     "test_units"),
    ("E37-PROBE_REPORT_KEYS 少一项", "core/compat.py",
     "    \"missing_callback\", \"missing_signal\", \"missing_reactions\", \"session_attribution_ok\",\n)",
     "    \"missing_callback\", \"missing_signal\", \"session_attribution_ok\",\n)",
     "test_units"),
    # ---- P6：黄金路径耗时 ---------------------------------------------------- #
    ("P6-轮耗时恒为 0", "core/panel.py",
     "    current[\"elapsed_ms\"] = max(0, int((now - float(current.get(\"started\") or now)) * 1000))",
     "    current[\"elapsed_ms\"] = 0",
     "check_hooks"),
]


def _prepare(dest_parent: Path) -> Path:
    """把工作树拷成 ``<dest_parent>/larkdeck``（目录名必须是 larkdeck，见模块 docstring）。"""
    dest = dest_parent / "larkdeck"
    shutil.copytree(REPO, dest, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "*.pyc", ".pytest_cache", "docs/deliveries"))
    return dest


#: 门禁失败的两种形态必须分开看：**断言失败**是判别力证据，**崩溃**（语法错误 / import 炸）
#: 只是「你把代码弄坏了」—— 第八路审计实测：语法错误型变异会让四个门禁全红
#: （`invalid syntax (adapter.py, line 99)`），如果把它也算「抓住了」，那这个验证器就在骗人。
_CRASH_MARKERS = ("SyntaxError", "invalid syntax", "IndentationError", "Traceback (most recent call last)")
_ASSERT_MARKERS = ("AssertionError", "FAIL", "FAILED", "passed")


def _classify(proc: "subprocess.CompletedProcess") -> str:
    """``red-assert``（真有判别力）/ ``red-crash``（只是崩了，不算证据）/ ``green``。"""
    if proc.returncode == 0:
        return "green"
    blob = (proc.stdout or "") + (proc.stderr or "")
    crashed = any(marker in blob for marker in _CRASH_MARKERS)
    asserted = any(marker in blob for marker in _ASSERT_MARKERS)
    if crashed and not asserted:
        return "red-crash"
    return "red-assert"


def _run_gates(repo: Path) -> "dict[str, tuple[int, str]]":
    out = {}
    for script in ("test_units.py", "check_override.py", "check_hooks.py",
                   "check_clarify_e2e.py"):
        proc = subprocess.run([sys.executable, str(repo / "tests" / script)],
                              capture_output=True, text=True, cwd=str(repo.parent),
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        tail = (proc.stdout or proc.stderr).strip().splitlines()
        out[script] = (_classify(proc), tail[-1] if tail else "")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="只跑名字里含该子串的变异")
    ap.add_argument("--keep", action="store_true", help="保留临时目录（排查用）")
    args = ap.parse_args()

    picked = [m for m in MUTATIONS if args.k in m[0]]

    # ⚠️ **基线守卫**（第八路审计实测出来的假绿源头）：基线自己就是红的时候，
    # 每条变异当然都「变红」—— 于是这个验证器会满口「全部被门禁抓住 ✅」。
    # 实测复现：把历史阻断项那句老赋值塞回基线（test_units 115/116），再跑 `-k P1`
    # 依然报全绿 + exit 0。所以基线必须是绿的，否则直接退出、不给结论。
    baseline = _run_gates(REPO)
    print(f"基线自校验：{baseline}")
    baseline_red = {k: v for k, v in baseline.items() if v[0] != "green"}
    if baseline_red:
        print("❌ 基线不是绿的，本次不给任何结论（否则每条变异都会「被抓住」）：")
        for name, (kind, last) in baseline_red.items():
            print(f"   {name}: {kind} · {last}")
        print("   注意：本验证器跑的是**工作树**，未提交的改动会一起被验。")
        return 2

    bad = []
    tmp_root = Path(tempfile.mkdtemp(prefix="larkdeck-mut-"))
    try:
        for idx, (name, rel, old, new, expect) in enumerate(picked):
            parent = tmp_root / f"mut{idx:02d}"
            parent.mkdir(parents=True)
            repo = _prepare(parent)
            target = repo / rel
            text = target.read_text(encoding="utf-8")
            if old not in text:
                bad.append(f"{name}: 变异锚点没找到（源码变了？）")
                print(f"❓ {name}: 锚点没找到")
                continue
            target.write_text(text.replace(old, new, 1), encoding="utf-8")
            results = _run_gates(repo)
            red = [k for k, (kind, _) in results.items() if kind != "green"]
            crashed = [k for k, (kind, _) in results.items() if kind == "red-crash"]
            evidence = [k for k in red if k not in crashed]
            expect_script = expect if expect.endswith(".py") else expect + ".py"
            control = "对照" in name
            if control:
                status = "⚪ 对照全绿（符合预期）" if not red else "❌ 对照项居然变红了（假红！）"
                print(f"{status} {name}  实红={red}")
                if red:
                    bad.append(f"{name}: 对照变异（行为等价）竟然让门禁变红 —— 门禁有假红")
                continue
            if not red:
                status = "🟢 全绿（**断言没有判别力！**）"
            elif not evidence:
                status = "💥 只有崩溃（语法错误 / import 炸），**不算判别力证据**"
            else:
                status = "🔴 断言失败"
            print(f"{status} {name}  期望={expect} 实红={red} 断言红={evidence}")
            if not red:
                bad.append(f"{name}: 撤掉修复后四门禁仍然全绿 —— 断言没有判别力")
            elif not evidence:
                bad.append(f"{name}: 只有崩溃、没有断言失败 —— 不能算被门禁抓住")
            elif expect_script not in evidence:
                print(f"   ⚠️ 期望 {expect} 变红，实际是 {evidence}（也算被守住了，但归因不准）")
    finally:
        if args.keep:
            print(f"临时目录保留在 {tmp_root}")
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)

    if bad:
        print("\n结论：")
        for line in bad:
            print(" -", line)
        return 1
    print(f"\n全部 {len(picked)} 条变异都被门禁抓住 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
