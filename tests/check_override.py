"""对**真实 Hermes 安装**验证平台覆盖是否生效。

它做的事：
  1. 造一个临时 HERMES_HOME，把本仓库软链进 ``plugins/larkdeck``；
  2. 在临时 config.yaml 里启用 larkdeck；
  3. 用 Hermes 自己的插件加载器加载插件，然后问平台注册表：
     ``feishu`` 现在解析到谁的工厂？
  4. 真造一个适配器实例，检查它的类确实是 LarkDeck 子类。

跑法::

    python3 tests/check_override.py

为什么必须真机验证:「注册表最后写入者胜」是**运行时**行为，只有真跑一遍才能证明。
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
sys.path.insert(0, str(INSTALL))

from hermes_cli.plugins import discover_plugins          # noqa: E402
from gateway.platform_registry import platform_registry  # noqa: E402
from gateway.config import PlatformConfig                # noqa: E402

discover_plugins()

entry = platform_registry.get("feishu")
if entry is None:
    print("FAIL: 注册表里没有 feishu 平台")
    raise SystemExit(2)

factory = entry.adapter_factory
module = getattr(factory, "__module__", "") or ""
qualname = getattr(factory, "__qualname__", "") or ""
print(f"resolved factory: {module}.{qualname}")

if "larkdeck" not in module:
    print(f"FAIL: feishu 平台未被 larkdeck 接管（factory 来自 {module!r}）")
    raise SystemExit(3)

adapter = factory(PlatformConfig(enabled=True, extra={}))
cls = type(adapter)
mro_names = [c.__name__ for c in cls.__mro__]
print(f"adapter class  : {cls.__module__}.{cls.__qualname__}")
print(f"MRO            : {' -> '.join(mro_names[:4])}")
print(f"REQUIRES_EDIT_FINALIZE = {getattr(adapter, 'REQUIRES_EDIT_FINALIZE', None)}")

problems = []
if cls.__name__ != "LarkDeckFeishuAdapter":
    problems.append(f"适配器类名不是 LarkDeckFeishuAdapter，而是 {cls.__name__}")
if "LarkDeckMixin" not in mro_names:
    problems.append("MRO 里没有 LarkDeckMixin")
if mro_names.index("LarkDeckMixin") > mro_names.index("FeishuAdapter"):
    problems.append("LarkDeckMixin 的优先级低于 FeishuAdapter，覆盖不会生效")
if getattr(adapter, "REQUIRES_EDIT_FINALIZE", None) is not True:
    problems.append("REQUIRES_EDIT_FINALIZE 不是 True，末帧会另发新消息而不是原地封口")

# 覆盖面必须真的落到我们的实现上 —— 光看 MRO 顺序不够，绑定方法可能在构造期就被取走了。
ld_mixin = next((c for c in cls.__mro__ if c.__name__ == "LarkDeckMixin"), None)
if ld_mixin is None:
    problems.append("找不到 LarkDeckMixin")
else:
    for name in ("send", "edit_message", "send_clarify", "_on_card_action_trigger"):
        own = ld_mixin.__dict__.get(name)
        if own is None:
            problems.append(f"LarkDeckMixin 没有实现 {name}")
            continue
        resolved = getattr(adapter, name, None)
        if getattr(resolved, "__func__", resolved) is not own:
            problems.append(
                f"{name} 解析到的是 {getattr(resolved, '__qualname__', resolved)!r}，不是 larkdeck 的实现"
            )
if not isinstance(getattr(adapter, "_ld_state", None), dict):
    problems.append("_ld_setup() 没跑，实例状态缺失")

if problems:
    for p in problems:
        print("FAIL:", p)
    raise SystemExit(4)

print("OVERRIDE OK")
'''


def main() -> int:
    install = Path(os.environ.get("HERMES_INSTALL_DIR") or DEFAULT_INSTALL)
    if not (install / "gateway" / "platform_registry.py").is_file():
        print(f"找不到 Hermes 安装目录：{install}")
        print("用 HERMES_INSTALL_DIR=<路径> 指定。")
        return 1

    python = install / "venv" / "bin" / "python3"
    if not python.is_file():
        python = Path(sys.executable)

    with tempfile.TemporaryDirectory(prefix="larkdeck-check-") as tmp:
        home = Path(tmp) / "home"
        (home / "plugins").mkdir(parents=True)
        # 软链仓库本身 —— 改代码立即生效，不需要重装。
        (home / "plugins" / "larkdeck").symlink_to(REPO, target_is_directory=True)
        (home / "config.yaml").write_text(
            "plugins:\n  enabled:\n    - larkdeck\n", encoding="utf-8"
        )
        # 让软链里的包能被 import（父目录上 sys.path）。
        env = dict(os.environ)
        env["HERMES_HOME"] = str(home)
        env["PYTHONPATH"] = str(REPO.parent) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [str(python), "-c", _INNER, str(install)],
            env=env, cwd=str(install), capture_output=True, text=True,
        )
        print(proc.stdout.strip())
        if proc.stderr.strip():
            print("--- stderr ---")
            print(proc.stderr.strip()[-3000:])
        if proc.returncode != 0:
            print(f"\n退出码 {proc.returncode}")
            return proc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
