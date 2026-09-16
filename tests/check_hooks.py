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

    # ------------------------------------------------------------------ #
    # R11-A7：正文净化要的两件事实**必须真的由钩子写进去**
    # ------------------------------------------------------------------ #
    # 为什么单列一格：正文净化的判据是「帧文本 == 我们的正文累积 + 分隔符 + 尾巴」，
    # 而**正文累积来自 `on_stream_delta(delta=...)`** —— 上面那条 `_stream("text", "这是正文")`
    # 正是它，且它排在一个 `pre_tool_call` 之前 ⇒ 工具窗口应当是**开着的**。
    # 这一格盯的是「钩子有没有把文本/工具事件真的送进正文仓库」：`hooks.py` 里少传一个
    # `delta`、或 `record_tool_started` 少调一次 `note_tool_event`，四门禁会全绿而真机上
    # **永远不剥**（症状 = 用户报的那个「正文里混进工具进度行」原样复现）。
    _answer_seen = ""
    # ⚠️ 归属必须**确定性**地钉在目标会话上（2026-09-16 对抗审计 M1）：`answer_state()` 的第一个
    # 形参是 `chat_id`，传一个**没绑定**的字符串时它会退回进程级「最近活跃」指针
    # （`panel._answer_session_for`）—— 那样读到的其实是「上一个写过正文的会话」，
    # 于是「按会话分桶」这条修复整个被拆掉时，这些断言**照样绿**（审计实测：把
    # `_answer_bucket_locked` 的 `sid` 换成常量，本门禁仍 `HOOKS OK`/exit 0）。
    # 真机上这条绑定来自 `pre_gateway_dispatch`，所以这里也走同一条路。
    _GOLDEN_CHAT = "chat-golden"
    _panel.bind_chat_session(_GOLDEN_CHAT, _SESSION)
    for _ in range(60):                     # 等 worker 消费（正文增量走的是异步队列）
        _answer_seen, _armed, _complete = _panel.answer_state(_GOLDEN_CHAT)
        if _answer_seen == "这是正文" and _armed:
            break
        time.sleep(0.05)
    print(f"answer_state = {(_answer_seen, _armed, _complete)}")
    if _panel._answer_session_for(_GOLDEN_CHAT) != _SESSION:
        problems.append(
            f"R11-A7 那一格的归属没落到 {_SESSION}："
            f"{_panel._answer_session_for(_GOLDEN_CHAT)!r}（读到的是别的会话，断言会假绿）")
    if _answer_seen != "这是正文":
        problems.append(
            f"正文累积没入账（`on_stream_delta` 的 delta 没传进数据层）：{_answer_seen!r}")
    if not _armed:
        problems.append("工具窗口没开（`pre_tool_call` 没打开它 ⇒ 真机上永远不剥进度块）")
    if not _complete:
        problems.append("正文累积被误判成「不完整」⇒ 判据会整体退回「不剥」")
    # 再来一条正文增量 ⇒ 窗口必须**关上**（核心也在这一刻清掉它的进度行）
    _stream("text", "，还没写完")
    for _ in range(60):
        _answer_seen2, _armed2, _ = _panel.answer_state(_GOLDEN_CHAT)
        if _answer_seen2 == "这是正文，还没写完" and not _armed2:
            break
        time.sleep(0.05)
    print(f"answer_state（正文增量之后） = {(_answer_seen2, _armed2)}")
    if _answer_seen2 != "这是正文，还没写完":
        problems.append(f"正文增量没有追加（拼接断链 ⇒ 前缀判据永远不成立）：{_answer_seen2!r}")
    if _armed2:
        problems.append("新的正文增量没有关闭工具窗口（核心此刻已经清掉了进度行）")

    # ------------------------------------------------------------------ #
    # 前提核对：核心给「正文钩子」与给「显示回调」的文本必须**逐字节同源**
    # ------------------------------------------------------------------ #
    # 为什么单列一格（2026-09-16 第四轮审计留下的残留）：`_strip_core_progress` 的判据是
    # 「帧文本 == 我们的正文累积 + 分隔符 + 尾巴」，而它**依赖一个外部前提** ——
    # 我们的累积与核心的 `_accumulated` 逐字节同源。此前那句话只有一段引用核心行号的论证，
    # **一条断言都没有**：核心哪天给钩子投另一个文本（或改掉累积 / 合成的形状），
    # 就可能在**切卡接缝**上静默吞正文，而四门禁全绿。
    #
    # 这一格**驱动核心自己的投递链路**，不替它接线：
    #   `StreamDeliveryMixin._fire_stream_delta`
    #     → `stream_delta_callback`（核心 docstring 写明的接线就是 `consumer.on_delta`）
    #     → `_drain_queue` → `_filter_and_accumulate` → `_append_accumulated`
    #   而**同一行代码**把**同一个 text** 交给 `_enqueue_stream_hook`（我们收到的那一份）。
    # 再拿核心真实的 `_compose_frame_content()` 合成一帧，喂给我们真实的 `_strip_core_progress`。
    #
    # ⚠️ **桩掉三处**（2026-09-16 对抗审计低-1 纠正过措辞：以前写「两处」，而宿主类其实盖了
    #    四个方法），每一处都写在这里，并说明它为什么动不了结论：
    #    ① **异步 `run()` 循环**（它做的是传输 I/O，不是累积）⇒ 改由我们同步泵一次队列；
    #    ② **工具轮边界那一个布尔位**（核心自己在 `agent/turn_tool_round.py:182` /
    #       `agent/conversation_loop.py:312` 置位）—— 它换来的是核心自己插的那个 `"\n\n"`，
    #       也就是「两边只要有一边多改一个字节就错位」的地方；
    #    ③ **宿主侧的 `_strip_think_blocks`（identity）** —— 核心的真实现
    #       （`agent/agent_runtime_helpers.py` 的 `strip_think_blocks`）确实会改写文本，
    #       但 `stream_delivery.py` 是**洗完之后**才把**同一个局部 `text`** 既给回调又给钩子
    #       ⇒ 换成 identity 只改输入形状，不改变「两边同源」这个结论。
    #       （代价如实说：这一格的输入因此进不了 `_filter_and_accumulate` 的 think 分支。）
    # ⚠️ **上游若改了这些形状，这一格要「红 + 打印原因」，不是「崩掉半张门禁」**
    #    （2026-09-16 对抗审计中-2 实测：改名会让裸调用抛 `AttributeError`、只打 traceback
    #    而没有 FAIL 行，并且**这一格之后的断言全部不再执行**）⇒ 整段驱动套一层 try/except，
    #    退化成一条可读的 FAIL。**它必须红**：这一格守的是外部前提，前提没了就得有人看一眼
    #    （`docs/lessons.md` 推论 8：探测失效 ≠ 契约齐全）。
    _PSESSION, _PTURN = "sess-premise", "premise-1"
    _PCHAT = "chat-premise"
    try:
        import queue as _qmod                                   # noqa: E402
        from agent.stream_delivery import StreamDeliveryMixin    # noqa: E402
        from gateway.stream_consumer import GatewayStreamConsumer   # noqa: E402
    except Exception as _exc:
        problems.append(f"无法核对「正文同源」前提：核心的投递/累积实现取不到（{_exc!r}）")
    else:
        try:
            class _DeliveryHost(StreamDeliveryMixin):
                """只补 `_fire_stream_delta` 会读到的几个格子（见上面三处桩的说明）。"""

                def __init__(self) -> None:
                    self.stream_delta_callback = None
                    self._stream_callback = None
                    self._stream_needs_break = False
                    self._streamed_assistant_text_parts = []
                    self.session_id = _PSESSION
                    self.model = "deepseek-v4-flash"
                    self.provider = "deepseek"
                    self.platform = "feishu"
                    self._current_turn_id = _PTURN
                    self._api_call_count = 1

                def _stream_writer_superseded(self) -> bool:
                    return False

                def _note_dropped_stream_writer(self, where: str) -> None:
                    pass

                def _strip_think_blocks(self, text: str) -> str:
                    return text

            # 不跑 `GatewayStreamConsumer.__init__`：那要 cfg / 传输 / 平台适配器；
            # 这里只补 `_drain_queue` / `_filter_and_accumulate` / `_append_accumulated` 会碰的格子。
            _consumer = object.__new__(GatewayStreamConsumer)
            _consumer._queue = _qmod.Queue()
            _consumer._accumulated = _consumer._stream_ledger = ""
            _consumer._tool_progress_lines = []
            _consumer._tool_progress_active = False
            _consumer._in_think_block = False
            _consumer._think_buffer = ""
            _consumer._use_native_streaming = True   # 工具进度覆盖层只在 native 下存在

            _host = _DeliveryHost()
            _host.stream_delta_callback = _consumer.on_delta  # ← `gateway/stream_consumer.py:101` 的原话

            _chunks = ["第一段正文。", "第二段。"]
            for _chunk in _chunks:
                _host._fire_stream_delta(_chunk)
            _host._stream_needs_break = True               # 工具轮边界（核心自己置位的那个布尔位）
            _chunks.append("工具之后的正文。")
            _host._fire_stream_delta(_chunks[-1])
            _consumer._drain_queue()                       # 真实链路：on_delta → 累积
            _core_acc = _consumer._accumulated

            # 自证（本项目对「判据不许恒真」的要求）：这一组输入必须**真的**把核心自己插的那个
            # 换行带进来 —— 否则「两边逐字节相同」对一段毫无变形的文本是**弱**的（少一个字节也看不出）。
            if "\n\n" not in _core_acc:
                problems.append(
                    "前提核对的构造无效：核心没有插工具轮边界那个换行 "
                    f"（{_core_acc!r}）⇒ 这一格退化成「拼字符串也对得上」")
            # ⚠️ 归属必须**确定性**地钉住（2026-09-16 对抗审计中-1）：`answer_state()` 的形参是
            # `chat_id`，传空串会退回进程级「最近活跃」指针 —— 那样「按会话分桶」整条被拆掉时
            # 这一格照样绿（审计实测：`_answer_bucket_locked` 的 `sid` 换成常量 ⇒ 仍 `HOOKS OK`）。
            # 真机上这条绑定来自 `pre_gateway_dispatch`，这里走同一条路。
            _panel.bind_chat_session(_PCHAT, _PSESSION)
            _our_acc = ""
            for _ in range(60):
                _our_acc, _, _ = _panel.answer_state(_PCHAT)
                if _our_acc == _core_acc:
                    break
                time.sleep(0.05)
            print(f"前提核对：核心累积 {_core_acc!r}")
            print(f"          我们钩子收到的累积 {_our_acc!r}")
            if _panel._answer_session_for(_PCHAT) != _PSESSION:
                problems.append(
                    f"前提核对的归属没落到 {_PSESSION}："
                    f"{_panel._answer_session_for(_PCHAT)!r} ⇒ 比的不是目标会话（断言会假绿）")
            if _our_acc != _core_acc:
                problems.append(
                    "核心给正文钩子的文本与给显示回调的文本**不同源**："
                    f"核心 {_core_acc!r} vs 我们 {_our_acc!r} ⇒ "
                    "`_strip_core_progress` 的前缀判据不再成立（可能在切卡接缝上静默吞正文）")

            # 形状前提：核心合成的帧必须**就是**「我们的累积 + 分隔符 + 逐行进度行」，且剥得回来。
            # ⚠️ 断言用**整条相等**（审计低-2：以前只 `startswith(累积 + 分隔符)`，尾巴一个字节
            # 都没钉住 —— 往分隔符后面拼任意垃圾照样通过，而那正是这一格宣称要守的「形状」）。
            _consumer._tool_progress_lines = ['⚙️ terminal: "ls -la"']
            _frame = _consumer._compose_frame_content()
            _expect = _our_acc + "\n\n---\n" + "\n".join(_consumer._tool_progress_lines)
            _stripped = _adapter_mod._strip_core_progress(_frame, _our_acc, True, True, finalize=False)
            print(f"前提核对：核心合成的帧（真实实现） = {_frame!r}")
            if _frame != _expect:
                problems.append(
                    f"核心合成的帧不等于「我们的累积 + 分隔符 + 进度行」：{_frame!r} != {_expect!r}")
            elif _frame == _our_acc:
                problems.append("前提核对的构造无效：合成帧没有尾巴，「剥不剥」恒真")
            elif _stripped != _our_acc:
                problems.append(
                    "真实帧过一遍 `_strip_core_progress` 没剥回核心自己的正文："
                    f"{_stripped!r} != {_our_acc!r}")
        except Exception as _exc:
            problems.append(
                f"核心投递链路的形状变了（{_exc!r}）⇒ 前提核对无法进行 —— "
                "`_strip_core_progress` 的前缀判据依赖它，必须有人看一眼")

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
                # P2 起默认主题是 ap_lite ⇒ 轮数 / 工具数符号是 🌊 / 🧰。这条门禁验的是
                # 「标题段有没有丢」，不是主题常量自身；常量由单测与 golden trace 冻结。
                if "🌊 3" not in title or "🧰 1" not in title:
                    problems.append(f"黄金路径：面板标题行不对（期望含「🌊 3 / 🧰 1」）：{title!r}")
                print(f"黄金路径面板：{title!r}")

            # ⚠️ 面板标题里的**耗时段**（`⏱ 12.3s`）此前无人验：上面那次调用传的是
            # `started=None`，所以它永远不出现（第八路审计点出过这个盲区）。生产路径
            # 是**传 t0 的**（native 帧/收尾/重绘都传 `state["t0"]`），所以这里必须补一次
            # 带 started 的调用 —— 否则「面板头里没有耗时」这种退化四门禁全绿。
            timed = _adm.LarkDeckMixin._ld_panel("", time.monotonic() - 12.3)
            if timed is None:
                problems.append("黄金路径：带 started 的面板渲染不出来")
            else:
                t_title = timed.get("header", {}).get("title", {}).get("content", "")
                print(f"黄金路径面板（带耗时）：{t_title!r}")
                if "⏱" not in t_title:
                    problems.append(f"黄金路径：面板标题没有耗时段（期望「⏱ 12.3s」）：{t_title!r}")
                elif "12.3s" not in t_title:
                    problems.append(f"黄金路径：耗时段的数值不对（期望 12.3s）：{t_title!r}")
                if "🌊 3" not in t_title or "🧰 1" not in t_title:
                    problems.append(f"黄金路径：耗时段的出现把它它段挤掉了：{t_title!r}")
        except Exception as exc:  # pragma: no cover - 防御性
            problems.append(f"黄金路径：渲染异常 {exc!r}")

    # ------------------------------------------------------------------ #
    # 一个完整回合的**可见帧序列**：seed → 帧 → 收尾，断言用户看到的那条线
    # ------------------------------------------------------------------ #
    # 为什么单开一节：前面每一节都在验「某一层对不对」（钩子写没写、面板有没有数据、
    # 单张卡渲染成什么样），而**没有任何一条门禁**把「一个回合里用户依次看到的那几张卡」
    # 串起来断言过。而那正是 6 项效果的落点：
    #   * 效果 1a：首帧就该有内容（等待期占位），不能是一张近乎空白的卡；
    #   * 效果 4：面板要**逐帧长大**（轮次/工具/耗时都在面板头里）；
    #   * 效果 2：收尾帧必须把边框画成绿色、并且**不能再带等待期占位**；
    #   * 效果 3：回合中途 `/stop`，那张卡必须被重绘成中止色（黄）。
    # 这一节用**真适配器类**（经插件加载器拿到的那个，MRO/覆盖都在）配一个"录音"客户端，
    # 走生产路径 `send_stream_frame` / `interrupt_session_activity`，把每次 API 的载荷抄下来。
    try:
        import asyncio
        from types import SimpleNamespace

        class _Resp:
            """够真的假响应：官方适配器用 `response.success()` 判成败（不是 `code`）。"""

            def __init__(self, code, message_id=""):
                self.code = code
                self.msg = "success" if code == 0 else "boom"
                self.data = SimpleNamespace(message_id=message_id)

            def success(self):
                return self.code == 0

        sent = []          # [(kind, payload_dict)]
        counter = {"n": 0}

        def _payload(request):
            body = getattr(request, "request_body", None) or getattr(request, "body", None)
            return json.loads(getattr(body, "content", "{}") or "{}")

        class _Message:
            def create(self, request):
                counter["n"] += 1
                sent.append(("create", _payload(request)))
                return _Resp(0, f"om_seq_{counter['n']}")

            def patch(self, request):
                sent.append(("patch", _payload(request)))
                return _Resp(0)

        fake_client = SimpleNamespace(
            im=SimpleNamespace(v1=SimpleNamespace(message=_Message())))

        _adm = (sys.modules.get("hermes_plugins.larkdeck.core.adapter")
                or sys.modules["larkdeck.core.adapter"])
        from gateway.config import PlatformConfig
        from gateway.platform_registry import platform_registry
        # SDK 的请求构造类是**懒绑定**在 connect 时机的；这里不 connect（不碰真网络），
        # 所以显式触发一次绑定，否则构造出的是 SimpleNamespace、载荷读不出来。
        _base_mod = sys.modules.get(type(_adm).__mro__[0].__module__) or sys.modules.get(
            "hermes_plugins.feishu_platform.adapter")
        _loader = getattr(_base_mod, "_load_lark_oapi", None)
        if _loader is not None:
            _loader()
        adapter_obj = platform_registry.get("feishu").adapter_factory(
            PlatformConfig(enabled=True, extra={}))
        adapter_obj._client = fake_client
        # ⚠️ 这一节验的是**传输无关**的可见帧语义（占位 / 面板出现 / 收尾绿边 / `/stop` 黄边），
        # 而它造的是真适配器 ⇒ 会把当时的默认传输带进来。所以**显式钉住 patch**：
        # 阶段 9 的新传输另有自己的门禁（单测的 cardkit 用例 + 真机 `--cardkit-prod`），
        # 两件事不该互相牵动（翻默认时这里曾红过一次，就是这么发现的）。
        _adm._CONFIG["native_transport"] = "patch"

        _panel.reset()
        turn_key = "seq-turn-1"
        chat_id = "oc_seq"

        def _frame(text, *, finalize=False):
            return asyncio.run(adapter_obj.send_stream_frame(
                text, finalize=finalize, chat_id=chat_id, turn_id=turn_key))

        # ① 首帧（seed）：等待期占位 + 流式标记 + 不是空卡
        assert _frame(""), "黄金序列：seed 帧没建起卡"
        assert sent and sent[0][0] == "create", sent[:1]
        first = sent[0][1]
        first_body = json.dumps(first, ensure_ascii=False)
        pending = _adm._i18n.t("stream.pending") if hasattr(_adm, "_i18n") else "⏳"
        print(f"黄金序列 ①：seed 帧卡片 {len(first_body)} 字节，含占位 = {pending in first_body}")
        if pending not in first_body:
            problems.append("黄金序列：seed 帧没有等待期占位（用户会看到一张近乎空白的卡）")
        if first.get("config", {}).get("streaming_mode") is not True:
            problems.append(f"黄金序列：seed 帧没有 streaming_mode=true：{first.get('config')!r}")

        def _wait_for(pred, what, timeout=3.0):
            """等异步 worker 把钩子数据落进面板 —— 不等就会读到「旧快照」。

            推理增量走的是 Hermes 的**异步**队列（每个回调一个守护 worker），
            所以「写完钩子立刻读」是竞态。黄金路径那节也用了同样的等待法。
            """
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                snap = _panel.snapshot()
                if snap and pred(snap):
                    return True
                time.sleep(0.02)
            problems.append(f"黄金序列：等不到「{what}」（面板快照={_panel.snapshot()!r}）")
            return False

        # ② 第一个**正文**帧：推理已经落进面板 ⇒ 面板必须出现，且占位必须消失
        # ⚠️ 两点必须与核心的真实行为对齐，否则这条门禁会在验一个不存在的场景：
        #   * 核心自己就跳过「文本没变」的中间帧（`gateway/stream_consumer_transport.py`
        #     的 `if not finalize and text == self._last_sent_text: return True`）——
        #     所以**纯推理期间（正文还是空）根本不会有帧**，面板只能在正文开始长之后才更新。
        #     这是核心的行为，不是我们的（README 的已知限制里写了）。
        #   * `_STREAM_MIN_INTERVAL = 0.25s` 的节流窗口也要等过，否则中间帧被跳过不下发。
        _stream("reasoning", "先想一下")
        _wait_for(lambda s: (s.get("rounds") or []), "推理轮落进面板")
        time.sleep(0.3)
        assert _frame("半"), "黄金序列：第一个正文帧失败"
        mid_body = json.dumps(sent[-1][1], ensure_ascii=False)
        print(f"黄金序列 ②：正文帧含面板 = {'collapsible_panel' in mid_body}，"
              f"含占位 = {pending in mid_body}")
        if "collapsible_panel" not in mid_body:
            problems.append("黄金序列：正文开始了但面板没出现（效果 4 的实时性丢了）"
                            f"（快照={_panel.snapshot()!r}）")
        if pending in mid_body:
            problems.append("黄金序列：正文到了还留着等待期占位")

        # ③ 正文长起来（帧内容确实变了）⇒ 面板里的推理文本要跟着长
        time.sleep(0.3)
        _stream("reasoning", "，再看工具")
        _wait_for(lambda s: len(str(s.get("reasoning") or "")) > 4, "推理文本变长")
        time.sleep(0.3)
        assert _frame("这是答案"), "黄金序列：正文帧失败"
        body_frame = json.dumps(sent[-1][1], ensure_ascii=False)
        print(f"黄金序列 ③：正文帧含真答案 = {'这是答案' in body_frame}，"
              f"含占位 = {pending in body_frame}")
        if "这是答案" not in body_frame:
            problems.append("黄金序列：正文帧里没有答案")
        if pending in body_frame:
            problems.append("黄金序列：正文到了还留着等待期占位")

        # ④ 收尾帧：绿边 + 不再带占位 + streaming_mode 关闭
        invoke_hook("on_session_end", session_id=_SESSION, task_id="t1", turn_id=_TURN,
                    completed=True, failed=False, interrupted=False,
                    turn_exit_reason="text_response(stop)", model="deepseek-v4-flash",
                    platform="feishu")
        _wait_for(lambda s: s.get("status") == "ok", "回合结局状态落进面板")
        assert _frame("这是答案", finalize=True), "黄金序列：收尾帧失败"
        last_kind, last = sent[-1]
        last_body = json.dumps(last, ensure_ascii=False)
        print(f"黄金序列 ④：收尾用 {last_kind}，绿边 = {'green' in last_body}，"
              f"占位 = {pending in last_body}")
        if last.get("config", {}).get("streaming_mode") is not False:
            problems.append(f"黄金序列：收尾帧没关 streaming_mode：{last.get('config')!r}")
        if "green" not in last_body:
            problems.append("黄金序列：收尾帧没有绿色状态色（效果 2 丢了）")
        if pending in last_body:
            problems.append("黄金序列：收尾帧带着等待期占位（空答案的回合会永远停在「正在生成…」）")

        # ⑤ `/stop`：同一张卡被重绘成中止色（效果 3）
        _panel.reset()
        sent.clear()
        counter["n"] = 0
        assert _frame(""), "黄金序列：/stop 场景 seed 帧失败"
        time.sleep(0.3)
        assert _frame("半截答案"), "黄金序列：/stop 场景正文帧失败"
        sent.clear()
        asyncio.run(adapter_obj.interrupt_session_activity("seq-turn-2", chat_id))
        painted = [(k, json.dumps(p, ensure_ascii=False)) for k, p in sent]
        print(f"黄金序列 ⑤：/stop 之后 {len(painted)} 次写入，"
              f"含黄边 = {any('yellow' in b for _, b in painted)}")
        if not painted:
            problems.append("黄金序列：/stop 之后一次写入都没有（那张卡不会变色）")
        elif not any("yellow" in b for _, b in painted):
            problems.append("黄金序列：/stop 的重绘载荷里没有中止色")
    except Exception as exc:  # pragma: no cover - 防御性
        problems.append(f"黄金序列：异常 {exc!r}")

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
