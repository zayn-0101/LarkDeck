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
3. 用 ``lark_oapi`` 以 ``im/v1/messages`` 发出**探针卡**（功能卡 + 双语互换实验 + 页脚样式对照）
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
    """把 core/cards.py 当包内模块加载，绕开 __init__.py（它会拉 Hermes 依赖）。"""
    pkg = types.ModuleType("_larkdeck_probe")
    pkg.__path__ = [str(REPO)]
    sys.modules["_larkdeck_probe"] = pkg
    return importlib.import_module("_larkdeck_probe.core.cards")


def load_adapter_parts():
    """同样绕开 ``__init__.py`` 加载 adapter / context（``__init__`` 会拉 Hermes 依赖）。

    挂在 ``_larkdeck_probe`` 这个合成包下，包内相对导入才会指向同一份模块 ——
    否则 ``cards`` 会出现两个模块对象，页脚和卡片用的就不是同一份状态了。
    """
    pkg = sys.modules.get("_larkdeck_probe")
    if pkg is None:
        pkg = types.ModuleType("_larkdeck_probe")
        pkg.__path__ = [str(REPO)]
        sys.modules["_larkdeck_probe"] = pkg
    return (importlib.import_module("_larkdeck_probe.core.adapter"),
            importlib.import_module("_larkdeck_probe.core.context"))


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


def build_footer_cases(cards) -> list:
    """页脚三样式对照卡 —— 走**真适配器**的页脚代码，不是手写字符串。

    页脚数据来自官方钩子（context.py），探针里没有真回合，所以喂一份仿真快照：
    模型名 + 45.2k/200k 上下文 + 12.3s 耗时。渲染路径与真机一致，只有数字是造的。
    """
    import time as _time

    _adapter, _context = load_adapter_parts()

    _context.reset()
    _context.set_context_override(200000)
    _context.record_api_call(
        model="deepseek-v4-flash", provider="opencode-go",
        usage={"prompt_tokens": 45200, "output_tokens": 320},
        api_call_count=3, session_id="probe", platform="feishu",
    )

    mark = f"{PROBE_MARK} · 页脚样式对照 · 只验渲染"
    labels = (("text", "⑦ 页脚【纯文字】当前默认"),
              ("bar", "⑧ 页脚【图形条】"),
              ("both", "⑨ 页脚【数字+条】"))
    cases = []
    for style, label in labels:
        _adapter._CONFIG["context_style"] = style
        # 页脚现在只放上下文用量（模型/耗时进了面板标题行），所以不再需要 started
        footer = _adapter.LarkDeckMixin._ld_footer()
        body = f"页脚样式 **{style}**。下面这行是真适配器算出来的：\n`{footer}`"
        cases.append((label, cards.reply_card(body, streaming=True,
                                              footer=f"{footer} · {mark}")))
    return cases


def build_cases(cards) -> list:
    mark = f"{PROBE_MARK} · larkdeck 渲染探针 · 只验渲染，不处理点击"
    panel_reasoning = ("用户问的是卡片渲染。核验三点：\n"
                       "1. 1.0 方言只带顶层 elements\n"
                       "2. 按钮必须在 action 容器里\n"
                       "3. 2.0 卡必须有 config.summary")
    panel_tools = [
        cards.tool_step("read_file", status="ok", duration_ms=12,
                        preview='{"path": "cards.py"}'),
        cards.tool_step("grep", status="ok", duration_ms=2300,
                        preview='{"pattern": "render.py"}'),
        cards.tool_step("terminal", status="running",
                        preview="probe_render.py --send"),
    ]
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
    ] + build_status_cases(cards, mark)


