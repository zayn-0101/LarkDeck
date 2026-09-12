#!/usr/bin/env python3
"""渲染探针：把 larkdeck 的真实卡片发到自己的飞书 DM，肉眼确认渲染结果。

为什么需要它
------------
卡片 JSON 的**合法性只能由飞书判定**，本地读源码读不出来。方言混用、漏字段、
元素不支持 —— 这些都只在 API 返回值里才现形。所以每改一次卡片结构，跑一遍这个探针。

它做什么
--------
1. 从 ``~/.hermes/.env`` 读飞书凭据（只读，不回显）
2. **先删掉自己上次发的探针卡**——按记录的消息 ID 删，再用内容里的 ``__PROBE__``
   标记补扫。**2.0 流式卡在飞书里是 cardkit 实体，读回来只剩一句「请升级至最新
   版本客户端，以查看内容」**，标记根本读不到，所以消息 ID 才是唯一靠得住的凭据
3. 用 ``lark_oapi`` 以 ``im/v1/messages`` 发出**三类**卡片
4. 打印每张卡的 API 返回码 —— **code != 0 就是卡片被拒，msg 是飞书给的原因**

它不做什么
----------
不启动 Hermes、不加载插件、不碰 HFC、不改任何配置。**按钮点击不会被处理**
（点击路由仍在 HFC 手里），本探针**只验渲染**。

用法::

    PY=/Users/Zayn/.hermes/hermes-agent/venv/bin/python3
    $PY tests/probe_render.py             # 清理旧探针卡 → 发新卡
    $PY tests/probe_render.py --no-clean  # 不清理，直接发（排查清理逻辑时用）
    $PY tests/probe_render.py --clean-only
"""
from __future__ import annotations

import importlib
import json
import os
import pathlib
import sys
import time
import types

HERMES_ENV = pathlib.Path.home() / ".hermes" / ".env"
REPO = pathlib.Path(__file__).resolve().parent.parent
#: 2.0 卡的脚注走 markdown，``__x__`` 会被当成加粗吃掉，所以标记不用下划线。
PROBE_MARK = "LARKDECK-RENDER-PROBE"
#: 记下自己发过的消息 ID。**只能靠它清理 2.0 流式卡**，见 clean_previous。
STATE_FILE = REPO / ".probe_state.json"

#: 清理时用来认出「这是探针发的卡」。多写几个，好把加标记之前留下的旧卡也一并清掉。
PROBE_MARKERS = (
    PROBE_MARK,
    "__LARKDECK_RENDER_PROBE__",  # 改标记之前发的旧卡
    "larkdeck 渲染探针",
    "这张卡渲染正常吗？",  # 更早期没打标记的版本，靠题面认
)


def load_env() -> dict:
    """从 ~/.hermes/.env 读凭据；已存在的环境变量优先。"""
    values = {}
    if HERMES_ENV.exists():
        for line in HERMES_ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_HOME_CHANNEL"):
        if os.environ.get(k):
            values[k] = os.environ[k]
    missing = [k for k in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_HOME_CHANNEL")
               if not values.get(k)]
    if missing:
        sys.exit(f"缺少凭据: {', '.join(missing)}（应在 ~/.hermes/.env 里）")
    return values


def load_cards():
    """把 cards.py 当包内模块加载，绕开 __init__.py（它会拉 Hermes 依赖）。"""
    pkg = types.ModuleType("_larkdeck_probe")
    pkg.__path__ = [str(REPO)]
    sys.modules["_larkdeck_probe"] = pkg
    return importlib.import_module("_larkdeck_probe.cards")


def _load_sent_ids() -> list:
    try:
        ids = json.loads(STATE_FILE.read_text())
        return ids if isinstance(ids, list) else []
    except Exception:
        return []


def _save_sent_ids(ids) -> None:
    STATE_FILE.write_text(json.dumps(sorted({str(i) for i in ids}), indent=0) + "\n")


def clean_previous(client, chat: str) -> int:
    """删掉本机器人此前发的探针消息，返回删除条数。

    **两条路并用**：先按「自己记下的消息 ID」删，再按内容标记补扫。

    为什么不能只靠标记：2.0 流式卡在飞书里存成 cardkit 实体，之后用 API 读回来
    只剩一句「请升级至最新版本客户端，以查看内容」——``__PROBE__`` 标记根本读不到，
    于是这批卡永远清不掉，在 DM 里越积越多。ID 是唯一记得住它们的凭据。
    """
    from lark_oapi.api.im.v1 import (ListMessageRequest, DeleteMessageRequest)

    removed = 0
    leftover = []
    for mid in _load_sent_ids():
        d = client.im.v1.message.delete(
            DeleteMessageRequest.builder().message_id(mid).build())
        if d.code == 0:
            removed += 1
        else:
            leftover.append(mid)
    if leftover:
        print(f"  ⚠️  {len(leftover)} 条按 ID 删除失败（多为已过期），放弃记账")
    _save_sent_ids([])

    start = str(int(time.time()) - 7 * 24 * 3600)
    req = (ListMessageRequest.builder()
           .container_id_type("chat")
           .container_id(chat)
           .start_time(start)
           .page_size(50)
           .sort_type("ByCreateTimeDesc")
           .build())
    resp = client.im.v1.message.list(req)
    if resp.code != 0:
        print(f"  ⚠️  列出历史消息失败 code={resp.code} msg={resp.msg}（跳过补扫）")
        return removed
    for item in (resp.data.items or []):
        content = (item.body.content if item.body else "") or ""
        if not any(m in content for m in PROBE_MARKERS):
            continue
        d = client.im.v1.message.delete(
            DeleteMessageRequest.builder().message_id(item.message_id).build())
        if d.code == 0:
            removed += 1
        else:
            print(f"  ⚠️  补扫删除 {item.message_id} 失败 code={d.code} msg={d.msg}")
    return removed


