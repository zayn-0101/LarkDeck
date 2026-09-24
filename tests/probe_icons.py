#!/usr/bin/env python3
"""探针（P3）：工具行「图标比文字偏上」——**同一段文本、只变图标**的三臂对照。

为什么重做（审计 A 的收口）
--------------------------
旧计划的 A/B（甲 `div.icon` / 乙 emoji 内联）**没有隔离变量**：它同时改了两件事
（有没有 `div.icon`、文本长什么样），用户选出来的结论无法归因。
审计 A 逐字段比对 CLS `builder.py:157-175` 与我们的 `cardview.py` 之后发现：两侧 icon 对象
**字段完全相同**（都没有 size / margin，`text_size` 都是 notation）⇒「系统性基线偏移」解释不了，
最可能的假说是**我们的行更长**（原始工具名 + 原始毫秒），在手机宽度换行时图标保持顶对齐，
看起来就像「图标比文字高」。

所以这张卡三行**互相对照**：

  甲  `div.icon` + **短文本**（CLS 形态：友好名 + `2.3 s`）…… 基线臂
  乙  文本内联 emoji + **同一段短文本**（没有 `div.icon`）…… 只差「图标怎么放」
  丙  `div.icon` + **长文本**（我们现在的生产形态：原始工具名 + `2340 ms`）…… 只差「文本长短」

用户回一句「甲/乙/丙 哪版图标与文字对得最齐」+「丙 那行换行了吗」就够定版。

用法::

    PY="${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python3"
    $PY tests/probe_icons.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_render as P  # noqa: E402
import probe_loading as L  # noqa: E402

#: 与生产同一张表（`core/cardview.py::ICON_ALIASES` 里 exec/command/run → setting_outlined）
TOKEN = "setting_outlined"
SHORT_TEXT = "**Run command (2.3 s)** · <font color='green'>Succeeded</font>"
LONG_TEXT = "**terminal** (2340 ms) · <font color='green'>Succeeded</font>"


def _row(icon, text: str, label: str) -> dict:
    node = {"tag": "div",
            "text": {"tag": "lark_md", "content": f"{label}　{text}",
                     "text_size": "notation"}}
    if icon is not None:
        node["icon"] = icon
    return node


def build_card() -> dict:
    icon = {"tag": "standard_icon", "token": TOKEN, "color": "grey"}
    return {
        "schema": "2.0",
        "config": {"streaming_mode": False},
        "header": {"template": "blue",
                   "title": {"tag": "plain_text", "content": "P3 工具行图标探针（三臂对照）"}},
        "body": {"elements": [
            _row(icon, SHORT_TEXT, "**甲**"),
            _row(None, f"🛠️ {SHORT_TEXT}", "**乙**"),
            _row(icon, LONG_TEXT, "**丙**"),
            {"tag": "markdown", "text_size": "notation", "content":
             "三行的**图标/emoji 与文字是同一段内容**；甲↔乙 只差「图标怎么放」，"
             "甲↔丙 只差「文字长短」。\n\n"
             "请回一句：**甲/乙/丙 哪一版图标与文字对得最齐？** 以及 **丙 那行有没有换行？**"},
        ]},
    }


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
    print("请肉眼确认：甲/乙/丙 哪版图标与文字对得最齐 + 丙 有没有换行")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