def build_status_cases(cards, mark: str) -> list:
    """状态色探针（阶段 1）：三种回合结局的边框色 + 无卡片 header 的观感。

    为什么必须真机跑（对应方案 R3「颜色枚举与样式都可能被飞书静默忽略」）：
    本地单测只能验 JSON 结构，颜色枚举合不合法、边框画不画得出来**只有飞书说了算** ——
    非法颜色不会报错，只会被忽略（边框回到默认灰），从返回码上完全看不出来。

    颜色语义与 aiduPOP 对齐（以它的**源码**为准，它的 README 把红/黄写反了）：
    绿 = 完成、红 = 报错、黄 = 中止。
    """
    todos = (("ok", "应该看到【绿色】边框"),
             ("error", "应该看到【红色】边框"),
             ("stopped", "应该看到【黄色】边框"))
    cases = []
    for status, expect in todos:
        node = cards.unified_panel(
            rounds=[{"text": "先看目录结构。", "elapsed_ms": 1200},
                    {"text": "再核对一下签名。", "elapsed_ms": 800}],
            tools=[cards.tool_step("read_file", status="ok", duration_ms=12,
                                   preview='{"path": "cards.py"}')],
            status=status,
            summary="🤖 deepseek-v4-flash · 🧠 2 · 🔧 1 · ⏱ 12.3s",
            expanded=True)
        card = cards.reply_card(f"状态色探针（status={status}）：{expect}。",
                               streaming=False, panel=node,
                               footer=f"{PROBE_MARK} · 状态色 {status} · 只验渲染")
        cases.append((f"⑩ 状态色【{status}】—— {expect}（这张卡没有卡片级 header）", card))

    # 只有结局、没有任何过程数据的回合：面板必须仍然渲染（否则状态色无处可放）
    only = cards.unified_panel(status="ok", summary="🤖 模型 · ⏱ 2.1s")
    cases.append(("⑪ 状态色【只有结局、无推理无工具】—— 应为绿边 + 一行 ✅ 文案",
                  cards.reply_card("这个回合没有工具调用、也没有推理增量。",
                                   streaming=False, panel=only,
                                   footer=f"{PROBE_MARK} · 状态色兜底 · 只验渲染")))
    return cases


def build_dialect_probe_cards(cards) -> list:
    """**2.0 交互组件探针**（阶段 4 的第一步：先量，不猜）。

    要回答的问题：决策门 D1 的结论「2.0 卡里用**组件级** ``behaviors`` 能把点击送到
    ``p2.card.action.trigger``」目前只有官方文档 + 第三方实测两重印证，**没有我们自己的
    一手真机证据**。而它是「澄清卡能不能改 2.0」的唯一前提 —— 要是它不成立，澄清卡就
    只能永远停在 1.0。

    为什么必须你亲手点：点击事件被 WebSocket 送进**正在跑的网关**，这个探针脚本接不到。
    适配器里为此留了一条只认 ``cards.PROBE_VALUE_KEY`` 的日志分支
    （``adapter._ld_log_probe_click``），点击真的到达时会打一行 INFO：

    ```
    [larkdeck] 探针点击到达 ✅ tag=select_static option='opt_b' input_value=None value={...}
    ```

    看到这行 = 2.0 组件的服务端回调**成立**（且能读出 ``action.option`` /
    ``action.input_value`` 的形状）；日志里什么都没有 = 点击根本没到服务端，
    澄清卡就继续用 1.0。

    卡片刻意只做两件事：一个下拉（选完就该回调）+ 一个输入框（回车就该回调）。
    按 ``AGENTS.md`` 不变量 5，**不许**在这张卡里混 1.0 的 ``action`` 按钮行。
    """
    probe = {cards.PROBE_VALUE_KEY: True}
    card = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "update_multi": True, "summary":
                   {"content": "LARKDECK 方言探针：点一下下拉 + 在输入框里回车"}},
        "body": {"elements": [
            {"tag": "markdown", "content":
             "**方言探针（2.0 组件级 behaviors）**\n"
             "请依次做两件事，每做一件就会有一条日志：\n"
             "1. 点开下面的下拉，随便选一项；\n"
             "2. 在输入框里敲几个字，然后**回车**。"},
            {"tag": "select_static",
             "placeholder": {"tag": "plain_text", "content": "选一个（应产生一条日志）"},
             "options": [
                 {"text": {"tag": "plain_text", "content": "选项 A"}, "value": "opt_a"},
                 {"text": {"tag": "plain_text", "content": "选项 B"}, "value": "opt_b"},
             ],
             "behaviors": [{"type": "callback", "value": {**probe, "kind": "select"}}]},
            {"tag": "input",
             "placeholder": {"tag": "plain_text", "content": "敲几个字再回车"},
             "label": {"tag": "plain_text", "content": "输入框（回车应产生一条日志）"},
             "behaviors": [{"type": "callback", "value": {**probe, "kind": "input"}}]},
            {"tag": "markdown", "text_size": "notation",
             "content": "LARKDECK-RENDER-PROBE · 方言探针 · 只验点击能不能到服务端"},
        ]},
    }
    # 对照组：1.0 的按钮行**不能**放进 2.0 卡（会被飞书拒收，230099）。
    # 不把它塞进用例里 —— 那会让整个探针跑出红色，而它「被拒」才是正确行为。
    cases = [("⑫ 方言探针【2.0 select_static + input】← 请点下拉 + 回车", card)]

    # ⑬⑭ 顺手把**生产代码真正会发的那两张 2.0 澄清卡**也发一遍：
    # 上面那张探针是手搓的最小结构，而 `clarify_card_2` 还带 `footnote()` 脚注、
    # input 的 label、多选走 `multi_select_static` —— 这些只有真发一次才知道飞书收不收
    # （本地单测只验结构）。翻 `clarify_dialect` 默认值之前必须先确证它们能发出去。
    cases.append(("⑬ 真 2.0 澄清卡（单选）—— clarify_card_2 的真实输出",
                  cards.clarify_card_2("这张 2.0 澄清卡渲染正常吗？",
                                       ["一切正常", "下拉没出来", "排版乱了"],
                                       clarify_id="probe-c2", session_key="probe-sk")))
    cases.append(("⑭ 真 2.0 澄清卡（多选，multi_select_static）",
                  cards.clarify_card_2("多选那张渲染正常吗？",
                                       ["模型名", "轮次", "工具数", "耗时"],
                                       clarify_id="probe-c2m", session_key="probe-sk",
                                       multi=True)))
    return cases


