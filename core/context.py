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
from typing import Any, Dict, Optional

logger = logging.getLogger("larkdeck.context")

_LOCK = threading.Lock()

#: 最近一次 API 调用的指标快照（进程级）。
_LATEST: Dict[str, Any] = {}

#: ``model@base_url`` -> 上下文上限；``None`` 表示查过但没查到（同样是有效缓存）。
_MAX_CACHE: Dict[str, Optional[int]] = {}

#: 配置钉住的上下文上限，优先级高于自动探测（所有模型统一生效）。
_MAX_OVERRIDE: Optional[int] = None

#: 用户配置的模型别名：真名 -> 显示名。
_ALIASES: Dict[str, str] = {}


# --------------------------------------------------------------------------- #
# 采集
# --------------------------------------------------------------------------- #
def _as_int(value: Any) -> Optional[int]:
    """宽容地把钩子里的数字转成 int；转不了就返回 None（不抛）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(float(value))
    except (TypeError, ValueError):
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
    """模型上下文窗口大小；查不到返回 ``None``（页脚会退化成只显示已用量）。

    优先级：配置钉住的值 → ``get_model_context_length()`` 自动探测。
    自动探测可能做一次网络/目录查询，所以结果按 ``model@base_url`` 缓存，
    同一个模型永远只查一次。
    """
    if _MAX_OVERRIDE:
        return _MAX_OVERRIDE
    model = (model or _LATEST.get("model") or "").strip()
    if not model:
        return None
    base_url = (base_url or _LATEST.get("base_url") or "").strip()
    key = f"{model}@{base_url}"
    with _LOCK:
        if key in _MAX_CACHE:
            return _MAX_CACHE[key]
    value: Optional[int] = None
    try:
        from agent.model_metadata import get_model_context_length  # 公开 API
        value = _as_int(get_model_context_length(model, base_url=base_url))
        if value is not None and value <= 0:
            value = None
    except Exception:
        logger.debug("[larkdeck] 取上下文上限失败（model=%s）", model, exc_info=True)
    with _LOCK:
        _MAX_CACHE[key] = value
    return value


def set_context_override(value: Optional[int]) -> None:
    """手工钉住上下文上限（配置 ``context_max_override`` 用；0/None 表示取消）。"""
    global _MAX_OVERRIDE
    _MAX_OVERRIDE = _as_int(value) or None


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
    }


def reset() -> None:
    """清空采集数据（最近快照 + 上下文上限缓存）。

    配置类状态（``_MAX_OVERRIDE`` / ``_ALIASES``）**不清** —— 它们来自用户配置，
    只能被新的配置覆盖，不该被测试或 ``/new`` 顺手抹掉。
    """
    with _LOCK:
        _LATEST.clear()
        _MAX_CACHE.clear()
