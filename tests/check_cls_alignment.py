#!/usr/bin/env python3
"""P3 交叉门禁：生产图标表 vs CLS `_TOOL_DESCRIPTORS`（**不是** vs 我们自己的冻结契约）。

为什么需要独立一条：`test_v4_18` 与 `check_cardview.py` 比对的是
`docs/audits/v0.7.2/tool-icons.json` —— 它能抓住「生产表被改坏」，但**抓不住**
「我们当初抄错 / 抄的是旧版 CLS」。审计 A 正是这样抓到 `terminal` 那条偏差的：
CLS 的 `Run command` 描述符只收 `exec/bash/command/run`，`terminal` 落 fallback。

这里直接**解析 CLS 源码里的表**（子进程 import，避免污染本进程），把两侧都按 CLS 的规则
归一化（`strip().lower().replace("-", "_")`）后逐条比对，**顺序也钉住**（前缀匹配的歧义
取决于顺序），并断言 `local_extra` 里登记的偏差**确实不在 CLS 表里**。

跑法::

    python3 tests/check_cls_alignment.py
    LARKDECK_CLS_DIR=/path/to/hermes-lark-streaming python3 tests/check_cls_alignment.py

CLS 仓库不在时默认 SKIP（exit 0）；设 ``LARKDECK_REQUIRE_CLS=1`` ⇒ 缺席即失败。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent
_DEFAULT_CLS = pathlib.Path.home() / "Code" / "hermes-lark-streaming"

_INNER = r'''
import json
from hermes_lark_streaming.streaming.tooluse import _TOOL_DESCRIPTORS

out = []
for desc in _TOOL_DESCRIPTORS:
    for alias in desc["aliases"]:
        norm = str(alias).strip().lower().replace("-", "_")
        if not any(norm == name for name, _ in out):
            out.append([norm, desc["icon"]])
print("@@" + json.dumps(out))
'''


def _cls_table(cls_dir: pathlib.Path) -> list:
    proc = subprocess.run([sys.executable, "-c", _INNER], cwd=str(cls_dir),
                          capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"解析 CLS 表失败（rc={proc.returncode}）：{proc.stderr.strip()[-500:]}")
    for line in proc.stdout.splitlines():
        if line.startswith("@@"):
            return json.loads(line[2:])
    raise RuntimeError(f"CLS 侧没有输出表：{proc.stdout.strip()[-300:]}")


def main() -> int:
    cls_dir = pathlib.Path(os.environ.get("LARKDECK_CLS_DIR") or _DEFAULT_CLS)
    if not (cls_dir / "hermes_lark_streaming" / "streaming" / "tooluse.py").is_file():
        msg = f"CLS 仓库不在（{cls_dir}）"
        if os.environ.get("LARKDECK_REQUIRE_CLS"):
            print(f"FAIL  {msg} —— LARKDECK_REQUIRE_CLS=1 时缺席即失败")
            return 1
        print(f"SKIP  {msg}（设 LARKDECK_REQUIRE_CLS=1 可要求必须存在）")
        return 0

    contract = json.loads(
        (_REPO / "docs" / "audits" / "v0.7.2" / "tool-icons.json").read_text(encoding="utf-8"))
    ours = [(str(a).strip().lower().replace("-", "_"), t)
            for a, t in contract["tool_icons"].items()]
    theirs = [(a, t) for a, t in _cls_table(cls_dir)]

    failures = []
    if ours != theirs:
        ours_d, theirs_d = dict(ours), dict(theirs)
        diff = {k: (ours_d.get(k), theirs_d.get(k))
                for k in set(ours_d) | set(theirs_d) if ours_d.get(k) != theirs_d.get(k)}
        order_same = [a for a, _ in ours] == [a for a, _ in theirs]
        failures.append(f"逐条不等：内容差={diff} 顺序相同={order_same}\n"
                        f"  本地={[a for a, _ in ours]}\n  CLS ={[a for a, _ in theirs]}")
    for alias in contract.get("local_extra") or {}:
        if any(alias == a for a, _ in theirs):
            failures.append(f"local_extra 里的 {alias!r} 其实**在** CLS 表里 ⇒ 不该登记成偏差")
    # 前缀歧义自查：一个别名是另一个的前缀时，两者的 token 必须相同（否则顺序就是语义）
    for a1, t1 in ours:
        for a2, t2 in ours:
            if a1 != a2 and a2.startswith(a1 + "_") and t1 != t2:
                failures.append(f"前缀歧义：{a1}→{t1} 是 {a2}→{t2} 的前缀，顺序会改变结果")

    if failures:
        print("CLS 对齐 FAIL")
        for item in failures:
            print("  - " + item)
        return 1
    print(f"CLS ALIGN OK（{len(ours)} 条别名逐条+顺序一致；"
          f"local_extra={contract.get('local_extra')}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
