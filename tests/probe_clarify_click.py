"""真机探针：**澄清卡点击的真实反馈**（toast / 内联换卡）—— 发给自己的 DM，用手机点。

## 它回答什么（只有眼睛能判的那一半）

写进 README / plan-v1 的四条真机留白里，有两条与「点一下」有关：
  * **toast 到底弹不弹**（`CallBackToast` 只在真机客户端才看得见）；
  * 失败态**卡片是不是原地不动**（不许被一次迟到的点击换成别的样子）。

**这一版探针故意用一个网关里不存在的 `clarify_id`** —— 于是点下去必然走
`NO_PENDING` 分支（「这条澄清已被处理或已过期，无需重复点击」）。这正好是**可以离线复现**
的那条路径：不需要真的让 agent 提问，也不需要等一个真实的澄清回合。
⚠️ 另一条（**空提交**不静默、`mode == none`）**没法这样造**：它要求网关里真的有一个
待答澄清条目（那是网关进程内的状态，探针在另一个进程里伪造不出来）⇒ 那条只能等一次真实澄清。

## 用法

```
python3 tests/probe_clarify_click.py            # 发一张澄清卡并**保留**
python3 tests/probe_clarify_click.py --delete   # 按 message_id 删掉上次那张（不盲删）
```

⚠️ 清理只按**自己账本里的 message_id** —— 绝不许按「最近 N 分钟」的时间窗删
（2026-09-13 实测：时间窗把用户那一回合的真实回答卡一起删了）。
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent))

from probe_ck_stream_ops import _connect                      # noqa: E402  复用连接与环境加载
import lark_oapi as lark                                       # noqa: E402
from lark_oapi.api.im.v1 import (CreateMessageRequest,         # noqa: E402
                                 CreateMessageRequestBody, DeleteMessageRequest)
from larkdeck.core import cards                                # noqa: E402

_STATE = _HERE.parent / ".probe_clarify_click.json"


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
    # ⚠️ 用**生产构造器**（不手搓 JSON）：卡片的形状就是真实澄清卡的形状，
    #    包括组件级 `behaviors`（2.0 方言）与 `larkdeck_action` 拦截键。
    card = cards.clarify_card_2(
        "【探针】请点**下面那个写着「请选择」的下拉框**（在弹出的三个选项里随便选一个）。"
        "这条澄清在网关里并不存在 ⇒ 预期只弹一条 toast，卡片一动不动。",
        ["选项甲", "选项乙", "选项丙"],
        clarify_id="probe-clarify-click-nonexistent", session_key="probe-session")
    sent = client.im.v1.message.create(
        CreateMessageRequest.builder().receive_id_type("chat_id")
        .request_body(CreateMessageRequestBody.builder()
                      .receive_id(chat).msg_type("interactive")
                      # ⚠️ **uuid 必须每次都不同**（2026-09-14 实测踩到）：飞书按 uuid 去重，
                      # 用固定 uuid 时「删掉再重发」会拿到**同一个已删的 message_id** ——
                      # 接口回 `code=0`，而 DM 里什么都没有（我因此白让你找了一次）。
                      .uuid(f"ld-probe-clarify-click-{int(time.time())}")
                      .content(json.dumps(card, ensure_ascii=False)).build()).build())
    mid = getattr(getattr(sent, "data", None), "message_id", None) or ""
    print(f"   发了澄清卡 message_id={mid}（code={getattr(sent, 'code', -1)}）")
    if mid:
        _STATE.write_text(json.dumps({"message_id": mid}, ensure_ascii=False), encoding="utf-8")
    print("\n点击位置：**问题文字下面那个写着「请选择」的灰框**（点它会弹出三个选项）——")
    print("   不是点问题文字、也不是点下面的输入框。然后回我三件事：")
    print("   ① 有没有弹出一条 toast？文案是不是「这条澄清已被处理或已过期，无需重复点击」？")
    print("   ② 卡片本身**有没有变样**？（应当一动不动）")
    print("   ③ 下拉能不能展开、三个选项看不看得见？")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
