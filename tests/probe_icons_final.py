#!/usr/bin/env python3
"""探针（P3 定版确认）：用**生产渲染器**渲染三行真实工具，确认「乙（emoji 内联）」的最终样子。

与 `tests/probe_icons.py` 的区别：那一张是**手搓的三臂对照**（用来做选择）；
这一张是 `cardview.entity_skeleton()` 的**真实输出**，也就是用户以后在真机上看到的东西
（每个工具各自的 emoji + 原始工具名 + 耗时 + 状态色）。

用户 2026-09-21 已选「乙 = 图标与文字对得最齐」，且明确「丙 那行没有换行」⇒
按内联 emoji 落地；这张卡是**落地后**的目视确认（emoji 选得是否合适、对齐是否真的好了）。

用法::

    PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
    $PY tests/probe_icons_final.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_render as P  # noqa: E402

#: 走**生产**模块（`core/cardview.py`）—— 与插件里发出去的是同一段代码。
#: 与 `probe_render.load_cards()` 同一条加载法：绕开 `__init__.py`（它会拉 Hermes 依赖）。
_P = P.load_cards()          # 同一个包对象 `_larkdeck_probe` 已经装好
import importlib as _importlib  # noqa: E402
_cardview = _importlib.import_module("_larkdeck_probe.core.cardview")


def build_card() -> dict:
    steps = [
        _cardview.ToolStepView(name="read_file", title="read_file", status="ok",
                               duration_ms=120, icon_token="file-link-text_outlined",
                               detail='{"path": "/tmp/a.txt"}'),
        _cardview.ToolStepView(name="terminal", title="terminal", status="running",
                               icon_token="setting_outlined",
                               detail='{"command": "df -h"}'),
        _cardview.ToolStepView(name="web_search", title="web_search", status="error",
                               duration_ms=2340, icon_token="search_outlined"),
        _cardview.ToolStepView(name="完全没听过", title="完全没听过", status="ok",
                               duration_ms=8, icon_token="setting-inter_outlined"),
    ]
    view = _cardview.CardView(
        answer="这一版工具行用**内联 emoji**（用户选版：乙）。请确认三点：\n"
               "1. 图标（emoji）与文字是否**对得齐**；\n"
               "2. 四个 emoji（📄 🛠️ 🔍 🔧）选得合不合适；\n"
               "3. 有没有哪一行看着别扭。",
        panel=_cardview.PanelView(
            title="🛠️ 工具执行 · 4 步",
            tools=steps,
            border="grey",
        ),
        header_enabled=False,
    )
    card = _cardview.entity_skeleton(view)
    card["config"]["streaming_mode"] = False
    return card


def main() -> int:
    import lark_oapi as lark
    env = P.load_env()
    client = (lark.Client.builder()
              .app_id(env["FEISHU_APP_ID"])
              .app_secret(env["FEISHU_APP_SECRET"])
              .log_level(lark.LogLevel.ERROR)
              .build())
    chat = env["FEISHU_HOME_CHANNEL"]
    code, msg, mid = P.send(client, chat, build_card())
    print(f"发送结果: code={code} msg={msg!r} message_id={mid}")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
