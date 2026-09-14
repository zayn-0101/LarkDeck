"""真机探针：**面板子元素的落点**（R3 的 P5 问题）—— 发两张对比卡到你自己的 DM。

## 它回答什么问题

R3 想把「执行详情」面板从**一个 markdown 元素**改成**多个子元素**（每个工具一行、按批次分组）。
接口层面有两种写法，**返回码都是 `0`**，所以「服务端收下了」这件事**不能**回答我们的问题：

  * `A`：`card_element.create(type="insert_after", target_element_id="panel_body")`
  * `B`：`card_element.create(type="append",      target_element_id="panel")`

真正要回答的是「**客户端把它们画进面板里了吗**」—— 这只有**眼睛**能判。
本探针把这两张卡真发到你的飞书 DM，每张卡上**都带一条对照组**：

  * 参照行：`insert_after(answer)` —— 已知落点（正文与面板之间的**顶层**，面板**外面**）
  * 被测行：上面 A 或 B 的写法

## 判据（看完请按这三条回）

1. 面板**收起**时，两张卡上**看得见哪几行**？（看得见 ⇒ 那一行落在面板**外面**）
2. 点开面板：被测行在面板**里面**吗？**A 在、B 不在**（或反过来）⇒ 这就是 R3 要用的写法。
3. 面板标题「执行详情」在它内容的**上面**还是**下面**？

## 实测结论（2026-09-14，已由用户截图确认 —— 别再重跑同一个问题）

| 写法 | 落在面板里？ | 子元素顺序 |
|---|---|---|
| `insert_after(panel_body)` | **是** | **倒序**（每次都插到 `panel_body` 正后方 ⇒ 后插的在上面） |
| `append(panel)` | **是** | **正序**（追加到面板子元素末尾 ⇒ 后插的在下面） |

⇒ **R3 用 `append(panel)`**：工具行要按时间顺序（老的在上）。对照组
`insert_after(answer)` 在两张卡上都落在**面板外面**（正文与面板之间），
所以「在不在那圈方框里」这个判据本身是可靠的。

⚠️ 判据的来源是**人的眼睛**，因为服务端没有这条路：CardKit **没有读回卡片实体的接口** ——
`GET /open-apis/cardkit/v1/cards/<card_id>` 实测 **404**，SDK 的 `cardkit.v1.card` 资源也只有
`create` / `update` / `settings` / `batch_update` / `id_convert`。别指望用返回码回答落点问题。

## 用法

```
python3 tests/probe_panel_placement.py            # 发两张卡并**保留**（给你看）
python3 tests/probe_panel_placement.py --delete   # 只按 message_id 删掉上次那两张（不盲删）
```

⚠️ 清理**只按 `message_id`**（本探针自己打印出来的那两个）。**绝不按时间窗删** ——
2026-09-13 实测过一次：时间窗把用户那一回合的**真实回答卡**一起删掉了。
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))                          # 复用同目录的探针工具
sys.path.insert(0, str(_HERE.parent.parent))

from probe_ck_stream_ops import (  # noqa: E402  复用（避免两份建卡逻辑漂移）
    ANSWER_ID, PANEL_BODY_ID, PANEL_ID, _Card, _connect,
)

from lark_oapi.api.cardkit.v1 import (  # noqa: E402
    CreateCardElementRequest, CreateCardElementRequestBody,
    SettingsCardRequest, SettingsCardRequestBody,
)

# 上次发出去的那两张（`--delete` 用；只有真实运行过才有值）
_STATE = _HERE.parent / ".probe_panel_placement.json"


def _next_seq(card: _Card) -> int:
    return card._next()


def _insert(card: _Card, content: str, element_id: str, target: str, kind: str) -> int:
    """往卡里插一个 markdown 元素，返回返回码（全是真机判据，不做任何假设）。"""
    r = card.client.cardkit.v1.card_element.create(
        CreateCardElementRequest.builder().card_id(card.card_id)
        .request_body(CreateCardElementRequestBody.builder()
                      .type(kind).target_element_id(target)
                      .elements(json.dumps([{"tag": "markdown", "element_id": element_id,
                                             "content": content}], ensure_ascii=False))
                      .sequence(_next_seq(card))
                      .uuid(f"p-{card.card_id}-{element_id}").build()).build())
    return card._code(r)


def _close(card: _Card) -> int:
    """显式关流式（收尾形态），这样你看到的是**静止**的卡，不会被动画干扰。"""
    r = card.client.cardkit.v1.card.settings(
        SettingsCardRequest.builder().card_id(card.card_id)
        .request_body(SettingsCardRequestBody.builder()
                      .settings(json.dumps({"config": {"streaming_mode": False}}))
                      .sequence(_next_seq(card)).uuid(f"p-{card.card_id}-close").build()).build())
    return card._code(r)


def _one_card(client, chat: str, variant: str) -> _Card:
    """发一张对比卡。`variant` 是 `A`（insert_after panel_body）或 `B`（append panel）。"""
    target, kind, how = ((PANEL_BODY_ID, "insert_after", "`insert_after(panel_body)`")
                         if variant == "A" else (PANEL_ID, "append", "`append(panel)`"))
    card = _Card(client, chat)
    if not card.open(answer=f"**对比卡 {variant}** · 子元素写法：{how}\n\n（正文先写两帧，"
                           f"再插元素，最后收尾 —— 与生产路径同序）",
                     panel="**面板正文**（`panel_body`）：这里是原来那个 markdown 元素。\n"
                           "（面板标题是「执行详情」。）"):
        return card
    card.write(f"**对比卡 {variant}** · 正文第一帧。", f"{variant}-1")
    time.sleep(0.4)
    card.write(f"**对比卡 {variant}** · 正文第二帧（正文与面板都在流式期间更新）。", f"{variant}-2")

    # ① 对照组：已知落点（顶层、面板外面）——有它才能对比出「里面」长什么样。
    control_id = f"p{variant}_control"
    c0 = _insert(card, f"🧩 **参照行（对照组）**：`insert_after(answer)` —— 应当在**面板外面**",
                 control_id, ANSWER_ID, "insert_after")
    # ② 被测行：R3 要用哪种写法，就看这一行落在哪里。
    line_id = f"p{variant}_inside"
    c1 = _insert(card, f"✅ **被测行 · 写法 {variant}**：`{how}` —— **在面板里面吗？**",
                 line_id, target, kind)
    c2 = _insert(card, "✅ **被测行 2**：再来一行（R3 是「每个工具一行」）",
                 f"p{variant}_inside2", target, kind)
    code_close = _close(card)
    print(f"   卡 {variant}：参照行 code={c0} · 被测行 code={c1}/{c2} · 关流式 code={code_close}")
    print(f"   卡 {variant} 的 message_id={card.message_id} · card_id={card.card_id}")
    return card


def main(argv) -> int:
    do_delete = "--delete" in argv
    if do_delete:
        ids = json.loads(_STATE.read_text(encoding="utf-8"))["message_ids"] if _STATE.exists() else []
        if not ids:
            print("没有上次的账本（.probe_panel_placement.json）⇒ 没什么可删的。")
            return 0
        from lark_oapi.api.im.v1 import DeleteMessageRequest
        client, _ = _connect()
        for mid in ids:
            d = client.im.v1.message.delete(DeleteMessageRequest.builder().message_id(mid).build())
            print(f"   删 {mid} → code={d.code}")
        _STATE.unlink(missing_ok=True)
        return 0

    client, chat = _connect()
    print("—— R3/P5：面板子元素落点（两张卡，接口全 code=0 也回答不了，得靠你的眼睛）——")
    cards = [_one_card(client, chat, v) for v in ("A", "B")]
    _STATE.write_text(json.dumps({"message_ids": [c.message_id for c in cards if c.message_id]},
                                 ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n📌 两张卡都留在你的 DM 里了（message_id 已记进 "
          f"{_STATE.name}）。请按这三条回我：")
    print("   ① 面板**收起**时，两张卡上分别看得见哪几行？（看得见的那些就在面板**外面**）")
    print("   ② 点开面板：A 的「被测行」在面板里面吗？B 的呢？（哪个在里面 ⇒ R3 用哪种写法）")
    print("   ③ 面板标题「执行详情」在它内容的**上面**还是**下面**？")
    print("\n   看完告我一声，我按 message_id 删这两张（`--delete`），绝不按时间窗删。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
