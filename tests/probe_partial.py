#!/usr/bin/env python3
"""诊断探针：`partial_update_element` 的**字段级**合法性矩阵（只建卡实体，不发消息）。

为什么需要它
------------
2026-09-21 真机 structured canary 首次报出 ``code=200770``（装饰写入失败 · panel），
而这个码**不在已知码表里**（300309 流式已关 / 300313 元素不存在 / 300317 序号错 /
300315 容量 / 230020 频控）。日志里只留了码、没留 ``msg``，所以「哪个字段不合法」
读源码读不出来。本探针把面板 partial 拆成**逐个字段**打给飞书，看**哪一个**把它拒了。

它不做什么
----------
**不发消息给任何人**：只调 ``cardkit.v1.card.create`` 建卡片实体（不投递），
跑完矩阵在本地打印码表。会话里一个气泡都不会多。

用法::
    PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
    $PY tests/probe_partial.py
"""
from __future__ import annotations

import importlib
import json
import pathlib
import sys
import time
import types

HERMES_ENV = pathlib.Path.home() / ".hermes" / ".env"
REPO = pathlib.Path(__file__).resolve().parent.parent


def load_env() -> dict:
    values = {}
    if HERMES_ENV.exists():
        for line in HERMES_ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    return values


def load_cardview():
    pkg = types.ModuleType("_larkdeck_probe")
    pkg.__path__ = [str(REPO)]
    sys.modules["_larkdeck_probe"] = pkg
    return importlib.import_module("_larkdeck_probe.core.cardview")


def build_view(cv):
    rounds = [cv.ReasoningRoundView(index=0, text="先读配置，再确认磁盘占用。",
                                    elapsed_ms=900)]
    tools = []
    for _ in range(3):
        tools.append(cv.ToolStepView(
            name="terminal", title="terminal", duration_ms=347, status="ok", icon_token=cv.ICON_TOKENS["terminal"],
            detail='{"command": "df -h"}', result_block="Filesystem Size Used\n/dev/disk 460G",
        ))
    panel = cv.PanelView(
        title="\U0001f4ad 思考 2.9s · \U0001f6e0\ufe0f 工具执行 · 3 步",
        tools=tools, reasoning_rounds=rounds, collapsed_hint="", border="green")
    return cv.CardView(answer="已跑完，结果如下：", footer="\u2705 已完成 · \U0001f9e0 model",
                       footer_enabled=True, panel=panel, header_enabled=True,
                       header_status="completed", header_title="\u2705 已完成")


def main() -> int:
    import lark_oapi as lark
    from lark_oapi.api.cardkit.v1 import (CreateCardRequest, CreateCardRequestBody,
                                          BatchUpdateCardRequest, BatchUpdateCardRequestBody)
    env = load_env()
    cv = load_cardview()
    client = (lark.Client.builder()
              .app_id(env["FEISHU_APP_ID"])
              .app_secret(env["FEISHU_APP_SECRET"])
              .log_level(lark.LogLevel.ERROR)
              .build())
    view = build_view(cv)
    card = cv.entity_skeleton(view)
    req = (CreateCardRequest.builder()
           .request_body(CreateCardRequestBody.builder()
                         .type("card_json")
                         .data(json.dumps(card, ensure_ascii=False)).build())
           .build())
    made = client.cardkit.v1.card.create(req)
    card_id = getattr(getattr(made, "data", None), "card_id", None)
    print(f"建实体: code={made.code} card_id={card_id} msg={made.msg}", flush=True)
    if made.code != 0 or not card_id:
        return 1

    full = cv.panel_partial(view.panel)
    cases = [("1 生产原样 panel_partial", full),
             ("2 只 expanded", {"expanded": True}),
             ("3 只 header", {"header": full["header"]}),
             ("4 只 elements", {"elements": full["elements"]}),
             ("5 只 vertical_spacing", {"vertical_spacing": full["vertical_spacing"]}),
             ("6 只 border", {"border": full["border"]}),
             ("7 去 vertical_spacing",
              {k: v for k, v in full.items() if k != "vertical_spacing"}),
             ("8 去 border", {k: v for k, v in full.items() if k != "border"}),
             ("9 去 expanded", {k: v for k, v in full.items() if k != "expanded"}),
             ("10 空 partial", {}),
             ("11 幽灵元素(对照)", full),
             ("12 只工具标题 div", {"elements": full["elements"][:1]}),
             ("13 只嵌套推理面板", {"elements": full["elements"][1:2]})]

    seq = 0
    for idx, (name, partial) in enumerate(cases):
        element_id = "ghost_missing_element" if name.startswith("11") else "panel"
        seq += 1
        body = (BatchUpdateCardRequestBody.builder()
                .actions(json.dumps([{"action": "partial_update_element",
                           "params": {"element_id": element_id,
                                      "partial_element": partial}}],
                                     ensure_ascii=False))
                .sequence(seq)
                .uuid(f"probe-partial-{idx}-{int(time.time())}")
                .build())
        resp = client.cardkit.v1.card.batch_update(
            BatchUpdateCardRequest.builder().card_id(card_id).request_body(body).build())
        payload = json.dumps(partial, ensure_ascii=False)
        print(f"{name}: code={resp.code} msg={resp.msg!r} bytes={len(payload)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
