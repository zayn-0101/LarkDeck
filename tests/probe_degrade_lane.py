"""真机探针（v0.7.20 规划阶段）：整卡 patch 能不能**保住嵌套子面板**、又能**不能续命**。

对应 `docs/internal/audits/v0.7.20/consensus.md` 的动手前提 V1 / V1b / V2：

* **V1**  把**生产渲染器**（`core.cardview.entity_skeleton`）造出的、含 ≥3 个嵌套
  `collapsible_panel` 的卡，用 `im.message.patch` **整卡**盖到一张 CardKit 实体卡上：
  服务端收不收（`code=0`）+ 人眼能不能展开内层（这一半只有眼睛能判）。
* **V1b** 同一次 patch 里 `config.streaming_mode=true`（生产默认）—— 之后**元素写还能不能写**：
  `code=0` ⇒ 元素通道能重新打开 ⇒ 长回合可以**不降级**；`300309` ⇒ 关了就回不来。
  这是 v0.7.20 方案 A 的**唯一硬前提**（仓库里此前没有真机证据）。
* **V2**  `--keepalive`：建一张流式卡，每 30 秒写一次元素，量「卡创建 → 第一次 300309」
  的秒数（A 路发现 5/5 次降级都落在 10.3–11.4 分钟，怀疑是飞书侧会话到期，用这条定性）。

用法::

    python3 tests/probe_degrade_lane.py             # V1 + V1b（卡保留给人眼看）
    python3 tests/probe_degrade_lane.py --delete     # 同上，跑完删卡
    python3 tests/probe_degrade_lane.py --keepalive  # V2：每 30s 写一次，最多 25 分钟
"""
from __future__ import annotations

import json
import pathlib
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent))

import probe_ck_stream_ops as _base                                  # noqa: E402
from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody  # noqa: E402

from larkdeck.core import cardview as _cv                            # noqa: E402

_SCRATCH = pathlib.Path.home() / ".larkdeck-scratch"
_SCRATCH.mkdir(exist_ok=True)


def _nested_production_card(*, rounds: int = 3, tools: int = 3,
                            expanded: bool = True) -> dict:
    """用**生产**渲染器造一张带嵌套子面板的卡（不是手搓 JSON）。"""
    round_views = [
        _cv.ReasoningRoundView(index=i, text=f"第 {i + 1} 轮推理（探针）",
                               elapsed_ms=1000 * (i + 1), finalized=(i < rounds - 1))
        for i in range(rounds)
    ]
    tool_views = [
        _cv.ToolStepView(name="terminal", title="terminal", status="ok",
                         duration_ms=123 + i, detail='{"cmd": "ls"}')
        for i in range(tools)
    ]
    panel = _cv.PanelView(title=f"探针面板 · {rounds} 轮 · {tools} 工具", expanded=expanded,
                          border="grey", reasoning_rounds=round_views, tools=tool_views)
    view = _cv.CardView(
        answer=("**探针 V1/V1b**：如果你看到下面有 "
                f"{rounds} 个可以展开的「第 N 轮思考」，说明**整卡 patch 能带嵌套子面板**。"),
        panel_enabled=True, footer_enabled=True, footer="✅ 探针 V1/V1b · 整卡 patch",
        panel=panel)
    return _cv.entity_skeleton(view)


def probe_degrade_lane(keep: bool) -> int:
    client, chat = _base._connect()
    card = _base._Card(client, chat)
    print("—— V1：整卡 patch 到实体卡，载荷里带生产渲染器的嵌套子面板 ——")
    if not card.open(answer="（V1 基线）", panel="面板：基线"):
        return 1
    card.write("基线帧", "v1-base")

    payload = _nested_production_card()
    blob = json.dumps(payload, ensure_ascii=False)
    nested = blob.count('"collapsible_panel"')
    print(f"   载荷：{len(blob)} 字节 · collapsible_panel × {nested}")

    r = client.im.v1.message.patch(
        PatchMessageRequest.builder().message_id(card.message_id)
        .request_body(PatchMessageRequestBody.builder().content(blob).build()).build())
    code = card._code(r)
    print(f"   V1 整卡 patch → code={code} · msg={str(getattr(r, 'msg', ''))[:90]!r}")

    print("\n—— V1b：这次 patch 带着 streaming_mode=true，之后元素写还活着吗？——")
    alive = card.write("整卡 patch 之后写元素", "v1b-after-patch")
    print(f"   V1b 元素写存活 = {alive}（True ⇒ 元素通道能重开 ⇒ 长回合可以不降级）")

    # 保留/删除
    if keep:
        print(f"\n👀 请看飞书里那张卡（message_id={card.message_id}）：")
        print("   1) 正文下面是否有面板，面板里是否有 3 个可展开的「第 N 轮思考」子面板？")
        print("   2) 子面板展开后是否能看到内容？")
        print("   3) 【V3 用】请手动把**外层面板收起**，然后告诉我「收起好了」——")
        print("      我再发一次整卡 patch，看你收起的状态会不会被顶开。")
    else:
        card.delete()
        print("\n（已按 --delete 删卡）")

    problems = []
    if code != 0:
        problems.append(f"V1 整卡 patch 被拒（code={code}）⇒ 方案 A 硬前提不成立")
    if nested < 3:
        problems.append(f"V1 载荷里 collapsible_panel 只有 {nested} 个（应 ≥3）")
    if problems:
        print("\n❌ 判据未过：")
        for item in problems:
            print(f"   - {item}")
        return 1
    print(f"\n✅ V1 通过（code=0、载荷含 {nested} 个嵌套面板）· V1b 元素写存活={alive}")
    return 0


