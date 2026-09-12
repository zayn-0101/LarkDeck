"""LarkDeck —— 用「继承 + 覆盖」给内置飞书适配器换上卡片渲染。

架构（三句话）
--------------
1. Hermes 的 ``platform_registry`` 对同名平台是**最后写入者胜**。larkdeck 在内置
   ``feishu`` 平台注册之后再注册一次 ``feishu``，于是被解析到的就是我们的工厂。
2. 我们的工厂先调用**内置工厂**，拿到一个已完整初始化好的 ``FeishuAdapter`` 实例
   —— 鉴权、WebSocket、长连接守护、媒体、重试、限流全在里面 —— 然后把这个实例的
   ``__class__`` 换成 ``type("LarkDeckFeishuAdapter", (LarkDeckMixin, <内置类>), {})``。
   纯 Python 对象改 ``__class__`` 是合法操作，MRO 让我们的方法优先。
3. 于是只需要覆盖四处：``send`` / ``edit_message`` / ``send_clarify`` /
   ``_on_card_action_trigger``，其余全部继承。

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
from . import i18n as _i18n

logger = logging.getLogger("larkdeck")

PLUGIN_NAME = "larkdeck"
PLATFORM_NAME = "feishu"
LABEL = "Feishu / Lark — LarkDeck cards"
ADAPTER_CLASS_NAME = "LarkDeckFeishuAdapter"

#: 卡片按钮 value 里的动作键；只认自己这一个，其余一律回落给内置实现。
ACTION_KEY = "larkdeck_action"
ACTION_CLARIFY = "clarify"

#: 追踪中的卡片上限，防止长跑会话无限增长。
_MAX_TRACKED = 512

_DEFAULTS: Dict[str, Any] = {
    "cards": True,            # 用卡片渲染回复
    "clarify_cards": True,    # 澄清使用按钮卡
    "unified_panel": True,    # 推理 + 工具合并为底部一个可折叠面板
    "panel_min_seconds": 0.0, # 短于该耗时的回复不渲染面板
}
_CONFIG: Dict[str, Any] = dict(_DEFAULTS)

#: 启动自检结论，供日志 / doctor 查看。
SELFCHECK: Dict[str, Any] = {"ok": None, "detail": "not run"}


def configure(**kwargs: Any) -> None:
    """运行时覆盖配置（未知键忽略，不抛）。"""
    for key, value in kwargs.items():
        if key in _DEFAULTS:
            _CONFIG[key] = value


def _cfg(key: str) -> Any:
    env = os.environ.get("LARKDECK_" + key.upper())
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "off", "")
    return _CONFIG.get(key, _DEFAULTS.get(key))


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

    ``__slots__ = ()`` 是**必需**的，不是洁癖：``enrich()`` 用 ``__class__`` 赋值把实例
    升级成子类，而 CPython 要求两个类的内存布局完全一致。混入类若隐式带上自己的
    ``__dict__``/``__weakref__`` 槽，布局就会多出一块，赋值直接抛
    ``TypeError: object layout differs``。实例状态照常可用 —— ``__dict__`` 来自被叠加的
    内置类。
    """

    __slots__ = ()

    #: 末帧也要走 edit_message，而不是另发一条新消息 —— 卡片原地收尾的关键。
    REQUIRES_EDIT_FINALIZE = True

    # ------------------------------------------------------------------ 状态
    def _ld_setup(self) -> None:
        self._ld_state: Dict[str, Dict[str, Any]] = {}
        self._ld_lock = threading.Lock()

    def _ld_track(self, message_id: str, chat_id: str) -> None:
        if not message_id:
            return
        with self._ld_lock:
            if len(self._ld_state) >= _MAX_TRACKED:
                # 按开始时间淘汰最旧的一半，简单且够用。
                oldest = sorted(self._ld_state.items(), key=lambda kv: kv[1].get("t0", 0.0))
                for key, _ in oldest[: _MAX_TRACKED // 2]:
                    self._ld_state.pop(key, None)
            self._ld_state[message_id] = {"chat_id": chat_id, "t0": time.monotonic()}

    def _ld_known(self, message_id: str) -> Optional[Dict[str, Any]]:
        with self._ld_lock:
            entry = self._ld_state.get(message_id)
            return dict(entry) if entry else None

    def _ld_forget(self, message_id: str) -> None:
        with self._ld_lock:
            self._ld_state.pop(message_id, None)

    @staticmethod
    def _ld_footer(started: Optional[float]) -> Optional[str]:
        """页脚 —— 刻意用「符号 + 数字」，天然无需翻译。"""
        if not started:
            return None
        elapsed = max(0.0, time.monotonic() - started)
        return f"⏱ {elapsed:.1f}s"

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
        """复用内置适配器的编辑原语，把 ``interactive`` 卡片整卡替换。"""
        body = self._build_update_message_body(
            msg_type="interactive", content=json.dumps(card, ensure_ascii=False),
        )
        request = self._build_update_message_request(message_id=message_id, request_body=body)
        response = await self._run_blocking(self._client.im.v1.message.update, request)
        return self._finalize_send_result(response, "larkdeck card update failed")

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
            card = _cards.reply_card(content, streaming=False,
                                     footer=self._ld_footer(time.monotonic()))
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
            card = _cards.reply_card(
                content, streaming=not finalize,
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
            multi = False
            try:
                from tools import clarify_gateway as _cg
                with _cg._lock:
                    multi = bool(getattr(_cg._entries.get(clarify_id), "multi_select", False))
            except Exception:
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
        """
        try:
            event = getattr(data, "event", None)
            action = getattr(event, "action", None)
            value = getattr(action, "value", {}) or {}
            if isinstance(value, dict) and value.get(ACTION_KEY) == ACTION_CLARIFY:
                return self._ld_handle_clarify_click(event=event, value=value)
        except Exception as exc:
            logger.warning("[larkdeck] 处理卡片点击时异常: %s", exc, exc_info=True)
        return super()._on_card_action_trigger(data)

    def _ld_handle_clarify_click(self, *, event: Any, value: Dict[str, Any]) -> Any:
        """把一次澄清点击变成 ``resolve_gateway_clarify`` 调用，并原地更新卡片。"""
        clarify_id = str(value.get("clarify_id") or "")
        answer = value.get("answer")
        if not clarify_id or answer is None:
            logger.warning("[larkdeck] 澄清点击缺少 clarify_id/answer，忽略")
            return self._card_response()

        operator = getattr(event, "operator", None)
        open_id = str(getattr(operator, "open_id", "") or "")
        if not self._is_interactive_operator_authorized(open_id):
            logger.warning("[larkdeck] 未授权的澄清点击 by %s", open_id or "<unknown>")
            return self._card_response()

        loop = self._loop
        if not self._loop_accepts_callbacks(loop):
            logger.warning("[larkdeck] 适配器 loop 未就绪，丢弃澄清点击")
            return self._card_response()

        is_other = answer == _cards.OTHER_VALUE

        async def _job() -> None:
            try:
                from tools.clarify_gateway import mark_awaiting_text, resolve_gateway_clarify
                if is_other:
                    # 「其他」不提交答案，只把该 clarify 切成等待文字输入。
                    mark_awaiting_text(clarify_id)
                else:
                    resolve_gateway_clarify(clarify_id, str(answer))
            except Exception as exc:
                logger.error("[larkdeck] resolve_gateway_clarify 失败: %s", exc, exc_info=True)

        self._submit_on_loop(loop, _job())

        user_name = self._get_cached_sender_name(open_id) or open_id or "?"
        question = str(value.get("question") or "")
        if is_other:
            return self._card_response()
        return self._card_response(
            _cards.clarify_resolved_card(question=question, answer=str(answer), user_name=user_name)
        )


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #
_BASE_CLASSES: Dict[Any, type] = {}
_MERGED_CLASSES: Dict[type, type] = {}


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

    try:
        adapter = merged_class(base_cls)(config)
    except Exception as exc:  # 卡片层构造失败绝不能让飞书起不来
        _remember_selfcheck(False, f"卡片层构造失败: {exc}")
        logger.error("[larkdeck] 卡片层构造失败，退回内置适配器: %s", exc, exc_info=True)
        return base_factory(config)

    adapter._ld_setup()
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

    # 4) 启动自检：确认解析出来的 feishu 工厂确实是我们这个。
    try:
        current = platform_registry.get(PLATFORM_NAME)
        ours = current is not None and current.adapter_factory is _factory
    except Exception:
        ours = False
    if ours:
        _remember_selfcheck(
            True, f"Hermes {_compat.hermes_version()} · feishu 平台已由 larkdeck 接管",
        )
    else:
        _remember_selfcheck(False, "注册表里 feishu 仍指向别处，卡片不会生效")
