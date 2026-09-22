#!/usr/bin/env python3
"""探针：页脚里的**模型显示名**（用户 2026-09-22：「要模型名，不是 ID」）。

这张卡回答两件事：
  ① 页脚现在长什么样（**生产** `cards.footer_line()` + `context.display_model()` 的逐字输出，
     就画在卡片自己的 footer 位上）；
  ② 各种模型 ID 会显示成什么名字（对照表），以及想改名字时写哪里（配置 `model_aliases`
     或 `~/.hermes/model_aliases.json`，后者与 hermes-fry-cards 同一份文件）。

用法::

    PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
    $PY tests/probe_footer_model.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_render as P  # noqa: E402

_CARDS = P.load_cards()              # `_larkdeck_probe.core.cards`
_ADAPTER, _CONTEXT = P.load_adapter_parts()   # context 挂在同一个合成包下（共享状态）
import importlib as _importlib  # noqa: E402
_cardview = _importlib.import_module("_larkdeck_probe.core.cardview")

#: 本机在用的模型（`~/.hermes/config.yaml`：model.default + fallback_providers）+ 常见 provider 路径
SAMPLES = [
    "deepseek-flash",
    "mimo-v2.5-free",
    "agnes-3.0-flash",
    "deepseek-v4-flash",
    "nvidia/moonshotai/kimi-k3",
    "anthropic/claude-opus-4.8",
    "claude-sonnet-4-5-20250929",
    "openrouter/qwen/qwen-2-5-72b:free",
]


def footer_line(model_id: str) -> str:
    """按**生产口径**渲染页脚：`✅ 已完成 · ⏱ 12.3s · 🤖 显示名 · ctx 55.6k/1m · 5%`。"""
    _CONTEXT.set_aliases({}, spec="")
    return _CARDS.footer_line(
        status="✅ 已完成",
        duration=12.3,
        model=_CONTEXT.display_model(model_id),
        context="ctx 55.6k/1m · 5%",
    ) or ""


def build_card() -> dict:
    rows = "\n".join(
        f"| `{raw}` | **{_CONTEXT.display_model(raw)}** |" for raw in SAMPLES
    )
    current = SAMPLES[0]
    answer = (
        "**页脚现在显示模型名，不再是模型 ID**（你 2026-09-22 的口径）。\n\n"
        f"本机默认模型 `{current}` 的页脚（就是这张卡最下面那行）：\n\n"
        f"> {footer_line(current)}\n\n"
        "各种 ID 会变成什么名字：\n\n"
        "| 原始模型 ID | 页脚显示 |\n| --- | --- |\n"
        f"{rows}\n\n"
        "格式化是**确定性的**（统一大小写 / 版本号 / 参数量 / 去掉尾部日期戳），不改语义。\n"
        "想自定义名字就写别名（两处都行，**别名永远优先于格式化**）：\n\n"
        "* 配置：`model_aliases: \"nvidia/moonshotai/kimi-k3=哈基米\"`（精确匹配）；\n"
        "* 文件：`~/.hermes/model_aliases.json` = `{\"kimi\": \"哈基米\"}`（子串匹配、改文件即生效，"
        "和 `hermes-fry-cards` 共用同一份）。\n\n"
        "请确认：① 页脚那行的模型名读起来顺不顺；② 哪个名字起得不对（告诉我想要的写法）。"
    )
    view = _cardview.CardView(answer=answer, footer=footer_line(current), header_enabled=False)
    card = _cardview.entity_skeleton(view)
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
    card = build_card()
    print(f"页面元素数：{len(card['body']['elements'])}")
    code, msg, mid = P.send(client, chat, card)
    print(f"发送结果: code={code} msg={msg!r} message_id={mid}")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
