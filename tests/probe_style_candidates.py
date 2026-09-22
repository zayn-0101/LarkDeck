#!/usr/bin/env python3
"""P6 探针：工具行「更粗」/ 细节行「更浅、更小」的候选矩阵（2026-09-22 用户三点诉求）。

用户诉求（截图标注）：
  * **绿框（工具名）**：现在不够粗 → 想**更粗**（`**x**` 是最强档了吗？`***x***`？`text_weight`？）
  * **红框（参数细节行 + Error 那行）**：想**更浅**、**更小**（`grey` 是最浅枚举？十六进制行不行？
    `notation` 是最小字号？`small`/`x-small` 存不存在？）

为什么拆成多张卡：卡 2.0 对**未知字段/非法枚举**是 `200621` **整卡被拒** ⇒ 把有风险的候选各自
单独一张，谁被拒就说明那条路走不通，其它候选照常可见（每张的 code 都打印出来）。

用法： $PY tests/probe_style_candidates.py            # 预览
       $PY tests/probe_style_candidates.py --send     # 逐张真发（每张打印 code）
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
    ap = argparse.ArgumentParser(description="工具行/细节行样式候选（默认预览）")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    cv = load_core()
    TS = cv.PANEL_TEXT_SIZE

    def row_title(content: str, **extra) -> dict:
        node = {"tag": "markdown",
                "icon": {"tag": "standard_icon", "token": "command_outlined", "color": "grey"},
                "content": content, "text_size": TS}
        node.update(extra)
        return node

    def row_detail(content: str, **extra) -> dict:
        node = {"tag": "markdown", "margin": "0px 0px 0px 22px",
                "icon": {"tag": "standard_icon", "token": "tool-indent_outlined", "color": "grey"},
                "content": content, "text_size": TS}
        node.update(extra)
        return node

    def head(t: str) -> dict:
        return {"tag": "markdown", "content": t}

    cards = []

    # 卡 1（低风险）：细节行「更浅」的三档候选（颜色是 content 里的 <font>，未知值一般不会整卡被拒）
    cards.append(("卡1·红框-更浅", [
        head("**红框：细节行「更浅」候选** —— 看哪一行的灰最舒服（① 是现状）"),
        row_detail("<font color='grey'>{\"query\": \"现状：枚举 grey\"}</font>"),
        row_detail("<font color='#B0B0B0'>{\"query\": \"候选A：十六进制 #B0B0B0\"}</font>"),
        row_detail("<font color='#C8C8C8'>{\"query\": \"候选B：十六进制 #C8C8C8\"}</font>"),
        head("（若某行没变化/没出现，说明该写法客户端不认）"),
    ]))

    # 卡 2：绿框「更粗」——粗+斜（markdown 合法，低风险）
    cards.append(("卡2·绿框-更粗候选", [
        head("**绿框：工具名「更粗」候选**（① 是现状 `**x**`）"),
        row_title("**terminal** (168 ms) · <font color='green'>\u2713</font>"),
        row_title("***terminal*** (168 ms) · <font color='green'>\u2713</font>"),
        row_title("__terminal__ (168 ms) · <font color='green'>\u2713</font>"),
        head("（② 是粗+斜 `***x***`：如果看起来更重、斜体也能接受就选它；③ 是另一种粗体写法）"),
    ]))

    # 卡 3（有风险）：更小字号 —— text_size 是**字段**，非法值可能整卡被拒 ⇒ 单独一张
    cards.append(("卡3·红框-更小字号(small)", [
        head("**红框：更小字号候选** —— 这一张试 `text_size: \"small\"`（① 是现状 notation）"),
        row_detail("<font color='grey'>{\"query\": \"现状 notation\"}</font>"),
        row_detail("<font color='grey'>{\"query\": \"候选 small\"}</font>", text_size="small"),
    ]))

    cards.append(("卡4·红框-更小字号(x-small)", [
        head("**红框：更小字号候选** —— 这一张试 `text_size: \"x-small\"`"),
        row_detail("<font color='grey'>{\"query\": \"现状 notation\"}</font>"),
        row_detail("<font color='grey'>{\"query\": \"候选 x-small\"}</font>", text_size="x-small"),
    ]))

    # 卡 5（有风险）：plain_text + text_weight（字段可能不存在 ⇒ 被拒就说明这条路不通）
    cards.append(("卡5·绿框-text_weight", [
        head("**绿框：`plain_text` + `text_weight: \"bold\"` 候选**（若这张没出现，说明该字段不被接受）"),
        {"tag": "div", "icon": {"tag": "standard_icon", "token": "command_outlined",
                                "color": "grey"},
         "text": {"tag": "plain_text", "content": " terminal (168 ms) · \u2713",
                  "text_size": TS, "text_weight": "bold"}},
        head("（对照：上一行是 `plain_text` + `text_weight`；若它没出现或没变化，就说明卡 2 的路线也不通）"),
    ]))

    if not a.send:
        print(f"（预览：共 {len(cards)} 张卡）")
        for name, els in cards:
            print(f"  · {name}: {len(els)} 个元素")
        return 0

    import lark_oapi as lark
    env = P.load_env()
    client = (lark.Client.builder().app_id(env["FEISHU_APP_ID"])
              .app_secret(env["FEISHU_APP_SECRET"]).log_level(lark.LogLevel.ERROR).build())
    for name, els in cards:
        card = {"schema": "2.0", "config": {"streaming_mode": False}, "body": {"elements": els}}
        code, msg, mid = P.send(client, env["FEISHU_HOME_CHANNEL"], card)
        print(f"{name}: code={code} msg={msg!r} mid={mid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
