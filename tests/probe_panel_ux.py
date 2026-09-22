#!/usr/bin/env python3
"""P6 真机探针（**部署后**跑）：v0.7.2「面板 UX 定版」一张多行对照卡。

设计依据：`docs/plan-v0.7.2-panel-ux.md` §8 的十格。全部用**生产构建器**（`core.cardview` /
`core.cards`）拼卡 —— 用户看到的就是生产会画的样子，不是手写 JSON。

用法::
    PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
    $PY tests/probe_panel_ux.py            # 真发一张卡到 FEISHU_HOME_CHANNEL
"""
from __future__ import annotations

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
    cv = importlib.import_module("_larkdeck_probe.core.cardview")
    cds = importlib.import_module("_larkdeck_probe.core.cards")
    return cv, cds


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="P6 面板 UX 探针（**默认只预览，--send 才真发**）")
    ap.add_argument("--send", action="store_true", help="真发到 FEISHU_HOME_CHANNEL")
    a = ap.parse_args()
    import lark_oapi as lark  # noqa: F401  (probe_render 自己建 client)
    cv, cds = load_core()
    if not a.send:
        print("（预览模式，未发送。加 --send 才真发到用户会话）")
    env = P.load_env()
    client = (lark.Client.builder().app_id(env["FEISHU_APP_ID"])
              .app_secret(env["FEISHU_APP_SECRET"]).log_level(lark.LogLevel.ERROR).build())

    def head(text: str) -> dict:
        return {"tag": "markdown", "content": text}

    # ① 工具行三态（运行中 / 成功 / 失败·超时）—— 走真实渲染函数
    tools = [
        cv.ToolStepView(name="terminal", title="terminal", status="running",
                        icon_token=cv.ICON_TOKENS["terminal"]),
        cv.ToolStepView(name="read_file", title="读取文件", status="ok", duration_ms=25,
                        icon_token=cv.ICON_TOKENS["read"]),
        cv.ToolStepView(name="web_search", title="web_search", status="timeout",
                        icon_token=cv.ICON_TOKENS["search"]),
    ]
    panel_rows = cv.panel_shell(cv.PanelView(title="🛠️ 工具执行 · 3 步", tools=tools,
                                             expanded=True, border="grey"))

    # ② 嵌套推理轮：已结束轮折叠 / 当前轮展开（A1）
    rounds_panel = cv.panel_shell(cv.PanelView(
        title="💭 思考 1.6s · 🛠️ 工具执行 · 1 步", expanded=True, border="grey",
        reasoning_rounds=[
            cv.ReasoningRoundView(index=0, text="第一轮已经结束的推理（应该收起来）。",
                                  elapsed_ms=1200, finalized=True),
            cv.ReasoningRoundView(index=1, text="当前还在生成的那一轮（应该展开）。",
                                  finalized=False),
        ],
        tools=[cv.ToolStepView(name="terminal", title="terminal", status="ok",
                               duration_ms=347, icon_token=cv.ICON_TOKENS["terminal"])],
    ))

    # ③ 回合结束的定稿：纯推理收尾后，那一轮必须是**收起**的
    pure_panel = cv.panel_shell(cv.PanelView(
        title="💭 思考 2.4s", expanded=False, border="green",
        reasoning_rounds=[cv.ReasoningRoundView(index=0, text="纯推理回合，结束时已定稿。",
                                                elapsed_ms=2400, finalized=True)]))

    # ④ 页脚三态（走生产 footer_line）
    foot_base = cds.footer_line(status="✅ 已完成", duration=12.3, model="DeepSeek V4.1 Flash",
                                context="ctx 55.6k/1m · 5%")
    foot_basic = cds.footer_line(status="✅ 已完成", duration=12.3, model="DeepSeek V4.1 Flash",
                                 context="ctx 55.6k/1m · 5%", cache=75.0, api=7)
    foot_full = cds.footer_line(status="✅ 已完成", duration=12.3, model="DeepSeek V4.1 Flash",
                                context="ctx 55.6k/1m · 5%", cache=75.0, api=7, ttfb=0.4)

    elements = [
        head("**v0.7.2 面板 UX 探针** —— 每格都请看一眼，回一句结论就行（哪一格不对说哪格）。"),
        {"tag": "hr"},
        head("**① 工具行三态**（运行中=动图 + 蓝 `Running`｜成功=灰图标 + 绿 `✓`｜"
             "超时=红 `Timed out` 保留词）"),
        panel_rows,
        {"tag": "hr"},
        head("**② 嵌套推理轮**：已结束轮**收起**、当前轮**展开**"),
        rounds_panel,
        {"tag": "hr"},
        head("**③ 回合结束的定稿**：纯推理回合收尾后，那一轮应该是**收起**的"),
        pure_panel,
        {"tag": "hr"},
        head(f"**④ 页脚三态**（都**没有**段前缀 emoji、没有短码）\n"
             f"* 基础：{foot_base}\n"
             f"* `basic`：{foot_basic}\n"
             f"* `full`：{foot_full}"),
        {"tag": "hr"},
        head("**⑤ 需要你手动验的两条**（各自单独一张卡）：\n"
             "1. 上一张「手动收起实验」卡：手动收起面板后，我们发一帧**不带 `expanded`** 的更新 —— "
             "**它应该保持收起**（如果被顶开，A1 的整个方向要改）；\n"
             "2. 内层轮（第 ② 块里那个「第 1 轮」）：手动点开它，等下一帧过来 —— "
             "**它会被收回**（这是我们已知并登记 v0.7.3 的限制，只想确认真机行为一致）。"),
        head("**⑥ 附加说明**：`native_transport: patch` 在 structured 引擎下**无效**"
             "（不会真的切回旧路径）—— 想回退请 revert 到 v0.7.0。"),
    ]
    # §8-5 动图对照（我们自研的那张 vs 借来的回落那张）—— 单列一行，一眼比对
    row0 = [{"tag": "div", "icon": {"tag": "custom_icon", "img_key": cv.spinner_img_key(),
                                    "size": "16px 16px"},
             "text": {"tag": "plain_text", "content": " ① 我们的（自研）"}},
            {"tag": "div", "icon": {"tag": "custom_icon", "img_key": cv.SPINNER_IMG_KEY,
                                    "size": "16px 16px"},
             "text": {"tag": "plain_text", "content": " ② 原来的（借来的，只作回落）"}}]
    elements[1:1] = [head("**⓪ 动图对照**：① 我们自研上传的那张（生产生效）｜② 原来借来那张（现在只当回落）")] + row0
    card = {"schema": "2.0", "config": {"streaming_mode": False}, "body": {"elements": elements}}
    if not a.send:
        print(f"卡片已构建（{len(elements)} 个元素，含 §8 各格）—— 未发送")
        return 0
    code, msg, mid = P.send(client, env["FEISHU_HOME_CHANNEL"], card)
    print(f"发送结果: code={code} msg={msg!r} message_id={mid}")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
