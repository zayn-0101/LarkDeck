"""对**真实 Hermes 安装**验证钩子订阅是否真的接通。

为什么不能只做单测:单测里 ``record_api_call()`` 是我自己调的，它证明不了
「Hermes 核心会在真实回合里调用它」。这个脚本走的是核心真正使用的同一套派发器
(``hermes_cli.lifecycle.invoke_hook``），并且用 Hermes 自己的 ``CanonicalUsage``
造 ``usage`` 载荷 —— 键名照抄核心 ``_usage_summary_for_api_request_hook()``，
不靠猜。

它做的事：
  1. 造临时 HERMES_HOME，软链本仓库，config 里启用 larkdeck；
  2. ``discover_plugins()`` 加载插件（真加载器）；
  3. ``has_hook("post_api_request")`` 必须为 True —— 证明钩子被登记了；
  4. 用真实载荷格式 invoke 一次，然后读 ``larkdeck.context.snapshot()``，
     核对模型名、上下文占用（含缓存命中）、上限与百分比；
  5. 对照组：不启用插件时 ``has_hook`` 必须是 False（证明上面的 True 不是因为
     内置了什么默认订阅）。

跑法::

    python3 tests/check_hooks.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_INSTALL = Path.home() / ".hermes" / "hermes-agent"

_INNER = r'''
import sys
from pathlib import Path

INSTALL = Path(sys.argv[1])
ENABLED = sys.argv[2] == "on"
sys.path.insert(0, str(INSTALL))

from hermes_cli.plugins import discover_plugins            # noqa: E402
from hermes_cli.lifecycle import has_hook, invoke_hook     # noqa: E402

discover_plugins()

wired = has_hook("post_api_request")
print(f"has_hook(post_api_request) = {wired}")

if not ENABLED:
    if wired:
        print("FAIL: 对照组里钩子却被登记了，说明这个观测点不可信")
        raise SystemExit(5)
    print("CONTROL OK（未启用插件时钩子为空）")
    raise SystemExit(0)

if not wired:
    print("FAIL: 启用 larkdeck 后 post_api_request 仍未被登记")
    raise SystemExit(6)

# --- 用核心同款方式造 usage 载荷 -------------------------------------------------
from dataclasses import asdict                              # noqa: E402
from agent.usage_pricing import CanonicalUsage              # noqa: E402

cu = CanonicalUsage(input_tokens=800, output_tokens=120, cache_read_tokens=44000)
usage = asdict(cu)
usage.pop("raw_usage", None)
usage["prompt_tokens"] = cu.prompt_tokens
usage["total_tokens"] = cu.total_tokens
print(f"payload usage keys = {sorted(usage)}")
print(f"prompt_tokens = {usage['prompt_tokens']} (= input 800 + cache_read 44000)")

invoke_hook(
    "post_api_request",
    task_id="t1", turn_id="turn1", api_request_id="req1",
    session_id="sess-hookcheck", platform="feishu",
    model="deepseek-v4-flash", provider="deepseek", base_url="https://api.example/v1",
    api_mode="chat_completions", api_call_count=2, api_duration=1.5,
    started_at=0.0, ended_at=1.5, first_chunk_at=None, finish_reason="stop",
    message_count=12, response_model="deepseek-v4-flash",
    response={}, usage=usage, assistant_message=None,
    assistant_content_chars=42, assistant_tool_call_count=0, moa_references=None,
    telemetry_schema_version=1,
)

from larkdeck import context                               # noqa: E402

# 坑：插件加载器把插件装进 ``hermes_plugins.larkdeck`` 命名空间，而我们用包名
# ``larkdeck`` 又导了一份 —— 两个模块对象各有各的模块级状态。钩子写进 A，
# 从 B 读就永远是空的。这里必须读**加载器那份**。
_loaded = None
for _name in ("hermes_plugins.larkdeck.context", "larkdeck.context"):
    if _name in sys.modules:
        _loaded = sys.modules[_name]
        break
if _loaded is not None and _loaded is not context:
    print(f"注意: 存在两份 larkdeck.context，断言改用加载器那份 {_loaded.__name__}")
    context = _loaded

snap = context.snapshot()
print(f"snapshot = model:{snap['model']} used:{snap['input_tokens']} "
      f"out:{snap['output_tokens']} cache:{snap['cache_read_tokens']}")

problems = []
if snap["model"] != "deepseek-v4-flash":
    problems.append(f"模型名没接住：{snap['model']!r}")
if snap["input_tokens"] != 44800:
    problems.append(f"上下文占用应为 44800（含缓存命中），实际 {snap['input_tokens']!r}")
if snap["output_tokens"] != 120:
    problems.append(f"输出 token 不对：{snap['output_tokens']!r}")
if snap["cache_read_tokens"] != 44000:
    problems.append(f"缓存命中没记下来：{snap['cache_read_tokens']!r}")

# 上限未钉住时允许探测不到，但钉住后百分比必须算得出来
context.set_context_override(200000)
snap2 = context.snapshot()
if snap2["context_max"] != 200000 or abs((snap2["context_pct"] or 0) - 22.4) > 0.05:
    problems.append(f"钉住上限后百分比不对：{snap2['context_max']} / {snap2['context_pct']}")

# 钩子抛异常不得影响调用方：塞一个坏载荷进去
invoke_hook("post_api_request", usage="不是 dict", model=None)
if context.snapshot()["input_tokens"] != 44800:
    problems.append("坏载荷把好数据冲掉了")

if problems:
    for p in problems:
        print("FAIL:", p)
    raise SystemExit(7)

print("HOOKS OK")
'''


def _run(install: Path, python: Path, enabled: bool) -> tuple[int, str]:
    with tempfile.TemporaryDirectory(prefix="larkdeck-hooks-") as tmp:
        home = Path(tmp) / "home"
        (home / "plugins").mkdir(parents=True)
        (home / "plugins" / "larkdeck").symlink_to(REPO, target_is_directory=True)
        enabled_yaml = "    - larkdeck\n" if enabled else ""
        (home / "config.yaml").write_text(
            f"plugins:\n  enabled:\n{enabled_yaml}", encoding="utf-8"
        )
        env = dict(os.environ)
        env["HERMES_HOME"] = str(home)
        env["PYTHONPATH"] = str(REPO.parent) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [str(python), "-c", _INNER, str(install), "on" if enabled else "off"],
            env=env, cwd=str(install), capture_output=True, text=True,
        )
        out = proc.stdout.strip()
        if proc.stderr.strip():
            out += "\n--- stderr ---\n" + proc.stderr.strip()[-3000:]
        return proc.returncode, out


def main() -> int:
    install = Path(os.environ.get("HERMES_INSTALL_DIR") or DEFAULT_INSTALL)
    if not (install / "hermes_cli" / "lifecycle.py").is_file():
        print(f"找不到 Hermes 安装目录：{install}")
        return 1
    python = install / "venv" / "bin" / "python3"
    if not python.is_file():
        python = Path(sys.executable)

    for enabled in (False, True):
        rc, out = _run(install, python, enabled)
        print(f"===== {'启用 larkdeck' if enabled else '对照组（不启用）'} =====")
        print(out)
        if rc != 0:
            return rc
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
