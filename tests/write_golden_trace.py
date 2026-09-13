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


def main() -> int:
    trace = test_units._golden_trace()
    out = _HERE / "golden_cardkit_trace.json"
    out.write_text(json.dumps(trace, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"已写出 {out}（{out.stat().st_size} 字节）")
    print(f"  帧返回值     : {trace['returns']}")
    print(f"  元素写入序列 : {[(w[0], w[2]) for w in trace['element_writes']]}")
    print(f"  收尾 patch 数: {len(trace['final_patch'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
