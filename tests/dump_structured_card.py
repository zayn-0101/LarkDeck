#!/usr/bin/env python3
"""离线看「最终那张卡」长什么样 —— 不联网、不发消息、不重启任何东西。

为什么需要它
------------
结构化引擎的元素树（`core/cardview.py`）改一处，真机上要等到**下一轮真回合**才看得见；
而审计与自查经常只需要回答「这张卡最终 JSON 里页脚是什么、面板标题有没有模型名、
工具行的 icon/margin/颜色对不对、元素数离硬上限还有多远」—— 这些**读 JSON 就够了**，
不该去消耗用户的时间（每次真机验证都要用户发消息 + 截图）。

它做什么
--------
用 `tests/test_units.py` 里那套**假 CardKit**（零网络）跑一遍真实的
`send_stream_frame` 三帧流程：seed → 更新 → finalize，然后把**收尾那张整卡 patch**
里的卡片 JSON 打出来（含面板与页脚），并打印元素数、字节数、页脚文本。

它不做什么
----------
不 import 真 SDK、不建卡实体、不发消息、不读 `~/.hermes` 的凭据，因此**没有副作用**。

用法::
    PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
    $PY tests/dump_structured_card.py            # 3 个工具 + 1 轮推理 + Result/Error
    $PY tests/dump_structured_card.py --plain    # 不渲染推理（show_reasoning=false）
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_units as T  # noqa: E402

from larkdeck.core import adapter, context, panel  # noqa: E402


def build_session(chat: str, *, show_reasoning: bool) -> tuple:
    panel.reset()
    context.reset()
    raw = T._make()
    calls, client = T._mk_cardkit_fake()
    raw._client = client
    target_cls = type(raw)
    old_reqs = target_cls.__dict__.get("_ld_ck_requests")
    adapter.configure(visual_engine="structured", native_transport="cardkit",
                      show_reasoning=show_reasoning)
    adapter._STREAM_MIN_INTERVAL = 0.0
    target_cls._ld_ck_requests = staticmethod(T._fake_ck_requests)
    panel.bind_chat_session(chat, "sess")
    if show_reasoning:
        panel.record_reasoning("sess", "sess", "先确认磁盘占用，再看是否需要清理。")
    for name, detail in (("skill_view", {"name": "df"}),
                         ("terminal", {"command": "df -h"}),
                         ("read_file", {"path": "/tmp/report.md"})):
        panel.record_tool_started("sess", "sess", name, detail, tool_call_id=f"tc-{name}")
    panel.record_tool_finished("sess", "sess", "skill_view", status="ok", duration_ms=210,
                               tool_call_id="tc-skill_view", result="ok · 4 行")
    panel.record_tool_finished("sess", "sess", "terminal", status="ok", duration_ms=347,
                               tool_call_id="tc-terminal", result="Filesystem 460G 92%")
    panel.record_tool_finished("sess", "sess", "read_file", status="error", duration_ms=12,
                               tool_call_id="tc-read_file", error_type="FileNotFound",
                               error_message="no such file")
    return raw, calls, target_cls, old_reqs


def main(argv: list) -> int:
    show_reasoning = "--plain" not in argv
    chat, turn = "oc_dump", "t1"
    raw, calls, target_cls, old_reqs = build_session(chat, show_reasoning=show_reasoning)
    try:
        for text, final in (("正在整理磁盘报告", False),
                            ("磁盘占用 92%，主要为数据卷。", False),
                            ("磁盘占用 92%，主要为数据卷；建议清理 Docker 缓存。", True)):
            if final:
                # 把回合起点往前推 12 秒：`footer_line` 只在耗时 ≥0.1s 时渲染那一段，
                # 离线一遍流程不到 1 毫秒 ⇒ 不打这一下，转储出来的页脚永远看不出时长。
                state = raw._ld_stream_get(f"{chat}:{turn}") or {}
                state["t0"] = time.monotonic() - 12.0
                raw._ld_stream_put(f"{chat}:{turn}", state)
            ok = T._run(raw.send_stream_frame(text, chat_id=chat, turn_id=turn,
                                              finalize=final))
            assert ok, f"帧失败: {text!r}"
        finals = [c for c in (calls.get("patch_cards") or [])
                  if not c.get("config", {}).get("streaming_mode", True)]
        card = finals[-1]
        footers = [e for e in card["body"]["elements"] if e.get("element_id") == "footer"]
        print("页脚文本:", json.dumps(footers[-1]["content"], ensure_ascii=False)
              if footers else "<无>")
        print("元素数:", T.adapter._cards.count_elements(card),
              "· 字节数:", T.adapter._cards.card_bytes(card))
        print("---")
        print(json.dumps(card, ensure_ascii=False, indent=2))
    finally:
        if old_reqs is None:
            delattr(target_cls, "_ld_ck_requests")
        else:
            target_cls._ld_ck_requests = old_reqs
        adapter._CONFIG.clear()
        panel.reset()
        context.reset()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
