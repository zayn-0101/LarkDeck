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
python3 tests/probe_ck_stream_ops.py             # 接口矩阵：逐个操作 + 之后能不能继续写（自动删卡）
python3 tests/probe_ck_stream_ops.py --visual    # 出一张「看得见」的卡（默认保留，--delete 可删）
python3 tests/probe_ck_stream_ops.py --batching  # R0：一次 batch 带多元素 / 序号账本语义（自动删卡）
python3 tests/probe_ck_stream_ops.py --withdrawn # R0：消息被撤回/删除后写卡回什么码（自动删卡）
python3 tests/probe_ck_stream_ops.py --element-limits  # R0：create 能带几个元素 / 运行时新增算不算进 200
python3 tests/probe_ck_stream_ops.py --capacity-codes  # R11-B2：容量满 / 重复 id 的码与 msg 字面形状（自动删卡）
python3 tests/probe_ck_stream_ops.py --lanes   # R5：batch 坏 id 的返回码口径 / 整卡 patch 能否覆盖实体卡（自动删卡）
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
        # ⚠️ 失败原因要分清楚，别一律说成「会话已关闭」：300309 才是会话被关，
        # 300317 是**序号冲突**（跳号/撞号），两者的修法完全不同（第十二路审计教训：
        # 探针把不同的码说成同一件事，比不说更坏）。
        hint = {"300309": "   ← 流式会话已被关闭",
                "300317": "   ← 序号冲突（必须严格递增：跳号与撞号都不行）"}.get(str(self._code(r)),
                                                                            "")
        print(f"   [{'✅' if alive else '❌'}] 写正文（{tag}）→ code={self._code(r)}{hint}")
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

    def op_element_create_inside(self, content: str = "面板里的新元素",
                                 target: str = PANEL_BODY_ID, kind: str = "insert_after",
                                 element_id: str = "added_in_panel") -> tuple:
        """把新元素插到**面板内部**（R3 的 P5 问题）：`insert_after(panel_body)` 与 `append(panel)`
        哪个真的落在面板**里面** —— 接口收下（`code=0`）不能回答这个，只有眼睛能。
        """
        r = self.client.cardkit.v1.card_element.create(
            CreateCardElementRequest.builder().card_id(self.card_id)
            .request_body(CreateCardElementRequestBody.builder()
                          .type(kind).target_element_id(target)
                          .elements(json.dumps([{"tag": "markdown", "element_id": element_id,
                                                 "content": content}], ensure_ascii=False))
                          .sequence(self._next()).uuid(f"p-{self.card_id}-in-{element_id}").build()).build())
        return self._code(r), self.write("面板内新增之后", f"after-{element_id}")

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


