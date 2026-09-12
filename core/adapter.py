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

#: 为「中止重绘」保留的正文长度上限（字符）。只用于非 native 路径把卡片重绘成中止态；
#: 超长就不保留（那条路的中止色会缺失，但不值得为此长期驻留几十 KB 正文）。
_MAX_TRACKED_TEXT = 20000

#: native 流式：并发回合上限 + 帧节流窗口（秒）。帧率过高会触发飞书限流，
#: 窗口内的中间帧直接跳过（返回 True 但不下发；下个 tick 文本变了会重试）。
#:
#: ``_STREAM_MIN_INTERVAL = 0.25`` 是**贴着官方上限取的安全值**，别再往下调：
#: ``im.v1.message.patch`` 的官方频控是**单条消息 5 QPS（≥200ms/帧）**，0.25s = 4/s
#: 已经用掉 80%。核心那侧的上限另有一层（``stream_consumer.py`` 的 ``await
#: asyncio.sleep(0.05)`` 轮询 ≈ 20 帧/秒），所以**真正卡住刷新密度的是这个常量**。
#: 想更快就得换传输（CardKit 卡片实体，单卡 10 次/秒），而那要另做真机验证；
#: 硬调小只会制造 99991400 限频 —— 那正是「一帧失败 → 本回合 native 被停用 →
#: 退化成多条纯文本」的入口（现在有 ``_TRANSIENT_BACKOFF`` 兜一层，但兜不等于鼓励）。
_MAX_STREAMS = 64
_STREAM_MIN_INTERVAL = 0.25

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
#:   * ``300309`` / ``300317`` —— **未验证**：它们是 CardKit sequence 语境的码，
#:     本插件的 patch 路径不碰 CardKit，理论上不该出现；保留纯属防御。
_TRANSIENT_CODES = frozenset({230020, 99991400, 300309, 300317})

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
    "clarify_cards": True,    # 澄清使用交互卡
    # 澄清卡方言：1.0（按钮 + 顶层 value，真机已跑通，**默认**）/ 2.0（下拉 + 输入框 +
    # 组件级 behaviors，需真机点击确证后再翻默认；见 AGENTS.md 不变量 5）
    "clarify_dialect": "1.0",
    # 「处理中」表情反应：Hermes 会在用户消息上打一个 Typing 表情、处理完撤掉 ——
    # 在飞书上这就相当于「输入提示」。流式卡片本身已是即时反馈，aiduPOP 把「无输入提示」
    # 列进了即时响应的观感。**默认保持 Hermes 的行为**（true）：它自己也并没有真的关
    # （抑制 wrapper 被注释掉了），关掉纯属观感偏好 —— 想关就设 false。
    "reactions": True,
    "unified_panel": True,    # 推理 + 工具合并为底部一个可折叠面板
    "panel_expanded": False,  # 面板默认收起（展开态很占屏；aiduPOP 同为默认收起）
    "footer": True,           # 页脚：只放上下文用量（模型/耗时已并入面板标题行）
    "show_model": True,       # 面板标题行里显示模型名
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


