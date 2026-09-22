#!/usr/bin/env python3
# ⚠️ 本文件是 **2026-09-21 那晚实际用的合并器** `merge_ledger4.py` 的**仓内固化副本**
# （原文在 `~/.larkdeck-scratch/v0.7.2-full-20260921/merge_ledger4.py`）。为什么搬进来：
# 发布脚本的 1b) 步依赖它做「现场重算三指纹 + 日志覆盖 + 12 名对照」，而**依赖一份 scratch
# 路径**在下一次发布时可能已经不存在（计划 §2 末行处置⑤：「依赖改硬失败」）。搬进 `tools/`
# 还能顺带进版本库 —— 它不参与安装（`install.sh` 排除 `./tools/*`），也不进任何指纹
# （`_HERMES_FILES` / `_HELPER_FILES` 都不含 `tools/`）。
# 逐字内容与原文相同（只加这段头）。
"""v0.7.2 账本合并器 **v4**（B 路审计 B4/B5 指出的洞已补；仍不是「证据生成器」）。

相对 v3（`merge_ledger3.py`，实际用过的那份）补了什么：

  1. `--head` 必须是**真实 commit**（`git cat-file -e <head>^{commit}`）；v3 能写 `deadbee`；
  2. **对现场树重算三指纹**：每条都要 `fp`/`helper_fp` 逐字相等、`gate_fp` 子集（与
     `_delta_split` 同口径）。v3 只在 base 与 fresh 都有非空字段时才比，且从不重算 ⇒
     base 里把 fp 改成全 0 也能过；
  3. `at` **不再无条件覆盖**：每条取自己的来源（fresh 若 `at==head`，否则 base 的 `at`），
     并要求所有 `at` ∈ {`--head`} ∪ `--allow-at`（用来接受「早先在冻结树跑过、指纹至今未变」
     的条目，例如全量直跑那批）；
  4. 对照证据按**名字**核：必须集齐 `CONTROLS` 的全部名字（12 条）的
     `⚪ 对照全绿（符合预期） <name>` 行，且没有 `❌ 对照变红了（假红！）`；
     v3 只数 ≥6 行文本（同一份文件可同时充当 🔴 覆盖与对照证据）。

**能力边界（写在这里，免得被当门禁用）**：它只能核对「名字集合 + 三指纹 + 日志行 + 对照名」。
它**不能**证明日志真的来自一次真实运行 —— 伪造一份含 475 行 `🔴 断言失败 <精确名>` 的文本
仍然能骗过它（B5 实测过 v3）。真正的修法（每条记 tree hash + 来源日志 + 行号，工具侧生成）
已登记 v0.7.3。用法::

    $PY merge_ledger4.py --head <ref> --base <ledger.json> [--fresh f.json ...] \
        [--log l.log ...] [--allow-at <ref> ...] [--write]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path("/Users/Zayn/Code/larkdeck")


def load_entries(path: str) -> dict:
    p = pathlib.Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8")).get("entries") or {}


def reds(path: str) -> set:
    p = pathlib.Path(path)
    if not p.exists():
        return set()
    return {l[len("🔴 断言失败 "):].split("  期望=")[0].strip()
            for l in p.read_text(encoding="utf-8", errors="replace").splitlines()
            if l.startswith("🔴 断言失败 ")}


def ctl_green(path: str) -> set:
    p = pathlib.Path(path)
    if not p.exists():
        return set()
    out = set()
    for l in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"⚪ 对照全绿（符合预期） (.+?)\s*$", l)
        if m:
            out.add(m.group(1).strip())
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--head", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--fresh", action="append", default=[])
    ap.add_argument("--log", action="append", default=[])
    ap.add_argument("--allow-at", action="append", default=[])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--allow-dirty", action="store_true",
                    help="⚠️ 只在排查时用：允许在工作树不干净时盖章（章会指向一个**不含本次改动**的 tree）")
    a = ap.parse_args()

    if subprocess.run(["git", "cat-file", "-e", f"{a.head}^{{commit}}"], cwd=REPO).returncode != 0:
        print(f"❌ --head {a.head!r} 不是真实 commit")
        return 2

    # ⚠️ **脏树不许盖章**（审计 B 第 4 条，中）：本脚本的指纹是在**现场工作树**上重算的，
    # 而章写的是 commit 短码 ⇒ 工作树不干净时，章指向的 tree 并不包含被测改动，
    # 事实错误。跑全量前必须先 commit（R16），这里把它变成硬失败。
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=REPO,
                           capture_output=True, text=True).stdout.strip()
    if dirty and not a.allow_dirty:
        print("❌ 工作树不干净 —— 拒绝盖章（章会指向一个不含本次改动的 tree）：")
        print("\n".join(dirty.splitlines()[:12]))
        print("   先 commit（R16：代码+测试+夹具一次性提交）再重跑合并；"
              "排查时可用 --allow-dirty 放行（会记进 _meta.tree_dirty）。")
        return 2
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=REPO,
                          capture_output=True, text=True).stdout.strip()

    sys.path.insert(0, str(REPO / "tests"))
    import mutate_check as M
    expected = {m[0] for m in M.MUTATIONS if m[4]}
    controls = {c[0] for c in M.CONTROLS}
    byname = {m[0]: m for m in M.MUTATIONS}

    base = load_entries(a.base)
    fresh: dict = {}
    for f in a.fresh:
        for k, v in load_entries(f).items():
            if v.get("at") == a.head:
                fresh[k] = v
    names = set(base) | set(fresh)
    if names != expected:
        print(f"❌ 名字集合不匹配：多 {sorted(names - expected)[:4]} / 少 {sorted(expected - names)[:4]}")
        return 2

    judged: set = set()
    green: set = set()
    for lg in a.log:
        judged |= reds(lg)
        green |= ctl_green(lg)
    miss = sorted(n for n in names if n not in judged)
    if miss:
        print(f"❌ 证据没盖住 {len(miss)} 条：{miss[:5]}")
        return 2
    if not controls <= green:
        print(f"❌ 对照名字没集齐（缺 {sorted(controls - green)[:4]}，共需 {len(controls)} 条）")
        return 2

    allowed_at = {a.head} | set(a.allow_at)
    merged: dict = {}
    bad: list = []
    for n in sorted(expected):
        rel, old, new = byname[n][1], byname[n][2], byname[n][3]
        e = dict(base.get(n) or {})
        if n in fresh:
            e.update(fresh[n])
        if not e:
            bad.append(f"{n}: 无来源")
            continue
        if e.get("verdict") != "red-assert":
            bad.append(f"{n}: verdict={e.get('verdict')!r}")
        if e.get("at") not in allowed_at:
            bad.append(f"{n}: at={e.get('at')!r} 不在允许集合")
        # 现场树重算三指纹
        if e.get("fp") != M._fingerprint(rel, old, new):
            bad.append(f"{n}: fp 与现场树不符")
        if e.get("helper_fp") != M._helper_fp():
            bad.append(f"{n}: helper_fp 与现场不符")
        gate = e.get("gate_fp") or ""
        have = M._gate_cases(e.get("gate") or "")
        if gate.startswith("cases:") and have.startswith("cases:"):
            if not set(gate[6:].split(",")) <= set(have[6:].split(",")):
                bad.append(f"{n}: gate_fp 不是现场子集")
        elif gate != have:
            bad.append(f"{n}: gate_fp 与现场不符（非 cases 口径）")
        merged[n] = e
    if bad:
        print(f"❌ {len(bad)} 条不合格：{bad[:5]}")
        return 2

    meta = {"_note": json.loads(pathlib.Path(a.base).read_text(encoding="utf-8")).get("_meta", {}).get("_note", "")}
    meta["full_audit_at"] = a.head
    # 章对应**哪一棵树**（可审计：commit + tree + 是否脏树放行）。审计 B 第 4 条的根治口径是
    # 「把 tree hash 记进 _meta」，这里落上；将来要核「章是不是这棵树」只看这两个字段。
    meta["full_audit_tree"] = tree
    meta["tree_dirty"] = bool(dirty)
    meta["full_audit_evidence"] = (f"机械核对通过：{len(merged)} 名恰好、现场三指纹一致、"
                                   f"日志 🔴 并集覆盖、对照 {len(controls)} 名全绿。"
                                   f"章对应 tree={tree[:12]}{'（脏树放行 ⚠️）' if dirty else ''}。"
                                   "⚠️ 日志真伪不在本脚本能力内。")
    out = {"_meta": meta, "entries": dict(sorted(merged.items()))}
    text = json.dumps(out, ensure_ascii=False, indent=1) + "\n"
    ats = sorted({v.get("at") for v in merged.values()})
    print(f"✅ {len(merged)} 条通过（现场重算三指纹 + 日志覆盖 + {len(controls)} 名对照全绿）；at 取值：{ats}")
    if a.write:
        pathlib.Path(a.base).write_text(text, encoding="utf-8")
        print(f"已写回 {a.base}")
    else:
        print("（dry-run：加 --write 才落盘）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