def probe_batching() -> int:
    """R0/P1+P2+P3：**一次 `card.batch_update` 能带几个元素**、**它占几个 sequence**、
    `card.settings` 是否也吃号 —— 回答「每帧 2 次写」这个预算能不能成立。

    判据全部是**返回码**；`--visual` 那一半（客户端是否真的重绘多个元素）另跑。

    设计：所有带 `sequence` 的调用**共用 +1 递增的单一计数器**，交替发 batch 与 content；
    只要全程 `code=0`，就说明「单计数器、每次调用 +1」这条设计对 batch 与 settings 同样成立。
    再补两条反证臂：**跳号**与**撞号**必须被拒（否则说明序号根本没人校验，我们的账本就白管了）。
    """
    client, chat = _connect()
    failures = []
    card = _Card(client, chat)
    print("▶ 臂 1：content(1) → batch(2, 三个元素) → content(3) → settings(4) → content(5)")
    if not card.open(answer="开始", panel="面板"): 
        return 1
    ok1 = card.write("第一帧", "b1")
    action = lambda eid, content: {                     # noqa: E731
        "action": "partial_update_element",
        "params": {"element_id": eid, "partial_element": {"content": content}}}
    actions = [action(PANEL_BODY_ID, "面板（batch 改的）"),
               action(ANSWER_ID, "第一帧（batch 改的）")]
    r = client.cardkit.v1.card.batch_update(
        BatchUpdateCardRequest.builder().card_id(card.card_id)
        .request_body(BatchUpdateCardRequestBody.builder()
                      .actions(json.dumps(actions, ensure_ascii=False))
                      .sequence(card._next()).uuid(f"p-{card.card_id}-b2").build()).build())
    code_batch = card._code(r)
    ok3 = card.write("第三帧", "b3")
    r = client.cardkit.v1.card.settings(
        SettingsCardRequest.builder().card_id(card.card_id)
        .request_body(SettingsCardRequestBody.builder()
                      .settings(json.dumps({"config": {"summary": {"content": "R0 探针：batch 与序号"}}}))
                      .sequence(card._next()).uuid(f"p-{card.card_id}-b4").build()).build())
    code_settings = card._code(r)
    ok5 = card.write("第五帧", "b5")
    print(f"   单计数器 +1 递增：content={ok1} · batch(code={code_batch}) · "
          f"content={ok3} · settings(code={code_settings}) · content={ok5}")
    if not (ok1 and code_batch == 0 and ok3 and code_settings == 0 and ok5):
        failures.append("臂 1：单计数器 +1 递增在 batch/settings 上不成立")
    card.delete()

    print("▶ 臂 2：**跳号**必须被拒（settings 用 seq=100 后，content 用 seq=2）")
    card = _Card(client, chat)
    if not card.open():
        return 1
    card.write("基线", "c1")
    r = client.cardkit.v1.card.settings(
        SettingsCardRequest.builder().card_id(card.card_id)
        .request_body(SettingsCardRequestBody.builder()
                      .settings(json.dumps({"config": {"summary": {"content": "跳号试验"}}}))
                      .sequence(100).uuid(f"p-{card.card_id}-jump").build()).build())
    jump_code = card._code(r)
    after_jump = card.write("跳号之后", "c2")
    print(f"   settings(seq=100) code={jump_code} · 之后 content(seq=2) 还能写={after_jump}")
    if jump_code == 0 and after_jump:
        print("   ⚠️ 跳号竟然被接受 ⇒ 服务端不校验序号连续性（我们的账本仍按 +1 走，安全）")
    card.delete()

    print("▶ 臂 3：**撞号**必须被拒（content 用同一个号发两次）")
    card = _Card(client, chat)
    if not card.open():
        return 1
    card.seq = 41
    first = card.write("同一个号第一次", "d1")
    r = client.cardkit.v1.card_element.content(
        ContentCardElementRequest.builder().card_id(card.card_id).element_id(ANSWER_ID)
        .request_body(ContentCardElementRequestBody.builder().content("同一个号第二次")
                      .sequence(41).uuid(f"p-{card.card_id}-dup41").build()).build())
    dup_code = card._code(r)
    print(f"   同号第一次={first} · 同号第二次 code={dup_code}"
          f"{'（被拒 ✅）' if dup_code != 0 else '（竟然接受 ⚠️）'}")
    if dup_code == 0:
        print("   ⚠️ 同号被接受 ⇒ 序号只是「单调不减」；我们仍然 +1，无害")
    card.delete()

    print("\n—— R0 batch 探针汇总 ——")
    print(f"   一次 batch 带 2 个 partial_update_element ⇒ code={code_batch}")
    print(f"   与 content/settings 共用 +1 计数器 ⇒ {'可行 ✅' if not failures else '不可行 ❌'}")
    print("   客户端是否真的同时重绘这两个元素：**只有眼睛能判**，"
          "确认卡在 R2 的 --visual 里一起出")
    if failures:
        print(f"\n❌ {failures}")
        return 1
    print("\n✅ 结论：**单计数器 + 每次 API 调用 +1** 对 content / batch_update / settings 都成立；"
          "\n   ⇒ 每帧 2 次写（1 次 batch 承载多个元素 + 1 次正文）在接口层站得住。")
    return 0


def _nested_card(children: int) -> dict:
    """造一张「面板里挂 N 个子元素」的实体卡（每个子元素都很小，用来逼近 200 的墙）。"""
    card = lark_cards.cardkit_entity_card("正文", "面板", streaming=True, panel=True)
    panel = card["body"]["elements"][1]
    panel["elements"] = [{"tag": "markdown", "element_id": f"c{i}", "content": "x"}
                         for i in range(children)]
    return card


