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
  4. 回填的卡与待答卡**同方言**（都 1.0）—— 3.0 会被飞书静默丢弃。

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
    await asyncio.sleep(0.3)

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
    await asyncio.sleep(0.3)

    check(getattr(entry2, "awaiting_text", None) is True,
          "点『其他』后转为等待用户输入文字")
    check(not entry2.event.is_set(), "点『其他』不会提交答案、不会解除阻塞")

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
