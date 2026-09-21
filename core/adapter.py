"""LarkDeck —— 用「继承 + 覆盖」给内置飞书适配器换上卡片渲染。

架构（三句话）
--------------
1. Hermes 的 ``platform_registry`` 对同名平台是**最后写入者胜**。larkdeck 在内置
   ``feishu`` 平台注册之后再注册一次 ``feishu``，于是被解析到的就是我们的工厂。
2. 我们的工厂先向注册表问出**内置适配器的真实类**，然后用
   ``type("LarkDeckFeishuAdapter", (LarkDeckMixin, <内置类>), {})`` **直接构造**一个
   实例 —— 鉴权、WebSocket、长连接守护、媒体、重试、限流全都继承自内置类。
3. 于是只需要覆盖四处：``send`` / ``edit_message`` / ``send_clarify`` /
   ``_on_card_action_trigger``，其余全部继承。

为什么不用 ``instance.__class__ = 子类``
---------------------------------------
CPython 允许改 ``__class__`` 的前提之一是「新类的**直接基类链**与旧类一致」，不只是内存
布局一致。``type("X", (Mixin, FeishuAdapter), {})`` 的直接基类是 ``Mixin`` 而非
``FeishuAdapter``，赋值必抛 ``TypeError: object layout differs``。直接构造子类没有这个
约束。代价是首轮多造一个实例 —— 它的 ``__init__`` 只读配置、不建连接，无副作用。

按钮回调为什么能接上
--------------------
内置适配器在 ``_prepare_client()`` → ``_build_event_handler()`` 里用
``register_p2_card_action_trigger(self._on_card_action_trigger)`` 注册**绑定方法**
（0.21.1 实测：注册点写在 ``_build_event_handler``，由 ``_prepare_client`` 调用，
而 ``_prepare_client`` 又晚于我们构造实例的时机）。所以那一刻按 MRO 解析出的就是
本类的实现。若哪天内置改成在 ``__init__`` 里注册，这里就会静默失效，
``tests/check_override.py`` 专门盯着这一点。

为什么这么绕而**不** import 内置适配器模块
------------------------------------------
内置适配器所在目录名、包名、加载方式都可能随 Hermes 版本变（Mac 是
``~/.hermes/hermes-agent/plugins/platforms/feishu/``，NAS 是
``/opt/hermes/plugins/platforms/feishu/``）。从注册表里取「上一个工厂」是唯一
不依赖任何路径的接法 —— 也正因如此，Hermes 升级后本插件不需要重装、不需要重新注入。

流式链路（全部是官方公开契约，无源码改写）
------------------------------------------
``config.yaml`` 的 ``streaming.transport: edit`` 下，网关消费者走：
``send()`` 建首帧 → 反复 ``edit_message(finalize=False)`` → 末帧
``edit_message(finalize=True)``。``REQUIRES_EDIT_FINALIZE = True`` 告诉消费者
「末帧也要走 edit，不要另发一条新消息」—— 这正是「一张卡片原地流到最后」的关键。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import threading
import time
import unicodedata
from collections import deque
from types import SimpleNamespace
from typing import (Any, Deque, Dict, List, Mapping, NamedTuple, Optional, Sequence,
                    Tuple)

from . import cards as _cards
from . import cardview as _cardview
from . import compat as _compat
from . import context as _context
from . import hooks as _hooks
from . import i18n as _i18n
from . import panel as _panel

logger = logging.getLogger("larkdeck")

#: v0.7.0 P1 过渡：`EXTRA_SUBSCRIPTIONS`（目前只有 on_stream_end 对账观察）先注册、
#: 但不参与 `7/7` 自检计数；P2a 合并进 SUBSCRIPTIONS/OBSERVED_HOOKS 后本集合消失。
_HOOK_EXTRA_NAMES = frozenset(
    name for name, _ in (getattr(_hooks, "EXTRA_SUBSCRIPTIONS", ()) or ()))

PLATFORM_NAME = "feishu"
LABEL = "Feishu / Lark — LarkDeck cards"
ADAPTER_CLASS_NAME = "LarkDeckFeishuAdapter"

#: 卡片按钮 value 里的动作键；只认自己这一个，其余一律回落给内置实现。
ACTION_KEY = "larkdeck_action"
ACTION_CLARIFY = "clarify"

#: 追踪中的卡片上限，防止长跑会话无限增长。
_MAX_TRACKED = 512

#: 为「中止重绘」保留正文的上限，**按 utf-8 字节**。
#:
#: ⚠️ 这里换过两次口径，每次都是被实测纠正的，记下来免得再想当然：
#:   1. 最初是「20000 **字符**、超了静默清空」⇒ 20500 个英文（20.5KB，卡片渲染正常）
#:      在 `/stop` 时**一次 patch 都不发**、零日志（第四路审计的阻断项）。
#:   2. 改成按字节、并写了句「超过卡片预算的正文本来就没卡了，所以是空集」——
#:      **这句是错的**（第六路审计用 `fit_reply_card` 实测证伪）：超 `CARD_BYTE_BUDGET`
#:      只是**我们自己**降载掉面板/页脚，**不是发不出去**；13300~20000 汉字（40~60KB）
#:      的卡照样发得出去（`--bytes` 实测：**128000 字节仍 `code=0`**，
#:      160000 才被拒：`230025 The length of the message content reaches its limit`）。
#:      也就是说「卡在、却存不下正文」的区间是**真实存在**的 —— 那种情况下中止时
#:      要么没有颜色、要么把正文抹掉，两者都不可接受。
#: 所以现在的口径是：**上限贴着飞书的硬上限**（128000 实测），留一点余量 ⇒
#: 「只要这张卡发得出去，它的正文就存得下」成立。内存靠 :data:`_MAX_TEXT_ENTRIES` 收。
#:
#: ⚠️ 第七路审计的阻断项：这里**曾经**还留着下面这句
#: ``_MAX_TRACKED_TEXT = _cards.CARD_BYTE_BUDGET``（第一版的老行，改造时忘了删）。
#: Python 后赋值覆盖前者 ⇒ 运行时实际是 40000，于是上面这整段修复**是死代码**：
#: 正文 40000~128000 字节、确实发得出去的卡，`/stop` 时一次 patch 都不发、不变色。
#: 更糟的是 `tests/test_units.py` 那条回归的 fixture 正好卡在 40000 上，所以它**当时的绿
#: 恰恰来自这行赋值**。教训：改常量口径必须 grep 全部同名赋值，且回归的 fixture
#: 要贴着**新**阈值写。现在只有一个定义处，`tests/test_units.py` 有一条形状断言守着。
#: 飞书卡片 JSON 的**实测硬上限** —— 单一事实来源在 `cards.py`（它决定 `status_shell`
#: 那一档要不要加），这里只引用，不再各写一份数字。
_FEISHU_CARD_BYTE_LIMIT = _cards.FEISHU_CARD_BYTE_LIMIT
#: 卡片 JSON 的固定开销（header/面板骨架/页脚…）实测 ≈426 字节，与正文长度无关。
#: 想保证「发得出去就存得下」，比的就是 `正文字节 + 固定开销 ≤ _FEISHU_CARD_BYTE_LIMIT`。
_CARD_BYTES_OVERHEAD = 1024
_MAX_TRACKED_TEXT = _FEISHU_CARD_BYTE_LIMIT - _CARD_BYTES_OVERHEAD

#: 最多让几个 chat 保留「中止重绘」用的正文（每个 ≤ :data:`_MAX_TRACKED_TEXT` 字节
#: ≈127KB）⇒ 内存上界 ≈2MB / 16 份。超出就清掉最久没用的那份（`last` 最小的）。
#: （口径按 utf-8 字节算；Python 的 str 每字符 2 字节，纯中文正文的实际堆占用比这个更大。）
_MAX_TEXT_ENTRIES = 16

#: 「明显没戏」的上界：超过它的正文连构造都不用试（一张卡绝不可能在 128000 字节内装下
#: 512KB 的正文）。**真正的判据是** :func:`_stop_redraw_would_paint` ——
#: `_MAX_TRACKED_TEXT` 只是它的**必要条件**，用它当充分条件会留下 86 字节宽的
#: 「留下了正文却画不上色」的带（第十路审计实测）。
_HOPELESS_BYTES = 512 * 1024

#: native 流式：并发回合上限 + 帧节流窗口（秒）。帧率过高会触发飞书限流，
#: 窗口内的中间帧直接跳过（返回 True 但不下发；下个 tick 文本变了会重试）。
#:
#: ``_STREAM_MIN_INTERVAL = 0.25`` **不是**瓶颈，所以别动它。2026-09-13 真机实测
#: （`tests/probe_render.py --rate-limit`，16 次连打一张探针卡）：
#:   * 单次 ``message.patch`` 的往返 **≈ 440~530ms**（偶发 1.5s）；
#:   * 16 次连打**没有一次被拒** —— 文档里的「单条消息 5 QPS」并不是硬拒绝。
#: 也就是说：**真正决定刷新密度的是 API 往返（≈0.5s/帧 ≈ 2 帧/秒）**，不是这个常量
#: （0.25s）。把它调小到 0 也不会更快 —— 串行 await 的往返摆在那里。
#: 想让观感更顺，唯一有效的杠杆是**客户端打字机**（``streaming_config``），
#: 它是客户端动画、与我们推帧的快慢无关；换 CardKit 传输（单卡 10 次/秒）也不能
#: 突破往返本身，收益有限而代价很大。
#: 硬调小的负面效果依旧成立：多出来的失败帧会走「一帧失败 → 本回合 native 被停用 →
#: 退化成多条纯文本」那条链（现在有 ``_TRANSIENT_BACKOFF`` 兜一层，但兜不等于鼓励）。
_MAX_STREAMS = 64
_STREAM_MIN_INTERVAL = 0.25

#: CardKit 的**写入预算**（`docs/plan-v1.md` 附录 B）：卡级上限按官方口径 **10 次/秒**，
#: 而帧节流窗口是 :data:`_STREAM_MIN_INTERVAL` ⇒ 每帧最多写
#: ``_CK_WRITES_PER_FRAME`` 次。**真实余量远大于此**（R0 真机实测 50 次/秒连打零失败），
#: 所以这个数不是「贴着上限走」，而是留了一倍以上的余量 —— 之所以还要这么省：
#: 一帧被拒（限流）就会让内核停用本回合的 native 流式 ⇒ 用户看到的不再是打字机。
#: 记账口径：一次 `card.batch_update`（承载面板 + 页脚）**算一次**，一次
#: `card_element.content`（正文）**算一次**（真机实测 batch 只占 1 个 sequence）。
#: ⚠️ 这是**逻辑写**的预算，不是 HTTP 调用数：撞限流时 `_ld_write_with_retry` 会把同一个请求
#: 重发（退避表 3 项 ⇒ 每次逻辑写最多 4 次尝试）⇒ **单帧最坏 = 2×4 + 预览 1 次 = 9 次调用 /
#: 退避睡眠 2.0s**（2026-09-14 更正：这里曾写「8 次 / ≈2.0s」= R7 之前的旧值，
#: 而 README/AGENTS 又写着把预览按 4 次算的「12 次」—— 两处都错，现已按 `retry=False` 统一）。
#: R7 的 **summary 进展**限频窗口（秒）：`card.settings` 会占**一个序号**、算**一次逻辑写**，
#: 所以绝不许每帧发（每帧 2 次已经是预算）。5 秒一次 ≈ 每 20 帧一次，平均远在预算内，
#: 峰值 3 次/帧也仍在卡级上限（10 次/秒）之内。它只影响**会话列表的预览文字**。
_CK_SUMMARY_INTERVAL = 5.0

_CK_WRITES_PER_SECOND = 10
_CK_WRITES_PER_FRAME = max(1, int(_CK_WRITES_PER_SECOND * _STREAM_MIN_INTERVAL))   # 0.25s ⇒ 2

#: **滑窗写入守卫**（R11-B1）的窗口长度（秒）—— 与「每卡 10 次/秒」同一时间单位。
_CK_WINDOW_SECONDS = 1.0
#: 窗口的容量上界（只关心最近 1 秒，多出来的条目没有任何用途）。
_CK_WINDOW_MAXLEN = 64


def _ck_window(state_ref: Dict[str, Any]) -> Deque[float]:
    """取（必要时建）**本回合**的写入滑窗（R11-B1）。

    ⚠️ **窗口挂在回合状态上，不是进程级共享盒子** —— 这条与 `docs/plan-r11.md` §3 里
    「共享盒子里的 `deque`」的原措辞**不同**，理由是实测出来的两条：
      * 飞书的口径是**每张卡** 10 次/秒 ⇒ 一个进程级窗口会把并发回合的写入**加在一起算**，
        两个长回合会让彼此的装饰互相饿死，而它们本来各有各的配额；
      * 进程级窗口还会让**测试按执行顺序漂移**：上一个用例的写入填满窗口之后，
        「这一帧写了几次 / 第 2 次写的是哪个元素」这类断言随顺序变化（实测：两条既有用例
        当场红，而它们与守卫毫无关系）—— 这正是 `docs/lessons.md` 推论 28 的形态
        （时间派生的行为必须能被冻结或隔离）。
      * A0 那条纪律（进程内全局必须与它的守卫/兄弟容器同源）在这里**自动满足**：它根本不是
        全局的，而是随回合状态显式传递的（回合状态本身有上界 `_MAX_STREAMS`）。
    ⚠️ 这个窗口**当前在稳态下不可达**，而且这句话有两个**前提**（B1 审计逐一实验验证过）：
       * 帧节流 `_STREAM_MIN_INTERVAL = 0.25` 是**常量**（按时间算 ⇒ 成功帧 ≤4 帧/秒）；
       * 核心在一帧 definitive `False` 之后会**停用本回合的 native**
         （`gateway/stream_consumer_transport.py` 的 `_native_push`）⇒ 「失败风暴里疯狂重试到
         顶满窗口」这条路径在真机上走不通。
    两个前提任一变了，守卫就会**复活**（审计实测：反事实地让核心继续发帧，1 秒内就能顶满、
    让出 68 次装饰）—— 所以它不是死代码，而是一条**有前提的**余量闸门。
    ⚠️ 另外**「≈8.2 次/秒」是摊还均值，不是「任何 1 秒窗口都 ≤10」**：正文与预览**不查
    allow**（提交点与限频预览都不跳），所以记账口径下 1 秒窗口内理论上最多能记到 **12** 条
    （第 10 条被放行 + 正文 + 预览）。那是有意的取舍；文档不许把 8.2 写成硬上界。
    它同时是 Phase C（运行时 `card_element.create` 会把滑窗顶到 10–14 次/秒）的前置之一，
    ⚠️ **但单独一件不够**（B1 审计中-3）：守卫每帧最多让出一次装饰，≥2 次 create/帧 时
    正文与 create 都不跳 ⇒ 还得补上 plan-v1 §193 的另一半「**含创建的帧不发装饰 batch**」。
    所以测试必须**构造**出窗口被顶满的情形（不然它就是一段永远不执行的代码，本项目对
    「恒假门禁 / 死代码」有前科）。
    """
    win = state_ref.get("ck_window")
    if not isinstance(win, deque):
        win = deque(maxlen=_CK_WINDOW_MAXLEN)
        state_ref["ck_window"] = win
    return win


def _ck_window_prune(state_ref: Dict[str, Any], now: float) -> Deque[float]:
    """丢掉窗口里过期（早于 ``now - _CK_WINDOW_SECONDS``）的时刻。"""
    win = _ck_window(state_ref)
    floor = now - _CK_WINDOW_SECONDS
    while win and win[0] <= floor:
        win.popleft()
    return win


def _ck_window_allow(state_ref: Dict[str, Any], now: float) -> bool:
    """这一秒里**还容得下**一次逻辑写吗（只看不记 —— 记账在真的发出去之后，R11-B1）。

    ⚠️ 「只看不记」是有意的：把记账放在这里就等于把「打算写」记成「写了」，
    与 `ck_decor` 那条纪律（记账只在真的成功之后）是同一条理由。
    """
    return len(_ck_window_prune(state_ref, now)) < _CK_WRITES_PER_SECOND


def _ck_window_note(state_ref: Dict[str, Any], now: float) -> None:
    """把一次**真的发出去了**的逻辑写记进滑窗（R11-B1）。

    口径（三条，缺一条这个守卫就变成装饰品）：
      * 记的是**配额消耗**，不是账本上的「成功写卡」：调用发出去了就计（含限流重试与失败响应）——
        与 `/larkdeck status` 的「只统计真的写出去的动作」**故意不同**，两者回答的问题不同
        （那边答「卡片有没有长」，这边答「这一秒还欠飞书多少配额」）。
        ⚠️ 两处如实说明（B1 审计低-3）：① `reqs is None`（本机没有 CardKit SDK）那一支**什么都没发**
        却仍会被上层记一笔 —— 现实里不可达（没有 SDK 时 seed 帧早就失败退出），口径是尽力而为；
        ② `_ld_write_with_retry` **抛异常**时（真机上连接重置的常见形状，请求可能已经消耗配额）
        不会记 —— 方向**保守**（宁可少算一次，也不虚报余量）；
      * 只记**元素通道**的三类逻辑写（装饰 batch / 正文 content / 预览 settings）；
        建实体不在窗口里（它发生在卡存在之前，且是一次性动作，不属于「每帧累积」的量）；
      * 它**不是**硬限速器：正文（提交点）与预览都不跳过（见 `_ld_ck_apply` 里那段说明），
        所以窗口仍可能被正文顶破 —— 那是有意的取舍（宁可多花一次配额，也不能让卡片停在半截）。
    """
    _ck_window_prune(state_ref, now).append(now)


#: **R4 卡链**：一张卡装到硬上限的这个比例就**封卡**、另开一张（超长回答不再掉成纯文本）。
#:
#: 为什么不是 1.0：封卡那一段还要多带一行「（续下一条）」，而**新卡要一次写完剩余的整段**
#: （帧文本是累积全文）⇒ 两边都得留余量。取 0.5 时，前半段与后半段都装得下。
#: ⚠️ 量的是**卡片正文的 JSON 字节**（`_card_body_bytes`，与两道墙同源）—— 拿原始 utf-8
#: 字节估会低估（`"` `\` `\n` 在 JSON 里翻倍，R1 审计实测过 127000 → 227291）。
_CK_SPLIT_SEAL_RATIO = 0.5
#: 上面那个比例换算成**字节阈值**（帧路径每帧都要比一次，别在热路径里重复算）
_CK_SPLIT_SEAL_AT = int(_cards.FEISHU_CARD_BYTE_LIMIT * _CK_SPLIT_SEAL_RATIO)
#: 切完之后**尾巴**最多能占硬上限的比例 —— 超了说明这一帧的增量太大、单张新卡装不下
#: ⇒ fail-open 交核心回落（宁可这一次掉成纯文本，也不发一张必被飞书拒的卡）。
_CK_SPLIT_TAIL_RATIO = 0.9


def _ck_split_point(text: str, offset: int, *, seal_budget: int,
                    tail_budget: int) -> Optional[int]:
    """给「累积全文」挑一个**封卡切点** ``cut``（``offset < cut <= len(text)``）；挑不到返回 None。

    三条判据（顺序即优先级）：
      1. ``text[offset:cut]`` 的卡片正文 JSON 字节 ≤ ``seal_budget``（旧卡装得下这一段）；
      2. ``text[cut:]`` 的字节 ≤ ``tail_budget``（**新卡这一帧就要把它整段写进去**）；
      3. 尽量落在**换行**上、且**不切在代码围栏/行内代码中间** —— 切在围栏里会让两张卡各自的
         markdown 残缺（前半段围栏没闭合、后半段凭空多出一段代码）。若可行区间整段都在代码区里，
         退到**围栏起点**（把代码块整块留给新卡）；连那也不行时退回 ``best`` —— **宁可让第一张卡
         的代码块残缺，也不 fail-open**：内容一个字节都不能丢，残缺只是观感问题。

    为什么用**二分**：``_card_body_bytes(text[offset:offset+n])`` 对 n **单调不减**（每个字符至少
    贡献 1 字节，JSON 转义只会加不会减）⇒ 二分找最大可行前缀是精确的，不必逐字符试（那是 O(n²)，
    几十万字的回答会卡住事件循环）。
    ⚠️ 判据 2 **只会随 cut 变小而变差**（尾巴变长）⇒ 若「最大的可行 cut」都装不下尾巴，就没有
    任何切点可行（这一帧的增量确实太大）⇒ 返回 None 让调用方 fail-open。
    ⚠️ 下标一律是**全文绝对下标**：``best`` 是相对 ``offset`` 的字符数，回溯找换行前要换算 ——
    第一版拿相对下标去索引全文，切过一次卡（``offset > 0``）之后就会看错位置。
    """
    span = len(text) - offset
    if span <= 0:
        return None
    lo, hi, best = 1, span, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if _card_body_bytes(text[offset:offset + mid]) <= seal_budget:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    if best <= 0:
        return None
    if best >= span:
        return None                       # 整段都装得下 ⇒ 没有可切的地方（调用方正常写卡）
    if _card_body_bytes(text[offset + best:]) > tail_budget:
        return None
    absolute = offset + best
    spans = _cards.code_spans(text)

    def _inside(index: int) -> bool:
        return any(start < index < end for start, end in spans)

    floor = absolute - max(1, int(best * 0.3))
    for index in range(absolute, floor, -1):
        if text[index - 1] != "\n" or _inside(index):
            continue
        return index                       # ① 换行 + 不在代码区：最理想的切点
    starts = [start for start, end in spans if start < absolute < end]
    if starts:
        candidate = min(starts)            # ② 整段都在代码区里 ⇒ 退到围栏起点，代码块整块给新卡
        if candidate > offset and _card_body_bytes(text[candidate:]) <= tail_budget:
            return candidate
    return absolute                        # ③ 兜底：仍在代码区里切（观感残缺 > 掉成纯文本）


#: 飞书的**瞬态**错误码 —— 命中就退避重试，而不是当成定义性失败。
#: 为什么这件事重要：一帧失败会被内核判成「本回合 native 不可用」
#: （``stream_consumer_transport.py`` 里定义性失败后置 ``_use_native_streaming = False``），
#: 之后本回合的输出改走 ``send()``，可能变成**多条纯文本消息**。而飞书的限流错误**不匹配**
#: 内核的 flood 启发式（错误被格式化成 ``[code] msg``，flood 只匹配
#: ``flood`` / ``retry after`` / ``rate``）⇒ 走硬失败分支、连自适应退避都不启动。
#: 所以退避得由我们自己在这一层做。
#: 码表（2026-09-13 审计更正，**以官方接口文档为准**）：
#:   * ``230020``   —— **`im.v1.message.patch` 官方错误码表里明写的频率限制**
#:     （"This operation triggers the frequency limit"）。这才是本路径最该兜住的那一个。
#:   * ``99991400`` —— 服务端**通用**错误码（不在 patch 的接口码表里）。飞书限频有时走
#:     网关层返回它，所以一并保留 —— 多一个码只会多一次幂等重试，代价可接受。
#:   * ``300309`` / ``300317`` —— **CardKit 专属码，当前路径上永远不会出现**（2026-09-13 真机实测：
#:     `im.v1.message.patch` **不是**飞书意义上的流式会话 —— 收尾帧之后再 patch 照样 `code=0`，
#:     所以这两个码在本插件的 patch 路径上没有触发条件）。它们的真实触发条件是
#:     「对 CardKit 卡片做完**整卡替换**（patch / card.update）后再写元素」⇒ `300309`，
#:     （2026-09-13 真机更正：**元素级**接口 `card_element.patch/create/update` 与
#:     `card.batch_update` 在流式期间可用、不关会话 —— 别再写成「任何结构性写入」）
#:     以及「用 settings 重开会话后序号没对齐」⇒ `300317`。
#:     放在这里是为将来可能的 CardKit 传输（见 docs/plan-6-effects.md 阶段 9）预先收口 ——
#:     多一个码只会多一次幂等重试，代价可接受。
_TRANSIENT_CODES = frozenset({230020, 99991400, 300309, 300317})

#: **撤回类**码（只可能在**整卡写入**时拿到 —— 元素写入对撤回无感，R0 的 P4 实测）：
#:   * ``230011``  —— `The message was withdrawn.`（真机实测：删掉消息后 `message.patch` 就报它）；
#:   * ``99992354`` —— 消息 id 不存在 / 非法（真机实测）。
#: 处置：标死 + 清追踪 + **绝不另建卡**（我们自己不补发；要不要补发由核心的 fail-open 决定）。
_WITHDRAWN_CODES = frozenset({230011, 99992354})

#: **卡级死法**（元素级写入拿到的、说明这张实体卡不能再走元素通道的码 ⇒ 转 patch 车道，
#: 用**同一张卡**继续）：
#:   * ``300309``  —— 流式会话已关闭（真机实测：整卡 patch / 显式关流式之后写元素就得它）；
#:   * ``300313``  —— 元素不存在（真机实测，msg 里点名 id）；
#:   * ``300317``  —— 序号冲突（跳号/撞号，真机实测）。
#: ⚠️ 转 patch 车道的**前提**是真机验过的：`message.patch` **能覆盖一张 CardKit 实体卡的消息**
#: （`probe_ck_stream_ops.py --lanes` 实测 `code=0`，且随后写元素得 `300309` ⇒ 会话确实被替换掉了）。
_CARD_DEATH_CODES = frozenset({300309, 300313, 300317})

#: **装饰通道**的卡级死法（比正文那组少一个 `300313`，差别是**有意的**，R5 审计高-1 的收口）：
#:   * `300309`（会话已关）/ `300317`（序号账本废了）⇒ 整条元素通道都不可用（正文那一次也一定会
#:     失败）⇒ **必须降级**，否则后续每帧都在往一个关掉的会话写正文，卡片静默冻住；
#:   * `300313`（元素不存在）⇒ **只是那一个装饰元素**在卡里没了（msg 会点名它），正文元素是另一个
#:     id、照样写得进去 ⇒ 只标死被点名的那个，**不降级**（保住打字机）。
_CARD_DEATH_DECOR_CODES = frozenset({300309, 300317})

#: ⚠️ **``200770`` 不属于上面任何一档**（2026-09-21 真机实测 + 探针复现，V4.1）：
#: ``code=200770 · msg='ErrMsg: this UUID has been recently consumed; '`` —— 含义是
#: **同一张卡上出现了两份 (seq, uuid) 相同、内容不同的写**。uuid 由 ``(card_id, seq)``
#: 推出（``ld-{card_id}-s{seq}``），所以这个码只可能来自**我们自己的并发算号**
#: （帧路径 + 工作心跳同时从一份 state 算 ``seq = _ck_seq + 1``）。
#: ⇒ 处置：**不降级**（卡是好的、正文通道不受影响）、按普通装饰失败留痕（带 msg）。
#: ⇒ 成因与收口见 `_ld_stream_frame_structured` 的 docstring（回合级写锁）。
#: 相关：两份写「同时在路上、序号先后到达」拿到的是 ``300317``（``sequence number
#: compare failed``），那个码**在** `_CARD_DEATH_DECOR_CODES` 里 ⇒ 同一把锁也是它的解药。

#: **写接口可以安全重试**的错误码：只有「服务端明确拒绝、什么都没执行」的频率限制类。
#: 为什么必须与 `_TRANSIENT_CODES` 分开：`300309`（流式会话已关闭）与 `300317`（序号不匹配）
#: 是**结构性状态**，把同一个请求原样重发**不可能成功** —— 重试只会白等 ~1 秒再 fail-open，
#: 所以它们属于「立刻回落」的那一类。
#: ⚠️ 上面那段注释写着这两个 CardKit 专属码是「为将来可能的 CardKit 传输预先收口」；
#: 2026-09-13 CardKit 成了**默认传输**，那个「将来」到了 —— 而元素写入路径此前
#: **一次都不重试**，等于那句收口一直是空的（第十二路审计）。
#: ⚠️ **容量到顶（`300305`/`300315`）也绝不在这张表里**（R11-B2）：它不是「服务端拒绝执行」，
#: 而是「这张卡装不下了」这种**确定性**状态，重发同一个请求必然同样失败。
_WRITE_RETRY_CODES = frozenset({230020, 99991400})

#: **元素容量到顶**的两个码（R11-B2）—— 处置是**确定性失败：不重试 + 留痕**。
#:   * ``300305`` —— 「element exceeds the limit」：建实体时**直接**回它（P7 实测，
#:     递归 200 收下、204 拒收）；
#:   * ``300315`` —— 运行时 ``card_element.create`` 被拒的**包装码**（B2 实测）：真原因
#:     在 msg 尾部的 ``code: NNNNNN`` 里（容量满是 `300305`，重复 id 是 `300301`）。
#:     ⇒ **只看外层码就会把「我们自己的 id 炒了」误判成「元素到顶」**，见
#:     `_CkResult.capacity_exceeded()`。
#: ⚠️ 正文列拿到它仍然是 **FATAL**（附录 A）：容量满意味着这张卡的正文元素写不进去，
#: 而卡片没有正文就没有意义 ⇒ fail-open 交核心回落。**不许**把它当成 `DEGRADE`
#: （`_CARD_DEATH_CODES`）—— patch 车道解决的是「会话被关 / 序号废了」，不是「这张卡满了」。
_CAPACITY_CODE = 300305
_CAPACITY_WRAPPER_CODE = 300315

#: 瞬态失败的退避间隔（秒）。**实测总代价约 1.0s**（0.101 + 0.302 + 0.602，四次调用）——
#: 核心的帧 pump 是串行 await，所以最坏情况就是「用户看到首字晚 1 秒」；`/stop` 路径上的
#: 重绘也走这个函数，同样最坏被挡 1s。审计判定这个代价可接受（不重试就是整回合掉 native，
#: 那比慢 1 秒糟得多），但**别再说「远小于内核容忍度」**：它就是 1 秒。
#: 取消语义实测正确：`asyncio.sleep` 期间 `task.cancel()` 会让 `CancelledError` 正常向外传播。
_TRANSIENT_BACKOFF = (0.1, 0.3, 0.6)

#: 流状态被判为「泄漏」的静默时长（秒）。必须取**小时级**：`last_at` 只在有正文帧时
#: 推进，一个长时间只跑工具的回合看起来会很陈旧，用分钟级阈值会把活跃回合踢掉、
#: 造成重复卡。这里只用来收「核心始终没发 finalize」的真正泄漏。
_STREAM_LEAK_SECONDS = 3600.0

#: 硬上限倍数：软上限只告警、不淘汰活跃流；到这个倍数才被迫淘汰（防内存）。
_STREAM_HARD_CAP_FACTOR = 4

#: ⚠️ 这里曾经有一个**猜**着做的「工具进度块不进正文 / 叙述归档」实现，**已作为安全修复移除**。
#:
#: 旧版用裸分隔符 ``"\n\n---\n"`` 判断核心有没有往帧里拼工具进度块，但核心的合成式是
#: ``gateway/stream_consumer.py`` 的
#: ``"\n\n---\n".join(p for p in (accumulated, progress) if p)`` —— **没有工具进度时，
#: 帧文本就是累积正文本身**。而 ``---`` 独占一行、前后空行，正是普通的 markdown 分隔线，
#: 模型随时会写。于是模型一写分隔线就被误判成进度块：归档点被推到分隔线处 → 之后每帧
#: 都算不出正文 → 卡片正文被刷空 → finalize 帧的 ``display or " "`` 让整条回答只剩一个空格。
#: 更致命的是核心侧对 finalize 是**乐观记账**（``stream_consumer_transport`` 按完整帧文本
#: 记录已送达，``delivered_final_matches`` 比对通过），核心认为送达成功、**不会再补发** ——
#: 这条回答就彻底没了，任何一层都不会报错。
#:
#: ⇒ 教训不是「不许剥」，而是「**判据必须有证据**」。R11-A7 重做了这件事，判据换成可以被
#: **证明**的形式（用户明确要求「一个核心配置都不动」⇒ 只能插件侧解决）：
#:
#:   * 核心那一段正文（``accumulated``）与我们从**公开钩子** ``on_stream_delta`` 收到的
#:     正文增量**同源**：``agent/stream_delivery.py`` 把**同一个 chunk** 既交给流式 consumers、
#:     又投给插件钩子队列（``_enqueue_stream_hook("on_stream_delta", delta=text, kind="text")``）；
#:   * 于是判据是：``帧文本 == 我们的累积全文 + "\n\n---\n" + 尾巴``
#:     ⇒ 那条尾巴**只可能**是核心叠加的工具进度块。
#:
#: 为什么这是**证明**而不是启发式：模型写下的每一个字节（**包括它自己写的 ``---``**）都在
#: 累积全文里，所以「累积之后的第一个字节是核心的分隔符」这件事，除了核心叠加没有别的来源。
#: 与旧版的关键差别：旧版把分隔符当**切点**（分隔符之前全算正文、之后全丢），新版把它当
#: **验证条件**（累积全文负责说「正文到哪为止」）。任何一个条件不成立就**原样渲染** ——
#: 代价只是这一次进度行可见（核心下一个正文增量到达时它也会自己清掉），绝不会吞正文。
_CORE_PROGRESS_SEP = "\n\n---\n"


#: Core appends its streaming cursor (default ``" ▉"``, ``gateway/config.py``) to the
#: whole composed frame.  It is not part of any progress line, so drop it before
#: shape matching or the last line never matches an exact tool-name suffix.
_CORE_PROGRESS_CURSOR = " ▉"

#: Core's ``_progress_emit`` de-duplicates consecutive identical lines by appending
#: ``" (×N)"`` to the **whole** line (``gateway/run_turn_runner.py``).
_CORE_PROGRESS_DEDUP_RE = re.compile(r"\s+\(×\d+\)\s*$")

#: ``{emoji} {rest}`` — the emoji must be a non-word symbol, otherwise the line is
#: ordinary prose/bullets (``1.``, ``-`` with no emoji token is rejected separately).
_CORE_PROGRESS_HEAD_RE = re.compile(r"^(\S+)\s+(.+)$")

#: Hermes 0.21.1 ``agent/display.py::_TOOL_VERBS`` (tool -> curated verb), in table
#: order.  It is kept as a **mapping** rather than a bare phrase list so a friendly
#: verb line is only accepted when the corresponding tool is present in the same
#: session's tool snapshot.  That is what makes ``⚙️ Reading hosts``
#: (``read_file`` preview is the basename, not a path) strippable while
#: ``📖 Reading list`` written by the model with no ``read_file`` running stays
#: fail-open (2026-09-18 audit A/F2).
_CORE_TOOL_VERBS: Dict[str, str] = {
    "web_search": "Searching the web",
    "web_extract": "Reading",
    "browser_navigate": "Browsing",
    "browser_click": "Clicking",
    "browser_type": "Typing",
    "read_file": "Reading",
    "write_file": "Writing",
    "patch": "Editing",
    "search_files": "Searching files",
    "terminal": "Running",
    "execute_code": "Running code",
    "image_generate": "Generating image",
    "video_generate": "Generating video",
    "text_to_speech": "Generating speech",
    "vision_analyze": "Looking at the image",
    "session_search": "Searching past sessions",
    "skill_view": "Reading skill",
    "skills_list": "Listing skills",
    "skill_manage": "Updating skill",
    "delegate_task": "Delegating",
    "cronjob_manage": "Scheduling",
    "clarify": "Asking",
    "memory": "Updating memory",
    "todo_list": "Updating tasks",
}
#: Distinct verb phrases in table order (same 23 values as core's table).
_CORE_PROGRESS_VERBS = tuple(dict.fromkeys(_CORE_TOOL_VERBS.values()))
#: Reverse index: verb phrase -> the tools whose curated label it is.
_VERB_TO_TOOLS: Dict[str, frozenset] = {
    phrase: frozenset(tool for tool, verb in _CORE_TOOL_VERBS.items() if verb == phrase)
    for phrase in _CORE_PROGRESS_VERBS
}

#: Core's long-running status overlay: ``⏳ Working — 9 min — iteration 29, …``.
_CORE_PROGRESS_STATUS = ("Working",)

#: Curated verbs whose connector is ``" for "`` (search-style phrasing).
_FOR_VERBS = frozenset({"Searching the web", "Searching files"})


#: 正文净化诊断日志的限流时间戳（每个 key 60 秒一条）。
#: 与项目其它诊断日志同纪律：F2 类形状持续 fail-open 时，长回合不能每帧打一条。
_BODY_DIAG_AT: Dict[str, float] = {}


def _log_body_diag_once(key: str, message: str, *args: Any) -> None:
    """正文净化诊断日志 60 秒一条（绝不让日志随帧数线性膨胀）。"""
    now = time.monotonic()
    if now - float(_BODY_DIAG_AT.get(key) or 0.0) < 60.0:
        return
    _BODY_DIAG_AT[key] = now
    logger.info(message, *args)


def _strip_core_progress_cursor(line: str) -> str:
    """Drop a trailing core cursor glyph from one line (shape check only)."""
    s = str(line or "")
    if s.endswith(_CORE_PROGRESS_CURSOR):
        return s[: -len(_CORE_PROGRESS_CURSOR)]
    return s.rstrip("\u2589\u2588\u258c\u2590")


def _is_core_progress_header(line: str, names: Any) -> bool:
    """One non-fence line: is it a core progress header rather than model prose?

    Recognised shapes (all produced by ``run_turn_runner._progress_build_message``):
      * ``{emoji} {running_tool_name}`` / ``…: "…"`` / ``…...`` / ``…(…)``;
      * ``{emoji} Searching the web for …`` — one of Core's curated verb phrases;
      * ``{emoji} custom_tool: "…"`` for a tool with no curated verb;
      * ``{emoji} Working — …`` — Core's long-running status overlay.

    The display token must start with a **non-ASCII** symbol: Core's emojis all do,
    while Markdown bullets/headings (``- item`` / ``# title`` / ``1. item``) start
    with ASCII punctuation or a digit and must stay fail-open.
    """
    s = _strip_core_progress_cursor(line).strip()
    if not s:
        return False
    s = _CORE_PROGRESS_DEDUP_RE.sub("", s).strip()
    match = _CORE_PROGRESS_HEAD_RE.match(s)
    if not match:
        return False
    head, rest = match.group(1), match.group(2).strip()
    # Emoji/pictograph only: category "S*" (So/Sk/…) rejects ASCII Markdown, CJK
    # prose (`执行 terminal: …`) and non-ASCII punctuation from being treated as core
    # overlays (2026-09-18 audit A/F4).  A custom ASCII tool emoji simply fails
    # open (progress stays visible), never swallows model text.
    if not head or not unicodedata.category(head[0]).startswith("S"):
        return False
    name_set = {str(name) for name in names if str(name)}
    for name in names:
        if rest == name or rest.startswith(name + ":") or rest.startswith(name + "..."):
            return True
        if rest.startswith(name + "("):
            return True
    for phrase in _CORE_PROGRESS_VERBS:
        # Cross-validate the friendly verb against the **same session's** tool
        # snapshot: ``Reading hosts`` strips only while ``read_file`` is present.
        if not (_VERB_TO_TOOLS.get(phrase, frozenset()) & name_set):
            continue
        if rest == phrase or rest.startswith(phrase + " ("):
            return True
        if phrase in _FOR_VERBS:
            # ``Searching the web for X`` — the connector is part of core's shape.
            if rest.startswith(phrase + " for "):
                return True
            continue
        if rest.startswith(phrase + " "):
            return True
    for phrase in _CORE_PROGRESS_STATUS:
        # Core's long-task line is ``⏳ Working — …``; plain ``Working on it``
        # (model prose with an emoji) must fail open.
        if rest.startswith(phrase + " —") or rest.startswith(phrase + " –"):
            return True
    return False


def _looks_like_core_progress_only(text: str, tools: Any = None,
                                  running: Any = None) -> bool:
    """True when an **empty-accumulated** frame is only core's tool progress block.

    Core's ``_compose_frame_content()`` joins ``(accumulated, progress)`` and drops
    empty parts.  With ``accumulated == ""`` the whole frame is the progress block,
    and that block is a **sequence** of lines: friendly verbs
    (``🔍 Searching the web for …`` / ``📄 Reading https://…``), generic lines
    (``⚙️ tool: "…"``), a status line (``⏳ Working — …``), and complete fenced
    terminal blocks (with or without the ``🖥 terminal`` header).  v0.6.1 only
    inspected the first line and therefore missed every multi-line frame — which is
    exactly the 2026-09-18 real-device screenshot.

    This remains a conservative shape check: every non-empty line must either be a
    progress header (tool-name / curated-verb / generic name shape) or belong to a
    closed fenced block.  ``tools`` must be non-empty and the frame must contain no
    ``_CORE_PROGRESS_SEP`` (a separator means real text is present and the normal
    prefix proof, not this helper, must decide).  Ambiguous prose fails open.

    A frame made of **bare fenced blocks only** (no explicit ``🖥 terminal`` header)
    is accepted only when ``running`` contains ``terminal``: model answers that start
    with a code block are otherwise indistinguishable from core's header-less
    consecutive terminal progress blocks.  Without that cross-check a single interim
    frame of the model's code would be blanked (finalize still never strips, so it is
    transient, but the user sees a flash).  ``running`` is normally the set of tool
    names whose panel status is still ``running``.
    """
    if not text or _CORE_PROGRESS_SEP in text:
        return False
    names = [str(item).strip() for item in (tools or []) if str(item).strip()]
    if not names:
        return False
    running_names = {str(item).strip() for item in (running or []) if str(item).strip()}
    lines = _strip_core_progress_cursor(text).splitlines()
    saw_progress = False
    saw_header = False
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith("```"):
            index += 1
            closed = False
            while index < len(lines):
                if lines[index].strip().startswith("```"):
                    closed = True
                    index += 1
                    break
                index += 1
            if not closed:
                return False
            saw_progress = True
            continue
        if not _is_core_progress_header(lines[index], names):
            return False
        saw_header = True
        saw_progress = True
        index += 1
    if not saw_progress:
        return False
    # Bare-fence-only frames need the running-terminal cross-check (see docstring).
    return saw_header or "terminal" in running_names


def _strip_core_progress(text: str, accumulated: str, tool_pending: bool,
                         complete: bool, *, finalize: bool,
                         tools: Any = None, running: Any = None) -> str:
    """剥掉核心叠加在**帧尾**的工具进度块；证明不了就原样返回（fail-open）。

    五个条件缺一不可，各自对应一类「不能剥」的情形（每条都有对应变异，撤掉必红）：

    0. **``not finalize``：收尾帧一律不剥。** 这条最强，因为它是**证明**而不是启发式：
       核心的收尾帧发的是**纯累积正文** —— `gateway/stream_consumer.py` 里
       `display_text = self._accumulated` 之后，**只有 ``tick.is_interim``** 才走
       ``_compose_frame_content()`` 把工具进度块合成进去（``:791-798``）；而 finalize 发送点里
       **除了一处例外**，传的都是纯累积全文（``:486``/``:747``/``:781`` 的切片/``:834``/``:856``/
       ``:862``/``:912``）。⚠️ **那一处例外是审计实测抓出来的（2026-09-15）**：
       ``gateway/stream_consumer_transport.py:391`` —— 某一帧**确定性失败**之后、关流之前，
       核心会用**同一个 text** 再发一帧 ``finalize=True``，而那个 text 正是 interim 帧的合成结果
       （累积 + 分隔符 + 进度行 + 光标）。**所以「所有 finalize 帧都是纯累积」是假的** ——
       这条注释曾经这么写，被审计当场推翻（它用真核心驱动抓到了
       ``frame[3] finalize=True text='第一段正文。\n\n---\n⚙️ …▌'``）。
       代价如实登记：在那条路上，条件 0 会把**本该剥的进度行与光标留在收尾正文里**。
       这是**有意取舍**：可见的进度行（丑，但一个字都没丢）vs. 不可逆地吞掉模型正文
       （静默、无法恢复）。要做对需要**形状判据**（尾巴逐行符合核心进度行形状
       ``{emoji} {tool_name}: "{preview}"``，tool_name 用我们 ``pre_tool_call`` 见过的名字比对），
       **未做 —— 登记为已知缺口**，见 ``docs/plan-v1.md`` 附录 F。
       ⇒ 在**其余** finalize 帧上，「累积之后还有内容」仍然只可能是模型自己写的
       （**含它自己写的 ``---``**），核心那一帧根本没有进度块可剥。
       为什么非加不可：我们的累积来自**钩子队列**（异步投递），收尾帧完全可能**早于**最后一个
       正文增量到达我们这里 ⇒ 累积是一个**陈旧的完整前缀**（``complete`` 仍为真），条件 1/3/4
       全部成立 ⇒ 旧版会把它当成核心进度剥掉；而核心对 finalize 是**乐观记账**
       （``_record_turn_final_payload`` / ``delivered_final_matches``）⇒ 它认为送达成功、
       **不会再补发**，用户看到的正文被静默砍掉一截，任何一层都不报错（变异 ``R11-9``）。
       ⚠️ **残留（如实登记）**：同样的滞后在**中间帧**上仍可能剥掉模型的续写，但那只是**一帧**
       —— 累积追上之后下一帧就把整段渲染出来（自愈）。收尾帧没有「下一帧」，所以只有它必须挡。

    1. ``tool_pending``：自上次正文增量以来**有过工具事件**。核心的 ``_tool_progress_lines``
       只在有工具进度时被 append、在下一个正文增量时被 clear；没有工具事件却去剥，
       等于把「模型自己写的内容」当成核心加的（变异 ``R11-2``）。
    2. ``complete``：我们的累积没被上限冻结。**残缺的累积仍然可能是帧文本的前缀**，
       拿它当证据就会把「冻结点之后、核心分隔符之前」的那段正文吞掉（变异 ``R11-4``）。
    3. ``accumulated`` 非空且是帧文本的前缀 —— 归属错、回合错、核心换了累积
       （``_adopt_final_text`` 用权威终稿替换、或流式被重试取代）都会在这里对不上。
       若累积为空（模型还没写正文），核心的合成式会**只返回进度块**；此时只有形状
       明确指向运行中工具（``🖥 terminal`` / 裸围栏 / ``{emoji} {tool_name}:``）才
       剥成占位，其余一律 fail-open（唯一代价是那一帧短暂可见，下一帧自愈）。
    4. 尾巴**以分隔符开头、且分隔符之后还有内容**：核心的合成式在 ``progress`` 为空时
       **不会**留下裸分隔符，所以裸分隔符只能是模型写的（那时它已在 ``accumulated`` 里，
       条件 3 就把它挡住了）。要求「还有内容」是给这条再加一道锁。

    ⚠️ **只可能剥掉后缀**，这条性质是 R4 卡链的前提：``ck_offset`` 之类的偏移量都指向
    正文内部，剥后缀不会让任何偏移失效（变异 ``R11-3`` 把判据换成「按最后一个分隔符切」，
    那会连正文一起切掉）。

    ⚠️⚠️ **上面那句的「理由」曾经写错了**（2026-09-16 第四轮审计实测指出，已更正）。
    旧写法把这条性质归因于「我们只做后缀剥离」—— 那是**同义反复**，不是保证：
    `return accumulated` 只有在 **`accumulated` 真的是 `text` 的前缀**时才等于「剥后缀」。
    真正的保证来自**条件 ③ + 条件 ④ 合起来**：
    ``tail = text[len(accumulated):]`` 且 ``tail.startswith(SEP)`` ⇒
    ``text[:len(accumulated)] == text[:text.index(SEP)] == accumulated`` ⇒
    **`accumulated` 必然是 `text` 的前缀**（不是猜的，是那两条推出来的）。
    而这条又依赖一个**外部前提**：我们的累积与核心的累积**逐字节同源**
    （实测出处：`agent/stream_delivery.py:314` 把**同一个** ``text`` 既投给钩子又累加；
    `gateway/stream_consumer.py:243` 的 ``_accumulated`` **从不含**进度行）。
    ⇒ **这个前提现在有判据守着了**（2026-09-16 补；此前确实只有上面这段推理）：
    `tests/check_hooks.py` 的**前提核对**那一格**驱动核心真实的投递链路**
    （`_fire_stream_delta` → 核心自己 docstring 写明的 `consumer.on_delta` → `_drain_queue`
    → `_filter_and_accumulate` → `_append_accumulated`），断言「钩子收到的累积」与
    「核心 `_accumulated`」**逐字节相同**（以及帧的形状与剥离回环）。
    它守的是**外部前提**，所以**上游改了那几个形状时它会红并打印原因** —— 那是故意的。
    另一半（我们自己的性质）由 `test_units` 那条「**display 恒为帧文本的前缀**」守（带构造自证）；
    变异 `PA-1`（入账的 delta 被 `.strip()`）与 `PA-2`（正文仓库的按会话分桶被拆掉）各钉一处。
    ⚠️ 仍未覆盖：`run()` 自己的分支与传输层、回合边界的 `_adopt_final_text`
    （核心可能把 `_accumulated` **整段换成**权威终稿 ⇒ 两边分叉；失败方向是 **fail-open**，
    只会「不剥」，不会吞正文）—— 登记在 `docs/plan-v1.md` 附录 F。
    **改这一段时，上面那两条判据一起看。**
    """
    if finalize or not text or not tool_pending or not complete:
        return text
    if not accumulated.strip():
        # No **real** answer text yet ⇒ core's composed frame is the progress block.
        # Usually the empty accumulated part is dropped and the frame has no separator,
        # but core can hold whitespace-only text (e.g. a leading "\n" delta that the
        # scrubber kept), and then its composer emits `"\n\n---\n" + progress` — the
        # exact real-device frame of 2026-09-18 15:17 (leading rule + search lines +
        # terminal block).  Normalise away that whitespace-only prefix before the shape
        # check; if the prefix contains any non-whitespace (real model text) we fall
        # through to fail-open and never cut it off.
        candidate = text
        at = text.find(_CORE_PROGRESS_SEP)
        if at >= 0 and not text[:at].strip():
            candidate = text[at + len(_CORE_PROGRESS_SEP):]
        # Empty accumulated/text still means no answer: fail-open.  A leading
        # separator with an empty tail is not evidence of progress by itself.
        if candidate and _looks_like_core_progress_only(candidate, tools, running):
            return ""
        return text
    if not text.startswith(accumulated):
        return text
    tail = text[len(accumulated):]
    if not tail.startswith(_CORE_PROGRESS_SEP) or len(tail) <= len(_CORE_PROGRESS_SEP):
        return text
    return accumulated

_DEFAULTS: Dict[str, Any] = {
    "cards": True,            # 用卡片渲染回复
    "native_streaming": True, # 官方 native streaming：一回合一张卡（工具进度合入同卡）
    "clarify_cards": True,    # 澄清使用交互卡
    # 澄清卡方言：1.0（按钮 + 顶层 value，真机已跑通，可作为回退）/ 2.0（下拉 + 输入框 +
    # 组件级 behaviors）。**默认 2.0，2026-09-13 翻的**，两条前提都留了证据：
    # ① 真机点击到达并解析出 clarify id（`探针点击到达 ✅ tag=select_static` +
    # `澄清提交未生效（clarify=probe-c2）`）；② `check_clarify_e2e.py` 的 2.0 场景全绿。
    # 想回旧路径配 `clarify_dialect: "1.0"`（那条路仍可用、仍有测试锁形状）。见 AGENTS.md 不变量 5。
    "clarify_dialect": "2.0",
    #: native 流式帧走哪条传输：``"cardkit"``（**默认**，真打字机）/ ``"patch"``（旧路径）。
    #: 2026-09-13 真机实测 + **用户肉眼判定**：普通卡 + `message.patch` 只是「几个字几个字」地跳，
    #: CardKit 实体 + `card_element.content` 才是一个字一个字往外冒 ⇒ 默认翻成 cardkit。
    #: 翻之前两条前提都满足：① 第十一路对抗审计无阻断（序号 / 失败语义 / 孤儿实体都过了一遍，
    #: 它指出的三条门禁缺口已补齐）；② 真机 `tests/probe_render.py --cardkit-prod` 走**生产路径**
    #: 全绿（建实体 1 次 + 发实体卡 1 次 + 元素写入 6 次 + patch 收尾 1 次，全 `code=0`）。
    #: 任何一步失败（建实体 / 写元素 / 拿不到 SDK / 超预算）都 fail-open 返回 False，由核心回落
    #: edit/send —— 那不变量 2 照旧：宁可有一次「没有动画的卡」，也绝不丢消息。
    #: 想退回旧路径：配 `native_transport: "patch"`。
    "native_transport": "cardkit",
    # 「处理中」表情反应：Hermes 会在用户消息上打一个 Typing 表情、处理完撤掉 ——
    # 在飞书上这就相当于「输入提示」。流式卡片本身已是即时反馈，aiduPOP 把「无输入提示」
    # 列进了即时响应的观感。**默认保持 Hermes 的行为**（true）：它自己也并没有真的关
    # （抑制 wrapper 被注释掉了），关掉纯属观感偏好 —— 想关就设 false。
    "reactions": True,
    # 客户端打字机的逐字间隔（毫秒）。**只对流式帧有意义**：流式模式下我们推全文、
    # 平台自己算增量逐字渲染。默认 15（与 aiduPOP、hermes-fry-cards 一致）；写 0 = 不带。
    "streaming_print_ms": _cards.DEFAULT_PRINT_FREQUENCY_MS,
    "unified_panel": True,    # 推理 + 工具合并为底部一个可折叠面板
    "panel_expanded": False,  # 面板默认收起（展开态很占屏；aiduPOP 同为默认收起）
    #: 核心的工具行要不要进**正文**。默认 False = **吃掉**：那些行（`⚙️ mem0_search: "…"`）
    #: 会被并进流式正文，而同一份信息我们已经在「执行详情」面板里结构化地给了一遍
    #: ⇒ 默认不重复、正文只有回答（用户 2026-09-13 明确要求）。想看核心那套就设 true。
    #: 页脚在上下文用量之外**还要显示哪些指标**（R7）：
    #: ``off``（默认，不改动现有观感）/ ``basic``（+ 缓存命中率 + 本回合 API 次数）/
    #: ``full``（再 + 首字节延迟 TTFB）。**缺数据就少一段，绝不编 0**（见 `cards.footer_line`）。
    "footer_metrics": "off",
    "progress_lines_in_body": False,
    # v0.7.0：own（默认，正文只认插件 on_stream_delta(kind="text") 累积；core 帧只作
    # 刷新信号 / finalize 兜底）。legacy 只保留给旧用例/回退，P2b 归档后删除。
    "body_source": "own",
    # v0.7.1 视觉层过渡键（V0 只登记与告警，行为在 V1–V4 逐步生效）：
    # legacy | structured；structured 引擎尚未实现时按 legacy 运行并留 WARNING。
    "visual_engine": "structured",
    # 卡片顶部状态条显隐；V2 实现前两种取值观感相同并留 WARNING。
    "card_status_header": True,
    # 是否展示推理正文；V3 实现前两种取值观感相同并留 WARNING（摘要行始终保留）。
    "show_reasoning": False,
    "footer": True,           # 页脚：状态 → 耗时 → 模型 → 上下文用量（+ 本卡短码）
    "show_model": True,       # 页脚里显示模型名（面板标题只放思考/工具摘要）
    "context_style": "text",  # 上下文用量样式：text（默认）| bar | both
    # P1b：CardKit 设备字号档位。off（不缩放）| mobile_friendly（PC 小、手机大，正文随设备）
    # | compact（默认：面板/脚注 12px notation，正文 14px normal）| large（整体放大）。
    # 只写 config.style.text_size 的 token 映射 + 给 markdown 元素加 text_size 引用；
    # 不改流式结构，不在流式中途做结构性 patch。2026-09-18：官方 markdown 文档确认
    # `notation` / `normal` 后，把 compact 从默认 off 翻成默认开启（用户要求看得见字号层级）。
    "text_profile": "compact",
    # P2：观感主题。neutral=原符号；ap_lite=抽象 emoji（用户选定默认）；ap_bubble=AP 泡波全量。
    "theme": "ap_lite",
    # 2026-09-17 D3：是否在 markdown 里使用 <font color>。2026-09-18 官方 Card 2.0
    # markdown 文档确认 `<font color='red'>…</font>` 与 14 色枚举（含 grey/green/red/
    # turquoise）后，默认翻成 true —— 用户真机截图里面板状态词/灰色细节必须真的有色。
    # 客户端不认时可在配置里显式关回 `panel_color_tags: false`（纯文本降级仍在）。
    "panel_color_tags": True,
    "model_aliases": "",      # 模型别名："真名=显示名, ..." 或 dict
    "max_reasoning_chars": _cards.MAX_REASONING_CHARS,
    "max_tool_result_chars": _cards.MAX_TOOL_RESULT_CHARS,
    "max_panel_steps": _cards.MAX_PANEL_STEPS,
    "context_max_override": 0,  # 非 0 时钉住上下文上限（自动探测不准时兜底）
}
_CONFIG: Dict[str, Any] = dict(_DEFAULTS)

#: 视觉层三键（V1–V4 已全部落地，不再是「登记未生效」）：改这几个键要**热重载**才换血，
#: 且 `visual_engine` 的 `legacy` 取值自 v0.7.1 起已退役（见 `_ld_visual_engine`）。
_VISUAL_TRANSITION_KEYS = frozenset({"visual_engine", "card_status_header", "show_reasoning"})

#: V2 Working 心跳：每回合至多一个任务，key 与 stream state 相同。
_LD_HEARTBEATS: Dict[str, "asyncio.Task[Any]"] = {}
_LD_HEARTBEAT_INTERVAL = 3.0



#: 启动自检结论，供日志 / doctor 查看。
SELFCHECK: Dict[str, Any] = {"ok": None, "detail": "not run"}

#: `PlatformEntry` 里**不允许**从内置 entry 透传的字段：身份与我们自己的工厂/探测
#: 必须由本插件给值，照抄会把「谁在提供这个平台」或「用哪个工厂」搞错。
#: 其余字段（见 `gateway/platform_registry.py` 的 dataclass）**全部**照抄 ——
#: `register_platform` 整条替换 entry，漏传一个就等于关掉一个能力（第十二路审计实测）。
_IDENTITY_ENTRY_FIELDS = frozenset({"name", "label", "adapter_factory", "check_fn",
                                     "source", "plugin_name"})

#: 钩子订阅结论：``{钩子名: 是否成功}``；空 dict 表示还没跑过 register()。
# ⚠️ R11-A0：**必须进程级共享** —— 两世代各记一份的话，`/larkdeck status` 与启动自检会按
# 「读到哪一代」给出两个不同的答案（钩子 7/7 还是 0/7、命令注册还是没注册），
# 而这两个答案都是**结论性**的、用户会拿去排障。
HOOKS: Dict[str, bool] = _panel.shared_box()["adapter_hooks"]

#: 插件命令名（`/larkdeck status`）与注册结论；`register()` 填 `COMMAND`（同上：进程级共享）。
LARKDECK_COMMAND = "larkdeck"
COMMAND: Dict[str, Any] = _panel.shared_box()["adapter_command"]

#: P1a：`compat.probe_report()` 的**只读快照**（进程级共享，避免世代裂脑）。
#: `build_adapter()` 在算出报告的同一处写入；`/larkdeck status` 只读它。
#: 绝不在命令路径上重新探测：命令可能来自旧世代，而探测读的是「当时接管的那一个基类」。
PROBE_REPORT: Dict[str, Any] = _panel.shared_box()["adapter_probe_report"]

#: P2：官方插件上下文的**只读句柄**（`ctx.get_config` 的绑定方法）。
#: 与 `PROBE_REPORT` 同一条纪律：register() 在最新世代写入，命令路径只从共享盒子取；
#: 命令处理器可能与注册不在同一个模块世代里，两代各存一份会读到不同的配置源。
#: ⚠️ 审计 security B1 后**不再保存 `set_config`**：聊天侧写入路径已移除（handler 拿不到
#: 发送者身份，进程级 env 开关无法安全授权）。写配置走官方 Hermes CLI/文件 + `config reload`。
PLUGIN_CTX: Dict[str, Any] = _panel.shared_box()["adapter_plugin_ctx"]

#: ``plugin.yaml`` 的位置与版本行。版本**每次现读**，不复制成常量 —— 常量会漂
#: （改了清单忘了改常量，卡片就会自信地报一个错的版本号）。
_PLUGIN_MANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugin.yaml")
_PLUGIN_VERSION_RE = re.compile(r"^version\s*:\s*([^\s#]+)", re.M)


def _apply_metrics_config() -> None:
    """把「别名 + 上下文上限覆盖」推给 :mod:`larkdeck.core.context`（幂等）。"""
    aliases = _cfg_raw("model_aliases")
    if isinstance(aliases, dict):
        _context.set_aliases(aliases)
    else:
        spec = str(aliases or "").strip()
        env = os.environ.get("LARKDECK_MODEL_ALIASES", "")
        _context.set_aliases({}, spec=f"{spec},{env}" if env else spec)
    pinned = _cfg_int("context_max_override", 0)
    # ⚠️ **无条件**推下去（pinned=0 ⇒ None=取消覆盖）。审计 A1 实测：只在 `if pinned`
    # 时调用，会让「官方删键 / 归零 reload」在内存里显示成功、运行时却仍钉着旧上限，
    # 直到重启进程 —— 正是「卡片不许撒谎」要消灭的形态。
    _context.set_context_override(pinned or 0)
    # D3 降级开关：配置关掉后所有 <font color> 渲染走纯文本；无条件推，reload 也生效。
    _cards.set_color_tags_enabled(bool(_cfg("panel_color_tags")))


def configure(**kwargs: Any) -> None:
    """运行时覆盖配置（未知键忽略，不抛）。"""
    for key, value in kwargs.items():
        if key in _DEFAULTS:
            _CONFIG[key] = value
    _apply_metrics_config()


def _remember_plugin_ctx(ctx: Any) -> None:
    """把官方插件上下文的**只读**配置方法存进进程级共享盒子（P2）。

    只拿 `get_config`，拿不到就记 ``None``；`set_config` 不再采集 —— 聊天侧写入路径已
    在审计（security B1）后整体移除：命令处理器只收 `raw_args`，拿不到发送者身份，
    进程级 env 开关无法把「写权限」绑定到操作者。写配置请走官方 Hermes CLI / 配置文件，
    再用 `/larkdeck config reload` 热刷新。
    """
    def _method(name: str) -> Any:
        try:
            value = getattr(ctx, name, None)
        except Exception:
            return None
        return value if callable(value) else None

    PLUGIN_CTX.clear()
    PLUGIN_CTX.update({"get_config": _method("get_config")})


def _apply_ctx_settings(ctx: Any) -> None:
    """从 Hermes 官方插件配置读本插件的设置。

    官方契约是 ``ctx.get_config(key, default)``，读的是
    ``plugins.entries.<plugin_id>.settings.<key>``（旧 ``config`` 子树为迁移兼容）。
    老版本 Hermes / 测试替身没有该方法时静默跳过，环境变量与默认值照旧生效。
    """
    get_config = getattr(ctx, "get_config", None)
    if not callable(get_config):
        return
    found: Dict[str, Any] = {}
    for key in _DEFAULTS:
        try:
            value = get_config(key, None)
        except Exception:
            continue
        if value is not None:
            found[key] = value
    if found:
        configure(**found)


def _cfg_raw(key: str, default: Any = None) -> Any:
    """取原始配置值（字符串/数字原样返回，供样式类开关用）。

    环境变量优先 —— 与 :func:`_cfg` 同一套优先级，只是不做布尔强转。
    """
    env = os.environ.get("LARKDECK_" + key.upper())
    if env is not None and env.strip() != "":
        return env.strip()
    return _CONFIG.get(key, _DEFAULTS.get(key, default))


def _cfg(key: str) -> bool:
    value = _cfg_raw(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(value)


#: v0.7.1 V0：未实现配置键的告警限流（5 分钟一条，避免刷日志）。
_VISUAL_WARN_AT: Dict[str, float] = {}


def _warn_visual_once(key: str, message: str) -> None:
    now = time.monotonic()
    if now - float(_VISUAL_WARN_AT.get(key) or 0.0) < 300.0:
        return
    _VISUAL_WARN_AT[key] = now
    logger.warning("[larkdeck] %s", message)


#: **仅供单测/探针**强迫某条车道（``"legacy"`` / ``"structured"`` / ``None`` = 生产行为）。
#: 生产不可达：`visual_engine` **配置**路径已在 v0.7.1 退役（plan §9.3），这里只是让
#: 「降级渲染器 + legacy 帧语义」仍能被门禁**直接**打到。
_LD_ENGINE_OVERRIDE: Optional[str] = None


def _ld_visual_engine() -> str:
    """**唯一引擎是 structured**（V4.7 起；`legacy` 配置路径已退役）。

    纪律（plan §9.3）：V4 删除 legacy **配置路径**、**保留降级渲染器** —— DEGRADE 车道由
    回合状态里的 `engine_stamp=degraded` 驱动（`_ld_stream_frame` 的 `engine_stamp` 判断），
    与配置无关，所以退役配置键不会动到那条安全网。

    `visual_engine=legacy` 现在只产生一条退休告警（限流一次），运行行为仍是 structured；
    真正要回退到旧引擎请 revert 到 v0.7.0 —— 这正是「配置路径删除、代码可回滚」的意思。
    """
    if _LD_ENGINE_OVERRIDE in ("legacy", "structured"):
        return _LD_ENGINE_OVERRIDE
    raw = str(_cfg_raw("visual_engine") or "structured").strip().lower()
    if raw == "legacy":
        _warn_visual_once("visual_engine-retired",
                          "visual_engine=legacy 已退役（v0.7.1 起唯一引擎是 structured，"
                          "降级渲染器仍由 DEGRADE 车道保留）；本进程按 structured 运行，"
                          "如需彻底回退请 revert 到 v0.7.0")
    elif raw != "structured":
        _warn_visual_once("visual_engine-invalid",
                          f"visual_engine={raw!r} 非法，按 structured 运行")
    return "structured"


def _ld_card_status_header_enabled() -> bool:
    """生产读取 ``card_status_header``；V2 实现前两种取值观感相同，非默认值告警。"""
    enabled = _cfg("card_status_header")
    if not enabled:
        _warn_visual_once("card_status_header",
                          "card_status_header=false 已记录；structured canary 下已隐藏状态条，"
                          "legacy 默认仍无 header")
    return enabled


def _ld_show_reasoning() -> bool:
    """生产读取 ``show_reasoning``；V3 实现前两种取值观感相同，非默认值告警。"""
    enabled = _cfg("show_reasoning")
    if enabled:
        _warn_visual_once("show_reasoning",
                          "show_reasoning=true 已记录；structured canary 下已控制推理正文，"
                          "legacy 默认仍按旧行为")
    return enabled


def _as_int(value: Any) -> Optional[int]:
    """把配置值归一成 ``int``；**不可用**（转不动 / ``nan`` / ``inf``）返回 ``None``。

    与 :func:`_cfg_int` 拆开是为了让调用方能**区分「没配」与「配了个不可用的值」** ——
    第八路审计实测：`_cfg_int` 会把坏值悄悄塌成默认，于是
    ``if value > high`` 那类范围检查永远见不到它们，写 ``streaming_print_ms: "fast"``
    与什么都不写完全无法区分（零日志）。

    ⚠️ 必须接住 ``OverflowError``：``int(float("inf"))`` 抛的是它。配置里写
    ``inf`` / ``1e999`` / ``.inf``（YAML 都合法）时，这个值会经 ``configure()``
    一路穿到 ``register()``，而那里没有任何 try —— 结果是插件注册整体失败、
    **静默退回纯文本**，正是本项目最怕的失败模式（已实测复现）。
    """
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    try:
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return None


def _cfg_int(key: str, default: int = 0) -> int:
    """取整数配置；坏值（含没配）一律退回 ``default``，**绝不抛**。"""
    value = _as_int(_cfg_raw(key))
    return default if value is None else value


def _print_frequency_ms() -> int:
    """``streaming_print_ms`` 的取值（客户端打字机的逐字间隔）。

    只做一件事：**越界不静默**。``cards.streaming_config()`` 会把越界值夹回默认
    （那层是纯函数、不许有 I/O），但用户写 ``streaming_print_ms: 5000``（想要「最慢」）
    时得到的是 15ms（视觉上等于没有动画）—— 第七路审计实测这时**零日志**，
    排查方向会被完全带反。所以这里按本项目对诊断日志的约定（限流 60 秒一条）如实报出来。
    """
    default = _cards.DEFAULT_PRINT_FREQUENCY_MS
    high = _cards.PRINT_FREQUENCY_MAX_MS
    raw = _cfg_raw("streaming_print_ms")
    value = _as_int(raw)
    if value is None and raw is not None:
        # 配了个**转不动**的值（"fast" / "" / [] / {} / inf / nan / 1e999）：
        # 与「什么都没配」必须能区分开，否则用户改了半天配置看不到任何反应
        _log_print_ms_once(f"{raw!r} 不是可用的数字", default)
        return default
    if value is None or value <= 0:
        # 没配 / 0 / 负数：0 = 关掉打字机（`cards.card()` 那边 `_positive` 判掉这个字段），
        # 是**正常取值**，不报警
        return default if value is None else value
    if value > high:
        # `5000` 的语义是用户想要「最慢」，实际会退回 15ms（视觉上等于没有动画）
        _log_print_ms_once(f"{raw!r} 超出 [1,{high}]ms", default)
        return default
    return value


def _log_print_ms_once(why: str, fallback: int) -> None:
    """`streaming_print_ms` 取值有问题的限流告警（60 秒一条，绝不静默）。"""
    now = time.monotonic()
    if now - getattr(_log_print_ms_once, "_at", 0.0) < 60.0:
        return
    _log_print_ms_once._at = now  # type: ignore[attr-defined]
    logger.warning("[larkdeck] streaming_print_ms 配置有问题：%s —— 已退回 %dms"
                   "（打字机逐字间隔；0 才是「关掉打字机」）", why, fallback)


def _card_body_bytes(body: str) -> int:
    """正文进卡片后的 **JSON 字节数**（= 判据口径，见 :data:`_MAX_TRACKED_TEXT`）。

    ⚠️ 为什么不能用 `len(body.encode())`：卡片 JSON 会把 ``\n`` ``"`` ``\\`` ``\t`` 转义成
    两字节，所以**JSON 口径 ≥ 原始口径**。第九路审计实测：两者混用会重新打开一条静默窗口 ——
    正文 126900 字节 + 300 个换行（原始口径"在阈值内"⇒ 正文被留下）时，那张带小面板的卡
    127750 字节**装得下**、patch 也确实发了、`code=0`，但**载荷里没有颜色**（守卫按 JSON 口径
    算出的 127202 + 壳 545 越过了 128000）⇒ 用户还是看不到中止色，唯一日志还不提颜色。
    判据与守卫必须同口径，这就是那个口径。
    """
    try:
        return len(json.dumps(str(body or ""), ensure_ascii=False).encode("utf-8", "ignore"))
    except Exception:                     # pragma: no cover - 防御性（str 不会失败）
        return len(str(body or "").encode("utf-8", "ignore"))


def _sanitize_for_send(text: str) -> str:
    """**要发出去的这一份**做 markdown 卫生 —— 并带一道**同口径**的字节闸门。

    两道判据合在一个函数里，理由（R6a 审计低-1/低-2）：

      * `sanitize_markdown` 对「标题密集」的正文**只会变长**（每个降级标题 +2 字节；只有删
        游离 `**` 才会 −2），于是存在一条窄带：**卫生前**的文本过了闸门、**卫生后**的那份
        超出上限。审计构造过 `raw 128000 字节 / 卫生后 128080 字节（40 个标题）`。
        收尾帧原本**一道闸门都没有**（`_ld_build_card` 刻意不截断正文），后果是发送失败 ⇒
        fail-open 到 `edit_message`/`send()`（同样是超限卡）⇒ 再失败 ⇒ 官方纯文本分块，
        用户从「一张卡」退化成「若干条纯文本」。
      * 闸门必须量**要发出去的那一份**（`docs/lessons.md` 推论 13 的口径病）：量的对象
        和发的对象不是同一份时，两个判据各自都对、合起来还是漏。

    超限时**退回原文**而不是丢弃卫生后的内容：原文就是上一帧用户已经看到的那份，
    它至少是「能发出去的」（不变量 2：宁可少一点格式，也不能丢消息）。
    """
    if not isinstance(text, str) or not text:
        return text
    clean = _cards.sanitize_markdown(text)
    if clean == text:
        return text
    size = _card_body_bytes(clean)
    if size > _cards.FEISHU_CARD_BYTE_LIMIT:
        _log_sanitize_reverted_once(size)
        return text
    return clean


def _log_sanitize_reverted_once(size: int) -> None:
    """卫生后的文本超上限、退回原文的限流告警（60 秒一条）—— 绝不静默。"""
    now = time.monotonic()
    if now - getattr(_log_sanitize_reverted_once, "_at", 0.0) < 60.0:
        return
    _log_sanitize_reverted_once._at = now  # type: ignore[attr-defined]
    logger.warning("[larkdeck] markdown 卫生后的正文 %d 字节超过飞书实测硬上限 %d —— "
                   "本次退回原文（少一点格式，不冒「卡发不出去、整条回答掉成纯文本」的风险）",
                   size, _cards.FEISHU_CARD_BYTE_LIMIT)


def _stop_redraw_would_paint(body: str, *, panel: Any = None,
                             footer: Any = None) -> bool:
    """**真正的判据**：留下这段正文之后，`/stop` 那张卡能不能既发得出去、又带上中止色。

    为什么不能只看「正文 JSON 字节 ≤ 阈值」（第十路审计实测出来的两条缝）：
    真实卡片的固定开销**随正文形状变化**（`config.summary` 会把正文 flatten 再截到 120 字），
    所以任何单一常量都只在一个方向上安全：

      * 阈值取得太保守 ⇒ 正文被判「存不下」丢掉，而那张卡其实**发得出去也画得上色**
        ⇒ `/stop` 一次 patch 都不发（这是 2026-09-13 我自己引入的一条窄回归：
        反斜杠 / `\t` 形状、~127KB 正文、窗口 50~284 字节）；
      * 阈值取得太宽 ⇒ 正文留下了、但那句话「装得下」是假的 ⇒ patch 发了、`code=0`、
        **载荷里没有颜色**（第九路 F1 的原始症状，纯 CJK 形状下有 67 字节的带）。

    与其调常量，不如**直接问真正的问题**：按 `_ld_redraw_one_stopped` 的同一条构造路径造出
    带状态小面板的卡，要求「字节数 ≤ 飞书实测硬上限」**且**「卡里真的有那个面板」。
    只在超出近似阈值这条**罕见**分支上调用，代价可以忽略。

    判不出来时（异常）返回 ``True``：**丢正文的代价比多留一份大**（丢 = 中止时不变色）。
    """
    try:
        # 优先用**真实卡的面板**（`_ld_redraw_one_stopped` 会带 `_ld_panel(chat)`）：
        # `fit_reply_card` 先受 40000 软预算约束，长正文 + 大面板会被降成 no-panel ⇒
        # 只按 status_shell 判会得出「留正文」而真实 `/stop` 卡没有面板/没有颜色
        # （2026-09-18 审计 A/F1）。panel=None 时才退回旧的状态 shell 兜底。
        shell = panel
        if shell is None:
            shell = _cards.status_shell(_cards.unified_panel(status=_panel.STATUS_STOPPED))
        if shell is None:                     # 连状态色都造不出来 ⇒ 没有「保色」这回事
            return True
        node, _tier = _cards.fit_reply_card(body, panel=shell, footer=footer)
        # 与 `_ld_build_card` **同一条构造路径**：字号档位也会加 `config.style.text_size`
        # 与元素 `text_size` 字段（默认 compact 下实测 +~300 字节）。漏掉这一步，
        # 判据会比真实 `/stop` 卡小一截 ⇒ 留下「发出去就超限」的正文。
        node = _cards.apply_text_profile(node, _cfg_raw("text_profile"))
        if _cards.card_bytes(node) > _cards.FEISHU_CARD_BYTE_LIMIT:
            return False
        return '"collapsible_panel"' in json.dumps(node, ensure_ascii=False)
    except Exception:
        logger.debug("[larkdeck] 中止重绘可行性判定异常，保守保留正文", exc_info=True)
        return True


#: CardKit 元素角色 —— 决定「这个元素写失败时这一帧怎么办」（处置矩阵见 docs/plan-v1.md 附录 A）。
#: R1 只把顺序与账本抽出来，**处置仍是老的**（正文/面板失败都 fail-open）；R2/R5 再按角色分档。
_CK_ROLE_ANSWER = "answer"      # 提交点：没有它这张卡就没有意义
_CK_ROLE_PANEL = "panel"        # 内容型装饰（面板正文）
_CK_ROLE_DECOR = "decor"        # 纯装饰（页脚 / 状态色 / summary），R2 起才会出现


def _log_ck_seq_invalid_once(raw: Any) -> None:
    """回合状态里的序号是坏值（字符串/负数/None 之外的怪东西）时限流告警。

    为什么必须留痕：坏值本身不会丢消息（外层 except 会兜住并让这一帧 fail-open），
    但「为什么这个回合掉成纯文本了」在真机上只有这一条线索。
    """
    now = time.monotonic()
    if now - getattr(_log_ck_seq_invalid_once, "_at", 0.0) < 60.0:
        return
    _log_ck_seq_invalid_once._at = now  # type: ignore[attr-defined]
    logger.warning("[larkdeck] 回合状态里的 ck_seq 不是可用的整数（%r）—— 按 0 处理；"
                   "这一帧可能因此撞号（服务端回 300317）", raw)


def _ck_seq(state: Dict[str, Any]) -> int:
    """回合状态里的 ``ck_seq`` → 可用的非负整数（**坏值归零 + 留痕，绝不抛**）。

    收口成一个函数而不是在五个调用点各写一遍 `int(state.get("ck_seq") or 0)`：散着写就是
    「同一件事多个口径」（推论 13），而且 `int("abc")` 会抛 —— 抛出去会被帧路径的外层
    except 兜住 ⇒ 整回合掉 native，用户只见「卡片怎么不变了」。R1 审计实测：把状态里的
    序号写成字符串，四门禁**全绿**（Y3）。
    """
    raw = state.get("ck_seq")
    if isinstance(raw, bool) or not isinstance(raw, int):
        if raw is not None:
            _log_ck_seq_invalid_once(raw)
        return 0
    if raw < 0:
        _log_ck_seq_invalid_once(raw)
        return 0
    return raw


def _ck_elems_from_card(card: Dict[str, Any]) -> List[str]:
    """从**真正建出来的那张卡**里抽出「可流式写的元素 id」——结构的**唯一来源**。

    为什么从卡 JSON 里抽，而不是「再读一遍配置算一遍」：`_ck_elems_for_entity()` 与
    `cards.cardkit_entity_card(panel=…)` 各自读一次 `_cfg("unified_panel")`（`_ld_panel_markdown`
    内部还有第三次），是**三个可以分叉的真相源**。R2 往结构里加页脚/状态元素时只要漏一处，
    症状就是「每帧去写一个卡里不存在的 id」⇒ `300313` 每帧失败 ⇒ 本回合掉 native +
    DM 里第二张卡（R1 审计的 U3）。从卡里抽，这种分叉**在构造上不可能发生**。
    """
    out: List[str] = []
    for rid in _cards.CARDKIT_STREAM_IDS:
        found = False
        stack: List[Any] = [card]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if node.get("element_id") == rid:
                    found = True
                    break
                stack.extend(node.values())
            elif isinstance(node, (list, tuple)):
                stack.extend(node)
        if found:
            out.append(rid)
    return out


class _CkResult(NamedTuple):
    """一次 CardKit 写的**结果三件套**：成功与否 + 返回码 + 原始 msg。

    为什么不再只返回 bool（R5）：处置**分档**需要码（`300309` 会话已关 ⇒ 转 patch 车道；
    `230020` 限流 ⇒ 重试），而「一批里哪个 id 是坏的」只能从 **msg** 里读出来 ——
    真机实测（`probe_ck_stream_ops.py --lanes`）：
    ``code=300313 · msg='ErrMsg: not find elementID : ghost_missing_element; '``
    ⇒ 服务端**会点名**坏 id。只看 bool 的话，「整批标死」是唯一能做的事（R2 审计第 3 条
    就是这么记的）；有了 msg 就能**只标坏的那个**，其余装饰下一帧照常写。
    """
    ok: bool
    code: int
    msg: str

    def bad_element_id(self) -> Optional[str]:
        """从 msg 里解析被服务端点名的坏 `element_id`（解析不出返回 ``None``）。

        ⚠️ 两道收紧（R5 审计的中-4，两个反例都是实测 msg 形状）：
          * **只在 `300313`（元素不存在）这个码上解析** —— 别的码的 msg 里出现 `elementID :`
            往往是在描述**上下文**（实测：`... elementID : panel_body content is too long,
            (the offending field is footer)` ⇒ 真凶是 footer，照解析会把好元素标死）；
          * **不截断 id**：id 字符集 `[A-Za-z0-9_]{1,20}` 是**我们自己建元素时**的规约，
            而服务端回显的可能是别人给的长 id（实测 21 字符被旧正则截成 20）⇒ 截断后匹配不上，
            调用方会「一个都不标死」而日志还说「只标死它」。所以这里贪婪匹配，
            **由调用方拿结果去和这一批的 op 对**（对不上就整批标死）。
        """
        if self.code != 300313:
            return None
        found = _CK_BAD_ELEMENT_RE.search(self.msg or "")
        return found.group(1) if found else None

    def inner_code(self) -> Optional[int]:
        """从 msg 里解析**内层码**（解析不出返回 ``None``，**绝不返回 0**）—— R11-B2。

        为什么需要它：`300315` 是运行时 `card_element.create` 被拒的**包装码**，同一个外层码
        下至少两种真原因（2026-09-15 真机实测，`probe_ck_stream_ops.py --capacity-codes`）：
        内层 `300305` = 元素数到顶、内层 `300301` = id 错（重复/格式）。不解析内层码就只能
        「按外层码一刀切」，而这两种病的修法完全相反。

        ⚠️ **绝不返回 0** 是硬纪律：`_ld_response_code(None)` 恰好就是 0，而 0 在飞书那侧是
        **成功**的语义 —— 一个「解析不出来」被读成「成功码」会把失败判成成功。本文件已经踩过
        同一个陷阱一次（`_ld_ck_settings` 里「老 SDK 缺模型时抛错而不是返回 None」那段）。
        所以「内层码真的是 0」与「解析不出」都返回 ``None``（0 不携带任何信息）。

        ⚠️ **已知风险面**（B2 审计低-1，**未经真机采样**，只记在这里不许当成实测）：
        正则只认「`code` 紧跟冒号」这一种标签形状，所以假想形状
        ``[limit, Code: 300305: exceeded]``（**描述码**后面紧贴冒号）也会被抓到。
        影响面是**有界的**：真机两条字面里权威内层码都在 msg 末尾，取最后一个匹配就是它
        （实测 `[... Code: 300305: x], code: 300301; ` ⇒ `300301`）；只有「msg 里根本没有
        尾部 `code:`」时才会读到描述码，而那时无论读成哪个内层码，**处置都是确定性失败**
        （不重试、留痕），差别只在 `_ck_reject_reason()` 那句原因怎么写。
        ⇒ 想收紧就得先拿到真机样本（附录 F 的规矩：不许猜），别照着假想形状改正则。
        """
        found = _CK_INNER_CODE_RE.findall(str(self.msg or ""))
        if not found:
            return None
        return int(found[-1]) or None

    def capacity_exceeded(self) -> bool:
        """这次写/建是不是**元素数到顶**（R11-B2）。

        判据必须是**两个码 + 内层码**，不能只看外层：
          * 外层 `300305` ⇒ 是（建实体时服务端直接回它，P7 实测）；
          * 外层 `300315` ⇒ **只在内层码也是 `300305` 时**才算（否则那是重复 id 等别的病）。
        处置（附录 A）：**确定性失败 —— 不重试、留痕**；正文列拿到它仍是 **FATAL**（fail-open）。
        """
        if self.code == _CAPACITY_CODE:
            return True
        return self.code == _CAPACITY_WRAPPER_CODE and self.inner_code() == _CAPACITY_CODE


#: 从 CardKit 的 msg 里抓坏元素 id 的正则。
#: ⚠️ **故意不写长度上界**：上界只对我们自己创建的 id 成立，对服务端回显的 id 不成立 ——
#: 截断会让「解析出来的 id」匹配不上任何 op，从而静默滑过标死那一步（R5 审计的中-4）。
_CK_BAD_ELEMENT_RE = re.compile(r"elementID\s*:\s*([A-Za-z0-9_]+)")

#: 从 CardKit 的 msg 里抓**内层码**的正则（R11-B2）。两个真机字面形状（2026-09-15 实测，
#: `probe_ck_stream_ops.py --capacity-codes`，两条臂各跑过两遍、字面一致）：
#:
#:   * 运行时 `create` 撞 200 墙（`append(panel)` 与 `insert_after(answer)` **同形**）::
#:
#:       code=300315 · msg='ErrMsg: msg: [element exceeds the limit], code: 300305; '
#:
#:   * 运行时 `create` 复用卡里已存在的 id（`panel_body`）::
#:
#:       code=300315 · msg='ErrMsg: msg: [ElementID panel_body: Code 1001: Duplicate ID], code: 300301; '
#:
#: 两条由此**确定**的结论（都是从这两条字面读出来的，不是推断；第 ③ 条是**已知风险面**，
#: 不是实测结论 —— 审计构造出 `[limit, Code: 300305: exceeded]` 这种形状会被同一正则吃掉，
#: 真机没有这个样本，所以只记录、不照着改）：
#:   ① `300315` 是**包装码**，真原因在 msg **尾部**的 `code: NNNNNN` ⇒ 所以取**最后一个**匹配；
#:   ② 方括号里的 `Code 1001` 是**描述码**（重复 id），它**不是**尾部的 `300301` ——
#:      正则 `\bcode\s*:` 恰好只吃尾部那一个（`Code 1001:` 的数字在冒号**前面**，形状不同）；
#:   ③ `\b` 是**有意的**：`card.create` 的 `230099` 里内层码写作 `ext=ErrCode: 11310;`
#:      （见 `cards.py`），`\b` 把这种**前缀粘连**挡在外面 —— `ErrCode` 属于另一套码空间，
#:      混进来会让「内层码」有两种口径。
_CK_INNER_CODE_RE = re.compile(r"\bcode\s*:\s*(\d+)", re.IGNORECASE)


class _CkOp(NamedTuple):
    """一次要发给飞书的元素写入。

    ``element_id`` 必须是**建实体时就存在**的那个（写不存在的 id 得 ``300313``，见
    ``docs/plan-v1.md`` 的 R0 结论）。``role`` 决定失败语义，不是装饰性字段。

    ``code`` 是**失败时**由 :meth:`_ld_ck_apply` 回填的服务端返回码（默认 ``None``）。
    ⚠️ 它是为了修 P1a 的类型错配：调用方原先把失败 op 当 ``_CkResult`` 调
    ``inner_code()`` ⇒ 写正文失败时抛 ``AttributeError``，具体元素与返回码一起丢。
    现在失败 op 通过 ``_replace(code=...)`` 带回返回码；成功/未写出时保持 ``None``。
    """

    element_id: str
    content: str
    role: str
    code: Optional[int] = None

    def fail_reason(self) -> str:
        """失败文案**必须点名角色**：三条文案互不相同，且各自带自己的 element_id。

        为什么强调：`_ld_stream_fail` 的告警是**进程级 30 秒限流** ⇒ 一帧里第二个失败的
        元素终生不留痕。文案里带 id 是唯一能区分「到底哪个元素死了」的线索（R1 审计 U4）。
        """
        what = {_CK_ROLE_ANSWER: "正文", _CK_ROLE_PANEL: "面板"}.get(self.role, "元素")
        return f"CardKit 写{what}元素失败（{self.element_id}）"


def _ck_plan(display: str, panel_text: str, elems: Sequence[str],
             footer_text: Optional[str] = None,
             panel_tools_text: str = "",
             streaming: bool = False) -> List[_CkOp]:
    """这一帧要写的元素列表（**按发送顺序**）。

    结构的唯一事实来源是 ``elems``（建实体时定下来的那份，之后只读）——所以「卡里没有的元素
    一个都不写」这件事是**由数据决定**的，不靠调用点上的 if。纯函数：单测可以直接锁
    「哪些元素、什么顺序、什么内容」，不必跑整条帧路径。

    R3 收窄版：面板是**两个**元素 —— ``panel_body``（推理块）与 ``panel_tools``（工具块），
    两者顺序与 `cards.panel_markdown` 的拼接顺序一致（先推理、后工具）。它们都进**同一个**
    装饰 batch（`_ck_split` 按 role 分流）⇒ 逻辑写次数一次都不增加。
    """
    ops: List[_CkOp] = []
    # 装饰先写、**正文最后写**（提交点在后）：上游按「最后一次**成功**发出的帧文本」记账
    # （`stream_consumer_fallback._visible_prefix`），正文最后落盘才能让「这一帧失败」
    # 等价于「正文没更新」——否则卡上已经有这一帧的正文，而核心以为可见前缀还停在前一帧，
    # 回落补发的尾部会把同一段话再说一遍（R1 审计的第③条，也是 `docs/plan-v1.md` 的 R1 交付项）。
    if _cards.CARDKIT_PANEL_BODY_ID in elems:
        ops.append(_CkOp(_cards.CARDKIT_PANEL_BODY_ID, panel_text or " ", _CK_ROLE_PANEL))
    if _cards.CARDKIT_PANEL_TOOLS_ID in elems:
        ops.append(_CkOp(_cards.CARDKIT_PANEL_TOOLS_ID, panel_tools_text or " ", _CK_ROLE_PANEL))
    if _cards.CARDKIT_FOOTER_ID in elems:
        # 页脚是**纯装饰**：钩子还没数据时 `footer_text` 是 None ⇒ 写空格占位
        # （元素建出来就必须有内容；空串在飞书那边有历史坑）。
        ops.append(_CkOp(_cards.CARDKIT_FOOTER_ID, footer_text or " ", _CK_ROLE_DECOR))
    if _cards.CARDKIT_ANSWER_ID in elems:
        # 流式中间帧没有正文时保留「正在生成…」占位，而不是写一个空格：
        # 空正文 + 折叠面板在真机上看像一张坏卡（2026-09-18 用户复测反馈）。
        # 收尾帧（streaming=False）仍写空格 —— 真的没有正文的回合不能永远停在占位。
        answer_text = _cards.answer_or_pending(display, streaming) or " "
        ops.append(_CkOp(_cards.CARDKIT_ANSWER_ID, answer_text, _CK_ROLE_ANSWER))
    return ops


def _ck_split(ops: Sequence["_CkOp"]) -> Tuple[List["_CkOp"], Optional["_CkOp"]]:
    """把一帧的 ops 拆成「一次 batch 里发的装饰」+「单独发的正文」。

    为什么这么拆（写入预算，R0 实测）：卡级写入上限按官方口径 10 次/秒，而帧节流窗口是
    0.25s ⇒ **每帧最多 2 次写**。装饰（面板 + 页脚）用**一次 `card.batch_update`** 承载，
    正文单独发一次 `card_element.content`（打字机只认这个通道的累进写入），正好 2 次。
    """
    decor = [op for op in ops if op.role != _CK_ROLE_ANSWER]
    answer = next((op for op in ops if op.role == _CK_ROLE_ANSWER), None)
    return decor, answer


def _log_ck_panel_write_failed_once() -> None:
    """CardKit 写**面板**元素失败的限流告警（60 秒一条）。

    为什么单列：正文元素先写成功了，这一条失败会让卡片停在「正文新、面板旧」的半更新态，
    而函数返回值是唯一判据 —— 没有日志的话，这种卡在排查时完全无迹可寻。
    """
    now = time.monotonic()
    if now - getattr(_log_ck_panel_write_failed_once, "_at", 0.0) < 60.0:
        return
    _log_ck_panel_write_failed_once._at = now  # type: ignore[attr-defined]
    logger.warning("[larkdeck] CardKit 面板元素写入失败（正文已写成功）—— 这张卡会停在"
                   "「正文新、面板旧」的半更新态；本帧按失败处理，交核心回落")


def _log_ck_decor_write_failed_once(ops: Sequence["_CkOp"], code: int = 0,
                                    blamed: Optional[str] = None, msg: str = "") -> None:
    """装饰元素（面板/页脚）写失败的限流告警（60 秒一条）。

    为什么单列一条而不是并进面板那条：装饰失败**不 fail-open**（见 `_ld_ck_apply`），
    所以卡片会继续逐字长大、只是那一段装饰冻结 —— 这种「看起来正常但其实坏了」的形态
    必须留痕，否则真机上完全无迹可寻。

    ⚠️ **必须带上服务端的 ``msg``**（V4.1）：真机 2026-09-21 那次只留了 ``code=200770``，
    而这个码不在任何已知码表里 —— 真正的原因（``this UUID has been recently consumed``）
    只在 ``msg`` 里。码是分类、msg 才是事实，排查时少一个就得多跑一次真机。
    """
    now = time.monotonic()
    if now - getattr(_log_ck_decor_write_failed_once, "_at", 0.0) < 60.0:
        return
    _log_ck_decor_write_failed_once._at = now  # type: ignore[attr-defined]
    ids = "、".join(op.element_id for op in ops)
    scope = (f"服务端点名的坏元素是 {blamed} ⇒ 只标死它" if blamed
             else "返回码没点名坏元素 ⇒ 整批标死（解析不出来不等于没坏）")
    logger.warning("[larkdeck] CardKit 装饰写入失败 code=%s（%s）msg=%r —— 本帧继续、"
                   "正文不受影响；标死：%s；%s；这些元素本回合内不再尝试，"
                   "收尾帧的整卡 patch 会补齐",
                   code, ids, str(msg or ""), scope,
                   "卡片会停在「正文在长、装饰冻结」的状态")


def _ck_create_wall(card: Mapping[str, Any]) -> Optional[str]:
    """建 CardKit 实体**之前**要过的两道墙：返回拒绝原因（``"字节"`` / ``"元素"``）或 ``None``。

    为什么两道都要（R2 补的，审计缺口）：patch 路径超预算会**分级丢装饰**（面板 → 页脚 → 裸卡），
    而 cardkit 的结构**建实体时定死、之后改不了** ⇒ 超了就是整张卡被飞书拒（`230099`/
    `300305`），这一帧什么都没有。两道墙的判据都必须是**飞书那侧的口径**：
      * 字节：``cards.card_bytes``（JSON 转义后的字节，见推论 13 的口径病）；
      * 元素：``cards.count_elements``（**递归**口径 —— 真机实测服务端就是数递归总数：
        递归 200 收下、204 拒收，码 `300305`；只数顶层会让「面板里塞了 200 个子元素」这种
        形状从闸门底下溜过去）。
    ⚠️ 这道墙**现在永远不会响**：实体卡的元素数仍然是**结构定死的 5 个**（正文 + 面板 +
    面板里**两个** markdown（推理块与工具块，R3 收窄版）+ 页脚）。留着它是**契约**：
    R3 完整版（每工具一行、运行时 `card_element.create`）会让元素数变成动态的，那时墙必须
    已经在位（`tests/test_units.py` 直接拿合成长卡验它，不依赖它今天会响）。
    """
    if _cards.card_bytes(card) > _cards.FEISHU_CARD_BYTE_LIMIT:
        return "字节"
    if _cards.count_elements(card) > _cards.FEISHU_ELEMENT_LIMIT:
        return "元素"
    return None


def _log_ck_over_budget_once(size: int) -> None:
    """CardKit 实体卡超过飞书硬上限的限流告警（60 秒一条）。

    cardkit 的结构建实体时定死、之后不能改 ⇒ 超预算就是整卡被拒（而不是像 patch 路径
    那样「优雅丢掉面板」）。这条日志是排查「这一帧怎么什么都没有」的唯一线索。
    """
    now = time.monotonic()
    if now - getattr(_log_ck_over_budget_once, "_at", 0.0) < 60.0:
        return
    _log_ck_over_budget_once._at = now  # type: ignore[attr-defined]
    logger.warning("[larkdeck] CardKit 实体卡 %d 字节超过飞书实测硬上限 %d —— "
                   "不能像 patch 路径那样分级丢装饰（结构已定死），本帧 fail-open 交核心回落",
                   size, _cards.FEISHU_CARD_BYTE_LIMIT)


def _ck_reject_reason(res: "_CkResult") -> str:
    """一次 CardKit 写/建被拒的**人话原因**（按**内层码**分档，R11-B2）。

    为什么要有这个纯函数：`300315` 是**包装码**，同一个外层码下至少两种真原因（真机实测）——
    内层 `300305` = 元素数到顶、内层 `300301` = id 错（重复/格式）。两者的修法**相反**：
    前者是容量（要提前算预算），后者是**我们自己的 bug**（id 重了）。而这两种在旧代码里
    都会塌成同一句「CardKit 建实体/发实体卡失败」—— 故障归因就此消失。
    """
    if res.capacity_exceeded():
        return "元素数到顶（200 是递归口径的硬墙）"
    inner = res.inner_code()
    if inner == 300301:
        return "元素 id 有问题（重复或格式非法）"
    if inner is not None:
        return f"内层码 {inner}"
    return "内层码解析不出（msg 里没有可认的 `code:` 标签）"


def _log_ck_window_skip_once(elems: Sequence[str], used: int) -> None:
    """滑窗守卫跳过装饰批量时的限流 WARNING（60 秒一条）—— **绝不静默**（R11-B1）。

    为什么必须有：这一帧的**装饰**（面板/页脚）没有更新，而帧照旧返回成功 ——
    「看起来正常、其实少写了一块」正是本项目的头号失败形态。文案必须说清三件事：
    跳过了什么、窗口里已经有多少次写、以及**这些元素没有被记账**（下一帧会补写）。
    """
    now = time.monotonic()
    if now - getattr(_log_ck_window_skip_once, "_at", 0.0) < 60.0:
        return
    _log_ck_window_skip_once._at = now  # type: ignore[attr-defined]
    ids = "、".join(elems)
    logger.warning("[larkdeck] 滑窗写入守卫：本帧装饰批量已让出（1 秒窗口内已写 %d 次 / 上限 %d）"
                   "—— 跳过的元素 %s **没有被记账**，下一帧会补写；正文不受影响、本帧照旧算成功",
                   used, _CK_WRITES_PER_SECOND, ids)


def _log_ck_reject_once(res: "_CkResult", where: str) -> None:
    """CardKit 写/建被拒的限流告警（60 秒一条）—— **确定性失败必须留痕**（R11-B2）。

    为什么单列一条：`_ld_ck_create` 失败以前是**静默 return None**（帧路径只说一句
    「CardKit 建实体/发实体卡失败」），而「容量满」与「id 错」是两种完全不同的病：
    本地闸门 `_ck_create_wall` 用的是**本地**递归计数，一旦它与服务端口径漂移
    （Phase C 的动态元素正是这种情形），唯一能留下凭据的就是这一行。
    """
    now = time.monotonic()
    if now - getattr(_log_ck_reject_once, "_at", 0.0) < 60.0:
        return
    _log_ck_reject_once._at = now  # type: ignore[attr-defined]
    logger.warning("[larkdeck] CardKit %s 被服务端拒：code=%s · 原因=%s —— "
                   "**确定性失败：不重试**（原样重发必然同样失败），本帧交核心回落（fail-open）；"
                   "原始 msg=%s", where, res.code, _ck_reject_reason(res), (res.msg or "")[:160])


def _log_ck_summary_failed_once(code: int) -> None:
    """`card.settings`（会话列表预览）写失败的限流告警（60 秒一条）——绝不静默。

    它坏了**不影响卡片内容**（只是列表里那行预览停留在旧文字），所以按 `DEAD` 处理：
    本回合不再试，而且必须留痕 —— 「看起来一切正常、其实某一项悄悄失效」是本项目的头号形态。
    """
    now = time.monotonic()
    if now - getattr(_log_ck_summary_failed_once, "_at", 0.0) < 60.0:
        return
    _log_ck_summary_failed_once._at = now  # type: ignore[attr-defined]
    logger.warning("[larkdeck] CardKit 会话预览（summary）写入失败 code=%s ⇒ 本回合不再尝试；"
                   "卡片内容不受影响，只是会话列表里那行预览停在旧文字", code)


def _log_ck_degrade_once(code: int) -> None:
    """「卡级死法 ⇒ 转 patch 车道」的限流告警（60 秒一条）——绝不静默。

    为什么必须留痕：降级之后**用户看到的东西不变**（还是同一张卡），只是不再逐字 ——
    这正是本项目最怕的那类「看起来正常、其实换了实现」的形态。这条日志是唯一的线索。
    """
    now = time.monotonic()
    if now - getattr(_log_ck_degrade_once, "_at", 0.0) < 60.0:
        return
    _log_ck_degrade_once._at = now  # type: ignore[attr-defined]
    logger.warning("[larkdeck] CardKit 元素通道拿到卡级死法（code=%s）⇒ 本回合**降级为整卡 patch**"
                   "续写同一张卡（不另建卡、不丢消息），代价是没有逐字打字机", code)


def _log_withdrawn_once(code: int) -> None:
    """「这张卡被撤回/删了」的限流告警（60 秒一条）——绝不静默。

    R0 的 P4 实测：元素写入对撤回**无感**（删掉消息后写元素照样 `code=0`），**只有整卡写入**
    会报 `230011`。所以这条守卫只可能在整卡写入路径上生效 —— 留痕是为了说明
    「卡片不见了不是我们丢消息，是这条消息被撤回了」。
    """
    now = time.monotonic()
    if now - getattr(_log_withdrawn_once, "_at", 0.0) < 60.0:
        return
    _log_withdrawn_once._at = now  # type: ignore[attr-defined]
    logger.warning("[larkdeck] 这张卡的消息已被撤回/删除（code=%s）⇒ 标死它并清掉追踪，"
                   "**不再往这条 message_id 写**（要不要重新发一条由核心的回落决定）", code)


def _log_ck_elements_over_once(count: int) -> None:
    """CardKit 实体卡元素数超过飞书硬上限的限流告警（60 秒一条）。

    与字节那条**分开**：两者的修法完全不同（字节 ⇒ 少装内容；元素 ⇒ 少建结构），
    合一条日志会让「到底撞了哪道墙」看不出来。
    """
    now = time.monotonic()
    if now - getattr(_log_ck_elements_over_once, "_at", 0.0) < 60.0:
        return
    _log_ck_elements_over_once._at = now  # type: ignore[attr-defined]
    logger.warning("[larkdeck] CardKit 实体卡 %d 个元素超过飞书实测硬上限 %d（**递归**口径）"
                   "—— 结构建实体时定死、之后改不了，本帧 fail-open 交核心回落",
                   count, _cards.FEISHU_ELEMENT_LIMIT)


def _log_no_colour_once() -> None:
    """「中止重绘这次没能带上状态色」的限流告警（60 秒一条）——绝不静默。"""
    now = time.monotonic()
    if now - getattr(_log_no_colour_once, "_at", 0.0) < 60.0:
        return
    _log_no_colour_once._at = now  # type: ignore[attr-defined]
    logger.warning("[larkdeck] 中止重绘**没有带上状态色**：正文大到让状态小面板装不下了"
                   "（那张卡贴着飞书卡片字节硬上限）—— 卡片内容仍会更新，只是边框不变色")


def _log_note_text_skipped(size: int, raw_size: int) -> None:
    """正文太大、无法为「中止重绘」保留 —— 限流告警（60 秒一条），绝不静默。

    ``size`` 是**判据口径**（JSON 转义后字节，见 :func:`_card_body_bytes`），
    ``raw_size`` 是原始 utf-8 字节。**两个数都要打**：第七路审计抓到过这里传的是 `len(body)`
    （字符数），打出「正文 14000 字符超过 40000 字节预算」这种像阈值算错了的日志；
    第九路审计又指出换口径后只打一个数会让「原始 126900 却报 127202」看着像 bug。
    这条日志是「卡片为什么不变色」的唯一线索，口径必须自洽且说得清。
    """
    now = time.monotonic()
    if now - getattr(_log_note_text_skipped, "_at", 0.0) < 60.0:
        return
    _log_note_text_skipped._at = now  # type: ignore[attr-defined]
    logger.warning("[larkdeck] 正文 %d 字节（JSON 转义后；原始 %d 字节）超过 %d 字节预算，"
                   "未为「中止重绘」保留副本 —— 若本回合掉过 native，"
                   "/stop 时这张卡不会变成中止色",
                   size, raw_size, _MAX_TRACKED_TEXT)


def _log_degrade_once(tier: str, elements: int = 0, size: int = 0) -> None:
    """卡片降载日志 —— **限流**：60 秒最多一条。

    降载是在每个流式帧上判定的，不限流的话超预算期间每秒会刷 4 条
    （本项目自己的约定是诊断日志必须限流，见 _log_empty_panel_once）。

    ``elements`` 是这张卡递归数出来的元素数（飞书硬上限 200，真机实测 202 就被
    ``230099/11310`` 拒）。必须打出来：光看档位名分不出降载是**字节**触发的还是
    **元素数**触发的，而两者的处置完全不同（后者要收轮数 / 步数，不是收长度）。

    ``size`` 是这张卡的 utf-8 字节数（与软预算一起打）—— 第八路审计指出：只打
    「元素 1/200」读起来像「还有很大余量」，看不出到底撞的是哪堵墙。
    """
    now = time.monotonic()
    if now - getattr(_log_degrade_once, "_at", 0.0) < 60.0:
        return
    _log_degrade_once._at = now
    logger.warning("[larkdeck] 卡片降载：档位=%s · 元素 %d/%d · 字节 %d/%d"
                   "（正文不截断；若仍发不出会回落官方分块）",
                   tier, elements, _cards.FEISHU_ELEMENT_LIMIT, size,
                   _cards.CARD_BYTE_BUDGET)


def _ld_trace_id(card_ref: Any) -> str:
    """**本卡短码** —— 取 ``message_id`` / ``card_id`` 的后 6 位（R11-C2）。

    为什么是后 6 位而不是整个 id：它的用途只有一个是「**把用户截图和日志对齐**」
    —— 印在卡片页脚上、同一串也进每回合自检行，人眼抄 6 位不会抄错，日志里 grep 也够唯一。
    ⚠️ 它**不是**安全凭据（id 本身在飞书里是本会话可见的），别拿它当鉴权用。
    """
    return str(card_ref or "")[-6:]


def _log_turn_selfcheck(chat_id: str, transport: str, frames: int,
                        strips: int = 0, trace: str = "") -> None:
    """**每回合一条自检汇总**（60 秒限流）：把「这次卡片到底长没长全」变成机器可读。

    ⚠️ 为什么必须有（2026-09-14 的教训）：面板为空 / 页脚不显示 / 账本「累计 0 帧」这三个
    症状，**用户看得见、日志里却一个字都没有** —— 于是只能靠「让用户再发一条消息、再截个图」
    来判定，而那是**在消耗用户的时间**。这一行把三件事一次性写进日志，任何人（包括我）
    读日志就能判定，不需要眼睛，也不需要用户复现。

    字段全部取自**只读**快照：`panel.diagnose()`（会话桶与内容）与 `context.status_snapshot()`
    （写卡帧数）。判据是「有没有内容」，不是「内容对不对」——内容对不对由单元测试与真机探针管。

    ``strips``（R11-A7）是「本回合有多少帧**真的剥掉了核心叠加的工具进度块**」：这个数
    只有真机回合才可能非 0（核心的进度行由 `display.tool_progress` 决定，默认飞书档位是
    `"new"`），所以它是**正文净化在真机上确实生效**的唯一凭据（卡片本身读不回来）。
    """
    now = time.monotonic()
    if now - getattr(_log_turn_selfcheck, "_at", 0.0) < 60.0:
        return
    _log_turn_selfcheck._at = now          # type: ignore[attr-defined]
    try:
        info = _panel.diagnose(chat_id)
        snap = _context.status_snapshot() or {}
    except Exception:
        logger.debug("[larkdeck] 回合自检汇总失败", exc_info=True)
        return
    panel_ok = bool(info.get("rounds") or info.get("tools"))
    try:
        footer_text = LarkDeckMixin._ld_footer(chat_id=chat_id) or ""
    except Exception:
        footer_text = ""
    logger.info(
        "[larkdeck] 回合自检：面板=%s（rounds=%s tools=%s）· 页脚=%s（%s）· 写卡帧数=%s · "
        "传输=%s · 本回合帧=%s · 正文剥进度=%s · 卡片=%s · 会话桶=%s",
        "有" if panel_ok else "无", info.get("rounds"), info.get("tools"),
        "有" if footer_text else "无", footer_text[:40] or "空",
        snap.get("frame_ok_count"), transport, frames, strips, trace or "无",
        info.get("buckets"))


def _log_empty_panel_once(chat_id: str = "") -> None:
    """面板为空时记一条限流 INFO **外加一份只读诊断**（60 秒最多一条）。

    ⚠️ 为什么必须带上 `chat_id` 并打**数字**（2026-09-14 真机踩到）：这个函数以前只写
    「面板无数据（钩子未写入或已清空）」—— 而「面板为空」有**两种成因、修法相反**：
    ① 这个 chat 绑定到了**另一个（空的）会话桶**；② 桶被**反复清空**（`_touch_locked`
    把每次钩子事件都判成「换了回合」⇒ rounds/tools 清零）。只有症状、没有数字时，
    排查只能靠猜，而猜错方向等于白改一通。`panel.diagnose()` 是**只读**快照，代价可忽略。
    """
    now = time.monotonic()
    if now - getattr(_log_empty_panel_once, "_at", 0.0) < 60.0:
        return
    _log_empty_panel_once._at = now
    logger.info("[larkdeck] 面板无数据（钩子未写入或已清空）")
    try:
        info = _panel.diagnose(chat_id)
        logger.info(
            "[larkdeck] 面板为空 · 诊断：chat=%s · 绑定会话=%s（桶存在=%s）· 选中会话=%s · "
            "该桶 rounds=%s tools=%s reasoning_len=%s turn=%s · 进程内共 %s 个会话桶",
            info.get("chat"), info.get("bound_session") or "（无）",
            info.get("bound_state_exists"), info.get("selected_session") or "（无）",
            info.get("rounds"), info.get("tools"), info.get("reasoning_len"),
            info.get("turn_id"), info.get("buckets"))
    except Exception:
        logger.debug("[larkdeck] 面板诊断失败", exc_info=True)


def generation_snapshot_line() -> str:
    """R11-A1：一行**机器可读**的世代快照（启动自检旁边打）。

    真机实测「同一进程里插件被加载两遍」，而这件事的两个关键问题——**哪一份是活的**、
    第二遍是同一个 manager 还是两个——在日志里原本只能靠时序推断（`Plugin discovery complete`
    出现两次 + 启动自检打印两遍，还容易被别的插件淹没）。这一行把它变成能读的数字：

    ``模块=<名字>#<id> · 加载序号=N · manager=<id> · home=<路径> · 钩子回调数={...}``

    * **加载序号**（`panel.load_seq()`）：1 = 第一世代；**≥2 就说明世代更替真的发生了**，
      而且同一份日志里两行不同的序号能直接指出「谁先谁后」；
    * **模块 id**：`#140234` 这种数字，两份模块对象一眼可分；
    * **manager id / home**：两个不同的 id ⇒ 是**两个 manager**（按 home 分身），
      与「同一个 manager 里重复加载」是两种病、修法不同；
    * **钩子回调数**：同一个 manager 里我们那 7 个钩子只该各一份；出现 2 份才是真的重复订阅。

    只读、不抛（`compat.manager_snapshot()` 取不到就只少几个字段，不许让自检失败）。
    """
    parts = [f"模块={__name__}#{id(sys.modules.get(__name__))}",
             f"加载序号={_panel.load_seq()}",
             f"（进程内最新={_panel.latest_load_seq()}）"]
    try:
        snap = _compat.manager_snapshot()
    except Exception:
        snap = {}
    if snap:
        raw_hooks = snap.get("hooks") or {}
        ours = {k: v for k, v in raw_hooks.items() if k in _compat.OBSERVED_HOOKS}
        parts.append(f"manager={snap.get('manager')}")
        parts.append(f"home={snap.get('home') or '（读不到）'}")
        # ⚠️ 「读不到」与「一个都没有」必须分开说（否则排障时会拿「空」当「没挂上」，
        # 或者更糟：拿「没挂上」当「读不到」而放过一次真的静默失灵）。
        if not raw_hooks:
            parts.append("钩子回调数=读不到")
        else:
            parts.append(f"钩子回调数={ours or '（我们订阅的一个都没有）'}")
    else:
        # 不在 Hermes 环境里（单测）——如实说，别编一个数字
        parts.append("manager=读不到（不在 Hermes 环境里）")
    return " · ".join(parts)


def _log_generation_snapshot() -> None:
    """把世代快照打进日志（启动自检旁边）。诊断失败绝不影响自检结论。"""
    try:
        logger.info("[larkdeck] 世代快照：%s", generation_snapshot_line())
    except Exception:  # pragma: no cover - 防御性
        logger.debug("[larkdeck] 世代快照打印失败", exc_info=True)


def _remember_selfcheck(ok: bool, detail: str) -> None:
    SELFCHECK["ok"] = ok
    SELFCHECK["detail"] = detail
    if ok:
        logger.info("[larkdeck] 启动自检通过：%s", detail)
    else:
        # 自检失败必须响亮：否则用户会以为卡片在跑，实际还是内置纯文本。
        logger.error("[larkdeck] 启动自检失败：%s（卡片不会生效，飞书仍是纯文本）", detail)
    _log_generation_snapshot()


# --------------------------------------------------------------------------- #
# 覆盖层
# --------------------------------------------------------------------------- #
def _ld_theme() -> str:
    """当前生效主题名；认不出的配置值退回 neutral 并**限流留痕**（审计 C2）。

    为什么不能静默：用户把 `theme` 写成笔误（`ap_late`）时，卡片观感会退回旧符号，
    但没有任何信号说明「是你配错了」还是「主题功能坏了」—— 这正是本项目最忌讳的
    静默失灵。日志限流复用 `_log_*_once` 的既定手法（60 秒一条），不在每帧热路径刷。
    """
    raw = _cfg_raw("theme")
    name = _cards.theme_name(raw)
    try:
        text = str(raw or "").strip().lower()
    except Exception:                           # pragma: no cover - 防御性
        text = ""
    if text and text != name:
        _log_theme_once(raw, name)
    return name


def _log_theme_once(raw: Any, fallback: str) -> None:
    now = time.monotonic()
    if now - getattr(_log_theme_once, "_at", 0.0) < 60.0:
        return
    _log_theme_once._at = now  # type: ignore[attr-defined]
    try:
        shown = repr(raw)
    except Exception:                           # pragma: no cover - 防御性
        shown = type(raw).__name__
    logger.warning("[larkdeck] theme=%s 不是可用主题，已退回 %s（可选：neutral / ap_lite / "
                   "ap_bubble）", shown, fallback)


def _ld_view_status(chat_id: str, *, default: str = "processing") -> str:
    """面板快照的结局词汇（``ok``/``error``/``stopped``）→ **卡级状态词汇**。

    两套词汇的映射只放一处（V4.5，审计 B 的「结构化错误色不可达」）：结构化路径过去在
    finalize 时写死 ``"completed"``，于是**失败回合的收尾卡照样是绿头**、`error`/`stopped`
    这两种状态在结构化车道上根本走不到（只有 `/stop` 的重绘那条路显式传了 stopped）。
    """
    try:
        snap = _panel.snapshot(chat_id) or {}
        raw = str(snap.get("status") or "")
    except Exception:      # pragma: no cover - 防御性：状态是装饰，绝不许把帧搞失败
        raw = ""
    return {"ok": "completed", "completed": "completed",
            _panel.STATUS_ERROR: "error", _panel.STATUS_STOPPED: "stopped"}.get(raw, default)


def _ld_status_text(status: Any) -> str:
    """面板结局 → 页脚最前面的状态文案（``✅ 已完成`` / ``❌ 执行出错`` / ``⛔ 已中止``）。

    ⚠️ 这里同时收**两套词汇**（V4.2 实测的静默失败）：面板快照用
    ``ok / error / stopped``，而结构化视图（`_cardview.CardView.header_status`）用
    ``completed / stopped / error``。只认前一套时，`_ld_footer(status="completed")` 会
    **静默少一段** —— 因为传入值非空，连「回落到面板快照」那条路都不会走。
    两套都在这里收口，就是这个映射表唯一的职责。
    """
    key = {
        _panel.STATUS_OK: "panel.status_ok",
        _panel.STATUS_ERROR: "panel.status_error",
        _panel.STATUS_STOPPED: "panel.status_stopped",
        #: 结构化视图的卡级状态词汇（``CardView.header_status``）
        "completed": "panel.status_ok",
    }.get(str(status or ""))
    return _i18n.t(key) if key else ""


class LarkDeckMixin:
    """叠在内置 FeishuAdapter 之上的卡片渲染层。

    不继承 ``BasePlatformAdapter`` —— 所有父类实现都来自被叠加的内置类，
    这样既避免 MRO 冲突，也保证「内置有什么我们就有什么」。

    ``__slots__ = ()`` 是刻意的：混入层不持有任何实例状态，状态一律挂在
    ``_ld_*`` 属性上（``_ld_setup()`` 里初始化）。这样这个类可以干净地叠在
    任何内置适配器类之前，不参与任何内存布局假设。
    """

    __slots__ = ()

    #: 末帧也要走 edit_message，而不是另发一条新消息 —— 卡片原地收尾的关键。
    REQUIRES_EDIT_FINALIZE = True

    # ------------------------------------------------------------------ 状态
    def _ld_setup(self) -> None:
        self._ld_state: Dict[str, Dict[str, Any]] = {}
        self._ld_streams: Dict[str, Dict[str, Any]] = {}
        #: v0.7.0 P1：native seed 帧刚失败的短窗口标记（chat -> (turn, monotonic)）。
        #: 核心随后可能在 interim tick 直接 `_first_send(display_text)`，而 display_text
        #: 是 `_compose_frame_content()` 合成文本（可能含 terminal 命令/args）。own 模式
        #: 必须在 `send()` 里识别这个窗口并拒绝把它当正文。
        self._ld_seed_failures: Dict[str, Any] = {}
        self._ld_lock = threading.Lock()

    def _ld_track(self, message_id: str, chat_id: str) -> None:
        if not message_id:
            return
        with self._ld_lock:
            if len(self._ld_state) >= _MAX_TRACKED:
                # 按「最近活动」淘汰，**不按创建时刻**：长回合的卡创建得早但仍在被编辑，
                # 按 t0 淘汰会把它们踢出去 —— 之后 edit_message 找不到追踪项就回落内置
                # 实现，而内置走 message.update、对 interactive 卡会被飞书拒，卡片永久冻结。
                oldest = sorted(self._ld_state.items(),
                                key=lambda kv: kv[1].get("last", kv[1].get("t0", 0.0)))
                for key, _ in oldest[: _MAX_TRACKED // 2]:
                    self._ld_state.pop(key, None)
            now = time.monotonic()
            # last_text 只用于「中止时原地重绘」，见 _ld_redraw_stopped。
            # 单个 chat 只留最近一张卡的正文（旧的清掉），所以内存量级 = 卡数 × 该卡正文，
            # 而不是「追踪过的全部卡 × 全部正文」。
            for other in self._ld_state.values():
                if other.get("chat_id") == chat_id:
                    other["last_text"] = ""  # 只留最近一张卡的正文
            self._ld_state[message_id] = {"chat_id": chat_id, "t0": now, "last": now,
                                          "last_text": ""}

    def _ld_note_text(self, message_id: str, text: str) -> None:
        """记下这张卡最后渲染过的正文（供非 native 路径的「中止重绘」用）。

        上限按 **JSON 转义后的字节数**（:func:`_card_body_bytes`），与「这张卡装不装得下
        状态小面板」的守卫**同口径**：**发得出去的卡就存得下正文、也一定画得上色**。
        真正超限时**不静默**：留一条限流日志说明「中止时这张卡不会变色」——
        静默降级是本项目的头号失败模式（审计实测出来的）。
        """
        body = str(text or "")
        size = _card_body_bytes(body)
        # 判据只有**一个**：留下它之后，`/stop` 那张卡能不能既发得出去、又画上色。
        # ⚠️ `_MAX_TRACKED_TEXT` **不能**当充分条件（第十路审计实测）：纯 CJK 在
        # JSON 126890 字节就已经画不上色 —— 比阈值 126976 **低 86 字节**，于是那 86 字节的
        # 带里会「留下了正文、却发出一次没有颜色的 patch」。所以这里只在**明显没戏**时
        # 用阈值省掉一次构造，其余一律问真判据（:func:`_stop_redraw_would_paint`）。
        entry = self._ld_state.get(message_id) if isinstance(self._ld_state, dict) else None
        chat = str((entry or {}).get("chat_id") or "")
        panel = None
        footer = None
        if chat:
            try:
                panel = self._ld_panel(chat, report_empty=True)
                footer = self._ld_frame_footer({
                    "message_id": message_id, "chat_id": chat,
                    "t0": (entry or {}).get("t0"), "status": _panel.STATUS_STOPPED,
                })
            except Exception:                 # pragma: no cover - 判据是装饰，绝不因此丢正文
                panel = None
                footer = None
        if size > _HOPELESS_BYTES or not _stop_redraw_would_paint(
                body, panel=panel, footer=footer):
            _log_note_text_skipped(size, len(body.encode("utf-8", "ignore")))
            body = ""
        with self._ld_lock:
            entry = self._ld_state.get(message_id)
            if isinstance(entry, dict):
                entry["last_text"] = body
            if body:
                # 内存上界：最多 _MAX_TEXT_ENTRIES 份正文，超出清掉最久没用过的
                holders = sorted((mid for mid, value in self._ld_state.items()
                                  if value.get("last_text")),
                                 key=lambda mid: self._ld_state[mid].get("last", 0.0))
                for stale_id in holders[:-_MAX_TEXT_ENTRIES]:
                    self._ld_state[stale_id]["last_text"] = ""

    def _ld_drop_message(self, message_id: str, code: int) -> None:
        """把一条**已经不存在**的消息标死：清追踪 + 清回合状态 + 限流留痕（R5 的撤回守卫）。

        ⚠️ 只清我们自己那一份状态：**不补发、不另建卡**。补发是核心的事（fail-open 链），
        我们补发就会变成「DM 两张卡」——那是本项目明确要避免的形态。
        """
        if not message_id:
            return
        _log_withdrawn_once(int(code))
        try:
            self._ld_forget(message_id)
        except Exception:      # noqa: BLE001 —— 守卫本身绝不许把帧路径炸掉
            logger.debug("[larkdeck] 清追踪失败（忽略）", exc_info=True)

    def _ld_known(self, message_id: str) -> Optional[Dict[str, Any]]:
        """查这张卡是不是我们自己发的（并顺带刷新「最近活动」，供淘汰用）。

        刻意写成**永不抛**：``edit_message`` 的第一行就会调它，而那一行在 try 之外 ——
        一旦 ``_ld_setup()`` 没跑成（构造期异常被上层吞掉），``AttributeError`` 会直接
        穿进核心的每帧编辑路径，破坏「卡片失败必须回落官方实现」这条不变量。
        宁可返回 None（调用方据此回落 ``super()``），也不能抛。
        """
        state = getattr(self, "_ld_state", None)
        lock = getattr(self, "_ld_lock", None)
        if not isinstance(state, dict) or lock is None:
            return None
        try:
            with lock:
                entry = state.get(message_id)
                if not entry:
                    return None
                # 刷新最近活动：淘汰必须按「最近用过」而不是「创建时刻」，
                # 否则长回合的卡会被踢掉，之后编辑回落内置实现、卡片永久冻结。
                entry["last"] = time.monotonic()
                return dict(entry)
        except Exception:  # pragma: no cover - 防御性
            logger.debug("[larkdeck] 查卡片追踪失败", exc_info=True)
            return None

    def _ld_stream_for_message(self, message_id: str) -> Optional[Dict[str, Any]]:
        """按 message_id 找当前活跃的 native stream state（浅拷贝；找不到 None）。"""
        if not message_id:
            return None
        lock = getattr(self, "_ld_lock", None)
        streams = getattr(self, "_ld_streams", None)
        if lock is None or not isinstance(streams, dict):
            return None
        with lock:
            for state in streams.values():
                if isinstance(state, dict) and str(state.get("message_id") or "") == message_id:
                    return dict(state)
        return None

    def _ld_note_seed_failure(self, chat_id: str, turn_id: str) -> None:
        chat = str(chat_id or "").strip()
        if not chat:
            return
        with self._ld_lock:
            self._ld_seed_failures[chat] = (str(turn_id or ""), time.monotonic())

    def _ld_seed_failure_active(self, chat_id: str, ttl: float = 15.0) -> bool:
        chat = str(chat_id or "").strip()
        if not chat:
            return False
        now = time.monotonic()
        with self._ld_lock:
            item = self._ld_seed_failures.get(chat)
            if not item:
                return False
            if now - float(item[1]) > ttl:
                self._ld_seed_failures.pop(chat, None)
                return False
            return True

    def _ld_clear_seed_failure(self, chat_id: str) -> None:
        chat = str(chat_id or "").strip()
        if not chat:
            return
        with self._ld_lock:
            self._ld_seed_failures.pop(chat, None)

    def _ld_stream_key_for_message(self, message_id: str) -> Optional[str]:
        """按 message_id 找活跃 native stream 的 key（找不到 None）。"""
        if not message_id:
            return None
        lock = getattr(self, "_ld_lock", None)
        streams = getattr(self, "_ld_streams", None)
        if lock is None or not isinstance(streams, dict):
            return None
        with lock:
            for key, state in streams.items():
                if isinstance(state, dict) and str(state.get("message_id") or "") == message_id:
                    return str(key)
        return None

    def _ld_stream_own_text(self, chat_id: str, stream_state: Dict[str, Any]) -> str:
        """own 模式取该流对应的正文；绑定/回合漂移时返回空（fail-open，不串会话/回合）。"""
        chat = str(chat_id or "").strip()
        stored = str(stream_state.get("session_id") or "")
        bound = str(_panel.bound_session_id(chat) or "") if chat else ""
        if stored and bound and stored != bound:
            _log_body_diag_once(
                "own-binding-drift",
                "[larkdeck] 卡片会话绑定漂移：stream_session=%s bound_session=%s"
                "（按空正文 fail-open，不串会话）", stored[:16], bound[:16])
            return ""
        stored_gen = int(stream_state.get("answer_gen") or 0)
        try:
            bucket_gen = (_panel.answer_generation(chat, require_binding=True)
                          if chat else 0)
        except Exception:  # pragma: no cover - 防御性
            bucket_gen = 0
        if stored_gen != bucket_gen:
            _log_body_diag_once(
                "own-generation-drift",
                "[larkdeck] 卡片正文世代漂移：stream_gen=%s bucket_gen=%s"
                "（按空正文 fail-open，不串会话/回合）", stored_gen, bucket_gen)
            return ""
        return self._ld_own_text(chat)

    def _ld_forget(self, message_id: str) -> None:
        with self._ld_lock:
            self._ld_state.pop(message_id, None)
            # native 回合状态也一并清掉：finalize 失败后核心回落 edit_message 成功时，
            # 单靠 edit_message 的 _ld_forget 会漏掉 _ld_streams 里的回合残留。
            stale = [key for key, state in self._ld_streams.items()
                     if state.get("message_id") == message_id]
            for key in stale:
                self._ld_streams.pop(key, None)

    @staticmethod
    def _ld_context_segment(snap: Optional[Dict[str, Any]] = None) -> str:
        """页脚里的上下文用量片段；样式由 ``context_style`` 决定（text / bar / both）。"""
        snap = snap if snap is not None else _context.snapshot()
        style = str(_cfg_raw("context_style") or "text").strip().lower()
        if style not in ("text", "bar", "both"):
            style = "text"
        return _cards.context_indicator(
            snap.get("input_tokens"), snap.get("context_max"), style=style,
        )

    @classmethod
    def _ld_frame_footer(self, state: Dict[str, Any]) -> Optional[str]:
        """**帧路径**的页脚：在原有页脚之后接上本卡短码（R11-C2）。

        两条纪律：
        ① **没有基数页脚就不加短码** —— 短码绝不能把「页脚=无」这个诊断信号抹掉
           （自检行里的 `页脚=无` 是排查「钩子没喂数据」的入口，见 R9 审计）；
        ② 短码取自**这一帧的卡**（`message_id` / `card_id`），不是进程级快照 ——
           多会话并发时不会串台（页脚指标那种串台是**已知取舍**，但短码是**定位**用的，
           串了就等于没有）。
        """
        base = self._ld_footer(chat_id=str(state.get("chat_id") or ""),
                               started=state.get("t0"),
                               status=state.get("status"))
        trace = _ld_trace_id(state.get("message_id") or state.get("card_id"))
        if not base or not trace:
            return base
        return f"{base} · \U0001f516 {trace}"

    @classmethod
    def _ld_footer(cls, chat_id: str = "", started: Optional[float] = None,
                   status: Optional[str] = None) -> Optional[str]:
        """页脚一行：``状态 · ⏱ 时长 · 🤖 模型 · ctx 用量 · 短码``。

        用户 2026-09-17 明确指定（对齐 aiduPOP 的页脚观感）：
          * 状态放**最前面**（``✅ 已完成`` / ``❌ 执行出错`` / ``⛔ 已中止``）；
          * 模型名从面板标题搬到页脚；
          * 上下文用量与卡短码继续留在页脚。
        面板标题因此只保留 ``轮数 · 工具数``。

        **R7 扩展**（配置 ``footer_metrics``，默认 ``off``）：
          * ``basic`` —— 加缓存命中率（``⚡ 75%``）与本回合 API 次数（``🔁 7``）；
          * ``full``  —— 再加首字节延迟（``🐢 0.4s``）。
        数据来自官方钩子（见 :mod:`larkdeck.core.context`）：钩子还没触发时该段自然缺失，
        全缺就返回 ``None``（不渲染脚注元素）。**任何情况下不抛异常** —— 页脚是装饰，
        不能因为它把整张卡片搞坏。
        """
        try:
            if not _cfg("footer"):
                return None
            mode = str(_cfg_raw("footer_metrics") or "off").strip().lower()
            if mode not in ("off", "basic", "full"):
                mode = "off"          # 认不出的值按 off（不猜、不放大）
            ctx_snap = _context.snapshot() or {}
            panel_snap = _panel.snapshot(chat_id) if chat_id else _panel.snapshot()
            status_text = _ld_status_text(
                status or (panel_snap.get("status") if isinstance(panel_snap, dict) else None))
            model = ""
            if _cfg("show_model"):
                model = str(ctx_snap.get("model_display") or "")
            duration = None
            # ⚠️ truthy 判据：``started=0`` 不是合法回合起点（monotonic 不会为 0），
            # 当「有起点」会算出机器 uptime 级别的假耗时（2026-09-17 审计 M6）。
            if started:
                try:
                    duration = max(0.0, time.monotonic() - float(started))
                except (TypeError, ValueError, OverflowError):
                    duration = None
            metric_snap = ctx_snap if mode != "off" else {}
            return _cards.footer_line(
                status=status_text,
                duration=duration,
                model=model,
                context=cls._ld_context_segment(ctx_snap),
                cache=metric_snap.get("cache_pct") if mode in ("basic", "full") else None,
                api=metric_snap.get("api_call_count") if mode in ("basic", "full") else None,
                ttfb=(None if metric_snap.get("ttfb_ms") is None
                      else float(metric_snap["ttfb_ms"]) / 1000.0)
                if mode == "full" else None,
                theme=_ld_theme(),
            )
        except Exception:
            logger.debug("[larkdeck] 页脚渲染失败，跳过", exc_info=True)
            return None

    @classmethod
    def _ld_panel_summary(cls, snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """外层折叠面板标题：``💭 思考 1.6s · 🛠️ 工具执行 · 3 步``（i18n 节点）。

        用户 2026-09-17 指定向 CLS 看齐：思考与工具用**两个不同的 emoji**，思考显示
        耗时、工具显示步数。返回的是 ``i18n_text`` 节点（不是裸字符串），折叠面板标题
        在中英文客户端各显示各的；``cards.unified_panel`` 直接把它当 plain_text 用。
        缺数据就少一段；全缺返回 ``None``，调用方退回固定标题「执行详情」。
        """
        try:
            rounds = [item for item in (snap.get("rounds") or []) if isinstance(item, dict)]
            tools = snap.get("tools") or []
            reasoning = str(snap.get("reasoning") or "")
            count = len(tools)
            elapsed_ms = _cards._rounds_elapsed_ms(rounds)
            if count:
                if rounds or reasoning:
                    if elapsed_ms > 0:
                        key = ("panel.summary_both_one" if count == 1
                               else "panel.summary_both")
                        return _i18n.i18n_text(
                            key, elapsed=_cards.format_elapsed(elapsed_ms / 1000.0), n=count)
                    key = ("panel.summary_both_plain_one" if count == 1
                           else "panel.summary_both_plain")
                    return _i18n.i18n_text(key, n=count)
                key = "panel.sec_tools_one" if count == 1 else "panel.sec_tools"
                return _i18n.i18n_text(key, n=count)
            if rounds or reasoning:
                if elapsed_ms > 0:
                    return _i18n.i18n_text(
                        "panel.sec_thinking",
                        elapsed=_cards.format_elapsed(elapsed_ms / 1000.0))
                return _i18n.i18n_text("panel.sec_thinking_plain")
            return None
        except Exception:
            logger.debug("[larkdeck] 面板标题渲染失败，跳过", exc_info=True)
            return None

    @classmethod
    def _ld_panel_parts(cls, chat_id: str = "", *,
                        report_empty: bool = False) -> Tuple[str, str]:
        """面板的**两块**内容（CardKit 实体卡用，R3 收窄版）：``(推理块, 工具块)``。

        为什么拆两块：推理文本**逐字在长**（轮次标题的耗时每秒还在变）⇒ 装推理的那个元素
        几乎每帧都要重写；而工具行**只在工具开始/结束时才变**。合成一个 markdown 时，
        那几十行工具摘要会跟着推理一起每帧重发（实测 ≈2.7KB/帧）。两块走**同一次**
        `card.batch_update` ⇒ **逻辑写次数一次都不增加**；去重（`ck_decor`）让工具块
        只在工具事件那一帧才发。

        与 :meth:`_ld_panel_markdown` **同源快照、同一套上限**，所以拆开不会改变用户看到的内容：
        `cards.panel_markdown()`（普通卡 / `patch` 传输）就是这两块的拼接（先推理、后工具）。

        任何异常都退回空串（面板是装饰，绝不因为它把帧搞失败）。
        """
        show_reasoning = _ld_show_reasoning()   # §9.3：这条车道同样要过 show_reasoning 过滤
        try:
            if not _cfg("unified_panel"):
                # 与 `_ld_panel_markdown` 同一条门禁（关掉面板的人不该在 cardkit 下还看到面板）
                return "", ""
            snap = _panel.snapshot(chat_id) or {}
            steps = [
                _cards.tool_step(
                    str(t.get("name") or "tool"),
                    status=str(t.get("status") or "ok"),
                    duration_ms=t.get("duration_ms"),
                    preview=str(t.get("preview") or ""),
                    theme=_ld_theme(),
                )
                for t in (snap.get("tools") or [])
            ]
            _body, _tools_text = (
                _cards.panel_rounds_markdown(
                    # show_reasoning=false ⇒ 只留 `💭 思考 · 1.6s` 摘要行，正文一个字不上卡
                    reasoning=str(snap.get("reasoning") or ""),
                    rounds=snap.get("rounds") or [],
                    include_text=show_reasoning,
                    max_reasoning_chars=_cfg_int("max_reasoning_chars", _cards.MAX_REASONING_CHARS),
                ),
                _cards.panel_tools_markdown(
                    tools=steps,
                    max_tool_chars=_cfg_int("max_tool_result_chars", _cards.MAX_TOOL_RESULT_CHARS),
                    max_steps=_cfg_int("max_panel_steps", _cards.MAX_PANEL_STEPS),
                ),
            )
            if not (_body or _tools_text) and report_empty:
                # ⚠️ **只在收尾帧报**（R11 §3.4 的真机噪声收口）：`report_empty` 由
                # 「这一帧是不是终局帧」决定，默认 False —— 因为**建卡 seed 帧必然为空**
                # （那一刻还没有任何过程数据），旧写法会在每个回合开头都打一条
                # 「面板为空 · 诊断」，把真正的异常淹掉。判据见 `_log_empty_panel_once`。
                _log_empty_panel_once(chat_id)
            return _body, _tools_text
        except Exception:
            logger.debug("[larkdeck] 面板两块渲染失败，跳过", exc_info=True)
            return "", ""

    @classmethod
    def _ld_panel_markdown(cls, chat_id: str = "") -> str:
        """面板内容的 **markdown 文本**（**普通卡 / `patch` 传输**用，见 :func:`cards.panel_markdown`）。

        与 :meth:`_ld_panel` 取**同一份快照、同一套上限**，所以两条传输看到的内容一致；
        差别只是载体（多个元素 vs 一个 markdown 字符串）。任何异常都退回空串（面板是装饰）。

        ⚠️ **CardKit 实体卡不再用它**（R3 收窄版起改用 :meth:`_ld_panel_parts` 的两块）。
        ⚠️⚠️ 与 `cards.panel_markdown` 一样，**它现在在生产里也没有调用方了**：
        `/stop` 重绘、降级车道、收尾整卡替换与普通卡走的是 `_ld_panel()` → `unified_panel`
        （另一条渲染路径）。保留它是给**单测与 `probe_render.py`** 用的对拍基准，
        别再把它描述成「几条车道共用」。
        """
        show_reasoning = _ld_show_reasoning()   # §9.3：这条车道同样要过 show_reasoning 过滤
        try:
            if not _cfg("unified_panel"):
                return ""          # 与 `_ld_panel` 同一条门禁（关掉面板的人不该在 cardkit 下还看到面板）
            snap = _panel.snapshot(chat_id) or {}
            steps = [
                _cards.tool_step(
                    str(t.get("name") or "tool"),
                    status=str(t.get("status") or "ok"),
                    duration_ms=t.get("duration_ms"),
                    preview=str(t.get("preview") or ""),
                    theme=_ld_theme(),
                )
                for t in (snap.get("tools") or [])
            ]
            if not steps and not snap.get("rounds") and not snap.get("reasoning"):
                return ""
            return _cards.panel_markdown(
                reasoning=str(snap.get("reasoning") or ""),
                rounds=snap.get("rounds") or [],
                include_reasoning_text=show_reasoning,
                tools=steps,
                max_reasoning_chars=_cfg_int("max_reasoning_chars", _cards.MAX_REASONING_CHARS),
                max_tool_chars=_cfg_int("max_tool_result_chars", _cards.MAX_TOOL_RESULT_CHARS),
                max_steps=_cfg_int("max_panel_steps", _cards.MAX_PANEL_STEPS),
            )
        except Exception:
            logger.debug("[larkdeck] 面板 markdown 渲染失败，跳过", exc_info=True)
            return ""

    @classmethod
    def _ld_panel(cls, chat_id: str = "", *,
                  report_empty: bool = False) -> Optional[Dict[str, Any]]:
        """底部折叠面板：推理过程 + 工具步骤 + 状态色（数据来自 :mod:`larkdeck.core.panel`）。

        ``chat_id`` 决定面板归属：由 ``pre_gateway_dispatch`` 观察到的
        ``chat_id -> session_id`` 映射给出**确定性**归属，并发会话不再串台。
        拿不到映射时（新会话首回合 / 老版本 Hermes）自动退回「最近活跃会话」的旧行为
        —— **归属失败绝不能导致面板不渲染**。

        ⚠️ **不再收 ``started``**（2026-09-17）：面板标题只放 CLS 观感的
        「💭 思考 / 🛠️ 工具执行」摘要，模型名与回合耗时都在页脚（``_ld_footer``）。
        删掉形参是为了让「面板标题不会再有耗时」由**签名**保证，而不是靠调用方记得别传。

        没有数据（钩子未触发 / reasoning 未开启 / 面板关掉）就返回 ``None``，
        ``reply_card`` 会自然跳过这个元素。与页脚同理：**任何情况下不抛异常**，
        面板是装饰，不能因为它把整张卡片搞坏。
        """
        show_reasoning = _ld_show_reasoning()   # §9.3：DEGRADE/旧车道同样要过这条过滤
        try:
            if not _cfg("unified_panel"):
                return None
            snap = _panel.snapshot(chat_id)
            if not snap:
                if report_empty:
                    # 同上：只在终局帧报（seed / 中间帧的面板为空是**正常**的）
                    _log_empty_panel_once(chat_id)
                return None
            steps = [
                _cards.tool_step(
                    str(t.get("name") or "tool"),
                    status=str(t.get("status") or "ok"),
                    duration_ms=t.get("duration_ms"),
                    preview=str(t.get("preview") or ""),
                    theme=_ld_theme(),
                )
                for t in (snap.get("tools") or [])
            ]
            return _cards.unified_panel(
                reasoning=str(snap.get("reasoning") or ""),
                rounds=snap.get("rounds") or [],
                include_reasoning_text=show_reasoning,
                tools=steps,
                expanded=_cfg("panel_expanded"),
                status=snap.get("status"),
                summary=cls._ld_panel_summary(snap),
                max_reasoning_chars=_cfg_int("max_reasoning_chars", _cards.MAX_REASONING_CHARS),
                max_tool_chars=_cfg_int("max_tool_result_chars", _cards.MAX_TOOL_RESULT_CHARS),
                max_steps=_cfg_int("max_panel_steps", _cards.MAX_PANEL_STEPS),
            )
        except Exception:
            logger.warning("[larkdeck] 面板渲染失败，跳过", exc_info=True)
            return None

    # ---------------------------------------------------------------- 卡片构造
    def _ld_render_card(self, chat_id: str, content: str, *, streaming: bool,
                        status: str, panel: Optional[Dict[str, Any]],
                        footer: Optional[str], started: Optional[float] = None,
                        message_id: Optional[str] = None) -> Dict[str, Any]:
        """**非流式车道**（`send()` / `edit_message()`）的卡片 JSON。

        为什么必须有这一层（V4.4，真机截图发现）：`structured` 只覆盖了 native 流式卡与
        收尾整卡，`send()`/`edit_message()` 这两条**回落车道**还在走旧 markdown 面板 ——
        用户 2026-09-21 的 `/stop` 回复截图就是证据：工具名是旧的（`Load skill`/`Search`/
        `Run command`）、没有 22px 细节缩进、页脚没有时长与短码，而且**推理正文照样上卡**
        （`show_reasoning=false` 被绕过 —— 违反 §9.3「show_reasoning 全车道」）。

        规则：
          * 引擎是 ``structured`` ⇒ 用**同一棵元素树**（:func:`_cardview.entity_skeleton`），
            面板/页脚/状态头与流式卡逐字段同源；`config.streaming_mode` 按调用方给的
            ``streaming`` 落值（非流式为 false）。
          * 超**元素/字节墙**（:func:`_ck_create_wall`）⇒ 退回旧渲染器：它会分级丢装饰
            （面板 → 页脚 → 裸卡），而结构化的元素树**建出来就定死**、没有分级余地 ——
            这里宁可退回旧观感，也不能让整条消息发不出去。
          * 旧渲染器同时负责 ``visual_engine`` 未接管的路径（DEGRADE 车道）。
        """
        if _ld_visual_engine() == "structured":
            try:
                status = _ld_view_status(chat_id, default=status)
                view = self._ld_cardview(chat_id, content, status=status,
                                        started=started, message_id=message_id)
                if footer:
                    # 调用方已经算好的页脚优先（它可能带短码）；空则保留视图自己那份
                    view.footer = footer
                status = _ld_view_status(chat_id, default=status)
                card = _cardview.entity_skeleton(view)
                card["config"]["streaming_mode"] = bool(streaming)
                # 与 legacy 车道同一层保护（审计 B 中-2）：设备字号档位必须也作用在
                # 结构化整卡上，否则 text_profile=mobile_friendly/large 时收尾会掉字号。
                card = _cards.apply_text_profile(card, _cfg_raw("text_profile"))
                if _ck_create_wall(card) is None:
                    return card
                _log_degrade_once("static-wall", _cards.count_elements(card),
                                  _cards.card_bytes(card))
            except Exception:
                logger.warning("[larkdeck] 结构化静态卡渲染失败，退回旧面板", exc_info=True)
        return self._ld_build_card(content, streaming=streaming, panel=panel, footer=footer)

    @classmethod
    def _ld_build_card(cls, content: str, *, streaming: bool,
                       panel: Optional[Dict[str, Any]],
                       footer: Optional[str]) -> Dict[str, Any]:
        """构造回复卡，超字节预算时**分级丢装饰**（面板 → 页脚 → 裸卡 → 只留状态小面板）。

        为什么不截断正文：官方把 native 流式下的长度责任明确推给适配器，而官方
        ``send()`` 本身会分块 —— 正文过大时正确做法是让卡片发送失败、由核心的
        fail-open 链回落到官方分块（退化成多条纯文本，但**答案完整**）。
        静默截断会让用户以为模型就说了这么多。
        """
        _ld_card_status_header_enabled()  # V0：生产读取配置；V2 前无观感差异
        card, tier = _cards.fit_reply_card(
            content, streaming=streaming, panel=panel, footer=footer,
            # 客户端打字机（只对流式帧有意义）：0 = 不带这个字段
            print_frequency_ms=_print_frequency_ms(),
        )
        # P1b：设备字号档位只改 config.style + 元素 text_size 引用；off/未知档位不动卡片。
        card = _cards.apply_text_profile(card, _cfg_raw("text_profile"))
        if tier != "ok":
            # 把元素数一起打出来：降载可能是**字节**触发的、也可能是**元素数**触发的
            # （飞书硬上限 200，真机实测 202 就被 230099/11310 拒），
            # 光看档位名分不出是哪一种，而两者的处置完全不同（后者要收轮数/步数）。
            _log_degrade_once(tier, _cards.count_elements(card), _cards.card_bytes(card))
        return card

    # ---------------------------------------------------------------- 发送原语
    async def _ld_send_card(self, chat_id: str, card: Dict[str, Any], *,
                            reply_to: Optional[str] = None,
                            metadata: Optional[Dict[str, Any]] = None) -> Any:
        """复用内置适配器的发送原语（含重试 / 限流 / token 处理）。

        ⚠️ **R9 审计「中-1」的落点之一：真的发出去一张卡，就在这里记一笔。**
        以前账本只在 ``_ld_stream_frame`` 里记，于是非 native 的官方卡片路径
        （``send()`` 首发、``send_clarify`` 澄清卡、``/stop`` 之后的补发）**写了卡却一个数都不加**；
        而 ``/larkdeck status`` 存在的唯一理由就是回答「插件到底在不在动」—— 那种情况下它会一边报
        「入站心跳：累计 42 条消息」、一边报「最近写卡：无记录」，而用户 DM 里明明躺着一张卡
        （审计实测 ``work/ledger_probe.py`` A 段：``send()`` 成功 ⇒ 三行全「无记录」）。
        判据仍然是「**真的发出去了一张卡**」：``success`` 为真**且**拿到 ``message_id``。
        剩下那半边（`success` 但没有 `message_id`）必须**不记** —— 那种卡在飞书侧没有落点，
        「脚本调用成功」不等于「用户看到了东西」，记成写卡就是本项目的头号病（绿而无判别力）。
        """
        response = await self._feishu_send_with_retry(
            chat_id=chat_id, msg_type="interactive",
            payload=json.dumps(card, ensure_ascii=False),
            reply_to=reply_to, metadata=metadata,
        )
        result = self._finalize_send_result(response, "larkdeck card send failed")
        if getattr(result, "success", False) and getattr(result, "message_id", ""):
            _context.note_frame_ok()
        return result

    async def _ld_update_card(self, chat_id: str, message_id: str, card: Dict[str, Any]) -> Any:
        """把 ``interactive`` 卡片整卡替换（瞬态错误退避重试）。

        必须走 **patch** 接口：``message.update`` 只收文本/帖子，卡片会被飞书拒
        （``[230001] invalid msg_type``，三种卡片方言真机实测均如此）。

        ``_ld_send_card`` 复用的内置发送原语自带重试，**patch 这条没有** —— 而它的失败
        代价最高（整回合掉 native，见 ``_TRANSIENT_CODES`` 的说明）。所以这里补一层
        **只对瞬态码**的重试；非瞬态错误立刻返回，让内核按既有 fail-open 链回落。

        ⚠️ **R9 审计「中-1」的落点之二**：整卡替换成功了就在这里记一笔（与 ``_ld_send_card``
        同一条判据）。它覆盖的**所有**调用点 —— native 的 patch 帧、CardKit 的收尾帧、
        DEGRADE 车道的整卡补写、非 native 的 ``edit_message``、``/stop`` 的中止重绘 ——
        所以帧路径里那几句 ``note_frame_ok()`` 被删掉了：**一次写只记一笔**由构造保证，
        而不是靠「记得别在调用方也写一遍」这种纪律（那种纪律会在下一个调用点静默失守）。
        """
        content = json.dumps(card, ensure_ascii=False)
        result: Any = None
        for attempt in range(len(_TRANSIENT_BACKOFF) + 1):
            request = self._ld_build_patch_request(message_id=message_id, content=content)
            response = await self._run_blocking(self._client.im.v1.message.patch, request)
            result = self._finalize_send_result(response, "larkdeck card patch failed")
            if getattr(result, "success", False):
                # 重试次数**不**进账本：账本记的是「这一次真的写出去了」，不是「发了几次 HTTP」
                # （口径见 `context.note_frame_ok` 与 README「写卡帧数」一条）。
                _context.note_frame_ok()
                return result
            if _ld_response_code(response) in _WITHDRAWN_CODES:
                # **撤回守卫**（R5）：这张消息没了（被撤回/删除，或 id 非法）⇒ 标死 + 清追踪，
                # 之后不再往它写。**绝不在这里另建卡**（我们自己补发会造成「DM 两张卡」）；
                # 要不要把内容送到用户面前，交给核心按既有 fail-open 决定。
                self._ld_drop_message(message_id, _ld_response_code(response))
                return result
            if _ld_response_code(response) not in _TRANSIENT_CODES:
                return result
            if attempt < len(_TRANSIENT_BACKOFF):
                delay = _TRANSIENT_BACKOFF[attempt]
                logger.info("[larkdeck] 卡片更新命中瞬态错误码 %s，%.1fs 后重试（第 %d 次）",
                            _ld_response_code(response), delay, attempt + 1)
                await asyncio.sleep(delay)
        return result

    def _ld_build_patch_request(self, *, message_id: str, content: str) -> Any:
        """构造 patch 请求对象（SDK 懒加载；测试替身可在实例上覆写本方法）。"""
        from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

        body = PatchMessageRequestBody.builder().content(content).build()
        return PatchMessageRequest.builder().message_id(message_id).request_body(body).build()

    # -------------------------------------------------------------------- send
    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                   metadata: Optional[Dict[str, Any]] = None, **kwargs: Any):
        """把回复渲染成卡片；任何一步出问题都回落到内置的纯文本发送。"""
        _ld_visual_engine()
        _ld_card_status_header_enabled()
        _ld_show_reasoning()
        # v0.7.0 P1：native seed 失败后的短窗口里，core 的 `_first_send` 传进来的
        # `content` 可能是 `_compose_frame_content()` 合成文本（含 terminal 命令/args）。
        # own 模式在这个窗口内只允许渲染 own 累积；空则渲染干净的流式占位卡，绝不把
        # 合成帧交给卡片或纯文本 fallback。
        guarded = (self._ld_body_source() == "own"
                   and self._ld_seed_failure_active(chat_id))
        if guarded:
            content = self._ld_own_text(str(chat_id or ""))
        fallback = lambda: super(LarkDeckMixin, self).send(  # noqa: E731
            chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs,
        )
        if not _cfg("cards") or not getattr(self, "_client", None) or (not content and not guarded):
            return await fallback()
        try:
            # 首帧没有「已耗时」可言（这一帧就是起点），所以不带 ⏱；⏱ 由后续
            # edit_message 按 t0 计算。之前这里传的是 time.monotonic()，等于
            # 恒等于 0.0s —— 属于白占一个字段，顺手修掉。
            # R6a 审计中-3：`send()` 写的是**一条完整消息**（不是流式累积帧的中间态），
            # 所以卫生在这里安全 —— 不做的后果是「同一段模型输出在两条路径上长得不一样」：
            # 走 native 收尾的回合看到降级后的标题，掉到 `send()` 的回合还露着字面 `**` 与 H1。
            # 判据是「这份文本是不是**完整文本**」，不是「这是哪条路径」（见 cards.sanitize_markdown）。
            content = _sanitize_for_send(content)
            card = self._ld_render_card(
                chat_id, content, streaming=guarded, status="completed",
                panel=self._ld_panel(chat_id, report_empty=True),
                footer=self._ld_footer(chat_id=chat_id))
            result = await self._ld_send_card(chat_id, card, reply_to=reply_to, metadata=metadata)
            if result is not None and getattr(result, "success", False):
                message_id = getattr(result, "message_id", "") or ""
                self._ld_track(message_id, chat_id)
                self._ld_note_text(message_id, content)
                if guarded:
                    self._ld_clear_seed_failure(str(chat_id or ""))
                return result
            _context.note_plaintext_fallback("send 未成功")
            logger.warning("[larkdeck] 卡片发送未成功（%s），回落纯文本",
                           getattr(result, "error", "unknown"))
        except Exception as exc:  # 卡片是增强，绝不能因为卡片把消息弄丢
            _context.note_plaintext_fallback(f"send 异常：{type(exc).__name__}")
            logger.warning("[larkdeck] 卡片发送异常，回落纯文本: %s", exc, exc_info=True)
        return await fallback()

    # ------------------------------------------------------------ edit_message
    async def edit_message(self, chat_id: str, message_id: str, content: str, *,
                           finalize: bool = False):
        """流式更新：改写我们自己发出的卡片；别人的消息交回内置实现。

        R6a 审计中-3：卫生**只加在 `finalize=True`（收尾整卡）这一侧** ——
        `finalize=False` 的每一次编辑写的是**流式累积帧的中间态**，它的文本必须与上游
        发出来的字节一致（前缀链纪律，见 `_ld_stream_frame`），改一个字符就会让用户看到
        回答被重发一遍。判据是「这份文本是不是完整文本」，不是「这是哪条调用路径」。
        """
        _ld_visual_engine()
        _ld_card_status_header_enabled()
        _ld_show_reasoning()
        state = self._ld_known(message_id)
        stream_state = (self._ld_stream_for_message(message_id)
                        if self._ld_body_source() == "own" else None)
        if not getattr(self, "_client", None):
            return await super().edit_message(chat_id, message_id, content, finalize=finalize)
        if state is None and stream_state is None:
            return await super().edit_message(chat_id, message_id, content, finalize=finalize)
        if state is None:
            # 极端路径（追踪表被淘汰等）：卡片仍在我们自己的活跃 native 流上，
            # **绝不能**把可能含合成进度的 `content` 交给 super()。用流里的 t0
            # 造一个最小追踪视图，正文选择仍走 own 规则。
            state = {"chat_id": chat_id, "t0": stream_state.get("t0"), "last": 0.0}
        try:
            if self._ld_body_source() == "own" and stream_state is not None:
                if int(stream_state.get("answer_gen") or 0) == 0:
                    try:
                        _gen = _panel.answer_generation(chat_id, require_binding=True)
                    except Exception:  # pragma: no cover - 防御性
                        _gen = 0
                    if _gen > 0:
                        _key = self._ld_stream_key_for_message(message_id)
                        if _key:
                            stream_state = {**stream_state, "answer_gen": _gen}
                            self._ld_stream_put(_key, stream_state)
                own = self._ld_stream_own_text(chat_id, stream_state)
                if finalize:
                    # core 权威终稿非空则整段采用；否则 own。
                    content = self._ld_final_body(content, own)
                else:
                    # F4 / 原生回落：interim core 文本可能含合成进度，own 模式一律不读它。
                    content = own
            if finalize:
                content = _sanitize_for_send(content)
            # ⚠️ 用 `_ld_frame_footer`（审计 C1）：这一帧**手上就有 message_id**，
            #    用基数页脚会把短码漏掉 —— 而「截图 ↔ 日志」对齐正是短码存在的唯一理由。
            card = self._ld_render_card(
                chat_id, content, streaming=not finalize,
                status="completed" if finalize else "processing",
                panel=self._ld_panel(chat_id, report_empty=bool(finalize)),
                footer=self._ld_frame_footer({"message_id": message_id,
                                              "chat_id": state.get("chat_id") or chat_id,
                                              "t0": state.get("t0")}),
                started=state.get("t0"), message_id=message_id,
            )
            result = await self._ld_update_card(chat_id, message_id, card)
            if result is not None and getattr(result, "success", False):
                self._ld_note_text(message_id, content)
                if finalize:
                    self._ld_forget(message_id)
                return result
            logger.warning("[larkdeck] 卡片更新未成功（%s），回落内置编辑",
                           getattr(result, "error", "unknown"))
        except Exception as exc:
            logger.warning("[larkdeck] 卡片更新异常，回落内置编辑: %s", exc, exc_info=True)
        return await super().edit_message(chat_id, message_id, content, finalize=finalize)

    # ------------------------------------------------- native 流式（官方契约）
    #: Hermes 官方 native streaming 协议（gateway/stream_consumer_transport.py 消费）：
    #: 启用后工具进度合入流式帧、工具边界不再另发消息 —— 一回合一张卡的正规路径。
    #: 任何一帧失败都会让核心自动禁用 native 并回落 send/edit（消息不会丢）。
    SUPPORTS_NATIVE_STREAMING = True

    def supports_native_streaming(self, chat_type: Optional[str] = None,
                                  metadata: Optional[Dict[str, Any]] = None) -> bool:
        """保守探测：配置关了卡片 / native / 没有 SDK 客户端时返回 False，核心走老路径。"""
        _ld_visual_engine()  # V0：公共探测入口也读一次，覆盖 native 关闭/早退路径
        return (bool(_cfg("cards")) and bool(_cfg("native_streaming"))
                and bool(getattr(self, "_client", None)))

    async def send_stream_frame(self, text: str, *, finalize: bool = False,
                                chat_id: Optional[str] = None,
                                reply_to: Optional[str] = None,
                                **kwargs: Any) -> bool:
        """官方流式帧：``text`` 是**累积全文**，整卡替换到同一张卡片。

        seed 帧（空文本）建卡；普通帧原地更新；finalize 收尾。返回 False 时
        核心自动禁用 native 并回落 send/edit —— 卡片失败绝不丢消息。
        """
        turn = str(kwargs.get("turn_id") or "")
        try:
            ok = await self._ld_stream_frame(
                text, finalize=finalize, chat_id=chat_id, reply_to=reply_to,
                turn_id=turn,
            )
        except Exception as exc:
            # 不能只打异常日志就 return False：核心同样会因为 False 停用本回合的 native，
            # 而「native 被停用 → 输出回落 send/edit（可能变成多条纯文本）」这条最强诊断
            # 会缺失。走 `_ld_stream_fail` 让「为什么掉 native」始终留痕（限流 30s 一条）。
            logger.warning("[larkdeck] native 流式帧异常，交由核心回落", exc_info=True)
            self._ld_remember_failed_frame(chat_id, turn, text)
            return self._ld_stream_fail(f"帧处理异常：{exc}")
        if not ok and not finalize:
            # v0.7.0 P1：记下失败帧文本（own 模式 F4 识别用），并打开“native 回落窗口”。
            # 任何 non-finalize 帧失败（不只空 seed）都会让 core 可能 `_first_send(合成文本)`；
            # own 模式 send() 必须在这个短窗口内拒绝 core 合成进度/密钥当正文。
            self._ld_remember_failed_frame(chat_id, turn, text)
            self._ld_note_seed_failure(str(chat_id or ""), turn)
        elif ok and not finalize:
            self._ld_clear_seed_failure(str(chat_id or ""))
        return ok

    def _ld_remember_failed_frame(self, chat_id: Optional[str], turn_id: str, text: str) -> None:
        """在 stream state 里记下最近一次失败帧原文（只用于识别 F4 重发）。"""
        chat = str(chat_id or "").strip()
        if not chat:
            return
        key = f"{chat}:{turn_id}" if turn_id else chat
        state = self._ld_stream_get(key)
        if state is None:
            return
        self._ld_stream_put(key, {**state, "last_failed_frame": str(text or "")})

    # ------------------------------------------------- CardKit 传输（阶段 9）
    @staticmethod
    def _ld_transport() -> str:
        """本回合的 native 帧走哪条传输。认不出的值按 ``patch`` 处理（不猜、不抛）。"""
        return "cardkit" if str(_cfg_raw("native_transport") or "").strip() == "cardkit" else "patch"

    @staticmethod
    def _ld_ck_requests() -> Any:
        """懒取 CardKit 的请求构造（Hermes 环境里才有 ``lark_oapi``）。

        **取不到就返回 None** ⇒ 调用方 fail-open 回落（单测环境正是这种情况：`test_units.py`
        是零 Hermes 依赖的，所以这里绝不能把 SDK 变成模块级 import —— 那会让单测直接崩）。
        """
        try:
            from lark_oapi.api.cardkit.v1 import (CreateCardRequest, CreateCardRequestBody,
                                                  ContentCardElementRequest,
                                                  ContentCardElementRequestBody,
                                                  BatchUpdateCardRequest,
                                                  BatchUpdateCardRequestBody)
            from lark_oapi.api.im.v1 import (CreateMessageRequest, CreateMessageRequestBody,
                                              ReplyMessageRequest, ReplyMessageRequestBody)
        except Exception:
            return None
        # ⚠️ **会话预览的两个模型必须单独 import**（R7 审计的留白第 5 条）：它们与上面那几个
        # 同属 CardKit 命名空间，但**能力等级完全不同** —— 缺了它们只该「关掉预览」，
        # 而混在同一个 try 里会让**整条 CardKit 传输**（建实体 + 元素写 + batch）一起被关掉、
        # fail-open 回 patch：用户失去打字机，而他根本没开过预览。
        # 缺了就在 `settings_card` 上抛一个说得清的错误 ⇒ 被 `_ld_ck_maybe_summary` 的
        # try/except 接住、只标死预览（这正是 H-1 那条修复的顺带收益）。
        try:
            from lark_oapi.api.cardkit.v1 import (SettingsCardRequest,
                                                  SettingsCardRequestBody)
        except Exception:
            SettingsCardRequest = SettingsCardRequestBody = None       # type: ignore[assignment]

        def _entity_payload(card_id: str) -> str:
            return json.dumps({"type": "card", "data": {"card_id": card_id}}, ensure_ascii=False)

        def _settings_card(card_id: str, payload: str, sequence: int,
                           uuid_value: str) -> Any:
            """`card.settings` 请求；老 SDK 缺这两个模型时**抛一个说得清的错误**。

            为什么抛而不是返回 None：调用方（`_ld_ck_settings`）的契约是「拿到响应对象」，
            返回 None 会让 `_ld_response_code(None)` 得到 0 —— 也就是**假成功**（预览静默不更新，
            还没有任何日志）。抛出去会被 `_ld_ck_maybe_summary` 的 try/except 接住 ⇒ 只标死预览、
            留下真实原因，整条 CardKit 传输不受影响。
            """
            if SettingsCardRequest is None or SettingsCardRequestBody is None:
                raise RuntimeError("这个版本的 lark_oapi 没有 SettingsCardRequest"
                                   "（会话预览不可用，其它 CardKit 能力不受影响）")
            return SettingsCardRequest.builder().card_id(card_id).request_body(
                SettingsCardRequestBody.builder().settings(payload)
                .sequence(sequence).uuid(uuid_value).build()).build()

        # ⚠️ `uuid` 是**请求去重键**（官方发送路径一直在填）：我们这条路径的写调用会退避重试，
        # 没有它的话「响应丢了但其实发成功了」会重发一次 ⇒ DM 里多一张实体卡，
        # 而那张卡永远收不到元素写入、也没人收尾（永久停在「⏳ 正在生成…」）。
        # 它的值必须由内容确定（同一次发送重试时不变），所以用 `ld-msg-<card_id>`。
        return SimpleNamespace(
            create_card=lambda card_json: CreateCardRequest.builder().request_body(
                CreateCardRequestBody.builder().type("card_json").data(card_json).build()
            ).build(),
            send_entity=lambda receive_id, card_id: CreateMessageRequest.builder()
            .receive_id_type("chat_id").request_body(
                CreateMessageRequestBody.builder().receive_id(receive_id)
                .msg_type("interactive").uuid(f"ld-msg-{card_id}")
                .content(_entity_payload(card_id)).build()).build(),
            # 有回复锚点时走**回复**接口 —— 与 patch 传输同形（那边交给官方 `_send_raw_message`，
            # 它在没有 thread 元数据时也是 `reply_in_thread=False`）。丢了锚点 = 回答不再挂在
            # 提问下面；话题群里更糟，可能自成一条新话题（第十二路审计实测到锚点被整个丢掉）。
            reply_entity=lambda reply_to, card_id: ReplyMessageRequest.builder()
            .message_id(reply_to).request_body(
                ReplyMessageRequestBody.builder().msg_type("interactive")
                .reply_in_thread(False).uuid(f"ld-msg-{card_id}")
                .content(_entity_payload(card_id)).build()).build(),
            batch_update=lambda card_id, actions, sequence, uuid_value:
            BatchUpdateCardRequest.builder().card_id(card_id).request_body(
                BatchUpdateCardRequestBody.builder()
                .actions(json.dumps(actions, ensure_ascii=False))
                .sequence(sequence).uuid(uuid_value).build()).build(),
            # `card.settings`：改**卡级 config**（R7 用它更新会话列表预览）。
            # ⚠️ 老 SDK 没有这两个模型时**只关预览**（抛错 → 被上层 try/except 接住 → 只标死），
            # 绝不让它把整条 CardKit 传输拖下去 —— 见上面那段拆分的理由。
            # ⚠️ `summary` 必须是 **i18n 对象** `{"content": …}`：传裸字符串会被拒 `300122`（真机实测）。
            settings_card=_settings_card,
            write_element=lambda card_id, element_id, content, sequence, uuid_value:
            ContentCardElementRequest.builder().card_id(card_id).element_id(element_id)
            .request_body(ContentCardElementRequestBody.builder().content(content)
                          .sequence(sequence).uuid(uuid_value).build()).build(),
        )

    async def _ld_ck_create(self, chat: str, *, answer: str, panel_text: str,
                            panel_tools_text: str,
                            reply_to: Optional[str] = None,
                            structured_view: Optional["_cardview.CardView"] = None) -> Any:
        """建 CardKit 实体 + 发实体卡。返回 ``(result, card_id, card_json)`` 或 ``None``。

        ⚠️ 第三个返回值是**建出来的那张卡的 JSON**：回合状态的元素表要从它里面抽
        （`_ck_elems_from_card`）—— 那是「结构」的唯一来源，不能靠再读一遍配置去猜。

        走的是官方三个接口（真机实测每一步都 ``code=0``，见 `docs/plan-6-effects.md` 阶段 9）：
        ``cardkit.v1.card.create`` → ``im.v1.message.create``（content 是
        ``{"type":"card","data":{"card_id":…}}``）。

        ⚠️ **页脚元素「要不要」的判据在函数体内的那一行（按配置），不是调用方传进来的** ——
        这里曾经有一个 `footer_text` 形参，而它**从来没被读过**（形参被函数体里那行写死的表达式
        盖掉了）。2026-09-16 审计实测：那是「同一件事两处真相」的静默形态 —— 调用方以为自己在
        决定页脚元素的有无，其实一个字节都没影响。形参与它唯一的生产者
        `_ld_seed_footer_text()` 都已删除，判据只剩这一处。
        """
        _ld_card_status_header_enabled()  # V0：cardkit 建实体入口也读一次
        reqs = self._ld_ck_requests()
        if reqs is None:
            return None                      # 没有 SDK ⇒ fail-open 回落（不猜、不抛）
        if structured_view is not None:
            # V1：结构化元素树（canary）。结构在 entity_skeleton 里一次定死。
            card = _cardview.entity_skeleton(structured_view)
            # V4.5：`streaming_print_ms` 在这条车道上也要生效（审计 B 中-4：结构化元素树里
            # 一直没有这个表达式，等于用户在结构化下把这个开关设了也白设）。
            _ms = _print_frequency_ms()
            if isinstance(_ms, int) and _ms > 0:
                card["config"]["streaming_config"] = _cards.streaming_config(_ms)
        else:
            card = _cards.cardkit_entity_card(answer, panel_text, streaming=True,
                                             expanded=bool(_cfg("panel_expanded")),
                                             panel=bool(_cfg("unified_panel")),
                                             # R3 收窄版：工具块是面板里的第二个元素（建实体时定死）
                                             panel_tools_text=panel_tools_text,
                                             # `footer: false` ⇒ 传 None ⇒ 页脚元素**不进卡**；
                                             # 开着但这一刻还没数据 ⇒ 空串 ⇒ 元素留在卡里等后续帧更新。
                                             # ⚠️ **判据是「要不要这个元素」（配置），不是「这一刻有没有
                                             # 数据」** —— 这里必须是**写死的表达式**，不能是
                                             # `self._ld_footer()`：那个函数在本进程第一次 API 调用之前
                                             # 返回 `None`（页脚指标来自官方钩子），而
                                             # `cardkit_entity_card` 的纪律是 `None ⇒ 元素不进卡`，
                                             # 元素表在建实体时定死 ⇒ 那一回合**永远没有页脚、也永远
                                             # 没有短码**（短码就写在页脚里）。变异 `G2-9` 钉这一行。
                                             footer_text=("" if _cfg("footer") else None))
        # P1b：设备字号档位（建实体时定死 text_size；之后只写内容，不做结构性 patch）。
        card = _cards.apply_text_profile(card, _cfg_raw("text_profile"))
        # ⚠️ **基线闸门（两道墙）**：patch 路径超预算会分级丢装饰（面板→页脚→裸卡），而 cardkit
        # 的结构**在建实体时定死、之后不能改**，超了就是「整卡被飞书拒（230099 / 300305）⇒
        # 这一帧什么都没了」。所以这里守**实测硬上限**，超了就 fail-open 交给核心回落。
        wall = _ck_create_wall(card)
        if wall == "字节":
            _log_ck_over_budget_once(_cards.card_bytes(card))
            return None
        if wall == "元素":
            _log_ck_elements_over_once(_cards.count_elements(card))
            return None
        made = await self._ld_write_with_retry(
            lambda: reqs.create_card(json.dumps(card, ensure_ascii=False)),
            self._client.cardkit.v1.card.create, "建实体")
        card_id = getattr(getattr(made, "data", None), "card_id", None)
        if _ld_response_code(made) != 0 or not card_id:
            # R11-B2：**按内层码留痕**。这里以前是**静默** `return None`，帧路径只会说一句
            # 「CardKit 建实体/发实体卡失败」⇒ 「容量到顶（服务端 300305，因为本地闸门是
            # **本地**递归计数、可能与服务端口径漂移）」和「我们发了一份非法卡 JSON」在日志里
            # 长得一模一样。两种都是**确定性失败**（`_WRITE_RETRY_CODES` 里没有它们），
            # 但排查方向完全相反，所以必须点名。
            _log_ck_reject_once(
                _CkResult(False, _ld_response_code(made),
                          str(getattr(made, "msg", "") or "")), "建实体")
            return None
        anchor = str(reply_to or "").strip()
        if anchor:
            sent = await self._ld_write_with_retry(
                lambda: reqs.reply_entity(anchor, card_id),
                self._client.im.v1.message.reply, "回复实体卡")
        else:
            sent = await self._ld_write_with_retry(
                lambda: reqs.send_entity(chat, card_id),
                self._client.im.v1.message.create, "发实体卡")
        result = self._finalize_send_result(sent, "larkdeck cardkit send failed")
        if not getattr(result, "success", False):
            return None
        return result, str(card_id), card

    async def _ld_ck_write(self, card_id: str, element_id: str, content: str,
                           sequence: int) -> "_CkResult":
        """往实体卡的某个元素里写文本（**这是打字机的写入通道**）。

        ⚠️ 序号必须**单调递增**：用 ``settings`` 重开会话后序号没对齐会拿到
        ``300317``（真机实测）。这里由调用方在 stream state 里维护一个计数器。

        ⚠️ ``uuid`` 由 (卡, 元素, 序号) **确定性**推出 ⇒ 限流重试是**幂等**的：
        同一个 uuid 再发一次，服务端认得出是同一次写入（`write_element` 的第五个参数）。
        """
        reqs = self._ld_ck_requests()
        if reqs is None:
            # 没有 SDK ⇒ **不是**「卡死了」，是这一帧没法写：码给 0 会让调用方以为成功，
            # 所以这里合成一个「失败但无码」的结果（`code=0` 与 `ok=False` 的组合只有这一处）。
            return _CkResult(False, 0, "没有 CardKit SDK")
        resp = await self._ld_write_with_retry(
            lambda: reqs.write_element(card_id, element_id, content, int(sequence),
                                       f"ld-{card_id}-{element_id}-{sequence}"),
            self._client.cardkit.v1.card_element.content,
            f"写元素 {element_id}")
        return _CkResult(_ld_response_code(resp) == 0, _ld_response_code(resp),
                         str(getattr(resp, "msg", "") or ""))

    async def _ld_ck_settings(self, card_id: str, summary: Dict[str, Any],
                              seq: int, *, retry: bool = True,
                              state_ref: Optional[Dict[str, Any]] = None) -> "_CkResult":
        """写**卡级 config** 的 `summary`（R7 的「进展」：会话列表里那行预览文字）。

        真机实测的两条硬约束（`probe_ck_stream_ops.py --p3`）：
          * `summary` 必须是 **i18n 对象** `{"content": …}` —— 传裸字符串会得 `300122`；
          * `card.settings` **吃一个序号**，与元素写入**共用**同一个计数器。
        所以调用方必须把 `seq + 1` 写回状态；本函数本身不碰状态（与 `_ld_ck_batch` 同形）。
        """
        reqs = self._ld_ck_requests()
        if reqs is None:
            return _CkResult(False, 0, "没有 CardKit SDK")
        payload = json.dumps({"config": {"summary": summary}}, ensure_ascii=False)
        def make_request() -> Any:
            return reqs.settings_card(card_id, payload, int(seq), f"ld-{card_id}-s{seq}")

        if retry:
            resp = await self._ld_write_with_retry(
                make_request, self._client.cardkit.v1.card.settings, "会话预览")
        else:
            # ⚠️ `retry=False` 是**预览专用**（R7 审计低-7）：这次写排在**正文写之后**（提交点
            # 之后），撞限流时退避重试只会给这一帧白加最多 ≈1.0s（0.1+0.3+0.6），而失败已经
            # 只标死 ⇒ 重试的收益是**零**。元素写入与建实体照旧带重试：那些失败会让整帧失败。
            resp = await self._run_blocking(self._client.cardkit.v1.card.settings, make_request())
        # 预览也是**元素通道的逻辑写**（`card.settings` 吃一个序号、算一次配额）⇒ 记进滑窗，
        # 否则滑窗会比真实写入率少 ≈0.2 次/秒（附录 B 的 8.2 就是这么算出来的，R11-B1）。
        # ⚠️ 拿不到回合状态时**干脆不记**（而不是记到一个临时容器里）：守卫宁可少算一次，
        # 也不给自己虚报配额 —— 记到假容器里等于把「这一秒还欠多少」变成一句谎话。
        # ⚠️ `retry=True` 那一支今天**没有生产调用方**（`_ld_ck_maybe_summary` 恒用 `retry=False`，
        # B1 审计低-5 实测）：留着它是这个通用写入口的对称面，但别以为它会被走到 ——
        # 真要启用得同时决定「重试的那几次怎么记账」。
        if isinstance(state_ref, dict):
            _ck_window_note(state_ref, time.monotonic())
        return _CkResult(_ld_response_code(resp) == 0, _ld_response_code(resp),
                         str(getattr(resp, "msg", "") or ""))

    @staticmethod
    def _ld_ck_elems(state: Dict[str, Any]) -> List[str]:
        """这张实体卡**实际有哪些元素**（建实体时的决定，之后只读）。

        ⚠️ 这里**故意不做**「老状态兼容」：`ck_elems` 之前用的是 `ck_panel` 布尔，而
        `self._ld_streams` 是**纯进程内**状态 —— 换了代码就必须重启网关（否则跑的是旧模块），
        所以「老状态的回合」在新代码里根本不可能存在。留一个没人测的兼容分支比删掉它更危险
        （R1 审计的 D9：把兼容分支改坏，四门禁全绿）。真拿到坏状态就返回空表，
        由 `_ld_ck_apply` 把「空 ops」当契约违反处理（帧失败 + 留痕），而不是静默冻卡。
        """
        elems = state.get("ck_elems")
        if isinstance(elems, (list, tuple)):
            return [str(e) for e in elems]
        return []

    async def _ld_ck_batch(self, card_id: str, ops: Sequence["_CkOp"],
                           seq: int) -> "_CkResult":
        """一次 `card.batch_update` 写多个装饰元素（每帧最多这一次 + 正文一次）。

        真机实测（R0）：流式期间可用、**不关会话**、只占**一个** sequence。
        """
        reqs = self._ld_ck_requests()
        if reqs is None:
            return _CkResult(False, 0, "没有 CardKit SDK")
        actions = [{"action": "partial_update_element",
                    "params": {"element_id": op.element_id,
                               # ⚠️ `tag`/`text_size` 这类**结构性**字段不能进 partial update
                               # （飞书只允许改内容），所以只传 content。
                               "partial_element": {"content": op.content}}}
                   for op in ops]
        resp = await self._ld_write_with_retry(
            lambda: reqs.batch_update(card_id, actions, int(seq), f"ld-{card_id}-b{seq}"),
            self._client.cardkit.v1.card.batch_update, "装饰元素")
        return _CkResult(_ld_response_code(resp) == 0, _ld_response_code(resp),
                         str(getattr(resp, "msg", "") or ""))

    @staticmethod
    def _ld_ck_mark_dead(state_ref: Dict[str, Any], ops: Sequence["_CkOp"]) -> None:
        """把写失败的**装饰**元素标死：本回合后续帧不再写它们（省配额、也不再刷日志）。

        只标装饰：正文失败是整帧失败（fail-open 交核心），没有「下帧再试」这回事。

        ⚠️ 粒度是**整批**，不是「只标真的坏了的那个」（R2 审计第 3 条指出与附录 A 的措辞不一致）：
        `batch_update` 的**返回码是卡级的**，一批里哪个 action 失败从返回码上看不出来，所以
        唯一能诚实做的处置就是「这一批全标死」。代价被两件事兜住：① 冻结**只限本回合**
        （收尾帧走整卡 patch，面板/页脚/状态色一次补齐）；② 这条路本来几乎不会撞上
        （真机 50 次/秒连打零失败）。附录 A 的措辞已按这条改准。
        """
        if not isinstance(state_ref, dict):
            return
        dead = state_ref.get("ck_dead")
        if not isinstance(dead, set):
            dead = set()
        dead.update(op.element_id for op in ops)
        state_ref["ck_dead"] = dead

    async def _ld_ck_apply(self, card_id: str, ops: Sequence["_CkOp"], seq: int,
                           state_ref: Dict[str, Any]) -> Tuple[bool, int, Optional["_CkOp"]]:
        """按顺序写一批元素；返回 ``(是否全部成功, 用掉之后的序号, 失败的那个 op)``。

        ⚠️ **序号只增不减**：调用方必须把返回的序号**无条件写回状态**，哪怕中途失败 ——
        回退序号会让下一帧用同一个号，而 `uuid` 是由 (卡, 元素, 序号) 推出来的 ⇒ 服务端按
        去重键处理（**可能返回 0 但内容没变** = 静默半更新）。这条规矩来自第十二路审计。

        ⚠️ **「只增不减」到底测到了什么**（R2 审计指出旧注释把三种形状混成了一句，这里按实测重写）：
          * **撞号**（同一个号发两次）⇒ 实测 `300317`；
          * **回退 / 大跳之后没对齐**（`card.settings(seq=100)` 之后发 `content(seq=2)`）⇒ 实测 `300317`；
          * **前向空洞**（例如 5 之后直接发 7）**没有单独实测过** —— 旧注释那句「跳号也回 300317」
            说的是上面第二条，别当成「必须有连续」来引用。
        我们的实现里**不会出现空洞**：装饰与正文共用一个 `+1` 计数器（真机 ledger 实测 1..6 连续、
        2/3/4/6 只是**正文那一列**的值）。测试里钉的是「严格递增 + 不撞号 + 不跳号」这个
        **实现性质**，它比服务端实测到的那条更强 —— 但这个更强是白拿的（我们本来就只 +1）。
        """
        decor, answer = _ck_split(ops)
        # **未变化不重写**（R2 规则）：装饰只在内容**真的变了**时才发那一次 batch。
        # 收益是写入预算的一半：一帧里真正会变的只有正文，面板/页脚往往好几帧不动
        # （面板等新工具/新推理数据，页脚等下一次 API 请求），稳态下每帧只写 1 次。
        # ⚠️ 判据是 state 里的 `ck_decor`（**已确认写成功**过的那份内容），不是「上一帧算出来的
        # 内容」—— 拿「算出来的」当已写会把「其实没写成功」的装饰永久静默冻结（本项目的头号
        # 失败模式），所以记账只在 batch 返回成功后发生（见下面那行）。
        sent = state_ref.get("ck_decor")
        sent = sent if isinstance(sent, dict) else {}
        fresh = [op for op in decor if sent.get(op.element_id) != op.content]
        if fresh and not _ck_window_allow(state_ref, time.monotonic()):
            # ── **滑窗写入守卫**（R11-B1）：这一秒的配额已经用掉 10 次 ⇒ **让出这次装饰**。
            # 两条边界必须同时守住（各有一条变异钉住）：
            #   ① **不置 `ck_decor`** —— 记账只发生在 batch 真的写成功之后（下面那个 `else:`）。
            #      在这里记一笔就等于把「没写」说成「写了」，去重逻辑随后会**永久跳过**这些
            #      元素 = 装饰静默冻结（本项目最怕的失败形态：看得见、没日志、也不回落）。
            #   ② **跳过 ≠ 失败**：本帧照旧返回 `True`（下面那段不动）—— 装饰是增强，
            #      为它把整帧判失败会买下「核心停用本回合 native ⇒ 用户掉成纯文本」这条链。
            # ⚠️ **正文（提交点）永远不跳**：帧文本是累积全文，核心按「这一帧送达」乐观记账，
            # 跳过正文 = 用户永远看不到那一段（finalize 帧尤其致命）。所以这个守卫**不是**
            # 硬限速器，它只把**可重放**的装饰写让出来；正文与预览照发，极端情况下窗口仍可能
            # 被正文顶破 —— 那是有意的取舍（宁可多花一次配额，也不能让卡片停在半截）。
            state_ref["ck_window_skips"] = int(state_ref.get("ck_window_skips") or 0) + 1
            _log_ck_window_skip_once([op.element_id for op in fresh],
                                     len(_ck_window(state_ref)))
        elif fresh:
            # 装饰**一次 batch 发走**（写入预算：每帧 ≤2 次；R0 实测 batch 只占 1 个 sequence）
            seq += 1
            batch_res = await self._ld_ck_batch(card_id, fresh, seq)
            _ck_window_note(state_ref, time.monotonic())   # 真的发出去了（成功与否都占配额）
            if not batch_res.ok:
                # 装饰失败**不 fail-open**（处置矩阵：`DEAD` + 限流 WARNING，帧继续）：
                # 把装饰失败升级成整帧失败，会买下「上游补 finalize + `_first_send` ⇒ DM 两张卡」
                # 这条链；而静默吞掉又是本项目的头号失败模式 —— 唯一同时满足两边的形态是
                # 「不 fail-open 但必须留痕」。正文失败仍然 fail-open（没有正文这张卡就没意义）。
                #
                # ⚠️⚠️ **卡级死法在装饰这一路也要降级**（R5 审计的高-1，实测能绕过全部门禁）：
                # 旧代码只在**正文**写入失败时查 `_CARD_DEATH_CODES`。于是「批量写入先撞上
                # `300309`（会话已关）」这条形状会走 DEAD 分支：整批标死、帧照旧返回 True、
                # `ck_degrade` 永远不置位 ⇒ 后续每一帧都往一个**已经关掉的会话**写正文，
                # 卡片静默冻在流式态 —— 正是 R5 要治的病。所以这里先把降级决定记下来，
                # 由帧路径统一处理（帧路径会清 `card_id` 并改用整卡 patch 续写同一张卡）。
                if batch_res.code in _CARD_DEATH_DECOR_CODES:
                    state_ref["ck_degrade"] = batch_res.code
                # ⚠️ **标死范围尽量收窄**（R5，真机实测支撑）：批级返回码能反映坏 id，而且
                # **msg 会点名它**（`ErrMsg: not find elementID : <id>`，`--lanes` 实测
                # `code=300313`）⇒ 解析得出**且真的在这一批里**就只标那一个，其余装饰下一帧照常写。
                # ⚠️ 「解析得出」不够（R5 审计的中-4）：msg 形状不止一种，实测有两个反例 ——
                # ① 解析结果被截断（`ghost_missing_element` ⇒ `ghost_missing_elemen`）；
                # ② 误伤（`elementID : panel_body … (the offending field is footer)` 里点名的是
                #    上下文而不是真凶）。两种情况都会「标死一个好元素、放走坏元素」，且日志会撒谎。
                # 所以判据是**解析出的 id 必须命中这一批的 op**，否则一律退回整批标死（保守）。
                blamed = batch_res.bad_element_id()
                hit = [op for op in fresh if op.element_id == blamed] if blamed else []
                dead_ops = hit or list(fresh)
                self._ld_ck_mark_dead(state_ref, dead_ops)
                _log_ck_decor_write_failed_once(dead_ops, batch_res.code,
                                                blamed if hit else None)
            else:
                # 记账 = 「卡上现在是这个内容」。所以 ① 只记**这一次真的发出去的** op
                # ② **合并**历史：本帧只写面板（页脚没变）时，页脚的记录不能被本帧抹掉，
                # 否则下一帧会把没变的页脚再写一次（去重记账自己戳出一个洞）。
                # ⚠️ **记账必须在 `else:` 里（成功分支），不许挪到 batch 调用之前**：挪上去之后
                # 「装饰没写成功」会被记成「写成功了」，接着它就会被去重逻辑永久跳过 = 静默冻结。
                # 今天这个挪动**会被 `ck_dead` 掩蔽**（同一元素随即被标死），所以门禁一度抓不住它
                # （R2 审计实测：挪到前面 132/132 全绿）—— 现在由用例 ②b 直接断言
                # 「装饰失败那一帧**没有**任何 `ck_decor` 记账」把它钉住（变异 `R2-13`）。
                state_ref["ck_decor"] = {**sent,
                                         **{op.element_id: op.content for op in fresh}}
        if answer is None and not fresh:
            # **一帧里一个 op 都写不出去 ⇒ 契约违反**，不是「没什么可写」：元素表为空、表里全是
            # 卡里没有的 id、或者装饰全被判成「未变化」而计划里又没有正文 —— 这几种都意味着
            # 这张卡这一帧长不动，而返回成功会让核心以为一切正常：**卡片静默冻死、一行日志都
            # 没有、也不回落**（R1 审计的 U1）。所以按失败处理。
            # 这条检查**放在过滤之后**（覆盖上面所有形状），只此一处；`not ops` 与它等价 ——
            # 两处都写会让「撤掉这条修复」的单点变异打不中（实测：留着旧的那行，U1 变异全绿）。
            # 正文 op 是**故意不做去重**的：帧只在 `text` 变了时才走到这里，正文内容必然是新的；
            # 给它也加去重就等于把「这一帧到底写没写」变成猜测。
            return False, seq, None
        if answer is not None:
            seq += 1
            wrote = await self._ld_ck_write(card_id, answer.element_id, answer.content, seq)
            # 正文这一次逻辑写**永远不跳**（它是提交点，见上面守卫那段说明），但照旧记进滑窗：
            # 守卫要算的是「这一秒真的欠了飞书多少次写」，漏记正文等于给自己虚报余量。
            _ck_window_note(state_ref, time.monotonic())
            if not wrote.ok:
                # **卡级死法 ⇒ 转 patch 车道**（R5，处置矩阵的 `DEGRADE`）：这张实体卡的元素通道
                # 已经不可用（会话被关 / 序号冲突 / 元素没了），但消息本身还在 ⇒ 用 `message.patch`
                # 把**同一张卡**换成普通卡继续写（真机实测 patch 能覆盖实体卡消息）。
                # 只把决定记进 state，**真正的降级动作在帧路径上做**（那里才有 patch 的代码路径）：
                # 这里负责「不吞掉这个事实」。
                if wrote.code in _CARD_DEATH_CODES:
                    state_ref["ck_degrade"] = wrote.code
                # P1a：把服务端返回码**回填到失败 op** 上。调用方原先把它当 `_CkResult`
                # 调 `inner_code()` ⇒ AttributeError ⇒ 外层只留「帧处理异常」，具体元素
                # 与返回码一起丢。失败 op 仍是 `_CkOp`（角色/元素名不能丢），额外带 code。
                return False, seq, answer._replace(code=wrote.code)
        return True, seq, None

    async def _ld_ck_split(self, chat: str, text: str, state: Dict[str, Any],
                           offset: int, reply_to: Optional[str],
                           now: float) -> Optional[Dict[str, Any]]:
        """**封旧卡 + 开新卡**（R4）：成功返回新卡那一份回合状态，失败返回 None（调用方 fail-open）。

        为什么值得为它多花写入配额：不切卡的话，正文一超过单卡硬上限，这一帧就只能 fail-open
        ⇒ 核心停用本回合 native ⇒ 用户从「逐字卡片」掉成一条条纯文本（卡上还停在半截）。
        超长回答（约四万字以上）恰恰是最需要卡片的时候。

        ⚠️ **这一帧的写入次数故意超出「每帧 ≤2 次」的预算**（封卡 patch + 建实体 + 发实体卡 +
        元素写）：切卡是**低频事件**（几万字才一次），而那条预算规则是给「稳态每 0.25 秒一帧」
        定的 —— 宁可多花一次配额，也不要丢内容或掉回纯文本。这条例外写在这里，别当成预算被无视。
        """
        # ⚠️ 封卡预算**必须与帧路径的触发阈值同源**（`_CK_SPLIT_SEAL_AT`）：两处各算一次的话，
        # 阈值一改就会出现「触发了但切点说『整段都装得下』」这种自相矛盾（实测踩到过）。
        seal_budget = int(_CK_SPLIT_SEAL_AT)
        tail_budget = int(_cards.FEISHU_CARD_BYTE_LIMIT * _CK_SPLIT_TAIL_RATIO)
        cut = _ck_split_point(text, offset, seal_budget=seal_budget, tail_budget=tail_budget)
        if cut is None:
            return None
        old_message_id = str(state.get("message_id") or "")
        # ① 封旧卡：整卡 patch（那一刻流式本来就结束 —— 我们**绝不会**再往它写元素）
        sealed = text[offset:cut]
        if _ld_visual_engine() == "structured":
            sealed_view = self._ld_cardview(
                chat, sealed + "\n\n" + _i18n.t("stream.continued"), status="completed",
                started=state.get("t0"), message_id=old_message_id)
            card = _cards.apply_text_profile(_cardview.entity_skeleton(sealed_view),
                                             _cfg_raw("text_profile"))
        else:
            card = self._ld_build_card(sealed + "\n\n" + _i18n.t("stream.continued"),
                                       streaming=False,
                                       panel=self._ld_panel(chat, report_empty=True),
                                       footer=self._ld_footer(chat_id=chat,
                                                              started=state.get("t0")))
        result = await self._ld_update_card(chat, old_message_id, card)
        if result is None or not getattr(result, "success", False):
            logger.warning("[larkdeck] 卡链：封旧卡失败（%s），本帧回落",
                           getattr(result, "error", "unknown"))
            return None
        # 封掉的卡不再追踪：`/stop` 的重绘只针对**最新那张**（多卡同时变色列入后续阶段）。
        # 但它的 id 留在 `ck_cards` 里 —— 那是「这一回合发过哪几张卡」的唯一记录。
        self._ld_forget(old_message_id)
        # ② 开新卡：只写**剩下的那一段**（写整段会让用户把前半段再看一遍 —— R4 最大的观感坑）
        new_body, new_tools = self._ld_panel_parts(chat)
        structured = _ld_visual_engine() == "structured"
        if structured:
            new_view = self._ld_cardview(chat, text[cut:])
            made = await self._ld_ck_create(chat, answer=text[cut:],
                                            panel_text=new_body, panel_tools_text=new_tools,
                                            reply_to=reply_to, structured_view=new_view)
        else:
            made = await self._ld_ck_create(chat, answer=text[cut:],
                                            panel_text=new_body, panel_tools_text=new_tools,
                                            reply_to=reply_to)
        if made is None:
            logger.warning("[larkdeck] 卡链：开新卡失败，本帧回落")
            return None
        new_result, new_card_id, new_card_json = made
        new_message_id = getattr(new_result, "message_id", "") or ""
        if not new_message_id:
            logger.warning("[larkdeck] 卡链：新卡没拿到 message_id，本帧回落")
            return None
        self._ld_track(new_message_id, chat)
        logger.info("[larkdeck] 卡链：封 %s（%d 字）→ 新卡 %s（余 %d 字）",
                    old_message_id[-8:], len(sealed), new_message_id[-8:], len(text) - cut)
        new_state = {
            **state,
            "message_id": new_message_id, "chat_id": chat, "t0": state.get("t0") or now,
            "last": text, "last_at": now, "frames": 0, "skipped": 0,
            "card_id": new_card_id, "ck_seq": 0,
            "ck_elems": _ck_elems_from_card(new_card_json),
            "ck_summary_at": now, "ck_summary": _cards.summary_text(text),
            # 新卡的正文从 cut 开始 ⇒ 之后每一帧都按 `text[ck_offset:]` 渲染
            "ck_offset": cut,
            "ck_cards": list(state.get("ck_cards") or []) + [old_message_id],
            "ck_sealed_bytes": int(state.get("ck_sealed_bytes") or 0) + _card_body_bytes(sealed),
            # 新卡不能继承旧卡的元素级死法/去重记账（R5/A3）。
            "ck_dead": set(), "ck_decor": {},
        }
        if structured:
            new_state["engine"] = "structured"
            new_state["ck_panel_sig"] = json.dumps(
                _cardview.panel_partial(new_view.panel), sort_keys=True, ensure_ascii=False)
        return new_state

    async def _ld_ck_maybe_summary(self, card_id: str, display: str, state_ref: Dict[str, Any],
                                   seq: int, now: float) -> Tuple[int, Dict[str, Any]]:
        """按限频更新**会话列表预览**（R7 的后半），返回 ``(新的序号, 要并进状态的字段)``。

        为什么这件事值得单开一条路：飞书的会话列表显示的是卡片的 `config.summary` ——
        建卡时它是 `Hermes`（`cards.DEFAULT_TITLE` 兜底；⚠️ R7 审计的 L-1 更正：本注释以前写的
        「建卡时是 ⏳ 正在生成…」**是错的**，那是**正文**占位），收尾时是回答的开头。中间那
        几分钟里列表一直停在旧文字，而**核心在流式期间不会替我们更新它**（它只管正文）。

        纪律（每一条都有理由，别简化）：
          * **限频** `_CK_SUMMARY_INTERVAL`：`card.settings` 吃一个序号、算一次逻辑写
            ⇒ 绝不许每帧发（窗口内的帧一次都不写；预算换算见附录 B）；
          * **失败只标死**（`ck_summary_dead`）+ 限流 WARNING：它坏了不影响卡片内容，
            所以**绝不让这一帧失败**（fail-open 会让整回合掉成纯文本，代价与收益不成比例）。
            ⚠️ **「失败」包括抛异常**（R7 审计高-1，实测能绕开当时全部门禁）：官方
            `_run_blocking` 是 `loop.run_in_executor(...)`，而 SDK 的 `Transport.execute` 里
            `requests.request(...)` **一行 try 都没有** ⇒ 一次连接重置/超时就会把异常直穿到
            `send_stream_frame` 的 except ⇒ 这一帧返回 False ⇒ 核心**停用本回合 native**，
            用户从卡片掉成纯文本（卡已经在 DM 里了，于是还会多一条重复文本）。
          * **不重试**（`retry=False`，R7 审计低-7）：失败既然只标死，重试就只剩白等；
          * **序号共用**：成功才 `seq += 1` 并把新序号交回调用方写进状态 —— 跳号/撞号都会被
            服务端拒（`300317`）。**失败/异常时序号也已经消耗掉**（那次写真实发生了）⇒
            调用方要把返回的序号无条件落账（与 `_ld_ck_apply` 的「只增不减」同一条规矩）；
          * **内容没变就不发**：预览文字与上次一样时省掉这次写；
          * **处置等级**（R7 审计中-4）：预览是**独立一档** —— 一律 DEAD、不看码表、也不置
            `ck_degrade`。理由：`card.settings` 坏掉时元素通道通常也坏了，而**下一帧的元素写**
            会自己拿到 `300309` 并走 `DEGRADE`；在预览这一路再判一遍码表就是同一件事两处真相
            （附录 A 的表头已按这条改准）。
        """
        fields: Dict[str, Any] = {}
        if state_ref.get("ck_summary_dead"):
            return seq, fields
        interval = float(_CK_SUMMARY_INTERVAL)
        last_at = state_ref.get("ck_summary_at")
        if isinstance(last_at, (int, float)) and now - float(last_at) < interval:
            return seq, fields
        text = _cards.summary_text(display)
        if not text or text == state_ref.get("ck_summary"):
            return seq, fields
        seq += 1
        try:
            res = await self._ld_ck_settings(card_id, {"content": text}, seq, retry=False,
                                             state_ref=state_ref)
        except Exception as exc:
            # 见 docstring 里那条纪律：**异常与返回码同等对待**，都只标死、绝不影响这一帧。
            logger.debug("[larkdeck] 会话预览写入异常", exc_info=True)
            res = _CkResult(False, 0, str(exc))
        if res.ok:
            fields = {"ck_summary": text, "ck_summary_at": now}
        else:
            # 标死 + 留痕；**不改 `ck_summary_at`**（让状态如实反映「上次成功是什么时候」），
            # 但 `ck_summary_dead` 会让后续帧直接短路，不再白试（也就不会再刷日志）。
            fields = {"ck_summary_dead": True}
            _log_ck_summary_failed_once(res.code)
        return seq, fields

    async def _ld_write_with_retry(self, make_request: Any, call: Any, what: str) -> Any:
        """CardKit 写调用的**限流退避**（与 patch 路径的 `_ld_update_card` 同一层保护）。

        为什么必须有（第十二路审计）：`_ld_update_card` 早就有这层退避，而 CardKit 的
        元素写入 / 建实体 / 发实体卡**一次都不重试** —— 撞上一次限流，这一帧就返回 False，
        内核随即**停用本回合的 native**：用户后面看到的就不再是逐字卡片了。
        同一个错误码在两条传输上给出不同后果，是纯粹的不对称；而 `_TRANSIENT_CODES`
        的注释里明明写着那两个 CardKit 码是「预先收口」，等于承诺了一件没做的事。

        ⚠️ **只重试 `_WRITE_RETRY_CODES`**（频率限制类，服务端拒绝了这次请求 ⇒ 幂等安全）。
        `300309`/`300317` 是结构性状态，原样重发不可能成功，立刻把响应交回调用方判失败。
        ⚠️ 请求**每次都重建**（照抄 `_ld_update_card` 的写法）：SDK 的请求对象不保证可复用。

        ⚠️⚠️ **调用放大效应**（R2 审计实测，别再把它说成「每帧最多 2 次」）：退避表有
        `len(_TRANSIENT_BACKOFF)` 项 ⇒ **一次逻辑写 = 最多 `len+1` 次 HTTP 调用**。一帧有
        2 次逻辑写（装饰 batch + 正文 content）⇒ **限流风暴下单帧最坏 9 次调用、
        退避睡眠 2.0s（2 次可重试的 ×4 + 预览 1 次不重试），耗时最坏
        ≈2.0s**（退避 0.1+0.3+0.6 各来一遍）。所以 `_CK_WRITES_PER_FRAME` 是**逻辑写预算**，
        **不是**「每帧最多 2 次 API 调用」这句更强的话 —— 文档口径必须写清（审计第 1 条）。
        为什么仍然选「重试」而不是「早失败」：帧失败会让内核**停用本回合的 native**，
        用户后面看到的是纯文本（比等 2 秒更差）；而按实测，这条路本来几乎不会撞限流
        （真机连打 50 次/秒零失败）。
        """
        response: Any = None
        for attempt in range(len(_TRANSIENT_BACKOFF) + 1):
            response = await self._run_blocking(call, make_request())
            code = _ld_response_code(response)
            if code not in _WRITE_RETRY_CODES:
                return response
            if attempt < len(_TRANSIENT_BACKOFF):
                delay = _TRANSIENT_BACKOFF[attempt]
                logger.info("[larkdeck] CardKit %s 命中限流码 %s，%.1fs 后重试（第 %d 次）",
                            what, code, delay, attempt + 1)
                await asyncio.sleep(delay)
        return response

    @staticmethod
    def _ld_body_source() -> str:
        """当前正文来源：``legacy``（过渡默认）| ``own``（v0.7.0 目标路径）。"""
        raw = str(_cfg_raw("body_source") or "").strip().lower()
        return "own" if raw == "own" else "legacy"

    @staticmethod
    def _ld_final_body(core_text: str, own_text: str) -> str:
        """finalize 正文选择的**唯一判据**（v0.7.0）：

        * core 空 → own；
        * core 非空且以 own 为前缀（或二者相等）→ 整段 core（权威扩展 / 清理终稿）；
        * **core 是 own 的精确后缀** → 取 own 整段：core 是权威尾稿，own 前面的
          正文是本轮已经流式展示过的前段（工具调用前的正文），不能因 core 只含
          最后一段而静默消失；
        * 其余分叉 → 整段 core（投递权威）。
        绝不 split/rsplit 分隔符，绝不拼接；分叉/后缀关系只记日志。
        """
        core = str(core_text or "")
        own = str(own_text or "")
        if not core:
            return own
        if not own or core.startswith(own) or core == own:
            return core
        # core 是 own 的**精确后缀**：core 保留权威尾稿，own 前面还有本轮已经流式
        # 展示过的正文（例如工具调用前的“开始”）。此时取 own 整段，绝不裁切/拼接。
        if own.endswith(core):
            return own
        return core

    def _ld_own_text(self, chat: str) -> str:
        """严格绑定的 own 累积（无 ``chat->session`` 或绑定过期 → 空，绝不退回最近活跃）。"""
        try:
            own, _armed, _complete = _panel.answer_state(chat, require_binding=True)
            return str(own or "")
        except Exception:  # pragma: no cover - 防御性：状态层异常不得让帧失败
            logger.debug("[larkdeck] own 累积读取失败，按空正文处理", exc_info=True)
            return ""

    def _ld_log_finalize_divergence(self, core: str, own: str) -> None:
        """finalize 两份文本分叉时留证据；**只记录，不参与选正文**。"""
        if not core or not own or core == own:
            return
        limit = min(len(core), len(own))
        first = limit
        for index in range(limit):
            if core[index] != own[index]:
                first = index
                break
        _log_body_diag_once(
            "finalize-divergence",
            "[larkdeck] finalize 分叉：own_len=%d core_len=%d first=%d "
            "core_prefix_of_own=%s own_prefix_of_core=%s（整段选一，不切片不拼接）",
            len(own), len(core), first, core.startswith(own), own.startswith(core))

    def _ld_body_text(self, text: str, chat: str, *, finalize: bool,
                      stream_state: Optional[Dict[str, Any]] = None) -> str:
        """帧文本 → **要渲染的正文**（正文净化的唯一入口）。

        v0.7.0 P1 起按 ``body_source`` 分两条路：
          * ``own``：``finalize=False`` **完全不读** ``text``，只回严格绑定的 own 累积
            （空由展示层给占位）；``finalize=True`` 按 :meth:`_ld_final_body` 整段选一。
            传入 ``stream_state`` 时还会校验建卡时的 session/世代（防主帧路径串会话/回合）。
          * ``legacy``：保留旧的“按证据剥核心叠加进度块”路径，直到 P2b 归档后删除。
        """
        if self._ld_body_source() != "own":
            return self._ld_body_text_legacy(text, chat, finalize=finalize)
        own = (self._ld_stream_own_text(chat, stream_state)
               if stream_state is not None else self._ld_own_text(chat))
        if finalize:
            self._ld_log_finalize_divergence(str(text or ""), own)
            return self._ld_final_body(text, own)
        return own

    def _ld_body_text_legacy(self, text: str, chat: str, *, finalize: bool) -> str:
        """旧路径（P2b 删除）：按前缀证据剥掉核心叠加的工具进度块。

        三个决策点，都在这里：
          * ``progress_lines_in_body: true`` ⇒ **原样渲染**（用户明确要核心那套：正文区也滚工具行）；
          * ``finalize=True``（收尾帧）⇒ **原样渲染**（那一帧通常是纯累积正文，没有进度块可剥；
            多剥一次就是不可逆地吞正文）。⚠️ **例外**：帧失败后核心会拿**同一个合成文本**再发一帧
            ``finalize=True``（``stream_consumer_transport.py:391``）—— 那一帧确实带进度行，
            本条件会把它留在正文里（有意取舍与改法见 :func:`_strip_core_progress` 条件 0）；
          * 否则按证据剥掉核心叠加的工具进度块（证明不了就不剥，见 :func:`_strip_core_progress`）。

        ⚠️ ``finalize`` 是**必填关键字**（没有默认值）：默认值会让「新调用点忘了传」静默退回
        有缺陷的行为，而那正是收尾帧吞正文的入口。

        ⚠️ 这个配置项**早就存在**（`_DEFAULTS` + `plugin.yaml` + README 三处都有，默认 ``false``），
        但在 2026-09-14 之前它的唯一实现是 ``format_tool_event`` 返回 ``None`` —— 而那个扩展点
        在 Hermes 0.21.1 的**生产路径上根本没有调用点**（唯一调用者在
        ``gateway/stream_dispatch.py``，那个 dispatcher 只在测试里被构造）。
        于是「正文只有回答」这句文档**一直是空转的**，真机上工具行照样出现在卡片正文里。
        R11-A7 把它接到真正的帧文本上 ⇒ 同一句文档、同一个默认值，从此**真的成立**。

        为什么收敛成一个方法：任何第二处「顺手也剥一下」的调用点都会变成两处真相
        （本项目最怕的形态）—— 而且四门禁抓不住它，只有真机上「同一帧有的地方剥了、
        有的地方没剥」这种症状才会暴露。
        """
        if _cfg("progress_lines_in_body"):
            return text
        try:
            accumulated, tool_pending, complete = _panel.answer_state(chat)
        except Exception:  # pragma: no cover - 防御性：状态层异常绝不能让整帧失败
            logger.debug("[larkdeck] 正文净化取状态失败，按原样渲染", exc_info=True)
            return text
        tools: list = []
        running: list = []
        try:
            # 必须与 `answer_state` 取**同一个会话**的工具桶：`snapshot(chat)` 走的是
            # 面板的「最近活跃/绑定」选择，可能与正文累积选中的会话不同（审计 A-P2）。
            # 归属不一致时拿别的会话的工具名单去剥本会话的正文，会把真实文本剥空。
            for item in _panel.answer_tools(chat):
                name = str(item.get("name") or "")
                if not name:
                    continue
                tools.append(name)
                if str(item.get("status") or "") == "running":
                    running.append(name)
        except Exception:  # pragma: no cover - 同上：取不到工具名单就不做空累积剥离
            logger.debug("[larkdeck] 正文净化取工具名单失败，按原样渲染", exc_info=True)
        display = _strip_core_progress(text, accumulated, tool_pending, complete,
                                       finalize=finalize, tools=tools, running=running)
        if (display == text and not accumulated and tool_pending and complete
                and not finalize):
            # v0.6.1 diagnostic: the empty-accumulated progress frame was not
            # stripped (shape/tool-name mismatch). One bounded line so the next
            # real-device retry tells us exactly what core sent.
            _log_body_diag_once(
                "empty-accum-progress",
                "[larkdeck] 空累积工具帧未剥离：tools=%r frame=%r",
                tools[:8], text[:200])
        if (finalize and text.strip() and not accumulated and tool_pending
                and complete):
            # Final frames are never stripped; log when that means a tool block
            # could be the only visible content, so the next retry is decisive.
            _log_body_diag_once(
                "finalize-progress",
                "[larkdeck] 收尾帧含工具帧且无累积正文：tools=%r frame=%r",
                tools[:8], text[:200])
        return display

    def _ld_heartbeat_cancel(self, key: str) -> None:
        task = _LD_HEARTBEATS.pop(str(key), None)
        if task is not None and not task.done():
            task.cancel()

    def _ld_heartbeat_cancel_chat(self, chat: str) -> None:
        prefix = f"{str(chat)}:"
        for key in list(_LD_HEARTBEATS):
            if key == str(chat) or key.startswith(prefix):
                self._ld_heartbeat_cancel(key)

    def _ld_heartbeat_cancel_all(self) -> None:
        for key in list(_LD_HEARTBEATS):
            self._ld_heartbeat_cancel(key)

    def _ld_heartbeat_start(self, chat: str, key: str, turn_id: str) -> None:
        self._ld_heartbeat_cancel(key)
        try:
            task = asyncio.get_running_loop().create_task(
                self._ld_heartbeat_loop(chat, key, turn_id))
        except RuntimeError:
            return
        _LD_HEARTBEATS[key] = task

    async def _ld_heartbeat_loop(self, chat: str, key: str, turn_id: str) -> None:
        """Working 心跳：只更新面板 header 的耗时，终态必须 cancel（V2）。

        ⚠️ **心跳是第二个 CardKit 写者**（另一个是帧路径）。两者写的是同一个 ``panel`` 元素，
        而序号/uuid 都从 ``state`` 里现算 ⇒ 撞车会拿到 ``200770``（uuid 重复消费）或
        ``300317``（序号乱序，会被判成卡级死法）。收口全在 `_ld_heartbeat_tick`：
        撞上锁就跳过这一拍、拿到锁后重读 state。见 `_ld_stream_frame_structured` 的
        docstring（V4.1 真机实测）。
        """
        try:
            while True:
                await asyncio.sleep(_LD_HEARTBEAT_INTERVAL)
                if await self._ld_heartbeat_tick(chat, key) in ("stop", "dead"):
                    return
        except asyncio.CancelledError:
            return
        except Exception:
            logger.debug("[larkdeck] heartbeat 异常退出", exc_info=True)
        finally:
            if _LD_HEARTBEATS.get(key) is asyncio.current_task():
                _LD_HEARTBEATS.pop(key, None)

    async def _ld_heartbeat_tick(self, chat: str, key: str) -> str:
        """一拍心跳（返回值的四种取值就是这一拍的全部可能结局，测试直接钉这四档）:

        * ``"skip"``      —— 帧路径正持着回合写锁（**不排队**：心跳只是补「模型不出字时
          耗时跳秒」，晚一拍无害；排队反而会把写窗口拖长）。
        * ``"stop"``      —— 回合状态没了 / 不是结构化 / 已降级 ⇒ 心跳该收工。
        * ``"unchanged"`` —— 面板签名与已写成功的那份一致 ⇒ 这一拍没有值得发的写。
        * ``"wrote"``     —— 真的写了一次 panel（序号从**锁内重读**的 state 里取）。
        """
        lock = self._ld_card_lock(key)
        if lock.locked():
            return "skip"
        async with lock:
            state = self._ld_stream_get(key)      # ⚠️ 锁内重读：锁外那份可能已被帧路径推进
            if not state or state.get("engine") != "structured":
                return "stop"
            if _ld_visual_engine() != "structured":
                # 引擎被热切换（`/larkdeck config reload`）⇒ 立即收工：否则本心跳会与
                # legacy 车道的写者并存（审计 A 中-2 复现过同卡同 seq 的重复 uuid）。
                return "stop"
            if state.get("engine_stamp") == "degraded" or not state.get("card_id"):
                return "stop"
            view = self._ld_cardview(
                chat, str(state.get("last_rendered_body") or ""), status="processing",
                started=state.get("t0"), message_id=state.get("message_id"))
            # ⚠️ **不许**在这里自己拼标题：帧路径的标题由 `_ld_cardview` 按「回合墙钟 +
            # 真实步数」统一算（V4.5）。曾经这里用墙钟 + **trim 后**的步数另算一份，
            # 结果同一张卡的两帧在 30.0s/0 步 与 2.0s/0 步 之间互跳（审计 B 实测）。
            if not view.panel_enabled:
                return "unchanged"          # 面板被配置关掉 ⇒ 心跳没有可写的东西
            partial = _cardview.panel_partial(view.panel)
            signature = json.dumps(partial, sort_keys=True, ensure_ascii=False)
            if signature == state.get("ck_panel_sig"):
                return "unchanged"
            seq = _ck_seq(state) + 1
            res = await self._ld_ck_partial(
                str(state["card_id"]), "panel", partial, seq)
            if not res.ok:
                # ⚠️ 失败必须**说出来**（审计 A 中-3：旧写法把「写失败」伪装成 unchanged、
                # 把「卡级死法」伪装成 stop，tick 里一行日志都不打、也不落账 ⇒
                # 面板耗时冻结 + 每 3 秒静默重试同号，真机上完全无迹可寻）。
                if res.code in _CARD_DEATH_DECOR_CODES:
                    self._ld_stream_put(key, dict(
                        state, ck_degrade=res.code, engine_stamp="degraded", card_id=""))
                    _log_ck_degrade_once(res.code)
                    return "dead"
                _log_ck_decor_write_failed_once(
                    [_CkOp("panel", "", _CK_ROLE_PANEL)], res.code, msg=res.msg)
                return "failed"
            updated = dict(state)
            updated["ck_panel_sig"] = signature
            updated["ck_seq"] = seq
            _ck_window_note(updated, time.monotonic())
            self._ld_stream_put(key, updated)
            return "wrote"


    @staticmethod
    def _ld_icon_token(name: str) -> str:
        lowered = str(name or "").lower()
        for key, token in _cardview.ICON_TOKENS.items():
            if key in lowered:
                return token
        return _cardview.ICON_TOKENS["fallback"]

    def _ld_cardview(self, chat: str, answer: str, *, status: str = "processing",
                     finalize: bool = False, started: Optional[float] = None,
                     message_id: Optional[str] = None) -> "_cardview.CardView":
        """从面板快照构造结构化视图（V1 canary）。

        ``started`` / ``message_id`` 只喂**页脚**：用户 2026-09-17 指定的页脚阅读顺序是
        「状态 → 时长 → 模型 → ctx → 短码」，而结构化路径以前两处都漏了
        （时长没有 `t0`、短码只走 legacy 的 `_ld_frame_footer`）—— 2026-09-21 真机截图
        一眼看出来（`✅ 已完成 · 🧠 deepseek-flash · ctx …`，中间少了 `⏱ 12.3s`、末尾少了
        `🔖 xxxxxx`）。这里把两条规则都收在同一处：**有基数页脚才挂短码**（短码不能把
        「页脚=无」这个诊断信号抹掉，见 `_ld_frame_footer` 的纪律①）。
        """
        snap = _panel.snapshot(chat) or {}
        rounds: List["_cardview.ReasoningRoundView"] = []
        for index, item in enumerate(snap.get("rounds") or []):
            if not isinstance(item, dict):
                continue
            rounds.append(_cardview.ReasoningRoundView(
                index=index,
                text=_cards.truncate(str(item.get("text") or ""),
                                     _cfg_int("max_reasoning_chars",
                                              _cards.MAX_REASONING_CHARS)),
                elapsed_ms=item.get("elapsed_ms"), finalized=bool(item.get("finalized"))))
        tools: List["_cardview.ToolStepView"] = []
        for item in snap.get("tools") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "tool")
            preview = str(item.get("preview") or "")
            try:
                detail = _cards._detail_safe(preview)  # type: ignore[attr-defined]
            except Exception:
                detail = preview
            cap = _cfg_int("max_tool_result_chars", _cards.MAX_TOOL_RESULT_CHARS)
            tools.append(_cardview.ToolStepView(
                name=name, title=name, status=str(item.get("status") or "running"),
                duration_ms=item.get("duration_ms"), detail=detail,
                result_block=_cards.truncate(str(item.get("result_block") or ""), cap),
                error_block=_cards.truncate(str(item.get("error_block") or ""), cap),
                icon_token=self._ld_icon_token(name)))
        total_ms = sum(int(r.elapsed_ms or 0) for r in rounds)
        if started:
            # V4.5：**帧路径与心跳必须同一口径**（审计 B 实测同一张卡 30.0s/0 步 ↔ 2.0s/0 步
            # 互跳）：标题里的耗时统一取「回合墙钟」，推理轮总时长只在拿不到 t0 时兜底。
            try:
                total_ms = max(0, int((time.monotonic() - float(started)) * 1000))
            except (TypeError, ValueError, OverflowError):
                pass
        collapsed_hint = ""
        total_tools = len(tools)
        total_rounds = len(rounds)
        # V4.5：既有配置在结构化车道上也要生效（审计 B 中-4）——
        # `max_panel_steps`（plugin.yaml 默认 30）曾在这里写死 20，等于把用户的配置吃掉。
        # §9.4：步数上限 = min(用户配置, 元素预算推导出的封顶 20)。工具步按
        # 「标题 + 细节 + Result」≈3 元素/步估，20 步 ≈60 元素，离 180 的墙壁还有余量给推理轮；
        # 而 `max_panel_steps` 的 plugin.yaml 默认是 30，直接照抄会撞墙 ⇒ 这里取小。
        max_steps = max(1, min(_cfg_int("max_panel_steps", 20), 20))
        if total_tools > max_steps:
            collapsed_hint = f"…更早的 {total_tools - max_steps} 步已折叠"
            tools = tools[-max_steps:]
        max_rounds = 20
        if total_rounds > max_rounds:
            extra = f"…更早的 {total_rounds - max_rounds} 轮已折叠"
            collapsed_hint = f"{collapsed_hint} {extra}".strip()
            rounds = rounds[-max_rounds:]
        panel = _cardview.PanelView(
            title=f"💭 思考 {total_ms / 1000:.1f}s · 🛠️ 工具执行 · {total_tools} 步",
            expanded=bool(_cfg("panel_expanded")),   # V4.5：与 legacy 同一条配置
            tools=tools,
            reasoning_rounds=rounds if _ld_show_reasoning() else [],
            collapsed_hint=collapsed_hint,
            border={"processing": "grey", "completed": "green",
                    "stopped": "yellow", "error": "red"}.get(status, "grey"),
        )
        base_footer = self._ld_footer(chat_id=chat, started=started, status=status) or ""
        trace = _ld_trace_id(message_id) if base_footer else ""
        return _cardview.CardView(
            answer=answer,
            panel_enabled=bool(_cfg("unified_panel")),   # V4.5：关掉面板的人不该还看到面板
            footer=f"{base_footer} · \U0001f516 {trace}" if trace else base_footer,
            footer_enabled=bool(_cfg("footer")), panel=panel,
            header_enabled=_ld_card_status_header_enabled(),
            header_status=status,
            header_title={"processing": "🫧 处理中…", "completed": "✅ 已完成",
                          "stopped": "⛔ 已停止", "error": "❌ 执行出错"}.get(
                              status, "🫧 处理中…"))

    async def _ld_ck_partial(self, card_id: str, element_id: str,
                             partial: Dict[str, Any], seq: int) -> "_CkResult":
        """结构化面板整段替换（partial_update_element，顶层不带 tag）。"""
        reqs = self._ld_ck_requests()
        if reqs is None:
            return _CkResult(False, 0, "没有 CardKit SDK")
        actions = [{"action": "partial_update_element",
                    "params": {"element_id": element_id, "partial_element": partial}}]
        resp = await self._ld_write_with_retry(
            # ⚠️ uuid 前缀必须是**独立命名空间**（审计 A 中-2 实测的撞车）：`s{seq}` 被
            # `_ld_ck_settings`（会话列表预览）用着 —— 两条路径共用同一个 (card, seq) 前缀时，
            # 只要有一次 engine 热切换/并发，同卡同 seq 就会出现**两份 uuid 相同的写**
            # （真机形状 = 200770 `this UUID has been recently consumed`）。
            lambda: reqs.batch_update(card_id, actions, int(seq), f"ld-{card_id}-p{seq}"),
            self._client.cardkit.v1.card.batch_update, "结构化面板")
        return _CkResult(_ld_response_code(resp) == 0, _ld_response_code(resp),
                         str(getattr(resp, "msg", "") or ""))

    def _ld_card_lock(self, key: str) -> "asyncio.Lock":
        """取**回合级** CardKit 写锁（同一回合的写串行；见 `_ld_stream_frame_structured`）。

        为什么锁键是回合 key 而不是 card_id：``_ld_ck_split`` 会在回合中途换卡，而
        「同一回合的写串行」才是保证（新卡的 seq 从 0 重来，撞上旧卡的写也不会串行化）。

        ⚠️ 用 ``getattr`` 懒建而不是在 ``_ld_setup`` 里预置：本文件有好几条「构造期异常
        被上层吞掉」的路径（见 `_ld_known` 的说明），锁表缺了只该退化成「没有锁」这一种
        可解释状态，不该在帧路径上抛 ``AttributeError``。
        """
        locks = getattr(self, "_ld_card_locks", None)
        if locks is None:
            locks = {}
            self._ld_card_locks = locks
        lock = locks.get(key)
        if lock is None:
            lock = locks[key] = asyncio.Lock()
        return lock

    async def _ld_structured_degrade(self, chat: str, state: Dict[str, Any],
                                     live: Dict[str, Any], visible: str, code: int) -> bool:
        """卡级死法 ⇒ **同卡 DEGRADE**（legacy 渲染 + `engine_stamp=degraded`）；成功返回 True。

        §9.3 说 DEGRADE 是唯一同卡回退车道。审计 A（高-4/中-5）实测结构化路径有两条缝：
        footer/正文写拿到 `300309/300317` 时**完全不看码表**（正文直接 fail-open 掉 native，
        用户掉纯文本、卡冻住），而面板那条缝虽然会 patch，却**不把 `ck_seq`/`card_id` 落账**
        ⇒ 下一帧又从旧序号起重发一次、再 patch 一遍。这里把三处收成同一个动作。
        """
        live["ck_degrade"] = code
        live["engine_stamp"] = "degraded"
        _log_ck_degrade_once(code)
        fallback = self._ld_build_card(visible, streaming=False,
                                       panel=self._ld_panel(chat, report_empty=True),
                                       footer=self._ld_frame_footer(state))
        updated = await self._ld_update_card(chat, str(state.get("message_id") or ""), fallback)
        if updated is None or not getattr(updated, "success", False):
            return False
        live["last_rendered_body"] = visible
        live["card_id"] = ""          # 账本切到 patch 车道：元素通道对这张卡已经不可用
        return True

    async def _ld_stream_frame_structured(self, text: str, *, finalize: bool,
                                          chat_id: Optional[str], reply_to: Optional[str],
                                          turn_id: str) -> bool:
        """V1 结构化流式帧（canary）的**串行化外壳**：整个帧体在一把按回合的异步锁里。

        为什么锁是**必须**的（2026-09-21 真机实测收口 · V4.1）：``elem_id=panel`` 是唯一
        会被**两条路径**写的元素 —— 帧路径（本方法）与工作心跳（``_ld_heartbeat_loop``）。
        两条路径都从同一份 ``state`` 里算 ``seq = _ck_seq(state) + 1``，而两次写入之间隔着
        ``await`` ⇒ 同一毫秒可能发出**两份 (seq, uuid) 一样、内容不同**的写。飞书对重复
        uuid 的应答是 ``code=200770 · ErrMsg: this UUID has been recently consumed``
        （``tests/probe_concurrent.py`` 实测复现）；而「两份写同时在路上、序号先后到达」
        拿到的是 ``300317``（``sequence number compare failed``）—— 那个码在
        `_CARD_DEATH_DECOR_CODES` 里，**会被判成卡级死法、把整卡降级**。
        一把锁同时关掉这两个洞：写窗口（算 seq → 发写 → 记账）永远只有一个持有者。

        ⚠️ 锁键是**回合 key**（``chat:turn_id``，与 ``_ld_streams`` 同一把尺子），不是
        card_id —— 切卡（``_ld_ck_split``）之后 card_id 会变，而「同一回合的写」仍必须串行。
        ⚠️ 心跳不排队：它撞上锁时**跳过这一拍**（见 `_ld_heartbeat_loop` 的 ``locked()`` 分支）。
        """
        chat = str(chat_id or "").strip()
        key = f"{chat}:{turn_id}" if turn_id else chat
        async with self._ld_card_lock(key):
            return await self._ld_stream_frame_structured_locked(
                text, finalize=finalize, chat=chat, key=key, reply_to=reply_to,
                turn_id=turn_id)

    async def _ld_stream_frame_structured_locked(self, text: str, *, finalize: bool,
                                                 chat: str, key: str,
                                                 reply_to: Optional[str],
                                                 turn_id: str) -> bool:
        """V1 结构化流式帧（canary）：seed 建结构化实体卡，后续帧替换面板 + 写正文。

        ⚠️ 本方法**必须在 `_ld_card_lock(key)` 内被调用**（唯一调用方是上面那个外壳）：
        它从 ``state`` 里算序号并发出 CardKit 写，而「序号/uuid 不撞车」这件事靠的正是
        「同一回合的写串行」—— 把这里挪到锁外，``200770`` 与 ``300317`` 会立刻回来。
        """
        state = self._ld_stream_get(key)
        now = time.monotonic()
        if state is None:
            if finalize:
                return False
            display = self._ld_body_text(text, chat, finalize=False, stream_state=None)
            view = self._ld_cardview(chat, display, started=now)
            made = await self._ld_ck_create(chat, answer=display, panel_text="",
                                            panel_tools_text="", reply_to=reply_to,
                                            structured_view=view)
            if made is None:
                return self._ld_stream_fail("structured 建实体失败")
            result, card_id, card_json = made
            message_id = getattr(result, "message_id", "") or ""
            if not message_id:
                return self._ld_stream_fail("structured 建卡成功但没拿到 message_id")
            self._ld_track(message_id, chat)
            self._ld_stream_put(key, {
                "message_id": message_id, "chat_id": chat, "t0": now, "last": text,
                "last_at": now, "frames": 0, "skipped": 0, "strips": 0,
                "card_id": card_id, "ck_seq": 0, "engine": "structured",
                "ck_elems": _ck_elems_from_card(card_json),
                "ck_panel_sig": json.dumps(_cardview.panel_partial(view.panel),
                                           sort_keys=True, ensure_ascii=False),
                "last_rendered_body": display, "last_failed_frame": "",
                "session_id": _panel.bound_session_id(chat) or "",
                "turn_id": str(turn_id or ""),
            })
            self._ld_heartbeat_start(chat, key, turn_id)
            _context.note_frame_ok()
            return True
        if self._ld_body_source() == "own" and self._ld_seed_failure_active(chat):
            return self._ld_stream_fail("structured 拒绝 F4 合成帧窗口")
        # ⚠️ **世代补种**（V4.6，审计 A 中-1 实测）：seed 帧可能早于第一个 delta（那一刻正文桶
        # 还不存在，gen=0），随后 `on_stream_delta` 把桶建成 gen>=1。结构化路径过去**没有**这一段
        # （legacy 有，见 `_ld_stream_frame` 里的同形分支）⇒ `_ld_stream_own_text` 的世代守卫
        # 每帧都按「漂移」fail-open 成空正文，正文元素一直写占位符 `⏳ 正在生成…`，
        # 只有收尾帧靠 core 权威终稿兜底才把正文补上 —— **打字机在结构化下等于没有**。
        if (self._ld_body_source() == "own"
                and int(state.get("answer_gen") or 0) == 0):
            try:
                _gen = _panel.answer_generation(chat, require_binding=True)
            except Exception:            # pragma: no cover - 防御性
                _gen = 0
            if _gen > 0:
                state = {**state, "answer_gen": _gen}
                self._ld_stream_put(key, state)
        display = self._ld_body_text(text, chat, finalize=finalize, stream_state=state,)
        offset = int(state.get("ck_offset") or 0)
        visible = display[offset:]
        if _card_body_bytes(visible) > _CK_SPLIT_SEAL_AT:
            split_state = await self._ld_ck_split(chat, display, state, offset, reply_to, now)
            if split_state is None:
                return self._ld_stream_fail("structured 正文超过单卡上限且切不出新卡")
            state = split_state
            self._ld_stream_put(key, state)
            offset = int(state.get("ck_offset") or 0)
            visible = display[offset:]
        status = _ld_view_status(chat, default="processing" if not finalize else "completed")
        view = self._ld_cardview(chat, visible, status=status, finalize=finalize,
                                 started=state.get("t0"),
                                 message_id=state.get("message_id"))
        card_id = str(state.get("card_id") or "")
        if not card_id:
            return self._ld_stream_fail("structured 状态缺 card_id")
        seq = _ck_seq(state)
        live = dict(state)
        partial = _cardview.panel_partial(view.panel)
        signature = json.dumps(partial, sort_keys=True, ensure_ascii=False)
        if view.panel_enabled and signature != state.get("ck_panel_sig"):
            seq += 1
            res = await self._ld_ck_partial(card_id, "panel", partial, seq)
            _ck_window_note(live, now)
            if not res.ok:
                if (res.code in _CARD_DEATH_DECOR_CODES
                        and await self._ld_structured_degrade(chat, state, live, visible,
                                                              res.code)):
                    live["ck_seq"] = seq          # 中-5：用掉的序号必须落账，别让下一帧重号
                    self._ld_stream_put(key, live)
                    _context.note_frame_ok()
                    return True
                _log_ck_decor_write_failed_once(
                    [_CkOp("panel", "", _CK_ROLE_PANEL)], res.code, msg=res.msg)
            else:
                live["ck_panel_sig"] = signature
        footer_text = self._ld_frame_footer(state) or " "
        if (_cards.CARDKIT_FOOTER_ID in self._ld_ck_elems(state)
                and state.get("ck_footer") != footer_text):
            seq += 1
            fres = await self._ld_ck_batch(
                card_id, [_CkOp(_cards.CARDKIT_FOOTER_ID, footer_text, _CK_ROLE_DECOR)], seq)
            if fres.ok:
                live["ck_footer"] = footer_text
            elif (fres.code in _CARD_DEATH_DECOR_CODES
                  and await self._ld_structured_degrade(chat, state, live, visible, fres.code)):
                live["ck_seq"] = seq
                self._ld_stream_put(key, live)
                _context.note_frame_ok()
                return True
        seq += 1
        # ⚠️ V4.8（用户反馈）：structured 卡片**不渲染 `⏳ 正在生成…` 占位符** ——
        # 首个 delta 之前正文元素留空即可（占位符会在「模型还在想」的十几秒里一直杵着，
        # 用户明确说丑、且对标插件不这么做）。
        answer_text = visible or " "
        wrote = await self._ld_ck_write(card_id, _cards.CARDKIT_ANSWER_ID, answer_text, seq)
        if not wrote.ok and wrote.code in _CARD_DEATH_CODES:
            # 审计 A 高-4：正文（提交点）拿到卡级死法时必须**同卡 DEGRADE** —— 旧写法直接
            # `_ld_stream_fail` 掉 native，用户从卡片掉成纯文本、而卡还冻在流式态。
            if await self._ld_structured_degrade(chat, state, live, visible, wrote.code):
                live["ck_seq"] = seq
                self._ld_stream_put(key, live)
                _context.note_frame_ok()
                return True
        if not wrote.ok:
            live["ck_seq"] = seq
            self._ld_stream_put(key, live)
            return self._ld_stream_fail("structured 正文写入失败")
        if finalize:
            # V2 修复：先 cancel 心跳，再发终态 patch，避免 tick 在 patch 后落回 processing 面板。
            self._ld_heartbeat_cancel(key)
            final_card = _cards.apply_text_profile(_cardview.entity_skeleton(view),
                                                   _cfg_raw("text_profile"))
            final_card["config"]["streaming_mode"] = False
            updated = await self._ld_update_card(
                chat, str(state.get("message_id") or ""), final_card)
            if updated is None or not getattr(updated, "success", False):
                logger.warning("[larkdeck] structured 收尾整卡 patch 未成功（%s）",
                               getattr(updated, "error", "unknown"))
                live["ck_seq"] = seq
                self._ld_stream_put(key, live)
                return self._ld_stream_fail("structured 收尾整卡 patch 失败")
            self._ld_stream_pop(key)
            self._ld_forget(str(state.get("message_id") or ""))
            _context.note_frame_ok()
            return True
        if self._ld_stream_get(key) is None:
            # `/stop` 已经把这个回合 pop 掉并重绘完了（V4.6）：这一帧的写虽然发出去了，
            # 但**绝不能**把状态插回去 —— 否则第二次 `/stop` 会把同一张卡再重画一遍
            # （审计 A 中-6 复现：`total patches 2`）。
            _context.note_frame_ok()
            return True
        live.update({"last_rendered_body": visible, "last": text, "last_at": now,
                     "ck_seq": seq, "frames": int(state.get("frames") or 0) + 1})
        if live.get("ck_degrade"):
            live["engine_stamp"] = "degraded"
            live["card_id"] = ""
            self._ld_stream_put(key, live)
            return False
        self._ld_stream_put(key, live)
        _context.note_frame_ok()
        return True

    async def _ld_stream_frame(self, text: str, *, finalize: bool, chat_id: Optional[str],
                               reply_to: Optional[str], turn_id: str) -> bool:
        chat = str(chat_id or "").strip()
        if not chat or not getattr(self, "_client", None):
            return self._ld_stream_fail("没有 chat / SDK 客户端")
        key = f"{chat}:{turn_id}" if turn_id else chat
        state = self._ld_stream_get(key)
        engine = _ld_visual_engine()  # V0：生产读取配置；V1 structured canary
        if engine == "structured" and not (state and state.get("engine_stamp") == "degraded"):
            return await self._ld_stream_frame_structured(
                text, finalize=finalize, chat_id=chat_id, reply_to=reply_to, turn_id=turn_id)
        now = time.monotonic()
        if finalize and state is not None and self._ld_body_source() == "own":
            failed_text = str(state.get("last_failed_frame") or "")
            if failed_text and failed_text == text:
                # v0.7.0 P1 / 审计 C 安全反例：core 帧确定性失败后会用**同一段
                # interim 合成文本**再发一帧 finalize=True。那段文本含未脱敏 terminal
                # 命令/args（run_turn_runner.py:210/238），不能持久化进正文。这里拒绝，
                # 让 core 走 edit_message 回落：own 模式的 interim edit 只渲 own 累积，
                # 最终 finalize edit 再用 core 权威终稿补齐全文。
                logger.info("[larkdeck] 检测到 native 帧失败后的 finalize 重发，"
                            "拒绝持久化合成帧（防止进度/密钥进正文）")
                return self._ld_stream_fail("F4 合成帧重发：拒绝持久化，交回核心 edit/send 回落")
        # 整帧渲染，**只剥掉能被证明是核心叠加的工具进度块**（R11-A7）—— 判据与
        # 「为什么这是证明而不是猜」见文件顶部那段说明。剥不出来就原样渲染（fail-open）。
        # ⚠️ 「整帧原样渲染」是 8f81b4d 的安全修复留下的口径；R11-A7 把它收紧成
        # 「按证据剥后缀」：仍然**不做任何正文归档**（不按分隔符切、不改写前缀）。
        # ⚠️ **必须把 ``finalize`` 传下去**（R11-A7 尾巴）：收尾帧是**唯一不可逆**的一帧
        # （核心对 finalize 乐观记账、不会再补发），而那一帧**通常**是纯累积正文
        # （例外：帧失败后的重发，见上面 F4 分支）。
        display = self._ld_body_text(text, chat, finalize=finalize, stream_state=state)
        # seed 帧可能早于第一个 text delta：那一刻正文桶还不存在（gen=0），
        # 随后 `on_stream_delta` 建桶 gen=1。这是**同一回合的合法首段**，必须把
        # 世代钉到这张卡上；否则会一直 fail-open 成空正文（真机 2026-09-18 实测）。
        if (state is not None and self._ld_body_source() == "own"
                and int(state.get("answer_gen") or 0) == 0):
            try:
                _gen = _panel.answer_generation(chat, require_binding=True)
            except Exception:  # pragma: no cover - 防御性
                _gen = 0
            if _gen > 0:
                state = {**state, "answer_gen": _gen}
                self._ld_stream_put(key, state)
                display = self._ld_body_text(text, chat, finalize=finalize,
                                             stream_state=state)
        # own 模式不“剥帧”，显示文本本来就来自 own 累积；strips 计数只属于 legacy 路径。
        stripped = (self._ld_body_source() != "own") and display != text
        if state is None:
            if finalize:
                # 没有活跃流可收尾：交核心回落（send/edit 会正常发出）。这是**正常路径**
                # （native 没开、或本回合首帧就没建成卡），所以不告警。
                #
                # ⚠️ **账本口径（R9 审计中-2，写死在这里以免被当成漏记）**：这一条
                # **只返回 False、不记 `note_frame_fail`**。两条纪律是分开的：
                #   * 返回值是给**核心**看的信号 —— False ⇒ 停用本回合 native、回落 edit/send
                #     （官方 fail-open 契约，不变量 2）；
                #   * 账本里的「写卡失败」记的是「**我们真的发起过一次写、而它失败了**」。
                #     这里**一次写请求都没有发出去**（没有我们的卡可收尾），把它记成「写卡失败」
                #     会让一个健康回合在用户的排障卡上显示一条失败原因 —— 而那条原因指向
                #     根本不存在的写卡动作。所以它不该进失败计数（语义见 `context.note_frame_fail`）。
                # 换句话说：**「账本失败次数」与「核心收到过几个 False」本来就不等价**，
                # 后者包含「我们按契约主动交还控制权」这一类正常返回。README 的「写卡失败」一条
                # 就是照这个口径写的；`/stop` 重绘成功后内核若再送 finalize 帧，也落在这一支。
                return False
            if self._ld_transport() == "cardkit":
                # ---- CardKit 实体卡（真打字机）：结构建实体时定死，之后只按 id 写元素 ----
                # R3 收窄版：面板是**两块**（推理 / 工具），建实体时都定死，之后只改内容
                panel_text, panel_tools_text = self._ld_panel_parts(
                    chat, report_empty=bool(finalize))
                # ⚠️ 这里**故意**用 `_ld_footer()` 而不是 `_ld_frame_footer(state)`：
                # 这一帧就是**建卡那一帧**，`message_id`/`card_id` 此刻还不存在 ——
                # 短码是「本卡的 id 后 6 位」，在 id 诞生之前不可能有。**从下一帧起**
                # （每帧装饰 / 元素写 / 收尾整卡）页脚就带上短码了。
                made = await self._ld_ck_create(chat, answer=display, panel_text=panel_text,
                                                panel_tools_text=panel_tools_text,
                                                reply_to=reply_to)
                if made is None:
                    # 任何一步失败都交给核心回落（这是**契约**：帧失败 ⇒ 本回合改走 edit/send）
                    return self._ld_stream_fail("CardKit 建实体/发实体卡失败")
                result, card_id, card_json = made
                message_id = getattr(result, "message_id", "") or ""
                if not message_id:
                    return self._ld_stream_fail("CardKit 建卡成功但没拿到 message_id")
                self._ld_track(message_id, chat)
                self._ld_stream_put(key, {"message_id": message_id, "chat_id": chat,
                                          "t0": now, "last": text, "last_at": now,
                                          "frames": 0, "skipped": 0,
                                          # R11-A7：seed 帧也可能剥（卡建起来之前就已经跑过工具）
                                          "strips": 1 if stripped else 0,
                                          "card_id": card_id, "ck_seq": 0,
                                          # 结构在这一刻定死：**卡里到底有哪些元素**记进状态，
                                          # 后续每一帧只写这里面的 id（写不在卡里的 id 会得
                                          # 300313，整帧失败）。这是 R1 的元素表，R2 往里加
                                          # 页脚/状态元素、R3 加面板子元素都靠它。
                                          # 元素表 = **这张卡里真有**的 id（从卡 JSON 抽出来，
                                          # 不是另读一遍配置算的）⇒ 结构分叉在构造上不可能
                                          "ck_elems": _ck_elems_from_card(card_json),
                                          # R7：建卡时的 summary 已经是「⏳ 正在生成…」，
                                          # 所以这里把「上次预览」记成**刚刚**——第一帧不必再写一次
                                          # （限频窗口到点后才更新成真实进展）
                                          "ck_summary_at": now,
                                          "ck_summary": _cards.summary_text(display),
                                          # R4：这一张卡显示的是累积全文里的哪一段
                                          # （`text[ck_offset:]`）。第一张卡从 0 开始。
                                          "ck_offset": 0,
                                          # 这一回合已经**封掉**的卡（按顺序）—— 卡链的唯一记录
                                          "ck_cards": [],
                                          # v0.7.0 P1：/stop 重绘只读「这张卡实际写出的正文」；
                                          # 绑定漂移校验用建卡时的 session_id。
                                          "session_id": _panel.bound_session_id(chat) or "",
                                          "turn_id": str(turn_id or ""),
                                          "answer_gen": _panel.answer_generation(
                                              chat, require_binding=True),
                                          "last_rendered_body": display,
                                          "last_failed_frame": ""})
                # ⚠️ **这一笔必须留在帧路径里**（R9 审计高-1 的另一半）：cardkit 的 seed 帧
                # 是 `cardkit.v1.card.create` + `im.v1.message.create/reply` **两次网络调用**，
                # 走的是 `_ld_write_with_retry` **而不是** `_ld_send_card` ⇒ 没有任何底层收口点
                # 能替它记账。撤掉它 ⇒ 默认传输（cardkit）下这一帧等于没发生。
                # 口径仍是「**帧**数」：两次网络调用算**一帧**（否则同一件事两处计数，
                # 详见 `context.note_frame_ok` 的说明与 README 的「写卡帧数」一条）。
                _context.note_frame_ok()      # R9：建实体 + 发实体卡 = 这一帧真的有东西发出去了
                return True
            # ⚠️ 同理（见上面 CardKit 那处）：**建卡那一帧**还没有 id ⇒ 只能是基数页脚；
            # 短码从第二帧起才有。
            card = self._ld_build_card(display, streaming=True,
                                       panel=self._ld_panel(chat, report_empty=bool(finalize)),
                                       footer=self._ld_footer(chat_id=chat, started=now))
            result = await self._ld_send_card(chat, card, reply_to=reply_to)
            if result is None or not getattr(result, "success", False):
                return self._ld_stream_fail(
                    f"建卡失败（{getattr(result, 'error', 'unknown')}）")
            message_id = getattr(result, "message_id", "") or ""
            if not message_id:
                return self._ld_stream_fail("建卡成功但没拿到 message_id")
            self._ld_track(message_id, chat)
            self._ld_stream_put(key, {"message_id": message_id, "chat_id": chat,
                                      "t0": now, "last": text, "last_at": now,
                                      "frames": 0, "skipped": 0,
                                      "strips": 1 if stripped else 0,
                                      "session_id": _panel.bound_session_id(chat) or "",
                                      "turn_id": str(turn_id or ""),
                                      "answer_gen": _panel.answer_generation(
                                          chat, require_binding=True),
                                      "last_rendered_body": display,
                                      "last_failed_frame": ""})
            # ⚠️ 这里**不再**记一笔：首发建卡发出去的正是 `_ld_send_card`，账本已经在
            # 那个底层收口点记过了（R9 审计中-1 的修法）。**一次写只记一笔**必须由构造保证，
            # 不能靠「记得别在调用方也写一遍」—— 那种纪律在下一个调用点就会静默失守
            # （同一件事两处真相，见 docs/lessons.md 推论 13）。
            return True
        message_id = state["message_id"]
        if finalize:
            # R6a：收尾帧做 markdown 卫生（删游离 `**` + H1–H3 降级 + **同口径字节闸门**）。
            # 为什么不能每帧做：流式帧的文本必须是**前缀链**（上游按「最后一次成功发出的
            # 帧文本是可见前缀」记账），中间帧改写会让前缀链断掉 ⇒ 回答被重发一遍。
            # 收尾帧之后不再有帧，所以在这里改写是安全的；而且用户最终看到的就是这一帧。
            # 这条判据与 `send()` / `edit_message(finalize=True)` / `/stop` 重绘一致：
            # 四处都是「完整文本」的写入（R6a 审计中-3）。
            # R4：收尾只封**最新那张**卡，正文同样是本卡那一段；封掉的那几张保持原样。
            # ⚠️ 卫生必须作用在**这一段**（切片之后）而不是累积全文：改写可能让前后长度不等
            # （每个降级标题 −1~+2 字节），那时 `ck_offset` 就不再是切点 ⇒ 两张卡的接缝处
            # 会凭空吞掉/重复几个字符。切片之后的这一段本身就是**完整文本**（R4 的切点只在
            # 行首、且避开代码区），所以它满足「只在完整文本上做卫生」这个判据。
            tail_offset = int(state.get("ck_offset") or 0)
            if self._ld_body_source() == "own" and tail_offset > len(display):
                # core 权威终稿可被 `_adopt_final_text` 整体替换成更短文本；卡链旧 offset
                # 若落在新终稿之外，继续切会得到空串 ⇒ 终稿字节丢失。own 模式回退整段。
                tail_offset = 0
            tail_visible = _sanitize_for_send(display[tail_offset:])
            card = self._ld_build_card(tail_visible or " ", streaming=False,
                                       panel=self._ld_panel(chat, report_empty=True),
                                       footer=self._ld_frame_footer(state))
            result = await self._ld_update_card(chat, message_id, card)
            if result is None or not getattr(result, "success", False):
                return self._ld_stream_fail(
                    f"收尾帧失败（{getattr(result, 'error', 'unknown')}）")
            self._ld_stream_pop(key)
            self._ld_forget(message_id)
            # 能走到这一行 = 本回合 native 全程可用。这是**自证**：帧一旦失败，内核会
            # 关掉本回合的 native 并改走 send/edit，**不会再发 finalize 帧**（见
            # _TRANSIENT_CODES 的说明）—— 所以这行日志本身就是「native 还在工作」的证据，
            # 也是发现「悄悄退回纯文本」的唯一线索（docs/lessons.md 推论 1）。
            logger.info("[larkdeck] native 流式收尾：更新 %d 帧（跳过 %d 帧）",
                        int(state.get("frames") or 0) + 1, int(state.get("skipped") or 0))
            _log_turn_selfcheck(chat, self._ld_transport(), int(state.get("frames") or 0) + 1,
                                strips=int(state.get("strips") or 0) + (1 if stripped else 0),
                                trace=_ld_trace_id(message_id))
            # 记账在 `_ld_update_card` 里（收尾就是一次整卡替换）—— 这里不再重复记。
            return True
        if text == state.get("last"):
            return True
        last_at = state.get("last_at")
        if (state.get("last") and isinstance(last_at, (int, float))
                and now - last_at < _STREAM_MIN_INTERVAL):
            # 节流窗口内的中间帧：跳过，等下个 tick（首帧不节流）
            self._ld_stream_put(key, {**state, "skipped": int(state.get("skipped") or 0) + 1})
            return True
        if stripped:
            # R11-A7：本帧**真的剥掉了**核心叠加的进度块 —— 累计进回合状态，供收尾那条
            # 自检汇总打印（真机没有读卡接口，「剥了几帧」是正文净化生效的唯一凭据）。
            # 放在节流早返回**之后**：被节流跳过的帧什么都没渲染，也就没有「剥」这回事。
            state = {**state, "strips": int(state.get("strips") or 0) + 1}
        # ⚠️ 「这个回合要不要走元素通道」的判据**只有一个**：`card_id` 在不在。降级时帧路径会把
        # `card_id` 清成空串（见下面的 `DEGRADE` 分支）—— 以前这里还额外查了一次 `ck_degrade`，
        # 那是**同一件事的第二处机制**：变异证明它是死代码（把这一查去掉，门禁全绿）。
        # `ck_degrade` 现在只承担一件事：**记住降级的原因**（供日志/summary/后续阶段读）。
        # R4：本卡那一段（`text[ck_offset:]`）—— **两条车道**（元素写 / 整卡 patch）都用它，
        # 所以算在分支之前。patch 传输没有切卡（`ck_offset` 缺省 0）⇒ `visible is display`。
        offset = int(state.get("ck_offset") or 0)
        visible = display[offset:]
        card_id = str(state.get("card_id") or "")
        if card_id:
            # ---- CardKit：本实现只写元素内容（**不做整卡替换** —— 那会关闭流式会话。
            # 元素级/批量接口其实可以在流式期间用，见 docs/plan-6-effects.md 的「重大更正」）----
            # **正文的容量闸门**（口径与建实体那两道墙同源）：这里量的是**要写进元素的那一段**，
            # 单位必须是 **JSON 转义后的字节**，不是原始 utf-8 —— 飞书拒的是**整卡 JSON**，而
            # `"` `\` `\n` 在 JSON 里会翻倍。R1 审计实测：正文 `"\n"*100000 + "a"*27000`
            # 原始 127000 字节（本地闸门放行），真实卡片 JSON 是 **227291 字节** ⇒ 必被拒 ⇒
            # 帧失败 ⇒ 上游补 finalize + `_first_send` ⇒ **DM 两张卡**。
            #
            # ---- R4 卡链：本卡装不下这一段 ⇒ **封旧卡 + 开新卡**，正文只写新卡那一段 ----
            # ⚠️ **账本口径（R9 收口时核过，别以为这里漏记）**：切卡这一帧会做**两次写**
            # （封旧卡 `_ld_update_card` + 建新实体并发出 `_ld_ck_create`），而账本口径是
            # 「**帧**真的有写出」，所以这一帧只 +1（由 `_ld_update_card` 记那一笔；
            # `_ld_ck_create` 是替本帧建下一张卡的载体，不再单独记 —— 与 seed 帧同一条规矩）。
            # 切卡本身**没有独立用例**（要造 4 万字以上的帧），所以这里只留口径、不留断言；
            # 想钉它得先有一个「真跑切卡」的用例，别拿这条注释当已验证。
            # 触发阈值比硬上限保守（`_CK_SPLIT_SEAL_AT` = 硬上限的一半）：留出「（续下一条）」
            # 那行、以及新卡要一次装下剩余整段的余量。切完仍可能超上限（说明这一帧的增量太大、
            # 单张新卡也装不下）⇒ 下面那道闸门会用**硬上限**再判一次。
            if _card_body_bytes(visible) > _CK_SPLIT_SEAL_AT:
                split_state = await self._ld_ck_split(chat, display, state, offset, reply_to, now)
                if split_state is None:
                    # 切不开 ⇒ 这一帧只能回落。**字节数照旧打出来**（运维第一眼要的就是数字），
                    # 复用另一条闸门的那句日志（同一件事：正文超过单卡能装下的量）。
                    _log_ck_over_budget_once(_card_body_bytes(visible))
                    return self._ld_stream_fail("正文超过单卡硬上限且这一帧切不出新卡")
                state = split_state
                self._ld_stream_put(key, state)
                offset = int(state.get("ck_offset") or 0)
                visible = display[offset:]
                state = {**state, "last_rendered_body": visible,
                         "session_id": state.get("session_id") or ""}
                self._ld_stream_put(key, state)
                card_id = str(state.get("card_id") or "")
            # ⚠️ 这里**曾经**还有一道 `_card_body_bytes(visible) > 硬上限 ⇒ fail-open` 的闸门。
            # R4 起它是**死代码**：上面那条切卡判据已经把两种情况都收口了 ——
            #   * `visible` 没超封卡阈值（≤ 硬上限 × 0.5）⇒ 不可能超硬上限；
            #   * 超了 ⇒ 要么切卡成功（新卡的 `visible` ≤ 尾巴预算 = 硬上限 × 0.9），
            #     要么切不开 ⇒ 在那条分支里就 fail-open 了。
            # 也就是说 `body_bytes` 永远是 ≤ 0.9 × 硬上限 ⇒ 这条件恒假。
            # 留着它的代价不是「多一行」，而是**它会骗过变异验证器**：R8 收口那一轮实测
            # `CK23`（把闸门拆掉）在整棵树上 🟢 —— 「撤掉修复必须变红」这条纪律对死代码无解。
            # 所以**删掉**，并把那条变异重新对准**真正**的那道闸门（切不开时的早返回）。
            #
            # ⚠️ 2026-09-14 二次教训：这段死代码**回来过一次** —— R9 收口的补丁重基在更早的
            # `e081d89` 上（那时它还在），`git apply --3way` 把这一区域判给了「theirs」，
            # 于是 f32e4ae 的删除被**静默回滚**，而四门禁与 `-k R9` 全绿（只有全量跑才发现
            # `CK23` 又变绿）。所以：**三方合并之后必须重跑全量变异**，而且删死代码这件事
            # 本身要靠「那条变异得是红的」来守卫。
            elems = self._ld_ck_elems(state)
            dead = state.get("ck_dead")
            dead = dead if isinstance(dead, set) else set()
            # 写失败的装饰元素后续不再尝试（省配额、也不再刷日志）
            live_elems = [e for e in elems if e not in dead]
            # ⚠️ 正文写的是**本卡那一段**（`visible`），不是累积全文 —— 写全文会让新卡把
            # 已经封掉的那几段**重放一遍**（R4 最大的观感坑，变异 `R4-1` 钉住）。
            # 装饰（面板/页脚）与预览（summary）照旧用整回合的语义。
            # ⚠️ 面板那两块**按关键字**传：`_ck_plan` 的第 3 个位置参数是**元素表**，
            # 用 `*parts` 展开会把工具块塞进 `elems`（实测症状：帧异常 ⇒ 整回合掉 native）。
            _panel_body, _panel_tools = self._ld_panel_parts(
                chat, report_empty=bool(finalize))
            ops = _ck_plan(visible, _panel_body, live_elems, self._ld_frame_footer(state),
                           panel_tools_text=_panel_tools, streaming=not finalize)
            live_state = dict(state)
            ok, seq_after, failed = await self._ld_ck_apply(card_id, ops, _ck_seq(state),
                                                           live_state)
            degrade_code = int(live_state.get("ck_degrade") or 0)
            if ok and not degrade_code:
                # R7：**会话列表预览**（`card.settings`）——限频、失败只标死、绝不影响这一帧
                seq_after, summary_extra = await self._ld_ck_maybe_summary(
                    card_id, display, live_state, seq_after, now)
                # `live_state` 里带着装饰失败时标下的 `ck_dead`，所以这里用它的并集
                self._ld_stream_put(key, {**live_state, "ck_dead": live_state.get("ck_dead") or set(),
                                          # 装饰的「已写成功」记账（未变化不重写的判据）
                                          "ck_decor": live_state.get("ck_decor") or {},
                                          "last_rendered_body": visible,
                                          "session_id": state.get("session_id") or "",
                                          "last": text, "last_at": now,
                                          "ck_seq": seq_after,
                                          "frames": int(state.get("frames") or 0) + 1,
                                          **summary_extra})
                # ⚠️ **这一笔必须留在帧路径里**（R9 审计高-1）：这条分支每帧只写 `card_element.content`
                # + 可选的装饰 `batch_update`，**不经过** `_ld_send_card` / `_ld_update_card` ——
                # 也就是说默认传输（cardkit）的真机主路径上，账本的自证只靠这一行。
                # 撤掉它 ⇒ `/larkdeck status` 永远说「最近写卡：无记录 · 累计 0 次」，
                # 而用户眼前正躺着一张逐字往外冒的卡（审计的 X3/X13 变异：四门禁全绿）。
                # 判据是「这一帧真的写出去了」：装饰没变、只写了正文也算（正文就是写出去的东西）。
                _context.note_frame_ok()          # R9：元素通道这一帧真的写出去了
                return True
            if ok and degrade_code:
                # 这一帧**正文写成功了**，但同一帧的装饰批量拿到了卡级死法（会话是在两次调用
                # 之间被关掉的）。这一帧算成功，但**降级决定必须落进状态**：否则下一帧又去写
                # 一个已经关掉的会话（R5 审计高-1 的另一半：决定写进 `live_state`、却在成功
                # 分支被丢掉）。同时清 `card_id`，让后续帧走 patch。
                _log_ck_degrade_once(degrade_code)
                live_state["last_rendered_body"] = visible
                live_state["session_id"] = state.get("session_id") or ""
                self._ld_stream_put(key, {**live_state, "ck_degrade": degrade_code, "card_id": "",
                                          "last": text, "last_at": now, "ck_seq": seq_after,
                                          "frames": int(state.get("frames") or 0) + 1})
                # 正文元素**已经写成功**（`ok` 为真），所以这一帧确实有东西到了飞书；
                # 装饰批量拿到的卡级死法只把「后续走哪条通道」改了，不改变「这一帧写出去了」。
                _context.note_frame_ok()          # R9：正文元素写成功、降级决定已落地
                return True
            if degrade_code:
                # ── `DEGRADE`：元素通道死了，但**消息还在** ⇒ 用 `message.patch` 把同一张卡
                # 换成普通卡继续写（真机实测 patch 能覆盖实体卡消息，`--lanes`）。
                # 为什么值得为它写一条车道：不降级就是「本帧失败 ⇒ 内核停用本回合 native ⇒
                # 用户后面看到纯文本」，而这里完全可以保住卡片（只是没有逐字打字机）。
                # **绝不另建卡**：patch 打在原 message_id 上，DM 里始终只有一张卡。
                # ⚠️ 状态从 `live_state` 出发（不是旧 `state`）：本帧算出来的 `ck_dead`/`ck_decor`
                # 不能被降级分支悄悄丢掉 —— 「同一件事两处真相」是本项目最怕的形态（审计低-5）。
                _log_ck_degrade_once(degrade_code)
                # ⚠️ 降级后写的也是**本卡那一段**（同 R4：写全文会把封掉的几段重放一遍）
                card = self._ld_build_card(visible, streaming=True,
                                           panel=self._ld_panel(chat),
                                           footer=self._ld_frame_footer(state))
                result = await self._ld_update_card(chat, message_id, card)
                if result is None or not getattr(result, "success", False):
                    self._ld_stream_put(key, {**live_state, "ck_degrade": degrade_code,
                                              "card_id": "", "ck_seq": seq_after})
                    return self._ld_stream_fail(
                        f"降级到 patch 后仍然失败（{getattr(result, 'error', 'unknown')}）")
                self._ld_stream_put(key, {**live_state, "ck_degrade": degrade_code, "card_id": "",
                                          "ck_seq": seq_after, "last": text, "last_at": now,
                                          "last_rendered_body": visible,
                                          "session_id": state.get("session_id") or "",
                                          "frames": int(state.get("frames") or 0) + 1})
                # 记账在 `_ld_update_card` 里（降级这一帧就是一次整卡 patch）—— 不再重复记。
                return True
            # 失败：**只把序号推进**（绝不回退，见 `_ld_ck_apply` 的说明），
            # 不动 `last`/`last_at`/`frames` —— 让下一帧还能把同一段文本重试一次。
            #
            # ⚠️ **必须从 `live_state` 出发**（R11-B3）：`_ld_ck_apply` 会把这一帧算出来的
            # 结论写进它（`ck_dead` / `ck_decor` / 将来的新键），从旧 `state` 出发就等于
            # **只保留了我们记得手动抄过来的那三个** —— 以后任何人在 `_ld_ck_apply` 里新写
            # 一个键，都会在「正文失败」这一支上**静默消失**，而且没有任何门禁看得见
            # （同一件事两处真相的又一个形态，见 docs/lessons.md 推论 13）。
            # 下面三行是**点名保留旧值**，不是「拷贝恰好没被改」：读者一眼能看出这一帧的意图。
            self._ld_stream_put(key, {**live_state,
                                      "last": state.get("last", ""),
                                      "last_at": state.get("last_at"),
                                      "frames": int(state.get("frames") or 0),
                                      "ck_dead": live_state.get("ck_dead") or set(),
                                      # 装饰**在正文之前写**，正文失败时装饰可能已经写成功 ⇒
                                      # 记账要跟着走，否则下一帧会把没变的装饰重写一遍
                                      "ck_decor": live_state.get("ck_decor") or {},
                                      "ck_seq": seq_after})
            if failed is not None and failed.role == _CK_ROLE_PANEL:
                # 这一条必须**单独留痕**：正文已经写成功了，面板失败意味着那张卡
                # 会停在「正文新、面板旧」的半更新态 —— 而唯一判据就是这次返回码。
                _log_ck_panel_write_failed_once()
            return self._ld_stream_fail(
                failed.fail_reason() if failed else "CardKit 写元素失败（没有可写的元素）",
                code=(failed.code if failed else None))
        # ⚠️ 这条车道（patch 传输 / 降级之后）同样只写**本卡那一段**：写整段会让降级后的卡
        # 把已经封掉的几段重放一遍（与元素车道同一条纪律）。
        card = self._ld_build_card(visible, streaming=True,
                                   panel=self._ld_panel(chat, report_empty=bool(finalize)),
                                   footer=self._ld_frame_footer(state))
        result = await self._ld_update_card(chat, message_id, card)
        if result is None or not getattr(result, "success", False):
            return self._ld_stream_fail(
                f"帧更新失败（{getattr(result, 'error', 'unknown')}）",
                code=getattr(result, "code", None))
        self._ld_stream_put(key, {**state, "last": text, "last_at": now,
                                  "last_rendered_body": visible,
                                  "session_id": state.get("session_id") or "",
                                  "frames": int(state.get("frames") or 0) + 1})
        # 记账在 `_ld_update_card` 里（patch 传输每帧一次整卡替换）—— 不再重复记。
        return True

    def _ld_stream_fail(self, reason: str, code: Any = None) -> bool:
        """一帧失败：**必须留下日志**，然后返回 False 让内核回落。

        为什么不能只是 ``return False``：失败会让内核**关掉本回合的 native 流式**，
        之后输出改走 ``send()``（可能变成多条纯文本消息）—— 而这是**静默**的，
        用户在飞书那侧只会觉得「卡片怎么变成一条条消息了」。限流：同一进程 30 秒一条，
        既留痕又不刷屏。
        """
        # R9：失败**每一次都记**（不跟着日志限流）—— 用户问「刚才那回合为什么掉成纯文本」时，
        # 卡片要答得出原因；日志只有 30 秒一条，且用户看不到日志。
        _context.note_frame_fail(reason)
        # R11-C2：同一个收口点顺手记两条**用户看不见但排障必须知道**的事 ——
        # ① 非零响应码（→ `/larkdeck status` 的错误码 top-N）；
        # ② 「本回合卡片车道被放弃」（→ 掉回纯文本的用户可见次数）。
        # 挂在**这一个**收口点，是因为它正是「帧失败」被记录的地方（12 个调用点共用），
        # 不在这里记就得在 12 处各记一遍 —— 那是「同一件事两处真相」的标准入口。
        _context.note_response_code(code)
        _context.note_plaintext_fallback(reason)
        # A3：失败收口也打一条自检汇总 —— 停在失败上的回合同样要能判定「面板/页脚有没有内容」
        # `strips=-1` 与 `frames=-1` 同义：这条路**没有正文净化可言**（帧没写出去）。
        _log_turn_selfcheck("", self._ld_transport(), -1, strips=-1)
        now = time.monotonic()
        # 限流状态挂在**函数对象**上（不是 self）：本方法同名于类属性，裸名字在方法体里
        # 不在作用域内，必须经类名取 —— 写成 ``getattr(_ld_stream_fail, ...)`` 会
        # NameError（第一次跑就撞上了）。
        if now - getattr(LarkDeckMixin._ld_stream_fail, "_at", 0.0) >= 30.0:
            LarkDeckMixin._ld_stream_fail._at = now  # type: ignore[attr-defined]
            logger.warning("[larkdeck] native 流式帧失败（%s）—— 本回合 native 将被内核停用，"
                           "后续输出回落 send/edit（可能变成多条纯文本）", reason)
        # V2 修复：失败退出必须停掉本回合心跳，否则终态后仍会每 3s 写卡。
        self._ld_heartbeat_cancel_all()
        return False

    def _ld_stream_get(self, key: str) -> Optional[Dict[str, Any]]:
        """读回合状态（**顺带刷新活跃度**）。

        ⚠️ 活跃度用**独立字段** `alive_at`，不复用 `last_at`：后者是**帧节流**用的
        （`now - last_at < _STREAM_MIN_INTERVAL`），拿它当存活判据会让「节流跳过的帧」看起来
        像「这个回合死了」（R5 审计的低-6）。刷新在这里做，是为了让「查过状态」也算活动，
        与 `_ld_state` 那边 `_ld_known` 的行为对称。
        """
        with self._ld_lock:
            state = self._ld_streams.get(key)
            if state:
                state["alive_at"] = time.monotonic()
                return dict(state)
            return None

    def _ld_stream_put(self, key: str, state: Dict[str, Any]) -> None:
        with self._ld_lock:
            if len(self._ld_streams) >= _MAX_STREAMS and key not in self._ld_streams:
                # ⚠️ 只淘汰**真正泄漏**的流，**绝不按「最近活动」淘汰**。
                # 理由：`last_at` 只在有正文帧时推进，而一个跑十分钟工具的回合期间
                # 只有 panel 在变、`_ld_stream_put` 根本不被调用 —— 那种流看起来
                # 「很陈旧」，实际正活跃。踢掉它，下一帧就会因为查不到状态而
                # **另发一张新卡**（重复卡 + 老卡永久停在流式态），正是这里要防的事。
                # 所以阈值取**小时级**，专门收核心没发 finalize 的泄漏回合。
                # 存活判据是 `alive_at`（每次 get/put 都刷新），**不是** `last_at`
                # （后者只在成功写帧时推进，且被帧节流读 —— 拿它当存活判据会误踢活跃回合，
                # R5 审计低-6）。
                now = time.monotonic()
                stale = [
                    other for other, value in self._ld_streams.items()
                    if now - float(value.get("alive_at") or value.get("last_at")
                                   or value.get("t0") or 0.0)
                    > _STREAM_LEAK_SECONDS
                ]
                for other in stale[: max(1, _MAX_STREAMS // 4)]:
                    self._ld_streams.pop(other, None)
                    self._ld_card_lock_drop(other)
                if len(self._ld_streams) >= _MAX_STREAMS:
                    # 一个都没到泄漏阈值 = 真的并发了很多活跃回合。软超限：照常插入、
                    # 只告警（宁可多留状态，也不能踢活跃流造重复卡）；硬上限兜底内存。
                    if len(self._ld_streams) >= _MAX_STREAMS * _STREAM_HARD_CAP_FACTOR:
                        oldest = sorted(self._ld_streams.items(),
                                        key=lambda kv: kv[1].get("alive_at",
                                                                 kv[1].get("last_at",
                                                                           kv[1].get("t0", 0.0))))
                        for other, _ in oldest[: max(1, _MAX_STREAMS // 4)]:
                            self._ld_streams.pop(other, None)
                            self._ld_card_lock_drop(other)
                        logger.error("[larkdeck] 并发流已达硬上限 %d，被迫淘汰最旧的回合"
                                     "（可能有回合的卡片停在流式态）",
                                     _MAX_STREAMS * _STREAM_HARD_CAP_FACTOR)
                    else:
                        logger.warning("[larkdeck] 并发流超过软上限 %d 且无可回收的泄漏流"
                                       "（活跃回合不淘汰，仅告警）", _MAX_STREAMS)
            # 每次写入都盖一次活跃度戳（`alive_at` 与帧节流的 `last_at` 是两件事）
            state.setdefault("alive_at", time.monotonic())
            state["alive_at"] = time.monotonic()
            self._ld_streams[key] = state

    def _ld_card_lock_drop(self, key: str) -> None:
        """丢掉某个回合的写锁（回合结束/被淘汰时调用）。

        ⚠️ **仍被持有的锁不摘**（V4.6，审计 A 中-6 实测）：`/stop` 与在飞帧重叠时，
        旧实现把 table 里的锁摘掉 ⇒ 下一次 `_ld_card_lock(key)` 返回**另一把**新锁，
        而旧锁还在被帧路径持有 ⇒ 同一回合出现两个持有者，锁的全部保证当场失效
        （审计复现：`old locked True / new locked False`，随后状态被复活、卡被重画两次）。
        """
        locks = getattr(self, "_ld_card_locks", None)
        if not isinstance(locks, dict):
            return
        lock = locks.get(str(key))
        if lock is None or getattr(lock, "locked", lambda: False)():
            return
        locks.pop(str(key), None)

    def _ld_stream_pop(self, key: str) -> None:
        with self._ld_lock:
            self._ld_streams.pop(key, None)
        # 回合结束 ⇒ 丢掉这一回合的写锁（锁对象若正被持有，`async with` 仍会正常释放它，
        # 只是从锁表里摘掉：下一回合重建一把新的，绝不与旧回合共用）。
        self._ld_card_lock_drop(key)

    # ------------------------------------------------- 显示 chrome（工具行是否进正文）
    def format_tool_event(self, event: Any, *, mode: str = "all",
                          preview_max_len: int = 40) -> Optional[str]:
        """**吃掉**核心的工具行（默认），还是原样交给父类渲染。

        ⚠️ **2026-09-14 实测更正：这个扩展点在 Hermes 0.21.1 的生产路径上没有被调用。**
        父类确实是这么设计的（docstring 明写「adapters without editing/rich text override to
        None」），但本版本里 `format_tool_event` 的唯一调用点是
        `gateway/stream_dispatch.py` 的 `_dispatch_tool_call`，而那个 `GatewayEventDispatcher`
        **只在测试里被构造**（全树 grep：生产侧零引用）。真机上的工具行由
        `gateway/run_turn_runner.py` 的 `_progress_build_message` 生成，native 流式下由
        `gateway/stream_consumer.py` 的 `"\n\n---\n".join((accumulated, progress))`
        **合成进同一帧** —— 所以「正文干净」这件事**不能靠这个覆盖**。

        它留着是**向前兼容的保险**：哪天核心把它接回来就自动生效（那时同一份配置语义仍然对）。
        真正的活杠杆是 :func:`_strip_core_progress` / :meth:`_ld_body_text`（R11-A7），
        两者共用 `progress_lines_in_body` 这一个配置键。

        ⚠️ 缺了不致命但要上报（`compat.DISPLAY_CHROME_ATTRS`）：父类哪天改名，这里会静默失效
        —— 那时连保险也没了，而启动自检不会说一句话。
        """
        try:
            if _cfg("progress_lines_in_body"):
                return super().format_tool_event(
                    event, mode=mode, preview_max_len=preview_max_len)
            return None
        except Exception:
            # 渲染 chrome 是**装饰**：任何异常都不许影响消息本身，静默吃掉即可
            logger.debug("[larkdeck] 工具行渲染失败，吃掉这个事件", exc_info=True)
            return None

    # ------------------------------------------------------ 即时响应观感
    def _reactions_enabled(self) -> bool:
        """「处理中」表情反应开关 —— 对应 aiduPOP README 效果 1 里的「无输入提示」。

        Hermes 默认在用户消息上打一个 Typing 表情、处理完再撤掉；在飞书上这正是这个项目的
        「输入提示」。流式卡片本身已经提供了即时反馈，所以「无提示」是即时响应观感的一部分。

        ⚠️ 实现方式是**在子类里覆盖这一个判据**，而不是让用户去改宿主的环境变量
        ``FEISHU_REACTIONS``：覆盖自己的平台实现本来就是这个插件的存在方式（不变量 1），
        而且只影响我们这个实例、随时能用配置回退。内置没有这个方法时返回 True ——
        版本差异不该被我们当成「把反应关掉」的理由。
        """
        try:
            if not _cfg("reactions"):
                return False
        except Exception:  # pragma: no cover - 防御性
            return True
        parent = getattr(super(), "_reactions_enabled", None)
        if not callable(parent):
            return True
        try:
            return bool(parent())
        except Exception:  # pragma: no cover - 防御性
            return True

    # --------------------------------------------------- 中止信号（/stop 路径）
    async def interrupt_session_activity(self, session_key: str, chat_id: str,
                                         metadata: Optional[Dict[str, Any]] = None) -> None:
        """核心在 ``/stop``、``/new`` 时调我们 —— **这是中止态唯一的落地机会**。

        为什么不能只是「改个内存状态等下一帧」：``/stop`` 会让 stream consumer 直接
        return（``gateway/stream_consumer.py``：「Session reset: abandon rather than
        deliver stale deltas」），而 native 模式下 ``_abandon_native_stream`` 是**空操作**
        —— 于是**永远不会有收尾帧**。状态改在内存里、卡片纹丝不动，而且不报任何错。
        所以这里必须**自己把那张卡重绘成中止态**。

        纪律：
          * 全程包在 try 里，**永远不把异常往上抛**（这是核心的 ``/stop`` 路径，
            弄炸它等于 /stop 失效）；异常时只记日志。
          * 无论卡片重绘成功与否，都照常 ``super()`` —— 内核的「置停止标志 + 停打字」
            必须发生，那是这个方法的**本职**，我们不能因为它挡住中止。
          * 重绘用**现有的卡与现有的正文**（:attr:`_ld_streams` 里存着最后一帧的累积全文），
            不新增消息、不改内容，只换状态色。
        """
        # 状态先写内存（廉价、不会失败），这样无论后面哪一步出问题，本回合的颜色都是对的。
        try:
            self._ld_mark_stopped(chat_id)
        except Exception:  # pragma: no cover - 防御性
            logger.debug("[larkdeck] 中止状态写入失败", exc_info=True)

        # ⚠️ **先转发给内核，再重绘卡片** —— 顺序在 2026-09-13 被审计更正。
        # 原来把 super() 放在重绘之后，而重绘里有 await（可能多次串行 patch、
        # ``_run_blocking`` 又没有超时）：一旦这段被取消（网关关闭 / 上层 wait_for 超时）
        # 或长时间挂住，``except Exception`` 接不住 ``CancelledError``（它是 BaseException），
        # 内核那两件本职（``_active_sessions[session_key].set()`` + 停打字）就不会发生。
        # 中止是内核的职责、卡片只是装饰 —— 所以停止优先，重绘放最后。
        parent = super(LarkDeckMixin, self)
        method = getattr(parent, "interrupt_session_activity", None)
        if callable(method):
            # 签名按父类能力决定（``agent/interrupt_compat._accepts_keyword`` 是核心用的
            # 同一套判据）。⚠️ 必须拿**父类方法本身**去探 —— 曾经写成
            # ``getattr(type(parent), ...)``，而 ``parent`` 是 ``super()`` 对象、
            # ``type(parent)`` 恒为 ``super``，探针永远说「不支持 metadata」。
            # **不再**用运行时 ``except TypeError`` 兜底：那会把父类**调用两次**
            # （审计实测：父类内部抛 TypeError 时被跑了两遍，重复置停止事件 / 停打字）。
            if _compat.accepts_keyword(method, "metadata"):
                await method(session_key, chat_id, metadata=metadata)
            else:
                await method(session_key, chat_id)
        else:
            logger.debug("[larkdeck] 父类没有 interrupt_session_activity，只做卡片重绘")

        try:
            await self._ld_redraw_stopped(chat_id)
        except Exception as exc:  # pragma: no cover - 防御性
            logger.warning("[larkdeck] 中止态卡片重绘失败（中止已完成）: %s", exc, exc_info=True)

    def _ld_mark_stopped(self, chat_id: str) -> str:
        """把该 chat 对应会话标成中止（返回命中的 session_id，仅用于日志）。"""
        sid = _panel.mark_stopped(chat_id)
        if not sid:
            # 没有面板数据可改（本回合没有过程信息）—— 卡片仍然会被重绘成中止色，
            # 因为下面的 _ld_redraw_stopped 会显式带上 stopped 状态。
            logger.debug("[larkdeck] 中止：没有找到 %s 对应的面板会话", chat_id)
        return sid

    async def _ld_redraw_stopped(self, chat_id: str) -> bool:
        """把该 chat 自己发出的那张卡原地重绘成**中止态**。

        数据来源有两条（都在本实例里，不额外查网络）：
          1. :attr:`_ld_streams` —— ``chat:turn_id`` → 最后一帧的**累积全文**，
             native 路径下就是「用户中止时屏幕上已经打出来的那段」；
          2. :attr:`_ld_state` —— 非 native（edit / 降级）路径下没有活跃流，
             但那张卡是我们发的、``message_id`` 与最后渲染过的正文都记着。

        两条都找不到时**什么都不做**：凭空发一张新卡会多出一条消息（卡片是增强，
        不该因为中止而多出一条空消息）。
        """
        chat = str(chat_id or "").strip()
        if not chat or not getattr(self, "_client", None):
            return False
        with self._ld_lock:
            keys = [key for key, state in self._ld_streams.items()
                    if state.get("chat_id") == chat]
            fallback = None
            if not keys:
                # 非 native 路径：取这个 chat 最近更新过、且记着正文的那张卡
                candidates = [(value.get("last", 0.0), mid, value)
                              for mid, value in self._ld_state.items()
                              if value.get("chat_id") == chat and value.get("last_text")]
                if candidates:
                    fallback = max(candidates, key=lambda item: item[0])
        if keys:
            return await self._ld_redraw_stopped_keys(chat, keys)
        if fallback is None:
            # 提到 INFO：这一行是「中止后卡片为什么没变色」的唯一线索。
            # 静默失败是本项目的头号失败模式，所以不留 debug。
            logger.info("[larkdeck] 中止：这个 chat 没有可重绘的卡"
                        "（`_ld_streams` 无本 chat 的流，且 `_ld_state` 里没有带正文的卡 —— "
                        "可能还没建卡，或正文太大没保留副本）")
            return False
        _at, message_id, entry = fallback
        return await self._ld_redraw_one_stopped(chat, message_id, str(entry.get("last_text") or ""),
                                                 entry.get("t0"))

    async def _ld_redraw_stopped_keys(self, chat: str, keys: List[str]) -> bool:
        redrawn = False
        for key in keys:
            state = self._ld_stream_get(key)
            if not state:
                continue
            message_id = str(state.get("message_id") or "")
            if not message_id:
                continue
            async with self._ld_card_lock(key):
                redrawn_ok = await self._ld_redraw_one_stopped(
                    chat, message_id, self._ld_stop_body(chat, state), state.get("t0"))
            if redrawn_ok:
                redrawn = True
                # ⚠️ **重绘成功后必须清掉这个流状态**（2026-09-13 审计）：
                # 不清的话，每个被中止的回合都会在 ``_ld_streams`` 里留一条（key 是
                # consumer 的 uuid、永不复用），于是**之后每次 /stop 都会把该 chat 的
                # 全部历史中止卡重画一遍** —— 串行网络往返全发生在 super() 之前，
                # /stop 延迟随中止次数线性增长，还会把 _MAX_STREAMS 的泄漏预算吃光。
                # 清掉后若内核奇迹般再送 finalize 帧，send_stream_frame 会返回 False，
                # 内核按 fail-open 回落 edit（消息不会丢）。
                self._ld_stream_pop(key)
        if redrawn:
            logger.info("[larkdeck] 已把中止态重绘到卡片（chat=%s）", chat)
        return redrawn

    def _ld_stop_body(self, chat: str, state: Dict[str, Any]) -> str:
        """`/stop` 重绘正文选择（v0.7.0 P1.5）：

        own 模式只读 `last_rendered_body`（当前卡**实际写出的 visible slice**，
        尊重 `ck_offset`，不会把已封头段重放进最新卡）；字段缺失时按严格绑定取
        own，绝不回退 `state['last']`（那是 core 原始帧文本，可能含合成进度）。
        legacy 模式保持旧行为直到 P2b。
        """
        if self._ld_body_source() != "own":
            return str(state.get("last") or "")
        if "last_rendered_body" in state:
            return str(state.get("last_rendered_body") or "")
        return self._ld_stream_own_text(chat, state)

    async def _ld_redraw_one_stopped(self, chat: str, message_id: str, text: str,
                                     started: Any) -> bool:
        """把**一张**卡重绘成中止态。返回是否成功。

        R6a 审计中-3：这里的正文**也要过卫生** —— 它是**完整文本**（不是累积帧的中间态），
        而且来源是我们自己的追踪表（`_ld_streams` 的 `last` / `_ld_state` 的 `last_text`），
        收尾帧写的就是同一段文本。不做的话同一段模型输出会长成两个样子（审计实测：

            正常收尾写成： ``**磁盘报告**\n占用前三：A、B``
            被 /stop 写成： ``# 磁盘报告\n占用前三：**A、B``

        ）—— 用户在「掉 native / 被 /stop / 关掉 cards」的回合里看不到任何卫生。
        """
        _ld_visual_engine()
        _ld_card_status_header_enabled()
        _ld_show_reasoning()
        self._ld_heartbeat_cancel_chat(chat)
        try:
            # 面板是状态色**唯一**的载体，所以这里**强制**给一个 stopped 面板：
            # 只靠 `_ld_panel` 会踩到一个实测过的坑 —— 该回合还没有任何过程数据时
            # （模型还在思考、还没调工具），快照里什么都没有 ⇒ 面板为 None ⇒
            # 「状态改了、卡片没变、还不报错」。这正是本项目最怕的形态。
            panel = self._ld_panel(chat, report_empty=True) or _cards.unified_panel(
                status=_panel.STATUS_STOPPED)
            # ⚠️ 同上（审计 C1）：`/stop` 重绘是**用户最可能截图的那一帧**，而且它以前会把
            #    卡片上**已有的** 🔖 抹掉（用基数页脚重画 ⇒ 短码没了）。这里手上就有 message_id。
            stopped_footer = self._ld_frame_footer(
                {"message_id": message_id, "chat_id": chat,
                 "t0": started, "status": _panel.STATUS_STOPPED})
            if _ld_visual_engine() == "structured":
                stopped_view = self._ld_cardview(
                    chat, _sanitize_for_send(text) or " ", status="stopped")
                stopped_view.footer = stopped_footer
                card = _cards.apply_text_profile(_cardview.entity_skeleton(stopped_view),
                                                 _cfg_raw("text_profile"))
            else:
                panel = self._ld_panel(chat, report_empty=True) or _cards.unified_panel(
                    status=_panel.STATUS_STOPPED)
                card = self._ld_build_card(_sanitize_for_send(text) or " ", streaming=False,
                                           panel=panel, footer=stopped_footer)
            blob = json.dumps(card, ensure_ascii=False)
            if '"collapsible_panel"' not in blob:
                # 第十路审计：正文贴着飞书硬上限时，降载阶梯会把承载状态色的面板摘掉 ⇒
                # patch 发了、`code=0`、**载荷里没有颜色**，而日志还说「已把中止态重绘到卡片」。
                # 这条限流告警说明白「这次重绘没有颜色」——本条路径唯一诚实的线索。
                _log_no_colour_once()
            result = await self._ld_update_card(chat, message_id, card)
            if result is None or not getattr(result, "success", False):
                logger.warning("[larkdeck] 中止态卡片更新未成功（%s）",
                               getattr(result, "error", "unknown"))
                return False
            return True
        except Exception as exc:  # pragma: no cover - 防御性
            logger.warning("[larkdeck] 中止态卡片更新异常: %s", exc, exc_info=True)
            return False

    # ----------------------------------------------------------- send_clarify
    async def send_clarify(self, chat_id: str, question: str, choices: Optional[list],
                           clarify_id: str, session_key: str,
                           metadata: Optional[Dict[str, Any]] = None):
        """澄清交互卡 —— 按钮点击由本类的 ``_on_card_action_trigger`` 接住。

        无线索（开放式提问）时**必须**回落内置实现：内置会用文字发问并
        ``mark_awaiting_text``，靠网关的文本拦截器收答案。这是最可靠的路径。
        """
        fallback = lambda: super(LarkDeckMixin, self).send_clarify(  # noqa: E731
            chat_id, question, choices, clarify_id, session_key, metadata=metadata,
        )
        if not _cfg("clarify_cards") or not choices or not getattr(self, "_client", None):
            return await fallback()
        try:
            try:
                multi = _compat.clarify_multi_select(clarify_id)
            except Exception:  # pragma: no cover - compat 内部已兜底
                multi = False
            card = self._ld_build_clarify_card(
                question, list(choices), clarify_id=clarify_id,
                session_key=session_key, multi=multi)
            result = await self._ld_send_card(chat_id, card, metadata=metadata)
            if result is not None and getattr(result, "success", False):
                return result
            logger.warning("[larkdeck] 澄清卡发送未成功（%s），回落内置文字提问",
                           getattr(result, "error", "unknown"))
        except Exception as exc:
            logger.warning("[larkdeck] 澄清卡异常，回落内置文字提问: %s", exc, exc_info=True)
        return await fallback()

    # ------------------------------------------------------- 卡片点击回调
    def _on_card_action_trigger(self, data: Any) -> Any:
        """拦截 larkdeck 自己的按钮；其余点击原样交回内置实现。

        这是内置适配器在 ``_build_event_handler`` 里用
        ``register_p2_card_action_trigger(self._on_card_action_trigger)`` 注册的
        **绑定方法**，所以 MRO 优先的覆盖会自动生效 —— 无需另注册事件。

        交回内置实现那一步**必须自己再兜一层**：内置哪天改名/移除这个方法，
        ``super()`` 会抛 ``AttributeError`` 直接穿透进 SDK 回调线程，而这里正是
        「别人的卡」（审批卡等）的必经之路 —— 不能因为我们的兜底把点击整条炸掉。
        ``_on_card_action_trigger`` 已登记在 ``compat.CALLBACK_ADAPTER_ATTRS``。
        """
        try:
            event = getattr(data, "event", None)
            action = getattr(event, "action", None)
            value = self._ld_normalize_value(action)
            if isinstance(value, dict) and value.get(ACTION_KEY) == ACTION_CLARIFY:
                return self._ld_handle_clarify_click(event=event, action=action, value=value)
            if _cards.is_probe_value(value):      # 判据只有一处（见 cards.is_probe_value）
                return self._ld_log_probe_click(event=event, action=action)
        except Exception as exc:
            logger.warning("[larkdeck] 处理卡片点击时异常: %s", exc, exc_info=True)
        return self._ld_passthrough_click(data)

    @staticmethod
    def _ld_normalize_value(action: Any) -> Dict[str, Any]:
        """把点击载荷的 ``action.value`` 归一成 dict（含两个**实战踩过**的形态）。

        * ``value`` 可能不是 dict 而是 **JSON 字符串** —— 同类项目
          （hermes-feishu-streaming-card 的取值器）专门为此写了 ``json.loads`` 兜底。
          我们不兜的话，`.get(ACTION_KEY)` 会抛 AttributeError，被外层 except 接住后
          **静默交回内置实现** —— 用户的点击就是「点了没反应」（本项目最怕的失败形态）。
        * 表单提交按钮（``form_action_type=submit``）的回调 **``value`` 是空的**，
          数据在 ``action.form_value`` 里（HFC 的注释记了这件事）。我们现在不发表单卡，
          但一旦发（比如 2.0 澄清卡要组合多个输入），没有这一层就是点了没反应。
          只从 ``form_value`` 里取**我们自己的键**，绝不整体合并（免得污染判断）。
        """
        raw = getattr(action, "value", None)
        value: Dict[str, Any] = {}
        if isinstance(raw, dict):
            value = dict(raw)
        elif isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                value = parsed
        if ACTION_KEY not in value:
            form_value = getattr(action, "form_value", None)
            if isinstance(form_value, dict):
                for key in (ACTION_KEY, "clarify_id", "session_key", "question", "answer"):
                    if key not in value and form_value.get(key) is not None:
                        value[key] = form_value[key]
        return value

    def _ld_log_probe_click(self, *, event: Any, action: Any) -> Any:
        """**方言探针**：把一次探针点击的原始载荷按 INFO 打进日志，别的什么都不做。

        为什么需要它：卡片方言的结论只能靠**真机点击**确立（官方文档 + 一手实测，
        不抄第三方注释 —— 见 ``AGENTS.md`` 不变量 5）。而点击事件被 WebSocket 送进
        正在跑的网关，探针脚本接不到，所以「到底有没有点进来、载荷长什么样」只能由
        生产代码里这一处如实记录。

        它只在值里带 ``cards.PROBE_VALUE_KEY`` 时触发（真卡从不带这个键），
        所以不是常规流量上的噪声；返回「无卡片变更」，不改任何状态。
        """
        def _get(obj: Any, name: str) -> Any:
            return getattr(obj, name, None)

        operator = _get(event, "operator")
        context = _get(event, "context")
        logger.info(
            "[larkdeck] 探针点击到达 ✅ tag=%s option=%r input_value=%r value=%r "
            "open_id=%s chat_id=%s token=%s",
            _get(action, "tag"), _get(action, "option"), _get(action, "input_value"),
            _get(action, "value"), _get(operator, "open_id"),
            _get(context, "open_chat_id") or _get(context, "chat_id"),
            bool(_get(event, "token")),
        )
        return self._ld_card_response_safe()

    def _ld_passthrough_click(self, data: Any) -> Any:
        """把点击原样交回内置实现；内置没有该方法时安全收场，绝不抛进回调线程。"""
        fallback = getattr(super(), "_on_card_action_trigger", None)
        if not callable(fallback):
            logger.warning("[larkdeck] 内置适配器没有 _on_card_action_trigger，放弃这次点击")
            return self._ld_card_response_safe()
        try:
            return fallback(data)
        except Exception as exc:
            logger.warning("[larkdeck] 内置点击处理异常: %s", exc, exc_info=True)
            return self._ld_card_response_safe()

    def _ld_card_response_safe(self, card_data: Optional[Dict[str, Any]] = None) -> Any:
        """构造回调响应；给了 ``card_data`` 就**内联换卡**（省一次 API 调用）。

        前提（官方源码）：内置 ``_card_response(card_data=None)`` 内部构造
        ``CallBackCard(type="raw", data=card_data)`` ⇒ 回调响应里直接带上新卡，飞书客户端
        就地重绘。它同时天然规避「方言混用」：回调响应只接受**一整张卡**。

        三条纪律（缺一条都会变成用户可见的失灵）：
          * **绝不抛**：这个函数跑在 SDK 回调线程上，抛出去就是把一次点击整条炸掉 ——
            连「别人的卡」（审批卡）的点击都走这条路；
          * **换卡能力要探测**（``compat.accepts_positional``）：不同版本的内置形参可能改名，
            而我们是**位置传参**，真契约只是「能不能收下这张卡」；收不下就退回无参调用
            （= 不改卡，但点击仍然正确生效）；
          * **卡片构造在调用方完成**：这里只负责把已经算好的卡交出去，构造失败由调用方
            兜住并退回无参调用。
        """
        build = getattr(self, "_card_response", None)
        if not callable(build):
            return None
        if card_data is not None:
            if _compat.accepts_positional(build, 1):
                try:
                    return build(card_data)
                except Exception as exc:
                    # 兜一层：签名探测说是「能收」，实际却抛了（装饰器 / 内部 TypeError）。
                    # 不把这次点击变成错误，退回「不改卡」的无参响应。
                    #
                    # ⚠️ **这一层不许删**（R8 审计中-5：撤掉它四门禁全绿）。异常穿透的代价
                    # **不是**「什么都没发生」：它会一路冒到 `_on_card_action_trigger` 的外层
                    # `except`，于是走 `_ld_passthrough_click` → **内置实现**；而内置对不认识的
                    # value 的做法是把它当成一条合成命令 `"/card button {…载荷…}"`
                    # **发进会话**（Hermes `plugins/platforms/feishu/adapter.py:2083` /
                    # `2340-2355`），卡片还不换 —— 用户会在聊天里看到自己的点击载荷原文，
                    # 而看到的内容与「不改卡但点击生效」是两件完全不同的事。
                    # `test_clarify_inline_swap_fallback_never_returns_to_builtin` 钉这条。
                    logger.warning("[larkdeck] 内联换卡失败，退回无卡片变更: %s", exc)
                    return self._ld_response_without_card(build)
            logger.warning("[larkdeck] 内置 _card_response 不接受卡片实参 ⇒ 本次点击不换卡"
                           "（点击本身已生效，只是卡片没重绘）")
            return self._ld_response_without_card(build)
        try:
            return build()
        except Exception as exc:
            logger.debug("[larkdeck] _card_response 失败: %s", exc, exc_info=True)
            return None

    def _ld_response_without_card(self, build: Any) -> Any:
        """**无法内联换卡**时的响应：不改卡，但补一条「已提交」toast。

        走到这里的两种情况（都在**提交已经成功**之后）：
          * 核心那侧的 ``_card_response`` 不收卡片实参（老签名，审计低-1 实测的形态）；
          * 探测说「能收」、真调用却抛了（装饰器 / 内部错误，审计中-5 那层兜底）。

        两种情况下用户的处境一样：**答案已经送达 agent**，但卡片不会重绘。而以前这条路上
        是「卡片不动 + 无 toast + 只有一行 WARNING」—— 用户看到的是**点了没反应**，
        于是他会再点一次，而第二次必然 ``not committed`` ⇒ 吃一条**误报**的失败提示
        （答案早就交上去了）。

        为什么可以「不换卡却说话」：toast **不可能**覆盖卡片状态，所以它与
        「失败态绝不换卡」那条纪律不冲突（那条防的是「换卡把已确认退回待答」）。
        拿不到 toast 类（老 SDK）时退回不带卡的原响应 —— 与改前行为一致，**绝不抛**。

        `build` 由调用方传入（就是 ``self._card_response`` 本身），本函数**不再自己探测签名**，
        免得两条判据分叉。
        """
        response = self._ld_toast_response(kind="success", text_key="clarify.toast_submitted")
        if response is not None:
            return response
        try:
            return build()
        except Exception as exc:
            logger.debug("[larkdeck] _card_response 失败: %s", exc, exc_info=True)
            return None

    @staticmethod
    def _ld_toast_classes() -> Any:
        """``(P2CardActionTriggerResponse, CallBackToast)``；取不到就 ``(None, None)``。

        ⚠️ 懒取 + **可注入**：``test_units.py`` 是零 Hermes 依赖的，绝不能让它 import
        ``lark_oapi``（系统解释器上没有这个包 ⇒ 单测会以「与真实原因无关的失败」红掉）。
        单测把这两个类换成哑对象，验的是**我们怎么填字段**，不是 SDK 能不能 import。
        """
        try:
            from lark_oapi.event.callback.model.p2_card_action_trigger import (
                CallBackToast, P2CardActionTriggerResponse)
            return P2CardActionTriggerResponse, CallBackToast
        except Exception:
            return None, None

    def _ld_toast_response(self, *, kind: str, text_key: str) -> Any:
        """**只弹 toast、不动卡**的回调响应；构造不出来返回 ``None``（调用方退回无变更）。

        为什么需要它：失败态与「其他」提示态都**必须保住用户眼前那张卡** ——
        把卡换成别的东西，会让一次**迟到的重复点击**把「已确认」回退成「待答」，
        而那个错误是不可逆的（用户看到自己确认过的卡又变回待答）。toast 正是飞书为这种
        瞬时提示提供的原生机制（``CallBackToast{type, content, i18n}``）。
        ``i18n`` 一起填，客户端按自己的语言挑一份 —— 与卡片文案同一套规则。
        """
        response_cls, toast_cls = self._ld_toast_classes()
        if response_cls is None or toast_cls is None:
            return None
        try:
            text = _i18n.i18n_text(text_key)
            toast = toast_cls()
            toast.type = str(kind or "info")
            toast.content = str(text.get("content") or "")
            locales = text.get("i18n_content")
            if isinstance(locales, dict) and locales:
                toast.i18n = dict(locales)
            response = response_cls()
            response.toast = toast
            return response
        except Exception as exc:      # pragma: no cover - 防御性
            logger.debug("[larkdeck] toast 响应构造失败: %s", exc, exc_info=True)
            return None

    def _ld_toast_or_noop(self, *, kind: str, text_key: str) -> Any:
        """优先「只弹 toast、不动卡」，构造不出来就退回「无变更」响应（**绝不抛**）。"""
        response = self._ld_toast_response(kind=kind, text_key=text_key)
        if response is not None:
            return response
        return self._ld_card_response_safe()

    @staticmethod
    def _ld_build_clarify_card(question: str, choices: List[str], *, clarify_id: str,
                               session_key: str, multi: bool) -> Dict[str, Any]:
        """按 ``clarify_dialect`` 选澄清卡方言。

        默认现在是 **2.0**（``select_static`` / ``multi_select_static`` / ``input`` +
        组件级 ``behaviors``）。翻这个默认值的前提在 :data:`_DEFAULTS` 与 ``AGENTS.md``
        不变量 5 里写死了两条，**两条都在 2026-09-13 满足并留了证据**：
          ① 真机点一次 —— 探针 ⑫（2.0 ``select_static``）点下去后网关日志出现
             ``[larkdeck] 探针点击到达 ✅ tag=select_static option='opt_a'``；
             探针 ⑬⑭（真 2.0 澄清卡）点下去后出现 ``澄清提交未生效（clarify=probe-c2）``
             —— 说明点击**到达并正确解析出 clarify id**（探针卡没在网关登记澄清，所以
             「没东西可解」是预期，不是失灵）；
          ② ``check_clarify_e2e.py`` 的 2.0 场景全绿。
        取到不认识的值时按 1.0 处理（不猜、不抛）。
        """
        dialect = str(_cfg_raw("clarify_dialect") or "1.0").strip()
        if dialect == "2.0":
            return _cards.clarify_card_2(question, choices, clarify_id=clarify_id,
                                         session_key=session_key, multi=multi)
        return _cards.clarify_card(question, choices, clarify_id=clarify_id,
                                   session_key=session_key, multi=multi)

    @staticmethod
    def _ld_build_resolved_card(*, question: str, answer: Any,
                                user_name: str) -> Dict[str, Any]:
        """已答复卡必须与待答卡**同方言**（换方言会让飞书丢弃这一帧）。"""
        dialect = str(_cfg_raw("clarify_dialect") or "1.0").strip()
        if dialect == "2.0":
            return _cards.clarify_resolved_card_2(question=question, answer=answer,
                                                  user_name=user_name)
        return _cards.clarify_resolved_card(question=question, answer=str(answer),
                                            user_name=user_name)

    @staticmethod
    def _ld_typing_text_key() -> str:
        """「其他（我直接输入）」的提示文案按**当前方言**选（审计低-3）。

        1.0 澄清卡（``cards._clarify_elements``）只有按钮 + 一个「其他（我直接输入）」按钮，
        **根本没有输入框** —— 在那里说「请在输入框里输入答案」是错话（用户会去找一个不存在的东西）。
        2.0 卡有 ``input`` 组件，那条文案才成立。

        判据与 `_ld_build_clarify_card` / `_ld_build_resolved_card` 同源（都读
        ``clarify_dialect``，认不出的值按 1.0 处理）—— 三处各写一份字面量就会分叉，
        所以这里也照抄那条规则，并有单测同时钉两种方言。
        """
        dialect = str(_cfg_raw("clarify_dialect") or "1.0").strip()
        return "clarify.toast_typing" if dialect == "2.0" else "clarify.toast_typing_text"

    @staticmethod
    def _ld_clarify_answer(action: Any, value: Dict[str, Any]) -> "tuple[Any, str]":
        """从点击载荷里取出答案 —— 返回 ``(answer, mode)``，``mode`` ∈
        ``{"choice", "multi", "text", "none"}``。

        三种方言/组件各把答案放在不同字段（**官方回调文档 + SDK 模型**）：
          * 1.0 按钮：``action.value`` 里我们自己的 ``answer``（**choice**）；
          * 2.0 `select_static`（单选）：``action.option``（**字符串**）（**choice**）；
          * 2.0 `multi_select_static`（多选）：``action.options``（**string[]**，
            官方文档第 48 行；SDK 模型 ``p2_card_action_trigger.py`` 里
            ``options: List[str]`` 而 ``option: str``）→ 拼成 **JSON 数组字符串**（**multi**，
            与核心 ``_coerce_multi_select_text`` 的规范形式一致）；
          * 2.0 `input`：``action.input_value`` 是自由文本（**text**）。

        ⚠️ **2026-09-13 审计更正了字段名，并顺带纠正了一个更重要的判断**：
          1. 多选的值在 ``action.options``（**复数**），不是 ``option`` —— 这条是硬 bug：
             只读 ``option`` 时官方形状的载荷直接落到 ``mode="none"``，用户「选完没反应」。
          2. 答案形态：工具侧（``clarify_tool._clean_answer`` →
             ``_parse_multi_select_response``）**两种都能解**——JSON 数组字符串走
             ``json.loads``、逗号串走 ``split(",")``（本机实测两者输出相同）。
             取 **JSON 数组**：它是核心 ``_coerce_multi_select_text`` 的规范形式，
             而且**选项文本里含逗号时不会被打散**。
             （审计原报告说「JSON 一律被拒」，那是**文字回答**那条路径
             （``clarify_gateway._coerce_text_response_detailed``）的行为；我们多选点击
             走的是 ``resolve_gateway_clarify``，两者不是一回事 —— 别把一条路径的
             结论当成另一条的。）

        ``mode == "none"`` 表示载荷里什么都没有 —— 调用方保持安静、不要提交空答案。
        """
        options = getattr(action, "options", None)
        if isinstance(options, str) and options.strip():
            # 线格式可能是 "A,B"（视客户端/版本），与 list 等价处理
            options = [part.strip() for part in options.split(",") if part.strip()]
        if isinstance(options, (list, tuple)) and options:
            return json.dumps([str(item) for item in options], ensure_ascii=False), "multi"
        option = getattr(action, "option", None)
        if isinstance(option, str) and option.strip():
            return option, "choice"
        typed = getattr(action, "input_value", None)
        if isinstance(typed, str) and typed.strip():
            return typed.strip(), "text"
        fallback = value.get("answer") if isinstance(value, dict) else None
        if fallback is None:
            return None, "none"
        return fallback, "choice"

    def _ld_handle_clarify_click(self, *, event: Any, action: Any,
                                 value: Dict[str, Any]) -> Any:
        """把一次澄清点击变成 ``resolve_gateway_clarify`` 调用，并原地更新卡片。

        ``action`` 是载荷里的 ``event.action``：1.0 的答案在 ``value["answer"]``，
        2.0 的在 ``action.option``（下拉）或 ``action.input_value``（输入框）——
        取值规则见 :meth:`_ld_clarify_answer`。
        """
        clarify_id = str(value.get("clarify_id") or "")
        if not clarify_id:
            logger.warning("[larkdeck] 澄清点击缺少 clarify_id，回一条 toast（P1b：不再静默）")
            return self._ld_toast_or_noop(kind="error", text_key="clarify.toast_missing_id")
        answer, mode = self._ld_clarify_answer(action, value)
        if mode == "none":
            # ⚠️ **以前这里是完全静默的**（R8 审计中-2，本项目头号失败模式）。
            #
            # 这条分支在**默认方言（2.0）**上就有三条入口 —— 2.0 的 `input` 组件
            # `behaviors.value` 里**刻意没有** `answer` 键（模式由载荷推导，
            # 见 `cards.clarify_card_2`），所以：
            #   ① 输入框空着回车（`input_value=''`）；② 只输了空白（`'   '`）；
            #   ③ 多选把勾选**全部取消**再提交（`options=[]`；
            #      而 `options=["A"]` 走 multi、`options=[]` 落这里 —— 代码可判，无需真机）。
            # 三种情况下用户屏幕上一个像素都不动，而本项目为另外三条路径都加了提示。
            logger.warning("[larkdeck] 澄清点击里既没有 answer 也没有 option/input_value"
                           "（空提交）—— 回一条提示，不再静默")
            # ⚠️ **留白（R8 审计明确留下的）**：「空输入框按回车、客户端到底发不发
            # `card.action.trigger`」这半边**只有真机能答** —— 飞书可能根本不发回调
            # （那这条就是死代码，属低而非中），也可能发一个 `input_value=''` 的回调。
            # **我没有验证过**，这里也**不假装**验证过；写下来的理由是「收到一个解析不出来的
            # 空点击 ⇒ 回一条提示」在任何一种可能下都不会比静默更糟。
            return self._ld_toast_or_noop(kind="error", text_key="clarify.toast_empty")

        operator = getattr(event, "operator", None)
        open_id = str(getattr(operator, "open_id", "") or "")
        if not self._is_interactive_operator_authorized(open_id):
            logger.warning("[larkdeck] 未授权的澄清点击 by %s，回一条 toast（P1b）",
                           open_id or "<unknown>")
            return self._ld_toast_or_noop(kind="error", text_key="clarify.toast_unauthorized")

        loop = self._loop
        if not self._loop_accepts_callbacks(loop):
            logger.warning("[larkdeck] 适配器 loop 未就绪，回一条 toast（P1b：不再静默）")
            return self._ld_toast_or_noop(kind="error", text_key="clarify.toast_unavailable")

        is_other = answer == _cards.OTHER_VALUE
        question = str(value.get("question") or "")
        session_key = str(value.get("session_key") or "")

        if mode == "text":
            # 输入框的自由文本。**两步，顺序不能反**：
            #
            # ① 先把这条澄清切成「等待文字输入」—— 用户点输入框本身就是 1.0 里「其他
            #    （我直接输入）」那个按钮的等价物。不做这一步，核心的判据会对「选项题 +
            #    散文」返回 `rejected_prose`：用户**打了字、回车、什么都没发生、也没有提示**
            #    （2026-09-13 审计实测：输入「我想先观察一下再说」→ 不提交、只有 WARNING）。
            # ② 再按**这张卡自己的** clarify_id 解析（编号 / 标签 / 多选都走核心规则）。
            #    核心给「用户直接打字回复」用的入口取的是该 session 最旧的待答澄清，
            #    同 session 两条待答时会答错问题，所以不能用它。
            try:
                _compat.clarify_mark_awaiting_text(clarify_id)
            except Exception:  # pragma: no cover - 防御性
                logger.debug("[larkdeck] 切换文字等待态失败", exc_info=True)
            outcome = _compat.clarify_text_answer(clarify_id, str(answer))
            if outcome != _compat.CLARIFY_TEXT_RESOLVED:
                logger.warning("[larkdeck] 澄清输入框的内容没有被接受（%s · session=%s）"
                               "—— 卡片保持原样（%s）", outcome, session_key[:8],
                               "该澄清已消失" if outcome == _compat.CLARIFY_TEXT_NO_PENDING
                               else "用户可换个说法重试")
                # 卡片**保持原样**（用户还要在上面改答案重试），只弹一条提示让他知道
                # 「东西收到了但没生效」—— 静默不动会让人以为点了没反应。
                #
                # ⚠️ **按判定分流，不能塌缩成一句**（审计低-2）：四种判定里只有
                # `REJECTED_*` 是「换个说法/换个选项就能救回来」的；`NO_PENDING` 表示这条
                # 澄清**已经没了**（`compat.py` 的 entry is None / event 已 set），
                # 对它说「请重试」是**错话** —— 重试永远不会成功。判据本身在 compat 里
                # 有唯一来源（`CLARIFY_TEXT_NO_PENDING`），这里只做二选一，不猜。
                return self._ld_toast_or_noop(
                    kind="error",
                    text_key="clarify.toast_no_pending"
                    if outcome == _compat.CLARIFY_TEXT_NO_PENDING
                    else "clarify.toast_rejected")
            user_name = self._get_cached_sender_name(open_id) or open_id or "?"
            return self._ld_card_response_safe(
                self._ld_build_resolved_card(question=question, answer=answer,
                                             user_name=user_name))

        # 提交是**同步**的：网关那两处只做「锁内 dict 取写 + threading.Event.set()」，
        # 不碰事件循环（已对 Hermes 源码核实），所以能当场拿到真实结果。
        # 而这正是关键：**「没抛异常」不等于提交成功** —— 重复点击，或点击一个已被
        # 超时/文字回答消费掉的澄清时，网关返回 False。此时若仍把卡片换成「已答复」，
        # 就会出现「卡片说已收到、agent 却仍阻塞在网关」：答案永久丢失，且卡片已变
        # 已解状态，用户连重试的机会都没有。所以只有真的提交成功才回填卡片。
        try:
            if is_other:
                # 「其他」不提交答案，只把该 clarify 切成等待文字输入。
                committed = _compat.clarify_mark_awaiting_text(clarify_id)
            else:
                committed = _compat.clarify_resolve_gateway_clarify(clarify_id, str(answer))
        except Exception as exc:
            logger.error("[larkdeck] 澄清提交异常: %s", exc, exc_info=True)
            committed = False

        if not committed:
            logger.warning("[larkdeck] 澄清提交未生效（clarify=%s）—— 该澄清已被处理或已过期；"
                           "卡片保持原样，无需重复点击", clarify_id)
            # ⚠️ **只能弹 toast、绝不能换卡**：这一条最常见的触发者是「一次迟到的重复点击」
            # （用户点了两下 / 网络重放）。那一瞬间卡片可能已经被前一次点击换成了「已确认」，
            # 若这里回一张「待答 + 失败提示」，用户就会看到自己确认过的卡被退回待答 ——
            # 而答案其实早已送达 agent。toast 既能告知失败，又不可能覆盖卡片状态。
            #
            # ⚠️⚠️ **这一支必须排在下面的 `if is_other:` 之前**（R8 审计中-3：把条件改成
            # `if not committed and not is_other:` 四门禁全绿）。反过来之后，组合是
            # **「提示说错话」**：点「其他」而这澄清已失效 ⇒ 用户被告知「去输入框打字」，
            # 于是他打字 —— 而网关注册表里已经没有这条澄清，`clarify_text_answer` 会返回
            # `NO_PENDING`，他的文字会变成一条**普通聊天消息**（更坏的是：他以为自己答上了）。
            # 判据是「用户被告知的是**失败**还是**去打字**」，由
            # `test_clarify_failed_priority_beats_other_hint` 钉住（载荷：is_other 且 commit=False）。
            return self._ld_toast_or_noop(kind="error", text_key="clarify.toast_failed")

        if is_other:
            # 「其他」不提交答案，只把该澄清切成等待文字输入 —— 卡片保持不变（用户要在
            # 卡片上继续输入），用 toast 告诉他下一步做什么。文案按方言选（1.0 卡没有输入框）。
            return self._ld_toast_or_noop(kind="info", text_key=self._ld_typing_text_key())

        user_name = self._get_cached_sender_name(open_id) or open_id or "?"
        # 回填卡必须与待答卡同方言，否则飞书会**静默丢弃**这一帧（HFC 踩过）
        return self._ld_card_response_safe(
            self._ld_build_resolved_card(question=question, answer=answer, user_name=user_name)
        )


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #
#: ⚠️ R11-A0 的**明确决定：这两个缓存留在「每个世代各一份」，不进共享盒子**。
#: 它们缓存的是**代码**（内置工厂 → 官方类、官方类 → 我们叠出来的类），不是结论：
#: 共享的话，第二世代会直接复用**第一世代那份合并类**，于是「活适配器」永远跑第一世代的
#: 代码，而钩子跑第二世代 —— 插件升级后只做一次重载（不重启进程）时，两边就是**不同版本的
#: 代码在同一个回合里协作**。留在各世代 + 下面 `_official_base_class()` 剥掉旧层，
#: 得到的正是「第二世代的混入 + 官方基类」：单一混入层、代码同一个世代。
_BASE_CLASSES: Dict[Any, type] = {}
_MERGED_CLASSES: Dict[type, type] = {}


def _ld_response_code(response: Any) -> int:
    """从飞书 SDK 响应里取出错误码（取不到返回 0，即「不是已知错误码」）。"""
    if isinstance(response, dict):
        raw = response.get("code")
    else:
        raw = getattr(response, "code", None)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _log_probe_report(report: Dict[str, Any]) -> None:
    """把 ``compat.probe_report()`` 的能力快照打进日志（缺失项提级到 WARNING）。

    这是「只探测上报」里那个**上报** —— 没有它，CALLBACK 两组的探测只在单测里跑过，
    生产代码从不调用，等于什么都没上报。

    ⚠️ 「报告里没有这个键」与「这个键是空列表」必须**区分开**：前者是探测本身失效
    （上游契约改名 / 有人误删），后者才是「契约齐全」。第七路审计实测：旧写法
    `report.get(k) or []` 把两者混成一件事 —— 于是删掉 `probe_report` 里的
    `missing_reactions`，启动自检**一声不响**，而它修的恰恰就是「静默失灵」。
    """
    # 缺键清单从 `compat.PROBE_REPORT_KEYS` **派生**（第八路审计：这里是第三份手写字面量，
    # 删掉一项没有任何门禁看得见）。探测自身失效的路径（`cls is None`，报告里带 `error`）
    # 不算缺键 —— 那条路已经有一条更准确的告警。
    if "error" in report:
        absent = []
    else:
        absent = [key for key in _compat.PROBE_REPORT_KEYS if key not in report]
    violation = list(report.get("contract_violation") or [])
    if violation:
        logger.warning("[larkdeck] 能力探测报告的契约没对齐：缺 %s —— "
                       "`probe_report()` 与 `PROBE_REPORT_KEYS` 不同步", violation)
    if absent:
        logger.warning("[larkdeck] 能力探测报告缺少 %s —— 探测本身失效，"
                       "上游契约改名会在启动自检里静默漏报", absent)
    missing_callback = list(report.get("missing_callback") or [])
    missing_optional = list(report.get("missing_optional") or [])
    missing_signal = list(report.get("missing_signal") or [])
    missing_reactions = list(report.get("missing_reactions") or [])
    detail = (f"{report.get('adapter_class')} · "
              f"缺可选 {missing_optional or '无'} · 缺点击回调 {missing_callback or '无'}")
    if missing_callback:
        logger.warning("[larkdeck] 能力探测：%s —— 澄清按钮会静默失灵（点下去没反应）", detail)
    else:
        logger.info("[larkdeck] 能力探测：%s", detail)
    # 这两组的缺失都只会「静默失灵」，所以必须在启动时说出来（别等用户来报「怎么没变色」）
    if missing_signal:
        logger.warning("[larkdeck] 能力探测：内置适配器缺少 %s —— "
                       "`/stop` 之后卡片不会变成中止色（而且不会有别的提示）", missing_signal)
    # P1b：核心查找名的静态 best-effort 探测。True=在位，False=改名/缺失，None=未取证。
    # 不改任何行为，只在启动日志里把「/stop 后卡片可能不变色」的另一种失灵说清楚。
    core_lookup = report.get("core_interrupt_lookup")
    if core_lookup is False:
        logger.warning("[larkdeck] 能力探测：核心源码里找不到 "
                       "getattr(type(adapter), %r) 的查找点 —— `/stop` 后卡片可能不会变色",
                       _compat.CORE_INTERRUPT_LOOKUP)
    if missing_reactions:
        logger.warning("[larkdeck] 能力探测：内置适配器缺少 %s —— "
                       "`reactions: false` 会静默失效（「处理中」表情照旧）", missing_reactions)
    missing_display = list(report.get("missing_display_chrome") or [])
    if missing_display:
        logger.warning("[larkdeck] 能力探测：内置适配器缺少 %s —— "
                       "工具行会重新并进正文（「干净卡片」静默失效）", missing_display)


#: 我们自己的层用的名字（`LarkDeckMixin` 与 `merged_class()` 造出来的类名）。
_OUR_LAYER_NAMES = frozenset({ADAPTER_CLASS_NAME, LarkDeckMixin.__name__})


def _is_our_layer(klass: type) -> bool:
    """这个类（或它的 MRO 里）是不是**本插件自己叠的一层**。

    ⚠️ **不能只按对象同一性判**（R11-A6 的关键细节）：同一进程里插件的模块被重新加载过时，
    上一世代的 `LarkDeckMixin` 与这一世代的 `LarkDeckMixin` 是**两个不同的类对象**
    （模块被重新 `exec` 了）—— 只按 `is` 判就会**认不出旧层**，于是自套娃照旧发生。
    所以对象同一性 **或** 名字命中（`LarkDeckMixin` / 合并类名）都算。名字来自我们自己的代码，
    不涉及 Hermes 内部结构（不变量 4）。
    """
    names = _OUR_LAYER_NAMES
    try:
        for item in getattr(klass, "__mro__", ()):
            if item is LarkDeckMixin or getattr(item, "__name__", "") in names:
                return True
    except Exception:  # pragma: no cover - 防御性
        return False
    return False


def _official_base_class(cls: type) -> type:
    """把 `cls` 的 MRO 里**我们自己的层**剥掉，返回真正该被继承的那个类（R11-A6）。

    为什么需要它（**自套娃**）：第二世代的 `_discover_base_class()` 问注册表要「上一个工厂
    造出来的类」，而那时注册表里挂的是**第一世代的 `build_adapter`** ⇒ 拿回来的基类
    是第一世代的合并类。照旧写法再叠一层，MRO 会变成
    ``[第二世代合并, 第二世代混入, 第一世代合并, 第一世代混入, 官方…]``。
    后果不是「多一层」这么轻：**混入层里那些 `super().xxx()` 的回退路径会被执行两遍**
    （第二世代混入 → 第一世代混入 → 官方），而我们的回退路径全都是「真的去写一次卡 /
    真的去交还控制权」，也就是**同一帧写两次、状态改两遍**。

    `cls` 本身就是官方类时**原样返回**（等价于改之前的行为）。
    """
    try:
        for klass in cls.__mro__:
            if _is_our_layer(klass):
                continue
            return klass
    except Exception:  # pragma: no cover - 防御性：拿不到 MRO 就用原类（退回旧行为）
        logger.debug("[larkdeck] 剥不掉自己那一层，按原类处理", exc_info=True)
    return cls


def merged_class(base_cls: type) -> type:
    """给 ``base_cls`` 叠**一层** ``LarkDeckMixin``；按剥掉旧层之后的官方类缓存。

    ⚠️ 缓存键是**剥掉旧层之后**的那个类（`_official_base_class`）：两世代都问同一个官方类，
    各自得到「自己世代的混入 + 官方类」，既不会叠成两层，也不会跨世代共用代码。
    """
    official = _official_base_class(base_cls)
    if official is not base_cls:
        # A6：把「自套娃」这件事说出来。它**不是**致命错误（下面已经剥掉了），但它是
        # 「插件在同一进程里被加载两遍」最直接的信号 —— 没有这一行，那个事实只能靠
        # `Plugin discovery complete` 出现两次来推断（日志里很容易被淹没）。
        logger.warning("[larkdeck] 适配器基类已经是本插件叠过的（%s）—— 说明同一进程里"
                       "插件的模块被重新加载过（世代更替）；本次改为继承官方类 %s，"
                       "避免把混入层套两层（回退路径会被执行两遍）",
                       getattr(base_cls, "__name__", base_cls),
                       getattr(official, "__name__", official))
    merged = _MERGED_CLASSES.get(official)
    if merged is None:
        merged = type(ADAPTER_CLASS_NAME, (LarkDeckMixin, official), {"__module__": __name__})
        _MERGED_CLASSES[official] = merged
    return merged


def _discover_base_class(base_factory: Any, config: Any) -> type:
    """问出内置工厂造出来的**真实类**。按工厂缓存，所以每个进程只多造一个实例。"""
    cached = _BASE_CLASSES.get(base_factory)
    if cached is None:
        cached = type(base_factory(config))
        _BASE_CLASSES[base_factory] = cached
        logger.debug("[larkdeck] 内置适配器类 = %s.%s",
                     cached.__module__, cached.__qualname__)
    return cached


def build_adapter(base_factory: Any, config: Any) -> Any:
    """造一个「内置适配器 + 卡片层」实例。任何一步失败都退回内置适配器。

    为什么是**重新构造**而不是改 ``instance.__class__``
    --------------------------------------------------
    CPython 允许 ``obj.__class__ = Other`` 的前提之一是「新类的直接基类链与旧类一致」，
    而不只是内存布局一致。``type("X", (LarkDeckMixin, FeishuAdapter), {})`` 的直接基类是
    ``LarkDeckMixin``（不是 ``FeishuAdapter``），所以即使实例布局一模一样，赋值依然抛
    ``TypeError: __class__ assignment: ... object layout differs``。
    实测：``type("P", (Stub,), {})`` 可以赋值，``type("M", (Mixin, Stub), {})`` 不行。

    直接把子类构造出来没有这个约束，MRO 也干净（LarkDeckMixin 在 FeishuAdapter 之前，
    零参 ``super()`` 正常工作）。代价是首轮多造一个实例——它的 ``__init__`` 只读配置和
    去重文件，没有副作用，所以这个代价是可接受的。
    """
    # P1a：每次 build 先清掉旧快照 —— 若基类发现就抛，状态卡必须落到「未探测」，
    # 而不是展示上一世代/上一次的过时结论。探测结论只由本次 build 写入。
    PROBE_REPORT.clear()
    try:
        base_cls = _discover_base_class(base_factory, config)
    except Exception as exc:
        _remember_selfcheck(False, f"无法解析内置适配器类: {exc}")
        return base_factory(config)

    # 完整能力快照 —— 点击回调路径与可选接口**只有这里能看见**。
    # 缺了不阻断卡片，但必须在日志里留痕：这些名字官方一改，澄清按钮就静默失灵
    # （点下去没有任何反应，也不报错），没有别的地方会给出信号。
    # `adopted` 表示「覆盖层真的构造成功了」，不是「探测跑了」——用于状态卡区分
    # 「已接管」「必需接口缺失」「覆盖层构造失败」三种状态，避免把没做成的说成做成了。
    report = _compat.probe_report(base_cls)
    report["adopted"] = False
    PROBE_REPORT.clear()
    PROBE_REPORT.update(report)
    _log_probe_report(report)
    if not report.get("ok"):
        missing = ", ".join(str(x) for x in (report.get("missing_required") or []))
        _remember_selfcheck(False, "内置适配器缺少所需接口: " + (missing or "未知"))
        return base_factory(config)

    try:
        adapter = merged_class(base_cls)(config)
        adapter._ld_setup()
    except Exception as exc:  # 卡片层构造失败绝不能让飞书起不来
        _remember_selfcheck(False, f"卡片层构造失败: {exc}")
        logger.error("[larkdeck] 卡片层构造失败，退回内置适配器: %s", exc, exc_info=True)
        return base_factory(config)

    # 只有走到这里才算「覆盖层接管成功」；状态卡据此显示「已接管」，而不是「探测跑了」。
    report["adopted"] = True
    PROBE_REPORT.clear()
    PROBE_REPORT.update(report)

    logger.debug("[larkdeck] 已接管适配器: %s", [c.__name__ for c in type(adapter).__mro__[:3]])
    return adapter


def _ld_plugin_version() -> str:
    """从 ``plugin.yaml`` **现读**版本号；读不到返回 ``""``（**不编一个**）。

    为什么不用常量 / 不 import ``hermes_cli``：常量会与清单各自漂移；而这只是
    用户敲 ``/larkdeck`` 时读一次小文件，代价可以忽略。读不到就少显示一段 ——
    「少一段」永远比「报一个错的版本」好（报错的版本号会把排障带偏）。
    ⚠️ 但「少一段」**不能是静默的**（R9 审计低-2）：空串由 :func:`_ld_command_card`
    翻译成 i18n 的「版本读不到」，「少显示」与「读不到」在卡片上必须看得出区别。
    """
    try:
        with open(_PLUGIN_MANIFEST, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        logger.debug("[larkdeck] 读不到插件清单 %s（卡片会写「版本读不到」）", _PLUGIN_MANIFEST)
        return ""
    match = _PLUGIN_VERSION_RE.search(text)
    return match.group(1) if match else ""


def _probe_absent_keys(report: Dict[str, Any]) -> List[str]:
    """探测契约里缺失的键（**从唯一事实来源求差集**，不只信 `contract_violation`）。

    显示层必须自证：producer 回归时可能连 `contract_violation` 自己都不写了。
    """
    absent = [key for key in _compat.PROBE_REPORT_KEYS if key not in report]
    extras = report.get("contract_violation") or []
    if isinstance(extras, str):
        extras = [extras]
    for extra in extras:
        if str(extra) and str(extra) not in absent:
            absent.append(str(extra))
    return absent


def _probe_state_key(report: Dict[str, Any]) -> str:
    """探测结论 → i18n 键。判别顺序：缺键 > 必需接口 > 覆盖层构造 > 已接管。"""
    if not report or "error" in report:
        return "probe.none"
    if _probe_absent_keys(report):
        return "probe.incomplete"
    if not report.get("ok"):
        return "probe.blocked_required"
    if not report.get("adopted"):
        return "probe.blocked_build"
    return "probe.covered"


def _probe_state_label(report: Dict[str, Any]) -> str:
    """探测结论的**短标签**（供聚合诊断在一行里嵌入，不携带整句前缀）。"""
    key = _probe_state_key(report)
    return _i18n.t("probe.short_none" if key == "probe.none" else key)


def _probe_status_lines() -> List[str]:
    """`/larkdeck status` 的能力探测摘要（P1a）。

    数据只来自 `build_adapter()` 时存下的 `PROBE_REPORT` 快照 —— 命令路径**不重新探测**：
    那时接管用的基类可能已经不在了，而且探测不应在命令线程上做 IO。
    未探测/探测本身失败 ⇒ 写「未探测」；已探测 ⇒ 按「状态 / 缺失 / 契约」三行给可读摘要。
    覆盖 `compat.PROBE_REPORT_KEYS` 全部键；不把原始键名列表直接堆给用户，也绝不写「正常」。
    """
    report = dict(PROBE_REPORT)
    state_key = _probe_state_key(report)
    if state_key == "probe.none":
        return [_i18n.t("probe.none")]

    def _names(key: str) -> str:
        value = report.get(key)
        if not isinstance(value, (list, tuple)):
            return _i18n.t("probe.unknown")
        names = ", ".join(str(x) for x in value if str(x))
        return names or _i18n.t("probe.none_list")

    absent = _probe_absent_keys(report)
    state = _i18n.t(state_key)
    version = str(report.get("hermes_version") or _i18n.t("probe.unknown"))
    adapter_class = str(report.get("adapter_class") or _i18n.t("probe.unknown"))
    session = (_i18n.t("probe.session_ok") if report.get("session_attribution_ok")
              else _i18n.t("probe.session_bad"))
    lookup = report.get("core_interrupt_lookup")
    if lookup is True:
        lookup_state = _i18n.t("probe.signal_ok")
    elif lookup is False:
        lookup_state = _i18n.t("probe.signal_bad")
    else:
        lookup_state = _i18n.t("probe.signal_unknown")
    lines = [
        _i18n.t("probe.line", state=state, version=version,
                adapter=adapter_class, session=session),
        _i18n.t("probe.missing",
                required=_names("missing_required"), optional=_names("missing_optional"),
                callback=_names("missing_callback"), signal=_names("missing_signal"),
                reactions=_names("missing_reactions"), chrome=_names("missing_display_chrome")),
        _i18n.t("probe.signal", state=lookup_state),
    ]
    contract = (_i18n.t("probe.contract_bad", keys=", ".join(absent))
                if absent else _i18n.t("probe.contract_ok"))
    lines.append(_i18n.t("probe.contract", contract=contract))
    return lines


def _ld_diag_inbound(snap: Dict[str, Any]) -> str:
    """入站心跳的**相对年龄**；没有可用记录就如实写「无记录」。"""
    age = _context.age_text(snap.get("inbound_at"))
    if age == _i18n.t("status.none"):
        return _i18n.t("diag.inbound_none")
    return _i18n.t("diag.inbound_ago", age=age)


def _ld_diagnosis_lines() -> List[str]:
    """P2 聚合诊断：把跨模块的健康事实压成两行，给 `/larkdeck status` 顶部用。

    与 AP 的 `doctor` 同一目的，但只报**本进程手上有证据的事实**，不做结论性健康声明：
      * 能力/链路行：探测结论、钩子挂载数、命令注册、模块世代；
      * 运行/账本行：入站心跳年龄、写卡帧数、写卡失败、掉回纯文本、错误码总数。

    两行在**有明确异常**（未接管 / 钩子不全 / 命令未注册 / 世代裂脑 / 有失败计数）时
    以 ``⚠️`` 开头；其余情况只列事实，**不写「正常」「健康」** —— 一个永远说健康的诊断
    与一个坏掉的诊断在卡上没有区别（`docs/lessons.md` 推论 6）。
    """
    try:
        report = dict(PROBE_REPORT)
        state_key = _probe_state_key(report)
        wired = sum(1 for hook_name, ok in HOOKS.items()
                    if ok and hook_name not in _HOOK_EXTRA_NAMES)
        total = len(_hooks.SUBSCRIPTIONS)
        command_ok = bool(COMMAND.get("registered"))
        generation = int(_panel.load_seq())
        latest = int(_panel.latest_load_seq())
        snap = _context.status_snapshot() or {}
        writes = int(snap.get("frame_ok_count") or 0)
        failures = int(snap.get("frame_fail_count") or 0)
        fallbacks = int(snap.get("fallback_count") or 0)
        codes = int(snap.get("code_total") or 0)
        capability_bad = (state_key != "probe.covered" or wired < total
                          or not command_ok or generation != latest)
        runtime_bad = failures > 0 or fallbacks > 0 or codes > 0
        return [
            ("⚠️ " if capability_bad else "") + _i18n.t(
                "diag.capability",
                probe=_probe_state_label(report),
                wired=wired,
                total=total,
                command=_i18n.t("diag.command_ok" if command_ok else "diag.command_bad"),
                gen=generation,
                latest=latest,
            ),
            ("⚠️ " if runtime_bad else "") + _i18n.t(
                "diag.runtime",
                inbound=_ld_diag_inbound(snap),
                writes=writes,
                fail=failures,
                fallback=fallbacks,
                codes=codes,
            ),
        ]
    except Exception as exc:
        # ⚠️ 不许静默少两行（R9 低-2 同源）：聚合失败必须让用户在卡上看到，
        # 否则「聚合行不见了」与「一切正常」在用户眼里一样。
        # 日志只记异常**类型名**：病态异常对象的 `__str__` 可能在 logger 格式化时再抛，
        # 那会把「诊断失败」升级成「命令处理器穿透」（审计 A6）。
        logger.warning("[larkdeck] 聚合诊断渲染失败: %s", type(exc).__name__, exc_info=True)
        try:
            reason = str(exc)
        except Exception:
            reason = _i18n.t("cmd.failed_no_reason")
        return [_i18n.t("diag.failed", error=reason)]


# --------------------------------------------------------------------------- #
# P2 `/larkdeck config`：只读视图 + 官方 ctx.get_config() 热刷新。
# ⚠️ 审计 security B1 后**没有聊天侧写入命令**：写配置走官方 Hermes CLI / 配置文件，
# 再用 `config reload` 刷新。插件从不直接写宿主 config.yaml，也不保存 ctx.set_config。
# --------------------------------------------------------------------------- #
def _official_cfg_getter() -> Any:
    getter = PLUGIN_CTX.get("get_config")
    return getter if callable(getter) else None


def _cfg_canonical(value: Any) -> str:
    """配置值比较用（诊断「官方文件与本进程内存是否分叉」）。"""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return repr(value)


def _cfg_text(value: Any) -> str:
    """配置值上卡前的短文本（单行、有界；预览不是倾倒）。"""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = repr(value)
    text = str(text).replace("\n", " ").replace("\r", " ").replace("\t", " ")
    return text if len(text) <= 80 else text[:77] + "..."


def _ld_config_show() -> str:
    """只读视图：每个键的**本进程生效值** + 来源（env / 官方 settings / 默认）。

    另外对「官方 settings 已改、本进程内存还是旧值」给一行显式提示（必须 `/larkdeck
    config reload` 才会换血）—— 这正是「配置刷新」要解决的那个静默窗口。
    """
    getter = _official_cfg_getter()
    official: Dict[str, Any] = {}
    read_errors: List[str] = []
    if getter:
        for key in _DEFAULTS:
            try:
                value = getter(key, None)
            except Exception:
                read_errors.append(key)
            else:
                if value is not None:
                    official[key] = value
    lines = [_i18n.t("config.header")]
    if not getter:
        lines.append(_i18n.t("config.no_reader"))
    for key in sorted(_DEFAULTS):
        default = _DEFAULTS[key]
        env = os.environ.get("LARKDECK_" + key.upper())
        env_active = env is not None and env.strip() != ""
        if env_active:
            source = _i18n.t("config.source_env")
        elif key in official:
            source = _i18n.t("config.source_official")
        else:
            source = _i18n.t("config.source_default")
        value = _cfg_raw(key, default)
        note = ""
        # ⚠️ 判据必须与 source 的非空判断一致（审计 A5）：`LARKDECK_THEME=""` 在 `_cfg_raw`
        # 里等效「未设」，如果用 `env is None` 判 note，官方已变时提示会静默消失。
        if (key in official and not env_active
                and _cfg_canonical(value) != _cfg_canonical(official[key])):
            note = " " + _i18n.t("config.needs_reload")
        lines.append(_i18n.t("config.item", name=key, value=_cfg_text(value),
                             source=source, note=note))
    if read_errors:
        lines.append(_i18n.t("config.read_errors", keys=", ".join(sorted(read_errors))))
    return "\n".join(lines)


def _ld_config_reload() -> str:
    """从官方 `ctx.get_config()` **重新读取全部键**并替换内存配置（只读路径）。

    **异常时全有全无**：任何一个键读取抛异常 ⇒ 整次刷新取消、内存保持原样，不会留下
    「读了一半」的配置。⚠️ 这**不是文件系统事务**：官方 `get_config` 每个键都是一次独立的
    `load_config_readonly()`，外部进程在读取过程中原子替换配置/并发写时，可能读到跨代的
    混合快照（本函数只能保证异常时不改内存，不能锁住官方读取）。需要强一致时请先停止
    外部写入再做 reload。
    """
    getter = _official_cfg_getter()
    if not getter:
        return _i18n.t("config.reload_no_reader")
    found: Dict[str, Any] = {}
    errors: List[str] = []
    for key in _DEFAULTS:
        try:
            value = getter(key, None)
        except Exception:
            errors.append(key)
        else:
            if value is not None:
                found[key] = value
    if errors:
        return _i18n.t("config.reload_failed", keys=", ".join(sorted(errors)))
    previous = {key: _CONFIG.get(key, _DEFAULTS[key]) for key in _DEFAULTS}
    fresh = dict(_DEFAULTS)
    fresh.update(found)
    try:
        _CONFIG.clear()
        _CONFIG.update(fresh)
        _apply_metrics_config()
    except Exception as exc:
        _CONFIG.clear()
        _CONFIG.update(previous)
        try:
            _apply_metrics_config()
        except Exception:                       # pragma: no cover - 防御性回滚
            logger.debug("[larkdeck] 配置刷新回滚后仍无法应用指标配置", exc_info=True)
        try:
            reason = str(exc)
        except Exception:
            reason = _i18n.t("cmd.failed_no_reason")
        return _i18n.t("config.reload_apply_failed", error=reason)
    changed = [key for key in sorted(_DEFAULTS)
               if _cfg_canonical(previous[key]) != _cfg_canonical(fresh[key])]
    result = _i18n.t("config.reload_ok", n=len(changed),
                     keys=", ".join(changed) or _i18n.t("config.none"))
    # ⚠️ 环境变量优先：reload 成功 ≠ 这些键的本进程生效值变了（审计 C3/L2）。
    shadowed = [key for key in sorted(_DEFAULTS)
                if (os.environ.get("LARKDECK_" + key.upper()) or "").strip()]
    if shadowed:
        result += "\n" + _i18n.t("config.reload_env_shadowed", keys=", ".join(shadowed))
    return result


def _ld_config_card(raw_args: str) -> str:
    """`/larkdeck config [show|reload]` 的分发（**只读**；写入入口已按安全审计移除）。"""
    text = str(raw_args or "").strip()
    if not text:
        return _ld_config_show()
    head = text.split(maxsplit=1)
    action = head[0].strip().lower()
    if action in ("show", "list"):
        return _ld_config_show()
    if action in ("reload", "refresh"):
        return _ld_config_reload()
    if action in ("set", "write"):
        # 明确说「不提供聊天侧写入」，而不是含糊地报「不认识」：用户要的是知道怎么做。
        return _i18n.t("config.read_only_set")
    return "\n".join([_i18n.t("config.unknown_action", arg=action), _i18n.t("cmd.help")])


def _ld_command_card(raw_args: str) -> str:
    """``/larkdeck [status|config|help]`` 的处理器（**模块级函数**：命令 API 要的是可调用对象）。

    返回值是 markdown 文本，由核心发回 —— 那条路径经过我们的 ``send()``，
    所以它自动成一张卡；这里只管「写什么」，不碰卡片 JSON。

    纪律：
      * **绝不抛** —— 抛出去会把「查一次状态」变成用户侧的错误提示；读不到就如实写读不到；
      * **只报有证据的事** —— 没记录就写「无记录」（见 :func:`larkdeck.core.context.status_lines`）；
      * **写明飞书网关里生成期间会被排队**（CLI / TUI 里可直接执行）—— 命令派发挂核心 idle 路径；
      * **写明数字是进程级累计** —— 多会话并发时它含**别的会话**的那部分（见下面那句口径说明）；
      * **config 默认只读** —— `/larkdeck config` 与 `config reload` 都只读；聊天侧没有写入
        命令（安全审计 B1：handler 拿不到发送者身份，进程级开关无法授权）。写配置走官方
        Hermes CLI / 配置文件，再用 `config reload` 热刷新。
    """
    try:
        raw = str(raw_args or "").strip()
        arg = raw.lower()
        if arg in ("help", "-h", "--help"):
            return _i18n.t("cmd.help")
        head = raw.split(maxsplit=1)
        token = head[0].lower() if head else ""
        if token == "config":
            return _ld_config_card(head[1] if len(head) > 1 else "")
        if arg not in ("", "status"):
            return "\n".join([_i18n.t("cmd.unknown", arg=arg), _i18n.t("cmd.help")])
        version = _ld_plugin_version()
        # ⚠️ 读不到清单**不许静默**（R9 审计低-2）：以前 `version` 为空串时版本段整段消失，
        # 卡片看起来和「一切正常」一模一样 —— 而「读不到就说读不到」正是本卡片的纪律
        #（与「没记录就写无记录」同源）。所以空串在这里翻译成 i18n 的「版本读不到」。
        name = "🃏 larkdeck v" + (version or _i18n.t("cmd.version_unknown"))
        wired = sum(1 for hook_name, ok in HOOKS.items()
                    if ok and hook_name not in _HOOK_EXTRA_NAMES)
        header = _i18n.t("cmd.header", name=name,
                         transport=LarkDeckMixin._ld_transport(),
                         wired=wired, total=len(_hooks.SUBSCRIPTIONS))
        # ⚠️ **口径说明必须进卡**（R9 审计中-4）：这三条记录和页脚指标一样是**进程级全局**
        # （`context._STATUS` 是模块级 dict），多会话并发时「累计 42 条消息 / 118 次写卡」
        # 里可能大部分来自**别的会话**，而「最近写卡失败：1 次 · <原因>」也可能是别人的失败。
        # 不说清楚，用户会拿别人的失败去查自己的卡 —— 正是本轮要消灭的「静默误诊」。
        # 放在**数据行之上**（不是之后）：用户是从上往下读的，先看到口径再看到数字才不会误解；
        # 而且它在三行全是「无记录」时也在（那种时刻同样需要知道这些数是全进程的）。
        # P2：聚合诊断紧跟口径说明，把「能力 / 链路 / 运行 / 账本」压成两行总览；随后才是
        # P1a 的能力探测详情与 R9 的逐条账本。顺序 = 先总后分，用户扫一眼就能判断要不要细看。
        return "\n".join([header, _i18n.t("cmd.scope")]
                          + _ld_diagnosis_lines()
                          + _probe_status_lines()
                          + _context.status_lines())
    except Exception as exc:  # pragma: no cover - 防御性：处理器绝不能抛
        # 日志只记类型名（A6）：`%s` 直接格式化病态异常会在 logger 里再抛一次。
        logger.warning("[larkdeck] `/larkdeck` 状态读取失败: %s", type(exc).__name__,
                       exc_info=True)
        # ⚠️ 兜底里的 `str(exc)` **自己也会抛**（R9 审计低-4：`__str__` 抛异常的异常，
        # 例如 `raw = str(raw_args)` 抛出的那个；外层 except 再 `str(exc)` 一次就穿透了）。
        # 穿透的后果是「用户什么也看不到」（核心只记一条 WARNING）—— 又一处静默。
        # 所以这里再兜一层：读不出原因就如实写「读不出原因」，绝不假装成功。
        try:
            reason = str(exc)
        except Exception:  # pragma: no cover - 只有病态异常对象会走到
            reason = _i18n.t("cmd.failed_no_reason")
        return _i18n.t("cmd.failed", error=reason)


def _log_standalone_client_fallback_once() -> None:
    """standalone client 建不出来导致回落内置时的限流告警（绝不静默降级）。"""
    now = time.monotonic()
    if now - getattr(_log_standalone_client_fallback_once, "_at", 0.0) < 60.0:
        return
    _log_standalone_client_fallback_once._at = now  # type: ignore[attr-defined]
    logger.warning("[larkdeck] standalone 适配器的 SDK client 建不出来 —— cron / send_message "
                   "本次投递回落内置 sender（用户可能收到纯文本而不是卡片）")


def _make_standalone_sender(factory: Any, fallback: Any) -> Any:
    """构造 `standalone_sender_fn`：让 cron / `send_message` 的无网关进程也走卡片层。

    官方 `PlatformEntry.standalone_sender_fn` 的契约是异步发一条文本：
    ``(pconfig, chat_id, message, *, thread_id, media_files, force_document) -> dict``。
    内置实现自己 new 一个**官方** ``FeishuAdapter``（不走我们的子类）⇒ cron 投递永远只有纯文本；
    这里把它换成先经 ``factory`` 构造我们的合并适配器，再调 ``adapter.send()``（卡片 + fail-open
    到内置纯文本）。**媒体附件仍回落到内置 sender**：上传/文档车道不在 Phase 3 范围内，
    静默丢掉附件比多一条纯文本更糟。

    纪律：
      * 只走官方 `register_platform(..., standalone_sender_fn=...)` 字段；SDK client 的初始化
        依赖官方私有名，已按不变量 3 集中封进 `compat.ensure_standalone_client()`；
      * 任何一步异常 / client 建不出来都**先回落内置 sender**（不丢消息），
        内置也没有才返回 ``{"error": ...}``，绝不抛进 cron 调度器；
      * 返回结构与内置实现同形，调用方（`send_message_senders`）无需分支。
    """
    async def _send(pconfig: Any, chat_id: str, message: str, *,
                    thread_id: Optional[str] = None,
                    media_files: Optional[list] = None,
                    force_document: bool = False) -> Dict[str, Any]:
        async def _fallback(reason: str = "") -> Dict[str, Any]:
            if callable(fallback):
                try:
                    return await fallback(pconfig, chat_id, message, thread_id=thread_id,
                                          media_files=media_files, force_document=force_document)
                except Exception as exc:
                    return {"error": f"larkdeck standalone fallback failed: {type(exc).__name__}"}
            return {"error": reason or "larkdeck standalone sender unavailable"}

        media = list(media_files or [])
        if media:
            # 媒体附件不在本阶段内：整条交给内置 sender（不静默丢附件）。
            return await _fallback("larkdeck standalone media fallback unavailable")
        if not str(message or "").strip():
            return {"error": "No deliverable text or media remained after processing MEDIA tags"}
        try:
            adapter = factory(pconfig)
        except Exception as exc:
            return await _fallback(f"larkdeck standalone adapter build failed: {type(exc).__name__}")
        # ⚠️ 官方 __init__ 不建 SDK client；没有它 adapter.send() 会直接返回
        # `SendResult(success=False, error='Not connected')`（审计 P3 真机复现的 blocker）。
        if _compat.ensure_standalone_client(adapter) is None:
            _log_standalone_client_fallback_once()
            return await _fallback("larkdeck standalone SDK client unavailable")
        try:
            result = await adapter.send(
                chat_id, str(message or ""),
                metadata=({"thread_id": thread_id} if thread_id else None))
        except Exception as exc:
            return {"error": f"larkdeck standalone send failed: {type(exc).__name__}"}
        if not getattr(result, "success", False):
            return {"error": f"Feishu send failed: {getattr(result, 'error', 'unknown')}"}
        return {"success": True, "platform": PLATFORM_NAME, "chat_id": chat_id,
                "message_id": getattr(result, "message_id", "") or ""}

    return _send


def register(ctx: Any) -> None:
    """插件入口：抢占 ``feishu`` 平台名，并把卡片层叠到内置适配器上。"""
    try:
        from dataclasses import fields as _dc_fields

        from gateway.platform_registry import PlatformEntry, platform_registry
    except Exception as exc:  # pragma: no cover - 只可能在非 Hermes 环境触发
        _remember_selfcheck(False, f"无法导入 Hermes 平台注册表: {exc}")
        return

    # 0) 读官方插件配置（plugins.entries.larkdeck.settings.*）—— 环境变量仍优先。
    #    同时把官方 ctx 的配置读写方法记进共享盒子：`/larkdeck config` 与 `config reload`
    #    可能要跨模块世代执行，句柄必须与 `PROBE_REPORT` 一样是进程级共享的。
    _remember_plugin_ctx(ctx)
    _apply_ctx_settings(ctx)

    # 1) 先把内置 feishu 解析出来（这一步会触发它的 deferred loader）。
    try:
        builtin = platform_registry.get(PLATFORM_NAME)
    except Exception as exc:
        _remember_selfcheck(False, f"解析内置 '{PLATFORM_NAME}' 平台失败: {exc}")
        return
    if builtin is None or getattr(builtin, "adapter_factory", None) is None:
        _remember_selfcheck(False, f"没有找到内置 '{PLATFORM_NAME}' 适配器工厂")
        return
    base_factory = builtin.adapter_factory

    # 2) 工厂：先造内置实例，再叠卡片层。任何能力缺失都原样返回，绝不弄坏飞书。
    def _factory(config: Any) -> Any:
        return build_adapter(base_factory, config)

    # 3) 透传内置 entry 的**全部**元数据字段 —— `register_platform` 是**整条替换** entry
    #    （不合并），所以「少传一个字段」不是「退回默认值」，而是**把这个能力关掉**。
    #    ⚠️ 这里以前是一份**手写 8 键清单**，实测漏了三个有害的（第十二路审计的可行性那一轮）：
    #      * `standalone_sender_fn` ⇒ `tools/send_message_senders.py:319` 直接报
    #        「plugin not registered or missing standalone_sender_fn」，**cron 在没有常驻网关
    #        的进程里投递会失败**，`send_message` 工具同理；
    #      * `max_message_length=8000` ⇒ `gateway/run_turn_runner.py:431` 读它做智能分块，
    #        丢掉后长回复不再按 8000 切分；
    #      * `apply_yaml_config_fn` ⇒ `feishu.allow_bots` 这类 YAML→env 桥**静默失效**。
    #    所以键集改成**从 dataclass 字段派生**：只排除「身份/覆盖类」字段（那些必须由我们
    #    自己给值），其余一律照抄。新增字段时自动跟随，不会再出现「升级后静默丢能力」。
    #    门禁：`tests/check_override.py` 拿注册前后的 entry **逐字段比对**，漏一个即红。
    #    ⚠️ P3 的唯一**有意覆盖**字段是 `standalone_sender_fn`：官方内置 sender 自己 new
    #    官方适配器 ⇒ cron / 无网关进程只能收到纯文本；我们换成经 `_factory` 的卡片 sender
    #    （媒体附件仍回落内置），因此 check_override 对这一个字段的判据是「可调用 + 非内置同名」。
    passthrough: Dict[str, Any] = {}
    builtin_standalone = getattr(builtin, "standalone_sender_fn", None)
    for field in _dc_fields(PlatformEntry):
        if field.name in _IDENTITY_ENTRY_FIELDS:
            continue
        value = getattr(builtin, field.name, None)
        if value is None or value == [] or value == "":
            continue                      # 内置也没设 ⇒ 不必显式传（传了也是默认值）
        passthrough[field.name] = value
    try:
        passthrough["standalone_sender_fn"] = _make_standalone_sender(
            _factory, builtin_standalone)
    except Exception as exc:  # pragma: no cover - 防御性：构造不出就退回内置
        logger.warning("[larkdeck] 构造 cron 卡片发送器失败，退回内置：%s",
                       type(exc).__name__)

    handle = ctx.register_platform(
        name=PLATFORM_NAME, label=LABEL, adapter_factory=_factory,
        check_fn=builtin.check_fn, **passthrough,
    )
    if handle is None:
        _remember_selfcheck(False, "register_platform 未接管成功（同名注册被拒）")
        return

    # 3.5) 订阅官方钩子（只读观察型），给页脚喂模型名 / 上下文用量。
    #      注册失败不改变自检结论：卡片照常工作，只是页脚少一两段。
    _apply_metrics_config()
    try:
        HOOKS.clear()
        HOOKS.update(_hooks.register(ctx))
    except Exception as exc:  # pragma: no cover - 防御性
        logger.warning("[larkdeck] 钩子订阅异常: %s", exc, exc_info=True)

    # 3.6) 注册插件命令 `/larkdeck`（公开 API：`ctx.register_command`）。
    #      它把「插件到底在不在动」变成用户自己**问得出来**的一件事 —— 启动自检只证明
    #      「注册那一刻接管成功」，此后插件是死是活没有任何证据，而本项目的失败形态
    #      全是静默的（钩子被改名 ⇒ 页脚空、帧失败 ⇒ 掉成纯文本）。账本见 context。
    #      ⚠️ 命令派发只挂在核心的**空闲态**路径上（`gateway/run_inbound.py` 的 idle 分支），
    #      **飞书网关里**生成回答期间发的命令会被当成普通输入排队 —— 这不是我们的选择，help 里必须写明。
    #      ⚠️ 但**别把它写成绝对规则**（R9 审计低-1）：CLI / TUI 里插件命令是**直接调处理器**的
    #      （`cli.py::_run_plugin_slash_command`、`tui_gateway/methods_tools.py`），没有忙碌概念 ⇒
    #      「仅空闲态可用」在客户端那边**不成立**。原措辞把一条「飞书网关的实现限制」说成了
    #      「这条命令的性质」，用户会在 CLI 里敲之前先怀疑它不能用。
    #      注册不到不是错误：卡片照常工作，只是少一个自检入口（所以不影响自检结论）。
    try:
        register_command = getattr(ctx, "register_command", None)
        if not callable(register_command):
            COMMAND.update({"registered": False,
                            "why": "当前 Hermes 未提供 ctx.register_command()"})
            logger.warning("[larkdeck] 当前 Hermes 未提供 ctx.register_command()，"
                           "`/larkdeck status` 不可用（卡片功能不受影响）")
        else:
            handle_cmd = register_command(
                LARKDECK_COMMAND, _ld_command_card,
                description=_i18n.t("cmd.description"),
                args_hint="[status|config|help]")
            COMMAND.update({"registered": bool(handle_cmd),
                            # ⚠️ 归因必须**可判定**（R9 审计低-6）：真核心的语义是
                            # 「与**内置命令**重名 ⇒ 跳过并返回 None」，而**同名插件命令
                            # 再注册一次是被覆盖、且返回真值**（审计 `inner_reg2.py` 用真
                            # `PluginContext` 实测）。所以旧文案「同名命令已被占用」在核心上
                            # **不可能为真** —— 一句永远不成立的原因会把排障带向错误方向
                            # （用户会去找那个根本不存在的占位者）。
                            "why": "" if handle_cmd else "注册被拒（与内置命令重名，或核心拒绝）"})
            if not handle_cmd:
                logger.warning("[larkdeck] `/larkdeck` 命令注册被拒"
                               "（与内置命令重名，或核心拒绝）")
    except Exception as exc:  # pragma: no cover - 防御性
        COMMAND.update({"registered": False, "why": f"注册异常：{exc}"})
        logger.warning("[larkdeck] `/larkdeck` 命令注册异常: %s", exc, exc_info=True)

    # 4) 启动自检：确认解析出来的 feishu 工厂确实是我们这个。
    try:
        current = platform_registry.get(PLATFORM_NAME)
        ours = current is not None and current.adapter_factory is _factory
    except Exception:
        ours = False
    if ours:
        hooks_ok = [name for name, ok in HOOKS.items() if ok]
        detail = f"Hermes {_compat.hermes_version()} · feishu 平台已由 larkdeck 接管"
        # 传输必须自报：默认值翻了之后，「这个进程到底在跑哪条传输」在真机上**没有别的自证手段**
        # （探针验的是它自己那个进程；卡片长得像不像逐字只有眼睛能判）。这一行让日志能直接回答
        # 「现在生效的是 patch 还是 cardkit」，也让「翻了默认却没重启」当场看得出来。
        detail += f" · native 传输 {LarkDeckMixin._ld_transport()}"
        detail += (f" · 钩子 {'/'.join(hooks_ok)}" if hooks_ok
                   else " · 未订阅到钩子（页脚缺模型/上下文用量）")
        # 命令注册必须**如实自报**：注册不到时要说得出来为什么，否则用户会以为
        # `/larkdeck` 能用、敲了却没反应 —— 又一处静默失灵。
        # ⚠️ 判据是 `COMMAND["registered"]`，而它来自 `ctx.register_command()` 的**返回值**；
        # 但「我们记的」与「核心注册表里真的有没有」是两件事（R9 审计中-6：把它硬编码成 `True`
        # 四门禁全绿）。所以 `check_override.py` 会拿**核心自己的 getter**
        # （`get_plugin_command_handler`）核对这句自报 —— 那句所谓「如实」才有判别力。
        # 注册状态在这里求值一次、也只写一次，避免同一件事两处取值（推论 13）。
        _cmd_registered = bool(COMMAND.get("registered"))
        detail += (f" · /{LARKDECK_COMMAND} 命令已注册" if _cmd_registered
                   else f" · /{LARKDECK_COMMAND} 命令未注册"
                        f"（{COMMAND.get('why') or '未知原因'}）")
        _remember_selfcheck(True, detail)
    else:
        _remember_selfcheck(False, "注册表里 feishu 仍指向别处，卡片不会生效")