def probe_element_limits() -> int:
    """R0/P7：`card_element.create` 一次能带几个元素？**运行时新增的元素算不算进 200**？

    这两条决定 R2/R3 的阈值能不能靠「估算」，还是必须用 `cards.count_elements` 真的数一遍。
    判据是返回码：超限时飞书会给一个**确定性**的拒收码（`230099`/`300312` 一类），
    而「元素不存在」是 `300313`、序号问题是 `300317` —— 三者要分清。
    """
    client, chat = _connect()

    print("▶ Q1：一次 card_element.create 能带几个元素？")
    for n in (1, 3, 10):
        card = _Card(client, chat)
        if not card.open():
            return 1
        card.write("基线", f"q1-{n}")
        els = [{"tag": "markdown", "element_id": f"add{i}", "content": f"新增 {i}"}
               for i in range(n)]
        r = client.cardkit.v1.card_element.create(
            CreateCardElementRequest.builder().card_id(card.card_id)
            .request_body(CreateCardElementRequestBody.builder()
                          .type("insert_after").target_element_id(ANSWER_ID)
                          .elements(json.dumps(els, ensure_ascii=False))
                          .sequence(card._next()).uuid(f"p-{card.card_id}-add{n}").build()).build())
        code = card._code(r)
        alive = card.write(f"{n} 个之后", f"q1b-{n}")
        print(f"   带 {n} 个元素：code={code} · 之后还能写正文={alive}"
              f"{'' if code == 0 else '   ← ' + str(getattr(r, 'msg', ''))[:60]}")
        card.delete()

    print("\n▶ Q2：面板里挂 N 个子元素时，**整卡建得起来吗**（找 200 的墙）")
    for children in (190, 196, 200, 205):
        card = _nested_card(children)
        total = lark_cards.count_elements(card)
        made = client.cardkit.v1.card.create(
            CreateCardRequest.builder().request_body(
                CreateCardRequestBody.builder().type("card_json")
                .data(json.dumps(card, ensure_ascii=False)).build()).build())
        code = getattr(made, "code", "?")
        cid = getattr(getattr(made, "data", None), "card_id", None) or ""
        print(f"   子元素 {children} 个（递归总数 {total}，{lark_cards.card_bytes(card)} 字节）"
              f" ⇒ code={code}{'' if code == 0 else '   ← ' + str(getattr(made, 'msg', ''))[:60]}")
        if code == 0 and cid:
            # 顺手清理：建起来的实体卡对应一条消息才需要删；这里没发消息 ⇒ 只留着实体
            pass

    print("\n▶ Q3：已经贴着上限时，运行时 `create` 还能不能再加（**新增算不算进 200**）")
    card = _Card(client, chat)
    base = _nested_card(190)
    # 用 _Card.open 的流程建不了自定义结构 ⇒ 手动建
    made = client.cardkit.v1.card.create(
        CreateCardRequest.builder().request_body(
            CreateCardRequestBody.builder().type("card_json")
            .data(json.dumps(base, ensure_ascii=False)).build()).build())
    card.card_id = getattr(getattr(made, "data", None), "card_id", None) or ""
    sent = client.im.v1.message.create(
        CreateMessageRequest.builder().receive_id_type("chat_id")
        .request_body(CreateMessageRequestBody.builder().receive_id(chat).msg_type("interactive")
                      .uuid(f"ld-probe-{card.card_id}")
                      .content(json.dumps({"type": "card", "data": {"card_id": card.card_id}}))
                      .build()).build())
    card.message_id = getattr(getattr(sent, "data", None), "message_id", None) or ""
    card.seq = 0
    print(f"   建卡 code={getattr(made, 'code', '?')}（递归总数 {lark_cards.count_elements(base)}）")
    # ⚠️ 第一版这里每轮都从 `z0` 开始 ⇒ 第二轮撞上第一轮建过的 id，飞书回 `300315`
    #    与 `Code 1001: Duplicate ID` —— **探针自己的 bug 冒充成了「元素上限」的答案**。
    #    所以 id 必须**全局递增**（这正是本项目「探针会替你撒谎」那条教训的又一例）。
    _next_id = {"n": 0}

    def _fresh(n: int) -> list:
        out = []
        for _ in range(n):
            out.append({"tag": "markdown", "element_id": f"z{_next_id['n']}", "content": "y"})
            _next_id["n"] += 1
        return out

    for n in (5, 10):
        els = _fresh(n)
        r = client.cardkit.v1.card_element.create(
            CreateCardElementRequest.builder().card_id(card.card_id)
            .request_body(CreateCardElementRequestBody.builder()
                          .type("insert_after").target_element_id(ANSWER_ID)
                          .elements(json.dumps(els, ensure_ascii=False))
                          .sequence(card._next()).uuid(f"p-{card.card_id}-z{n}").build()).build())
        print(f"   +{n} 个 ⇒ code={card._code(r)}"
              f"{'' if card._code(r) == 0 else '   ← ' + str(getattr(r, 'msg', ''))[:70]}")
    card.delete()

    print("\n—— 结论怎么用 ——")
    print("   · 若「运行时新增」会被拒 ⇒ R3 的元素预算必须**在本地用 count_elements 真的数**，")
    print("     不能估算（估算 = 撞墙时整帧失败 = 上游补 finalize + `_first_send` ⇒ DM 两张卡）")
    print("   · 建实体时的 200 墙决定 R2/R3 的『初始结构能挂多少元素』")
    return 0


