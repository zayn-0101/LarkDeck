#!/usr/bin/env python3
"""LarkDeck 开发期快速验证入口（替代每次跑全量/长时间变异）。

默认并行跑三件最快、判别力最高的门禁：
  1. tests/test_units.py
  2. tests/check_own_body.py
  3. tests/mutate_check.py --preflight

用法：
  python3 tests/run_fast.py            # 默认快速三件，并行，目标 ≤30s
  python3 tests/run_fast.py --full     # 再串行跑 check_override / check_hooks / check_clarify_e2e
  python3 tests/run_fast.py --serial   # 默认三件也串行（排查时用）

只做进程级调度 + 计时，不修改任何源码；任何一步非 0 即整体非 0。
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_PY = sys.executable

FAST = [
    ("test_units", [_PY, str(_HERE / "test_units.py")], 120.0),
    ("check_own_body", [_PY, str(_HERE / "check_own_body.py")], 60.0),
    ("mutate_preflight", [_PY, str(_HERE / "mutate_check.py"), "--preflight"], 120.0),
]
FULL = [
    ("check_override", [_PY, str(_HERE / "check_override.py")], 120.0),
    ("check_hooks", [_PY, str(_HERE / "check_hooks.py")], 180.0),
    ("check_clarify_e2e", [_PY, str(_HERE / "check_clarify_e2e.py")], 120.0),
]


def _run_one(name: str, cmd: list[str], timeout: float) -> dict:
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=str(_REPO), stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=timeout)
        code = proc.returncode
        out = proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        code = 124
        out = (exc.stdout or "") + f"\n[TIMEOUT] {name} 超过 {timeout:.0f}s\n"
    elapsed = time.monotonic() - start
    tail = "\n".join(out.strip().splitlines()[-3:])
    return {"name": name, "code": code, "elapsed": elapsed, "tail": tail}


def _report(results: list[dict]) -> int:
    print("\n=== run_fast 结果 ===")
    failed = 0
    for item in sorted(results, key=lambda r: r["name"]):
        flag = "OK" if item["code"] == 0 else "FAIL"
        if item["code"] != 0:
            failed += 1
        print(f"[{flag:4}] {item['name']:<20} {item['elapsed']:6.2f}s  {item['tail']}")
    total = sum(r["elapsed"] for r in results)
    print(f"合计（各步耗时之和）: {total:.2f}s")
    if total > 30.0:
        print("⚠️ 超过 30s 预算：检查是否有测试在固定 sleep / 网络重试。")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="再跑 override/hooks/clarify_e2e")
    ap.add_argument("--serial", action="store_true", help="默认三件也串行")
    args = ap.parse_args()

    results: list[dict] = []
    if args.serial:
        for name, cmd, timeout in FAST:
            results.append(_run_one(name, cmd, timeout))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(FAST)) as pool:
            futures = [pool.submit(_run_one, name, cmd, timeout)
                       for name, cmd, timeout in FAST]
            for fut in concurrent.futures.as_completed(futures):
                results.append(fut.result())
    if args.full:
        for name, cmd, timeout in FULL:
            results.append(_run_one(name, cmd, timeout))
    return _report(results)


if __name__ == "__main__":
    sys.exit(main())
