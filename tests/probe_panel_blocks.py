"""真机探针：**面板两块的实际观感**（R3 收窄版）—— 发一张卡到自己的 DM，用眼睛看。

## 它回答什么（只有眼睛能判的那一半）

R3 收窄版把实体卡的面板从「一个 markdown」拆成**两个**：`panel_body`（推理轮）+
`panel_tools`（工具行列表）。接口层面全 `code=0` **回答不了**「客户端画出来是什么样」：
两个相邻 markdown 的**间距**、面板里**有没有多出空行**、标题「执行详情」在不在内容**上面** ——
这三件事只能看图。本探针用**生产构造器**（`cards.cardkit_entity_card` + 两个 markdown 纯函数）
造卡，与线上跑的是同一段代码；建卡时就把面板**展开**（线上默认收起，展开只为省你一次点击）。

## 用法

```
python3 tests/probe_panel_blocks.py            # 发一张卡并**保留**
python3 tests/probe_panel_blocks.py --delete   # 按 message_id 删掉上次那张（不盲删）
```

⚠️ 清理只按**自己账本里的 message_id**（`.probe_panel_blocks.json`），绝不按时间窗删。
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent))

from probe_ck_stream_ops import _connect                        # noqa: E402
from lark_oapi.api.cardkit.v1 import (                          # noqa: E402
    ContentCardElementRequest, ContentCardElementRequestBody,
    CreateCardRequest, CreateCardRequestBody,
    SettingsCardRequest, SettingsCardRequestBody,
)
from lark_oapi.api.im.v1 import (CreateMessageRequest,          # noqa: E402
                                 CreateMessageRequestBody, DeleteMessageRequest)
from larkdeck.core import cards                                 # noqa: E402

_STATE = _HERE.parent / ".probe_panel_blocks.json"

ROUNDS = [
    {"text": "先把问题拆成三步：看数据结构、看调用点、再看失败路径。", "elapsed_ms": 1200},
    {"text": "现在只看调用点，先不动失败路径。", "elapsed_ms": 800},
]
TOOLS = [
    cards.tool_step("read_file", status="ok", duration_ms=2300,
                    preview='{"path": "/tmp/r3.txt"}'),
    cards.tool_step("bash", status="ok", duration_ms=420, preview='{"command": "ls -la"}'),
    cards.tool_step("grep", status="running", preview='{"pattern": "panel_tools"}'),
]


def main(argv) -> int:
    if "--delete" in argv:
        if not _STATE.exists():
            print("没有上次的账本 ⇒ 没什么可删的。")
            return 0
        mid = json.loads(_STATE.read_text(encoding="utf-8"))["message_id"]
        client, _ = _connect()
        d = client.im.v1.message.delete(DeleteMessageRequest.builder().message_id(mid).build())
        print(f"   删 {mid} → code={d.code}")
        _STATE.unlink(missing_ok=True)
        return 0

    client, chat = _connect()
    # `--no-tools`：**这一版才是最容易出观感问题的那一版** —— 整回合没有工具时，
    # `panel_tools` 元素里只有占位空格（元素必须先在卡里，之后才写得进去），
    # 客户端会不会因此多留一行空白只有眼睛能判。
    _no_tools = "--no-tools" in argv
    body = cards.panel_rounds_markdown(rounds=ROUNDS)
    tools = "" if _no_tools else cards.panel_tools_markdown(tools=TOOLS)
    print("   模式 =", "无工具（占位空格）" if _no_tools else "有工具三行")
    print("   推理块 =", repr(body))
    print("   工具块 =", repr(tools or " "))
    card = cards.cardkit_entity_card(
        "**（探针）** 看面板里的两块：上面是推理、下面是工具行。" if not _no_tools
        else "**（探针）** 这张是**没有工具**的那一版：面板里只该有推理块，**不该多出空行**。",
                                     body, streaming=True, expanded=True,
                                     panel_tools_text=tools)
    made = client.cardkit.v1.card.create(
        CreateCardRequest.builder().request_body(
            CreateCardRequestBody.builder().type("card_json")
            .data(json.dumps(card, ensure_ascii=False)).build()).build())
    card_id = getattr(getattr(made, "data", None), "card_id", "") or ""
    code_make = int(getattr(made, "code", -1) or 0)
    print(f"   card.create code={code_make} card_id={card_id}")
    if not card_id:
        return 1
    sent = client.im.v1.message.create(
        CreateMessageRequest.builder().receive_id_type("chat_id")
        .request_body(CreateMessageRequestBody.builder()
                      .receive_id(chat).msg_type("interactive")
                      .uuid(f"ld-probe-blocks-{card_id}")
                      .content(json.dumps({"type": "card", "data": {"card_id": card_id}}))
                      .build()).build())
    mid = getattr(getattr(sent, "data", None), "message_id", "") or ""
    print(f"   message.create code={int(getattr(sent, 'code', -1) or 0)} message_id={mid}")
    seq = 0

    def _next() -> int:
        nonlocal seq
        seq += 1
        return seq

    for piece in ("正文第一帧：", "正文第二帧：面板里那两块**不是**正文，正文只有这一行。"):
        r = client.cardkit.v1.card_element.content(
            ContentCardElementRequest.builder().card_id(card_id).element_id("answer")
            .request_body(ContentCardElementRequestBody.builder().content(piece)
                          .sequence(_next()).uuid(f"p-{card_id}-{_next()}").build()).build())
        print(f"   写正文 code={int(getattr(r, 'code', -1) or 0)}")
        time.sleep(0.5)
    r = client.cardkit.v1.card.settings(
        SettingsCardRequest.builder().card_id(card_id)
        .request_body(SettingsCardRequestBody.builder()
                      .settings(json.dumps({"config": {"streaming_mode": False}}))
                      .sequence(_next()).uuid(f"p-{card_id}-close").build()).build())
    print(f"   关流式 code={int(getattr(r, 'code', -1) or 0)}")
    if mid:
        _STATE.write_text(json.dumps({"message_id": mid, "card_id": card_id},
                                     ensure_ascii=False), encoding="utf-8")
    print("\n📌 卡留在你的 DM 里了。请回我：")
    if _no_tools:
        print("   ① 面板里**有没有多出一行空白**（工具块是占位空格，最坏就在这里露馅）？")
        print("   ② 推理那两轮看着正常吗？标题「执行详情」在内容上面吗？")
    else:
        print("   ① 面板里**推理那一段**与**工具那三行**是不是分成两块？中间的空格看着正常吗？")
        print("   ② 面板里**有没有多出空行**（尤其工具块末尾 / 两块之间）？")
        print("   ③ 标题「执行详情」在内容**上面**吗？面板边框是不是灰色（流式期间的颜色）？")
        print("   ④ 正文区里**只有**那行正文，工具行没有跑进正文区吧？")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
