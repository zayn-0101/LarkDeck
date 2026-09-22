#!/usr/bin/env python3
"""P6 探针：工具行「运行中」把动图放到 `Running` **后面**（用户 2026-09-22 口径）。

用户原话：「红框里的这个动态效果，放在 running 后面，最前端还是用之前的 icon」

`markdown.icon` 是**前缀**槽（只有一个），所以后缀只能写进 content 里做**内联图片**
（`![alt](img_key)` —— 飞书 Card 2.0 的 markdown 支持，CLS 的 `optimize_markdown_style`
里也在用这个形态）。内联图片的**尺寸**由客户端决定，真机才能看清 ⇒ 先探针、再改代码。

用法： $PY tests/probe_running_suffix.py            # 预览（不发）
       $PY tests/probe_running_suffix.py --send     # 真发对照卡
"""
from __future__ import annotations

import argparse
import importlib
import json
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
    return (importlib.import_module("_larkdeck_probe.core.cardview"),
            importlib.import_module("_larkdeck_probe.core.cards"))


def main() -> int:
    ap = argparse.ArgumentParser(description="工具行「运行中」动图位置对照（默认预览）")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    import lark_oapi as lark
    cv, cds = load_core()
    key = cv.spinner_img_key()
    body = "**terminal**"
    blue = "<font color='blue'>Running</font>"

    def row(icon_token: str, content: str) -> dict:
        return {"tag": "markdown",
                "icon": {"tag": "standard_icon", "token": icon_token, "color": "grey"},
                "content": content, "text_size": cv.PANEL_TEXT_SIZE}

    rows = [
        ("① 线上现行：动图做**前缀**（你要改掉的形态）",
         {"tag": "markdown", "icon": {"tag": "custom_icon", "img_key": key},
          "content": f"{body} · {blue}", "text_size": cv.PANEL_TEXT_SIZE}),
        ("② 前缀＝原线性图标；动图**跟在 Running 后面**（内联图片）",
         row("command_outlined", f"{body} · {blue} ![ ]({key})")),
        ("③ 同 ②，但动图写在 `Running` **前面**（备选）",
         row("command_outlined", f"{body} · ![ ]({key}) {blue}")),
        ("④ 对照：已结束那一行（静态线性图标 + 绿 ✓）",
         row("command_outlined", f"{body} (347 ms) · <font color='green'>\u2713</font>")),
    ]
    elements = [{"tag": "markdown",
                 "content": "**工具行「运行中」的动图位置** —— 请回我一句选哪个（①②③），"
                            "并说一下 ② 里那个动图**大小/位置**是否合适（太小/太大/贴太近都能调）。"}]
    for label, node in rows:
        elements += [{"tag": "markdown", "content": label}, node, {"tag": "hr"}]
    elements += [{"tag": "markdown",
                  "content": "⚠️ 内联图片的尺寸由客户端决定（`markdown` 里没有 size 字段），"
                             "所以只有真机能看清 —— 这一格就是为此发的。"}]
    card = {"schema": "2.0", "config": {"streaming_mode": False}, "body": {"elements": elements}}
    if not a.send:
        print(json.dumps(card, ensure_ascii=False)[:400] + " …")
        print(f"（预览，未发送；共 {len(elements)} 个元素，动图 key = {key}）")
        return 0
    env = P.load_env()
    client = (lark.Client.builder().app_id(env["FEISHU_APP_ID"])
              .app_secret(env["FEISHU_APP_SECRET"]).log_level(lark.LogLevel.ERROR).build())
    code, msg, mid = P.send(client, env["FEISHU_HOME_CHANNEL"], card)
    print(f"发送结果: code={code} msg={msg!r} message_id={mid}")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
