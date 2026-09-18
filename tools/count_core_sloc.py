#!/usr/bin/env python3
"""冻结口径的 core/ SLOC 计数脚本（v0.7.0 P0.5）。

口径固定，禁止在执行中途按结果改口径：
  * total   = sum(len(path.read_text().splitlines()))
  * blank   = 行 strip() 后为空
  * comment = 行 lstrip() 以 '#' 开头（因此三引号 docstring 行不算 comment）
  * doc     = ast 中 Module/ClassDef/FunctionDef/AsyncFunctionDef 的首个 body
               节点为 Expr(Constant(str)) 时，该节点 lineno..end_lineno 覆盖的
               全部物理行（含三引号行）
  * SLOC    = total - blank - comment - doc
只统计 core/*.py（不递归 tests/docs）。输出 JSON 到 stdout；--write 可落盘。
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "core"


def _doc_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    nodes: list[ast.AST] = [tree]
    nodes.extend(node for node in ast.walk(tree) if isinstance(
        node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)))
    for node in nodes:
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr):
            continue
        value = getattr(first, "value", None)
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        start = getattr(first, "lineno", None)
        end = getattr(first, "end_lineno", None)
        if isinstance(start, int) and isinstance(end, int):
            lines.update(range(start, end + 1))
    return lines


def count_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    total = len(lines)
    blank = sum(1 for line in lines if not line.strip())
    comment = sum(1 for line in lines if line.lstrip().startswith("#"))
    tree = ast.parse(text)
    doc = len(_doc_lines(tree))
    return {
        "total": total,
        "blank": blank,
        "comment_only": comment,
        "docstring": doc,
        "sloc": total - blank - comment - doc,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core", type=Path, default=CORE)
    parser.add_argument("--write", type=Path, default=None)
    args = parser.parse_args()
    files = sorted(args.core.glob("*.py"))
    if not files:
        raise SystemExit(f"no core/*.py under {args.core}")
    per_file = {path.name: count_file(path) for path in files}
    totals = {key: sum(item[key] for item in per_file.values())
              for key in ("total", "blank", "comment_only", "docstring", "sloc")}
    payload = {"core_dir": str(args.core), "files": per_file, "totals": totals}
    blob = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(blob + "\n", encoding="utf-8")
    else:
        print(blob)
    return 0


if __name__ == "__main__":
    sys.exit(main())
