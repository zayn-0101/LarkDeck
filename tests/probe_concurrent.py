#!/usr/bin/env python3
"""诊断探针：同卡片**并发** batch_update 的码表（只建卡实体，不发消息）。

生产疑点（2026-09-21 10:40:01 · WARNING code=200770 · panel）：
面板（element_id=``panel``）是**唯一**会被两条路径写的元素 —— 帧路径
（``_ld_stream_frame_structured``）与心跳（``_ld_heartbeat_loop``）。两条路径都从同一份
``state`` 里算 ``seq = _ck_seq(state) + 1``，而两次写入之间隔着 ``await``
⇒ 同一张卡上可能几乎同时出现两份 (seq, uuid) 相同的写。

本探针把三种冲突各打一轮，看飞书分别给什么码：
  * A 同 seq + 同 uuid + 不同内容（最像生产的那一种）
  * B 同 seq + 不同 uuid
  * C 不同 seq + 不同 uuid（纯并发，做对照）

它不做什么
----------
不发消息给任何人：只 ``cardkit.v1.card.create`` 建实体，跑完打印码表。

用法::
    PY="${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python3"
    $PY tests/probe_concurrent.py
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor

import probe_partial as pp


def main() -> int:
    import lark_oapi as lark
    from lark_oapi.api.cardkit.v1 import (BatchUpdateCardRequest,
                                          BatchUpdateCardRequestBody,
                                          CreateCardRequest, CreateCardRequestBody)
    env = pp.load_env()
    cv = pp.load_cardview()
    client = (lark.Client.builder()
              .app_id(env["FEISHU_APP_ID"])
              .app_secret(env["FEISHU_APP_SECRET"])
              .log_level(lark.LogLevel.ERROR)
              .build())
    view = pp.build_view(cv)
    card = cv.entity_skeleton(view)
    made = client.cardkit.v1.card.create(
        CreateCardRequest.builder().request_body(
            CreateCardRequestBody.builder().type("card_json")
            .data(json.dumps(card, ensure_ascii=False)).build()).build())
    card_id = getattr(getattr(made, "data", None), "card_id", None)
    print(f"建实体: code={made.code} card_id={card_id}", flush=True)
    if made.code != 0 or not card_id:
        return 1

    full = cv.panel_partial(view.panel)
    variants = [("A 同 seq 同 uuid 不同内容", True, True),
                ("B 同 seq 不同 uuid", True, False),
                ("C 不同 seq 不同 uuid（对照）", False, False)]
    ok_all = True
    for index, (name, same_seq, same_uuid) in enumerate(variants):
        seq_base = 500 + index * 10

        def one(tag: int) -> tuple:
            seq = seq_base if same_seq else seq_base + tag
            uuid_value = (f"probe-conc-{index}-{seq}" if same_uuid
                          else f"probe-conc-{index}-{seq}-{tag}")
            partial = dict(full)
            partial["border"] = dict(partial["border"], color=("green" if tag == 0 else "grey"))
            body = (BatchUpdateCardRequestBody.builder()
                    .actions(json.dumps([{"action": "partial_update_element",
                                          "params": {"element_id": "panel",
                                                     "partial_element": partial}}],
                                        ensure_ascii=False))
                    .sequence(seq).uuid(uuid_value).build())
            resp = client.cardkit.v1.card.batch_update(
                BatchUpdateCardRequest.builder().card_id(card_id)
                .request_body(body).build())
            return tag, resp.code, str(resp.msg or "")

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(one, range(4)))
        codes = [r[1] for r in results]
        if any(c != 0 for c in codes):
            ok_all = False
        print(f"{name}: codes={codes} "
              f"msgs={[r[2] for r in results if r[1]]}", flush=True)
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