def build_bilingual_cases(cards) -> list:
    """双语机制回归探针：互换实验，验证客户端确实按 i18n_content 选语言。

    「互换实验」：把 i18n_content 的 ``zh_cn`` 里放英文。中文客户端如果显示英文，
    就证明它确实按 i18n_content 选语言，而不是回落到默认 ``content``；显示中文
    则说明 i18n 没生效（该去查飞书是不是改了行为）。

    为什么保留而非常规单测：生产卡两种语言各显示一份，**只看当前语言的卡片看不出
    机制死活** —— 互换方向才能主动探测。受试对象两层：2.0 的 markdown 元素（普通 +
    notation 脚注样式），以及 1.0 澄清卡的 header 标题 + 按钮；后者还兼作「飞书接不
    接受 1.0 标题/按钮上的 i18n_content」的探针，本地读代码读不出结论，只有 API
    返回码说了算。
    """
    swap = {"zh_cn": "SWAP HIT：看到这句英文 = 客户端确实在用 i18n_content ✅",
            "en_us": "SWAP MISS：看到这句中文 = 走了 en_us 分支"}

    def swap_text(tag: str, default: str) -> dict:
        return {"tag": tag, "content": default, "i18n_content": dict(swap)}

    # ⑤ 2.0：markdown + 纯文本两个元素各来一个互换实验（默认 content 是中文，作对照）
    two = cards.reply_card("双语机制实验（2.0）。下面两行是互换过语言的元素：",
                           streaming=True,
                           footer=f"{PROBE_MARK} · 双语实验 2.0 · 只验渲染")
    two["body"]["elements"][1:1] = [
        swap_text("markdown", "【互换 · 正文】默认中文：看到这行 = i18n 没生效"),
        {**swap_text("markdown", "【互换 · 脚注样式】默认中文：看到这行 = i18n 没生效"),
         "text_size": "notation"},
    ]

    # ⑥ 1.0：header 标题 + 每个按钮都带互换的 i18n_content
    legacy = cards.clarify_card("双语实验：这张 1.0 卡的标题和按钮都带了 i18n_content。",
                                ["选项 A", "选项 B"], clarify_id="probe-bi",
                                session_key="probe-sk")
    legacy["header"]["title"]["i18n_content"] = dict(swap)
    for el in legacy["elements"]:
        if el.get("tag") == "action":
            for btn in el.get("actions", []):
                text = btn.get("text")
                if isinstance(text, dict):
                    text["i18n_content"] = dict(swap)
    legacy["elements"].append(cards.note(f"{PROBE_MARK} · 双语实验 1.0 · 只验渲染"))

    return [("⑤ 双语实验（2.0）：markdown / 纯文本互换", two),
            ("⑥ 双语实验（1.0）：澄清卡 header + 按钮带 i18n_content", legacy)]


