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
默认模式不启动 Hermes、不加载插件、不改任何配置，**按钮点击不会被处理**，只验渲染。

带参数的模式会做更多事（各自在自己的 docstring 里写清了为什么必须真机跑）::

    --typing       打字机 A/B（两张卡交替长大，肉眼比；动画只能靠眼睛）
    --bytes        卡片字节上限阶梯（create + patch 都打）
    --elements     元素数上限阶梯（飞书硬上限 200）
    --rate-limit   连续 patch 的限流实证（退避重试该收哪个错误码）
    --cardkit      **打字机传输的 A/B**：甲=普通卡 + streaming_config，乙=CardKit 卡片实体
                   + card_element.content 逐帧。两张卡并排留在 DM 里等你看哪张在逐字
    --stop-redraw  **中止重绘的真机端到端**：正文超降载预算但发得出去时，
                   /stop 必须真的把那张卡重绘成中止色。**两条路径都跑**：
                   ① 非 native（`send` + `message.patch`）；② 真 native 流式
                   （`send_stream_frame` 建卡 → 若干帧 → `/stop`）。会加载真适配器
    --clean-only   只清理上次的探针卡
    --no-clean     不清理，直接发（排查清理逻辑时用）
    --button-2     **只发 ⑮ 一张**：2.0 `button` + 组件级 `behaviors`。不变量 5 里
                   「组件可以是 button」这句**只有官方文档**（真机点过的只有 select_static /
                   multi_select_static / input）—— 这一格**只有真机点击能判**。
                   发一张、点一下、看日志里有没有 `探针点击到达 ✅ tag=button`，就这些。
                   （单独一条命令，是为了不用在一批探针卡里找那一张。）

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
        # ⚠️ **只删本机器人自己发的**（2026-09-16 审计实测的隐患）：标记里混着
        # `这张卡渲染正常吗？` 这种**自然中文句子**，而补扫原先不看发送者 ⇒ 理论上会把
        # 用户/agent 的真实消息一起删掉。这与 `AGENTS.md` 的红线
        # 「清理只许按 `message_id` 删」相抵触 —— ID 那条路已经天然是「自己发的」，
        # 这条内容补扫就得自己补上这个判据。
        sender = item.sender
        if sender is not None and getattr(sender, "id_type", None) not in (None, "app_id"):
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
        ("⓪ 首帧占位卡（这一张就是**回合刚开始那一秒**的样子）",
         cards.reply_card("", streaming=True, footer=mark)),
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


def _probe_button_card(probe: dict) -> dict:
    """⑮ 用的那张卡：**一个 2.0 按钮**（组件级 ``behaviors``）。

    ⚠️ 只放 ``button`` + 说明文字，**不放** 1.0 的 ``action`` 行 —— 混用会被飞书拒收
    （``230099``），而这张卡要回答的问题恰恰是「**单独一个 2.0 按钮**能不能把点击
    送到服务端」。混进来就把问题换掉了。
    """
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "update_multi": True,
                   "summary": {"content": "LARKDECK 按钮探针：点一下那个按钮"}},
        "body": {"elements": [
            {"tag": "markdown", "content":
             "**2.0 按钮探针（组件级 `behaviors`）**\n"
             "请点下面那个按钮。日志里应当出现一行 `探针点击到达 ✅ tag=button`。"},
            {"tag": "button",
             "text": {"tag": "plain_text", "content": "点我一个（应产生一条日志）"},
             "type": "primary",
             "behaviors": [{"type": "callback", "value": {**probe, "kind": "button"}}]},
            {"tag": "markdown", "text_size": "notation",
             "content": "LARKDECK-RENDER-PROBE · 按钮探针 · 只验 2.0 button 的点击能不能到服务端"},
        ]},
    }


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

    ⑮ 补的是**另一格**：``button`` + 组件级 ``behaviors``。不变量 5 里「组件可以是
    ``button``」这句**只有官方文档**支撑（真机点过的只有 ``select_static`` / ``input``），
    而卡片上的按钮比下拉直观得多 —— 要不要给澄清卡加按钮形态，取决于这一格成不成立。

    按 ``AGENTS.md`` 不变量 5，**不许**在这些卡里混 1.0 的 ``action`` 按钮行
    （混用会被飞书拒收 ``230099``，而 ⑮ 要回答的正是「**单独一个 2.0 按钮**行不行」）。
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
    # ⑮ **2.0 的 `button` + 组件级 `behaviors`** —— 不变量 5 里**唯一没有真机证据**的那一格。
    #    不变量 5 写的是「组件可以是 `button` / `select_static` / `multi_select_static` / `input`」，
    #    可真机点过的只有后三种（⑫⑬⑭）—— `button` 这一格**只有官方文档**。
    #    值不值得补：卡片上的按钮比下拉**直观得多**（选项一眼看见、一下点完），而我们要不要给
    #    澄清卡加按钮形态，完全取决于这一格成不成立。点一下、看日志里有没有 `tag=button` 即可。
    #    ⚠️ 这是**纯探针**：不带 `larkdeck_action` ⇒ 点它不改卡、不提交任何澄清，只看日志。
    cases.append(("⑮ 2.0 按钮探针【button + 组件级 behaviors】← 请点一下按钮",
                  _probe_button_card(probe)))
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
#: 打字机探针的正文：**逐字长出来**，让「是逐字打还是一整段跳」这件事有一个可看的窗口。
#: 一次 2 个字、每步 0.35s ⇒ 一行字要 ~10 秒才长完。原来只推 3 步、每步 0.15s
#: （共 0.45 秒），等于要求人正好盯着屏幕 —— 那不是「可验证」，那是碰运气。
_TYPING_BASE = ("打字机对照：这一行应该一个字一个字地冒出来，而不是整段一起跳出来。"
                "看到这里说明这一行已经长完了 —— 甲卡是逐步冒的，乙卡是一次跳的。")
_TYPING_CHAR_MS = 350
_TYPING_CHARS_PER_STEP = 2


def _typing_frames() -> list:
    """把正文切成逐步长出来的若干帧（最后多留一帧完整态）。"""
    text = _TYPING_BASE
    frames = [text[:i] for i in range(_TYPING_CHARS_PER_STEP, len(text),
                                      _TYPING_CHARS_PER_STEP)]
    frames.append(text)
    return frames


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
    frames = _typing_frames()
    if not frames:
        # 「没测到」不能说成「没问题」—— 探针必须自己挡住这种假结论
        print("⚠️ 没有任何帧可推，A/B 对照不成立（探针自身有问题，不是打字机的结论）")
        return 1
    print()
    print("=" * 64)
    print(f"👀 现在盯住飞书 DM —— 接下来约 {len(frames) * _TYPING_CHAR_MS / 1000:.0f} 秒里，")
    print("   那张【甲】卡的字应该一个个往外冒；【乙】卡应该一整段跳出来。")
    print("=" * 64)
    targets: list = []          # [(label, message_id, streaming_config)]，顺序即甲乙
    for label, streaming_config in variants:
        base = cards.reply_card("", streaming=True, footer="LARKDECK-RENDER-PROBE · 打字机探针")
        if streaming_config is not None:
            base["config"]["streaming_config"] = streaming_config
        code, msg, mid = send(client, chat, base)
        codes.append((label, code, msg))
        print(f"{'✅' if code == 0 else '❌'} 打字机探针 · {label}  code={code} msg={msg} id={mid}")
        if code == 0 and mid:
            _save_sent_ids(_load_sent_ids() + [mid])
            targets.append((label, mid, streaming_config))
    if len(targets) < 2:
        print("   ⚠️ 两张卡没都建起来，A/B 对照不成立（看上面的错误码）")
        return 1
    # 两张卡**交替**更新：同一时刻屏幕上各有一张在动，A/B 对照才成立
    for index, text in enumerate(frames):
        for label, mid, streaming_config in targets:
            node = cards.reply_card(text, streaming=True,
                                    footer="LARKDECK-RENDER-PROBE · 打字机探针")
            node["config"]["streaming_config"] = streaming_config or {}
            if not streaming_config:
                node["config"].pop("streaming_config", None)
            pcode, pmsg = patch(client, mid, node)
            if pcode != 0:
                print(f"   ❌ 第 {index + 1} 帧 patch 被拒（{label}）：code={pcode} msg={pmsg}")
                return 1
        _time.sleep(_TYPING_CHAR_MS / 1000)
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
_BYTE_LADDER = (80000, 96000, 112000, 128000, 160000, 200000)

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
        print("   → 这是飞书的**硬上限**（不是我们的降载预算）：CARD_BYTE_BUDGET 是质量取舍，"
              "_MAX_TRACKED_TEXT 则要贴着硬上限才能保证「发得出去的卡都存得下正文」")
    else:
        print("\n⚠️ 一档都没被接受 —— 这不是「上限很低」，可能是凭据/权限/网络问题，"
              "先看上面每档的 msg")
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


#: 限流探针的突发次数与目标间隔。官方 `im.v1.message.patch` 文档写的是
#: **单条消息 5 QPS（≥200ms/帧）**；我们生产用的 `_STREAM_MIN_INTERVAL = 0.25`（4/s）
#: 就是贴着这个上限取的安全值 —— 但那个「5 QPS」目前**只有文档背书**。
#: 这里往一张**一次性的探针卡**上打突发，把真实的限流码与阈值量出来。
_RATE_BURST = 16


