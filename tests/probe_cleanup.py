"""清理探针残留：列出最近的探针消息，删掉「半成品/孤儿」卡，保留正在用的那张。

判据（只删我们自己的探针消息）：正文里含「基线帧」或「（V1 基线）」这类探针标记，
且不是当前正在使用的那条（由命令行 --keep 指定，默认保留最新一条）。
"""
from __future__ import annotations

import json
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent))

import probe_ck_stream_ops as _base                                   # noqa: E402
from lark_oapi.api.im.v1 import (                                     # noqa: E402
    ListMessageRequest, DeleteMessageRequest,
)

KEEP_DEFAULT = "om_x100b63fd56dbe484c2850d72bcd5f85"


def main(argv: list) -> int:
    keep = argv[argv.index("--keep") + 1] if "--keep" in argv else KEEP_DEFAULT
    client, chat = _base._connect()
    resp = client.im.v1.message.list(
        ListMessageRequest.builder().container_id_type("chat").container_id(chat)
        .sort_type("ByCreateTimeDesc").page_size(20).build())
    items = getattr(getattr(resp, "data", None), "items", None) or []
    print(f"最近 {len(items)} 条消息（保留 {keep}）：")
    for item in items:
        mid = getattr(item, "message_id", "") or ""
        body = str(getattr(item, "body", None) and getattr(item.body, "content", "") or "")
        marker = "（探针）" if ("基线帧" in body or "V1 基线" in body or "探针" in body) else ""
        print(f"  - {mid} {getattr(item, 'msg_type', '')} {marker} {body[:70]!r}")
    if "--delete-orphans" in argv:
        for item in items:
            mid = getattr(item, "message_id", "") or ""
            body = str(getattr(item, "body", None) and getattr(item.body, "content", "") or "")
            if mid == keep or "基线帧" not in body:
                continue
            r = client.im.v1.message.delete(DeleteMessageRequest.builder()
                                           .message_id(mid).build())
            print(f"  删除孤儿 {mid} → code={int(getattr(r, 'code', -1) or 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
