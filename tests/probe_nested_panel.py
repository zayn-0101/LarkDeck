#!/usr/bin/env python3
"""探针：**嵌套 `collapsible_panel`** 在真机上到底渲不渲染（关闭一条残余风险）。

为什么需要它
------------
生产代码在 `show_reasoning=true` 时会把「推理轮」渲染成**外层面板里再套一层**
`collapsible_panel`（外：`💭 思考 … · 🛠️ 工具执行 · N 步`；内：`💭 思考 · 1 · 1.2s`，
每个推理轮一个）。仓库自己的 `docs/audits/v0.7.1-visual/plan-consensus.md:111` 把
「嵌套 collapsible_panel 客户端渲染」列为**未验证**；两个参考实现（CLS/FC）是把面板
**平铺在 body 顶层**的，所以「嵌套」这件事没有先例可抄。

风险形态：若客户端**不渲染内层**（或整卡被拒），用户开 `show_reasoning=true` 的回合里
要么看不到推理轮、要么整张卡不渲染 —— 而默认 `show_reasoning=false` 恰好规避了它，
所以线上一直没暴露。

这张卡做什么
------------
用**生产渲染器**（`core/cardview.entity_skeleton`）渲染一棵真实的两层结构（外层面板 +
2 个推理轮内层面板 + 1 行工具），发到用户 DM，请用户回一句：
「**内层能展开吗**（能不能看到 `思考 · 1 · 1.2s` / `思考 · 2 · 0.8s` 两行）」。

用法::

    PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
    $PY tests/probe_nested_panel.py
"""
from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_render as P  # noqa: E402

P.load_cards()
_cv = importlib.import_module("_larkdeck_probe.core.cardview")


def build_card() -> dict:
    view = _cv.CardView(
        answer="**嵌套面板探针**：下面这块面板里，应该还能展开两个**内层**推理面板。\n"
               "请回一句：**内层能展开吗**？（能不能看到 `思考 · 1 · 1.2s` / `思考 · 2 · 0.8s` 两行）\n"
               "如果内层看不见、或这张卡根本没显示出来，也请告诉我。",
        panel=_cv.PanelView(
            title="💭 思考 2.0s · 🛠️ 工具执行 · 1 步",
            reasoning_rounds=[
                _cv.ReasoningRoundView(index=0, text="第 1 轮推理正文（内层面板 1）", elapsed_ms=1200),
                _cv.ReasoningRoundView(index=1, text="第 2 轮推理正文（内层面板 2）", elapsed_ms=800),
            ],
            tools=[_cv.ToolStepView(name="terminal", title="terminal", status="ok",
                                    duration_ms=120, icon_token="setting_outlined")],
            border="grey",
        ),
        header_enabled=False,
    )
    card = _cv.entity_skeleton(view)
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
