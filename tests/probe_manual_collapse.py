#!/usr/bin/env python3
"""P6 真机探针：**手动收起会不会被中间帧顶开**（A1 唯一无法自动化的协议前提）。

为什么必须真机做（审计 A 的中-7）：`partial_update_element` 是**合并**语义还是「整元素替换」，
仓库里查不到权威口径。我们唯一有的证据是 `docs/internal/audits/v0.7.1-visual/window1-live.md:74` 的字段
矩阵（「去 expanded 的载荷 code=0」）—— 那**只证明载荷合法**，不证明合并行为。
A1 的整个「运行中展开」都押在这条上：如果服务端把「没带 expanded」当成「外层默认收起」，
那我们第一帧 partial 就会把用户看到的面板静默收掉。

实验设计（三步，用户各回一句）：
  ``--start``   建卡实体（`panel.expanded=true`）+ 发消息 → 请用户**手动收起**面板；
  ``--frame``   发一帧**生产形态**的 partial（内容变了、**不带 `expanded`**）→ 问「还收着吗？」；
  ``--control`` 再发一帧**带 `expanded: true`** 的 partial → 问「是不是被顶开了？」
                （对照组：如果 control 能顶开、frame 顶不开，就证明「省略 ⇒ 保留」成立）

用法::
    PY="${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python3"
    $PY tests/probe_manual_collapse.py --start     # 发卡（把 card_id 记到 scratch）
    $PY tests/probe_manual_collapse.py --frame     # 用户手动收起之后跑这一条
    $PY tests/probe_manual_collapse.py --control   # 再跑这条（对照）
"""
from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import sys
import time
import types

HERMES_ENV = pathlib.Path.home() / ".hermes" / ".env"
REPO = pathlib.Path(__file__).resolve().parent.parent
STATE = pathlib.Path.home() / ".larkdeck-scratch" / "v0.7.2-panelux" / "manual-collapse.json"


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


def _view(cv, *, expanded: bool, step: int):
    tools = [cv.ToolStepView(name="terminal", title="terminal", duration_ms=347,
                             status="ok", icon_token=cv.ICON_TOKENS["terminal"],
                             detail='{"command": "df -h"}')]
    rounds = [cv.ReasoningRoundView(index=0, text=f"第 {step} 次更新：先读配置。",
                                    elapsed_ms=900, finalized=True)]
    panel = cv.PanelView(title=f"\U0001f4ad 手动收起实验（第 {step} 帧）",
                         tools=tools, reasoning_rounds=rounds, border="green",
                         expanded=expanded)
    return cv.CardView(answer="这是一张**只为做交互实验**的卡：请按说明回复一句就行。",
                       footer="实验卡 · 不属于任何回合", footer_enabled=True, panel=panel,
                       header_enabled=False, header_status="processing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", action="store_true")
    ap.add_argument("--frame", action="store_true")
    ap.add_argument("--control", action="store_true", help="带 expanded=true 的对照帧")
    args = ap.parse_args()
    if not (args.start or args.frame or args.control):
        ap.error("要 --start / --frame / --control 之一")

    import lark_oapi as lark
    from lark_oapi.api.cardkit.v1 import (CreateCardRequest, CreateCardRequestBody,
                                          BatchUpdateCardRequest, BatchUpdateCardRequestBody)
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
    env = load_env()
    cv = load_cardview()
    client = (lark.Client.builder()
              .app_id(env["FEISHU_APP_ID"]).app_secret(env["FEISHU_APP_SECRET"])
              .log_level(lark.LogLevel.ERROR).build())
    STATE.parent.mkdir(parents=True, exist_ok=True)

    if args.start:
        view = _view(cv, expanded=True, step=1)
        card = cv.entity_skeleton(view)
        made = client.cardkit.v1.card.create(
            CreateCardRequest.builder().request_body(
                CreateCardRequestBody.builder().type("card_json")
                .data(json.dumps(card, ensure_ascii=False)).build()).build())
        card_id = getattr(getattr(made, "data", None), "card_id", None)
        print(f"建实体: code={made.code} card_id={card_id} msg={made.msg!r}", flush=True)
        if made.code != 0 or not card_id:
            return 1
        body = (CreateMessageRequestBody.builder().receive_id(env["FEISHU_HOME_CHANNEL"])
                .msg_type("interactive")
                .content(json.dumps({"type": "card", "data": {"card_id": card_id}},
                                    ensure_ascii=False)).build())
        sent = client.im.v1.message.create(
            CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build())
        mid = getattr(getattr(sent, "data", None), "message_id", None)
        print(f"发消息: code={sent.code} message_id={mid} msg={sent.msg!r}", flush=True)
        if sent.code != 0:
            return 1
        STATE.write_text(json.dumps({"card_id": card_id, "message_id": mid, "seq": 0}),
                         encoding="utf-8")
        print("\n请对用户说：**把这张卡的面板手动收起**（点标题行右侧的箭头），收起后回我一句。")
        return 0

    state = json.loads(STATE.read_text(encoding="utf-8"))
    card_id = state["card_id"]
    step = int(state.get("seq") or 0) + 1
    partial = cv.panel_partial(_view(cv, expanded=True, step=step).panel)
    if args.frame:
        partial = {k: v for k, v in partial.items() if k != "expanded"}   # 生产形态：省略
        label = "frame（生产形态：**不带** expanded）"
    else:
        partial["expanded"] = True                                        # 对照：显式 true
        label = "control（对照：**显式带** expanded=true）"
    body = (BatchUpdateCardRequestBody.builder()
            .actions(json.dumps([{"action": "partial_update_element",
                                  "params": {"element_id": "panel",
                                             "partial_element": partial}}], ensure_ascii=False))
            .sequence(100 + step).uuid(f"probe-collapse-{step}-{int(time.time())}").build())
    resp = client.cardkit.v1.card.batch_update(
        BatchUpdateCardRequest.builder().card_id(card_id).request_body(body).build())
    print(f"{label}: code={resp.code} msg={resp.msg!r} keys={sorted(partial)}", flush=True)
    state["seq"] = step
    STATE.write_text(json.dumps(state), encoding="utf-8")
    print("\n请对用户说：面板的标题行文字变了（第 %d 帧）——**它还收着吗**？" % step)
    return 0 if resp.code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
