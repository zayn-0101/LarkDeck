"""运行时指标采集 —— 官方钩子喂数据，卡片页脚读快照。

为什么要有这一层
----------------
卡片页脚想显示「模型 + 上下文用量」，但这两个数字**不在**流式适配器的
``send()`` / ``edit_message()`` 入参里：``metadata`` 只带话题信息。
其余五家飞书卡片插件（HFC / fry-cards / lark-streaming / lark-hls-v2 / aiduPOP）
全都靠 **patch 核心 ``run_turn.py``**，从一个叫 ``_hfc_completed_locals`` 之类的
内部字典里把 ``model`` / ``context.used_tokens`` 抠出来 —— 升级 Hermes 就得重打补丁。

larkdeck 的替代路径（零源码改写）
--------------------------------
* 已用上下文 → 官方钩子 ``post_api_request`` 的 ``usage.input_tokens``
  （就是这一次请求的 prompt tokens，等于当前上下文大小）；
* 模型名 → 同一钩子的 ``model`` / ``response_model``；
* 上下文上限 → ``agent.model_metadata.get_model_context_length()``（公开函数）。

钩子每次 API 调用后都触发，所以比「只在封卡时算一次」更新得更勤。

线程模型
--------
钩子回调跑在核心的**独立 worker 线程**上（不是调用方线程），而 ``post_api_request``
**在超时受限集合里** —— 超时的回调会被丢弃，并触发一个 60 秒的抑制窗口。
所以 :func:`record_api_call` **只做内存写入，绝不做 IO / 网络 / 加锁等待**：
纪律不变，但理由不是「别拖慢调用方」，而是「别把自己拖进超时抑制」。
重量级的上下文上限查询放在渲染时才做，并且按 ``model@base_url`` 缓存。

已知取舍
--------
快照是**进程级全局**的（最后一次 API 调用）。单 agent 进程下这正确；
多会话并发时页脚可能显示另一会话的模型 —— 与内置适配器共享同一
``metadata`` 契约的现实相符，换来的是完全不用碰核心代码。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from . import i18n as _i18n

logger = logging.getLogger("larkdeck.context")

def _shared_box() -> Dict[str, Any]:
    """进程级共享容器本体 —— **委托面板层的唯一入口**（R11-A0）。

    ⚠️ 这里**过去自己声明了一串键**（`panel_state` / `status` / `ctx_latest` …），与
    `panel._shared_state()` 构成「同一件事两处真相」：给盒子加一个键很容易只加一处，
    而漏掉的那一份会**静默新建一个不属于盒子的容器** —— 症状与「世代裂脑」一模一样
    （钩子写一份、卡片读另一份），而这次连「两份模块对象」都看不出来。
    现在键清单只在 `panel._SHARED_BOX_FACTORY` 一处；本模块只读/取用。
    依赖方向是安全的：`panel` 只 import 标准库，不 import 本模块。
    """
    from . import panel as _panel
    return _panel.shared_box()


#: 指标层的互斥锁 —— **从共享盒子取**（与 `panel._LOCK` 同一条纪律：容器共享了、锁也必须共享，
#: 否则同一进程里两份模块对象各持一把锁，`_LATEST`/`_STATUS` 的读改写就不再互斥）。
_LOCK: threading.Lock = _shared_box()["context_lock"]

def _shared_status() -> Dict[str, Any]:
    """账本容器挂在**进程级稳定位置**上（理由见 `panel._shared_state` 的长注释）。

    ⚠️ 2026-09-14 真机实测：同一进程里插件被发现两次 ⇒ 本模块有两份模块对象 ⇒
    钩子记在 A 份、`/larkdeck` 读 B 份 ⇒ 卡片上永远写「最近写卡：无记录 · 累计 0 帧」，
    而 DM 里明明躺着一张张卡。共享同一个 dict 之后这个症状从构造上消失。
    """
    box = _shared_box()
    if not isinstance(box.get("status"), dict):
        box["status"] = dict(_STATUS_DEFAULTS)
        # ⚠️ 这两个键**必须在这里换成新对象/新时间**，不能直接沿用 `_STATUS_DEFAULTS` 里的：
        # ``dict(...)`` 是浅拷贝 ⇒ `"codes": {}` 会被所有副本共享（清一次等于清全部），
        # 那是本项目最恨的那类静默别名；而 `started_at` 是**进程起点**，
        # 只能在建盒子这一刻取（它不是「运行期计数」，所以 `_clear_status_locked` 不清它）。
        box["status"]["codes"] = {}
        box["status"]["started_at"] = time.time()
    return box["status"]


_STATUS_DEFAULTS: Dict[str, Any] = {
    "inbound_at": None, "inbound_count": 0,
    "frame_ok_at": None, "frame_ok_count": 0,
    "frame_fail_at": None, "frame_fail_count": 0, "frame_fail_reason": "",
    # R11-C2 三条新记录（`/larkdeck status` 要读）
    "started_at": None,
    "fallback_at": None, "fallback_count": 0, "fallback_reason": "",
    "codes": {}, "code_total": 0,
}

#: 错误码表最多记多少个**不同的**码 —— 超出的**一律并进 `other`**。
#: ⚠️ 口径：这是「不同的码」的上界，键数上界是 **+1**（多一个 `other` 桶）。
#: 没有这个上限，一个「每个响应码都不同」的坏上游能让这张表无限长大。
_MAX_CODE_KEYS = 24


#: 最近一次 API 调用的指标快照（进程级）。
# ⚠️ 与 `_STATUS` 同一条纪律（见 `_shared_status` 的长注释）：**这些容器也必须进程内共享**。
# 否则「钩子记录最近一次 API 请求、卡片读它画页脚」会分开落在两份模块对象上 ⇒
# **页脚永远不显示**（用户实测：卡片上没有那行 `ctx 4.3k/20k · 22%`，
# 而接口层一切正常、没有任何报错）。同一进程里插件被发现两次是 Hermes 的既有行为。
_LATEST: Dict[str, Any] = _shared_box()["ctx_latest"]

#: ``model@base_url`` -> 上下文上限；``None`` 表示查过但没查到（同样是有效缓存）。
_MAX_CACHE: Dict[str, Optional[int]] = _shared_box()["ctx_max_cache"]

#: 正在后台探测的 key（避免同一模型被并发渲染起出一堆线程）。
# ⚠️ 也必须进程内共享（R11-A0）：两世代各持一份 ⇒ 同一个 key 会被探测两次（多起线程、
# 多花一次网络往返），而且 `_INFLIGHT.discard` 只清掉自己那份 ⇒ 另一份的 key **永远留在
# 「在飞」集合里**、那个模型的上下文窗口**永远不会被探测**（静默退化成家族兜底）。
_INFLIGHT: set = _shared_box()["ctx_inflight"]

#: 探测**失败**后的退避截止时刻（单调钟）。失败不写负缓存，只退避重试。
_RETRY_AFTER: Dict[str, float] = _shared_box()["ctx_retry_after"]
_PROBE_FAIL_BACKOFF_SECONDS = 300.0

#: 配置钉住的上下文上限，优先级高于自动探测（所有模型统一生效）。
#: 标量没法跨模块对象共享 ⇒ 用**长度 1 的盒子**（与 panel 的「最近活跃会话」同一手法）
_MAX_OVERRIDE_BOX: list = _shared_box()["ctx_max_override"]

#: 用户配置的模型别名：真名 -> 显示名。
_ALIASES: Dict[str, str] = _shared_box()["ctx_aliases"]

# --------------------------------------------------------------------------- #
# R9 自检账本 ——「插件到底在不在动」的三条证据
# --------------------------------------------------------------------------- #
#: 三条记录：入站心跳 / 成功帧 / 失败帧（各带时刻与累计次数）。
#:
#: 为什么需要：启动自检只证明**注册那一刻**接管成功，此后插件是死是活没有任何证据 ——
#: 而本项目的失败形态**全是静默的**（官方给钩子改名 ⇒ 页脚空、帧失败 ⇒ 掉成纯文本且
#: 只在日志里限流留痕）。``/larkdeck status`` 就是「事后自证」的入口。
#:
#: ⚠️ 判据纪律：**没记录就写「无记录」**，绝不许把「没有数据」渲染成「正常」——
#: 那正是「绿而无判别力」（docs/lessons.md 推论 6）：一个永远说「正常」的自检，
#: 与一个坏掉的自检在用户眼里长得一模一样。
#:
#: ⚠️ **口径：进程级全局、跨会话共享**（R9 审计中-4，与页脚指标同一件事）。`_STATUS` 是
#: 模块级 dict，没有 session 维度：两个会话并发（或一个进程服务多人）时，「累计 42 条消息 /
#: 118 帧写卡」里可能大部分来自**别的会话**，「最近写卡失败：1 次 · <原因>」也可能是别人的失败。
#: 所以 `/larkdeck status` 的卡片**必须**带一句口径说明（i18n `cmd.scope`），README/AGENTS
#: 也要写 —— 页脚那条同类说明早就在，账本这条此前一个字都没有，用户会拿别人的失败去查
#: 自己的卡（正是本阶段要消灭的「静默误诊」）。要真做对得从钩子载荷的 `session_id` 分桶
#: （与页脚同一件事，**未做**）。
_STATUS: Dict[str, Any] = _shared_status()

#: 失败原因存进账本前截断到多少字符（原因来自异常字符串 / SDK 返回，长度不可控）。
_STATUS_REASON_MAX = 120

#: 时刻的**下界**（epoch 秒）：早于它的都按「无记录」处置（R9 审计低-3）。
#: 理由：写入账本的唯一来源是 `time.time()`（≈1.7e9），而 `_when()` 以前只判 `ts > 0`，
#: 于是 `ts = 1e-6` 会渲染成 `01-01 08:00:00`（本地时区下的 1970-01-01）—— 一个**看起来
#: 像真时刻**的脏值，比「无记录」危险得多：用户会以为插件刚刚写过卡。
#: 1e9 ≈ 2001-09-09，比任何真实运行时刻都早得多，所以收紧它不会误伤（审计实测
#: `_when(1e18)`、`nan`、`inf`、负数都已经是「无记录」，只有极小的正数是漏网的）。
_EPOCH_FLOOR = 1e9


# --------------------------------------------------------------------------- #
# 采集
# --------------------------------------------------------------------------- #
def _as_float(value: Any) -> Optional[float]:
    """宽容地把钩子里的数字转成 float（秒/毫秒这类）；转不了返回 ``None``。

    与 :func:`_as_int` 同一套纪律：**连 ``OverflowError`` 一起接住**（``float("inf")``
    合法但没意义），并且绝不抛 —— 指标是装饰，不能因为它把钩子链搞坏。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")):   # NaN / ±Inf
        return None
    return number


