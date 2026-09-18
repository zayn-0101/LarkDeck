#!/usr/bin/env python3
"""v0.7.0 P1 own 正文门禁：用**真实 core `_compose_frame_content`** 生成帧语料，
证明 own 模式非 finalize 正文只来自插件累积；finalize 整段选一且绝不按分隔符切片。

跑法（Hermes venv）：``python3 tests/check_own_body.py``。
本脚本只读/写内存状态，不发网络请求。
"""
from __future__ import annotations

import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_REPO_PARENT = os.path.dirname(_REPO)
for _p in (_REPO_PARENT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from larkdeck.core import adapter  # noqa: E402
from larkdeck.core import panel  # noqa: E402

import test_units as tu  # noqa: E402


def _compose(accumulated: str, progress: str) -> str:
    """用 Hermes 真实方法合成帧：`"\\n\\n---\\n".join((accumulated, progress))`。"""
    from gateway.stream_consumer import GatewayStreamConsumer
    node = types.SimpleNamespace(_accumulated=accumulated,
                                 _tool_progress_lines=list(progress.splitlines()))
    return GatewayStreamConsumer._compose_frame_content(node)


def main() -> int:
    panel.reset()
    adapter.configure(body_source="own", native_transport="patch")
    chat, turn = "oc_own_gate", "t-own-gate"
    raw = None
    try:
        panel.bind_chat_session(chat, "sess-own-gate")
        panel.record_answer_delta("sess-own-gate", turn, "正文答案")
        raw = tu._make()
        adapter.configure(body_source="own")

        corpus = {
            "terminal": "⚙️ terminal: \"echo secret\"",
            "search": "🔍 Searching the web for latest news",
            "working": "⏳ Working — 9 min — iteration 29",
            "cursor": "⚙️ Running",
            "multiline": "🔍 Searching files\n📖 Reading hosts",
        }
        for name, progress in corpus.items():
            frame = _compose("正文答案", progress)
            assert progress in frame and frame != "正文答案", \
                f"{name}: 核心合成前提失效（帧里没有进度，断言会恒真）"
            got = raw._ld_body_text(frame, chat, finalize=False)
            assert got == "正文答案", f"{name}: 非 finalize 读到 core 帧字节：{got!r}"
            assert progress not in got, f"{name}: 进度进入正文"
        # 模型自己写的分隔线 + own 之后的核心进度：非 finalize 也只回 own
        own_with_rule = "前半段\n\n---\n后半段"
        panel.reset()
        panel.bind_chat_session(chat, "sess-own-gate")
        panel.record_answer_delta("sess-own-gate", turn, own_with_rule)
        frame = _compose(own_with_rule, "⚙️ terminal: \"secret\"")
        assert raw._ld_body_text(frame, chat, finalize=False) == own_with_rule, \
            "非 finalize 不能读模型分隔线之后的任何 core 字节"
        # finalize：core 非空整段采用，不做任何 split/rsplit
        tail = own_with_rule + "\n\n---\n模型收尾句 END"
        assert raw._ld_body_text(tail, chat, finalize=True) == tail, \
            "finalize 必须整段采用 core，不能按分隔符切掉模型尾段"
        # core 清理后可以比 own 短且分叉：core 非空仍是权威
        assert raw._ld_body_text("清理后的权威终稿", chat, finalize=True) == "清理后的权威终稿"
        # core 空回 own
        assert raw._ld_body_text("", chat, finalize=True) == own_with_rule
        # 无绑定严格 fail-open：非 finalize 空、finalize 走 core
        panel.reset()
        assert raw._ld_body_text(frame, chat, finalize=False) == ""
        assert raw._ld_body_text("core 正文", chat, finalize=True) == "core 正文"
        # 占位不出现在账本：空 own 时 answer_state 严格读仍是空
        panel.bind_chat_session(chat, "sess-empty")
        text, _armed, _complete = panel.answer_state(chat, require_binding=True)
        assert text == "", f"空累积必须是空，占位只能在展示层：{text!r}"
        print("OWN BODY GATE OK")
        return 0
    finally:
        adapter.configure(body_source="legacy")
        panel.reset()
        if raw is not None:
            raw._client = None


if __name__ == "__main__":
    raise SystemExit(main())