#: 打字机探针的文案（逐帧长大，模拟真实流式）。
_TYPING_STEPS = (
    "飞书客户端会不会把新出现",
    "飞书客户端会不会把新出现的字**逐字打出来**？",
    "飞书客户端会不会把新出现的字**逐字打出来**？\n\n"
    "（如果是一次性整段跳出来 = 没有打字机，内核的流式节奏就是最终观感）",
)


def patch(client, message_id: str, card: dict) -> tuple:
    """整卡替换（与生产 `send_stream_frame` / `edit_message` 同一条 API）。"""
    from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody
    body = PatchMessageRequestBody.builder().content(json.dumps(card, ensure_ascii=False)).build()
    req = PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
    resp = client.im.v1.message.patch(req)
    return resp.code, resp.msg


def probe_typewriter(client, chat: str, cards) -> int:
    """**打字机探针**（阶段 3 的第一步：先量，不猜）。

    要回答的问题：aiduPOP 的「15ms 打字机」是卡里的
    ``streaming_config.print_frequency_ms`` 造成的**客户端逐字动画**。我们目前走
    ``im.v1.message.patch`` 整卡替换、卡里**没有** ``streaming_config`` ——
    于是「能不能拿到打字机」取决于两件事，都不是读代码能定的：

      1. 飞书**接不接受** ``message.patch`` 送来的 ``streaming_config``（不接受会返回非 0）；
      2. 接受之后**客户端有没有真的逐字打**（这是纯客户端的，API 返回码看不出来，只能肉眼）。

    所以这里发两张卡、逐帧长大，A/B 对照：甲带 ``streaming_config``、乙不带。
    哪张是逐字打出来的，一眼就知道 —— 结论直接决定阶段 3 要不要换传输
    （换成 CardKit 卡片实体 + ``card_element.content``）。
    """
    import time as _time

    cfg = cards.STREAMING_CONFIG if hasattr(cards, "STREAMING_CONFIG") else {
        "print_frequency_ms": {"default": 15},
        "print_step": {"default": 1},
        "print_strategy": "fast",
    }
    variants = (
        ("甲（带 streaming_config，print_frequency_ms=15）", dict(cfg)),
        ("乙（对照组：不带 streaming_config）", None),
    )
    codes = []
    for label, streaming_config in variants:
        base = cards.reply_card("", streaming=True, footer="LARKDECK-RENDER-PROBE · 打字机探针")
        if streaming_config is not None:
            base["config"]["streaming_config"] = streaming_config
        code, msg, mid = send(client, chat, base)
        codes.append((label, code, msg))
        print(f"{'✅' if code == 0 else '❌'} 打字机探针 · {label}  code={code} msg={msg} id={mid}")
        if code != 0:
            continue
        _save_sent_ids(_load_sent_ids() + [mid])
        # 逐帧长大：每步 150ms（≈ 生产节流 250ms 的节奏），模拟真实流式
        for text in _TYPING_STEPS:
            node = cards.reply_card(text, streaming=True,
                                    footer="LARKDECK-RENDER-PROBE · 打字机探针")
            if streaming_config is not None:
                node["config"]["streaming_config"] = streaming_config
            pcode, pmsg = patch(client, mid, node)
            if pcode != 0:
                print(f"   ❌ patch 被拒：code={pcode} msg={pmsg} —— "
                      f"streaming_config 在 patch 路径上不被接受，阶段 3 不能直接加它")
                codes[-1] = (label, pcode, pmsg)
                break
            _time.sleep(0.15)
    print()
    print("👀 现在去飞书 DM 看那两张「打字机探针」卡：哪一张的字是**逐个打出来**的？")
    print("   * 甲逐字打、乙整段跳 → 只要给流式卡加 streaming_config 就够了（阶段 3 大幅缩小）；")
    print("   * 两张都是整段跳   → message.patch 拿不到打字机，要换 CardKit 卡片实体传输；")
    print("   * 飞书直接拒了甲   → streaming_config 不适用于 patch 路径（上面会打 ❌）。")
    return 0 if all(code == 0 for _, code, _ in codes) else 1