def probe_rate_limit(client, chat: str, cards) -> int:
    """**限流实证**：往一张探针卡上打突发 patch，看飞书到底在什么时候、用什么码拒绝。

    为什么要量：`adapter._TRANSIENT_CODES` 里到底该放哪个码，目前是从接口文档推的
    （`230020` 是 patch 文档明写的频率限制；`99991400` 是通用码）。而「退避重试」这条
    修复的价值**完全取决于**真实返回的是哪个码 —— 猜错就是一次都不会启动（静默）。

    做法：建一张卡 → 连续 16 次 patch（不留间隔）→ 打印每次的 code 与耗时。
    只打**这一张探针卡**（频控是单条消息的），跑完随其他探针卡一起被清理。
    """
    import time as _time

    body = cards.reply_card("限流探针（这张卡会被连续快速更新，属一次性探针）",
                            streaming=True, footer="LARKDECK-RENDER-PROBE · 限流探针")
    code, msg, mid = send(client, chat, body)
    print(f"{'✅' if code == 0 else '❌'} 限流探针建卡 code={code} msg={msg} id={mid}")
    if code != 0:
        return 1
    _save_sent_ids(_load_sent_ids() + [mid])

    results: list = []
    for i in range(_RATE_BURST):
        node = cards.reply_card("限流探针 " + "。" * (i + 1), streaming=True,
                                footer="LARKDECK-RENDER-PROBE · 限流探针")
        t0 = _time.perf_counter()
        pcode, pmsg = patch(client, mid, node)
        dt_ms = (_time.perf_counter() - t0) * 1000
        results.append((i + 1, pcode, round(dt_ms, 1), pmsg))
        if pcode != 0:
            print(f"  ⛔ 第 {i + 1} 次被拒：code={pcode} msg={pmsg}（前一次耗时 {dt_ms:.0f}ms）")
            break
    if len(results) < _RATE_BURST:
        print(f"  ⚠️ 只测到 {len(results)}/{_RATE_BURST} 次就中断了，"
              f"下面的结论**不成立**（先看上面那条错误码）")
        return 1
    ok_n = sum(1 for _, c, _, _ in results if c == 0)
    print(f"  连打 {len(results)} 次，成功 {ok_n} 次；单次耗时 "
          f"{[r[2] for r in results]}")
    slowest = max(r[2] for r in results)
    if slowest > 0:
        print(f"  → 最快可达节奏 ≈ {1000 / slowest:.1f} 次/秒（这是**本地串行**的往返，"
              f"不是服务端上限）")
    bad = [r for r in results if r[1] != 0]
    if bad:
        print(f"  → 实测限流码 = {bad[0][1]}（`adapter._TRANSIENT_CODES` 必须包含它）")
    else:
        print("  → 16 次连打没被拒：说明 5 QPS 不是硬拒绝，或频控窗口比这更宽 —— "
              "`_STREAM_MIN_INTERVAL` 保持现状（贴文档上限的安全值）即可")
    return 0


def probe_cardkit(client, chat: str, cards) -> int:
    """**CardKit 打字机 A/B**：证明「打字机该走哪条传输」，并把结论摆到眼前。

    要回答的问题：aiduPOP 的逐字打字机到底靠什么？我们现在的做法是在**普通卡**里带
    ``config.streaming_config``（飞书接受，``code=0``）。飞书那套「服务端流式卡片」
    还有另一条路 —— **CardKit 卡片实体**：``cardkit.v1.card.create`` 建实体 →
    ``im.v1.message.create`` 发一条 ``{"type":"card","data":{"card_id":…}}`` →
    ``cardkit.v1.card_element.content`` 按 ``sequence`` 逐帧写元素内容 →
    ``cardkit.v1.card.settings`` 收尾（``streaming_mode: false``）。

    2026-09-13 真机实测：这条链**每一步都是 ``code=0``**（见下面的打印），
    也就是说「打字机需要 CardKit 实体」在**传输层是可行的**、SDK 也就位（Hermes 自身
    完全没用 CardKit，所以不会有冲突）。剩下唯一的问题是**客户端会不会逐字打**——
    那是纯客户端行为，API 返回码看不到，只能眼睛判。

    所以这个探针发**两张卡并排**：
      * 甲 = 我们现在的做法（普通卡 + ``streaming_config``）；
      * 乙 = CardKit 实体 + ``card_element.content`` 逐帧。
    两张都逐帧长大，哪张在逐字、哪张整段跳，一眼就能定。
    **别加 `--no-clean` 之外的花样**：跑完两张卡留在 DM 里等你看，它们的 ID 记在探针账本里，
    下次默认跑探针时会一起清掉。
    """
    import time as _time

    from lark_oapi.api.cardkit.v1 import (
        CreateCardRequest, CreateCardRequestBody, ContentCardElementRequest,
        ContentCardElementRequestBody, SettingsCardRequest, SettingsCardRequestBody)
    from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

    element_id = "answer"
    panel_id = "panel_body"
    text = ("这是一次**打字机传输**的对照实验。\n\n"
            "甲卡走的是普通 message.create + config.streaming_config（每帧 message.patch 整卡替换）；"
            "乙卡走的是 CardKit 卡片实体，**正文与面板两个元素分别用 card_element.content 逐帧写**。\n\n"
            "哪张卡的字是一个个冒出来的，哪张就是真的打字机 —— 请盯住这两张卡。") * 2
    panel_text = "**第 1 轮 · 1.2s**\n\n先想一下这个问题该怎么拆。\n\n🔧 terminal ✅ 42ms"

    # 甲：我们现在的做法
    plain = cards.reply_card(text, streaming=True, footer="LARKDECK-RENDER-PROBE · 甲（普通卡）")
    code, msg, mid_a = send(client, chat, plain)
    print(f"{'✅' if code == 0 else '❌'} 甲（普通卡 + streaming_config）：code={code} msg={msg} id={mid_a}")
    if mid_a:
        _save_sent_ids(_load_sent_ids() + [mid_a])

    # 乙：CardKit 实体
    # ⚠️ 结构必须**在建实体时定死**：2026-09-13 真机实测 —— 任何**结构性写入**
    # （`message.patch` 或 `cardkit.v1.card.update`）都会**关闭流式会话**，
    # 之后再写元素会拿到 `300309 streaming mode is closed`。所以乙卡的结构是
    # 「answer 元素 + 一个折叠面板（面板里一个 markdown 子元素）」，
    # 流式期间只做 `card_element.content` 写入（正文与面板**两个元素分别写**，实测都 `code=0`），
    # 收尾那一帧才 `message.patch` 整卡替换（补状态色/页脚）—— 那时流式本来就结束了。
    card_json = {
        "schema": "2.0",
        "config": {"streaming_mode": True, "update_multi": True},
        "body": {"elements": [
            {"tag": "markdown", "element_id": element_id, "content": text[:8]},
            {"tag": "collapsible_panel", "element_id": "panel", "expanded": False,
             "header": {"title": {"tag": "plain_text", "content": "执行详情"},
                        "vertical_align": "center",
                        "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined",
                                 "size": "16px 16px"},
                        "icon_position": "right", "icon_expanded_angle": -180},
             "border": {"color": "grey", "corner_radius": "8px"},
             "padding": "8px 8px 8px 8px",
             "elements": [{"tag": "markdown", "element_id": panel_id,
                           "content": panel_text[:12]}]},
        ]},
    }
    create_body = (CreateCardRequestBody.builder().type("card_json")
                   .data(json.dumps(card_json, ensure_ascii=False)).build())
    resp = client.cardkit.v1.card.create(
        CreateCardRequest.builder().request_body(create_body).build())
    card_id = getattr(resp.data, "card_id", None) if resp.data else None
    print(f"   card.create code={resp.code} msg={resp.msg} card_id={card_id}")
    if resp.code != 0 or not card_id:
        print("❌ CardKit 建卡片实体失败 —— 打字机若要 CardKit，这条路当前走不通")
        return 1
    msg_body = (CreateMessageRequestBody.builder().receive_id(chat).msg_type("interactive")
                .content(json.dumps({"type": "card", "data": {"card_id": card_id}})).build())
    sent = client.im.v1.message.create(
        CreateMessageRequest.builder().receive_id_type("chat_id")
        .request_body(msg_body).build())
    mid_b = sent.data.message_id if sent.data else None
    print(f"   发实体卡 code={sent.code} msg={sent.msg} id={mid_b}")
    if mid_b:
        _save_sent_ids(_load_sent_ids() + [mid_b])
    if sent.code != 0 or not mid_b:
        print("❌ 实体卡发不出去（content 形式不对？）")
        return 1

    # ⚠️ 对照必须**公平**：两张卡都以同样的节奏、同样的切分长大，否则看到的差异可能只是
    # 「一张根本没更新」。甲用 `message.patch` 整卡替换（我们现在的做法），乙用
    # `card_element.content` 逐帧写元素（CardKit）。
    print(f"👀 现在盯住飞书 DM：接下来约 {8 * 0.8:.0f} 秒里，两张卡的字都会往外长 ——")
    print("   甲 = message.patch 整卡替换（我们现在的方式，卡里带 streaming_config）")
    print("   乙 = CardKit card_element.content 逐帧写元素")
    print("   哪张在**逐字**打、哪张**整段**跳，就是这两条传输的差别。")
    seq = 0
    for step_no in range(1, 9):
        cut = min(len(text), 8 + step_no * 12)
        grown = cards.reply_card(text[:cut], streaming=True,
                                 panel=cards.unified_panel(reasoning=panel_text[:step_no * 3]),
                                 footer="LARKDECK-RENDER-PROBE · 甲（普通卡）")
        p_code, p_msg = patch(client, mid_a, grown) if mid_a else (None, "no id")
        codes = []
        for eid, payload in ((element_id, text[:cut]),
                            (panel_id, panel_text[:step_no * 4])):
            seq += 1
            body = (ContentCardElementRequestBody.builder().content(payload)
                    .sequence(seq).uuid(f"lk-probe-{eid}-{seq}").build())
            step = client.cardkit.v1.card_element.content(
                ContentCardElementRequest.builder().card_id(card_id)
                .element_id(eid).request_body(body).build())
            codes.append((eid, step.code))
        print(f"   seq={step_no} 字符={cut} · 甲 patch code={p_code} · 乙 content {codes}")
        bad = [c for eid, c in codes if c != 0]
        if bad:
            print(f"❌ card_element.content 被拒 {codes} —— 打字机的流式写入走不通")
            return 1
        if p_code not in (0, None):
            print(f"❌ 甲（对照卡）的 patch 也失败了 code={p_code} —— 这张对照无效：{p_msg}")
            return 1
        _time.sleep(0.8)
    # 收尾：**整卡替换**（那时流式已经结束，patch 会把会话关掉，正好收尾）
    final_card = cards.reply_card(text, streaming=False,
                                  panel=cards.unified_panel(reasoning=panel_text, status="ok"),
                                  footer="LARKDECK-RENDER-PROBE · 乙（CardKit）")
    fin_code, fin_msg = patch(client, mid_b, final_card)
    print(f"   收尾：乙卡 message.patch（带状态色/面板）code={fin_code} msg={fin_msg}")
    if fin_code != 0:
        print("❌ 乙卡收尾整卡替换失败 —— 实体卡上用 patch 收尾这条路走不通")
        return 1
    in_final = "green" in json.dumps(final_card, ensure_ascii=False)
    print(f"   （收尾帧里带绿色状态色 = {in_final}；真机上应当能看到乙卡边框变绿）")
    print()
    print("结论（传输层）：CardKit 实体链每一步都通（正文与嵌套面板元素分别流式写入 + patch 收尾）。")
    print("   实测到的硬约束：**任何结构性写入（message.patch / card.update）都会关闭流式会话**，")
    print("   之后的 card_element.content 会拿到 300309 —— 所以结构必须建实体时定死。")
    print("**动画本身只能眼睛判** ——")
    print("  甲逐字 ⇒ 我们现在的做法就够（不必付 CardKit 的复杂度）；")
    print("  乙逐字而甲整段 ⇒ 打字机必须走 CardKit 实体（实现是下一步的独立工作）。")
    return 0