def send(client, chat: str, card: dict) -> tuple:
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
    body = (CreateMessageRequestBody.builder()
            .receive_id(chat)
            .msg_type("interactive")
            .content(json.dumps(card, ensure_ascii=False))
            .build())
    req = (CreateMessageRequest.builder()
           .receive_id_type("chat_id")
           .request_body(body)
           .build())
    resp = client.im.v1.message.create(req)
    return resp.code, resp.msg, (resp.data.message_id if resp.data else None)


def build_cases(cards) -> list:
    mark = f"{PROBE_MARK} · larkdeck 渲染探针 · 只验渲染，不处理点击"
    panel_reasoning = ("用户问的是卡片渲染。核验三点：\n"
                       "1. 1.0 方言只带顶层 elements\n"
                       "2. 按钮必须在 action 容器里\n"
                       "3. 2.0 卡必须有 config.summary")
    panel_tools = ["read_file(cards.py)", "grep(render.py)", "terminal(probe_render.py)"]
    panel = cards.unified_panel(reasoning=panel_reasoning, tools=panel_tools,
                                expanded=False)
    panel_open = cards.unified_panel(reasoning=panel_reasoning, tools=panel_tools,
                                     expanded=True)

    def tag_legacy(card: dict) -> dict:
        """给 1.0 卡也打上清理标记，否则下次清理漏掉它们。"""
        card["elements"].append(cards.note(mark))
        return card

    return [
        ("① 澄清卡（legacy 1.0，应有 3 个按钮）",
         tag_legacy(cards.clarify_card("这张卡渲染正常吗？按钮能看见吗？",
                                       ["一切正常", "按钮没出来", "排版乱了"],
                                       clarify_id="probe-cid", session_key="probe-sk"))),
        ("② 已答复卡（legacy 1.0，点击后的回填态）",
         tag_legacy(cards.clarify_resolved_card(
             question="这张卡渲染正常吗？按钮能看见吗？",
             answer="一切正常", user_name="汪老师"))),
        ("③ 回复卡 + 面板【收起】← 点标题行右边的三角箭头应能展开",
         cards.reply_card("这张卡的面板是收起态。标题行右边应该有个三角箭头，点一下能展开。",
                          streaming=True, panel=panel, footer=mark)),
        ("④ 回复卡 + 面板【展开】（对照组，只应比 ③ 多展开状态）",
         cards.reply_card("这张卡的面板是展开态，用来和 ③ 对照。",
                          streaming=True, panel=panel_open, footer=mark)),
    ]


def main(argv: list) -> int:
    clean_only = "--clean-only" in argv
    do_clean = "--no-clean" not in argv

    env = load_env()
    cards = load_cards()

    import lark_oapi as lark
    client = (lark.Client.builder()
              .app_id(env["FEISHU_APP_ID"])
              .app_secret(env["FEISHU_APP_SECRET"])
              .log_level(lark.LogLevel.ERROR)
              .build())
    chat = env["FEISHU_HOME_CHANNEL"]

    if do_clean:
        n = clean_previous(client, chat)
        print(f"清理旧探针卡: {n} 条")
    if clean_only:
        return 0

    cases = build_cases(cards)
    ok = True
    for label, card in cases:
        code, msg, mid = send(client, chat, card)
        dialect = card.get("schema", "1.0(legacy)")
        status = "✅" if code == 0 else "❌"
        if code != 0:
            ok = False
        print(f"{status} {label}")
        print(f"     方言={dialect}  code={code}  msg={msg}  id={mid}")
        if code != 0:
            one_line = json.dumps(card, ensure_ascii=False)
            print(f"     被拒卡片({len(one_line)} 字符): {one_line[:900]}")
        else:
            _save_sent_ids(_load_sent_ids() + [mid])

    print()
    print("全部被飞书接收 ✅ —— 去飞书 DM 看四张卡长什么样（③ 记得点一下三角箭头）" if ok
          else "有卡片被拒 ❌ —— 按上面飞书给的 msg 改")
    print("注意：按钮点击不会被处理（点击路由目前还在 HFC 手里），本探针只验渲染。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