#: 字节阶梯探针要试探的目标大小（utf-8 字节）。两个同类项目给的上限各不相同：
#: hermes-feishu-streaming-card 的 ``SAFE_CARD_JSON_BYTES = 28_000``、
#: hermes-lark-streaming 注释里的「卡片总大小 30KB 上限」（``200860``）——
#: 而本项目的 ``CARD_BYTE_BUDGET`` 是个**没有权威依据的保守猜测（40000）**。
#: 上限高估的后果不是「卡片太大被拒」这么简单：一帧失败会被内核判成「本回合 native
#: 不可用」，之后输出退化成多条纯文本。所以这件事必须真机量出来。
_BYTE_LADDER = (40000, 48000, 56000, 64000, 80000)

#: 元素阶梯：同类项目（hermes-feishu-streaming-card）用 ``FEISHU_MAX_ELEMENTS = 200``，
#: 但那是它的常量、不是官方数字。计数口径是「**整卡里所有带 tag 的对象**」（含嵌套），
#: 所以这里按生产卡的真实形状来试探 —— 把一个折叠面板塞满，而不是平铺一堆元素。
_ELEMENT_LADDER = (196, 200, 201, 204)


def probe_byte_limit(client, chat: str, cards) -> int:
    """**卡片字节上限探针**：一路加码发到飞书拒收，把真实阈值钉出来。

    做法：正文用纯 ASCII（1 字符 = 1 字节，好控），把整卡 JSON 的 utf-8 字节数
    顶到目标值附近再发。返回码就是答案（``code=0`` 收下、非 0 拒收）。
    每一张卡都带探针标记，会被下次清理带走。
    """
    print("逐档试探卡片字节上限（正文用 ASCII 精确控字节）：")
    accepted = []
    for target in _BYTE_LADDER:
        # 先估一个正文长度，再按实际序列化字节回调到目标附近（迭代两次足够）
        body = "x" * max(1, target - 1500)
        card = cards.reply_card(body, streaming=False,
                                footer=f"{PROBE_MARK} · 字节阶梯 {target}")
        for _ in range(3):
            actual = len(json.dumps(card, ensure_ascii=False).encode("utf-8"))
            delta = target - actual
            if abs(delta) < 200:
                break
            body = "x" * max(1, len(body) + delta)
            card = cards.reply_card(body, streaming=False,
                                    footer=f"{PROBE_MARK} · 字节阶梯 {target}")
        nbytes = len(json.dumps(card, ensure_ascii=False).encode("utf-8"))
        code, msg, mid = send(client, chat, card)
        flag = "✅" if code == 0 else "❌"
        print(f"  {flag} 目标 {target:>6} 字节 → 实际 {nbytes:>6} 字节  code={code}  msg={msg}")
        if code == 0:
            accepted.append(nbytes)
            _save_sent_ids(_load_sent_ids() + [mid])
            # **流式路径走的是 PATCH，不是 create** —— 上限可能不同，必须单独量。
            # 建卡成功后原地 patch 成同样大的卡，看得的是同一个数字。
            pcode, pmsg = patch(client, mid, card)
            print(f"     ↳ 同尺寸 PATCH（流式帧走的就是这条）code={pcode}  msg={pmsg}")
            if pcode != 0:
                print("       ⚠️ PATCH 的上限比 CREATE 低！流式卡必须按 PATCH 的上限来定预算")
        else:
            print(f"     ↑ 被拒的那张卡带在飞书侧，msg 是唯一线索：{msg}")
    if accepted:
        print(f"\n📏 实测被接受的**最大**卡片 = {max(accepted)} 字节")
        print("   → CARD_BYTE_BUDGET 必须小于它（建议留 15%~30% 余量），"
              "并把这个数字写回 core/cards.py 的注释里")
    return 0