def probe_cardkit_transport(client, chat: str, cards) -> int:
    """**生产路径**的 CardKit 传输真机验证（阶段 9）。

    与 `--cardkit`（我自己复刻的一条链）不同：这里**不配置任何传输**，直接对**真适配器**
    调 `send_stream_frame`（生产契约：text 是累积全文）—— 所以它验的是**用户不写配置时
    真正生效的那条路**（2026-09-13 起默认就是 cardkit；哪天有人把默认翻回去，这里会如实
    打印出 `patch`，并因为「建实体 0 次」当场失败）。断言：
      * seed 帧建起实体卡（`card.create` + 发实体卡都 `code=0`）；
      * 每帧正文走一次 `card_element.content`，装饰只在**内容变了**时走一次
        `card.batch_update`（首帧带面板+页脚，面板变化那一帧**只带 panel_body**）；
      * 序号单调递增（`code=0` 是唯一判据）；
      * 收尾帧走 `message.patch`（那一刻流式本来就结束，patch 关掉会话正好）；
      * 全程没有掉 native（掉 native = 卡会变成一条条纯文本）。
    """
    import asyncio
    _load, adapter, _panel = _load_adapter_for_probe(chat)
    if adapter is None:
        return 1
    calls = {"content": [], "patch": 0, "create": 0, "send": 0, "batch": []}
    original_write = adapter._ld_ck_write
    original_batch = adapter._ld_ck_batch
    original_update = adapter._ld_update_card
    # ⚠️ **turn_id 必须全程复用**：`_ld_stream_frame` 不把 turn_id 存进 state，
    # 所以「第一帧用 ckt-xxx、后续帧用 state 里读出来的空串」会让 key 从 `chat:ckt-xxx`
    # 变成 `chat` ⇒ state 变 None ⇒ 又走 seed **再建一张卡**，而第一张永远收不到写入、
    # 停在流式态（第十一路审计实测：探针因此**谎报通过**、还在 DM 里留冻结卡）。
    tid = f"ckt-{int(time.time())}"
    _orig_card_create = adapter._client.cardkit.v1.card.create
    _orig_msg_create = adapter._client.im.v1.message.create

    def _count_card_create(request):
        calls["create"] += 1
        return _orig_card_create(request)

    def _count_msg_create(request):
        calls["send"] += 1
        return _orig_msg_create(request)

    adapter._client.cardkit.v1.card.create = _count_card_create
    adapter._client.im.v1.message.create = _count_msg_create

    async def _spy_write(card_id, element_id, content, sequence):
        # ⚠️ 生产返回的是 `_CkResult`（R5 起，带码与 msg）—— 探针必须**原样转发**，
        # 不能在这里 `bool(...)`：那会把 R5 的降级车道判据抹掉（探针自己造的假象）。
        res = await original_write(card_id, element_id, content, sequence)
        calls["content"].append((element_id, sequence, res.ok, content))
        return res

    async def _spy_batch(card_id, ops, sequence):
        res = await original_batch(card_id, ops, sequence)
        # 记下**每个元素被写进去的内容**（不只是 id）：`R2` 要断言「首帧写出的页脚 == 那一刻
        # `_ld_footer()`」，只记 id 的话这条断言写不出来（R2 审计的「探针只会 print」之痛）。
        contents = {op.element_id: op.content for op in ops}
        calls["batch"].append(([op.element_id for op in ops], sequence, res.ok, contents))
        return res

    # ⚠️ `_frame_writes` 是**独立于账本**的一份观测：生产 `_ld_update_card` 真的被调用过几次
    #   （收尾那一帧走的就是它）。R9 的跨源等式要用它（见下面 `_expected_frames` 的说明）——
    #   注意探针**必须转发**给 `original_update`：底层收口点才是记账的地方，
    #   把 `_ld_update_card` 整个换成不调原实现的东西会让账本永远是 0（探针自己造的假象）。
    _frame_writes: list = []

    async def _spy_update(chat_id, message_id, card):
        calls["patch"] += 1
        _frame_writes.append(message_id)
        return await original_update(chat_id, message_id, card)

    adapter._ld_ck_write = _spy_write
    adapter._ld_ck_batch = _spy_batch
    adapter._ld_update_card = _spy_update
    # ② 给面板**灌真实数据**（推理轮 + 工具步骤）—— 只验「正文元素」不够：
    #    卡片的第二个元素是面板内容，必须证明它真的收到过有内容的 markdown。
    try:
        _panel_mod = None
        for _name in ("hermes_plugins.larkdeck.core.panel", "larkdeck.core.panel"):
            if _name in sys.modules:
                _panel_mod = sys.modules[_name]
                break
        if _panel_mod is None:
            import importlib as _il
            _panel_mod = _il.import_module("hermes_plugins.larkdeck.core.panel")
        _sid, _tid = f"probe-ck-{int(time.time())}", "probe-turn"
        _panel_mod.reset()
        _panel_mod.bind_chat_session(chat, _sid)
        _panel_mod.begin_turn(_sid, _tid)
        _panel_mod.record_reasoning(_sid, _tid, "先想一下这个问题该怎么拆。")
        _panel_mod.record_answer_delta(_sid, _tid)          # 正文开始 ⇒ 切断第 1 轮
        _panel_mod.record_reasoning(_sid, _tid, "再看一眼工具。")
        _panel_mod.record_tool_started(_sid, _tid, "terminal", args={"command": "ls"})
        _panel_mod.record_tool_finished(_sid, _tid, "terminal", duration_ms=42, status="ok")
        print("   已灌面板数据（1 个工具 + 2 轮推理）")
    except Exception as exc:
        print(f"   ⚠️ 面板数据灌不进去（{exc!r}）—— 面板元素会只有占位空格")

    text = ("这是**生产路径**的 CardKit 传输验证：正文会逐字往外冒。") * 3
    try:
        # ⚠️ 故意**不**set 传输：这一条验的就是「用户不写配置时真正跑的那条路」。
        # 报错必须分得清「默认被翻回去了」与「你在 config.yaml / 环境变量里显式改了」——
        # 后者不是缺陷，探针不该拿它当失败（但也不能假装在测 cardkit）。
        resolved = adapter._ld_transport()
        _mod = sys.modules.get(type(adapter).__module__)
        # ⚠️ `_DEFAULTS` 在**模块**上（不是类属性）：写成 `adapter._DEFAULTS` 会 AttributeError，
        # 而且是在真机建卡**之前**炸 —— 探针的「真机门禁」当场变哑巴（这次就是被它绊了一下）。
        declared = dict(getattr(_mod, "_DEFAULTS", {}) or {}).get("native_transport")
        configured = dict(getattr(_mod, "_CONFIG", {}) or {}).get("native_transport")
        env_val = os.environ.get("LARKDECK_NATIVE_TRANSPORT")
        print(f"   生效的传输 = {resolved!r} · 环境变量 {env_val!r} · `_DEFAULTS` 声明的是 "
              f"{declared!r} · `_CONFIG` 里是 {configured!r}"
              f"（注意 `_CONFIG` 也可能来自 plugin.yaml 的 schema 默认，不能当「用户配过」）")
        if resolved != "cardkit":
            print(f"❌ 这条探针要验 cardkit 传输，但当前生效的是 {resolved!r} —— 无法继续"
                  + ("（有环境变量覆盖，是显式的，不是缺陷）" if env_val else
                     "（没人显式覆盖过 ⇒ 默认值或配置把它翻回去了，得查）"))
            adapter._ld_ck_write = original_write
            adapter._ld_ck_batch = original_batch
            adapter._ld_update_card = original_update
            adapter._client.cardkit.v1.card.create = _orig_card_create
            adapter._client.im.v1.message.create = _orig_msg_create
            return 1
        loop = asyncio.new_event_loop()
        # ★ 页脚必须**在建实体之前**灌好真数据（R2 审计指出：探针进程里 `_ld_footer()` 恒为空串
        #   时只能证明「写成功」，证明不了「写的是当前内容」）。灌在 seed 之前，首帧那次 batch
        #   里页脚就是**真内容**；而之后它不再变 ⇒ 面板变化那一帧的 batch 里**只该有 panel_body**
        #   （这正好是同一条去重规则的另一面）。
        #   ⚠️ `configure()` 是**模块级**函数（不是实例方法）：写成 `adapter.configure(...)` 会
        #   AttributeError，而且是在真机建卡**之后**炸 ⇒ DM 里留一张冻结卡、探针白跑（被绊过）。
        _mod.configure(footer=True)
        _ctx_mod = _probe_context_module()
        _ctx_mod.reset()
        _ctx_mod.record_api_call(model="probe-model",
                                 usage={"input_tokens": 4321, "output_tokens": 10})
        _ctx_mod.set_context_override(20000)
        footer_at_start = adapter._ld_footer() or ""
        print(f"   探针页脚（建实体前灌进去的） = {footer_at_start!r}")
        try:
            ok_seed = loop.run_until_complete(adapter.send_stream_frame(
                "", chat_id=chat, turn_id=tid))
            print(f"   seed 帧（建实体） = {ok_seed}")
            state = None
            for item in list(adapter._ld_streams.values()):
                state = item
            card_id = (state or {}).get("card_id") if isinstance(state, dict) else None
            print(f"   实体 card_id = {card_id}（必须有值，否则说明走的是 patch 传输）")
            # ⚠️ **必须记账**（2026-09-14 实测踩到）：这一支以前不记 `message_id`，
            # 于是「认不出来的探针卡」只能靠人长按删 —— 我自己就有一次重跑留下的卡
            # 认不出来（`--clean-only` 按账本删，账本里没有它就删不掉）。
            # 判据：能从回合状态里拿到 message_id 就立刻记进账本。
            _mid_now = (state or {}).get("message_id") if isinstance(state, dict) else None
            if _mid_now:
                _save_sent_ids(_load_sent_ids() + [str(_mid_now)])
                print(f"   已记账 message_id = {_mid_now}（`--clean-only` 能按它删掉这张）")
            for cut in (20, 40, len(text)):
                ok = loop.run_until_complete(adapter.send_stream_frame(
                    text[:cut], chat_id=chat, turn_id=tid))     # ← 同一个 tid
                print(f"   正文帧 {cut} 字 = {ok}")
                time.sleep(0.4)
            # ★ R3 收窄版：**seed 之后再起一个工具**，让「工具块在流式期间被单独重写」这条
            #   真机路径真的被走到。审计指出：把工具事件全灌在 seed 之前的话，工具块只在
            #   **建实体**那一刻被写一次，运行期的更新车道**一次都走不到** —— 那样这次改动
            #   在真机上等于没有验证。
            _before_batches = len(calls["batch"])
            _panel_mod.record_tool_started(_sid, _tid, "bash", args={"command": "pwd"})
            # ⚠️ 正文必须**比上一帧长**：帧文本的契约是「累积全文」，发一个更短的前缀虽然
            #    直连适配器不会真的伤到用户，但作为「生产路径」证据不该出现非法帧形状
            #    （R3 代码审计低-5 抓到）。
            ok_tool = loop.run_until_complete(adapter.send_stream_frame(
                text + "。", chat_id=chat, turn_id=tid))
            _new_ids = [b[0] for b in calls["batch"][_before_batches:]]
            print(f"   工具开始那一帧 = {ok_tool} · 该帧写出的装饰元素 = {_new_ids}"
                  f"（**预期只含 panel_tools** —— 推理块没变就不许重发）")
            time.sleep(0.4)

            # ★ R2 的「**未变化不重写**」真机验证：把面板内容改掉再发一帧，那一帧**只该写面板**。
            #   为什么必须在真机上验这一条：去重的判据在本地（`ck_decor`），所以它不会因为
            #   飞书拒绝而变红 —— 真机要证明的是「只带一个 action 的 batch 照样 `code=0`」
            #   （多元素 batch 已经验过，单元素那条路是新的）。
            panel_before = adapter._ld_panel_markdown(chat)
            _panel_mod.record_reasoning(_sid, _tid, "再补一段推理，让面板内容变一次。") \
                if _panel_mod is not None else None
            panel_after = adapter._ld_panel_markdown(chat)
            print(f"   面板内容变了 = {panel_after != panel_before}"
                  f"（{len(panel_before)} → {len(panel_after)} 字符）")
            ok_tail = loop.run_until_complete(adapter.send_stream_frame(
                text + "。", chat_id=chat, turn_id=tid))
            print(f"   面板变化帧 = {ok_tail}")
            time.sleep(0.4)
            ok_fin = loop.run_until_complete(adapter.send_stream_frame(
                text, finalize=True, chat_id=chat, turn_id=tid))
            print(f"   收尾帧 = {ok_fin}")
        finally:
            loop.close()
    finally:
        adapter._ld_ck_write = original_write
        adapter._ld_ck_batch = original_batch
        adapter._ld_update_card = original_update
        adapter._client.cardkit.v1.card.create = _orig_card_create
        adapter._client.im.v1.message.create = _orig_msg_create

    writes = [c for c in calls["content"]]
    batches = [b for b in calls["batch"]]
    print(f"   正文元素写入 {len(writes)} 次：{[(w[0], w[1], w[2]) for w in writes]}")
    print(f"   装饰 batch {len(batches)} 次：{[(b[0], b[1], b[2]) for b in batches]}")
    panel_hits = [b for b in batches if "panel_body" in b[0] and b[2]]
    print(f"   面板内容成功写入 {len(panel_hits)} 次（必须 ≥1，否则面板内容根本没上卡）")
    print(f"   收尾 patch {calls['patch']} 次")
    # ⚠️ R2 起的写入形态：**每帧最多 1 次装饰 batch + 1 次正文 content**（卡级写入上限按官方
    #    口径 10 次/秒 × 0.25s 帧窗口 = 2），而装饰**内容没变就不发那个 batch** ⇒ 这里应当
    #    R3 收窄版起是**三次** batch：第一帧（三块都第一次有内容）、「工具开始」那一帧
    #    （**只带 `panel_tools`**）、面板变化那一帧（只带 `panel_body`）—— 后两条正是
    #    「拆两块」的真机判据。
    #    「旧断言（每帧各 2 次）不改就会在正确的实现上红」这件事上一轮已经发生过一次。
    print(f"   card.create {calls['create']} 次（必须 ==1）· message.create {calls['send']} 次"
          f"（必须 ==1）· 正文写入 {len(writes)} 次（必须 == 正文帧数 5）"
          f"· 装饰 batch {len(batches)} 次（必须 ==3：首帧三块 + 工具开始帧 + 面板变化帧）")
    batch_shapes = [tuple(b[0]) for b in batches]
    # ★ **序号账本**（R2 审计第 2 条：旧版把「单调递增」写在 docstring 里却一条断言都没有，
    #   于是提交信息里那些序号值**无法被复核**）。现在逐条真断言，并把账本打出来进提交信息：
    #   装饰与正文**共用**一个计数器 ⇒ 合并后必须严格递增、不撞号、且**连续**（连续是我们
    #   自己 +1 的实现性质；服务端只实测过「撞号/回退 ⇒ 300317」，前向空洞没实测过）。
    ledger = sorted([b[1] for b in batches] + [w[1] for w in writes])
    print(f"   序号账本（装饰 + 正文共用）= {ledger}")
    ledger_strictly_increasing = all(a < b for a, b in zip(ledger, ledger[1:]))
    # ★ 页脚那一路**写的是当前内容**吗（R2 审计指出：探针进程页脚恒为空串时只能证明「写成功」）
    # ★ R9 自检账本：这一回合**必须**在账本上留下痕迹，否则 `/larkdeck status` 在真机上
    #   永远说「无记录」—— 而那张卡存在的唯一理由就是回答「插件在不在动」。
    #
    #   ⚠️ **旧写法是自证的**（R9 审计中-5）：`_expected_writes = len(writes) + 2`，
    #   而 `writes` 是本探针在 `_ld_ck_write` 上打的桩、`+2` 是照着实现对出来的 ⇒
    #   等号两边来自**同一个证据源**，它只能发现「某帧漏记」，发现不了「一次逻辑写其实
    #   一个字节都没到飞书」。现在改成**跨源的等价式**（每一侧都是独立观测）：
    #     ① `len(writes)` —— `_ld_ck_write` 上桩到的**元素写**次数（= 4，场景是脚本写死的）；
    #     ② `calls["create"]` —— 真 SDK 的 `cardkit.v1.card.create` 调用账本（= 1，建实体）；
    #     ③ `_frame_writes` —— 生产 `_ld_update_card` 被真的调用过几次（= 1，收尾整卡替换）。
    #   ①+②+③ 就是「这个回合用户看到的每个帧各自是否写过东西」的**分帧证据**，
    #   账本 `frame_ok_count` 必须等于它。等式两边来源不同，才有判别力。
    #   ⚠️ 口径（中-5）：账本数的是**帧**，不是网络调用 —— seed 建实体是
    #   `card.create` + 发实体卡**两次**网络调用，但只算**一帧**（所以下面用
    #   `int(calls["create"] > 0)` 而不是 `calls["create"]` 参与求和，把这个口径写死在算式里）。
    _snap: dict = {}
    try:
        _snap = dict(_probe_context_module().status_snapshot() or {})
    except Exception as exc:      # noqa: BLE001
        print(f"   ⚠️ 读不到 R9 自检账本：{exc!r}")
    _expected_frames = len(writes) + int(calls["create"] > 0) + len(_frame_writes)
    _ledger_ok = (int(_snap.get("frame_ok_count") or 0) == _expected_frames
                  # 场景是脚本写死的**五帧正文**（20/40/102 字的累积帧、R3 的「工具开始」那一帧、
                  # 面板变化帧）+ 一次建实体 + 一次收尾 ⇒ 帧数必须是 7。
                  # 这一条**不依赖**任何桩：它把「探针脚本到底跑了几帧」写成了字面量。
                  and _expected_frames == 7
                  and int(_snap.get("frame_fail_count") or 0) == 0)
    print(f"   R9 自检账本：写卡 {_snap.get('frame_ok_count')} 帧（期望 {_expected_frames} = "
          f"元素写 {len(writes)} + 建实体 {int(calls['create'] > 0)} + 整卡 patch {len(_frame_writes)}；"
          f"脚本场景固定 7）· 失败 {_snap.get('frame_fail_count')} 次 "
          f"{_snap.get('frame_fail_reason')!r}")
    footer_written = [b[3].get("footer") for b in batches]
    footer_is_current = bool(footer_at_start) and footer_written[0] == footer_at_start
    print(f"   首帧写出的页脚 = {footer_written[0]!r} · 那一刻 `_ld_footer()` = {footer_at_start!r}"
          f"（必须相等；为空串说明探针没把页脚灌进去 ⇒ 这一条没验，按失败处理）")
    ok = (bool(state) and card_id and writes and calls["patch"] == 1
          and all(w[2] for w in writes) and bool(panel_hits)
          and calls["create"] == 1 and calls["send"] == 1
          # R3 收窄版：场景现在有**五**个正文帧（多出「工具开始」那一帧），
          # 装饰 batch 是**三**次，且形状必须**逐字**是「首帧三块 → 只有工具块 → 只有推理块」
          # —— 最后两条正是「拆两块」的真机判据（写成字面量，不做任何推导）。
          and len(writes) == 5
          and batch_shapes == [("panel_body", "panel_tools", "footer"), ("panel_tools",),
                               ("panel_body",)]
          and all(b[2] for b in batches)
          and ledger_strictly_increasing
          and ledger == list(range(1, len(ledger) + 1))
          and footer_is_current
          and _ledger_ok)
    if ok:
        print("✅ 生产路径的 CardKit 传输真机通过（建实体 + 元素写入 + patch 收尾）")
        print("   ⚠️ 这些卡的 id 没进账本（收尾后 stream state 已清）—— 看够了就叫我删。")
    else:
        print("❌ 生产路径的 CardKit 传输有问题（见上面的 code / 计数）")
    return 0 if ok else 1


