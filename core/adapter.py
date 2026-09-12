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
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import cards as _cards
from . import compat as _compat
from . import context as _context
from . import hooks as _hooks
from . import i18n as _i18n
from . import panel as _panel

logger = logging.getLogger("larkdeck")

PLATFORM_NAME = "feishu"
LABEL = "Feishu / Lark — LarkDeck cards"
ADAPTER_CLASS_NAME = "LarkDeckFeishuAdapter"

#: 卡片按钮 value 里的动作键；只认自己这一个，其余一律回落给内置实现。
ACTION_KEY = "larkdeck_action"
ACTION_CLARIFY = "clarify"

#: 追踪中的卡片上限，防止长跑会话无限增长。
_MAX_TRACKED = 512

#: native 流式：并发回合上限 + 帧节流窗口（秒）。帧率过高会触发飞书限流，
#: 窗口内的中间帧直接跳过（返回 True 但不下发；下个 tick 文本变了会重试）。
_MAX_STREAMS = 64
_STREAM_MIN_INTERVAL = 0.25

#: 流状态被判为「泄漏」的静默时长（秒）。必须取**小时级**：`last_at` 只在有正文帧时
#: 推进，一个长时间只跑工具的回合看起来会很陈旧，用分钟级阈值会把活跃回合踢掉、
#: 造成重复卡。这里只用来收「核心始终没发 finalize」的真正泄漏。
_STREAM_LEAK_SECONDS = 3600.0

#: 硬上限倍数：软上限只告警、不淘汰活跃流；到这个倍数才被迫淘汰（防内存）。
_STREAM_HARD_CAP_FACTOR = 4

#: ⚠️ 这里曾经有一个「工具进度块不进正文 / 叙述归档」的实现，**已作为安全修复移除**。
#:
#: 它用裸分隔符 ``"\n\n---\n"`` 判断核心有没有往帧里拼工具进度块，但核心的合成式是
#: ``gateway/stream_consumer.py`` 的
#: ``"\n\n---\n".join(p for p in (accumulated, progress) if p)`` —— **没有工具进度时，
#: 帧文本就是累积正文本身**。而 ``---`` 独占一行、前后空行，正是普通的 markdown 分隔线，
#: 模型随时会写。于是模型一写分隔线就被误判成进度块：归档点被推到分隔线处 → 之后每帧
#: 都算不出正文 → 卡片正文被刷空 → finalize 帧的 ``display or " "`` 让整条回答只剩一个空格。
#: 更致命的是核心侧对 finalize 是**乐观记账**（``stream_consumer_transport`` 按完整帧文本
#: 记录已送达，``delivered_final_matches`` 比对通过），核心认为送达成功、**不会再补发** ——
#: 这条回答就彻底没了，任何一层都不会报错。
#:
#: 分隔符天生无歧义判据可用（两侧都是普通 markdown，核心的进度行也没有稳定形状），
#: 判错任一方向都会吞正文。所以结论是**不猜**：整帧原样渲染。代价是工具执行期间
#: 核心叠加的进度行会短暂出现在正文里 —— 那本来就是核心给 native 流式的默认呈现，
#: 而且核心在下一个正文增量到达时会自己清掉它；工具细节另有折叠面板承载。

_DEFAULTS: Dict[str, Any] = {
    "cards": True,            # 用卡片渲染回复
    "native_streaming": True, # 官方 native streaming：一回合一张卡（工具进度合入同卡）
    "clarify_cards": True,    # 澄清使用按钮卡
    "unified_panel": True,    # 推理 + 工具合并为底部一个可折叠面板
    "panel_expanded": False,  # 面板默认收起（展开态很占屏；aiduPOP 同为默认收起）
    "footer": True,           # 页脚：模型 + 上下文用量 + 耗时
    "show_model": True,       # 页脚显示模型名（关掉只剩上下文和耗时）
    "context_style": "text",  # 上下文用量样式：text（默认）| bar | both
    "model_aliases": "",      # 模型别名："真名=显示名, ..." 或 dict
    "max_reasoning_chars": _cards.MAX_REASONING_CHARS,
    "max_tool_result_chars": _cards.MAX_TOOL_RESULT_CHARS,
    "max_panel_steps": _cards.MAX_PANEL_STEPS,
    "context_max_override": 0,  # 非 0 时钉住上下文上限（自动探测不准时兜底）
}
_CONFIG: Dict[str, Any] = dict(_DEFAULTS)