def _as_int(value: Any) -> Optional[int]:
    """宽容地把钩子里的数字转成 int；转不了就返回 None（不抛）。

    ⚠️ 必须连 ``OverflowError`` 一起接住：``int(float("inf"))`` 抛的是它，不是
    ``ValueError``。漏了会让整条调用链炸掉 —— 配置里写 ``1e999`` / ``.inf`` / ``inf``
    就能一路穿到 ``register()``，插件注册整体失败、**静默退回纯文本**。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):  # NaN / ±Inf
            return None
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return None


def record_api_call(**payload: Any) -> None:
    """``post_api_request`` 钩子回调 —— 热路径，必须廉价。

    只读 ``payload`` 里的几个字段写进全局快照。任何异常都吞掉：指标是装饰，
    绝不能让钩子抛异常污染正常的 API 调用链。
    """
    try:
        usage = payload.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        model = str(payload.get("response_model") or payload.get("model") or "").strip()
        input_tokens = _as_int(usage.get("input_tokens"))
        out_tokens = _as_int(usage.get("output_tokens"))
        cache_read = _as_int(usage.get("cache_read_tokens"))
        # 上下文占用要的是「这次请求送进去多少 token」。缓存命中的部分同样占窗口，
        # 所以优先用 ``prompt_tokens``（= input + cache_read + cache_write，由 Hermes
        # 的 CanonicalUsage 算好）；拿不到才退回 ``input_tokens`` —— 否则开了提示缓存
        # 的会话会严重低报占用（命中部分不计入 input_tokens）。
        prompt_tokens = _as_int(usage.get("prompt_tokens"))
        context_used = prompt_tokens if prompt_tokens is not None else input_tokens
        if not model and context_used is None:
            return  # 没有任何可用信息，别污染上一帧
        # 只有模型名、没有用量：**整帧不更新**。
        # 只刷 model/provider 会把「新模型的显示名」和「上一个模型的 token 数」拼在一起
        # —— 页脚于是显示一个从未存在过的组合，context_pct 还会拿新模型的窗口去除旧数字
        # （被 min(100) 掩盖，看起来只是"偏高"）。
        # 首帧例外：那时还没有任何快照，光有模型名也值得记下来（页脚先显示模型名）。
        if context_used is None and _LATEST:
            return
        with _LOCK:
            prev = dict(_LATEST)
            _LATEST.update({
                "model": model or prev.get("model", ""),
                "provider": str(payload.get("provider") or prev.get("provider", "")),
                "base_url": str(payload.get("base_url") or prev.get("base_url", "")),
                "input_tokens": (context_used if context_used is not None
                                 else prev.get("input_tokens")),
                "prompt_tokens": prompt_tokens if prompt_tokens is not None
                                 else prev.get("prompt_tokens"),
                "output_tokens": (out_tokens if out_tokens is not None
                                  else prev.get("output_tokens")),
                "cache_read_tokens": (cache_read if cache_read is not None
                                      else prev.get("cache_read_tokens")),
                "api_call_count": _as_int(payload.get("api_call_count")),
                # ---- R7 页脚扩展要用的字段（语义来自 Hermes 的 `_fire_post_api_request_hook`：
                # `first_chunk_at` 是**首个流式分块的 epoch 秒**，官方注释写明
                # TTFB = `first_chunk_at - started_at`；`api_duration` 是**本次**请求耗时，
                # 不是回合耗时（回合耗时在面板标题里，两者别混）。----
                "cache_write_tokens": _as_int(usage.get("cache_write_tokens")),
                "reasoning_tokens": _as_int(usage.get("reasoning_tokens")),
                "total_tokens": _as_int(usage.get("total_tokens")),
                "api_duration": _as_float(payload.get("api_duration")),
                "started_at": _as_float(payload.get("started_at")),
                "first_chunk_at": _as_float(payload.get("first_chunk_at")),
                "finish_reason": str(payload.get("finish_reason") or ""),
                "message_count": _as_int(payload.get("message_count")),
                "session_id": str(payload.get("session_id") or ""),
                "platform": str(payload.get("platform") or ""),
                "at": time.time(),
            })
            first = not prev
            rec = dict(_LATEST)
        if first:
            # 只在「第一帧」记一条 INFO：这是启动自检里最有用的一句 ——
            # 它证明钩子真的挂上了，而不是插件默默没生效。
            logger.info("[larkdeck] 指标已接入：model=%s · input_tokens=%s · provider=%s",
                        rec.get("model"), rec.get("input_tokens"), rec.get("provider"))
    except Exception:  # pragma: no cover - 防御性：钩子绝不能抛
        logger.debug("[larkdeck] 指标采集忽略了一次异常", exc_info=True)


# --------------------------------------------------------------------------- #
# 上下文上限
# --------------------------------------------------------------------------- #
def context_max(model: str = "", base_url: str = "") -> Optional[int]:
    """模型上下文窗口大小；**查不到或还没探到时返回 ``None``**（页脚退化成只显示已用量）。

    优先级：配置钉住的值 → 缓存 → 后台探测（本次仍返回 ``None``）。

    ⚠️ **探测绝不能在调用线程上同步做。** 这个函数由 ``snapshot()`` → ``_ld_footer()``
    进入，而三个入口（``send`` / ``edit_message`` / ``send_stream_frame``）都是 async，
    跑在**事件循环线程**上；``get_model_context_length()`` 会发 HTTP（官方为此专门提供
    ``get_model_context_length_async``，注释写明 blocking HTTP 会 stall the event loop）。
    同步探测会让首次渲染页脚时**所有会话的流式帧一起停等几秒**。

    所以：命中缓存立即返回；未命中就起一个守护线程去探，本次返回 ``None``，
    下一个渲染周期自然拿到值。代价是页脚的 ctx 段可能晚一帧出现 —— 可接受。
    """
    if _MAX_OVERRIDE_BOX[0]:
        return _MAX_OVERRIDE_BOX[0]
    model = (model or _LATEST.get("model") or "").strip()
    if not model:
        return None
    base_url = (base_url or _LATEST.get("base_url") or "").strip()
    key = f"{model}@{base_url}"
    now = time.monotonic()
    with _LOCK:
        if key in _MAX_CACHE:
            return _MAX_CACHE[key]
        # 「查缓存 → 未命中 → 未在探测中 → 置位 + 起线程」必须在同一把锁内完成，
        # 否则并发渲染会对同一模型起出一堆线程。
        if key not in _INFLIGHT and now >= _RETRY_AFTER.get(key, 0.0):
            _INFLIGHT.add(key)
            threading.Thread(target=_probe_context_max, args=(key, model, base_url),
                             name="larkdeck-ctx-probe", daemon=True).start()
    return None


def _probe_context_max(key: str, model: str, base_url: str) -> None:
    """后台线程：探测上下文上限并写缓存。**任何异常都不许穿出去**。

    刻意用 ``threading.Thread(daemon=True)`` 而不是线程池：非守护线程在解释器退出时
    会被 join，而这里的探测没有超时 —— 那样会把 Hermes 的退出拖住。
    """
    value: Optional[int] = None
    probed = False
    try:
        from agent.model_metadata import get_model_context_length  # 公开 API

        value = _as_int(get_model_context_length(model, base_url=base_url))
        if value is not None and value <= 0:
            value = None
        probed = True  # 探测本身跑通了 —— 即便结论是「查不到」
    except Exception:
        logger.debug("[larkdeck] 取上下文上限失败（model=%s）", model, exc_info=True)
    with _LOCK:
        _INFLIGHT.discard(key)
        if probed:
            # 探测跑通但结论是「未知」→ 负缓存：查过一次就不再查（与原行为一致）
            _MAX_CACHE[key] = value
        else:
            # 探测**失败**（网络抖动 / 导入异常）：**不写负缓存**，只退避重试。
            # 否则一次网络问题会把该模型的百分比永久钉死成「只显示已用量」。
            _RETRY_AFTER[key] = time.monotonic() + _PROBE_FAIL_BACKOFF_SECONDS


def set_context_override(value: Optional[int]) -> None:
    """手工钉住上下文上限（配置 ``context_max_override`` 用；0/None 表示取消）。"""
    _MAX_OVERRIDE_BOX[0] = _as_int(value) or None


# --------------------------------------------------------------------------- #
# 模型别名
# --------------------------------------------------------------------------- #
def _split_aliases(spec: str) -> Dict[str, str]:
    """``"a=b, c=d"`` -> ``{"a": "b", "c": "d"}``；脏输入静默跳过。"""
    out: Dict[str, str] = {}
    for chunk in str(spec or "").split(","):
        name, sep, alias = chunk.partition("=")
        name, alias = name.strip(), alias.strip()
        if sep and name and alias:
            out[name] = alias
    return out


def set_aliases(mapping: Optional[Any] = None, *, spec: str = "") -> None:
    """设置模型别名。接受 dict，也接受 ``"真名=显示名, ..."`` 字符串。"""
    merged: Dict[str, str] = {}
    if isinstance(mapping, dict):
        merged.update({str(k).strip(): str(v).strip()
                       for k, v in mapping.items() if str(k).strip() and str(v).strip()})
    merged.update(_split_aliases(spec))
    with _LOCK:
        _ALIASES.clear()
        _ALIASES.update(merged)


def known_aliases() -> Dict[str, str]:
    with _LOCK:
        return dict(_ALIASES)


def display_model(name: str) -> str:
    """把真实模型名换成显示名。

    优先用户别名；否则做一次保守的瘦身：去掉 ``vendor/`` 前缀和 ``:free`` 之类后缀，
    保留本名 —— 宁可原样显示，也不猜一个可能错的名字。
    """
    name = str(name or "").strip()
    if not name:
        return ""
    with _LOCK:
        alias = _ALIASES.get(name)
    if alias:
        return alias
    short = name.rsplit("/", 1)[-1]          # openrouter 那种 vendor/model
    short = short.split(":", 1)[0]           # 后缀变体 :free / :nitro
    return short or name


# --------------------------------------------------------------------------- #
# 读快照
# --------------------------------------------------------------------------- #
def snapshot() -> Dict[str, Any]:
    """当前指标快照（含算好的百分比与显示名）。数据不全时字段为 ``None``。

    ``input_tokens`` 的语义：**最近一次 API 请求送进去的输入 token 数**，
    也就是「上下文此刻占了多少」—— 优先取 ``prompt_tokens``（含缓存命中），
    不是整个会话的累计消耗。
    """
    with _LOCK:
        raw = dict(_LATEST)
    used = raw.get("input_tokens")
    model = raw.get("model", "")
    maximum = context_max(model, raw.get("base_url", "")) if model else None
    pct: Optional[float] = None
    if isinstance(used, int) and isinstance(maximum, int) and maximum > 0:
        pct = min(100.0, used / maximum * 100.0)
    return {
        "model": model,
        "model_display": display_model(model),
        "provider": raw.get("provider", ""),
        "input_tokens": used,
        "prompt_tokens": raw.get("prompt_tokens"),
        "output_tokens": raw.get("output_tokens"),
        "cache_read_tokens": raw.get("cache_read_tokens"),
        "api_call_count": raw.get("api_call_count"),
        "context_max": maximum,
        "context_pct": pct,
        "session_id": raw.get("session_id", ""),
        "age": (time.time() - raw["at"]) if raw.get("at") else None,
        # ---- R7：页脚扩展用的派生值（**缺数据就是 None，绝不编 0**）----
        "cache_write_tokens": raw.get("cache_write_tokens"),
        "reasoning_tokens": raw.get("reasoning_tokens"),
        "total_tokens": raw.get("total_tokens"),
        "finish_reason": raw.get("finish_reason", ""),
        "message_count": raw.get("message_count"),
        # 缓存命中率 = 命中量 / 本次送进模型的输入量（prompt = input + 缓存读 + 缓存写）。
        # 分母取 prompt_tokens 而不是 input_tokens：后者不含缓存部分，会把比例算高。
        "cache_pct": _pct(raw.get("cache_read_tokens"), raw.get("prompt_tokens")),
        # 首字延迟（TTFB）：官方注释给的定义就是 first_chunk_at - started_at。
        "ttfb_ms": _diff_ms(raw.get("first_chunk_at"), raw.get("started_at")),
        "api_duration_ms": (round(raw["api_duration"] * 1000)
                            if isinstance(raw.get("api_duration"), float) else None),
    }


def _pct(part: Any, whole: Any) -> Optional[float]:
    """``part / whole`` 的百分比；任一侧不可用、或分母非正 ⇒ ``None``（**不编 0**）。"""
    if not isinstance(part, int) or not isinstance(whole, int) or whole <= 0:
        return None
    return min(100.0, max(0.0, part / whole * 100.0))


def _diff_ms(later: Any, earlier: Any) -> Optional[int]:
    """两个 epoch 秒之间差多少**毫秒**；任一缺失或差为负 ⇒ ``None``。"""
    if not isinstance(later, float) or not isinstance(earlier, float):
        return None
    delta = later - earlier
    if delta < 0:
        return None
    return int(round(delta * 1000))


def note_inbound() -> None:
    """入站心跳：``pre_gateway_dispatch`` 每收到一条入站消息记一次。

    这个钩子在 **auth 之前**、每条消息都跑（纪律见 :mod:`larkdeck.core.hooks`），
    所以这里只做一次加锁写、绝不做 IO、异常自吞。**调用点是那个回调的第一行**
    （R9 审计中-3）：它以前排在归属绑定后面、还在同一个 try 块的末尾，
    于是兄弟模块抛异常就会连心跳一起吞掉 —— 而心跳正是判「消息到没到插件」的唯一凭据。

    ⚠️ **归因必须可判定**（R9 审计中-3 的措辞更正）。旧措辞「心跳不动 ⇒ 消息根本没到插件」
    是**错的**：心跳不动还有好几种完全健康的情形，而且它们都在**插件之外或插件之前**：
      * 消息在网关的**更早关口**就被挡下 —— `_hm_admit_event` 里有四类消息在
        `pre_gateway_dispatch` **之前**就 return 了：internal 合成事件（后台进程通知 /
        子代理回报）、profile 路由被拒、Slack 忽略频道、启动恢复期队列（审计实读
        `gateway/run_inbound.py:152-184`）。这些消息**从来没到过我们的钩子**。
    所以正确的说法是：
    ``心跳不动`` ⇒ 消息没到插件的钩子回调（进程换了 / 平台名被别的插件抢走 / 在网关更早的
    关口就被挡下）；``心跳在动而写卡不动`` ⇒ 消息到了，问题在卡片侧（配置关了 / 帧全失败）。
    另一面同样要记住：**心跳在动 ≠ 这条消息会被处理** —— 未授权发送者、没有 user_id 的消息
    是在钩子**之后**被拒的，心跳照记。
    """
    try:
        with _LOCK:
            _STATUS["inbound_at"] = time.time()
            _STATUS["inbound_count"] = int(_STATUS.get("inbound_count") or 0) + 1
    except Exception:  # pragma: no cover - 防御性：钩子绝不能抛
        logger.debug("[larkdeck] 入站心跳采集忽略了一次异常", exc_info=True)


def note_frame_ok() -> None:
    """**一帧真的有东西写出去了** —— 这个账本数的是**帧数**，不是「写了几次 API」。

    R9 审计中-1 之后，它的调用点分两类（**一次写只记一笔**由构造保证）：
      * **底层收口点**：``_ld_send_card`` / ``_ld_update_card`` 成功处 —— 覆盖
        native 的 patch 帧与收尾帧、CardKit 的收尾帧、DEGRADE 的整卡补写、
        非 native 的 ``edit_message``、澄清卡 ``send_clarify``、``/stop`` 的中止重绘；
      * **两条帧路径特例**（不经过上面两个原语，所以只能在这里记）：CardKit 的
        **seed 建实体**（`card.create` + 发实体卡）与**每帧元素写**（`card_element.content`
        + 装饰 `batch_update`）。
    判据始终是「**真的有东西到了飞书**」，不是「函数返回了 True」。

    ⚠️ **口径（R9 审计中-5，别再叫它「写卡次数」）**：一帧 = +1，**哪怕这一帧做了多次写**：
    cardkit 一帧最多 3 次逻辑写（装饰 batch + 正文 content + 限频的会话预览）、
    seed 帧是 2 次网络调用（建实体 + 发实体卡）、一次逻辑写撞限流最多重发 4 次 HTTP。
    反过来，**节流跳过**的中间帧与**文本没变**的去重帧 +0（它们一个字节都没写）。
    R4 的**切卡帧**（封旧卡 + 开新卡，两次写）同样只 +1 —— 它是**一帧**，
    口径与 seed 帧一致（seed 也是两次网络调用算一帧）。
    把「没写」记成「写了」会让这张卡在长回合里自信地说「一直在写」，而假绿比没有数据更糟。
    真机探针里的那条等式（`probe_render.py --cardkit-prod`）也按这个口径写：
    它比的是**两个不同证据源**（账本计数 vs SDK 边界上的调用账本），不是拿账本核账本。
    """
    try:
        with _LOCK:
            _STATUS["frame_ok_at"] = time.time()
            _STATUS["frame_ok_count"] = int(_STATUS.get("frame_ok_count") or 0) + 1
    except Exception:  # pragma: no cover - 防御性
        logger.debug("[larkdeck] 成功帧记账忽略了一次异常", exc_info=True)


def note_frame_fail(reason: str) -> None:
    """**我们真的发起过一次写、而它失败了** —— 只记这一种（``reason`` 入库前折叠 + 截断）。

    ⚠️ **口径钉在这里（R9 审计中-2）**：这个计数与「核心收到过几个 `False`」**不等价**，
    而且不该等价。`send_stream_frame` 返回 False 有两种完全不同的来源：
      * **写卡失败**（本函数的调用者 ``_ld_stream_fail``）—— 我们发起了写，飞书拒了 / 超时了；
        这是**故障**，用户问「为什么掉成纯文本」时要看的就是它；
      * **按契约把控制权交还核心**（``_ld_stream_frame`` 里 `state is None and finalize`
        那一支）—— 没有活跃流可收尾，**一个写请求都没发**，核心会正常回落 edit/send
        把消息发出去。那是**正常路径**，把它记成失败会让一个健康回合在排障卡上显示一条
        指向不存在写卡动作的失败原因（绿而无判别力 / 红而误导，本项目最怕的两种病）。
    README 的「写卡失败」一条、i18n 的 `status.frame_fail` 文案与这条 docstring 是同一口径。
    """
    try:
        text = " ".join(str(reason or "").split())[:_STATUS_REASON_MAX]
        with _LOCK:
            _STATUS["frame_fail_at"] = time.time()
            _STATUS["frame_fail_count"] = int(_STATUS.get("frame_fail_count") or 0) + 1
            _STATUS["frame_fail_reason"] = text
    except Exception:  # pragma: no cover - 防御性
        logger.debug("[larkdeck] 失败帧记账忽略了一次异常", exc_info=True)


def note_plaintext_fallback(reason: str) -> None:
    """记一次「**本回合的卡片车道被放弃**」—— 核心会改走它的纯文本路径（R11-C2）。

    ⚠️ 口径与 :func:`note_frame_fail` **故意不同**，别混：
      * ``note_frame_fail`` = 「我们**真的发起过一次写、而它失败了**」；
      * 本函数        = 「**本回合我们把卡片车道让给了核心**」⇒ 用户**肉眼能看出**「这次没卡片」。
    ⚠️ **口径以代码为准（2026-09-15 审计 D1 指出这段文字曾与实现相反）**：调用点只有两处，
    都是「**我们真的发起过一次写、而它失败了**」（`_ld_stream_fail` 与 `send()`），
    所以在这一版实现里它与 `note_frame_fail` **同点同帧各 +1**；它**不含**「没有活跃流可收尾
    ⇒ 按契约交还核心」那条**正常路径**（一个写请求都没发，见下一条）。
    判据是后者、不是前者：核心收到 ``False`` 之后会停用本回合 native、退回 edit/send，
    所以「掉回纯文本」的用户可见次数 = 本计数，而**不等于**它收到的 ``False`` 个数。
    ⚠️ **不含那条正常路径**：``send_stream_frame(finalize=True)`` 在**没有活跃流**时返回
    False（native 没开 / 首帧就没建成卡）—— 一个写请求都没发，消息照常发出去
    （R9 审计中-2 的口径）。那条**不计**，否则健康回合会凭空多出一次「掉回纯文本」。
    """
    # ⚠️ **必须单行归一**（审计 X21）：原因串来自异常字符串 / SDK 返回，里面可以有换行；
    #    不归一的话 `/larkdeck status` 的卡片会被撑成多行，甚至把 `#` 顶到**行首**变成 markdown
    #    语法（用户看到的是标题，而它本来是失败原因的一部分）。
    #    同文件的 `note_frame_fail` 早就是同一写法（`" ".join(str(...).split())`）——
    #    两处口径本该一致，这里是漏掉的那一处。
    text = " ".join(str(reason or "").split())[:200]
    try:
        with _LOCK:
            _STATUS["fallback_at"] = time.time()
            _STATUS["fallback_count"] = int(_STATUS.get("fallback_count") or 0) + 1
            _STATUS["fallback_reason"] = text
    except Exception:                      # pragma: no cover - 记账绝不影响主路径
        logging.getLogger("larkdeck").debug("[larkdeck] 回落计数失败", exc_info=True)


def note_response_code(code: Any) -> None:
    """记一次**非零响应码**（`/larkdeck status` 的错误码 top-N）。

    调用点是**失败判定处**（不是 ``_ld_response_code`` 内部）—— 那个函数每帧被调好几次
    （包含成功路径），在里面计数会把「帧数」记成「调用数」，正是 AGENTS 里那条
    「口径是**帧**不是**次**」的纪律要防的事。
    ``0``（成功）与 ``None``（没拿到响应）都不计；不同的码最多记 :data:`_MAX_CODE_KEYS` 个。
    """
    if code in (None, 0, "0", ""):
        return
    key = str(code)
    try:
        with _LOCK:
            codes = _STATUS.get("codes")
            if not isinstance(codes, dict):
                codes = {}
                _STATUS["codes"] = codes
            if key not in codes and len(codes) >= _MAX_CODE_KEYS:
                key = "other"
            codes[key] = int(codes.get(key) or 0) + 1
            _STATUS["code_total"] = int(_STATUS.get("code_total") or 0) + 1
    except Exception:                      # pragma: no cover
        logging.getLogger("larkdeck").debug("[larkdeck] 响应码计数失败", exc_info=True)


def status_snapshot() -> Dict[str, Any]:
    """账本快照（测试 / 探针读它）。卡片**不读**它 —— 卡片要的是人读的行。

    ⚠️ **`codes` 必须跟着拷贝一层**（审计 X19 实测）：它是账本里**唯一可变**的那一格，
    只做 `dict(_STATUS)` 的话快照里的 `codes` 就是**活动账本本身** ——
    读者（测试 / 探针 / 以后的排障面板）拿 `snap["codes"]["999999"] = 42` 写一下，
    污染的是**共享账本**，而调用方以为自己只是改了个快照。
    这与 `_STATUS_DEFAULTS` 那处「浅拷贝导致清一次等于清全部」是同一个病（见 `_shared_status`）。
    """
    with _LOCK:
        return dict(_STATUS, codes=dict(_STATUS.get("codes") or {}))


def _when(ts: Any) -> str:
    """epoch 秒 → 本地 ``MM-DD HH:MM:SS``；没有记录 ⇒ i18n 的「无记录」。

    ⚠️ 刻意**不做**「N 分钟前」这类相对描述：它要么引入单复数/语言分支，要么在跨天时
    误导。绝对时间本身就是事实，也不需要判据。

    ⚠️ 判据是「**在一个真实运行时刻的合理范围内**」，不是「正数」（R9 审计低-3）：
    `ts < _EPOCH_FLOOR`（含 0 与负数）一律「无记录」—— 脏的小正数会被渲染成一个
    看着像真时刻的 1970 年日期，「无记录」才是诚实的答案。
    """
    if (not isinstance(ts, (int, float)) or isinstance(ts, bool)
            or ts < _EPOCH_FLOOR):      # 含负数与 0：`_EPOCH_FLOOR` 见上面的理由（低-3）
        return _i18n.t("status.none")
    try:
        return time.strftime("%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (ValueError, OSError, OverflowError):  # pragma: no cover - 时钟异常值
        return _i18n.t("status.none")


def status_lines() -> List[str]:
    """自检行（markdown 文本），给 ``/larkdeck status`` 卡片用。

    每行都带**累计次数**：只有「最近一次是什么时候」的话，一个刚重启的进程会显示得
    和「跑了三天一直没失败」一模一样；有了次数才能区分「从来没发生过」与「刚刚发生」。

    R11-C2 加了三条（**排障用，用户在卡上看不见**）：
      * ``status.uptime``   —— 进程已运行多久：区分「刚重启」与「跑很久了」，
        它也是判「这次故障是不是重启之后就有的」的第一个数；
      * ``status.fallback`` —— **掉回纯文本的次数**：用户**肉眼能看出**「这次没卡片」的那一类，
        与 `frame_fail_count`（我们真的发起过一次写而失败）**口径不同**，别混（见
        :func:`note_plaintext_fallback`）；
      * ``status.codes``    —— 错误码 top-5：把「失败了很多次」收敛成「失败在哪个码上」，
        而码表正是处置表的键（``docs/plan-v1.md`` 附录 A）。
    """
    snap = status_snapshot()
    failures = int(snap.get("frame_fail_count") or 0)
    fallbacks = int(snap.get("fallback_count") or 0)
    return [
        _i18n.t("status.inbound", when=_when(snap.get("inbound_at")),
                n=int(snap.get("inbound_count") or 0)),
        _i18n.t("status.frame_ok", when=_when(snap.get("frame_ok_at")),
                n=int(snap.get("frame_ok_count") or 0)),
        (_i18n.t("status.frame_fail", when=_when(snap.get("frame_fail_at")), n=failures,
                 reason=str(snap.get("frame_fail_reason") or ""))
         if failures else _i18n.t("status.frame_fail_none")),
        # ---- R11-C2：三条「用户看不见但排障必须知道」的记录 ----
        _i18n.t("status.uptime", v=_dur(snap.get("started_at"))),
        (_i18n.t("status.fallback", when=_when(snap.get("fallback_at")), n=fallbacks,
                 reason=str(snap.get("fallback_reason") or ""))
         if fallbacks else _i18n.t("status.fallback_none")),
        (_i18n.t("status.codes", n=int(snap.get("code_total") or 0), top=_codes_top(snap))
         if int(snap.get("code_total") or 0) else _i18n.t("status.codes_none")),
    ]


def _dur(ts: Any) -> str:
    """**进程已运行多久**（``2h13m`` / ``45s``）—— 读不到就写「无记录」，绝不编 0。

    ⚠️ 判据与 :func:`_when` **对齐**（R9 低-3 那条：「在一个真实运行时刻的合理范围内」，
    不是「能转成 float」）。`_dur` 的输出是给**人**看的一句话，编出来的数字比空着更难查。
    审计 Y5 实测出来的四类脏值 —— 旧版**全都编了一个数字**（或者直接炸出函数外）：
      * ``nan`` → `int(nan)` 抛 **ValueError**（记账路径上的一个异常源）；
      * ``inf`` / ``-inf`` → `int(...)` 抛 **OverflowError**（同上，`_as_int` 的 docstring
        早写过 `int(inf)` 这个坑，这里漏了）；
      * ``0`` / ``-1`` → ``'497079h14m'`` —— 「已运行 56 年」这种**看着像真数字**的脏值，
        正是 `_EPOCH_FLOOR` 要消灭的形态；
      * ``1e18``（未来时间戳）→ ``'0s'`` —— 显示成「刚重启」，而它其实是坏数据。
    ⇒ 四类一律「无记录」。反面同样钉住：真实时间戳照常算（见 `test_units` 的 ㉚ 一组）。
    """
    try:
        started = float(ts)
    except (TypeError, ValueError, OverflowError):
        return _i18n.t("status.none")
    # NaN 与任何数比较都是 False ⇒ **必须单独判**（`started != started` 是 NaN 的唯一判据）。
    # ±inf 不用单独写：`inf > now`、`-inf < _EPOCH_FLOOR` 都会落到下面那一句。
    now = time.time()
    if started != started or started < _EPOCH_FLOOR or started > now:
        return _i18n.t("status.none")
    try:
        secs = max(0, int(now - started))
    except (ValueError, OverflowError):     # pragma: no cover - 上面已经拦住，防御性兜底
        return _i18n.t("status.none")
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    return f"{secs // 3600}h{(secs % 3600) // 60}m"


def _codes_top(snap: Dict[str, Any]) -> str:
    """错误码 top-5（``300309×3 · 300313×1``）；一个都没有时返回空串（调用方换另一句文案）。"""
    codes = snap.get("codes")
    if not isinstance(codes, dict) or not codes:
        return ""
    top = sorted(codes.items(), key=lambda kv: (-int(kv[1] or 0), str(kv[0])))[:5]
    return " · ".join(f"{k}\u00d7{v}" for k, v in top)


def _clear_status_locked() -> None:
    """把账本复位（调用方已持锁）。**就地 update**，不换新 dict（外部可能持有引用）。"""
    _STATUS.update({
        "inbound_at": None, "inbound_count": 0,
        "frame_ok_at": None, "frame_ok_count": 0,
        "frame_fail_at": None, "frame_fail_count": 0, "frame_fail_reason": "",
        # R11-C2 的三条运行时记录跟着一起清 —— 否则测试/探针之间会互相继承上一次的计数，
        # 断言就没有判别力了。⚠️ `started_at` **不清**：它是**进程起点**，不是运行期计数。
        "fallback_at": None, "fallback_count": 0, "fallback_reason": "",
        "codes": {}, "code_total": 0,
    })


def reset() -> None:
    """清空采集数据（最近快照 + 上下文上限缓存）。

    配置类状态（``_MAX_OVERRIDE`` / ``_ALIASES``）**不清** —— 它们来自用户配置，
    只能被新的配置覆盖，不该被测试或 ``/new`` 顺手抹掉。
    """
    with _LOCK:
        _LATEST.clear()
        _MAX_CACHE.clear()
        # 也清掉探测状态：否则一次失败探测留下的 300s 退避会被下一个测试/探针继承，
        # 表现为「怎么探测都不触发」。
        _RETRY_AFTER.clear()
        _INFLIGHT.clear()
        # R9 账本同样是**运行时状态**（不是配置），跟着一起清 —— 否则测试之间、
        # 探针之间会互相继承上一次的心跳/帧计数，断言就没有判别力了。
        _clear_status_locked()
