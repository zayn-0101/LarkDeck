#!/usr/bin/env python3
"""探针（P5/P0 收尾）：**长回合**那张卡的真机验收 —— 25 个工具步 > `max_steps`(20)。

为什么单独发这一张：长回合走的代码路径与短回合**不同**（`panel.trimmed` 折叠提示 + 只保留最后
20 步），而 2026-09-22 修掉的两个非法字段恰好都只在这条路径上出现：

  * `markdown.text_color` —— 折叠提示那行（`collapsed_hint`）；
  * `div.text.icon`      —— 错误块标题的前缀图标。

服务端对未知字段是 **整张卡被拒**（`200621`，不是忽略该字段）⇒ 核心回落 `send()` ⇒ 用户看到
「卡片 + 灰色气泡」两张（用户反馈 #4 的同一种故障）。**这张卡就是生产渲染器对「25 步回合」的
真实输出**，发出去成功 = 长回合形状不再会被拒。

判据（用户目视）：① 面板顶部有「还有 5 步未显示」那行、且带前缀图标；② 20 行工具都在面板里、
图标是**统一的灰色线性**；③ 没有纯文本「⏳ Working —」气泡；④ 面板能正常展开/折叠。

用法::

    PY="${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python3"
    $PY tests/probe_long_turn.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_render as P  # noqa: E402

_P = P.load_cards()          # 同一个包对象 `_larkdeck_probe` 已经装好
import importlib as _importlib  # noqa: E402
_cardview = _importlib.import_module("_larkdeck_probe.core.cardview")

#: 生产上限（`adapter` 里是 `min(_cfg_int("max_panel_steps", 20), 20)`）
MAX_STEPS = 20

#: 25 步：前 5 步会被折叠掉（只保留最后 20 步），其中包含 running / error / timeout 与错误块
_NAMES = [
    ("skills_list", "app-default_outlined", "ok"),
    ("read_file", "file-link-text_outlined", "ok"),
    ("grep", "doc-search_outlined", "ok"),
    ("todo_read", "todo_outlined", "ok"),
    ("session_list", "history-search_outlined", "ok"),
    ("terminal", "setting_outlined", "ok"),
    ("web_search", "search_outlined", "ok"),
    ("web_fetch", "language_outlined", "ok"),
    ("memory_search", "organization-book_outlined", "ok"),
    ("cronjob_list", "alarm-clock_outlined", "ok"),
    ("write_file", "edit_outlined", "ok"),
    ("read_file", "file-link-text_outlined", "ok"),
    ("exec", "setting_outlined", "ok"),
    ("skills_list", "app-default_outlined", "ok"),
    ("terminal", "setting_outlined", "running"),
    ("read_file", "file-link-text_outlined", "ok"),
    ("send_message", "send_outlined", "ok"),
    ("react", "emoji_outlined", "ok"),
    ("image_generate", "image_outlined", "ok"),
    ("read_file", "file-link-text_outlined", "error"),
    ("grep", "doc-search_outlined", "ok"),
    ("terminal", "setting_outlined", "timeout"),
    ("todo_write", "todo_outlined", "ok"),
    ("memory_write", "organization-book_outlined", "ok"),
    ("summarize", "app-default_outlined", "ok"),
]


def build_card() -> dict:
    steps = [
        _cardview.ToolStepView(
            name=name, title=name, status=status,
            duration_ms=None if status == "running" else 12 + i,
            icon_token=tok,
            detail=f'{{"step": {i + 1}}}',
            error_block=("permission denied: /tmp/locked.txt" if status == "error" else ""),
        )
        for i, (name, tok, status) in enumerate(_NAMES)
    ]
    hint = _cardview._i18n.t("panel.trimmed", n=len(steps) - MAX_STEPS)
    view = _cardview.CardView(
        answer="这是一张**长回合**（25 个工具步 > 上限 20）的真实形状：\n"
               f"1. 面板里只保留最后 {MAX_STEPS} 步，顶部一行折叠提示（`panel.trimmed`）—— "
               "它以前带着非法字段 `markdown.text_color`，会让**整张卡**被服务端拒收；\n"
               "2. 错误块标题的前缀图标以前挂在 `div.text.icon`（那里没有这个字段）—— 同样整卡被拒；\n"
               "3. 两个都是 2026-09-22 真机探针揪出来并修掉的（见 `docs/internal/audits/v0.7.2/audit-round1.md` §8.7）。\n\n"
               "请确认：① 折叠提示那行有图标、没乱码；② 20 行工具图标是否**统一灰色线性**；"
               "③ 展开/折叠是否正常；④ 这张卡之外**没有**再跟一条纯文本「⏳ Working —」。",
        panel=_cardview.PanelView(
            title=f"🛠️ 工具执行 · {len(steps)} 步",
            tools=steps[-MAX_STEPS:],
            collapsed_hint=hint,
            border="green",
            tool_icon_mode="line",
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
    card = build_card()
    print(f"卡片元素数：{len(card['body']['elements'])}；"
          f"面板子元素数：{len(card['body']['elements'][1]['elements'])}")
    code, msg, mid = P.send(client, chat, card)
    print(f"发送结果: code={code} msg={msg!r} message_id={mid}")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