#: 启动自检结论，供日志 / doctor 查看。
SELFCHECK: Dict[str, Any] = {"ok": None, "detail": "not run"}

#: 钩子订阅结论：``{钩子名: 是否成功}``；空 dict 表示还没跑过 register()。
HOOKS: Dict[str, bool] = {}


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
    if pinned:
        _context.set_context_override(pinned)


def configure(**kwargs: Any) -> None:
    """运行时覆盖配置（未知键忽略，不抛）。"""
    for key, value in kwargs.items():
        if key in _DEFAULTS:
            _CONFIG[key] = value
    _apply_metrics_config()


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


def _cfg_int(key: str, default: int = 0) -> int:
    """取整数配置；坏值一律退回 ``default``，**绝不抛**。

    ⚠️ 必须接住 ``OverflowError``：``int(float("inf"))`` 抛的是它。配置里写
    ``inf`` / ``1e999`` / ``.inf``（YAML 都合法）时，这个值会经 ``configure()``
    一路穿到 ``register()``，而那里没有任何 try —— 结果是插件注册整体失败、
    **静默退回纯文本**，正是本项目最怕的失败模式（已实测复现）。
    """
    try:
        number = float(_cfg_raw(key))
        if number != number or number in (float("inf"), float("-inf")):
            return default
        return int(number)
    except (TypeError, ValueError, OverflowError):
        return default


def _log_degrade_once(tier: str) -> None:
    """卡片降载日志 —— **限流**：60 秒最多一条。

    降载是在每个流式帧上判定的，不限流的话超预算期间每秒会刷 4 条
    （本项目自己的约定是诊断日志必须限流，见 _log_empty_panel_once）。
    """
    now = time.monotonic()
    if now - getattr(_log_degrade_once, "_at", 0.0) < 60.0:
        return
    _log_degrade_once._at = now
    logger.warning("[larkdeck] 卡片超字节预算，降载档位=%s（正文不截断；"
                   "若仍发不出会回落官方分块）", tier)


def _log_empty_panel_once() -> None:
    """面板为空时记一条限流 INFO（排查「钩子没数据」用；60 秒最多一条）。"""
    now = time.monotonic()
    if now - getattr(_log_empty_panel_once, "_at", 0.0) < 60.0:
        return
    _log_empty_panel_once._at = now
    logger.info("[larkdeck] 面板无数据（钩子未写入或已清空）")


def _remember_selfcheck(ok: bool, detail: str) -> None:
    SELFCHECK["ok"] = ok
    SELFCHECK["detail"] = detail
    if ok:
        logger.info("[larkdeck] 启动自检通过：%s", detail)
    else:
        # 自检失败必须响亮：否则用户会以为卡片在跑，实际还是内置纯文本。
        logger.error("[larkdeck] 启动自检失败：%s（卡片不会生效，飞书仍是纯文本）", detail)


