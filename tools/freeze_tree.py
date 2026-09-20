#!/usr/bin/env python3
"""生成 v0.7.1 视觉重构的阶段冻结 manifest（C2 强制条件）。

用法：
  python3 tools/freeze_tree.py --stage V0 [--out docs/audits/v0.7.1-visual/freeze-V0.json]

记录：
  plan sha256 / HEAD / tree / branch / git status / dirty diff sha /
  解释器版本 / Hermes 版本 / 参考源码关键文件 sha / 审计员列表。

审计对象 = manifest 中 tree + diff 哈希的并集；任一文件变化 ⇒ 审计作废重跑。
本脚本只读仓库与参考目录，不修改源码。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
PLAN = REPO / "docs" / "plan-v0.7.1-visual.md"

REFS = {
    "cls_builder": "/private/tmp/ref-cls/hermes_lark_streaming/cardkit/builder.py",
    "cls_segment_helper": "/private/tmp/ref-cls/hermes_lark_streaming/streaming/segment_helper.py",
    "cls_tooluse": "/private/tmp/ref-cls/hermes_lark_streaming/streaming/tooluse.py",
    "fc_builder": "/private/tmp/ref-fc/hermes_fry_cards/cardkit/builder.py",
    "ap_elements": "/Users/Zayn/.larkdeck-scratch/route-audit/aiduPOP/cardkit/elements.py",
}


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: str) -> str:
    p = pathlib.Path(path)
    return _sha_bytes(p.read_bytes()) if p.exists() else ""


def _run(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, cwd=str(REPO), text=True).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, help="阶段名，如 V0 / V1")
    ap.add_argument("--out", default="", help="输出 JSON 路径")
    args = ap.parse_args()

    plan_bytes = PLAN.read_bytes()
    manifest = {
        "stage": args.stage,
        "plan_path": str(PLAN.relative_to(REPO)),
        "plan_sha256": _sha_bytes(plan_bytes),
        "plan_bytes": len(plan_bytes),
        "head": _run(["git", "rev-parse", "HEAD"]),
        "tree": _run(["git", "rev-parse", "HEAD^{tree}"]),
        "branch": _run(["git", "branch", "--show-current"]),
        "git_status": _run(["git", "status", "--porcelain"]),
        "diff_sha256": _sha_bytes(
            subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=str(REPO))),
        "python": f"{sys.executable} {sys.version.split()[0]}",
        "hermes_version": "0.21.1",
        "references": {name: _sha_file(path) for name, path in REFS.items()},
        "auditors": ["A 架构/源码证据", "B 用户可见/回归", "C 执行/反假绿"],
    }
    out = pathlib.Path(args.out) if args.out else (
        REPO / "docs" / "audits" / "v0.7.1-visual" / f"freeze-{args.stage}.json")
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in
                      ("stage", "plan_sha256", "head", "tree", "branch", "diff_sha256")},
                     ensure_ascii=False))
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
