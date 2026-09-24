"""澄清卡的**真实类**端到端验证（发送 → 点击 → 网关解除阻塞）。

与 ``test_units.py`` 的分工
--------------------------
``test_units.py`` 用桩类验证逻辑；这里用的是**从平台注册表里取出来的真内置
``FeishuAdapter``**，所以能抓到桩类抓不到的问题：方法签名不匹配、私有原语改名、
MRO 覆盖没生效、返回类型不对。

它跑在 Hermes 自己的 venv 里，但**不连飞书、不发任何网络请求** —— 只把最底层的
``_feishu_send_with_retry`` 换掉以捕获 payload，其余全是真实现。

验证四件事：
  1. ``send_clarify`` 产出的确实是 **legacy 1.0** 卡片，按钮在 ``action`` 容器里；
  2. 模拟点击后，``tools.clarify_gateway`` 里那个**真的** waiter 被解除阻塞；
  3. 点「其他」走 ``mark_awaiting_text``，不提交答案；
  4. 回填的卡与待答卡**同方言**（都 1.0）—— 2.0 会被飞书静默丢弃。

跑法::

    python3 tests/check_clarify_e2e.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_INSTALL = Path.home() / ".hermes" / "hermes-agent"

_INNER = r'''
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

INSTALL = Path(sys.argv[1])
sys.path.insert(0, str(INSTALL))

from hermes_cli.plugins import discover_plugins          # noqa: E402
from gateway.platform_registry import platform_registry  # noqa: E402
from gateway.config import PlatformConfig                # noqa: E402

discover_plugins()
factory = platform_registry.get("feishu").adapter_factory
adapter = factory(PlatformConfig(enabled=True, extra={}))

# 内置适配器由插件加载器装成**独立模块**（hermes_plugins.feishu_platform.adapter），
# 与按路径 import 到的 plugins/platforms/feishu/adapter.py **不是同一个模块对象**。
# 所以必须从真实类的 __globals__ 里取模块，否则 _load_lark_oapi() 会绑错地方。
_real_mod = sys.modules[type(adapter)._card_response.__globals__["__name__"]]
_real_mod._load_lark_oapi()          # 类名是延迟绑定的，不触发的话卡响应恒为 None
FeishuAdapter = next(c for c in type(adapter).__mro__ if c.__name__ == "FeishuAdapter")

# --- 只替换最底层发送原语，其余（重试/限流/封口/卡响应）全是真实现 -------------
class _Resp:
    code, msg = 0, "ok"

    class _D:
        message_id = "om_e2e_1"
    data = _D()

    def success(self):
        return True


sent = []


async def _fake_send(*, chat_id, msg_type, payload, reply_to, metadata):
    sent.append((msg_type, json.loads(payload)))
    return _Resp()


adapter._feishu_send_with_retry = _fake_send
adapter._client = object()          # 过掉 send_clarify 的 client 门禁

from tools import clarify_gateway as cg  # noqa: E402

failures = []


def check(cond, label):
    print(("ok    " if cond else "FAIL  ") + label)
    if not cond:
        failures.append(label)


def legacy_card_ok(card):
    """待答卡必须顶层 elements、无 schema/body、按钮在 action 容器里。"""
    if "schema" in card or "body" in card:
        return False
    if not isinstance(card.get("elements"), list):
        return False
    for el in card["elements"]:
        if el.get("tag") == "action" and el.get("actions"):
            return True
    return False


def digest(obj):
    """把 lark 的响应对象拍平成 JSON，便于断言里面是什么卡。"""
    return json.dumps(obj, default=lambda o: getattr(o, "__dict__", str(o)),
                      ensure_ascii=False, sort_keys=True)


async def scenario():
    adapter._loop = asyncio.get_running_loop()
    ld_mod = None
    for _name in ("hermes_plugins.larkdeck.core.adapter", "larkdeck.core.adapter"):
        if _name in sys.modules:
            ld_mod = sys.modules[_name]
            break

    # ⚠️ 方言**必须显式配**，不能依赖默认值：2026-09-13 默认从 "1.0" 翻成了 "2.0"
    # （真机点击到达 + 2.0 e2e 全绿，两条前提都满足），于是下面这些**按 1.0 形状断言**的
    # 场景当场就红了 —— 这不是「默认翻错了」，而是这些场景原来偷偷依赖了那个默认值。
    # 现在每条场景自己声明方言；顺带把「默认是 2.0」也钉成一条断言。
    if ld_mod is not None:
        check(ld_mod._DEFAULTS.get("clarify_dialect") == "2.0",
              "默认方言是 2.0（翻默认值的前提：真机点击到达 + 2.0 e2e 全绿）")
        ld_mod._CONFIG["clarify_dialect"] = "1.0"      # 下面按 1.0 形状断言

    # ---------------- 场景 1：选项按钮点击 → 解除阻塞 ----------------
    question, choices, cid, skey = "选哪个方案？", ["A 方案", "B 方案"], "cid-e2e-1", "sk-1"
    cg.register(cid, skey, question, list(choices))

    result = await adapter.send_clarify("oc_test", question, choices, cid, skey)
    check(getattr(result, "success", False) is True, "send_clarify 返回成功")

    check(len(sent) == 1 and sent[0][0] == "interactive",
          "发出的是 interactive 卡片消息（不是纯文本）")
    card = sent[0][1] if sent else {}
    check(legacy_card_ok(card), "待答卡是 legacy 1.0 且按钮在 action 容器里")
    buttons = [b for el in card.get("elements", []) if el.get("tag") == "action"
               for b in el["actions"]]
    check(len(buttons) == 3, "两个选项 + 一个『其他』= 3 个按钮")
    check(buttons and buttons[0]["value"].get("answer") == "A 方案",
          "第一个按钮 value 带答案")
    check(buttons and buttons[0]["value"].get("question") == question,
          "按钮 value 带问题原文（回填卡要用）")

    # 模拟飞书推来的点击事件（字段路径与内置注册的 p2 回调一致）
    value = dict(buttons[0]["value"])
    data = NS(event=NS(action=NS(value=value),
                       operator=NS(open_id="ou_zayn"),
                       context=NS(open_message_id="om_e2e_1", chat_id="oc_test")))
    resp = adapter._on_card_action_trigger(data)

    answer = cg.wait_for_response(cid, 2.0)
    check(answer == "A 方案", f"网关 waiter 被解除阻塞并拿到答案（实得 {answer!r}）")

    dump = digest(resp)
    check('"elements"' in dump and "A 方案" in dump,
          "回调原地回填了『已答复』卡")
    check('"schema"' not in dump, "回填卡也是 1.0 方言（混 2.0 会被飞书丢弃）")

    # ---------------- 场景 2：点「其他」→ 转文字等待，不提交答案 ----------------
    cid2, skey2 = "cid-e2e-2", "sk-2"
    cg.register(cid2, skey2, "还有别的意见吗？", ["就这样", "再改改"])

    await adapter.send_clarify("oc_test", "还有别的意见吗？", ["就这样", "再改改"], cid2, skey2)
    card2 = sent[-1][1]
    buttons2 = [b for el in card2.get("elements", []) if el.get("tag") == "action"
                for b in el["actions"]]
    other_value = buttons2[-1]["value"]
    check(other_value.get("answer") == "__larkdeck_other__", "末位按钮是『其他』哨兵")

    entry2 = cg._entries.get(cid2)
    check(getattr(entry2, "awaiting_text", None) is False, "点之前不处于文字等待态")

    data2 = NS(event=NS(action=NS(value=other_value),
                        operator=NS(open_id="ou_zayn"),
                        context=NS(open_message_id="om_e2e_2", chat_id="oc_test")))
    adapter._on_card_action_trigger(data2)

    check(getattr(entry2, "awaiting_text", None) is True,
          "点『其他』后转为等待用户输入文字")
    check(not entry2.event.is_set(), "点『其他』不会提交答案、不会解除阻塞")

    # ---------------- 场景 2b：**2.0 方言**（下拉 / 多选 / 输入框）走真实网关 ----------------
    # 2026-09-13 审计的结论很硬：这三条路径原先**没有任何门禁经过真实的
    # clarify_gateway 判据** —— 把答案改成根本不可能被接受的形态（如 "\x01" 连接）
    # 门禁照样全绿，于是「本地全绿、真机全废」的多选缺陷可以长期存活。
    # 所以这里必须走**真 gateway + 真工具侧解析**，而不是核字面形状。
    ld_mod = sys.modules.get("hermes_plugins.larkdeck.core.adapter") or sys.modules["larkdeck.core.adapter"]
    ld_mod._CONFIG["clarify_dialect"] = "2.0"
    try:
        sent.clear()
        cid3, skey3 = "cid-e2e-3", "sk-3"
        cg.register(cid3, skey3, "单选还是多选？", ["A 方案", "B 方案", "C 方案"])
        await adapter.send_clarify("oc_test", "单选还是多选？",
                                   ["A 方案", "B 方案", "C 方案"], cid3, skey3)
        card3 = sent[-1][1]
        check(card3.get("schema") == "2.0", "2.0 方言确实生效（clarify_dialect=2.0）")
        tags3 = json.dumps(card3, ensure_ascii=False)
        # **逐个**交互组件检查 behaviors —— 只核「卡里出现过 behaviors 字样」会被
        # 「一个组件漏了」的变异骗过去（实测：只删掉下拉的 behaviors 时旧断言照样绿）
        interactive = [el for el in card3.get("body", {}).get("elements", [])
                       if el.get("tag") in ("select_static", "multi_select_static",
                                            "input", "button")]
        missing = [el.get("tag") for el in interactive if not el.get("behaviors")]
        check(interactive and not missing,
              f"2.0 卡的每个交互组件都必须带组件级 behaviors（缺：{missing}）")
        check('"note"' not in tags3 and '"tag": "action"' not in tags3,
              "2.0 卡里不能混 1.0 的 note / action 行（飞书会拒收）")

        # —— 单选下拉：官方形状是 action.option（字符串）——
        opt_value = {"larkdeck_action": "clarify", "clarify_id": cid3,
                     "session_key": skey3, "question": "单选还是多选？"}
        click = NS(event=NS(action=NS(value=opt_value, option="B 方案", options=None,
                                      input_value=None),
                            operator=NS(open_id="ou_zayn"),
                            context=NS(open_message_id="om_e2e_3", chat_id="oc_test")))
        resp_2 = adapter._on_card_action_trigger(click)
        check(cg.wait_for_response(cid3, 2.0) == "B 方案",
              "2.0 单选的 action.option 能真正解除网关阻塞（走真判据）")

        # —— 判据落在 **真 SDK 类** 上（收口清单 #10）——
        # 为什么比单测里的哑类强：`test_units.py` 用的是自造的
        # `_fake_toast_classes()` —— 它只是两个空对象，`response.card = 卡` 这种赋值
        # **永远成功**，所以「字段名写错」在那里看不见；而这里用的是
        # `lark_oapi.event.callback.model.p2_card_action_trigger` 里的**真类**，
        # 真类带 `_types` 声明，且 `digest()` 走的是 WS 客户端
        # （`lark_oapi/ws/client.py` 的 `JSON.marshal(result)`）那条线上序列化的同一批字段
        # —— 断言的东西与**真机上会发出去的响应体**同源（`docs/lessons.md` 推论 19
        # 第三形态：别把「断言我自己的替身」当成「断言生产代码」）。
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse as _REAL_RESP)
        check(isinstance(resp_2, _REAL_RESP),
              f"响应必须是真 SDK 类的实例（实得 {type(resp_2).__name__}）")
        if isinstance(resp_2, _REAL_RESP):
            dump_ok = digest(resp_2)
            check(resp_2.card is not None and resp_2.toast is None,
                  "成功那一击：响应里只有卡、没有 toast")
            check("B 方案" in dump_ok and '"elements"' in dump_ok,
                  f"成功响应里真的装着换上去的卡：{dump_ok[:200]}")
            # ⚠️ 判据只能用**属性**（`resp.card is not None`），不能拿 `'"toast"' not in dump`
            # —— 真 SDK 类会把没设过的字段一起序列化成 `"card": null` / `"toast": null`
            # （实测：成功那一击的 dump 里既有 `"card"` 也有 `"toast"`）。这正是「真类」比
            # 哑类强的另一面：它连**字段存在性**都如实反映，所以判据必须落在值上。
            check('"toast": null' in dump_ok,
                  f"成功响应里 toast 必须是**空的**（允许出现 null 键，值必须为空）：{dump_ok[:200]}")

        # —— 失败态：同一个 clarify_id 再点一次 ⇒ 网关返回 False ⇒ 只有 toast、没有卡 ——
        dup = NS(event=NS(action=NS(value=dict(opt_value), option="B 方案", options=None,
                                    input_value=None),
                          operator=NS(open_id="ou_zayn"),
                          context=NS(open_message_id="om_e2e_3", chat_id="oc_test")))
        resp_dup = adapter._on_card_action_trigger(dup)
        check(isinstance(resp_dup, _REAL_RESP),
              f"失败态响应也必须是真 SDK 类的实例（实得 {type(resp_dup).__name__}）")
        if isinstance(resp_dup, _REAL_RESP):
            dump_dup = digest(resp_dup)
            check(resp_dup.card is None and resp_dup.toast is not None,
                  "重复点击：响应里只有 toast、**没有卡**（有卡就是把已确认退回待答）")
            check(getattr(resp_dup.toast, "type", None) == "error",
                  f"失败提示应当是 error：{getattr(resp_dup.toast, 'type', None)!r}")
            # ⚠️ 语义判据（审计中-1）：走到这条 toast 时该澄清**已经**被处理掉，
            # 再点一次**永远不可能成功**（Hermes `clarify_gateway.py:90-98`）——
            # 所以线上载荷里**不许**出现邀请重试的话。判据落在**序列化后的载荷**上，
            # 与真机发出去的那一坨同源。
            check("重试" not in dump_dup and "retry" not in dump_dup.lower(),
                  f"线上载荷里不许邀请用户重试（这次重试永远不可能成功）：{dump_dup[:200]}")
            check("无需重复" in dump_dup,
                  f"线上载荷必须说明「无需重复点击」：{dump_dup[:200]}")
            # 同一条纪律：判据落在**值**上（真类会把不设的字段序列化成 null）
            check('"card": null' in dump_dup,
                  f"失败响应的卡必须是**空的**（带卡就等于把已确认退回待答）：{dump_dup[:200]}")

        # —— 多选：官方形状是 action.options（**string[]**），答案要逗号串 ——
        cid4, skey4 = "cid-e2e-4", "sk-4"
        cg.register(cid4, skey4, "选哪些？", ["A 方案", "B 方案", "C 方案"],
                    multi_select=True)
        await adapter.send_clarify("oc_test", "选哪些？", ["A 方案", "B 方案", "C 方案"],
                                   cid4, skey4)
        multi_value = {"larkdeck_action": "clarify", "clarify_id": cid4,
                       "session_key": skey4, "question": "选哪些？"}
        click4 = NS(event=NS(action=NS(value=multi_value, option=None,
                                       options=["A 方案", "C 方案"], input_value=None),
                             operator=NS(open_id="ou_zayn"),
                             context=NS(open_message_id="om_e2e_4", chat_id="oc_test")))
        adapter._on_card_action_trigger(click4)
        got4 = cg.wait_for_response(cid4, 2.0)
        check(got4 is not None, f"2.0 多选没能解除阻塞（实得 {got4!r}）")
        # 用**工具侧自己的解码器**验证答案 —— 这才是下游真正消费的形态，
        # 而不是「字符串里恰好含这几个字」。两种合法形态（JSON / 逗号串）都算通过，
        # 但解码结果必须正好是用户勾选的那两项。
        sys.path.insert(0, str(INSTALL))
        from tools.clarify_tool import _clean_answer as _decode_multi
        check(_decode_multi(got4, True) == ["A 方案", "C 方案"],
              f"2.0 多选答案在下游解码后不对：{_decode_multi(got4, True)!r}")

        # —— 输入框：自由文本，必须只解**这张卡自己的**澄清 ——
        # 同 session 再挂一条待答（更旧），然后在**新**卡的输入框里作答：
        # 走「最旧待答」的实现会把答案解到旧问题上去（审计 A2 实测过的错配）。
        cid_old, cid_new = "cid-e2e-old", "cid-e2e-new"
        cg.register(cid_old, "sk-5", "旧问题：目标环境？", ["staging", "prod"])
        cg.register(cid_new, "sk-5", "新问题：回滚策略？", ["立即回滚", "灰度"])
        await adapter.send_clarify("oc_test", "新问题：回滚策略？", ["立即回滚", "灰度"],
                                   cid_new, "sk-5")
        text_value = {"larkdeck_action": "clarify", "clarify_id": cid_new,
                      "session_key": "sk-5", "question": "新问题：回滚策略？",
                      "free_text": True}
        click5 = NS(event=NS(action=NS(value=text_value, option=None, options=None,
                                       input_value="1"),
                             operator=NS(open_id="ou_zayn"),
                             context=NS(open_message_id="om_e2e_5", chat_id="oc_test")))
        adapter._on_card_action_trigger(click5)
        check(cg._entries[cid_old].event.is_set() is False,
              "输入框的答案被解到了**另一个**澄清上（假确认 + 错答案）")
        check(cg._entries[cid_new].event.is_set() is True,
              "输入框的答案没有落在它自己那个澄清上")
        check(cg._entries[cid_new].response == "立即回滚",
              f"输入框答案解析不对：{cg._entries[cid_new].response!r}")

        # —— 输入框里输入**散文**（不是编号、不是选项原文）：必须也能作答 ——
        # 审计 P2：卡片上写着「直接输入你的答案」，但服务端没人把它切成「等待文字输入」，
        # 核心的判据对「选项题 + 散文」返回 rejected_prose ⇒ 用户打了字、回车、
        # **什么都没发生也没有提示**。1.0 路径没这个问题（用户得先点「其他」按钮）。
        cid_prose, skey_prose = "cid-e2e-prose", "sk-6"
        cg.register(cid_prose, skey_prose, "还有什么要补充？", ["没有", "有"])
        await adapter.send_clarify("oc_test", "还有什么要补充？", ["没有", "有"],
                                   cid_prose, skey_prose, )
        prose_value = {"larkdeck_action": "clarify", "clarify_id": cid_prose,
                       "session_key": skey_prose, "question": "还有什么要补充？"}
        click6 = NS(event=NS(action=NS(value=prose_value, option=None, options=None,
                                       input_value="我想先观察一下再说"),
                             operator=NS(open_id="ou_zayn"),
                             context=NS(open_message_id="om_e2e_6", chat_id="oc_test")))
        adapter._on_card_action_trigger(click6)
        check(cg._entries[cid_prose].event.is_set() is True,
              "输入框里的散文没有被接受（打了字、回车、什么都没发生）")
        check(cg._entries[cid_prose].response == "我想先观察一下再说",
              f"散文答案被改写了：{cg._entries[cid_prose].response!r}")
        # —— 表单提交（官方按钮 `form_action_type: submit`）：`action.value` **是空的**，
        #    路由键与答案都只在 `action.form_value` 里（官方 `card-callback-communication.md`
        #    41-49；FC/aiduPOP 都是这个形态）。这里用**真 SDK payload 类**构造事件 ——
        #    与真机 WS 反序列化出来的对象同源（审计 C 实测：合成 `SimpleNamespace` 抓不到
        #    「字段名写错」，而字段名写错 = 用户点了没反应）。
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTrigger as _REAL_EVENT)
        cid_form, skey_form = "cid-e2e-form", "sk-form"
        cg.register(cid_form, skey_form, "表单提交要选哪个？", ["A 方案", "B 方案"])
        ev_form = _REAL_EVENT({"event": {
            "action": {"value": {},          # ⚠️ 提交按钮的 value 是**空的**
                       "form_value": {"larkdeck_action": "clarify",
                                      "clarify_id": cid_form,
                                      "session_key": skey_form,
                                      "question": "表单提交要选哪个？",
                                      "answer": "B 方案"}},
            "operator": {"open_id": "ou_zayn"},
            "context": {"open_message_id": "om_e2e_form", "open_chat_id": "oc_test"},
        }})
        check(ev_form.event.action.value == {} and ev_form.event.action.form_value,
              "前提：这是「value 空、答案在 form_value」的提交形态")
        resp_form = adapter._on_card_action_trigger(ev_form)
        # ⚠️ 顺序：先读 entry（`wait_for_response` 会在返回前**把 entry pop 掉** —— 它的
        #    契约是「无论结果如何都清理」），再调用有界等待器；反过来的话这里会 KeyError。
        check(cg._entries[cid_form].event.is_set() is True,
              "表单提交后事件必须已 set（同步 handler，无需等）")
        check(cg._entries[cid_form].response == "B 方案", "答案原值必须落到会话上下文")
        check(cg.wait_for_response(cid_form, 2.0) == "B 方案",
              "表单提交（答案只在 form_value）必须真正解除网关阻塞 —— 否则就是「空提交」")
        dump_form = digest(resp_form)
        check(resp_form.card is not None and resp_form.toast is None,
              f"表单提交必须回执换卡（不是空提交 toast）：{dump_form[:200]}")
        resp_form2 = adapter._on_card_action_trigger(ev_form)
        check(resp_form2.card is None and resp_form2.toast is not None,
              "同一条提交再点一次：只许 toast，不许把已确认退回待答")

        # —— 多选表单：路由键在提交按钮 value，选中值在 form_value["clarify_options"] ——
        # 2026-09-24 用户反馈「多选没有自动提交、也没有提交按钮」后的新形态。
        cid_multi, skey_multi = "cid-e2e-multi", "sk-multi"
        cg.register(cid_multi, skey_multi, "多选测试：要哪些？", ["A 方案", "B 方案"],
                    multi_select=True)
        ev_multi = _REAL_EVENT({"event": {
            "action": {"value": {"larkdeck_action": "clarify",
                                 "clarify_id": cid_multi,
                                 "session_key": skey_multi,
                                 "question": "多选测试：要哪些？"},
                       "tag": "button",
                       "name": "clarify_submit",
                       "form_value": {"clarify_options": ["A 方案", "B 方案"]}},
            "operator": {"open_id": "ou_zayn"},
            "context": {"open_message_id": "om_e2e_multi", "open_chat_id": "oc_test"},
        }})
        resp_multi = adapter._on_card_action_trigger(ev_multi)
        check(cg._entries[cid_multi].event.is_set() is True,
              "多选表单提交后事件必须已 set（同步 handler）")
        check(cg._entries[cid_multi].response == json.dumps(["A 方案", "B 方案"],
                                                            ensure_ascii=False),
              f"多选表单的答案必须是 JSON 数组字符串：{cg._entries[cid_multi].response!r}")
        check(resp_multi.card is not None and resp_multi.toast is None,
              f"多选表单提交必须回执换卡：{digest(resp_multi)[:200]}")

    finally:
        ld_mod._CONFIG["clarify_dialect"] = "1.0"

    # ---------------- 场景 3：非本插件的点击必须回落内置实现 ----------------
    check(callable(getattr(adapter, "_card_response", None)), "_card_response 可用")
    check(adapter._card_response({"ping": 1}) is not None,
          "SDK 类名已绑定，卡响应不是 None（否则点击后卡片永远不刷新）")

    fell_through = {}


    def _spy_super(self, data):
        fell_through["hit"] = True
        return "builtin"


    FeishuAdapter = globals()["FeishuAdapter"]
    original = FeishuAdapter._on_card_action_trigger
    FeishuAdapter._on_card_action_trigger = _spy_super
    try:
        plain = NS(event=NS(action=NS(value={"hermes_action": "approve"}),
                            operator=NS(open_id="ou_zayn"),
                            context=NS(open_message_id="om_x", chat_id="oc_x")))
        adapter._on_card_action_trigger(plain)
    finally:
        FeishuAdapter._on_card_action_trigger = original
    check(fell_through.get("hit") is True, "非 larkdeck 的点击原样交回内置实现")


asyncio.run(scenario())

print()
if failures:
    print(f"FAILED: {len(failures)} 项 -> " + "; ".join(failures))
    raise SystemExit(5)
print("CLARIFY E2E OK")
'''


def main() -> int:
    install = Path(os.environ.get("HERMES_INSTALL_DIR") or DEFAULT_INSTALL)
    if not (install / "gateway" / "platform_registry.py").is_file():
        print(f"找不到 Hermes 安装目录：{install}")
        print("用 HERMES_INSTALL_DIR=<路径> 指定。")
        return 1

    python = install / "venv" / "bin" / "python3"
    if not python.is_file():
        python = Path(sys.executable)

    with tempfile.TemporaryDirectory(prefix="larkdeck-e2e-") as tmp:
        home = Path(tmp) / "home"
        (home / "plugins").mkdir(parents=True)
        (home / "plugins" / "larkdeck").symlink_to(REPO, target_is_directory=True)
        (home / "config.yaml").write_text(
            "plugins:\n  enabled:\n    - larkdeck\n", encoding="utf-8"
        )
        env = dict(os.environ)
        env["HERMES_HOME"] = str(home)
        env["PYTHONPATH"] = str(REPO.parent) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [str(python), "-c", _INNER, str(install)],
            env=env, cwd=str(install), capture_output=True, text=True,
        )
        print(proc.stdout.strip())
        if proc.stderr.strip():
            print("--- stderr ---")
            print(proc.stderr.strip()[-3000:])
        if proc.returncode != 0:
            print(f"\n退出码 {proc.returncode}")
            return proc.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