# --------------------------------------------------------------------------- #
# 覆盖层
# --------------------------------------------------------------------------- #
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
            self._ld_state[message_id] = {"chat_id": chat_id, "t0": now, "last": now}

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
    def _ld_footer(cls, started: Optional[float] = None) -> Optional[str]:
        """页脚一行：``🤖 模型 · ctx 用量 · ⏱ 耗时``。

        数据全部来自官方钩子（见 :mod:`larkdeck.core.context`）：钩子还没触发时该段
        自然缺失，全缺就返回 ``None``（不渲染脚注元素）。**任何情况下不抛异常**
        —— 页脚是装饰，不能因为它把整张卡片搞坏。
        """
        try:
            if not _cfg("footer"):
                return None
            snap = _context.snapshot()
            duration = max(0.0, time.monotonic() - started) if started else None
            return _cards.footer_line(
                model=(snap.get("model_display") or "") if _cfg("show_model") else "",
                context=cls._ld_context_segment(snap),
                duration=duration,
            )
        except Exception:
            logger.debug("[larkdeck] 页脚渲染失败，跳过", exc_info=True)
            return None

    @classmethod
    def _ld_panel(cls, chat_id: str = "") -> Optional[Dict[str, Any]]:
        """底部折叠面板：推理过程 + 工具步骤（数据来自 :mod:`larkdeck.core.panel`）。

        ``chat_id`` 决定面板归属：由 ``pre_gateway_dispatch`` 观察到的
        ``chat_id -> session_id`` 映射给出**确定性**归属，并发会话不再串台。
        拿不到映射时（新会话首回合 / 老版本 Hermes）自动退回「最近活跃会话」的旧行为
        —— **归属失败绝不能导致面板不渲染**。

        没有数据（钩子未触发 / reasoning 未开启 / 面板关掉）就返回 ``None``，
        ``reply_card`` 会自然跳过这个元素。与页脚同理：**任何情况下不抛异常**，
        面板是装饰，不能因为它把整张卡片搞坏。
        """
        try:
            if not _cfg("unified_panel"):
                return None
            snap = _panel.snapshot(chat_id)
            if not snap:
                _log_empty_panel_once()
                return None
            steps = [
                _cards.tool_step(
                    str(t.get("name") or "tool"),
                    status=str(t.get("status") or "ok"),
                    duration_ms=t.get("duration_ms"),
                    preview=str(t.get("preview") or ""),
                )
                for t in (snap.get("tools") or [])
            ]
            return _cards.unified_panel(
                reasoning=str(snap.get("reasoning") or ""),
                rounds=snap.get("rounds") or [],
                tools=steps,
                expanded=_cfg("panel_expanded"),
                max_reasoning_chars=_cfg_int("max_reasoning_chars", _cards.MAX_REASONING_CHARS),
                max_tool_chars=_cfg_int("max_tool_result_chars", _cards.MAX_TOOL_RESULT_CHARS),
                max_steps=_cfg_int("max_panel_steps", _cards.MAX_PANEL_STEPS),
            )
        except Exception:
            logger.warning("[larkdeck] 面板渲染失败，跳过", exc_info=True)
            return None

    # ---------------------------------------------------------------- 卡片构造
    @classmethod
    def _ld_build_card(cls, content: str, *, streaming: bool,
                       panel: Optional[Dict[str, Any]],
                       footer: Optional[str]) -> Dict[str, Any]:
        """构造回复卡，超字节预算时**分级丢装饰**（面板 → 页脚 → 全摘）。

        为什么不截断正文：官方把 native 流式下的长度责任明确推给适配器，而官方
        ``send()`` 本身会分块 —— 正文过大时正确做法是让卡片发送失败、由核心的
        fail-open 链回落到官方分块（退化成多条纯文本，但**答案完整**）。
        静默截断会让用户以为模型就说了这么多。
        """
        card, tier = _cards.fit_reply_card(content, streaming=streaming,
                                           panel=panel, footer=footer)
        if tier != "ok":
            _log_degrade_once(tier)
        return card

    # ---------------------------------------------------------------- 发送原语
    async def _ld_send_card(self, chat_id: str, card: Dict[str, Any], *,
                            reply_to: Optional[str] = None,
                            metadata: Optional[Dict[str, Any]] = None) -> Any:
        """复用内置适配器的发送原语（含重试 / 限流 / token 处理）。"""
        response = await self._feishu_send_with_retry(
            chat_id=chat_id, msg_type="interactive",
            payload=json.dumps(card, ensure_ascii=False),
            reply_to=reply_to, metadata=metadata,
        )
        return self._finalize_send_result(response, "larkdeck card send failed")

    async def _ld_update_card(self, chat_id: str, message_id: str, card: Dict[str, Any]) -> Any:
        """把 ``interactive`` 卡片整卡替换。

        必须走 **patch** 接口：``message.update`` 只收文本/帖子，卡片会被飞书拒
        （``[230001] invalid msg_type``，三种卡片方言真机实测均如此）。
        """
        request = self._ld_build_patch_request(
            message_id=message_id, content=json.dumps(card, ensure_ascii=False),
        )
        response = await self._run_blocking(self._client.im.v1.message.patch, request)
        return self._finalize_send_result(response, "larkdeck card patch failed")

    def _ld_build_patch_request(self, *, message_id: str, content: str) -> Any:
        """构造 patch 请求对象（SDK 懒加载；测试替身可在实例上覆写本方法）。"""
        from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

        body = PatchMessageRequestBody.builder().content(content).build()
        return PatchMessageRequest.builder().message_id(message_id).request_body(body).build()

    # -------------------------------------------------------------------- send
    async def send(self, chat_id: str, content: str, reply_to: Optional[str] = None,
                   metadata: Optional[Dict[str, Any]] = None, **kwargs: Any):
        """把回复渲染成卡片；任何一步出问题都回落到内置的纯文本发送。"""
        fallback = lambda: super(LarkDeckMixin, self).send(  # noqa: E731
            chat_id, content, reply_to=reply_to, metadata=metadata, **kwargs,
        )
        if not _cfg("cards") or not getattr(self, "_client", None) or not content:
            return await fallback()
        try:
            # 首帧没有「已耗时」可言（这一帧就是起点），所以不带 ⏱；⏱ 由后续
            # edit_message 按 t0 计算。之前这里传的是 time.monotonic()，等于
            # 恒等于 0.0s —— 属于白占一个字段，顺手修掉。
            card = self._ld_build_card(content, streaming=False,
                                       panel=self._ld_panel(chat_id),
                                       footer=self._ld_footer())
            result = await self._ld_send_card(chat_id, card, reply_to=reply_to, metadata=metadata)
            if result is not None and getattr(result, "success", False):
                self._ld_track(getattr(result, "message_id", "") or "", chat_id)
                return result
            logger.warning("[larkdeck] 卡片发送未成功（%s），回落纯文本",
                           getattr(result, "error", "unknown"))
        except Exception as exc:  # 卡片是增强，绝不能因为卡片把消息弄丢
            logger.warning("[larkdeck] 卡片发送异常，回落纯文本: %s", exc, exc_info=True)
        return await fallback()

    # ------------------------------------------------------------ edit_message
    async def edit_message(self, chat_id: str, message_id: str, content: str, *,
                           finalize: bool = False):
        """流式更新：改写我们自己发出的卡片；别人的消息交回内置实现。"""
        state = self._ld_known(message_id)
        if state is None or not getattr(self, "_client", None):
            return await super().edit_message(chat_id, message_id, content, finalize=finalize)
        try:
            card = self._ld_build_card(
                content, streaming=not finalize,
                panel=self._ld_panel(chat_id),
                footer=self._ld_footer(state.get("t0")),
            )
            result = await self._ld_update_card(chat_id, message_id, card)
            if result is not None and getattr(result, "success", False):
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
        try:
            return await self._ld_stream_frame(
                text, finalize=finalize, chat_id=chat_id, reply_to=reply_to,
                turn_id=str(kwargs.get("turn_id") or ""),
            )
        except Exception:
            logger.warning("[larkdeck] native 流式帧异常，交由核心回落", exc_info=True)
            return False

    async def _ld_stream_frame(self, text: str, *, finalize: bool, chat_id: Optional[str],
                               reply_to: Optional[str], turn_id: str) -> bool:
        chat = str(chat_id or "").strip()
        if not chat or not getattr(self, "_client", None):
            return False
        key = f"{chat}:{turn_id}" if turn_id else chat
        state = self._ld_stream_get(key)
        now = time.monotonic()
        # 整帧原样渲染，**不做任何正文归档** —— 理由见文件顶部那段说明。
        # 简言之：核心的分隔符与模型自己写的 markdown 分隔线无法区分，猜错就会
        # 静默吞掉整条回答（且核心按完整帧文本判定已送达，不会补发）。
        display = text
        if state is None:
            if finalize:
                # 没有活跃流可收尾：交核心回落（send/edit 会正常发出）。
                return False
            card = self._ld_build_card(display, streaming=True,
                                       panel=self._ld_panel(chat),
                                       footer=self._ld_footer(now))
            result = await self._ld_send_card(chat, card, reply_to=reply_to)
            if result is None or not getattr(result, "success", False):
                return False
            message_id = getattr(result, "message_id", "") or ""
            if not message_id:
                return False
            self._ld_track(message_id, chat)
            self._ld_stream_put(key, {"message_id": message_id, "chat_id": chat,
                                      "t0": now, "last": text, "last_at": now})
            return True
        message_id = state["message_id"]
        if finalize:
            card = self._ld_build_card(display or " ", streaming=False,
                                       panel=self._ld_panel(chat),
                                       footer=self._ld_footer(state.get("t0")))
            result = await self._ld_update_card(chat, message_id, card)
            if result is None or not getattr(result, "success", False):
                return False
            self._ld_stream_pop(key)
            self._ld_forget(message_id)
            return True
        if text == state.get("last"):
            return True
        last_at = state.get("last_at")
        if (state.get("last") and isinstance(last_at, (int, float))
                and now - last_at < _STREAM_MIN_INTERVAL):
            return True  # 节流窗口内的中间帧：跳过，等下个 tick（首帧不节流）
        card = self._ld_build_card(display, streaming=True,
                                   panel=self._ld_panel(chat),
                                   footer=self._ld_footer(state.get("t0")))
        result = await self._ld_update_card(chat, message_id, card)
        if result is None or not getattr(result, "success", False):
            return False
        self._ld_stream_put(key, {**state, "last": text, "last_at": now})
        return True

    def _ld_stream_get(self, key: str) -> Optional[Dict[str, Any]]:
        with self._ld_lock:
            state = self._ld_streams.get(key)
            return dict(state) if state else None

    def _ld_stream_put(self, key: str, state: Dict[str, Any]) -> None:
        with self._ld_lock:
            if len(self._ld_streams) >= _MAX_STREAMS and key not in self._ld_streams:
                # ⚠️ 只淘汰**真正泄漏**的流，**绝不按「最近活动」淘汰**。
                # 理由：`last_at` 只在有正文帧时推进，而一个跑十分钟工具的回合期间
                # 只有 panel 在变、`_ld_stream_put` 根本不被调用 —— 那种流看起来
                # 「很陈旧」，实际正活跃。踢掉它，下一帧就会因为查不到状态而
                # **另发一张新卡**（重复卡 + 老卡永久停在流式态），正是这里要防的事。
                # 所以阈值取**小时级**，专门收核心没发 finalize 的泄漏回合。
                now = time.monotonic()
                stale = [
                    other for other, value in self._ld_streams.items()
                    if now - float(value.get("last_at") or value.get("t0") or 0.0)
                    > _STREAM_LEAK_SECONDS
                ]
                for other in stale[: max(1, _MAX_STREAMS // 4)]:
                    self._ld_streams.pop(other, None)
                if len(self._ld_streams) >= _MAX_STREAMS:
                    # 一个都没到泄漏阈值 = 真的并发了很多活跃回合。软超限：照常插入、
                    # 只告警（宁可多留状态，也不能踢活跃流造重复卡）；硬上限兜底内存。
                    if len(self._ld_streams) >= _MAX_STREAMS * _STREAM_HARD_CAP_FACTOR:
                        oldest = sorted(self._ld_streams.items(),
                                        key=lambda kv: kv[1].get("last_at",
                                                                 kv[1].get("t0", 0.0)))
                        for other, _ in oldest[: max(1, _MAX_STREAMS // 4)]:
                            self._ld_streams.pop(other, None)
                        logger.error("[larkdeck] 并发流已达硬上限 %d，被迫淘汰最旧的回合"
                                     "（可能有回合的卡片停在流式态）",
                                     _MAX_STREAMS * _STREAM_HARD_CAP_FACTOR)
                    else:
                        logger.warning("[larkdeck] 并发流超过软上限 %d 且无可回收的泄漏流"
                                       "（活跃回合不淘汰，仅告警）", _MAX_STREAMS)
            self._ld_streams[key] = state

    def _ld_stream_pop(self, key: str) -> None:
        with self._ld_lock:
            self._ld_streams.pop(key, None)

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
            card = _cards.clarify_card(question, list(choices), clarify_id=clarify_id,
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
            value = getattr(action, "value", {}) or {}
            if isinstance(value, dict) and value.get(ACTION_KEY) == ACTION_CLARIFY:
                return self._ld_handle_clarify_click(event=event, value=value)
        except Exception as exc:
            logger.warning("[larkdeck] 处理卡片点击时异常: %s", exc, exc_info=True)
        return self._ld_passthrough_click(data)

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

    def _ld_card_response_safe(self) -> Any:
        """构造「无卡片变更」的回调响应；连 ``_card_response`` 都缺时返回 None。"""
        build = getattr(self, "_card_response", None)
        if not callable(build):
            return None
        try:
            return build()
        except Exception:
            logger.debug("[larkdeck] _card_response 失败", exc_info=True)
            return None

    def _ld_handle_clarify_click(self, *, event: Any, value: Dict[str, Any]) -> Any:
        """把一次澄清点击变成 ``resolve_gateway_clarify`` 调用，并原地更新卡片。"""
        clarify_id = str(value.get("clarify_id") or "")
        answer = value.get("answer")
        if not clarify_id or answer is None:
            logger.warning("[larkdeck] 澄清点击缺少 clarify_id/answer，忽略")
            return self._ld_card_response_safe()

        operator = getattr(event, "operator", None)
        open_id = str(getattr(operator, "open_id", "") or "")
        if not self._is_interactive_operator_authorized(open_id):
            logger.warning("[larkdeck] 未授权的澄清点击 by %s", open_id or "<unknown>")
            return self._ld_card_response_safe()

        loop = self._loop
        if not self._loop_accepts_callbacks(loop):
            logger.warning("[larkdeck] 适配器 loop 未就绪，丢弃澄清点击")
            return self._ld_card_response_safe()

        is_other = answer == _cards.OTHER_VALUE
        question = str(value.get("question") or "")

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
            logger.warning("[larkdeck] 澄清提交未生效（clarify=%s）—— 该澄清可能已被处理或过期；"
                           "卡片保持原样，用户仍可重试", clarify_id)
            return self._ld_card_response_safe()

        if is_other:
            return self._ld_card_response_safe()

        user_name = self._get_cached_sender_name(open_id) or open_id or "?"
        return self._card_response(
            _cards.clarify_resolved_card(question=question, answer=str(answer), user_name=user_name)
        )


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #
_BASE_CLASSES: Dict[Any, type] = {}
_MERGED_CLASSES: Dict[type, type] = {}


def _log_probe_report(report: Dict[str, Any]) -> None:
    """把 ``compat.probe_report()`` 的能力快照打进日志（缺失项提级到 WARNING）。

    这是「只探测上报」里那个**上报** —— 没有它，CALLBACK 两组的探测只在单测里跑过，
    生产代码从不调用，等于什么都没上报。
    """
    missing_callback = list(report.get("missing_callback") or [])
    missing_optional = list(report.get("missing_optional") or [])
    detail = (f"{report.get('adapter_class')} · "
              f"缺可选 {missing_optional or '无'} · 缺点击回调 {missing_callback or '无'}")
    if missing_callback:
        logger.warning("[larkdeck] 能力探测：%s —— 澄清按钮会静默失灵（点下去没反应）", detail)
    else:
        logger.info("[larkdeck] 能力探测：%s", detail)


def merged_class(base_cls: type) -> type:
    """给 ``base_cls`` 叠一层 ``LarkDeckMixin``；按 base_cls 缓存，避免重复建类。"""
    merged = _MERGED_CLASSES.get(base_cls)
    if merged is None:
        merged = type(ADAPTER_CLASS_NAME, (LarkDeckMixin, base_cls), {"__module__": __name__})
        _MERGED_CLASSES[base_cls] = merged
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
    try:
        base_cls = _discover_base_class(base_factory, config)
    except Exception as exc:
        _remember_selfcheck(False, f"无法解析内置适配器类: {exc}")
        return base_factory(config)

    ok, missing = _compat.probe_adapter_class(base_cls)
    if not ok:
        _remember_selfcheck(False, "内置适配器缺少所需接口: " + ", ".join(missing))
        return base_factory(config)

    # 完整能力快照 —— 点击回调路径与可选接口**只有这里能看见**。
    # 缺了不阻断卡片，但必须在日志里留痕：这些名字官方一改，澄清按钮就静默失灵
    # （点下去没有任何反应，也不报错），没有别的地方会给出信号。
    _log_probe_report(_compat.probe_report(base_cls))

    try:
        adapter = merged_class(base_cls)(config)
        adapter._ld_setup()
    except Exception as exc:  # 卡片层构造失败绝不能让飞书起不来
        _remember_selfcheck(False, f"卡片层构造失败: {exc}")
        logger.error("[larkdeck] 卡片层构造失败，退回内置适配器: %s", exc, exc_info=True)
        return base_factory(config)

    logger.debug("[larkdeck] 已接管适配器: %s", [c.__name__ for c in type(adapter).__mro__[:3]])
    return adapter


def register(ctx: Any) -> None:
    """插件入口：抢占 ``feishu`` 平台名，并把卡片层叠到内置适配器上。"""
    try:
        from dataclasses import fields as _dc_fields

        from gateway.platform_registry import PlatformEntry, platform_registry
    except Exception as exc:  # pragma: no cover - 只可能在非 Hermes 环境触发
        _remember_selfcheck(False, f"无法导入 Hermes 平台注册表: {exc}")
        return

    # 0) 读官方插件配置（plugins.entries.larkdeck.settings.*）—— 环境变量仍优先。
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

    # 3) 透传内置 entry 的依赖探测 / 安装 / 校验字段，行为与内置完全一致。
    allowed = {f.name for f in _dc_fields(PlatformEntry)}
    passthrough: Dict[str, Any] = {}
    for key in ("validate_config", "required_env", "install_hint", "ensure_deps_fn",
                "setup_fn", "emoji", "allowed_users_env", "platform_hint"):
        if key not in allowed:
            continue
        value = getattr(builtin, key, None)
        if value in (None, [], ""):
            continue
        passthrough[key] = value

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

    # 4) 启动自检：确认解析出来的 feishu 工厂确实是我们这个。
    try:
        current = platform_registry.get(PLATFORM_NAME)
        ours = current is not None and current.adapter_factory is _factory
    except Exception:
        ours = False
    if ours:
        hooks_ok = [name for name, ok in HOOKS.items() if ok]
        detail = f"Hermes {_compat.hermes_version()} · feishu 平台已由 larkdeck 接管"
        detail += (f" · 钩子 {'/'.join(hooks_ok)}" if hooks_ok
                   else " · 未订阅到钩子（页脚缺模型/上下文用量）")
        _remember_selfcheck(True, detail)
    else:
        _remember_selfcheck(False, "注册表里 feishu 仍指向别处，卡片不会生效")
