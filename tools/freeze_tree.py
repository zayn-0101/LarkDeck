#!/usr/bin/env python3
"""生成 v0.7.1 视觉重构的阶段冻结 manifest（C2 强制条件）。

用法：
  python3 tools/freeze_tree.py --stage V0 [--out docs/internal/audits/v0.7.1-visual/freeze-V0.json]

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
import os
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
    "ap_elements": os.environ.get(
        "LARKDECK_AP_ELEMENTS",
        os.path.expanduser("~/.larkdeck-scratch/route-audit/aiduPOP/cardkit/elements.py")),
}


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: str) -> str:
    p = pathlib.Path(path)
    return _sha_bytes(p.read_bytes()) if p.exists() else ""


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, cwd=str(REPO), text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise SystemExit(f"❌ git 命令失败：{' '.join(cmd)}（{exc}）")


def _untracked_digest() -> str:
    """未跟踪文件的确定性摘要（路径 + 内容），让 manifest 钉住工作树而非只钉 index。"""
    digest = hashlib.sha256()
    files = _run(["git", "ls-files", "--others", "--exclude-standard"]).splitlines()
    for rel in sorted(files):
        path = REPO / rel
        if not path.is_file():
            continue
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, help="阶段名，如 V0 / V1")
    ap.add_argument("--out", default="", help="输出 JSON 路径")
    ap.add_argument("--gate-log", action="append", default=[],
                    help="门禁日志路径（可重复）；记录 sha256")
    args = ap.parse_args()

    plan_bytes = PLAN.read_bytes()
    missing = [name for name, path in REFS.items() if not pathlib.Path(path).exists()]
    if missing:
        print(f"❌ 参考源缺失，拒绝生成 manifest：{missing}")
        return 2
    try:
        inside = _run(["git", "rev-parse", "--is-inside-work-tree"])
    except SystemExit:
        print("❌ 当前目录不是 git 仓库；freeze_tree 必须在真实仓库运行")
        return 2
    if inside != "true":
        print("❌ 当前目录不是 git 仓库；freeze_tree 必须在真实仓库运行")
        return 2
    tree = _run(["git", "write-tree"])
    worktree_commit = _run(["git", "stash", "create"]) or "clean"
    out = pathlib.Path(args.out) if args.out else (
        REPO / "docs" / "audits" / "v0.7.1-visual" / f"freeze-{args.stage}.json")
    try:
        out_rel = str(out.resolve().relative_to(REPO.resolve()))
    except ValueError:
        out_rel = ""
    ignore_fragments = ["docs/internal/audits/v0.7.1-visual/freeze-"] + [
        str(pathlib.Path(p)) for p in args.gate_log]
    if out_rel:
        ignore_fragments.append(out_rel)
    status_lines = [
        line for line in _run(["git", "status", "--porcelain"]).splitlines()
        if not any(fragment in line for fragment in ignore_fragments)
    ]
    manifest = {
        "stage": args.stage,
        "plan_path": str(PLAN.relative_to(REPO)),
        "plan_sha256": _sha_bytes(plan_bytes),
        "plan_bytes": len(plan_bytes),
        "head": _run(["git", "rev-parse", "HEAD"]),
        "head_tree": _run(["git", "rev-parse", "HEAD^{tree}"]),
        "tree": tree,
        "worktree_commit": worktree_commit,
        "branch": _run(["git", "branch", "--show-current"]),
        "git_status": "\n".join(status_lines),
        "staged_diff_sha256": _sha_bytes(
            subprocess.check_output(["git", "diff", "--cached", "--binary", "HEAD"], cwd=str(REPO))),
        "unstaged_diff_sha256": _sha_bytes(
            subprocess.check_output(["git", "diff", "--binary"], cwd=str(REPO))),
        "untracked_sha256": _untracked_digest(),
        "audit_object": "index tree + staged diff + unstaged diff + untracked files + worktree_commit",
        "python": f"{sys.executable} {sys.version.split()[0]}",
        "hermes_version": "0.21.1",
        "references": {name: _sha_file(path) for name, path in REFS.items()},
        "gate_logs": {str(pathlib.Path(p)): _sha_file(p) for p in args.gate_log},
        "auditors": ["A 架构/源码证据", "B 用户可见/回归", "C 执行/反假绿"],
    }
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in
                      ("stage", "plan_sha256", "head", "head_tree", "tree", "worktree_commit",
                       "branch", "staged_diff_sha256", "unstaged_diff_sha256", "untracked_sha256")},
                     ensure_ascii=False))
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
