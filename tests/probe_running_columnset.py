#!/usr/bin/env python3
"""P6 探针：把「运行中」的动图放到 Running **后面** —— 只剩 `column_set` 这一条路。

背景（用户 2026-09-22 真机截图）：markdown 内联图片 `![ ](key)` 会渲染成**块级整行大图**，
`Running` 还被挤到下一行 ⇒ 「文字后面的 16px 小图」在 markdown 里做不到。
唯一还剩的机制：`column_set` 两列（左列文字、右列 16px 动图）。

用法： $PY tests/probe_running_columnset.py [--send]
"""
from __future__ import annotations

import argparse
import importlib
import pathlib
import sys
import types

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
import probe_render as P  # noqa: E402


def load_core():
    pkg = types.ModuleType("_larkdeck_probe")
    pkg.__path__ = [str(REPO)]
    sys.modules["_larkdeck_probe"] = pkg
    return importlib.import_module("_larkdeck_probe.core.cardview")


def main() -> int:
    ap = argparse.ArgumentParser(description="column_set 变体对照（默认只预览）")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    import lark_oapi as lark
    cv = load_core()
    key = cv.spinner_img_key()
    body = "**terminal** · <font color='blue'>Running</font>"
    icon = {"tag": "custom_icon", "img_key": key, "size": "16px 16px"}

    def variant(name: str) -> dict:
        left = {"tag": "column", "vertical_align": "center",
                "width": "weighted", "weight": 1,
                "elements": [{"tag": "markdown", "content": body,
                              "text_size": cv.PANEL_TEXT_SIZE}]}
        right = {"tag": "column", "vertical_align": "center", "width": "auto",
                 "elements": [{"tag": "div", "icon": icon,
                               "text": {"tag": "plain_text", "content": ""}}]}
        if name == "B":
            left["width"] = "auto"
        return {"tag": "column_set", "flex_mode": "none", "horizontal_align": "left",
                "horizontal_spacing": "0px" if name != "C" else "8px",
                "columns": [left, right]}

    elements = [{"tag": "markdown",
                 "content": "**动图放在 Running 后面**：三条 `column_set` 变体 —— "
                            "回我 **A / B / C / 都不行**（顺带说一下动图与文字的距离）"}]
    for name, label in (("A", "A：左列 weighted(1) + 右列 auto，间距 0px"),
                        ("B", "B：两列都 auto（贴合内容）"),
                        ("C", "C：同 A，间距 8px")):
        elements += [{"tag": "markdown", "content": label}, variant(name), {"tag": "hr"}]
    elements += [{"tag": "markdown",
                  "content": "三条都不理想的话就保留现状（动图做前缀），我不再折腾布局。"}]
    card = {"schema": "2.0", "config": {"streaming_mode": False}, "body": {"elements": elements}}
    if not a.send:
        print(f"（预览，未发送；{len(elements)} 个元素；key={key}）")
        return 0
    env = P.load_env()
    client = (lark.Client.builder().app_id(env["FEISHU_APP_ID"])
              .app_secret(env["FEISHU_APP_SECRET"]).log_level(lark.LogLevel.ERROR).build())
    code, msg, mid = P.send(client, env["FEISHU_HOME_CHANNEL"], card)
    print(f"发送结果: code={code} msg={msg!r} message_id={mid}")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
