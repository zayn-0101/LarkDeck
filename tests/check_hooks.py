"""对**真实 Hermes 安装**验证钩子订阅是否真的接通。

为什么不能只做单测:单测里 ``record_api_call()`` 是我自己调的，它证明不了
「Hermes 核心会在真实回合里调用它」。这个脚本走的是核心真正使用的同一套派发器
(``hermes_cli.lifecycle.invoke_hook``），并且用 Hermes 自己的 ``CanonicalUsage``
造 ``usage`` 载荷 —— 键名照抄核心 ``_usage_summary_for_api_request_hook()``，
不靠猜。

它做的事：
  1. 造临时 HERMES_HOME，软链本仓库，config 里启用 larkdeck；
  2. ``discover_plugins()`` 加载插件（真加载器）；
  3. 七个观测钩子（``post_api_request`` / ``on_stream_start`` / ``on_stream_delta`` /
     ``pre_tool_call`` / ``post_tool_call`` / ``pre_gateway_dispatch`` /
     ``on_session_end``）必须全部被登记；
     清单以 ``compat.OBSERVED_HOOKS`` 为单一事实来源（下面断言两边一致）；
  4. 用真实载荷格式派发它们，核对数据真的流进 larkdeck 的数据层：
     - ``post_api_request`` 经 ``invoke_hook`` → 页脚指标（模型 / 上下文占用）；
     - ``pre/post_tool_call`` 经 ``invoke_hook`` → 面板工具步骤（含耗时 / 状态）；
     - ``on_stream_delta`` 经**核心专用的流式队列**（``agent.plugin_stream_hooks``）
       → 面板推理文本；``kind="reasoning"`` 进面板，``kind="text"``（正文）**进不了
       面板但必须切断推理轮** —— 轮次定义就是「被正文或工具打断」；
     - ``pre_tool_call`` 的返回值必须全是 ``None`` —— 观察者绝不能变成拦截者；
  5. 对照组：不启用插件时 ``has_hook`` 必须全为 False（证明上面的 True 不是因为
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
import time
from pathlib import Path

INSTALL = Path(sys.argv[1])
ENABLED = sys.argv[2] == "on"
sys.path.insert(0, str(INSTALL))

from hermes_cli.plugins import discover_plugins            # noqa: E402
from hermes_cli.lifecycle import has_hook, invoke_hook     # noqa: E402

discover_plugins()

WIRED_HOOKS = ("post_api_request", "on_stream_start", "on_stream_delta",
               "pre_tool_call", "post_tool_call", "pre_gateway_dispatch",
               "on_session_end")
# 「订阅了哪几个钩子」以插件自己的 compat.OBSERVED_HOOKS 为单一事实来源：
# 门禁里写死一份清单，插件加钩子时就会悄悄漏掉验证（对照组里插件没加载，拿不到它）。
if ENABLED:
    _compat = None
    for _name in ("hermes_plugins.larkdeck.core.compat", "larkdeck.core.compat"):
        if _name in sys.modules:
            _compat = sys.modules[_name]
            break
    if _compat is None:
        print("FAIL: 插件已启用却找不到 compat 模块（订阅清单无法核对）")
        raise SystemExit(8)
    if tuple(WIRED_HOOKS) != tuple(_compat.OBSERVED_HOOKS):
        print(f"FAIL: 本脚本的钩子清单与 compat.OBSERVED_HOOKS 不一致："
              f"{WIRED_HOOKS!r} vs {_compat.OBSERVED_HOOKS!r}")
        raise SystemExit(8)
    print(f"OBSERVED_HOOKS = {list(_compat.OBSERVED_HOOKS)}")

wired = {name: has_hook(name) for name in WIRED_HOOKS}
print(f"has_hook = {wired}")

if not ENABLED:
    if any(wired.values()):
        print("FAIL: 对照组里钩子却被登记了，说明这个观测点不可信")
        raise SystemExit(5)
    print("CONTROL OK（未启用插件时钩子为空）")
    raise SystemExit(0)

_missing = [name for name, ok in wired.items() if not ok]
if _missing:
    print(f"FAIL: 启用 larkdeck 后这些钩子仍未被登记: {_missing}")
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

from larkdeck.core import context                           # noqa: E402

# 坑：插件加载器把插件装进 ``hermes_plugins.larkdeck`` 命名空间，而我们用包名
# ``larkdeck`` 又导了一份 —— 两个模块对象各有各的模块级状态。钩子写进 A，
# 从 B 读就永远是空的。这里必须读**加载器那份**。
_loaded = None
for _name in ("hermes_plugins.larkdeck.core.context", "larkdeck.core.context"):
    if _name in sys.modules:
        _loaded = sys.modules[_name]
        break
if _loaded is not None and _loaded is not context:
    print(f"注意: 存在两份 larkdeck.core.context，断言改用加载器那份 {_loaded.__name__}")
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

# --- 面板：工具生命周期（真实 invoke_hook）+ 推理增量（真实流式队列） -------------
from larkdeck.core import panel                             # noqa: E402

_panel = None
for _name in ("hermes_plugins.larkdeck.core.panel", "larkdeck.core.panel"):
    if _name in sys.modules:
        _panel = sys.modules[_name]
        break
if _panel is None:
    problems.append("找不到 larkdeck.core.panel 模块，无法核对面板数据")
else:
    from agent.plugin_stream_hooks import (                 # noqa: E402
        enqueue_plugin_stream_hook, has_stream_observer_hooks,
    )
    if not has_stream_observer_hooks():
        problems.append("has_stream_observer_hooks() 为假：推理订阅没被认到")

    # pre_tool_call 是 fail-closed 钩子：返回列表必须全是 None（观察者不能变成拦截者）
    pre_results = invoke_hook(
        "pre_tool_call",
        tool_name="read_file", args={"path": "/tmp/demo.txt"},
        task_id="t1", session_id="sess-panelcheck", tool_call_id="call-1",
        turn_id="turn-p1", api_request_id="req1", middleware_trace=[],
    )
    if any(r is not None for r in pre_results):
        problems.append(f"pre_tool_call 回调返回值不是 None：{pre_results!r}")

    invoke_hook(
        "post_tool_call",
        tool_name="read_file", args={"path": "/tmp/demo.txt"},
        result="demo content", task_id="t1", session_id="sess-panelcheck",
        tool_call_id="call-1", turn_id="turn-p1", api_request_id="req1",
        duration_ms=42, status="ok", error_type=None, error_message=None,
        middleware_trace=[],
    )

    # on_stream_delta 走核心真正的专用队列（每个回调一个 daemon worker）。
    #
    # ⚠️ 这里必须**同时**覆盖两种 kind 的顺序语义：两种 kind 走的是同一个钩子名 +
    # 同一个回调，而 Hermes 的队列键是 ``(hook_name, id(callback))``
    # （``agent/plugin_stream_hooks.py``）⇒ 共享同一条 FIFO，顺序是硬的。
    # 而「正文切断推理轮」这条关键线路原先**没有任何门禁**：把 ``hooks.py`` 里
    # ``kind == "text"`` 那个分支整条删掉，四个门禁**全部照绿**（2026-09-12 变异测试实证）。
    # 那意味着推理永久连成一个巨大的「第 1 轮」—— 静默退化，用户只会觉得「轮次没用」。
    enqueue_plugin_stream_hook(
        "on_stream_delta", delta="先看目录结构。", kind="reasoning",
        session_id="sess-panelcheck", turn_id="turn-p1",
        model="deepseek-v4-flash", provider="deepseek", surface="feishu",
    )
    enqueue_plugin_stream_hook(
        "on_stream_delta", delta="这是正文，不该进面板。", kind="text",
        session_id="sess-panelcheck", turn_id="turn-p1",
        model="deepseek-v4-flash", provider="deepseek", surface="feishu",
    )
    enqueue_plugin_stream_hook(
        "on_stream_delta", delta="正文之后的推理。", kind="reasoning",
        session_id="sess-panelcheck", turn_id="turn-p1",
        model="deepseek-v4-flash", provider="deepseek", surface="feishu",
    )

    expected_reasoning = "先看目录结构。正文之后的推理。"
    p_snap = None
    for _ in range(60):  # 等 worker 线程消费，最多约 3 秒
        # 等到**最后一条**推理也落账（否则会在只处理完第一条时就跳出，误判轮次）
        p_snap = _panel.snapshot()
        if (p_snap and p_snap["reasoning"] == expected_reasoning
                and not any(t.get("status") == "running" for t in p_snap["tools"])):
            break
        time.sleep(0.05)

    print(f"panel snapshot = {p_snap}")
    if not p_snap:
        problems.append("面板快照为空：推理 / 工具数据没进数据层")
    else:
        if p_snap["reasoning"] != expected_reasoning:
            problems.append(f"推理文本不对（正文混入 / 丢失 / 迟到）：{p_snap['reasoning']!r}")
        rounds = p_snap.get("rounds") or []
        if len(rounds) != 2:
            problems.append(
                f"正文没有切断推理轮：期望 2 轮，实得 {len(rounds)} 轮 {rounds!r} —— "
                "「轮次」这个特性整条失效（且是静默的）")
        elif rounds[0].get("text") != "先看目录结构。" or rounds[1].get("text") != "正文之后的推理。":
            problems.append(f"轮的切分点不对：{[r.get('text') for r in rounds]!r}")
        elif not isinstance(rounds[0].get("elapsed_ms"), int):
            problems.append(f"已结束的轮没有耗时：{rounds[0]!r}")
        steps = p_snap["tools"]
        if not steps:
            problems.append("工具步骤为空：pre/post_tool_call 没进数据层")
        else:
            step = steps[0]
            if step.get("name") != "read_file" or step.get("status") != "ok":
                problems.append(f"工具步骤没配对上：{step!r}")
            if step.get("duration_ms") != 42:
                problems.append(f"工具耗时不对：{step.get('duration_ms')!r}")
            if "demo.txt" not in str(step.get("preview") or ""):
                problems.append(f"参数预览丢了：{step.get('preview')!r}")

    # 回合结局 → 面板状态色。这是「状态色」整条特性的唯一信号，
    # 而且它有一条**极易踩反**的优先级规则：官方 completed 的表达式里没有 interrupted，
    # 所以「被中止但已产出部分正文」的回合会同时 completed=True / interrupted=True。
    # 先看 completed 就会把中止显示成绿色完成 —— 下面这条断言就是钉住它的。
    invoke_hook("on_session_end", session_id="sess-panelcheck", task_id="t1",
                turn_id="turn-p1", completed=True, failed=False, interrupted=False,
                turn_exit_reason="text_response(stop)", model="deepseek-v4-flash",
                platform="feishu")
    st = _panel.snapshot()
    print(f"status after completed=True → {st.get('status') if st else None!r}")
    if not st or st.get("status") != "ok":
        problems.append(f"正常完成没被记成 ok：{st.get('status') if st else None!r}")

    invoke_hook("on_session_end", session_id="sess-panelcheck", task_id="t1",
                turn_id="turn-p1", completed=True, failed=True, interrupted=False,
                turn_exit_reason="api_error", model="deepseek-v4-flash", platform="feishu")
    st = _panel.snapshot()
    if not st or st.get("status") != "error":
        problems.append(f"报错没被记成 error：{st.get('status') if st else None!r}")

    invoke_hook("on_session_end", session_id="sess-panelcheck", task_id="t1",
                turn_id="turn-p1", completed=True, failed=False, interrupted=True,
                turn_exit_reason="interrupted", model="deepseek-v4-flash", platform="feishu")
    st = _panel.snapshot()
    if not st or st.get("status") != "stopped":
        problems.append(
            f"中止没被记成 stopped（优先级必须是 interrupted > failed > completed）："
            f"{st.get('status') if st else None!r}")

    # 新回合边界：on_stream_start 一到，上一回合的推理 / 工具必须先清掉 ——
    # 否则新回合首帧（可能早于本回合第一个事件）会带着旧面板出门。
    enqueue_plugin_stream_hook(
        "on_stream_start",
        session_id="sess-panelcheck", turn_id="turn-p2",
        model="deepseek-v4-flash", provider="deepseek", surface="feishu",
    )
    cleared = False
    for _ in range(60):
        if _panel.snapshot() is None:
            cleared = True
            break
        time.sleep(0.05)
    if not cleared:
        problems.append(f"新回合开始时旧面板没被清掉：{_panel.snapshot()!r}")
    # 状态色也必须随回合一起清：否则新卡片会挂着上一回合的颜色
    st = _panel.snapshot()
    if st is not None and st.get("status"):
        problems.append(f"新回合没有清掉上一回合的结局状态：{st.get('status')!r}")

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