def probe_degrade_lane(client, chat: str, cards) -> int:
    """R5 真机端到端：元素通道拿到**卡级死法**时，生产路径必须**降级续写同一张卡**。

    做法（不碰生产状态机之外的任何东西）：用生产路径起一个 cardkit 回合 → **手工把流式会话
    关掉**（`card.settings(streaming_mode=false)`，等价于「元素通道死了」）→ 再发一帧：
    这一帧的元素写入必然拿到 `300309` ⇒ 必须走 `DEGRADE`（整卡 patch 打在**同一张卡**上）。
    断言：这一帧返回 **True**（不是 fail-open）、patch 计数 +1、`card.create` 仍是 1
    （**没有第二张卡**）、状态里记着 `ck_degrade` 且 `card_id` 被清空；再发一帧确认后续仍走 patch。
    卡按 message_id 精确删（**不按时间窗**）。
    """
    import asyncio
    from lark_oapi.api.im.v1 import DeleteMessageRequest
    from lark_oapi.api.cardkit.v1 import (SettingsCardRequest, SettingsCardRequestBody)
    _load, adapter, _panel = _load_adapter_for_probe(chat)
    if adapter is None:
        return 1
    calls = {"create": 0, "send": 0, "patch": 0, "content": 0, "batch": 0}
    original_update = adapter._ld_update_card
    original_write = adapter._ld_ck_write
    original_batch = adapter._ld_ck_batch
    _orig_card_create = adapter._client.cardkit.v1.card.create
    _orig_msg_create = adapter._client.im.v1.message.create

    def _count_card_create(request):
        calls["create"] += 1
        return _orig_card_create(request)

    def _count_msg_create(request):
        calls["send"] += 1
        return _orig_msg_create(request)

    async def _spy_write(*a, **k):
        calls["content"] += 1
        return await original_write(*a, **k)

    async def _spy_batch(*a, **k):
        calls["batch"] += 1
        return await original_batch(*a, **k)

    async def _spy_update(chat_id, message_id, card, **k):
        calls["patch"] += 1
        calls.setdefault("patch_mids", []).append(message_id)
        return await original_update(chat_id, message_id, card, **k)

    adapter._client.cardkit.v1.card.create = _count_card_create
    adapter._client.im.v1.message.create = _count_msg_create
    adapter._ld_ck_write = _spy_write
    adapter._ld_ck_batch = _spy_batch
    adapter._ld_update_card = _spy_update
    tid = f"ckd-{int(time.time())}"
    message_id = ""
    try:
        loop = asyncio.new_event_loop()
        try:
            assert loop.run_until_complete(adapter.send_stream_frame(
                "", chat_id=chat, turn_id=tid)), "seed 帧必须成功（否则这条探针没意义）"
            state = None
            for item in list(adapter._ld_streams.values()):
                state = item
            card_id = (state or {}).get("card_id") if isinstance(state, dict) else None
            message_id = (state or {}).get("message_id") if isinstance(state, dict) else ""
            print(f"   实体 card_id = {card_id} · message_id = {message_id}")
            ok1 = loop.run_until_complete(adapter.send_stream_frame(
                "降级前的一帧", chat_id=chat, turn_id=tid))
            print(f"   降级前那一帧 = {ok1}（元素写入 {calls['content']} 次 / batch {calls['batch']} 次）")
            # 手工关掉流式会话 —— 与「元素通道拿到 300309」等价，且**不需要真出故障**。
            # ⚠️ 序号必须取**当前**状态：第一帧跑完之后 `ck_seq` 已经不是 seed 那一刻的值了，
            #    拿旧值会得 `300317`（序号冲突）—— 探针第一版就是这么失败的，而它看起来
            #    像是「关会话失败」，其实是探针自己拿错了号。
            now_state = adapter._ld_stream_get(f"{chat}:{tid}")
            seq_now = int((now_state or {}).get("ck_seq") or 0)
            closed = adapter._client.cardkit.v1.card.settings(
                SettingsCardRequest.builder().card_id(card_id)
                .request_body(SettingsCardRequestBody.builder()
                              .settings(json.dumps({"config": {"streaming_mode": False}}))
                              .sequence(seq_now + 1)
                              .uuid(f"ld-probe-close-{card_id}").build()).build())
            print(f"   手工关流式会话 code={getattr(closed, 'code', None)}"
                  f"（用序号 {seq_now + 1}）")
            if int(getattr(closed, "code", -1) or 0) != 0:
                print("   ❌ 关会话失败 ⇒ 这一帧不会拿到 300309，探针无法继续（不当成通过）")
            time.sleep(0.4)
            ok2 = loop.run_until_complete(adapter.send_stream_frame(
                "这一帧必须降级", chat_id=chat, turn_id=tid))
            state2 = adapter._ld_stream_get(f"{chat}:{tid}")
            degrade = (state2 or {}).get("ck_degrade") if isinstance(state2, dict) else None
            print(f"   降级那一帧 = {ok2} · ck_degrade={degrade} · "
                  f"card_id={((state2 or {}).get('card_id') if isinstance(state2, dict) else None)!r}"
                  f" · patch {calls['patch']} 次 · card.create {calls['create']} 次")
            # 降级那一帧**会**尝试一次元素写入（正是它拿到 300309 才发现通道死了）——
            # 所以基线要取在**这一帧之后**：之后不该再有任何元素写入。
            content_after_degrade = calls["content"]
            ok3 = loop.run_until_complete(adapter.send_stream_frame(
                "降级后的第二帧", chat_id=chat, turn_id=tid))
            print(f"   降级后的下一帧 = {ok3} · patch {calls['patch']} 次 · "
                  f"元素写入 {calls['content']} 次（降级后应当**不再增长**）")
            ok_fin = loop.run_until_complete(adapter.send_stream_frame(
                "收尾", finalize=True, chat_id=chat, turn_id=tid))
            print(f"   收尾帧 = {ok_fin}")
        finally:
            loop.close()
        # ⚠️ 必须断言 patch 的目标就是原来那条 message_id（R5 审计低-9/高-2：只看次数的话
        #    「打到别的卡上」也会绿）
        patch_mids = calls.get("patch_mids") or []
        ok = (ok1 and ok2 and ok3 and degrade == 300309
              and calls["create"] == 1 and calls["send"] == 1
              and calls["patch"] >= 3 and calls["content"] == content_after_degrade
              and bool(message_id) and all(mid == message_id for mid in patch_mids))
        print(f"   降级后的 patch 目标：{patch_mids}（必须全是这一张：{message_id}）")
        print("✅ 降级车道真机通过：卡级死法 ⇒ 整卡 patch 续写同一张卡，没有第二张卡"
              if ok else "❌ 降级车道有问题（见上面的计数与状态）")
        return 0 if ok else 1
    finally:
        adapter._ld_ck_write = original_write
        adapter._ld_ck_batch = original_batch
        adapter._ld_update_card = original_update
        adapter._client.cardkit.v1.card.create = _orig_card_create
        adapter._client.im.v1.message.create = _orig_msg_create
        if message_id:
            d = _client_delete(adapter, message_id)
            print(f"   清理这张探针卡（按 message_id）→ code={d}")


