"""重新生成 `tests/golden_cardkit_trace.json`（golden trace 夹具）。

```bash
/Users/Zayn/.hermes/hermes-agent/venv/bin/python3 tests/write_golden_trace.py
```

**什么时候该跑它**：只有当你**有意**改变 CardKit 写入路径的对外行为时（例如把正文挪到最后写）。
跑完 `git diff tests/golden_cardkit_trace.json` —— **那份 diff 就是「这次改了什么行为」的声明**，
请把它写进 commit message。无意改行为却需要重跑 ⇒ 那不是「夹具过期」，是**行为漂移**，别用重跑掩盖。

为什么需要这个脚本（R1 审计 D8）：夹具的 docstring 原本写着「生成方式见 `_write_golden_trace()`」，
而那个函数和 `--write-golden` 参数**在整个仓库里都不存在** —— 下一个人只能手改 JSON，
而手改恰好摧毁了「夹具 diff 即行为声明」这个设计意图。
"""
from __future__ import annotations

import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import test_units  # noqa: E402  （它自己会把仓库父目录加进 sys.path）


def _first_diff(got, want, path: str = "") -> str:
    """返回第一处差异的可读描述（相同则空串）。给 `--check` 用。"""
    if type(got) is not type(want):
        return f"{path}: 类型不同 {type(got).__name__} vs {type(want).__name__}"
    if isinstance(got, dict):
        for key in sorted(set(got) | set(want)):
            if key not in got or key not in want:
                return f"{path}.{key}: 只出现在一侧"
            found = _first_diff(got[key], want[key], f"{path}.{key}")
            if found:
                return found
    elif isinstance(got, list):
        if len(got) != len(want):
            return f"{path}: 长度不同 {len(got)} vs {len(want)}"
        for index, (left, right) in enumerate(zip(got, want)):
            found = _first_diff(left, right, f"{path}[{index}]")
            if found:
                return found
    elif got != want:
        return f"{path}: {str(got)[:120]!r} vs {str(want)[:120]!r}"
    return ""


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 与测试同源：先复位到默认配置（否则记下来的是「当前环境」，换个前序用例就假红）
    _adapter = test_units.adapter
    _saved = dict(_adapter._CONFIG)
    _adapter._CONFIG.clear()
    _adapter._CONFIG.update(_adapter._DEFAULTS)
    try:
        trace = test_units._golden_trace()
    finally:
        _adapter._CONFIG.clear()
        _adapter._CONFIG.update(_saved)
    out = _HERE / "golden_cardkit_trace.json"
    if "--check" in argv:
        # 在**独立进程**里比对：夹具不受前序用例的全局残留影响（2026-09-21 实测：同一段场景
        # 在「单跑」与「全套件跑」下会得到不同的卡，根因是类方法/模块级打桩残留）。
        if not out.exists():
            print(f"缺少夹具：{out}")
            return 2
        diff = _first_diff(trace, json.loads(out.read_text(encoding="utf-8")))
        if diff:
            print(f"夹具不一致：{diff}")
            return 1
        print("夹具一致 OK")
        return 0
    out.write_text(json.dumps(trace, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"已写出 {out}（{out.stat().st_size} 字节）")
    print(f"  帧返回值     : {trace['returns']}")
    print(f"  元素写入序列 : {[(w[0], w[2]) for w in trace['element_writes']]}")
    print(f"  收尾 patch 数: {len(trace['final_patch'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
