"""真机探针：CardKit **流式会话进行中**的元素级 / 批量写入（2026-09-13 实验的固化版）。

## 它回答什么问题

我们此前的文档写着「任何结构性写入都会关闭流式会话（`300309`）」—— 调研同类插件时发现
`aiduPOP` / `lark-hls-v2` 都在流式期间调 `card.batch_update`，与本项目旧结论冲突。
只能真机判。本探针把那次实验固化成一条可重跑的命令，并**顺带回答「客户端画不画」**
（那一半只有眼睛能判，所以留一张卡给用户看）。

## 判据

每一步操作之后**紧跟一次 `card_element.content` 写**，以「后一次写入的返回码」为判据：
  * 返回 `0`      ⇒ 该操作**不关**流式会话
  * 返回 `300309` ⇒ 该操作**关**了流式会话

## 两种模式

```
python3 tests/probe_ck_stream_ops.py            # 接口矩阵：逐个操作 + 之后能不能继续写（自动删卡）
python3 tests/probe_ck_stream_ops.py --visual   # 出一张「看得见」的卡（默认保留，--delete 可删）
```

`--visual` 那张卡是给**人的眼睛**看的：正文 → 流式期间新增元素 → 面板边框改黄 →
面板正文更新 → 继续写正文。接口全 `code=0` 只证明「服务端收下了」，
「客户端画出来了」必须由图里的人确认（2026-09-13 已确认过一次）。
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))            # 使 `import larkdeck` 成立
sys.path.insert(0, "/Users/Zayn/.hermes/hermes-agent/venv/lib/python3.11/site-packages")

import lark_oapi as lark                                            # noqa: E402
from lark_oapi.api.cardkit.v1 import (                              # noqa: E402
    ContentCardElementRequest, ContentCardElementRequestBody,
    CreateCardElementRequest, CreateCardElementRequestBody,
    CreateCardRequest, CreateCardRequestBody,
    PatchCardElementRequest, PatchCardElementRequestBody,
    SettingsCardRequest, SettingsCardRequestBody,
    UpdateCardElementRequest, UpdateCardElementRequestBody,
    BatchUpdateCardRequest, BatchUpdateCardRequestBody,
)
from lark_oapi.api.im.v1 import (CreateMessageRequest, CreateMessageRequestBody,   # noqa: E402
                                 DeleteMessageRequest)
from larkdeck.core import cards as lark_cards                       # noqa: E402

ANSWER_ID, PANEL_ID, PANEL_BODY_ID = "answer", "panel", "panel_body"


def _load_env() -> dict:
    env: dict = {}
    for line in (pathlib.Path.home() / ".hermes" / ".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class _Card:
    """建一张流式实体卡，暴露几个「流式期间能不能这么干」的操作。"""

    def __init__(self, client, chat: str) -> None:
        self.client = client
        self.chat = chat
        self.seq = 0
        self.message_id = ""
        self.card_id = ""

    def _next(self) -> int:
        self.seq += 1
        return self.seq

    def _code(self, resp) -> int:
        return int(getattr(resp, "code", -1) or 0)

    def open(self, answer: str = "（实验中）", panel: str = "面板内容") -> bool:
        # ⚠️ 卡片 JSON 一律用**生产构造函数**：手搓时 `config.summary` 容易写成字符串，
        # 飞书会回 `10002 failed to unmarshal for Summary, type: string`。
        card = lark_cards.cardkit_entity_card(answer, panel, streaming=True, panel=True)
        made = self.client.cardkit.v1.card.create(
            CreateCardRequest.builder().request_body(
                CreateCardRequestBody.builder().type("card_json")
                .data(json.dumps(card, ensure_ascii=False)).build()).build())
        self.card_id = getattr(getattr(made, "data", None), "card_id", None) or ""
        if not self.card_id:
            print(f"❌ card.create 失败：code={self._code(made)} {str(getattr(made, 'msg', ''))[:80]}")
            return False
        sent = self.client.im.v1.message.create(
            CreateMessageRequest.builder().receive_id_type("chat_id")
            .request_body(CreateMessageRequestBody.builder()
                          .receive_id(self.chat).msg_type("interactive")
                          .uuid(f"ld-probe-{self.card_id}")
                          .content(json.dumps({"type": "card", "data": {"card_id": self.card_id}}))
                          .build()).build())
        self.message_id = getattr(getattr(sent, "data", None), "message_id", None) or ""
        print(f"   建实体 card_id={self.card_id} · message_id={self.message_id}")
        return bool(self.message_id)

    def write(self, text: str, tag: str) -> bool:
        """写正文元素；返回「流式会话是否还活着」。"""
        r = self.client.cardkit.v1.card_element.content(
            ContentCardElementRequest.builder().card_id(self.card_id).element_id(ANSWER_ID)
            .request_body(ContentCardElementRequestBody.builder().content(text or " ")
                          .sequence(self._next()).uuid(f"p-{self.card_id}-{tag}").build()).build())
        alive = self._code(r) == 0
        print(f"   [{'✅' if alive else '❌'}] 写正文（{tag}）→ code={self._code(r)}"
              + ("" if alive else "   ← 流式会话已被关闭"))
        return alive

    # --- 以下每个方法都返回 (返回码, 之后还能不能写正文) --------------------------- #
    def op_element_patch(self) -> tuple:
        r = self.client.cardkit.v1.card_element.patch(
            PatchCardElementRequest.builder().card_id(self.card_id).element_id(PANEL_BODY_ID)
            .request_body(PatchCardElementRequestBody.builder()
                          .partial_element(json.dumps({"content": "面板内容（局部更新）"},
                                                      ensure_ascii=False))
                          .sequence(self._next()).uuid(f"p-{self.card_id}-patch").build()).build())
        return self._code(r), self.write("patch 之后", "after-patch")

    def op_batch_update(self) -> tuple:
        action = {"action": "partial_update_element",
                  "params": {"element_id": PANEL_BODY_ID,
                             "partial_element": {"content": "面板内容（批量局部更新）"}}}
        r = self.client.cardkit.v1.card.batch_update(
            BatchUpdateCardRequest.builder().card_id(self.card_id)
            .request_body(BatchUpdateCardRequestBody.builder()
                          .actions(json.dumps([action], ensure_ascii=False))
                          .sequence(self._next()).uuid(f"p-{self.card_id}-batch").build()).build())
        return self._code(r), self.write("batch_update 之后", "after-batch")

    def op_element_create(self, content: str = "流式期间新增的元素") -> tuple:
        r = self.client.cardkit.v1.card_element.create(
            CreateCardElementRequest.builder().card_id(self.card_id)
            .request_body(CreateCardElementRequestBody.builder()
                          .type("insert_after").target_element_id(ANSWER_ID)
                          .elements(json.dumps([{"tag": "markdown", "element_id": "added_one",
                                                 "content": content}], ensure_ascii=False))
                          .sequence(self._next()).uuid(f"p-{self.card_id}-add").build()).build())
        return self._code(r), self.write("element.create 之后", "after-create")

    def op_element_update(self) -> tuple:
        r = self.client.cardkit.v1.card_element.update(
            UpdateCardElementRequest.builder().card_id(self.card_id).element_id(PANEL_BODY_ID)
            .request_body(UpdateCardElementRequestBody.builder()
                          .element(json.dumps({"tag": "markdown", "element_id": PANEL_BODY_ID,
                                               "content": "面板内容（整元素替换）"},
                                              ensure_ascii=False))
                          .sequence(self._next()).uuid(f"p-{self.card_id}-upd").build()).build())
        return self._code(r), self.write("element.update 之后", "after-update")

    def op_settings_summary(self, summary: str = "会话列表预览：流式未关") -> tuple:
        # ⚠️ `summary` 是 i18n 对象（`{"content": …}`），传字符串会被拒 300122。
        r = self.client.cardkit.v1.card.settings(
            SettingsCardRequest.builder().card_id(self.card_id)
            .request_body(SettingsCardRequestBody.builder()
                          .settings(json.dumps({"config": {
                              "summary": {"content": summary[:120]}}}, ensure_ascii=False))
                          .sequence(self._next()).uuid(f"p-{self.card_id}-sum").build()).build())
        return self._code(r), self.write("settings(summary) 之后", "after-summary")

    def op_settings_close(self) -> tuple:
        r = self.client.cardkit.v1.card.settings(
            SettingsCardRequest.builder().card_id(self.card_id)
            .request_body(SettingsCardRequestBody.builder()
                          .settings(json.dumps({"config": {"streaming_mode": False}}))
                          .sequence(self._next()).uuid(f"p-{self.card_id}-close").build()).build())
        return self._code(r), self.write("settings(streaming_mode=false) 之后（预期会被关）", "after-close")

    def delete(self) -> None:
        if not self.message_id:
            return
        d = self.client.im.v1.message.delete(
            DeleteMessageRequest.builder().message_id(self.message_id).build())
        print(f"   清理实验卡 → code={d.code}")


def _connect():
    env = _load_env()
    client = (lark.Client.builder().app_id(env["FEISHU_APP_ID"])
              .app_secret(env["FEISHU_APP_SECRET"]).log_level(lark.LogLevel.ERROR).build())
    return client, env["FEISHU_HOME_CHANNEL"]


def probe_matrix() -> int:
    """接口矩阵：每个操作之后都问一句「流式会话还活着吗」。"""
    client, chat = _connect()
    print("—— 流式期间的元素级/批量写入矩阵 ——")
    results = []
    for name, op in (("card_element.patch", "op_element_patch"),
                     ("card.batch_update", "op_batch_update"),
                     ("card_element.create（新增元素）", "op_element_create"),
                     ("card_element.update", "op_element_update"),
                     ("card.settings（只写 summary）", "op_settings_summary")):
        card = _Card(client, chat)
        print(f"\n▶ {name}")
        if not card.open():
            return 1
        card.write("基线一", "base")
        code, alive = getattr(card, op)()
        results.append((name, code, alive))
        card.delete()
    # 收尾必须是「关会话」那一个（否则矩阵不完整）
    card = _Card(client, chat)
    print("\n▶ card.settings(streaming_mode=false)（对照组：这一步本来就该关）")
    if not card.open():
        return 1
    card.write("基线一", "base")
    code, alive = card.op_settings_close()
    results.append(("card.settings(streaming_mode=false)", code, alive))
    card.delete()

    print("\n—— 汇总 ——")
    bad = []
    for name, code, alive in results:
        want = not name.endswith("streaming_mode=false)")   # 只有显式关流式那一步该失败
        ok = (code == 0) and (alive is want)
        print(f"   {'✅' if ok else '❌'} {name}: 返回码={code} · 之后还能写正文={alive}")
        if not ok:
            bad.append(name)
    if bad:
        print(f"\n❌ 有 {len(bad)} 项与预期不符：{bad}")
        return 1
    print("\n✅ 结论：关流式会话的只有**整卡替换**与**显式关流式**；"
          "\n   元素级（patch/create/update）与批量（batch_update）接口在流式期间可用且不关会话。")
    return 0


def probe_visual(keep: bool) -> int:
    """出一张给人看的卡：流式期间新增元素 + 改边框色 + 改面板内容。"""
    client, chat = _connect()
    card = _Card(client, chat)
    print("—— 给人看的那张卡（接口收下了 ≠ 客户端画出来了）——")
    if not card.open(answer="（准备中）", panel="面板：初始内容"):
        return 1
    for i, t in enumerate(("这是**流式期间**的动态写入实验：",
                           "正文一帧一帧地长出来，",
                           "同时卡片的**结构**也在变。"), 1):
        card.write(t * 2, f"frame{i}")
        time.sleep(0.6)
    code, alive = card.op_element_create("🧩 **这一行是流式期间插入的新元素**")
    print(f"   新增元素 code={code} · 之后还能写={alive}")
    r = client.cardkit.v1.card_element.patch(
        PatchCardElementRequest.builder().card_id(card.card_id).element_id(PANEL_ID)
        .request_body(PatchCardElementRequestBody.builder()
                      .partial_element(json.dumps({"border": {"color": "yellow"}}))
                      .sequence(card._next()).uuid(f"p-{card.card_id}-color").build()).build())
    print(f"   面板边框改黄 code={card._code(r)}")
    card.write("改完边框色之后，正文**依然**能写。", "after-color")
    r = client.cardkit.v1.card_element.patch(
        PatchCardElementRequest.builder().card_id(card.card_id).element_id(PANEL_BODY_ID)
        .request_body(PatchCardElementRequestBody.builder()
                      .partial_element(json.dumps({"content": "**面板也在流式期间更新了**："
                                                              "工具 terminal ✅ 1.2s · bash ✅ 0.4s"},
                                                  ensure_ascii=False))
                      .sequence(card._next()).uuid(f"p-{card.card_id}-panel").build()).build())
    print(f"   面板正文更新 code={card._code(r)}")
    card.write("面板更新之后，正文一路写到底。", "after-panel")
    code, _ = card.op_settings_close()
    print(f"   收尾（只关流式）code={code}")
    if keep:
        print(f"\n📌 卡片留在 DM 里给你看：message_id={card.message_id}")
        print("   请确认：① 正文之后有没有那一行「🧩 …新增元素」；② 面板「执行详情」是不是黄边；"
              "③ 展开后面板里是不是「面板也在流式期间更新了…」。")
    else:
        card.delete()
    return 0


def main(argv) -> int:
    if "--visual" in argv:
        return probe_visual(keep="--delete" not in argv)
    return probe_matrix()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
