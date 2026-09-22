#!/usr/bin/env python3
"""一次性上传卡片图片资产（拿到 `img_key` 后写进代码常量）。

为什么要单独一个脚本：卡片里的 `custom_icon` 只认**本 app 上传**的图片（别人的 key 我们
下载不了、也随时可能失效：`234008 The app is not the resource sender`）。上传是**一次性**动作，
之后每张卡只是引用 `img_key`，**不产生每回合的额外 API 调用**。

用法::

    PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
    $PY tools/upload_card_asset.py assets/spinner-tool.gif          # 打印 image_key
    $PY tools/upload_card_asset.py assets/spinner-tool.gif --json    # 机器可读

凭据来源：`~/.hermes/.env` 的 `FEISHU_APP_ID/FEISHU_APP_SECRET`（与插件运行时同一份），
可用环境变量覆盖。失败时**非零退出**（P2 出口判据要求"上传失败=红"，不许静默回落）。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys


def _load_env() -> dict:
    """先读进程环境，再用 `~/.hermes/.env` 补缺（只认 KEY=VALUE 行）。"""
    env = dict(os.environ)
    home = pathlib.Path(os.environ.get("HERMES_HOME") or (pathlib.Path.home() / ".hermes"))
    try:
        for line in (home / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key and key not in env:
                env[key] = value
    except OSError:
        pass
    return env


def main() -> int:
    ap = argparse.ArgumentParser(description="上传卡片图片资产（im/v1/images, image_type=message）")
    ap.add_argument("file", help="图片/GIF 路径")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    path = pathlib.Path(args.file)
    if not path.is_file():
        print(f"[FAIL] 文件不存在：{path}", file=sys.stderr)
        return 2
    env = _load_env()
    app_id = env.get("FEISHU_APP_ID") or env.get("LARK_APP_ID") or ""
    app_secret = env.get("FEISHU_APP_SECRET") or env.get("LARK_APP_SECRET") or ""
    if not app_id or not app_secret:
        print("[FAIL] 缺少 FEISHU_APP_ID / FEISHU_APP_SECRET（~/.hermes/.env）", file=sys.stderr)
        return 2

    import lark_oapi as lark
    from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody
    client = (lark.Client.builder().app_id(app_id).app_secret(app_secret)
              .log_level(lark.LogLevel.ERROR).build())
    with open(path, "rb") as fh:
        body = CreateImageRequestBody.builder().image_type("message").image(fh).build()
        resp = client.im.v1.image.create(CreateImageRequest.builder().request_body(body).build())
    if resp.code != 0 or resp.data is None:
        print(f"[FAIL] 上传失败 code={resp.code} msg={resp.msg}", file=sys.stderr)
        return 1
    key = resp.data.image_key
    if args.json:
        print(json.dumps({"file": str(path), "image_key": key, "code": resp.code},
                         ensure_ascii=False))
    else:
        print(f"上传成功：{path} → image_key = {key}")
        print("（把 key 写进 core/cardview.py 的常量；之后不要再换图：换图=换 key=夹具变=再全量）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
