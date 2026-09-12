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
import json
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

    # **纵向切片**：钩子 → 数据层 → 卡片 JSON 一次走通。
    # 前面只验到「数据层里状态对不对」，而用户看到的是**卡片**；中间还隔着适配器的
    # 渲染层（`_ld_panel` → `unified_panel` → `border_for_status`）。分段测过不等于
    # 连起来对 —— 这条断言盯的就是「绿色到底有没有画到卡的边框上」。
    try:
        _adapter_mod = (sys.modules.get("hermes_plugins.larkdeck.core.adapter")
                        or sys.modules["larkdeck.core.adapter"])
        card = _adapter_mod.LarkDeckMixin._ld_build_card(
            "答案正文", streaming=False, panel=_adapter_mod.LarkDeckMixin._ld_panel("", None),
            footer=None)
        blob = json.dumps(card, ensure_ascii=False)
        panel_node = None  # noqa: F841 - 下面重新找，这里只是占位

        def _find(node):
            if isinstance(node, dict):
                if node.get("tag") == "collapsible_panel":
                    return node
                for value in node.values():
                    got = _find(value)
                    if got is not None:
                        return got
            elif isinstance(node, list):
                for item in node:
                    got = _find(item)
                    if got is not None:
                        return got
            return None

        panel_node = _find(card)
        if panel_node is None:
            problems.append("完成的回合：卡片上没有面板（状态色无处可画）")
        elif panel_node.get("border", {}).get("color") != "green":
            problems.append(f"完成的回合：边框不是绿色，而是 {panel_node.get('border')!r}")
        # 注意判的是**卡片级**的 `header` 键（决策 D2 去掉了它）——
        # 不能拿字符串 `"header"` 去搜整份 JSON：折叠面板自己也有一个 `header`（标题区）。
        if "header" in card:
            problems.append("回复卡不该有卡片级 header（决策 D2）")
        print(f"完成态卡片：border={panel_node.get('border') if panel_node else None} "
              f"字节={len(blob.encode('utf-8'))}")
    except Exception as exc:  # pragma: no cover - 防御性
        problems.append(f"纵向切片（钩子→卡片）失败：{exc!r}")

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

    # note_turn 链路：`post_api_request` 必须带 session_id/turn_id 才能真正清掉上一回合的
    # 状态（非流式模式下 `on_stream_start` 完全不触发，这是唯一的新回合信号）。
    # 上面那次派发的载荷**没有** session_id/turn_id，会在回调里直接早退 —— 那正是
    # 「这条链路零覆盖」的原因（2026-09-13 审计的 B5）。
    invoke_hook(
        "post_api_request",
        task_id="t1", turn_id="turn-p9", api_request_id="req9",
        session_id="sess-panelcheck", platform="feishu",
        model="deepseek-v4-flash", provider="deepseek", base_url="https://api.example/v1",
        api_mode="chat_completions", api_call_count=1, api_duration=0.5,
        started_at=0.0, ended_at=0.5, first_chunk_at=None, finish_reason="stop",
        message_count=1, response_model="deepseek-v4-flash",
        response={}, usage=usage, assistant_message=None,
        assistant_content_chars=0, assistant_tool_call_count=0, moa_references=None,
    )
    st = _panel.snapshot()
    if st is not None and st.get("status"):
        problems.append(f"新回合的 post_api_request 没有清掉上一回合的结局状态：{st.get('status')!r}")
    if st is not None:
        problems.append(f"新回合的 post_api_request 没有清空上一回合的面板：{st!r}")

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

    # ------------------------------------------------------------------ #
    # 黄金路径：**一个完整回合**的所有钩子按真实顺序走一遍，最后验卡片
    # ------------------------------------------------------------------ #
    # 分段测过不等于连起来对：前面每段都在验自己那一层，这里验的是**串起来**的结果 ——
    # 轮次有没有被正文/工具正确切断、工具耗时有没有配对上、结局色有没有画到边框上、
    # 面板标题行有没有把「模型 · 轮数 · 工具数 · 耗时」拼出来。
    # （这一节正是「推理连成一个巨大第 1 轮」那类静默退化会被抓住的地方。）
    _panel.reset()
    _SESSION, _TURN = "sess-golden", "golden-1"

    def _stream(kind, delta=""):
        enqueue_plugin_stream_hook(
            "on_stream_delta", delta=delta, kind=kind,
            session_id=_SESSION, turn_id=_TURN,
            model="deepseek-v4-flash", provider="deepseek", surface="feishu")

    enqueue_plugin_stream_hook("on_stream_start", session_id=_SESSION, turn_id=_TURN,
                               model="deepseek-v4-flash", provider="deepseek",
                               surface="feishu")
    _stream("reasoning", "先想")
    _stream("reasoning", "一下")            # 同一轮
    time.sleep(0.02)                        # 让第 1 轮的最低耗时 > 0（下面有断言）
    _stream("text", "这是正文")              # 正文开始 → 切断第 1 轮
    time.sleep(0.02)
    _stream("reasoning", "再看工具")          # 第 2 轮
    time.sleep(0.02)
    invoke_hook("pre_tool_call", tool_name="terminal", args={"command": "ls"},
                session_id=_SESSION, task_id="t1", tool_call_id="g-call-1",
                turn_id=_TURN, api_request_id="greq1", middleware_trace=[])
    invoke_hook("post_tool_call", tool_name="terminal", args={"command": "ls"},
                result="ok", task_id="t1", session_id=_SESSION,
                tool_call_id="g-call-1", turn_id=_TURN, api_request_id="greq1",
                duration_ms=42, status="ok", error_type=None, error_message=None,
                middleware_trace=[])
    # 工具之后**再补一段推理**：这一段必须开成**第 3 轮**。
    # 没有它的话，「工具切断推理轮」这条线路是看不见的 —— 第 2 轮本来就是最后那个
    # 未结束的轮，切与不切在快照里都表现为「2 轮」（实测：把工具那处切轮删掉，
    # 黄金路径照样全绿）。这一段把它变成可判定的。
    _stream("reasoning", "工具之后")
    time.sleep(0.02)
    invoke_hook("on_session_end", session_id=_SESSION, task_id="t1", turn_id=_TURN,
                completed=True, failed=False, interrupted=False,
                turn_exit_reason="text_response(stop)", model="deepseek-v4-flash",
                platform="feishu")

    # ⚠️ 轮次形状依赖**异步** worker：`enqueue_plugin_stream_hook` 每个回调一条队列 + 守护线程，
    # 而 `invoke_hook("pre_tool_call", ...)` 是同步的 —— 若 worker 还没消费掉「再看工具」，
    # 工具那次切轮就切在空轮上，轮的切分会少一段（实测按「工具先到、推理增量迟到」的顺序
    # 直接驱动 panel ⇒ 2 轮）。这里的失败方向是**变红**（不是静默放行），所以按「等队列排空」
    # 处理：超时后如实报「可能是 worker 时序，不一定是退化」，免得有人用放宽断言来「修」它。
    golden = None
    drained = False
    for _ in range(60):
        golden = _panel.snapshot()
        if golden and len(golden.get("rounds") or []) >= 3 and golden.get("status") == "ok":
            drained = True
            break
        time.sleep(0.05)
    print(f"黄金路径 snapshot = {golden}")
    if not golden:
        problems.append("黄金路径：面板快照为空")
    else:
        rounds = golden.get("rounds") or []
        if len(rounds) != 3:
            problems.append(f"黄金路径：期望 3 轮（正文、工具各切断一次 + 工具后再来一段），"
                            f"实得 {len(rounds)}"
                            + ("" if drained else "（等排空超时 —— 可能是异步 worker 时序，"
                                                  "不一定是退化）"))
        elif [r.get("text") for r in rounds] != ["先想一下", "再看工具", "工具之后"]:
            problems.append(f"黄金路径：轮的切分不对 {[r.get('text') for r in rounds]!r}")
        # ⚠️ 原来这里断言的是 `isinstance(elapsed_ms, int)` —— **恒真**：钩子之间没有等待，
        # 每一轮实测都是 0ms，而 0 是 int（第七路审计实测：把 `panel.py` 里
        # `current["elapsed_ms"] = max(0, int(...))` 改成 `= 0`，四个门禁全绿 ⇒
        # 效果 4 的「每轮相对耗时」整块消失、卡片上只剩「第 N 轮」）。
        # 所以上面故意插了 sleep，这里改判 `> 0`。
        notimed = [r for r in rounds if not isinstance(r.get("elapsed_ms"), int)
                   or r["elapsed_ms"] <= 0]
        if notimed:
            problems.append(f"黄金路径：轮缺耗时（必须 > 0，否则效果 4 的「每轮耗时」静默消失）"
                            f"{rounds!r}")
        if not all(isinstance(r.get("elapsed_ms"), int) for r in rounds):
            problems.append(f"黄金路径：耗时的类型不对 {rounds!r}")
        tools = golden.get("tools") or []
        if len(tools) != 1 or tools[0].get("duration_ms") != 42 or tools[0].get("status") != "ok":
            problems.append(f"黄金路径：工具步骤不对 {tools!r}")
        if golden.get("status") != "ok":
            problems.append(f"黄金路径：结局状态不对 {golden.get('status')!r}")

        # —— 最后一步：真渲染成卡片，验用户**看得见**的那部分 ——
        try:
            _adm = (sys.modules.get("hermes_plugins.larkdeck.core.adapter")
                    or sys.modules["larkdeck.core.adapter"])
            node = _adm.LarkDeckMixin._ld_panel("", None)
            if node is None:
                problems.append("黄金路径：渲染不出面板")
            else:
                if node.get("border", {}).get("color") != "green":
                    problems.append(f"黄金路径：边框不是绿色 {node.get('border')!r}")
                title = node.get("header", {}).get("title", {}).get("content", "")
                if "🧠 3" not in title or "🔧 1" not in title:
                    problems.append(f"黄金路径：面板标题行不对（期望含「🧠 3 / 🔧 1」）：{title!r}")
                print(f"黄金路径面板：{title!r}")
        except Exception as exc:  # pragma: no cover - 防御性
            problems.append(f"黄金路径：渲染异常 {exc!r}")

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
