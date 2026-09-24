#!/usr/bin/env python3
"""探针（P2 第一步）：验证「三家参考实现共用的那个 loading `img_key`」在本应用能不能用。

为什么必须先探针、而不是直接上传一张 spinner
------------------------------------------------
用户口径是「像 aiduPOP 那样：**一个在动的东西、没有任何文字**」。查源码发现
aiduPOP / CLS / FC 三家**硬编码了同一个 `img_key`**
（`aiduPOP/cardkit/elements.py:103`、`CLS/cardkit/builder.py:23`、`FC/cardkit/builder.py:24`），
三家都**没有** spinner 上传代码（它们的 `upload_image` 是正文远程图用的）。
⇒ 「上传一次资产」是我的错误推断（计划 v1 已被审计 B 纠正）。
正确顺序：**先验证这个 key 在本应用可不可用/会不会动**；可用就是零上传。

本探针做什么
------------
往用户 DM 发**一张**卡，三行对照（同一张卡里，方便一眼比对）：
  ① `custom_icon` + 共享 `img_key` + `text: " "`（目标形态：应该是一个**会动**的图标、无文字）
  ② `standard_icon: time_outlined` + `text: " "`（我们当前形态：静态沙漏、无文字）
  ③ 一行说明文字（告诉用户该看什么）

它不做什么
----------
不启网关、不改插件代码、不写配置。只调 `im.v1.message.create` 发一张卡。

用法::
    PY="${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python3"
    $PY tests/probe_loading.py
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_render as P  # noqa: E402

#: 三家参考实现硬编码的同一个 key（各自源码里都能 grep 到）
SHARED_IMG_KEY = "img_v3_02vb_496bec09-4b43-4773-ad6b-0cdd103cd2bg"


def build_card() -> dict:
    def row(icon: dict, note: str) -> dict:
        return {
            "tag": "div",
            "icon": icon,
            "text": {"tag": "lark_md", "content": note, "text_size": "notation"},
        }

    return {
        "schema": "2.0",
        "config": {"streaming_mode": False},
        "header": {"template": "blue",
                   "title": {"tag": "plain_text", "content": "P2 加载指示探针"}},
        "body": {"elements": [
            row({"tag": "custom_icon", "img_key": SHARED_IMG_KEY, "size": "16px 16px"},
                "① 上面/左边应该是**会动**的加载图标（无文字）——这是 aiduPOP/CLS/FC 共用的 img_key"),
            row({"tag": "standard_icon", "token": "time_outlined",
                 "size": "16px 16px", "color": "grey"},
                "② 上面/左边是**静态沙漏**（我们当前形态，用户说“不是这个”）"),
            {"tag": "markdown",
             "content": "请回一句：**① 会动 / ① 不动 / ① 不显示**（② 作为对照）",
             "text_size": "notation"},
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
    print("请肉眼确认：① 会动 / ① 不动 / ① 不显示")
    return 0 if code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
