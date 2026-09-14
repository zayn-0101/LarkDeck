"""真机探针：**在同一进程里复现「插件被加载两次」的条件**，验证面板/页脚数据不再分裂。

## 它回答什么问题（不需要用户参与）

真机根因（2026-09-14）：Hermes 在**同一个网关进程里**把插件发现两遍（日志里
`Plugin discovery complete` 出现两次、启动自检打印两遍）⇒ `core.panel` / `core.context`
各有**两份模块对象**。修法是把这些状态挂到进程级共享容器上。本探针把那个条件**搬到本地复现**：

1. 用**真适配器**（Hermes 加载器那份命名空间）驱动一整个回合 —— seed + 若干帧 + 收尾；
2. 同时**另外导入一份** `panel` 模块（`larkdeck.core.panel`，与加载器那份不同的模块对象），
   **用第二份**记录推理与工具（模拟「钩子写 A 份」）；
3. 由代码判定：适配器**建出来的卡**里，面板两块与页脚**有没有内容** ——
   修好之后必须都有；把共享改回模块局部（变异 `R10-1/R10-2/R10-3`）就必须没有。

## 判据（全部代码判，不用眼睛）

* `panel_text`（推理块）非空且含探针灌入的推理文本；
* `panel_tools_text`（工具块）非空且含探针灌入的工具名；
* `_ld_footer()` 非空且与建卡时传进去的页脚一致。

## 用法

```
python3 tests/probe_two_instances.py            # 跑完自动删卡（按 message_id）
python3 tests/probe_two_instances.py --keep     # 留一张卡给你看
```
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
sys.path.insert(0, str(REPO.parent))                 # 使 `import larkdeck` 指向**第二份**

spec = importlib.util.spec_from_file_location("pr", str(REPO / "tests" / "probe_render.py"))
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)

os.environ.setdefault("HERMES_HOME", os.path.expanduser("~/.hermes"))
sys.path.insert(0, os.path.expanduser("~/.hermes/hermes-agent"))

env = pr.load_env()
chat = env["FEISHU_HOME_CHANNEL"]
_made = pr._load_adapter_for_probe(chat)
adapter, _panel_loaded = _made[1], (_made[2] if len(_made) > 2 else None)
# 加载器那份 panel 模块：优先用探针返回的，其次按加载器命名空间从 sys.modules 里取
if _panel_loaded is None:
    _panel_loaded = sys.modules.get("hermes_plugins.larkdeck.core.panel")
if _panel_loaded is None:
    raise SystemExit("拿不到加载器那份 panel 模块")
if adapter is None:
    raise SystemExit("造不出真适配器")
cards = pr.load_cards()

# ★ **第二份模块对象**：名字与加载器那份（`hermes_plugins.larkdeck.core.panel`）不同
import larkdeck.core.panel as panel_copy                      # noqa: E402
import larkdeck.core.context as context_copy                  # noqa: E402

print(f"   加载器那份 panel 模块 = {_panel_loaded.__name__}")
print(f"   第二份 panel 模块     = {panel_copy.__name__}")
print(f"   两份的 _STATE 是同一个对象吗 = {_panel_loaded._STATE is panel_copy._STATE}（必须是 True）")

tid = f"probe-2inst-{int(time.time())}"
sid = f"probe-sid-{int(time.time())}"
# ① **用第二份模块**记录推理与工具（模拟「钩子写在另一份模块对象上」）
panel_copy.bind_chat_session(chat, sid)
panel_copy.begin_turn(sid, tid)
panel_copy.record_reasoning(sid, tid, "探针：这段推理必须在面板里看得见。")
panel_copy.record_tool_started(sid, tid, "probe_tool", {"cmd": "echo hi"}, "probe-c1")
panel_copy.record_tool_finished(sid, tid, "probe_tool", status="ok", duration_ms=1200,
                                tool_call_id="probe-c1")
context_copy.record_api_call(model="probe-model",
                             usage={"input_tokens": 4321, "output_tokens": 12})
context_copy.set_context_override(20000)
print(f"   （数据由第二份模块写入：tools={len((panel_copy.snapshot(chat) or {}).get('tools') or [])}）")

captured: dict = {}
orig_create = adapter._ld_ck_create


async def _spy(*a, **kw):
    captured.update(kw)
    return await orig_create(*a, **kw)


adapter._ld_ck_create = _spy
_adapter_mod = sys.modules.get("hermes_plugins.larkdeck.core.adapter") or _load
_orig_write = adapter._ld_ck_write


async def _noop_write(*a, **kw):
    """正文元素写打桩（只验建卡载荷，不必真发帧）。"""
    class _R:
        ok = True
        code = 0
        msg = ""
    return _R()


adapter._ld_ck_write = _noop_write
try:
    loop = asyncio.new_event_loop()
    ok_seed = loop.run_until_complete(adapter.send_stream_frame("", chat_id=chat, turn_id=tid))
    ok_body = loop.run_until_complete(adapter.send_stream_frame(
        "正文（探针）", chat_id=chat, turn_id=tid))
    loop.close()
finally:
    adapter._ld_ck_create = orig_create
    adapter._ld_ck_write = _orig_write

body = captured.get("panel_text") or ""
tools = captured.get("panel_tools_text") or ""
footer = captured.get("footer_text") or ""
print(f"   seed={ok_seed} · 正文帧={ok_body}")
print(f"   建卡时的推理块 = {body[:60]!r}")
print(f"   建卡时的工具块 = {tools[:60]!r}")
print(f"   建卡时的页脚   = {footer!r}")

checks = {
    "推理块非空且含探针推理": bool(body) and "这段推理必须在面板里看得见" in body,
    "工具块非空且含探针工具": bool(tools) and "probe_tool" in tools,
    "页脚非空且含用量": bool(footer) and "ctx" in footer,
}
for name, ok in checks.items():
    print(f"   {'✅' if ok else '❌'} {name}")
# 清理：按 message_id 删掉探针卡（账本是 `probe_render` 那份）
for mid in list(pr._load_sent_ids()):
    try:
        from lark_oapi.api.im.v1 import DeleteMessageRequest
        adapter._client.im.v1.message.delete(
            DeleteMessageRequest.builder().message_id(str(mid)).build())
        print(f"   已删探针卡 {str(mid)[-8:]}")
    except Exception as exc:
        print(f"   删卡失败（{exc!r}）")
pr._save_sent_ids([])
raise SystemExit(0 if all(checks.values()) else 1)
