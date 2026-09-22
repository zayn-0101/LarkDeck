#!/usr/bin/env python3
"""v0.7.3 探针：`text_size: "x-small"` 的宿主矩阵（三 host × 三主题，同卡 notation 对照）
+ N1/N2 生产卡（已知系统提示静默 / 真实回合必须有 ✅）。

生产元素来源：
  * 细节行 line 模式 = `markdown`（`_tool_detail_div(..., "line")`）；
  * 细节行 emoji 模式 = `div.text` + `plain_text`；
  * Error/Result 块 = `div.text` + `lark_md`（真实 20 行栈 + 超长行）。
每 host 一张独立卡，卡内一半 notation 对照、一半生产 x-small；逐张打印 `code`。主题维度只改
符号、不改 host，但按要求每主题复跑并在卡头标注。

用法： $PY tests/probe_text_size_hosts.py            # 预览
       $PY tests/probe_text_size_hosts.py --send     # 3 host × 3 theme + N1/N2 逐张真发
任何一张 `code!=0` ⇒ 退出码非 0（宿主矩阵不许「打印完算过」）。
"""
from __future__ import annotations

import argparse
import copy
import importlib
import json
import pathlib
import sys
import time
import types

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
import probe_render as P  # noqa: E402

THEMES = ("ap_lite", "neutral", "ap_bubble")


def load_core():
    pkg = types.ModuleType("_larkdeck_probe")
    pkg.__path__ = [str(REPO)]
    sys.modules["_larkdeck_probe"] = pkg
    return importlib.import_module("_larkdeck_probe.core.cardview")


def _production_cards():
    """N1/N2：走**仓库内**生产 `_ld_render_card` 的系统提示静默卡 / 真回合 ✅ 卡。

    为什么不用 ``_load_adapter_for_probe``：那会经 Hermes 插件加载器拿到**已安装**的
    larkdeck（P1 阶段仍是 v0.7.2 的 `.deploy`）—— N1/N2 就会验到旧代码。这里用
    `probe_render.load_adapter_parts()` 加载**当前 checkout** 的 `core.adapter`。
    """
    ad_mod, _ = P.load_adapter_parts()
    if not callable(getattr(ad_mod.LarkDeckMixin, "_ld_render_card", None)):
        print("⚠️ N1/N2 跳过：checkout 内适配器缺少 _ld_render_card")
        return []
    ad_mod.configure(visual_engine="structured", unified_panel=True,
                     footer=True, show_model=True, card_status_header=True)
    obj = object.__new__(ad_mod.LarkDeckMixin)
    render = ad_mod.LarkDeckMixin._ld_render_card
    panel_of = ad_mod.LarkDeckMixin._ld_panel
    footer_of = ad_mod.LarkDeckMixin._ld_footer
    n1 = render(obj, "probe-v073", "♻️ Gateway online — Hermes is back and ready.",
                streaming=False, status="completed", panel=None, footer=None,
                turn_card=False)
    n1_blob = json.dumps(n1, ensure_ascii=False)
    assert "footer" not in n1_blob and "collapsible_panel" not in n1_blob, n1
    assert "header" not in n1, n1
    now = time.monotonic()
    n2 = render(obj, "probe-v073", "真实回合回答（N2）",
                streaming=False, status="completed",
                panel=panel_of("probe-v073", report_empty=True),
                footer=footer_of(chat_id="probe-v073", status="completed",
                                 started=now - 3.2, turn_card=True),
                started=now - 3.2, turn_card=True)
    n2_blob = json.dumps(n2, ensure_ascii=False)
    assert "✅ 已完成" in n2_blob, n2
    return [("N1·系统提示（无 footer/面板/状态头）", n1["body"]["elements"]),
            ("N2·真实回合（必须有 ✅）", n2["body"]["elements"])]


def _as_notation(node: dict) -> dict:
    out = copy.deepcopy(node)
    if "text_size" in out:
        out["text_size"] = "notation"
    if isinstance(out.get("text"), dict):
        out["text"]["text_size"] = "notation"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="x-small 宿主矩阵（默认预览）")
    ap.add_argument("--send", action="store_true")
    a = ap.parse_args()
    cv = load_core()

    long_stack = "\n".join(
        [f'Traceback (most recent call last): File "task.py", line {i}, in run'
         for i in range(1, 21)]
        + ["RuntimeError: " + ("x" * 160) + " — end of a deliberately long line"])

    def head(t: str) -> dict:
        return {"tag": "markdown", "content": t}

    def cards_for(theme: str):
        line_detail = cv._tool_detail_div('{"command": "df -h"}', "line")
        emoji_detail = cv._tool_detail_div('{"query": "emoji host"}', "emoji")
        error_block = cv._tool_output_div(long_stack, "Error", "line")
        return [
            (f"A·markdown(line)@{theme}", [
                head(f"**宿主 A：`markdown`（theme={theme}）** ① notation 对照 / ② 生产 x-small"),
                _as_notation(line_detail), line_detail,
            ]),
            (f"B·plain_text(emoji)@{theme}", [
                head(f"**宿主 B：`div.text=plain_text`（theme={theme}）** ① notation / ② x-small"),
                _as_notation(emoji_detail), emoji_detail,
            ]),
            (f"C·lark_md(Error 长栈)@{theme}", [
                head(f"**宿主 C：`div.text=lark_md`（theme={theme}）** ① notation / ② x-small；"
                     "看 20+ 行栈与超长行是否仍可读、不溢出"),
                _as_notation(error_block), error_block,
            ]),
        ]

    cards = [c for theme in THEMES for c in cards_for(theme)]
    cards += _production_cards()

    if not a.send:
        print(f"（预览：共 {len(cards)} 张卡 = 3 host × {len(THEMES)} theme + N1/N2）")
        for name, els in cards:
            print(f"  · {name}: {len(els)} 个元素")
        return 0

    import lark_oapi as lark
    env = P.load_env()
    client = (lark.Client.builder().app_id(env["FEISHU_APP_ID"])
              .app_secret(env["FEISHU_APP_SECRET"]).log_level(lark.LogLevel.ERROR).build())
    failed = []
    print(f"逐张发送 {len(cards)} 张卡。请记录：客户端主题（ap_lite/neutral/ap_bubble）、设备；"
          "A/B/C 看 x-small 是否更小且不溢出、Error 是否可读；N1 是否有面板/状态头/页脚、"
          "N2 是否仍有 ✅ 已完成。")
    for name, els in cards:
        card = {"schema": "2.0", "config": {"streaming_mode": False}, "body": {"elements": els}}
        code, msg, mid = P.send(client, env["FEISHU_HOME_CHANNEL"], card)
        print(f"{name}: code={code} msg={msg!r} mid={mid}")
        if code != 0:
            failed.append(name)
            print(f"  ⚠️ 宿主探针被拒：{name} ⇒ 该 host 退回 notation，并如实记录")
    if failed:
        print("被拒清单：", failed)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