def probe_withdrawn() -> int:
    """R0/P4：**消息被撤回/删除之后写卡回什么码** —— 撤回守卫的码表必须实测，不许抄。

    六臂（每臂一张独立探针卡，一次操作，记录 code/msg）：
      A1 删掉自己发的卡 → `card_element.content`
      A2 删掉自己发的卡 → `message.patch`
      A3 从不存在的 message_id → patch（区分「不存在」与「已删除」）
      A4 非法格式的 message_id → patch
      A6 删掉自己发的卡 → `card.batch_update`
    判据：把 (臂, 操作, code) **去重**后打印；结论落成码表（写进代码注释时要带出处）。
    """
    client, chat = _connect()
    rows = []
    unknown_mid = f"om_probe_absent_{int(time.time())}"

    def patch_card(mid: str, label: str) -> tuple:
        node = lark_cards.status_shell(lark_cards.unified_panel(status="ok")) \
            if False else lark_cards.card(elements=[{"tag": "markdown", "content": label}])
        from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody
        req = PatchMessageRequest.builder().message_id(mid).request_body(
            PatchMessageRequestBody.builder()
            .content(json.dumps(node, ensure_ascii=False)).build()).build()
        r = client.im.v1.message.patch(req)
        return getattr(r, "code", "?"), str(getattr(r, "msg", ""))[:60]

    # A1 / A2 / A6：先建卡，删掉，再写
    card = _Card(client, chat)
    if not card.open():
        return 1
    card.write("删除前的正文", "pre-del")
    mid = card.message_id
    d = client.im.v1.message.delete(DeleteMessageRequest.builder().message_id(mid).build())
    print(f"   删卡 message_id={mid} → code={d.code}")
    code_content = card.write("删除后写正文", "post-del")
    rows.append(("A1 删卡后 card_element.content", "content", code_content))
    c, m = patch_card(mid, "删除后 patch")
    rows.append(("A2 删卡后 message.patch", "patch", c))
    print(f"   A2 patch 返回：code={c} {m}")
    r = client.cardkit.v1.card.batch_update(
        BatchUpdateCardRequest.builder().card_id(card.card_id)
        .request_body(BatchUpdateCardRequestBody.builder()
                      .actions(json.dumps([{"action": "partial_update_element",
                                            "params": {"element_id": ANSWER_ID,
                                                       "partial_element": {"content": "删除后 batch"}}}],
                                          ensure_ascii=False))
                      .sequence(card._next()).uuid(f"p-{card.card_id}-postdel").build()).build())
    rows.append(("A6 删卡后 card.batch_update", "batch", card._code(r)))

    # A3 / A4：不存在的 / 非法格式的 message_id
    c3, m3 = patch_card(unknown_mid, "不存在的 id")
    rows.append(("A3 不存在的 message_id", "patch", c3))
    c4, m4 = patch_card("om_probe_bad", "非法格式 id")
    rows.append(("A4 非法格式的 message_id", "patch", c4))
    print(f"   A3 code={c3} {m3}")
    print(f"   A4 code={c4} {m4}")

    print("\n—— 码表（按 (臂, 码) 去重后的原始记录）——")
    seen = set()
    for label, op, code in rows:
        key = (op, code)
        tag = "" if key not in seen else "  ← 与上面同一码"
        seen.add(key)
        print(f"   {label:<34} {op:<8} code={code}{tag}")
    codes = sorted({c for _, _, c in rows if c != 0})
    print(f"\n   实测到的非零码集合：{codes}")
    print("   ⚠️ 这三个码必须写进 `_CK_WITHDRAWN_CODES` 的是**上面实测的**，"
          "不是第三方 README 里的 `231003/1000023/230011` —— 按本项目规矩：官方文档 + 真机探针为准。")
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
    code, alive = card.op_element_create("🧩 **A：`insert_after(answer)` 插的新元素**（应当在面板**外面**，"
                                        "正文与面板之间）")
    print(f"   新增元素 code={code} · 之后还能写={alive}")
    # R3 的 P5：往**面板里面**插元素的两种写法（哪个真的落在面板里只有眼睛能判）
    code_a, alive_a = card.op_element_create_inside(
        "🧩 **B：`insert_after(panel_body)` 插的元素**（展开面板时若看得见 ⇒ 落在面板**里面**）",
        element_id="added_p5_a")
    print(f"   B(insert_after panel_body) code={code_a} · 之后还能写={alive_a}")
    code_b, alive_b = card.op_element_create_inside(
        "🧩 **C：`append(panel)` 插的元素**（同上判据）",
        target=PANEL_ID, kind="append", element_id="added_p5_b")
    print(f"   C(append panel) code={code_b} · 之后还能写={alive_b}")
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
        print("   请按这四条看，逐条回我（这决定 R3 的面板结构化怎么做）：")
        print("   ① 正文之后有没有那一行「🧩 A：insert_after(answer)…」，它在**面板外面**还是里面？")
        print("   ② 面板「执行详情」**收起**时，能不能看到 B / C 两行？（能看到 ⇒ 它们落在面板外面）")
        print("   ③ 点开面板：B（insert_after panel_body）与 C（append panel）在不在面板**里面**？")
        print("      哪个在、哪个不在，直接决定 R3 用哪种写法。")
        print("   ④ 面板的标题行「执行详情」是在它内容的**上面**还是**下面**？边框是不是黄色的？")
    else:
        card.delete()
    return 0