def _client_delete(adapter, message_id: str) -> int:
    """按 message_id 删一条自己发的消息（**绝不按时间窗**）。"""
    try:
        from lark_oapi.api.im.v1 import DeleteMessageRequest
        resp = adapter._client.im.v1.message.delete(
            DeleteMessageRequest.builder().message_id(message_id).build())
        return int(getattr(resp, "code", -1))
    except Exception as exc:      # noqa: BLE001
        print(f"   ⚠️ 删卡失败（{exc!r}）")
        return -1


def _probe_context_module():
    """拿到**加载器那份** `context` 模块（与适配器用的是同一个对象）。

    ⚠️ 必须走 `sys.modules` 里那个名字：直接 `import larkdeck.core.context` 会拿到**第二个**
    模块对象，往里写指标时适配器读的还是原来那份（`check_hooks.py` 专门盯这个坑）。
    """
    for name in ("hermes_plugins.larkdeck.core.context", "larkdeck.core.context"):
        mod = sys.modules.get(name)
        if mod is not None:
            return mod
    import importlib
    return importlib.import_module("hermes_plugins.larkdeck.core.context")


def _load_adapter_for_probe(chat: str):
    """给探针造一个**真适配器**（经 Hermes 插件加载器）并注入真客户端；顺带兜住 SDK 懒绑定。"""
    import os as _os
    import pathlib as _pl
    home = _os.environ.get("HERMES_HOME") or str(_pl.Path.home() / ".hermes")
    install = _pl.Path(home) / "hermes-agent"
    _os.environ.setdefault("HERMES_HOME", home)
    if str(install) not in sys.path:
        sys.path.insert(0, str(install))
    try:
        from hermes_cli.plugins import discover_plugins
        from gateway.platform_registry import platform_registry
        from gateway.config import PlatformConfig
    except Exception as exc:
        print(f"⚠️ 需要 Hermes 环境（用 ~/.hermes/hermes-agent/venv/bin/python3 跑）：{exc!r}")
        return None, None, None
    discover_plugins()
    factory = getattr(platform_registry.get("feishu"), "adapter_factory", None)
    if factory is None:
        print("⚠️ 注册表里没有 feishu 平台")
        return None, None, None
    adapter = factory(PlatformConfig(enabled=True, extra={}))
    env = load_env(); cards = load_cards()
    lark = __import__("lark_oapi", fromlist=["Client"])
    client = (lark.Client.builder().app_id(env["FEISHU_APP_ID"])
              .app_secret(env["FEISHU_APP_SECRET"])
              .log_level(lark.LogLevel.ERROR).build())
    adapter._client = client
    base_mod = sys.modules.get("hermes_plugins.feishu_platform.adapter")
    loader = getattr(base_mod, "_load_lark_oapi", None)
    if loader:
        loader()
    return None, adapter, None