def probe_element_limit(client, chat: str, cards) -> int:
    """**元素数上限探针**：把折叠面板塞满 markdown，一路加到飞书拒收。

    为什么要真机量：``FEISHU_ELEMENT_LIMIT`` 这类常量在同类项目里各写各的（200 / 180），
    而撞上它的后果是**整张卡完全不渲染**（不是截断）。我们的面板元素数由
    ``max_panel_steps`` 与推理轮数决定，用户把配置调大就会直接撞墙 ——
    所以这个数字必须是量出来的，不是抄来的。
    """
    print("逐档试探卡片元素数上限（把折叠面板塞满）：")
    accepted = []
    for target in _ELEMENT_LADDER:
        children = [cards.md(f"第 {i} 行") for i in range(max(1, target - 3))]
        panel = cards.collapsible({"tag": "plain_text", "content": "元素阶梯"},
                                  children, expanded=True)
        card = cards.reply_card("元素数探针", streaming=False, panel=panel,
                                footer=f"{PROBE_MARK} · 元素阶梯 {target}")
        counted = cards.count_elements(card)
        code, msg, mid = send(client, chat, card)
        flag = "✅" if code == 0 else "❌"
        print(f"  {flag} 目标 {target:>4} 元素 → 递归实测 {counted:>4} 元素  code={code}  msg={msg}")
        if code == 0:
            accepted.append(counted)
            _save_sent_ids(_load_sent_ids() + [mid])
    if accepted:
        print(f"\n📏 实测被接受的**最大**元素数 = {max(accepted)}")
        print("   → cards.FEISHU_ELEMENT_LIMIT 必须小于它")
    return 0


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
    if "--typing" in argv:
        return probe_typewriter(client, chat, cards)
    if "--bytes" in argv:
        return probe_byte_limit(client, chat, cards)
    if "--elements" in argv:
        return probe_element_limit(client, chat, cards)

    cases = (build_cases(cards) + build_bilingual_cases(cards) + build_footer_cases(cards)
             + build_dialect_probe_cards(cards))
    ok = True
    byte_report: list = []
    for label, card in cases:
        code, msg, mid = send(client, chat, card)
        dialect = card.get("schema", "1.0(legacy)")
        status = "✅" if code == 0 else "❌"
        if code != 0:
            ok = False
        # 字节数必须量出来 —— 这是 `cards.CARD_BYTE_BUDGET` 的唯一权威来源。
        # 注意量纲是 **utf-8 字节**，不是字符（汉字 3 字节，按字符估会低估 3 倍）。
        nbytes = len(json.dumps(card, ensure_ascii=False).encode("utf-8"))
        byte_report.append((label, nbytes, code))
        print(f"{status} {label}")
        print(f"     方言={dialect}  code={code}  msg={msg}  id={mid}  字节={nbytes}")
        if code != 0:
            one_line = json.dumps(card, ensure_ascii=False)
            print(f"     被拒卡片({len(one_line)} 字符): {one_line[:900]}")
        else:
            _save_sent_ids(_load_sent_ids() + [mid])

    # 把「飞书接受过的最大字节数」打成一行，方便直接更新 CARD_BYTE_BUDGET。
    if byte_report:
        accepted = [(n, lb) for lb, n, code in byte_report if code == 0]
        if accepted:
            top = max(accepted)
            print()
            print(f"📏 飞书本轮接受的最大卡片 = {top[0]} 字节（{top[1]}）")
            print(f"   → `core/cards.py` 的 CARD_BYTE_BUDGET 应不小于这个数的一个安全倍数")

    print()
    print("全部被飞书接收 ✅ —— 去飞书 DM 看九张卡（③ 记得点一下三角箭头；⑤⑥ 是双语互换实验，看到英文说明 i18n 生效；⑦⑧⑨ 是页脚三样式对照）" if ok
          else "有卡片被拒 ❌ —— 按上面飞书给的 msg 改")
    print("注意：按钮点击不会被处理（本探针只验渲染，不验点击）；"
          "打字机单独跑：`--typing`。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