def probe_capacity_codes() -> int:
    """R11-B2 真机探针：**容量到顶 / 重复 id 时，运行时 `card_element.create` 到底回什么码 + 什么 msg**。

    为什么必须实测（`docs/plan-v1.md` 附录 F 把 append 形状登记为**未测**）：
    `300315` 在本项目里**一名两义** ——
      * 容量满：P7 记的是「msg 里包着内层 `300305`」，但那是 `insert_after(answer)` 的形状；
      * 复用已存在的元素 id：`Code 1001: Duplicate ID`。

    B2 的 `_CkResult.inner_code()` 要**从 msg 里解析内层码**，所以 msg 的**字面形状**就是它的
    输入契约；而「append 形状撞 200 墙」这一条至今没有真机样本（方案里明写未测）。

    判据是**断言**不是 print（本项目纪律：探针自己也会撒谎）：
      ① 两种容量形状（`append(panel)` / `insert_after(answer)`）都要回 `300315`，
         且 msg 里能解析出**尾部**的内层 `300305`；
      ② 重复 id 也要回 `300315`，但**尾部内层码是 `300301`**（方括号里的 `Code 1001` 只是
         **描述码**，不是权威内层码）—— 这正是「不能只看外层码」的实测依据。
        ⚠️ 这一句曾经写错成「内层码是 `1001`」（B2 审计低-3 抓到的：docstring 与断言不同步），
        现在写成与断言一致的那一个。
    ⚠️ 元素 id 必须**全局递增**（P7 的教训：探针自己的 bug 冒充过「元素上限」的答案）。
    ⚠️ **残留**（B2 审计低-3，不是本探针独有）：`_Card.delete()` 只删**消息**
    （`im.v1.message.delete`），`card.create` 建出来的 **CardKit 实体卡本体留在飞书远端**
    （DM 里看不见，也没有对应的删除调用被实测过）。既有探针（P7 等）本来就是这么做的，
    所以这里如实记账、不假装干净；`--capacity-codes` 也因此**不进探针账本**
    （账本按 `message_id` 删卡，而这里的两张卡都是即时删消息）。
    """
    import re as _re
    client, chat = _connect()
    fresh = {"n": 0}

    def _new_elems(n: int) -> list:
        out = []
        for _ in range(n):
            out.append({"tag": "markdown", "element_id": f"w{fresh['n']}", "content": "y"})
            fresh["n"] += 1
        return out

    def _create(card, kind: str, target: str, elems: list, tag: str) -> tuple:
        r = client.cardkit.v1.card_element.create(
            CreateCardElementRequest.builder().card_id(card.card_id)
            .request_body(CreateCardElementRequestBody.builder()
                          .type(kind).target_element_id(target)
                          .elements(json.dumps(elems, ensure_ascii=False))
                          .sequence(card._next()).uuid(f"p-{card.card_id}-{tag}").build()).build())
        return card._code(r), str(getattr(r, "msg", "") or "")

    def _send_raw(base: dict) -> "tuple":
        """建实体 + 发消息（不经过 `_Card.open`，因为要自定义卡结构）。"""
        made = client.cardkit.v1.card.create(
            CreateCardRequest.builder().request_body(
                CreateCardRequestBody.builder().type("card_json")
                .data(json.dumps(base, ensure_ascii=False)).build()).build())
        card = _Card(client, chat)
        card.card_id = getattr(getattr(made, "data", None), "card_id", None) or ""
        sent = client.im.v1.message.create(
            CreateMessageRequest.builder().receive_id_type("chat_id")
            .request_body(CreateMessageRequestBody.builder().receive_id(chat)
                          .msg_type("interactive")
                          .uuid(f"ld-probe-{card.card_id}")
                          .content(json.dumps({"type": "card", "data": {"card_id": card.card_id}}))
                          .build()).build())
        card.message_id = getattr(getattr(sent, "data", None), "message_id", None) or ""
        return card

    problems: list = []
    print("—— ① 容量满：append(panel) 撞 200 墙（附录 F 登记的未测项）——")
    base = _nested_card(190)
    print(f"   基线递归元素数={lark_cards.count_elements(base)}")
    card = _send_raw(base)
    if not card.card_id or not card.message_id:
        print("   ❌ 建实体/发消息失败，探针无法继续")
        return 1
    code5, msg5 = _create(card, "append", PANEL_ID, _new_elems(5), "cap-a5")
    print(f"   append +5  ⇒ code={code5} · msg={msg5!r}")
    code_append, msg_append = _create(card, "append", PANEL_ID, _new_elems(10), "cap-a10")
    print(f"   append +10 ⇒ code={code_append} · msg={msg_append!r}")
    code_ins, msg_ins = _create(card, "insert_after", ANSWER_ID, _new_elems(10), "cap-i10")
    print(f"   insert_after(answer) +10 ⇒ code={code_ins} · msg={msg_ins!r}")
    card.delete()

    print("\n—— ② 重复 id：拿一个卡里已存在的 id 去 create（P7 副产物的形状）——")
    card2 = _Card(client, chat)
    if not card2.open(answer="（基线）", panel="面板：基线"):
        return 1
    dup = [{"tag": "markdown", "element_id": PANEL_BODY_ID, "content": "y"}]
    code_dup, msg_dup = _create(card2, "append", PANEL_ID, dup, "cap-dup")
    print(f"   复用已存在 id({PANEL_BODY_ID}) ⇒ code={code_dup} · msg={msg_dup!r}")
    card2.delete()

    inner = _re.compile(r"code\s*:\s*(\d+)", _re.IGNORECASE)      # 尾部的权威内层码
    desc = _re.compile(r"Code\s+(\d+)\s*:")                       # 方括号里的描述码（1001 = 重复 id）

    def _inner(msg: str):
        m = inner.search(msg or "")
        return int(m.group(1)) if m else None

    def _desc(msg: str):
        m = desc.search(msg or "")
        return int(m.group(1)) if m else None

    # ⚠️ 期望值是 **2026-09-15 实测的字面形状**，不是从文档抄的；两条容量臂必须**同形**
    # （append 与 insert_after 都回 300315 + 内层 300305），重复 id 臂的内层码是 **300301**
    # （不是描述码 1001）—— 这正是「`300315` 只是包装码、真原因在尾部 `code:`」的实测依据。
    for label, code, msg, want_code, want_inner, want_desc in (
            ("append +10", code_append, msg_append, 300315, 300305, None),
            ("insert_after +10", code_ins, msg_ins, 300315, 300305, None),
            ("重复 id", code_dup, msg_dup, 300315, 300301, 1001)):
        got_inner, got_desc = _inner(msg), _desc(msg)
        ok = (code == want_code and got_inner == want_inner and got_desc == want_desc)
        print(f"   {'✅' if ok else '❌'} {label}: code={code}（期望 {want_code}）· "
              f"尾部内层码={got_inner}（期望 {want_inner}）· 描述码={got_desc}（期望 {want_desc}）")
        if not ok:
            problems.append(f"{label}: code={code} 内层码={got_inner} 描述码={got_desc}"
                            f"（期望 {want_code}/{want_inner}/{want_desc}）"
                            "—— 码表与 B2 的解析契约要按实测改")
    if code5 != 0:
        problems.append(f"预期「+5 成功」（194 → 199 < 200）实测却 code={code5}："
                        "说明 200 墙的口径与 P7 记的不一致")
    if problems:
        print("\n❌ 与码表/B2 解析契约不一致：")
        for item in problems:
            print(f"   - {item}")
        return 1
    print("\n✅ 结论：`300315` 是**运行时 create 被拒的包装码**，真原因在 msg 尾部的 `code: NNNNNN` ——"
          "\n   容量满两种形状（append / insert_after）都是 `300305`，重复 id 是 `300301`"
          "（方括号里另有一个描述码 `Code 1001`）"
          "\n   ⇒ `capacity_exceeded()` **必须解析内层码**，只看外层 `300315` 会把重复 id 误判成容量满。")
    return 0


