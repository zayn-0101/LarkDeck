#!/usr/bin/env python3
"""探针（P3 续）：飞书内置**线性图标**的三种放法对照 —— 统一灰色，只变「图标放在哪」。

用户 2026-09-22 口径：「emoji 好看但有点花里胡哨、颜色不统一；CLS 那种统一颜色看起来更高级」
⇒ 换成内置线性图标（`_outlined` + `color:"grey"`，与 CLS 同款）。但用户更早还提过
「图标比文字靠上」。本探针把**同一段文字、同一个 token、同一个灰色**放成三臂：

  甲  `div.icon`（元素级图标位，**CLS 同款**）…… 用户上次见过的「偏上」就是它
  乙  `markdown.icon`（官方文档叫「**前缀图标**」，图标作为文本前缀）…… 待验证是否不偏
  丙  `column_set` 两列（图标独占一列 + `vertical_align:center`）…… 用布局强制居中，保底

判据（用户目视，一次点击 / 一句话即可）：哪一臂「图标与文字对得最齐、看起来最统一」。
用法::

    PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
    $PY tests/probe_icons_layout.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_render as P  # noqa: E402

#: 与生产/CLS 同一张表：exec/command/run/terminal → `setting_outlined`（线性 + 统一灰）
TOKEN = "setting_outlined"
ROW = "**terminal (666 ms)** · <font color='green'>Succeeded</font>"


def _icon() -> dict:
    return {"tag": "standard_icon", "token": TOKEN, "color": "grey"}


def _label(text: str) -> dict:
    return {"tag": "markdown", "text_size": "notation",
            "content": f"**{text}**　<font color='grey'>（{_ARMS[text]}）</font>"}


_ARMS = {
    "甲": "div.icon：元素级图标位（CLS 同款，上次你觉得偏上的那种）",
    "乙": "markdown.icon：文本前缀图标（官方文档里的「前缀图标」）",
    "丙": "column_set：图标独占一列 + 垂直居中（用布局强制对齐）",
}


def _arm_a() -> dict:
    return {"tag": "div", "icon": _icon(),
            "text": {"tag": "lark_md", "content": ROW, "text_size": "notation"}}


def _arm_b() -> dict:
    return {"tag": "markdown", "icon": _icon(), "content": ROW, "text_size": "notation"}


def _arm_c() -> dict:
    return {
        "tag": "column_set",
        "flex_mode": "none",
        "horizontal_spacing": "6px",
        "columns": [
            {"tag": "column", "width": "weighted", "weight": 1,
             "vertical_align": "center", "elements": [
                 {"tag": "div", "icon": _icon(),
                  "text": {"tag": "plain_text", "content": " ", "text_size": "notation"}}]},
            {"tag": "column", "width": "weighted", "weight": 5,
             "vertical_align": "center", "elements": [
                 {"tag": "markdown", "content": ROW, "text_size": "notation"}]},
        ],
    }


def build_card() -> dict:
    return {
        "schema": "2.0",
        "config": {"streaming_mode": False},
        "header": {"template": "blue",
                   "title": {"tag": "plain_text",
                             "content": "工具行图标：三种放法对照（统一灰色线性图标）"}},
        "body": {"elements": [
            {"tag": "markdown", "text_size": "notation",
             "content": "三臂**文字、图标、颜色完全相同**（`setting_outlined` + `grey`），"
                        "只有「图标放在哪」不同。请挑一版：**图标与文字最齐、整体最统一**的那行。"},
            _label("甲"), _arm_a(),
            _label("乙"), _arm_b(),
            _label("丙"), _arm_c(),
            {"tag": "markdown", "text_size": "notation",
             "content": "\n请回一句：**甲 / 乙 / 丙**（若都不满意，说一下「谁比谁高/低」也可以）。"},
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
    print("请肉眼确认：甲（div.icon）/ 乙（prefix icon）/ 丙（column_set）哪版最齐")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