def probe_keepalive(interval: int = 30, limit_s: int = 1500) -> int:
    """V2：量「卡创建 → 第一次非 0 码」的秒数（飞书侧流式会话寿命）。"""
    client, chat = _base._connect()
    card = _base._Card(client, chat)
    log = _SCRATCH / "probe-v2-keepalive.log"
    if not card.open(answer="（V2 保活探针，可以忽略；跑完会删）", panel="面板：保活"):
        return 1
    t0 = time.monotonic()
    lines = [f"# V2 保活探针 · card_id={card.card_id} · message_id={card.message_id}",
             f"# 起点 {time.strftime('%Y-%m-%d %H:%M:%S')} · 间隔 {interval}s"]
    print("\n".join(lines[:1]))
    while True:
        elapsed = time.monotonic() - t0
        ok = card.write(f"保活 {int(elapsed)}s", f"ka-{int(elapsed)}")
        code = "" 
        lines.append(f"elapsed={int(elapsed)}s alive={ok}")
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if not ok:
            lines.append(f"# 第一次写失败于 {int(elapsed)}s ⇒ 会话寿命≈{int(elapsed)}s")
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"❌ 第一次写失败：{int(elapsed)}s（≈{int(elapsed) / 60:.1f} 分钟）")
            break
        if elapsed > limit_s:
            lines.append(f"# 到 {limit_s}s 仍未失败（寿命 > 上限）")
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"⏹ 到 {limit_s}s 仍未失败（寿命大于本次上限）")
            break
        time.sleep(interval)

    # --- V2b：到期之后，能不能用「整卡 patch（生产结构化卡，streaming_mode=true）」续命？ ---
    print("—— V2b：到期后发一次生产结构化整卡 patch，然后看元素写能不能复活 ——")
    payload = _nested_production_card(rounds=1, tools=1, expanded=False)
    blob = json.dumps(payload, ensure_ascii=False)
    r = client.im.v1.message.patch(
        PatchMessageRequest.builder().message_id(card.message_id)
        .request_body(PatchMessageRequestBody.builder().content(blob).build()).build())
    code_patch = card._code(r)
    revived = card.write("到期后整卡 patch + 再写元素", "v2b-revive")
    lines.append(f"# V2b：到期后整卡 patch code={code_patch} · 之后元素写存活={revived}")
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"   V2b 整卡 patch → code={code_patch} · 之后元素写存活={revived}")
    card.delete()
    print(f"日志：{log}")
    return 0


def probe_v3(message_id: str, expanded: bool) -> int:
    """V3：对**已有**那条消息再发一次整卡 patch，看用户手动收起的面板会不会被顶开。

    为什么只有眼睛能判：整卡 patch **必然**带 `expanded` 字段，而元素车道的成文契约是
    「中间帧不重放展开态、用户手动收起不被顶开」。如果 patch 会把收起的面板顶开，
    续命/降级那一帧就不能无条件写 `expanded=True`。
    """
    client, _chat = _base._connect()
    payload = _nested_production_card(rounds=3, tools=3, expanded=expanded)
    payload["body"]["elements"][0]["content"] = (
        "**V3 探针**：刚刚发了第二次整卡 patch（expanded=%s）。\n"
        "请看**外层面板**现在是「保持你收起的样子」还是「被重新展开了」。" % expanded)
    blob = json.dumps(payload, ensure_ascii=False)
    r = client.im.v1.message.patch(
        PatchMessageRequest.builder().message_id(message_id)
        .request_body(PatchMessageRequestBody.builder().content(blob).build()).build())
    print(f"V3 整卡 patch（expanded={expanded}）→ code={int(getattr(r, 'code', -1) or 0)}"
          f" · msg={str(getattr(r, 'msg', ''))[:80]!r}")
    print(f"   message_id={message_id}")
    return 0


def main(argv: list) -> int:
    if "--keepalive" in argv:
        return probe_keepalive()
    if "--v3" in argv:
        return probe_v3(argv[argv.index("--v3") + 1],
                        expanded=("--collapsed" not in argv))
    return probe_degrade_lane(keep=("--delete" not in argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