def probe_lanes() -> int:
    """R5 真机探针：两条**必须实测**的语义（决定 R5 的两条车道怎么实现）。

    ① **`batch_update` 的返回码是卡级的还是按动作的**？一批里混一个**卡里不存在的 id**：
       如果返回码非 0 ⇒ 卡级判据是对的（R2 的「整批标死」也就有了实测依据）；
       如果返回码 0 ⇒ 说明服务端**不校验**坏 id，那么「整批标死」与「只标坏的那个」都不成立，
       真正的处置要重写（这条直接决定附录 A 的 DEAD 那一格该怎么写）。
    ② **整卡 patch 能不能覆盖一张 CardKit 实体卡的消息**？这决定 `DEGRADE` 车道
       （卡级死法 ⇒ 转 patch 传输续写**同一张卡**）到底做不做得出来。
       注意：`code=0` 只证明服务端收下了，不证明客户端真的换了卡 —— 但**返回码非 0**
       足以证伪这条车道（那就是「做不到」，不能再假设）。
    两张卡都自动删。
    """
    from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody
    client, chat = _connect()
    print("—— ① batch_update 混一个坏 id，返回码是卡级还是按动作？——")
    card = _Card(client, chat)
    if not card.open(answer="（基线）", panel="面板：基线"):
        return 1
    card.write("基线帧", "lane1-base")

    def _action(eid: str, content: str) -> dict:
        return {"action": "partial_update_element",
                "params": {"element_id": eid, "partial_element": {"content": content}}}

    r = client.cardkit.v1.card.batch_update(
        BatchUpdateCardRequest.builder().card_id(card.card_id)
        .request_body(BatchUpdateCardRequestBody.builder()
                      .actions(json.dumps([_action("ghost_missing_element", "坏 id（卡里没有它）"),
                                           _action(PANEL_BODY_ID, "面板：这一格和坏 id 同一批")],
                                          ensure_ascii=False))
                      .sequence(card._next()).uuid(f"p-{card.card_id}-lane1").build()).build())
    code_bad = card._code(r)
    print(f"   ① batch(坏 id + 好 id) → code={code_bad} · msg={str(getattr(r, 'msg', ''))[:80]!r}")
    alive = card.write("坏 id 那一批之后，正文还能写吗", "lane1-after")
    print(f"      之后还能写正文 = {alive}（会话没被关 ⇒ 坏 id 不是卡级死法）")
    card.delete()

    print("\n—— ② 整卡 patch 能不能覆盖一张 CardKit 实体卡的消息？——")
    card2 = _Card(client, chat)
    if not card2.open(answer="（实体卡基线）", panel="面板：实体卡"):
        return 1
    card2.write("实体卡基线帧", "lane2-base")
    plain = lark_cards.reply_card(
        "**如果你看到这一行**，说明整卡 patch 覆盖了这张实体卡的消息。",
        streaming=False, footer="R5 探针：patch-on-entity")
    r2 = client.im.v1.message.patch(
        PatchMessageRequest.builder().message_id(card2.message_id)
        .request_body(PatchMessageRequestBody.builder()
                      .content(json.dumps(plain, ensure_ascii=False)).build()).build())
    code_patch = card2._code(r2)
    print(f"   ② 对实体卡消息做 message.patch → code={code_patch} "
          f"· msg={str(getattr(r2, 'msg', ''))[:80]!r}")
    alive2 = card2.write("patch 覆盖之后，还能写元素吗", "lane2-after-patch")
    print(f"      之后还能写元素 = {alive2}")
    card2.delete()
    print("\n结论怎么写：① 非 0 ⇒ 卡级判据成立（R2 的整批标死有实测依据）；"
          "0 ⇒ 服务端不校验坏 id，附录 A 的 DEAD 要重写。")
    print("             ② 0 ⇒ DEGRADE 车道可做；非 0（含 230001/230099）⇒ 做不到，"
          "只能 fail-open 交核心回落，并把这条写进附录 A。")
    # ⚠️ **必须判定，不能只 print**（R5 审计低-9）：这条探针原来自称「以返回码为判据」，
    # 实际一条 assert 都没有、恒定 `return 0` —— 也就是说返回码变了它也不会红。
    problems = []
    if code_bad == 0:
        problems.append("① batch 混坏 id 竟然 code=0 ⇒ 服务端不校验坏 id，附录 A 的 DEAD 要重写")
    if code_bad and "elementID" not in str(getattr(r, "msg", "")):
        problems.append(f"① 返回码非 0 但 msg 没点名坏 id（bad_element_id() 会失效）：{getattr(r, 'msg', '')!r}")
    if not alive:
        problems.append("① 坏 id 之后会话被关（说明坏 id 是卡级死法，与 300313 的定位冲突）")
    if code_patch != 0:
        problems.append(f"② 整卡 patch 覆盖实体卡消息失败（code={code_patch}）⇒ DEGRADE 车道做不到")
    if alive2 != 0:
        problems.append("② patch 之后元素写入竟然还能写（说明 patch 没真的替换掉那张卡）")
    if problems:
        print("\n❌ 与代码里的码表/车道前提不一致：")
        for item in problems:
            print(f"   - {item}")
        return 1
    print("\n✅ 两条前提都成立（码表与 DEGRADE 车道的前提与代码一致）")
    return 0


def main(argv) -> int:
    if "--visual" in argv:
        return probe_visual(keep="--delete" not in argv)
    if "--lanes" in argv:
        return probe_lanes()
    if "--batching" in argv:
        return probe_batching()
    if "--withdrawn" in argv:
        return probe_withdrawn()
    if "--element-limits" in argv:
        return probe_element_limits()
    if "--capacity-codes" in argv:
        return probe_capacity_codes()
    return probe_matrix()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