def _set_probe_config(adapter, **kw) -> None:
    """临时改探针进程里的配置（只影响这个进程，不动真 config.yaml）。"""
    mod = sys.modules.get(type(adapter).__module__)
    if mod is not None:
        mod._CONFIG.update(kw)


def probe_stop_redraw(client, chat: str, cards) -> int:
    """**中止重绘的真机端到端**（第七路审计那条阻断项的现场复现与回归）。

    要回答的问题：正文大到「**飞书收得下**、但超过我们自己的降载预算（`CARD_BYTE_BUDGET`
    = 40000）」时，本回合若掉过 native，`/stop` 之后那张卡**必须真的变成中止色**。

    为什么必须在真机上跑：这条路径的失效形态是**完全静默**的 —— 一次 patch 都不发、
    卡片不变色、零日志。单测只能证明我们的分支逻辑，证明不了「patch 真的被飞书接受、
    真的把颜色画上去了」。而第七路审计的阻断项恰恰长在这个缝里：阈值被同文件随后一句
    旧赋值覆盖成 40000 ⇒ 60000 字节的正文被丢掉 ⇒ 这里会看到 **patch 数 = 0**。

    它用的是**真适配器**（经 Hermes 插件加载器拿到的那个类），所以走的是生产路径：
    ``send()`` → 卡片纳入追踪 → ``interrupt_session_activity()`` → ``_ld_redraw_stopped()``
    → ``message.patch``。跑完的卡会记进探针账本，下次默认跑探针时一起清掉。
    """
    import asyncio

    try:
        from hermes_cli.plugins import discover_plugins
        from gateway.platform_registry import platform_registry
        from gateway.config import PlatformConfig
    except Exception as exc:                       # pragma: no cover - 环境问题
        print(f"⚠️ 这个探针需要 Hermes 环境（用 ~/.hermes/hermes-agent/venv/bin/python3 跑）：{exc!r}")
        return 1

    install = pathlib.Path(os.environ.get("HERMES_INSTALL_DIR")
                           or (pathlib.Path.home() / ".hermes" / "hermes-agent"))
    if str(install) not in sys.path:
        sys.path.insert(0, str(install))
    os.environ.setdefault("HERMES_HOME", str(pathlib.Path.home() / ".hermes"))
    discover_plugins()
    entry = platform_registry.get("feishu")
    factory = getattr(entry, "adapter_factory", None) if entry else None
    if factory is None:
        print("⚠️ 注册表里没有 feishu 平台，跑不了这个探针")
        return 1
    adapter = factory(PlatformConfig(enabled=True, extra={}))
    print(f"适配器类 = {type(adapter).__module__}.{type(adapter).__qualname__}")
    if "LarkDeckMixin" not in [c.__name__ for c in type(adapter).__mro__]:
        print("⚠️ 这个适配器不是 larkdeck 的子类 —— 探针会在官方实现上跑，结论无意义")
        return 1
    if not isinstance(getattr(adapter, "_ld_state", None), dict):
        print("⚠️ 适配器没有 `_ld_state`（`_ld_setup()` 没跑），无法验证追踪")
        return 1
    # 适配器的 `_client` 正常由内置的 `_prepare_client()` 在 connect 时建好。这里是一个探针
    # 进程、**故意不 connect**（网关正连着同一个应用，抢连接没意义），所以把上面**已经建好的
    # 同一个** lark 客户端挂进去 —— `_client` 是 `compat.CALLBACK_INSTANCE_ATTRS` 里登记过的
    # 实例契约名，插件的发送/更新都只经它。
    adapter._client = client

    # ⚠️ 官方适配器把 SDK 的请求构造类（`CreateMessageRequest` …）**懒绑定**在 connect 时机
    # （`_load_lark_oapi()`，见官方模块顶部注释）。不 connect 的话那些名字还是 None，
    # 请求构造器会退化成 SimpleNamespace，SDK 直接报 `token_types` 属性错误 ——
    # 也就是说这里必须显式触发一次绑定，否则本探针证不了任何东西。
    # 这是**探针脚本**对 Hermes 私有名的唯一一处使用，不进插件本体（不变量 3 仍然只约束 core/）。
    base_module = sys.modules.get(type(adapter).__mro__[2].__module__)
    loader = getattr(base_module, "_load_lark_oapi", None)
    if loader is None or not loader():
        print("⚠️ 绑不上 lark_oapi 的请求构造器（Hermes 版本变了或 SDK 缺失）—— 探针结论不可信")
        return 1

    # 正文：20k 汉字 = 60000 字节。**两个条件都要满足**：飞书收得下（实测硬上限 128000）、
    # 但超过我们自己的降载预算（40000）—— 后者正是「卡在、却存不下正文」的那个区间。
    body = "汉" * 20000
    body_bytes = len(body.encode("utf-8"))
    node, tier = cards.fit_reply_card(body)
    print(f"正文 {body_bytes} 字节（降载档位={tier}，卡片 {cards.card_bytes(node)} 字节）")
    if tier == "ok":
        print("⚠️ 这版 `fit_reply_card` 没有降载 —— 探针的前提（超预算但发得出去）不成立")

    # 统计真实 patch（只包一层计数 + 抄下请求体，不改行为）。
    # **必须看请求体**：2.0 卡在飞书里存成 cardkit 实体，用 API 读回来只剩一句
    # 「请升级至最新版本客户端，以查看内容」，看不到边框颜色（`clean_previous` 的注释
    # 早就记过这件事）。所以「颜色有没有画上去」只能由**我们发出去的载荷 + code=0**
    # 共同证明 —— 剩下那一点点「客户端真的画成黄色」只能靠眼睛。
    patches: list = []

    def _payload(request):
        body = getattr(request, "request_body", None) or getattr(request, "body", None)
        return (getattr(body, "content", None) or "") if body is not None else ""

    original_patch = adapter._client.im.v1.message.patch

    def _counting_patch(request):
        result = original_patch(request)
        patches.append((getattr(result, "code", None), _payload(request)))
        return result

    adapter._client.im.v1.message.patch = _counting_patch

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(adapter.send(chat, body))
        mid = getattr(result, "message_id", None)
        print(f"{'✅' if mid else '❌'} 建卡 message_id={mid}")
        if not mid:
            print(f"   发送结果：{result!r}（卡片没发出去，无法验证中止重绘）")
            return 1
        _save_sent_ids(_load_sent_ids() + [mid])

        ld_module = sys.modules.get(type(adapter).__module__)
        limit = getattr(ld_module, "_MAX_TRACKED_TEXT", None)
        measure = getattr(ld_module, "_card_body_bytes", None)
        kept = (adapter._ld_state.get(mid) or {}).get("last_text") or ""
        # ⚠️ 两个口径都要打（第十路审计 §7-2）：`limit` 是**近似阈值**，判据其实是
        # 「那张带色的卡发不发得出去」；混着说会让读日志的人误判。
        print(f"   追踪到的正文 = {len(kept.encode('utf-8'))} 原始字节 / "
              f"{measure(kept) if measure else '?'} 判据字节"
              f"（近似阈值 {limit}；真判据是「带状态小面板的卡发不发得出去」）")
        if len(kept.encode("utf-8")) != body_bytes:
            print("❌ 正文没有被保留 —— 这就是阻断项的症状（/stop 一次 patch 都不会发）")
            return 1

        patches.clear()
        loop.run_until_complete(
            adapter.interrupt_session_activity(f"probe-stop-{int(time.time())}", chat))
        print(f"   /stop 之后的 patch 次数 = {len(patches)}")
        for code, payload in patches:
            print(f"     code={code}  载荷 {len(payload.encode('utf-8'))} 字节  含 yellow = "
                  f"{'yellow' in payload}")
    finally:
        adapter._client.im.v1.message.patch = original_patch
        loop.close()

    # ---- 第二条路径：**真 native 流式**（首帧建卡 → 若干帧 → /stop）----------------- #
    # 第八路审计对这条只剩**推断**（两条重绘路径共用 `_ld_build_card`，所以颜色同样会被
    # 降载吃掉）。推断不算证据，这里直接驱动官方的 native 契约跑一遍：
    #   `send_stream_frame("")` 建卡 → 带正文的帧 → `interrupt_session_activity` → 看载荷。
    print()
    print("—— 第二条路径：native 流式 + /stop ——")
    stream_key = f"probe-stream-{int(time.time())}"
    painted_stream: list = []          # 提前定义：中途 return/抛异常时下面的判定不该炸
    # 记账钩子：seed 帧建卡时适配器会调 `_ld_track(message_id, chat_id)`。
    # 第九路审计指出一个账本缺口 —— 建卡成功、但 `_ld_stream_get` 拿不到 message_id 时
    # 我们会直接 return，那张**已经发到用户 DM 里**的卡就没人记账（下次清理带不走它）。
    # 所以从 `_ld_track` 这里顺手抄一份，保证「发出去的卡一定进账本」。
    tracked_ids: list = []
    original_track = adapter._ld_track

    def _spy_track(message_id, chat_id=None):
        if message_id:
            tracked_ids.append(str(message_id))
        if chat_id is None:
            return original_track(message_id)
        return original_track(message_id, chat_id)

    adapter._ld_track = _spy_track       # type: ignore[method-assign]
    loop = asyncio.new_event_loop()
    try:
        adapter._client.im.v1.message.patch = _counting_patch
        seed_ok = loop.run_until_complete(adapter.send_stream_frame(
            "", chat_id=chat, turn_id=stream_key))
        print(f"   seed 帧建卡 = {seed_ok}")
        if not seed_ok:
            print("❌ native seed 帧没建起卡（effect 1a 的前提不成立）")
            return 1
        state = adapter._ld_stream_get(f"{chat}:{stream_key}")
        stream_mid = str((state or {}).get("message_id") or "")
        print(f"   流式卡 message_id = {stream_mid}")
        # ⚠️ 必须**直接判传输**，不能靠推断：默认传输翻了之后，这条路径会在 cardkit 上跑
        # （实体卡 + 元素写入），而「有没有出现帧失败日志」证明不了它走的是哪条
        # （patch 路径失败了一样有日志）。判据是结构性的：只有 cardkit 的回合状态里才有
        # `card_id`（建实体拿到的那个 id），patch 路径没有这个键。
        _used = "cardkit" if (state or {}).get("card_id") else "patch"
        print(f"   这条路径实际用的传输 = {_used}"
              f"（状态里有 card_id「{(state or {}).get('card_id')}」⇒ 实体卡）")
        if _used == "cardkit":
            print("   ⇒ 下面「/stop 载荷带黄边」这条结论覆盖的是 **CardKit 实体卡**")
        # 先把**所有认出来的 id** 记账（`_ld_track` 抄到的那份是兜底），再判失败
        if tracked_ids:
            _save_sent_ids(_load_sent_ids() + sorted(set(tracked_ids)))
            print(f"   已记账 {len(set(tracked_ids))} 个 id（来自 `_ld_track`）")
        if not stream_mid:
            print("❌ native 建卡后没有记录 message_id，后续帧无处可去"
                  f"（已把认出的 id {sorted(set(tracked_ids))} 记进账本，免得 DM 里堆卡）")
            return 1
        _save_sent_ids(_load_sent_ids() + [stream_mid])
        # 正文同样取 60000 字节（超我们自己的降载预算、但飞书收得下）
        for shrink in (3, 2, 1):
            ok = loop.run_until_complete(adapter.send_stream_frame(
                "汉" * (len(body) // shrink), chat_id=chat, turn_id=stream_key))
            print(f"   帧（{len(body) // shrink * 3} 字节）→ {ok}")
        patches.clear()
        loop.run_until_complete(
            adapter.interrupt_session_activity(stream_key, chat))
        painted_stream = [(code, payload) for code, payload in patches
                          if code == 0 and "yellow" in payload]
        print(f"   /stop 之后的 patch 次数 = {len(patches)}")
        for code, payload in patches:
            print(f"     code={code}  载荷 {len(payload.encode('utf-8'))} 字节  含 yellow = "
                  f"{'yellow' in payload}")
    finally:
        adapter._client.im.v1.message.patch = original_patch
        adapter._ld_track = original_track       # type: ignore[method-assign]
        loop.close()
    if painted_stream:
        print("✅ native 路径同样保住了中止色（不再只是推断）")
    else:
        print("❌ native 路径的 /stop 载荷里没有黄边")
        return 1

    from lark_oapi.api.im.v1 import GetMessageRequest
    got = client.im.v1.message.get(GetMessageRequest.builder().message_id(mid).build())
    content = ""
    try:
        content = (got.data.items[0].body.content or "")
    except Exception:                              # pragma: no cover - 读回失败
        pass
    print(f"   读回消息 {len(content)} 字节（2.0 卡是 cardkit 实体，读回来通常是占位文案，"
          f"所以它证明不了颜色）")
    print()
    # ⚠️ 断言必须同时看**两件事**，否则会漏掉一半的缺陷（这正是本探针存在的理由）：
    #   ① 正文有没有留住（第七路阻断项：阈值被覆盖 ⇒ 正文被丢 ⇒ 一次 patch 都不发）；
    #   ② **载荷里有没有颜色**（同日实测的第二半：正文留住之后，降载阶梯仍然把承载
    #      状态色的面板整块摘掉 ⇒ patch 发了、code=0、但载荷里没有任何颜色 ⇒ 用户还是
    #      看不到变色。只查 patch 次数的话这一半永远抓不到）。
    painted = [(code, payload) for code, payload in patches
               if code == 0 and "yellow" in payload]
    kept_ok = len(kept.encode("utf-8")) == body_bytes
    if kept_ok and painted:
        print(f"✅ 中止重绘真机通过：{body_bytes} 字节正文保住了，"
              f"/stop 把带黄边的载荷发了出去且飞书 code=0")
        print("   （客户端是否把这 300 多字节的小面板画成黄框，仍要眼睛确认一次）")
        return 0
    if not kept_ok:
        print("❌ 正文没被保留（第七路阻断项的症状：/stop 一次 patch 都不会发）")
    elif not patches:
        print("❌ /stop 之后一次 patch 都没发（中止重绘整条失效）")
    elif not painted:
        print("❌ patch 发出去了但**载荷里没有状态色** —— 降载阶梯把承载颜色的面板"
              "整块摘掉了（`status_shell` 就是修这个的）")
    print("（上面这些是**第一条（非 native）路径**的判定；它是本次运行的独立结论，"
          "不再靠第二条路径的结论兜底）")
    return 1


def _iter_elements(node):
    """把卡 JSON 里**所有**元素节点逐个吐出来（递归进 `column_set` / `column` 等容器）。

    ⚠️ 为什么要递归（2026-09-16 对抗审计实测）：只扫顶层 `body.elements` 会**两头都错** ——
    ① **假阴性**：1.0 的 `action` 行嵌在容器里照样会被飞书拒收（`230099`），而自检报 ✅；
    ② **假阳性**：把按钮放进 `column_set` 是**合法的 2.0 写法**，却被「应当正好一个 button」
       判成 0 个而拒发一张好卡。两头的判据都必须是「遍历整棵树」。
    """
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter_elements(v)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_elements(item)


def probe_card_problems(card: dict, cards) -> list:
    """**发出去之前**把这张卡能本地查的问题查掉 —— 返回问题列表（空 = 可以发）。

    为什么这个自检**必须长在探针自己身上**（2026-09-16 加）：这一格是「请用户点一次」，
    而**用户那一击只能点一次** —— 如果卡本身有结构问题（方言混用 ⇒ 飞书拒收 `230099`、
    元素/字节超限），那一击就白费了，而且用户只会在 DM 里看到「什么都没有」。
    能本地判的先本地判，别把可预防的失败留给用户的手。

    ⚠️ 它**查不出**「点击能不能到服务端」—— 那正是需要真人点一次的原因，不是这里能替代的。

    ⚠️⚠️ **判据必须与适配器同口径**（2026-09-16 对抗审计抓出的**真缺陷**）：回调 value 那一条
    一度写成 `PROBE_VALUE_KEY in value`（**键存在**），而适配器判的是 `value.get(...)` 的
    **真值性** ⇒ `{"larkdeck_probe": False}` 被自检放行、却被适配器静默交回内置实现
    ⇒ **一行日志都不会有**，用户一次**成功**的点击会被读成「飞书没投递」。现在两侧都调
    `cards.is_probe_value()`（一处真相）。
    同一批审计还抓出：只扫顶层（漏嵌套 `action` 行 / 误拒合法容器里的按钮）、
    `config` 从不检查、`button.text` 不查、`behaviors[0]` 不是 dict 会崩、
    字节边界 `>=` 与真机的 `128000 被接受` 矛盾。逐条都补上了。
    """
    problems = []
    if card.get("schema") != "2.0":
        problems.append(f"schema 必须是 2.0（实际 {card.get('schema')!r}）")

    # `config.summary`：2.0 卡的**会话列表预览**文案，形态必须是 {"content": "…"}。
    # 同类字段在 `card.settings` 那条 API 上真机实测回过 300122（`test_units.py` 有那条断言），
    # 这里按同一形状先查一遍 —— 形态错的 summary 会让整张卡在 IM create 上被拒。
    summary = (card.get("config") or {}).get("summary")
    if not isinstance(summary, dict) or not str(summary.get("content") or "").strip():
        problems.append(f"config.summary 必须是 {{\"content\": 非空字符串}}（实际 {summary!r}）")

    nodes = list(_iter_elements(card.get("body") or {}))
    elems = ((card.get("body") or {}).get("elements")) or []
    btns = [e for e in nodes if e.get("tag") == "button"]
    if len(btns) != 1:
        problems.append(f"应当正好一个 button，实际 {len(btns)} 个（按**整棵树**数）")
    for e in btns:
        text = e.get("text")
        # 2.0 的 `button.text` 只接 `plain_text`；写成 markdown 会被拒。
        if not isinstance(text, dict) or text.get("tag") != "plain_text":
            problems.append(f"button.text 必须是 {{'tag': 'plain_text', …}}（实际 {text!r}）")
        behs = e.get("behaviors")
        if not isinstance(behs, list) or not behs or not isinstance(behs[0], dict):
            problems.append("按钮缺**组件级** behaviors（2.0 卡里只有顶层 value 的按钮点不动）")
            continue
        if behs[0].get("type") != "callback":
            problems.append(f"behaviors[0].type 必须是 callback（实际 {behs[0].get('type')!r}）")
        # 多条目行为**未定义**（审计 FN-7）：我们只造一条，就要求**恰好一条 callback** ——
        # 与其让它「碰巧能用」，不如把意图钉死。
        if len(behs) != 1:
            problems.append(f"behaviors 应当**恰好一条**（实际 {len(behs)} 条，多条目行为未定义）")
        # ⚠️ 与适配器**同一条判据**（`cards.is_probe_value`）—— 别在这里另写一遍。
        if not cards.is_probe_value(behs[0].get("value")):
            problems.append(
                f"回调 value 必须让 cards.is_probe_value() 为真（即 dict 里 "
                f"{cards.PROBE_VALUE_KEY} 为**真值**）—— 否则适配器不派发、日志一行都没有，"
                f"用户点了我们也拿不到凭据。实际 {behs[0].get('value')!r}")
    # 1.0 的 action 行：**整棵树**扫（嵌在 column_set 里照样被拒收 `230099`）
    if any(e.get("tag") == "action" for e in nodes):
        problems.append("卡里混进了 1.0 的 action 按钮行 —— 飞书会拒收（230099），方言不许混用")
    if len(elems) > 200:
        problems.append(f"元素数 {len(elems)} 超官方硬上限 200")
    import json as _json
    size = len(_json.dumps(card, ensure_ascii=False).encode("utf-8"))
    # ⚠️ 边界是 **>**，不是 **>=**：`test_units.py` 里那条真机包络线写死了
    #    `128000 仍 code=0`（被接受），所以「恰好等于上限」是**能发出去**的。
    if size > cards.FEISHU_CARD_BYTE_LIMIT:
        problems.append(f"卡 JSON {size} 字节，**超过**硬上限 {cards.FEISHU_CARD_BYTE_LIMIT}")
    return problems


def probe_button_card(client, chat: str, cards) -> int:
    """**只发 ⑮ 一张**（2.0 `button` + 组件级 `behaviors`）。

    为什么值得单独一条命令：这一格（不变量 5 里 `button` 是唯一**只有官方文档**的组件）
    只能靠**真机点一次**判定，而「请人点一次」是本项目里**唯一需要消耗用户时间**的动作 ——
    所以要把它的成本压到最低：**发一张卡、点一个按钮、看日志一行**，不用在一批探针卡里找。

    ⚠️ 点击事件由**正在跑的网关**接收（这个脚本接不到），所以判据在网关日志里：
    `[larkdeck] 探针点击到达 ✅ tag=button …`。看到 = 2.0 按钮的服务端回调成立；
    什么都没有 = 点击没到服务端（那澄清卡就继续不用按钮形态）。
    """
    cases = [c for c in build_dialect_probe_cards(cards) if c[0].startswith("\u246e")]
    assert cases, "⑮ 用例不见了（`build_dialect_probe_cards` 被改过？）"
    label, card = cases[0]
    # ⚠️ **先自检再发**：用户那一击只能点一次，别把可本地预防的失败留给他的手。
    problems = probe_card_problems(card, cards)
    if problems:
        print("\u274c ⑮ 探针卡**本地自检没过**，没有发出去（免得白费你一次点击）：")
        for p in problems:
            print(f"   · {p}")
        return 1
    code, msg, mid = send(client, chat, card)
    # ⚠️ 标记先取出来再拼：f-string 的**表达式部分不许含反斜杠**（3.11 的限制）——
    #    直接写 `f"{'\u2705' if ...}"` 会 SyntaxError，而且这个文件**不被任何门禁 import**
    #    （它是真机探针），所以那种错只会等到真跑探针时才炸。改完必须 `py_compile` 一遍。
    mark = "\u2705" if code == 0 else "\u274c"
    print(f"{mark} {label}  code={code} {msg}")
    if code == 0:
        # ⚠️ **必须记进账本**（2026-09-16 审计：全文件 11 处入账，唯独这条路径没有）——
        #    不记的话，它只靠「7 天内、最近 50 条、且内容读得回来」的内容补扫才能被清掉，
        #    超过其中任何一条就永久留在用户 DM 里，只能靠人长按删除。
        if mid:
            try:
                _save_sent_ids(_load_sent_ids() + [mid])
            except Exception as exc:            # 记不上不该让探针失败（卡已经发出去了）
                print(f"   ⚠️  记账失败（不影响这次点击）：{exc!r}")
        print("   请点那张卡上的按钮，然后看网关日志里有没有这一行：")
        print("   [larkdeck] 探针点击到达 \u2705 tag=button \u2026")
    return 0 if code == 0 else 1


def probe_font_size(client, chat: str, cards) -> int:
    """窗口 0：normal vs normal_v2 两张正文对照卡，肉眼确认客户端字号。"""
    def _card(size: str) -> dict:
        card = cards.cardkit_entity_card(
            f"字号探针 **{size}**\n\n中文正文 ABC 123 —— 对比 14px 与 normal_v2 的可见差异。",
            panel_text=f"面板 {size}", streaming=False)
        for element in card.get("body", {}).get("elements", []):
            if element.get("element_id") == cards.CARDKIT_ANSWER_ID:
                element["text_size"] = size
        return card

    for size in ("normal", "normal_v2"):
        code, msg, message_id = send(client, chat, _card(size))
        print(f"字号探针 {size}: code={code} msg={msg} message_id={message_id}")
    print("请肉眼对比两张卡：正文可读性/紧凑度；确认后再决定是否写回 normal_v2 token。")
    return 0


def probe_structured_canary(client, chat: str, cards) -> int:
    """V1–V3 structured canary：不重启网关，走生产 structured 路径发一张真卡。"""
    import asyncio
    import importlib
    _, adapter, _ = _load_adapter_for_probe(chat)
    if adapter is None:
        return 1
    panel = sys.modules.get("hermes_plugins.larkdeck.core.panel")
    if panel is None:
        panel = importlib.import_module("hermes_plugins.larkdeck.core.panel")
    _set_probe_config(adapter, visual_engine="structured", card_status_header=True,
                      show_reasoning=True, native_transport="cardkit")
    sess = f"structured-canary-{int(time.time())}"
    panel.reset()
    panel.bind_chat_session(chat, sess)
    panel.record_reasoning(sess, sess, "先读取配置，再确认磁盘占用；这里演示推理 A 形态。")
    panel.record_tool_started(sess, sess, "terminal", {"command": "df -h"},
                              tool_call_id="tc-df")
    panel.record_tool_finished(sess, sess, "terminal", status="ok", duration_ms=1860,
                               tool_call_id="tc-df",
                               result="Filesystem Size Used Avail\n/dev/disk 460G 300G 160G")
    tid = f"structured-{int(time.time())}"

    async def _run() -> tuple:
        return (await adapter.send_stream_frame("正在整理磁盘报告", chat_id=chat, turn_id=tid),
                await adapter.send_stream_frame("正在整理磁盘报告：占用前三为 A、B、C",
                                                chat_id=chat, turn_id=tid),
                await adapter.send_stream_frame("磁盘报告完成：占用前三为 A、B、C。",
                                                chat_id=chat, turn_id=tid, finalize=True))

    seed, update, finalize = asyncio.run(_run())
    print(f"structured canary: seed={seed} update={update} finalize={finalize} "
          f"（chat={chat}，卡在最新一条消息）")
    print("请肉眼确认：工具行图标/22px、推理 A 形态、加载指示（会动、无文字）、页脚字段"
          "（状态·耗时·模型·ctx，**没有 🔖 短码**）。")
    return 0 if (seed and update and finalize) else 1


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
    if "--font-size" in argv:
        return probe_font_size(client, chat, cards)
    if "--structured-canary" in argv:
        return probe_structured_canary(client, chat, cards)
    if "--rate-limit" in argv:
        return probe_rate_limit(client, chat, cards)
    if "--stop-redraw" in argv:
        return probe_stop_redraw(client, chat, cards)
    if "--cardkit" in argv:
        return probe_cardkit(client, chat, cards)
    if "--cardkit-prod" in argv:
        return probe_cardkit_transport(client, chat, cards)
    if "--degrade-lane" in argv:
        return probe_degrade_lane(client, chat, cards)

    if "--button-2" in argv:
        return probe_button_card(client, chat, cards)

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
    if ok:
        print(f"全部被飞书接收 ✅ —— 去飞书 DM 看这 {len(cases)} 张卡：")
        print("  ⓪ 首帧占位卡（回合刚开始那一秒：应当是「⏳ 正在生成…」而不是空白）")
        print("  ①② 澄清卡（1.0 按钮 / 已答复回填）")
        print("  ③④ 折叠面板（③ 点一下标题行右边的三角应能展开）· ⑤⑥ 双语互换实验"
              "（看到英文说明 i18n 生效）")
        print("  ⑦⑧⑨ 页脚三样式 · ⑩⑪ 三种状态色边框 · ⑫ 方言探针（可点）· "
              "⑬⑭ 真 2.0 澄清卡（可点）· ⑮ 2.0 按钮探针（可点）")
    else:
        print("有卡片被拒 ❌ —— 按上面飞书给的 msg 改")
    print("注意：按钮点击不会被处理（本探针只验渲染，不验点击）；"
          "打字机单独跑：`--typing`；中止重绘单独跑：`--stop-redraw`；"
          "CardKit 对照跑：`--cardkit`。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