def _log_degrade_once(tier: str, elements: int = 0) -> None:
    """卡片降载日志 —— **限流**：60 秒最多一条。

    降载是在每个流式帧上判定的，不限流的话超预算期间每秒会刷 4 条
    （本项目自己的约定是诊断日志必须限流，见 _log_empty_panel_once）。

    ``elements`` 是这张卡递归数出来的元素数（飞书硬上限 200，真机实测 202 就被
    ``230099/11310`` 拒）。必须打出来：光看档位名分不出降载是**字节**触发的还是
    **元素数**触发的，而两者的处置完全不同（后者要收轮数 / 步数，不是收长度）。
    """
    now = time.monotonic()
    if now - getattr(_log_degrade_once, "_at", 0.0) < 60.0:
        return
    _log_degrade_once._at = now
    logger.warning("[larkdeck] 卡片超限（元素 %d/200），降载档位=%s"
                   "（正文不截断；若仍发不出会回落官方分块）", elements, tier)


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

        有上限：超长正文直接不存（宁可中止时不变色，也不要把几十 KB 的正文长期驻留内存）。
        """
        body = str(text or "")
        if len(body) > _MAX_TRACKED_TEXT:
            body = ""
        with self._ld_lock:
            entry = self._ld_state.get(message_id)
            if isinstance(entry, dict):
                entry["last_text"] = body

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
    def _ld_footer(cls) -> Optional[str]:
        """页脚一行：只放**上下文用量**（``ctx 45.2k/200k · 23%``）。

        模型名与耗时已经搬进面板标题行（决策 D2：卡片级 header 去掉了，信息压进面板头），
        这里再写一遍就是同一屏里重复两行同样的信息。页脚留下的是面板头不显示的那一项。

        数据来自官方钩子（见 :mod:`larkdeck.core.context`）：钩子还没触发时该段自然缺失，
        全缺就返回 ``None``（不渲染脚注元素）。**任何情况下不抛异常** —— 页脚是装饰，
        不能因为它把整张卡片搞坏。
        """
        try:
            if not _cfg("footer"):
                return None
            return _cards.footer_line(context=cls._ld_context_segment())
        except Exception:
            logger.debug("[larkdeck] 页脚渲染失败，跳过", exc_info=True)
            return None

    @classmethod
    def _ld_panel_summary(cls, snap: Dict[str, Any], started: Optional[float]) -> str:
        """面板标题行：``🤖 模型 · 🧠 3 · 🔧 5 · ⏱ 12.3s``（决策 D2 的信息落点）。

        全是「符号 + 数字 + 英文缩写」，与页脚同理：天然无需翻译，也不该走 i18n。
        各段数据缺失时自然缺段；全缺时返回空串，调用方退回固定标题。
        """
        try:
            model = ""
            if _cfg("show_model"):
                model = str(_context.snapshot().get("model_display") or "")
            duration = None
            if started:
                duration = max(0.0, time.monotonic() - float(started))
            return _cards.footer_line(
                model=model,
                rounds=len(snap.get("rounds") or []),
                tools=len(snap.get("tools") or []),
                duration=duration,
            ) or ""
        except Exception:
            logger.debug("[larkdeck] 面板标题渲染失败，跳过", exc_info=True)
            return ""

    @classmethod
    def _ld_panel(cls, chat_id: str = "", started: Optional[float] = None
                  ) -> Optional[Dict[str, Any]]:
        """底部折叠面板：推理过程 + 工具步骤 + 状态色（数据来自 :mod:`larkdeck.core.panel`）。

        ``chat_id`` 决定面板归属：由 ``pre_gateway_dispatch`` 观察到的
        ``chat_id -> session_id`` 映射给出**确定性**归属，并发会话不再串台。
        拿不到映射时（新会话首回合 / 老版本 Hermes）自动退回「最近活跃会话」的旧行为
        —— **归属失败绝不能导致面板不渲染**。

        ``started`` 是本回合的起始时刻（用于面板标题里的耗时）；拿不到就不显示耗时。

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
                status=snap.get("status"),
                summary=cls._ld_panel_summary(snap, started),
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
            # 把元素数一起打出来：降载可能是**字节**触发的、也可能是**元素数**触发的
            # （飞书硬上限 200，真机实测 202 就被 230099/11310 拒），
            # 光看档位名分不出是哪一种，而两者的处置完全不同（后者要收轮数/步数）。
            _log_degrade_once(tier, _cards.count_elements(card))
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
        """把 ``interactive`` 卡片整卡替换（瞬态错误退避重试）。

        必须走 **patch** 接口：``message.update`` 只收文本/帖子，卡片会被飞书拒
        （``[230001] invalid msg_type``，三种卡片方言真机实测均如此）。

        ``_ld_send_card`` 复用的内置发送原语自带重试，**patch 这条没有** —— 而它的失败
        代价最高（整回合掉 native，见 ``_TRANSIENT_CODES`` 的说明）。所以这里补一层
        **只对瞬态码**的重试；非瞬态错误立刻返回，让内核按既有 fail-open 链回落。
        """
        content = json.dumps(card, ensure_ascii=False)
        result: Any = None
        for attempt in range(len(_TRANSIENT_BACKOFF) + 1):
            request = self._ld_build_patch_request(message_id=message_id, content=content)
            response = await self._run_blocking(self._client.im.v1.message.patch, request)
            result = self._finalize_send_result(response, "larkdeck card patch failed")
            if getattr(result, "success", False):
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
                message_id = getattr(result, "message_id", "") or ""
                self._ld_track(message_id, chat_id)
                self._ld_note_text(message_id, content)
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
                panel=self._ld_panel(chat_id, state.get("t0")),
                footer=self._ld_footer(),
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
        except Exception as exc:
            # 不能只打异常日志就 return False：核心同样会因为 False 停用本回合的 native，
            # 而「native 被停用 → 输出回落 send/edit（可能变成多条纯文本）」这条最强诊断
            # 会缺失。走 _ld_stream_fail 让「为什么掉 native」始终留痕（限流 30s 一条）。
            logger.warning("[larkdeck] native 流式帧异常，交由核心回落", exc_info=True)
            return self._ld_stream_fail(f"帧处理异常：{exc}")

    async def _ld_stream_frame(self, text: str, *, finalize: bool, chat_id: Optional[str],
                               reply_to: Optional[str], turn_id: str) -> bool:
        chat = str(chat_id or "").strip()
        if not chat or not getattr(self, "_client", None):
            return self._ld_stream_fail("没有 chat / SDK 客户端")
        key = f"{chat}:{turn_id}" if turn_id else chat
        state = self._ld_stream_get(key)
        now = time.monotonic()
        # 整帧原样渲染，**不做任何正文归档** —— 理由见文件顶部那段说明。
        # 简言之：核心的分隔符与模型自己写的 markdown 分隔线无法区分，猜错就会
        # 静默吞掉整条回答（且核心按完整帧文本判定已送达，不会补发）。
        display = text
        if state is None:
            if finalize:
                # 没有活跃流可收尾：交核心回落（send/edit 会正常发出）。这是**正常路径**
                # （native 没开、或本回合首帧就没建成卡），所以不告警。
                return False
            card = self._ld_build_card(display, streaming=True,
                                       panel=self._ld_panel(chat, now),
                                       footer=self._ld_footer())
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
                                      "frames": 0, "skipped": 0})
            return True
        message_id = state["message_id"]
        if finalize:
            card = self._ld_build_card(display or " ", streaming=False,
                                       panel=self._ld_panel(chat, state.get("t0")),
                                       footer=self._ld_footer())
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
            return True
        if text == state.get("last"):
            return True
        last_at = state.get("last_at")
        if (state.get("last") and isinstance(last_at, (int, float))
                and now - last_at < _STREAM_MIN_INTERVAL):
            # 节流窗口内的中间帧：跳过，等下个 tick（首帧不节流）
            self._ld_stream_put(key, {**state, "skipped": int(state.get("skipped") or 0) + 1})
            return True
        card = self._ld_build_card(display, streaming=True,
                                   panel=self._ld_panel(chat, state.get("t0")),
                                   footer=self._ld_footer())
        result = await self._ld_update_card(chat, message_id, card)
        if result is None or not getattr(result, "success", False):
            return self._ld_stream_fail(
                f"帧更新失败（{getattr(result, 'error', 'unknown')}）")
        self._ld_stream_put(key, {**state, "last": text, "last_at": now,
                                  "frames": int(state.get("frames") or 0) + 1})
        return True

    def _ld_stream_fail(self, reason: str) -> bool:
        """一帧失败：**必须留下日志**，然后返回 False 让内核回落。

        为什么不能只是 ``return False``：失败会让内核**关掉本回合的 native 流式**，
        之后输出改走 ``send()``（可能变成多条纯文本消息）—— 而这是**静默**的，
        用户在飞书那侧只会觉得「卡片怎么变成一条条消息了」。限流：同一进程 30 秒一条，
        既留痕又不刷屏。
        """
        now = time.monotonic()
        # 限流状态挂在**函数对象**上（不是 self）：本方法同名于类属性，裸名字在方法体里
        # 不在作用域内，必须经类名取 —— 写成 ``getattr(_ld_stream_fail, ...)`` 会
        # NameError（第一次跑就撞上了）。
        if now - getattr(LarkDeckMixin._ld_stream_fail, "_at", 0.0) >= 30.0:
            LarkDeckMixin._ld_stream_fail._at = now  # type: ignore[attr-defined]
            logger.warning("[larkdeck] native 流式帧失败（%s）—— 本回合 native 将被内核停用，"
                           "后续输出回落 send/edit（可能变成多条纯文本）", reason)
        return False

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
            logger.debug("[larkdeck] 中止：这个 chat 没有可重绘的卡（可能还没建卡）")
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
            if await self._ld_redraw_one_stopped(chat, message_id,
                                                str(state.get("last") or ""),
                                                state.get("t0")):
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

    async def _ld_redraw_one_stopped(self, chat: str, message_id: str, text: str,
                                     started: Any) -> bool:
        """把**一张**卡重绘成中止态。返回是否成功。"""
        try:
            # 面板是状态色**唯一**的载体，所以这里**强制**给一个 stopped 面板：
            # 只靠 `_ld_panel` 会踩到一个实测过的坑 —— 该回合还没有任何过程数据时
            # （模型还在思考、还没调工具），快照里什么都没有 ⇒ 面板为 None ⇒
            # 「状态改了、卡片没变、还不报错」。这正是本项目最怕的形态。
            panel = self._ld_panel(chat, started) or _cards.unified_panel(
                status=_panel.STATUS_STOPPED)
            card = self._ld_build_card(text or " ", streaming=False,
                                       panel=panel, footer=self._ld_footer())
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
            if isinstance(value, dict) and value.get(_cards.PROBE_VALUE_KEY):
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

    @staticmethod
    def _ld_build_clarify_card(question: str, choices: List[str], *, clarify_id: str,
                               session_key: str, multi: bool) -> Dict[str, Any]:
        """按 ``clarify_dialect`` 选澄清卡方言。

        默认 **1.0**：那是本插件真机跑通的路径（按钮 + 顶层 ``value``）。
        ``"2.0"`` 是决策门 D1 的路径 A（``select_static`` / ``multi_select_static`` /
        ``input`` + 组件级 ``behaviors``），在**真机点过一次**之前不翻默认值
        —— ``AGENTS.md`` 不变量 5 的纪律：卡片方言的结论只能靠真机实验。
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
            logger.warning("[larkdeck] 澄清点击缺少 clarify_id，忽略")
            return self._ld_card_response_safe()
        answer, mode = self._ld_clarify_answer(action, value)
        if mode == "none":
            logger.warning("[larkdeck] 澄清点击里既没有 answer 也没有 option/input_value，忽略")
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
        session_key = str(value.get("session_key") or "")

        if mode == "text":
            # 输入框的自由文本：用**核心自己的判据**解析（编号 / 标签 / 多选 / 无效选择），
            # 但**必须限定在这一张卡的 clarify_id 上** —— 核心给「用户直接打字回复」用的
            # 入口取的是该 session 最旧的待答澄清，同 session 两条待答时会答错问题
            # （2026-09-13 审计实测）。自己拼答案则会把「1,3」当成一个叫「1,3」的选项。
            outcome = _compat.clarify_text_answer(clarify_id, str(answer))
            if outcome != _compat.CLARIFY_TEXT_RESOLVED:
                logger.warning("[larkdeck] 澄清输入框的内容没有被接受（%s · session=%s）"
                               "—— 卡片保持原样，用户可重试", outcome, session_key[:8])
                return self._ld_card_response_safe()
            user_name = self._get_cached_sender_name(open_id) or open_id or "?"
            return self._card_response(
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
            logger.warning("[larkdeck] 澄清提交未生效（clarify=%s）—— 该澄清可能已被处理或过期；"
                           "卡片保持原样，用户仍可重试", clarify_id)
            return self._ld_card_response_safe()

        if is_other:
            return self._ld_card_response_safe()

        user_name = self._get_cached_sender_name(open_id) or open_id or "?"
        # 回填卡必须与待答卡同方言，否则飞书会**静默丢弃**这一帧（HFC 踩过）
        return self._card_response(
            self._ld_build_resolved_card(question=question, answer=answer, user_name=user_name)
        )


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #
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
