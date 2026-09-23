"""飞书卡片 JSON 构造 —— 纯函数、无网络、无 Hermes 依赖，便于单测。

两种方言，按「要不要服务端回调」分开用
--------------------------------------
飞书卡片有两套互不兼容的写法，混用会被直接拒绝：

* **legacy 1.0**：顶层 ``elements`` + ``{"tag": "action", "actions": [...]}`` 按钮行。
  按钮带 ``value``，点击经 WebSocket 送到 ``p2.card.action.trigger``。
  **需要服务端处理点击的卡片必须用这套。**
* **schema 2.0**：``config`` / ``header`` / ``body.elements``。
  提供 ``streaming_mode``（打字机流式）和 ``collapsible_panel``（可折叠面板）。

两种方言各自的点击怎么接
------------------------
**要接服务端点击的组件，必须在它所属的方言里用对应的声明**（``AGENTS.md`` 不变量 5）：

* **1.0**：``{"tag": "action", "actions": [...]}`` 按钮行 + 按钮**顶层** ``value``；
* **2.0**：**组件级** ``behaviors: [{"type": "callback", "value": {...}}]`` —— ``value``
  会**原样**成为 ``event.action.value``；组件可以是 ``button`` / ``select_static``
  （回调带 ``action.option``）/ ``multi_select_static``（带 ``action.options``）/
  ``input``（带 ``action.input_value``）。

在 2.0 卡里放 1.0 的 ``action`` 行、或只有顶层 ``value`` 的按钮 → 飞书**拒收**
（IM API ``230099``）。混用是**静默失灵**（点了没反应），所以这条纪律要守。

> **这里曾经写过相反的结论**（「2.0 的 ``behaviors`` 到不了 ``p2.card.action.trigger``，
> 所以澄清卡只能是 1.0」）—— 那是**误诊**：当时那张「2.0 澄清卡」实际用的是没有
> ``behaviors``、只有顶层 ``value`` 的 1.0 按钮，属于「2.0 卡里放 1.0 组件」这个病；
> 论据还抄自第三方插件的**代码注释**。已在 2026-09-12 更正（见 ``AGENTS.md`` 不变量 5）。

所以本模块导出两组构造函数：
  * :func:`clarify_card` / :func:`clarify_resolved_card` —— 1.0（**当前默认**，真机跑通）
  * :func:`clarify_card_2` / :func:`clarify_resolved_card_2` —— 2.0（由配置
    ``clarify_dialect`` 选择；真机点击确证后再翻默认）
  * :func:`reply_card` —— 2.0（流式 + 折叠面板；**不接点击**）

元素级方言差异（实测，别再踩）
------------------------------
``note`` 元素在 **2.0 卡里已被飞书废弃**：实测 ``im/v1/messages`` 返回
``230099 / ErrCode 200861 · "cards of schema V2 no longer support this capability"``，
而同样的 ``note`` 放在 1.0 卡里正常通过。2.0 想要小字脚注只能用 :func:`footnote`。
**结构正确 ≠ 飞书接受** —— 卡片合法性只有真发一次才知道，见 ``tests/probe_render.py``。

界面文案走 :mod:`larkdeck.core.i18n`，靠飞书原生 ``i18n_content`` 做双语。
"""

from __future__ import annotations

import re as _re

import json
import math
import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from . import i18n as _i18n

SCHEMA = "2.0"

#: 「其他（自己输入）」按钮的哨兵值；点它不提交答案，而是让网关等用户下一条文字。
OTHER_VALUE = "__larkdeck_other__"

#: **方言探针卡**专用键：值里带它的点击只会在日志里留一行载荷，不改任何状态。
#: 用途见 ``adapter._ld_log_probe_click`` 与 ``tests/probe_render.py`` 的探针卡。
PROBE_VALUE_KEY = "larkdeck_probe"


def is_probe_value(value: Any) -> bool:
    """这个点击载荷是不是**探针**的？—— 适配器与探针**共用这一条判据**。

    ⚠️ 为什么值得单独一个函数（2026-09-16 对抗审计实测，**真缺陷**）：
    适配器用的判据是 `isinstance(value, dict) and value.get(PROBE_VALUE_KEY)`（**真值性**），
    而探针的自检一度写的是 `PROBE_VALUE_KEY in value`（**键存在**）—— 两者**不等价**：
    `{"larkdeck_probe": False}` 会被自检放行，却被适配器判为「不是探针」⇒ **不打任何日志、
    静默交回内置实现**。而那一行日志是「2.0 `button` 能不能到服务端」**唯一**的凭据 ⇒
    用户一次**成功**的点击会被读成「飞书没投递」。
    ⇒ 判据只能有一处。探针的卡片形状与适配器的派发都调它（`PROBE_VALUE_KEY` 仍是它的数据来源），
    这样「自检放行的形状」与「派发认的形状」在构造上不可能分叉。
    """
    return isinstance(value, dict) and bool(value.get(PROBE_VALUE_KEY))



DEFAULT_TITLE = "Hermes"

#: 2.0 卡片在通知栏 / 会话列表里显示的一句话摘要上限，超长会被截断。
SUMMARY_MAX = 120

#: 推理文本 / 单条工具结果的字数上限 —— 超出的部分折叠成一行说明。
#: 默认值对齐 HFC 在真机上跑出来的经验值（推理 1200 / 工具结果 600），
#: 它那个量级是「能看清一步在干什么，又不至于把卡片撑成论文」。
MAX_REASONING_CHARS = 1200
MAX_TOOL_RESULT_CHARS = 600

#: 单轮推理至少渲染这么多字符才读得下去；同时它也是**渲染轮数的分母** ——
#: 面板最多渲染 ``max_reasoning_chars // _MIN_ROUND_CHARS`` 轮（见 :func:`unified_panel`）。
_MIN_ROUND_CHARS = 120

#: 统一面板最多保留多少条步骤；更早的收成一行计数。
MAX_PANEL_STEPS = 30

# --------------------------------------------------------------------------- #
# 状态色 —— **唯一载体是面板边框**
# --------------------------------------------------------------------------- #
#: 飞书卡片的颜色枚举（官方 resource/colors.md：14 个基础色名，无后缀 = -600）。
#: 这里只用其中四个，语义与 aiduPOP 对齐（它的源码是
#: ``"green" if not is_error and not is_aborted else ("red" if is_error else "yellow")``；
#: 注意它的 README 把红/黄写反了，以源码为准）。
BORDER_NEUTRAL = "grey"   # 进行中 / 还没有结论
BORDER_OK = "green"       # 完成
BORDER_ERROR = "red"      # 报错
BORDER_STOPPED = "yellow"  # 用户中止

#: 回合状态 → 边框色。键必须与 ``panel.STATUS_*`` 一致
#: （单测 ``test_status_colors_match_panel_constants`` 钉住这件事 —— 两边改名而另一边
#: 没改的后果是**边框永远灰色**，即状态色整条静默失效）。
STATUS_BORDERS: Dict[str, str] = {
    "ok": BORDER_OK,
    "error": BORDER_ERROR,
    "stopped": BORDER_STOPPED,
}


def border_for_status(status: Any) -> str:
    """状态 → 边框色；未知/未给一律中性灰（不猜）。"""
    return STATUS_BORDERS.get(str(status or ""), BORDER_NEUTRAL)


#: 状态 → 面板正文里的兜底文案键（面板没有别的内容时用）。
_STATUS_TEXT_KEYS: Dict[str, str] = {
    "ok": "panel.status_ok",
    "error": "panel.status_error",
    "stopped": "panel.status_stopped",
}

# --------------------------------------------------------------------------- #
# 客户端打字机（`streaming_config`）
# --------------------------------------------------------------------------- #
#: 流式卡片里的 ``streaming_config``：**客户端**逐字渲染的节奏。
#:
#: 关键事实（aiduPOP 与 hermes-fry-cards 两个独立项目取值完全一致，都是 15/1/fast）：
#: 流式模式下我们推的是**全文**，**平台自己算增量、逐字打出来** —— 所以这是纯客户端动画，
#: 与我们推帧的快慢无关。而 2026-09-13 真机实测：``message.patch`` 往返 ≈0.5s/帧
#: （`probe_render.py --rate-limit`，16 次连打零拒绝）⇒ **推帧侧最快也就 ~2 帧/秒**，
#: 想让观感顺滑，唯一有效的杠杆就是这个字段。
#:
#: 实测已确证「飞书接受它」：带它的卡在 create 与 patch 两条路径上都是 ``code=0``。
#:
#: ⚠️ **但「客户端到底打不打字」仍未确证，而且有反证倾向**（2026-09-13 追查）：
#:   * 官方文档只有一句「``streaming_mode`` 流式更新模式（配 ``streaming_config``）」，
#:     没有说清哪种写入 API 才触发动画；
#:   * **Hermes 上游自己有两个 PR 明确把「打字机」与 CardKit 流式接口绑在一起**
#:     （标题原文：*Card Kit streaming — typewriter-style output via Card Kit API*、
#:     *streaming cards for native typewriter effect using Feishu's CardKit streaming
#:     update API*）—— 这倾向于「``im.v1.message.patch`` 拿不到动画」；
#:   * 同类项目 lark-hls-v2 的注释也说**第一次推送必须用 ``card_element.content``**
#:     才有打字机，之后改 ``partial_update_element`` 是为了**避免**动画重放。
#: 反方向只有 HFC 归档代码的三处注释（patch + ``streaming_config``，作者相信它能打字，
#: 但那段代码已不在运行路径上）。
#:
#: 结论：这一条**只能靠肉眼定**，`tests/probe_render.py --typing` 就是为它准备的
#: （两张卡交替长大 12 秒，一眼看得出哪张在逐字）。带上它本身无害（飞书接受、
#: 收尾帧不带），所以默认开着；**确证无效就写 ``streaming_print_ms: 0`` 关掉，
#: 真要打字机则得换 CardKit 卡片实体传输**（那时才值得付那份复杂度）。
#:
#: ⚠️ **只在流式帧上带**（``streaming=True``）：收尾帧是 ``streaming_mode: false``，
#: 带上它可能让客户端把整段答案**再打一遍**，那是明确要避免的观感。
DEFAULT_PRINT_FREQUENCY_MS = 15


#: 逐字间隔的上限（毫秒）。超过就没有「打字机」可言了 —— 客户端永远追不上推送节奏，
#: 卡片看起来是「慢慢挤牙膏」（而且它是**客户端**动画，调大到几千毫秒不会省任何额度）。
PRINT_FREQUENCY_MAX_MS = 2000


def streaming_config(print_frequency_ms: Any = DEFAULT_PRINT_FREQUENCY_MS) -> Dict[str, Any]:
    """``streaming_config`` 节点：15ms/字、每步 1 字、``fast`` 策略（两个同类项目的取值）。

    间隔会夹到 ``[1, PRINT_FREQUENCY_MAX_MS]``：越界值一律退回默认，**不是**原样下发 ——
    配置写错不该让卡片变成挤牙膏（也不该让 ``int(inf)`` 抛出去把整张卡打回纯文本）。
    """
    try:
        value = int(print_frequency_ms)
    except (TypeError, ValueError, OverflowError):
        value = DEFAULT_PRINT_FREQUENCY_MS
    if not 1 <= value <= PRINT_FREQUENCY_MAX_MS:
        value = DEFAULT_PRINT_FREQUENCY_MS
    return {
        "print_frequency_ms": {"default": value},
        "print_step": {"default": 1},
        "print_strategy": "fast",
    }


#: 上下文进度条的格子数（8 格是 fry-cards 实测过的宽度，手机上不换行）。
CONTEXT_BAR_WIDTH = 8


# --------------------------------------------------------------------------- #
# 通用元素（两种方言都认）
# --------------------------------------------------------------------------- #
def md(content: str) -> Dict[str, Any]:
    """markdown 文本块。空内容给一个空格，避免卡片元素被判为空。"""
    return {"tag": "markdown", "content": content if content else " "}


# --------------------------------------------------------------------------- #
# P1b：CardKit 设备字号档位（AP 对齐的 PC/手机差异化字号）
# --------------------------------------------------------------------------- #
#: 支持的档位名。配置值不认识时**不猜**，整体退回 off（保持现状）。
TEXT_PROFILE_OFF = "off"
_TEXT_PROFILE_STYLES: Dict[str, Dict[str, Dict[str, str]]] = {
    # 正文：PC 小一档、手机大一档；面板/脚注保持 notation，不跟着正文放大。
    "mobile_friendly": {
        "body": {"default": "normal", "pc": "small", "mobile": "large"},
        "panel": {"default": "notation", "pc": "notation", "mobile": "notation"},
        "notice": {"default": "notation", "pc": "notation", "mobile": "notation"},
    },
    # 整体紧凑：正文 normal(14px)，面板/脚注 notation(12px)。
    # `x-small` 用于**工具细节行**（markdown / `plain_text` 两宿主）与 **Error/Result 块**：
    # 2026-09-23 真机对照确认前两者更小；Error 块原用 `div.text=lark_md` 宿主时客户端
    # 忽略 `text_size`，换成 `markdown` 宿主后 x-small 确认生效且代码栈可读。
    # ⚠️ 这是**字面量**：没有运行时自动回退；若以后某宿主改版后失效，必须改代码并重跑
    # 断言/变异/夹具。其它位置继续只用官方文档列出的 `normal` / `notation`。
    "compact": {
        "body": {"default": "normal", "pc": "normal", "mobile": "normal"},
        "panel": {"default": "notation", "pc": "notation", "mobile": "notation"},
        "notice": {"default": "notation", "pc": "notation", "mobile": "notation"},
    },
    # 整体放大：正文 large、面板 normal、脚注 notation（脚注保持小字）。
    "large": {
        "body": {"default": "large", "pc": "large", "mobile": "large"},
        "panel": {"default": "normal", "pc": "normal", "mobile": "normal"},
        "notice": {"default": "notation", "pc": "notation", "mobile": "notation"},
    },
}
#: token 名固定（同一张卡只启用一个档位），元素用 `text_size` 引用 token。
_TEXT_PROFILE_TOKENS = {
    "mobile_friendly": {"body": "ld_body", "panel": "ld_panel", "notice": "ld_notice"},
    "compact": {"body": "ld_body", "panel": "ld_panel", "notice": "ld_notice"},
    "large": {"body": "ld_body", "panel": "ld_panel", "notice": "ld_notice"},
}


def text_profile(profile: Any) -> Any:
    """把配置档位名解析成 ``(config.style.text_size 映射, 元素 token 映射)``。

    不认识的档位返回 ``({}, {})`` —— 调用方据此完全不动卡片（fail-open）。
    """
    name = str(profile or "").strip().lower()
    styles = _TEXT_PROFILE_STYLES.get(name)
    tokens = _TEXT_PROFILE_TOKENS.get(name)
    if not styles or not tokens:
        return {}, {}
    return ({tokens[role]: value for role, value in styles.items() if role in tokens}, tokens)


def apply_text_profile(card: Dict[str, Any], profile: Any) -> Dict[str, Any]:
    """在**建卡期**把设备字号写进 2.0 卡：``config.style.text_size`` + 元素引用 token。

    只改内容/样式，不做任何流式期结构写；因此不会破坏「建实体时结构定死」的不变量。
    markdown 元素按位置分角色：面板子元素 -> panel，footer 元素 -> notice，其余 -> body。
    """
    styles, tokens = text_profile(profile)
    if not styles:
        return card
    config = card.setdefault("config", {})
    if not isinstance(config, dict):
        return card
    if styles:
        config["style"] = {"text_size": styles}

    def _walk(node: Any, role: str) -> None:
        if isinstance(node, dict):
            if node.get("element_id") == CARDKIT_FOOTER_ID:
                # footer 建卡时自带 notation；档位要覆盖它，否则 ld_notice 永远不生效。
                node["text_size"] = tokens["notice"]
                return
            tag = node.get("tag")
            if tag == "collapsible_panel":
                header = node.get("header")
                if isinstance(header, dict):
                    _walk(header, "panel")
                for child in node.get("elements") or []:
                    _walk(child, "panel")
                return
            if tag in ("markdown", "lark_md"):
                if "text_size" not in node:
                    node["text_size"] = tokens[role]
                return
            if tag in ("note",):
                # 1.0 的 note 没有 text_size 字段；保持不动（字号档位主要面向 2.0 / CardKit）。
                return
            for value in node.values():
                _walk(value, role)
        elif isinstance(node, (list, tuple)):
            for item in node:
                _walk(item, role)

    _walk(card, "body")
    return card



def note(content: str) -> Dict[str, Any]:
    """**1.0 专属**小字脚注行 —— 放进 2.0 卡会被飞书拒（请用 :func:`footnote`）。"""
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": content}]}


def footnote(content: str) -> Dict[str, Any]:
    """**2.0 专属**小字脚注：``markdown`` + ``text_size: "notation"``。

    飞书已废掉 2.0 里的 ``note`` 元素，2.0 想要灰字小字只能走 markdown 的 text_size。
    """
    node: Dict[str, Any] = {"tag": "markdown", "content": content if content else " "}
    node["text_size"] = "notation"
    return node


def note_i18n(key: str, **fmt: Any) -> Dict[str, Any]:
    """双语脚注行。"""
    return {"tag": "note", "elements": [_i18n.i18n_text(key, **fmt)]}


def button(label: Union[str, Dict[str, Any]], value: Dict[str, Any],
           *, btype: str = "default") -> Dict[str, Any]:
    """按钮元素（1.0 方言里必须放进 :func:`action_row`）。

    ``label`` 可直接给 :func:`i18n.i18n_text` 的双语节点 —— 1.0 按钮的
    ``text`` 接受 ``i18n_content``，已由真机探针确证。
    """
    text = label if isinstance(label, dict) else {"tag": "plain_text", "content": label}
    return {
        "tag": "button",
        "type": btype,
        "text": text,
        "value": value,
    }


def action_row(buttons: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """1.0 的按钮行容器 —— 服务端回调的唯一可靠载体。"""
    return {"tag": "action", "actions": list(buttons)}


def collapsible(title_node: Dict[str, Any], elements: Sequence[Dict[str, Any]], *,
                expanded: bool = False, element_id: str = "auxiliary_timeline",
                border_color: str = BORDER_NEUTRAL,
                ) -> Dict[str, Any]:
    """可折叠面板（**2.0 专属**元素）。用于把推理过程 / 工具调用收进底部。

    ⚠️ ``header.title`` **必须**是 ``plain_text`` 节点。塞 ``lark_md`` / ``markdown``
    飞书不会报错，只会**悄悄把面板降级成普通行**——标题文字照常显示，但三角箭头
    没了、内容全部铺开，面板等于白做。这个坑静态检查抓不到，只有真机探针能抓。
    """
    panel: Dict[str, Any] = {
        "tag": "collapsible_panel",
        "expanded": bool(expanded),
        "header": {
            "title": title_node,
            "vertical_align": "center",
            # ⚠️ 箭头是**选配**的：不给 ``icon``，飞书面板就没有任何展开/收起控件 ——
            # 不报错、不提示，只是点不动，等于一个"永远打不开的抽屉"。
            # 官方示例 JSON 里是显式给 icon + icon_position + icon_expanded_angle 的。
            "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined",
                     "size": "16px 16px"},
            "icon_position": "right",
            "icon_expanded_angle": -180,
        },
        "border": {"color": str(border_color or BORDER_NEUTRAL),
                   "corner_radius": "8px"},
        "padding": "8px 8px 8px 8px",
        "elements": list(elements),
    }
    if element_id:
        panel["element_id"] = element_id
    return panel


# --------------------------------------------------------------------------- #
# 指标渲染：上下文用量 / 页脚（纯函数，可在单测里钉死每个边界）
# --------------------------------------------------------------------------- #
#: 进度条密度：空 → 半空 → 半实 → 实。用渐变而不是二值块，低占用时也能一眼看出趋势。
_BAR_DENSITY = ("░", "▒", "▓", "█")


def compact_tokens(value: Any) -> str:
    """``45200 -> "45.2k"``，``200000 -> "200k"``，``1000000 -> "1.0m"``。

    整数化后再压缩，避免 ``200.0k`` 这种别扭写法。非数字返回空串。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    n = int(value)
    if n < 0:
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}m".replace(".0m", "m")
    if n >= 1_000:
        return f"{n / 1_000:.1f}k".replace(".0k", "k")
    return str(n)


def progress_cells(pct: float, width: int = CONTEXT_BAR_WIDTH) -> str:
    """把百分比画成 ``width`` 格渐变条，如 ``██▓▒░░░░``。

    每格取它自身的填充比例（0..1）映射到四档密度，所以边界格是半实心而不是
    突然从满格跳到空格 —— 8 格也能看出 12% 和 25% 的差别。
    """
    width = max(1, int(width))
    filled = max(0.0, min(100.0, float(pct))) / 100.0 * width
    cells = []
    for index in range(width):
        frac = min(max(filled - index, 0.0), 1.0)
        cells.append(_BAR_DENSITY[min(3, int(frac * 4 + 1e-9))])
    return "".join(cells)


def context_indicator(used: Any, maximum: Any, *, style: str = "text") -> str:
    """页脚里的上下文用量片段；数据不全返回空串（调用方据此不渲染）。

    * ``text``（默认）—— ``ctx 45.2k/200k · 23%``
    * ``bar``         —— ``ctx [███▓▒░░░] 23%``
    * ``both``        —— ``ctx 45.2k/200k [███▓▒░░░] 23%``
    """
    if isinstance(used, bool) or not isinstance(used, int) or used <= 0:
        return ""
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum <= 0:
        return ""
    pct = min(100.0, used / maximum * 100.0)
    amount = f"{compact_tokens(used)}/{compact_tokens(maximum)}"
    if style == "bar":
        return f"ctx [{progress_cells(pct)}] {pct:.0f}%"
    if style == "both":
        return f"ctx {amount} [{progress_cells(pct)}] {pct:.0f}%"
    return f"ctx {amount} · {pct:.0f}%"


def format_elapsed(seconds: float) -> str:
    """``12.3s``；超过一分钟换成 ``2m05s``，避免出现 ``125.7s`` 这种要心算的读数。"""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


#: P2 主题名常量。完整主题表在下方；`footer_line` 的默认形参在定义期就需要这个名字，
#: 所以这里先给出常量（模块加载完成后表一定可见）。
THEME_NEUTRAL = "neutral"


def footer_line(*, duration: Optional[float] = None, model: str = "",
                tools: Optional[int] = None, rounds: Optional[int] = None,
                context: str = "", cache: Optional[float] = None,
                api: Optional[int] = None, ttfb: Optional[float] = None,
                status: str = "", theme: Any = THEME_NEUTRAL) -> Optional[str]:
    """「值 + 英文缩写」拼成的信息行 —— 天然无需翻译（不依赖 i18n）。

    调用点：**页脚**（``status + duration + model + context + R7 指标``，用户 2026-09-17 指定：
    状态在最前、模型名放页脚，参考 aiduPOP 的 ``已完成 · 1m 4s · ✳ model``）；
    ``rounds`` / ``tools`` 两段是历史遗留（面板标题行现在由 i18n 文案自己拼 ``💭``/``🛠️``）。

    ⚠️ **B1（用户 2026-09-22 拍板）：页脚去段前缀 emoji** ——
    ``⏱``/``🤖``/``⚡``/``🔁``/``🐢`` 全部改成纯文本（``12.3s`` / ``Sonnet`` / ``cache 75%`` /
    ``api 7`` / ``ttfb 0.4s``）。**只去「段前缀」**：状态词里的 ``✅``/``❌``/``⛔`` 是
    i18n 状态词的一部分（用户确认过的逐字表里有），不在这次口径内。
    ``🧠``/``🔧``（rounds/tools）也不动 —— 它们属于上面那条历史用法，且 ``_THEME_SYMBOLS``
    还要当 ``theme_name()`` 的白名单和 ``tool_step`` 的图标表用。

    各段之间用 ``·`` 分隔；一段都没有时返回 ``None``，调用方就不渲染。

    R7 新增的三段（**都由调用方按配置决定要不要传**，这里只负责渲染）：
      * ``cache`` —— 缓存命中率（百分比，来自 ``cache_read_tokens / prompt_tokens``）⇒ ``cache 75%``；
      * ``api``   —— 本回合 API 请求次数 ⇒ ``api 7``；
      * ``ttfb``  —— 首个流式分块的首字节延迟（**秒**）⇒ ``ttfb 0.4s``。

    ⚠️ **缺数据就少一段，绝不编 0**（`docs/lessons.md` 的口径病）。判据逐段不同，
    **不是**一条规则（R7 审计低-2 更正 —— 本 docstring 以前写成「三段都按 `is None` 判」，
    与实现不符）：
      * ``cache`` 按 **`is None`** 判：`0.0` 是「真的 0% 命中」⇒ 要显示 `cache 0%`；
      * ``api`` / ``ttfb`` 按 **正数** 判：`0` 是「无意义的读数」⇒ 不显示。理由：本函数只在
        渲染卡片时调用，那时**至少发生过一次 API 请求**（`api_call_count ≥ 1`），而 TTFB
        要 < 0.5ms 才会四舍五入成 `0.0s` —— 两者出现 0 都只可能是脏数据。
    """
    parts: List[str] = []
    syms = _THEME_SYMBOLS[theme_name(theme)]
    # 顺序 = 用户指定的页脚阅读顺序：状态 → 时长 → 模型 → 其余指标。
    if status:
        parts.append(str(status))
    if isinstance(duration, (int, float)) and duration >= 0.1:
        parts.append(format_elapsed(float(duration)))
    if model:
        parts.append(f"{model}")   # 非 str 的坏值也不抛（f-string 自己 str()）
    # 注意排除 bool：Python 里 isinstance(True, int) 为真，不排会拼出「🧠 True」。
    if isinstance(rounds, int) and not isinstance(rounds, bool) and rounds > 0:
        parts.append(f"{syms['rounds']} {rounds}")
    if isinstance(tools, int) and not isinstance(tools, bool) and tools > 0:
        parts.append(f"{syms['tools']} {tools}")
    if context:
        parts.append(context)
    if cache is not None and not isinstance(cache, bool):
        try:
            pct = max(0.0, min(100.0, float(cache)))
        except (TypeError, ValueError):
            pct = None
        if pct is not None:
            parts.append(f"cache {pct:.0f}%")
    if isinstance(api, int) and not isinstance(api, bool) and api > 0:
        parts.append(f"api {api}")
    if isinstance(ttfb, (int, float)) and not isinstance(ttfb, bool) and ttfb > 0:
        parts.append(f"ttfb {format_elapsed(float(ttfb))}")
    return " · ".join(parts) or None


#: R6a 卫生用到的正则：围栏、行内代码、H1–H3。
#:
#: ⚠️ 围栏**不再**是一个「```…```」的配对正则（那是 R6a 审计的中-2）：围栏的合法形状
#: 由**行首扫描**决定（见 :func:`_code_spans`），因为配对正则漏掉三种真实形状 ——
#: ① **未闭合**围栏（上游的 `ensure_closed_code_fences` 不是每条路都过：核心的「边界收尾」
#: 直接送出累积原文，`gateway/stream_consumer.py:484/486`）② 四反引号及以上的嵌套围栏
#: （CommonMark 合法，块内可以有 ```）③ `~~~` 围栏。三种都会让围栏里的 `#` / `**`
#: 被当成 markdown 改坏 —— 那是**代码内容**被篡改，用户先看到原文、收尾时突然变样。
_INLINE_CODE_RE = _re.compile(r"`[^`\n]*`")
_HEADING_RE = _re.compile(r"^(#{1,3})[ \t]+(.*)$", _re.M)
#: 标题体里出现「反引号 / 波浪线围栏标记」时不做加粗降级的检测（幂等性，见 :func:`_demote_headings`）。
_FENCE_ISH_RE = _re.compile(r"`|~~~")
#: 一行是不是「ATX 标题行」（`#{1,6}` + 空白）—— 用来判断剥掉尾随 `#` 之后会不会露出新标题。
_HEADING_START_RE = _re.compile(r"#{1,6}(?=[ \t]|$)")
#: 标题体是否**只由 markdown 标记与空白组成**（`'# # '` / `'# **'`）—— 这类标题不做任何改动。
_MARKER_ONLY_RE = _re.compile(r"[#* \t]+$")
#: CommonMark 的「闭合法标题」尾随序列（`# 标题 ####` 里 `####` 本来不显示）。
_HEADING_CLOSING_RE = _re.compile(r"\s+#+$")

#: 工具步骤状态符号（符号语言无关，无需 i18n；未知状态用「•」兜底）。
_TOOL_STATUS_MARKS = {"running": "⏳", "ok": "✅", "error": "❌", "blocked": "⛔",
                      "cancelled": "⛔", "canceled": "⛔", "timeout": "⏰", "skipped": "⏭"}

#: P2 主题层：只改「符号 + 文案」的观感，不碰卡片结构。
#: neutral = 原观感；ap_lite = 抽象 emoji（用户选定的默认风格）；ap_bubble = AP 泡波全量。
THEME_AP_LITE = "ap_lite"
THEME_AP_BUBBLE = "ap_bubble"
_THEME_SYMBOLS: Dict[str, Dict[str, str]] = {
    THEME_NEUTRAL: {"model": "🤖", "rounds": "🧠", "tools": "🔧", "duration": "⏱",
                    "cache": "⚡", "api": "🔁", "ttfb": "🐢"},
    THEME_AP_LITE: {"model": "🤖", "rounds": "🌊", "tools": "🧰", "duration": "⏱",
                    "cache": "⚡", "api": "🔁", "ttfb": "🐢"},
    THEME_AP_BUBBLE: {"model": "👸🏻", "rounds": "🌊", "tools": "🫧", "duration": "✨",
                      "cache": "⚡", "api": "🔁", "ttfb": "🐢"},
}
_TOOL_ICONS: Dict[str, Dict[str, str]] = {
    THEME_NEUTRAL: {},
    THEME_AP_LITE: {"read": "📖", "write": "✍️", "search": "🔍", "web": "🌐",
                    "terminal": "⌨️", "skill": "🧩", "agent": "🤝", "image": "🖼️",
                    "default": "🧰"},
    # AP 原版是人物 emoji；这一档是可选项，默认 ap_lite 不启用，避免「过度卡通」。
    THEME_AP_BUBBLE: {"read": "👩🏻‍🏫", "write": "👩🏻‍🎨", "search": "🕵🏻‍♀️", "web": "👩🏻‍🚀",
                      "terminal": "👩🏻‍💻", "skill": "🤹🏻‍♀️", "agent": "👷🏻‍♀️", "image": "🖼️",
                      "default": "👩🏻‍🔧"},
}
#: 工具类别识别表。**按 token 精确匹配，不做子串包含**（否则 `ls` 会命中 `false`、
#: `cat` 会命中 `catalog`）；同一类别里的多个 token 命中任意一个即可。
#: 顺序 = 优先级：更具体的类别放前面（`create_image` 必须归 image、不能被 write 的
#: `create` 抢走；`run_skill` 必须归 skill、不能被 terminal 的 `run` 抢走）。
#: `read` 必须在 `terminal` 前：真实工具 `read_terminal` / `close_terminal` 的语义是
#: 「读/关终端输出」，归 📖 比归 ⌨️ 更接近用户心智（审计 C1）。
_TOOL_CATEGORIES = (
    ("web", ("web", "http", "https", "fetch", "url", "browser")),
    ("search", ("search", "grep", "glob", "find")),
    ("image", ("image", "img", "photo", "picture")),
    ("skill", ("skill",)),
    ("agent", ("agent", "delegate", "subagent")),
    ("write", ("write", "edit", "patch", "save", "create")),
    ("read", ("read", "cat", "head", "tail", "open", "ls")),
    ("terminal", ("bash", "shell", "exec", "execute", "command", "terminal", "run")),
)


def theme_name(theme: Any) -> str:
    """配置主题名归一：认不出的值退回 neutral（不猜、不放大）。"""
    name = str(theme or "").strip().lower()
    return name if name in _THEME_SYMBOLS else THEME_NEUTRAL


def _tool_category(name: str) -> Optional[str]:
    """工具名 → 粗类别（web / search / image / skill / agent / write / read / terminal）。"""
    tokens = [tok for tok in _re.split(r"[^0-9a-z]+", str(name or "").lower()) if tok]
    for category, keys in _TOOL_CATEGORIES:
        if any(token in tokens for token in keys):
            return category
    return None


def _tool_icon(name: str, theme: str) -> str:
    """工具类别图标（neutral 没有图标，保持原观感）。返回带尾随空格或空串。"""
    icons = _TOOL_ICONS.get(theme_name(theme)) or {}
    if not icons:
        return ""
    # `read_file` → ["read", "file"]；非字母数字一律当分隔符。token 精确匹配而不是
    # 子串包含：`ls` 不该命中 `false`，`cat` 不该命中 `catalog`。
    category = _tool_category(name)
    if category:
        return f"{icons.get(category) or icons.get('default', '')} "
    return f"{icons.get('default', '')} " if icons.get("default") else ""


#: AP-lite 工具标签：把英文工具名换成用户能扫一眼看懂的动作词。
#: 参考 aiduPOP 展开面板的 `Load skill / Run command / Read config.yaml` 观感。
_TOOL_LABELS: Dict[str, str] = {
    "web": "Web search",
    "search": "Search",
    "image": "Image",
    "skill": "Load skill",
    "agent": "Run sub-agent",
    "write": "Write file",
    "read": "Read file",
    "terminal": "Run command",
}

#: 每个类别优先从参数里取哪个键做「细节行」；取不到再退回第一个非空标量。
_TOOL_DETAIL_KEYS: Dict[str, Tuple[str, ...]] = {
    "web": ("query", "url", "q"),
    "search": ("pattern", "query", "glob", "name"),
    "image": ("prompt", "path", "file"),
    "skill": ("skill", "skill_name", "name"),
    "agent": ("prompt", "task", "description", "name"),
    "write": ("path", "file", "file_path"),
    "read": ("path", "file", "file_path"),
    "terminal": ("command", "cmd"),
}


def _tool_label(name: str, theme: Any) -> str:
    """AP-lite 工具名 → 动作标签；认不出的工具退回可读的原名。

    ⚠️ 动作词固定英文（CLS 风格），与固定英文的状态词一致；markdown element.content
    无法承载 ``i18n_content``，所以这是**明确的 i18n 边界**，详见 AGENTS.md。
    """
    category = _tool_category(name)
    if category:
        return _TOOL_LABELS.get(category, "Tool")
    raw = str(name or "tool").replace("_", " ").replace("-", " ").strip()
    return " ".join(raw.split()) or "Tool"


#: 工具状态 → （状态文本, 飞书 ``<font color>`` 颜色）。
#: 用户 2026-09-17 点单的 CLS 观感：状态不再只是一个 emoji，而是**带颜色的词**
#: （``Running`` / ``Failed``），这样色盲用户也能读懂，且与工具面板里的标题同一行就能扫完。
#: 英文状态词对所有客户端一致（CLS 也是这么做的）。
#:
#: ⚠️ **必须与 ``cardview.ToolStepView.status_style`` 同表**（结构化车道用后者）——
#: ``tests/test_units.py`` 有逐键相等用例。C1 / 默认②（用户 2026-09-22 拍板）之后：
#: 运行中 = 蓝色 ``Running``（turquoise 在浅色主题下与绿色成功撞色）；**只有成功**换绿色 ``✓``；
#: 失败 / 超时 / 中止 / 跳过**保留词**（词能说清是哪一种，符号不能）。
_TOOL_STATUS_STYLES: Dict[str, Tuple[str, str]] = {
    "running": ("Running", "blue"),
    "ok": ("✓", "green"),
    "success": ("✓", "green"),
    "error": ("Failed", "red"),
    "blocked": ("Blocked", "red"),
    # Hermes 的中断 / 跳过工具用的是这些状态；不映射就会掉进 fallback
    # （必须存在，2026-09-17 审计 H1：旧写法 `.get()` 后直接解包，未知状态先 TypeError）。
    "cancelled": ("Cancelled", "grey"),
    "canceled": ("Cancelled", "grey"),
    "skipped": ("Skipped", "grey"),
    "timeout": ("Timed out", "red"),
}


def _format_tool_duration(duration_ms: Any) -> str:
    """工具耗时的人类格式（对齐 CLS 的 ``25 ms`` / ``1.2 s`` 观感）。

    < 1 秒显示毫秒（模型跑得快的工具人眼更直观），≥ 1 秒显示秒；超过一分钟才退回合时格式。
    非数字 / NaN / ±Inf / 非正数 / 超过 24h 都返回空串或 ``>24h``（缺数据就少一段，绝不编 0；
    也绝不让坏 hook 数据把面板整块带走 —— 2026-09-17 审计 M1）。
    """
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
        return ""
    try:
        ms = float(duration_ms)
    except (TypeError, ValueError, OverflowError):
        return ""
    if not math.isfinite(ms) or ms <= 0:
        return ""
    if ms < 1000:
        return f"{int(round(ms))} ms"
    seconds = ms / 1000.0
    if seconds < 60:
        return f"{seconds:.1f} s"
    if seconds >= 86400:
        return ">24h"
    return format_elapsed(seconds)


def _duration_seconds(duration_ms: Any) -> Optional[float]:
    """安全地把毫秒转成秒；坏值（bool/非数值/NaN/Inf/≤0）返回 None。"""
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, (int, float)):
        return None
    try:
        ms = float(duration_ms)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(ms) or ms <= 0:
        return None
    return ms / 1000.0


def _detail_safe(text: str) -> str:
    """细节行放进 ``<font>`` 之前的中和：尖括号换全角、反引号换单引号。

    不这么做的话，参数里的 ``</font>`` / ``<at>`` 会被飞书当 HTML 截断，
    细节行会突然变成正文（用户看到的是「灰色小字跑出来了」）。
    """
    # 只中和 `<`（它才可能开启 HTML 标签）；`>` 与 `&` 都保留 —— 终端里的
    # `2>/dev/null` / `&&` 换掉会让人误以为是另一个命令（可读性优先）。
    return " ".join(str(text or "").split()).replace("<", "‹").replace("`", "'")


def _unescape_json_fragment(value: str) -> str:
    r"""把截断 JSON 字符串片段里的转义还原成可读文本（单次扫描，有界）。

    非法 ``\uXXXX`` 保留原文、绝不崩；lone surrogate 转 U+FFFD，避免产出无法 UTF-8
    编码的字符绕过字节预算。
    """
    out: List[str] = []
    i = 0
    simple = {'"': '"', "\\": "\\", "/": "/", "n": " ", "t": " ", "r": " ",
              "b": " ", "f": " "}
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt == "u" and i + 5 < len(value):
                hex4 = value[i + 2:i + 6]
                if not _re.fullmatch(r"[0-9a-fA-F]{4}", hex4):
                    out.append("\\u")
                    i += 2
                    continue
                codepoint = int(hex4, 16)
                i += 6
                if (0xD800 <= codepoint <= 0xDBFF and i + 5 < len(value)
                        and value[i:i + 2] == "\\u"
                        and _re.fullmatch(r"[0-9a-fA-F]{4}", value[i + 2:i + 6])):
                    low = int(value[i + 2:i + 6], 16)
                    if 0xDC00 <= low <= 0xDFFF:
                        codepoint = 0x10000 + ((codepoint - 0xD800) << 10) + (low - 0xDC00)
                        i += 6
                if 0xD800 <= codepoint <= 0xDFFF:
                    codepoint = 0xFFFD
                out.append(chr(codepoint))
                continue
            if nxt == "u":
                # 不足 4 位的截断 \u：保留原始反斜杠，不能悄悄吞掉
                out.append("\\u")
                i += 2
                continue
            out.append(simple.get(nxt, nxt))
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)

def _preview_value(text: str, category: Optional[str]) -> Optional[str]:
    """从**被截断的** JSON 预览里摸出一个值（只用于展示，绝不回填原文）。

    :func:`panel._args_preview` 会把预览截到 80 字符，因此 write/terminal 这类大参数
    的预览通常**不是合法 JSON**。以前的做法是一律不显示细节 —— 干净，但用户看不到
    加载了哪个 skill / 跑了什么命令。这里做**有界**的 key 提取：只认单层的
    ``"key": "value"``，value 到字符串结束（截断处）为止；解不出来就返回 None。
    """
    keys = _TOOL_DETAIL_KEYS.get(category or "", ())
    # ⚠️ 只认「对象开头（可有 ``{``）或逗号之后」的键：在别的字符串值里搜到
    # ``"command": ...`` 不能当成本次工具的参数（2026-09-17 审计 M2 的第二个形状）。
    for key in keys:
        match = _re.search(
            r'(?:\A\{?\s*|,\s*)"%s"\s*:\s*"((?:\\.|[^"\\])*)' % _re.escape(key), text)
        if match:
            return _unescape_json_fragment(match.group(1))
    # 已知类别没命中自己的键：**不要**退回「第一个字符串值」——那会把其它参数
    # （例如 note）当成命令展示（审计 M2 的第二个形状）。只有未知类别才做这种兜底。
    if category in _TOOL_DETAIL_KEYS:
        return None
    match = _re.search(r'(?:\A\{?\s*|,\s*)"[^"]{1,40}"\s*:\s*"((?:\\.|[^"\\])*)', text)
    if match:
        return _unescape_json_fragment(match.group(1))
    return None


def _shell_summary(command: str) -> str:
    """终端命令做「首条 + 条数」摘要，避免把整串复合命令倒进卡里。"""
    parts = [part.strip() for part in _re.split(r"\s*(?:&&|\|\||;|\n)\s*", command) if part.strip()]
    if not parts:
        return command.strip()
    if len(parts) > 1:
        return f"{parts[0]} 等 {len(parts)} 条"
    return parts[0]


def _tool_detail(name: str, preview: str) -> str:
    """参数预览 → 一行人话细节（**绝不把 JSON 原文倒回卡上**）。

    预览是合法 JSON 时按类别取关键键；被上游 80 字符截断时退到
    :func:`_preview_value` 的有界提取；两者都拿不到就返回空串（少一段，不倾倒原文）。
    """
    text = str(preview or "").strip()
    if not text:
        return ""
    category = _tool_category(name)
    value: Any = None
    try:
        parsed = json.loads(text)
    except Exception:
        value = _preview_value(text, category)
        if value is None:
            return ""
    else:
        if isinstance(parsed, dict):
            for key in _TOOL_DETAIL_KEYS.get(category or "", ()):
                candidate = parsed.get(key)
                if (candidate is not None and not isinstance(candidate, (dict, list))
                        and str(candidate).strip()):
                    value = candidate
                    break
            if value is None and (category or "") not in _TOOL_DETAIL_KEYS:
                # 只有未知类别才允许「第一个标量」兜底；已知类别没有自己的键就直接不显示，
                # 否则会把 note/terminal_id 之类的无关参数当成命令/路径（审计 F1）。
                for candidate in parsed.values():
                    if (isinstance(candidate, (str, int, float))
                            and not isinstance(candidate, bool) and str(candidate).strip()):
                        value = candidate
                        break
        elif isinstance(parsed, (str, int, float)):
            value = parsed
        if value is None:
            return ""
    if isinstance(value, (dict, list)):
        # 不把嵌套 JSON 原文倒回卡上（2026-09-17 审计 L3）；拿不到可读标量就不显示细节。
        return ""
    detail = str(value)
    if category == "terminal":
        detail = _shell_summary(detail)
    detail = _detail_safe(detail)
    if len(detail) > 60:
        detail = detail[:59] + "…"
    return detail

#: `<font color>` 运行开关：v0.6.2 生产默认 true（官方 Card 2.0 文档确认语法，真机视觉待确认）；adapter 会按配置推送；
#: 直接调用 cards 的探针/测试若要看彩色，必须显式 `set_color_tags_enabled(True)`。
_COLOR_TAGS_ENABLED = False


def set_color_tags_enabled(enabled: bool) -> None:
    """全局开关（只影响后续渲染；测试与探针失败时使用）。"""
    global _COLOR_TAGS_ENABLED
    _COLOR_TAGS_ENABLED = bool(enabled)


def color_tags_enabled() -> bool:
    return _COLOR_TAGS_ENABLED


def _colorize(text: str, color: str) -> str:
    """带颜色的小段；开关关闭时返回纯文本（不留下半个标签）。"""
    if not _COLOR_TAGS_ENABLED:
        return text
    return f"<font color='{color}'>{text}</font>"


def tool_step(name: str, *, status: str = "ok", duration_ms: Any = None,
              preview: str = "", theme: Any = THEME_NEUTRAL) -> str:
    """一步工具调用的摘要，供 :func:`unified_panel` 的 ``tools`` 参数使用。

    * ``neutral``：保持旧观感 —— ``✅ read_file · 2.3s · `{"path": …}```。
    * ``ap_lite`` / ``ap_bubble``：按 AP 的可读性整理 —— 动作标签 + 时长 +
      一行细节（``✅ 📖 读取文件 (2.3s)`` + 下一行细节），细节只取参数里的关键值，
      **不把 JSON 原文倒回卡上**（用户截图里的主要杂乱来源）。
    """
    mark = _TOOL_STATUS_MARKS.get(str(status or ""), "•")
    icon = _tool_icon(name, theme)
    # neutral 保持旧行为（包括既有测试/探针的逐字输出）
    if theme_name(theme) == THEME_NEUTRAL:
        line = f"{mark} {icon}{name or 'tool'}"
        seconds = _duration_seconds(duration_ms)
        if seconds is not None:
            line += f" · {format_elapsed(max(0.1, seconds))}"
        if preview:
            safe_preview = str(preview).replace("`", "'")
            line += f" · `{safe_preview}`"
        return line
    # ap_lite / ap_bubble：CLS 风格 —— 图标 + 加粗动作名 + 耗时 + **带颜色的状态词**，
    # 细节另起一行、灰色小字（不是 JSON 预览，也不是反引号代码块）。
    status_text, color = _TOOL_STATUS_STYLES.get(
        str(status or ""), (str(status or "").strip().capitalize() or "Unknown", "grey"))
    line = f"{icon}**{_detail_safe(_tool_label(name, theme))}**"
    duration = _format_tool_duration(duration_ms)
    if duration:
        line += f" ({duration})"
    line += " · " + _colorize(_detail_safe(status_text), color)
    detail = _tool_detail(name, preview)
    if detail:
        line += "\n" + _colorize(f"↳ {detail}", "grey")
    return line


# --------------------------------------------------------------------------- #
# 溢出保护：任何一段用户不可控的长文本，进卡片前都必须过这一关
# --------------------------------------------------------------------------- #
def _positive(value: Any) -> bool:
    """配置值是否为正数（用于「0 = 关掉这个特性」的开关型配置，**不是**上限）。"""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return number == number and number not in (float("inf"), float("-inf")) and number > 0


def _cap(value: Any, default: int) -> int:
    """把配置来的上限归一到**正数**。

    ⚠️ ``<= 0`` **不能理解成「不设限」** —— 面板是收进卡片里的：不截断就会把整段推理
    （panel 的缓冲上限 262144 字符）塞进卡片，卡片 JSON 膨胀到几百 KB，飞书直接拒收，
    那个回合的卡片功能整块丢掉（按不变量会回落纯文本，消息不丢，但卡片没了）。
    `panel.py` 里最多还缓存 200 步工具。所以 0 / 负数 / 转不动一律退回默认值 ——
    这与 README 把这三个键描述成「上限」的自然预期一致。
    """
    try:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):  # NaN / ±Inf
            return default
        n = int(number)
    except (TypeError, ValueError, OverflowError):
        # OverflowError：int(float("inf")) 抛的是它，不是 ValueError。
        # 漏了会让 _ld_panel 静默整块丢面板（那里 catch 住后返回 None）。
        return default
    return n if n > 0 else default


def truncate(text: str, limit: int, *, key: str = "panel.overflow") -> str:
    """超长文本截断并补一行「已省略 N 字符」，而不是静默切掉。

    静默截断会让用户以为模型就说了这么多；补一行说明才知道下面还有内容。

    截断点按**优先级阶梯**选，目的是不切断 markdown 的块结构：

    1. 空行（``\\n\\n``，段落边界）—— 最优；
    2. 行首块级标记（``#`` / ``-`` / ``*`` / ``+`` / ``>`` / ``|`` / ```` ``` ````）；
    3. 任意换行；
    4. 空白（用 ``isspace()`` 而不是 ``" "`` —— CJK 文本里几乎不出现半角空格，
       原来的 ``" " in head[-80:]`` 对中文基本不生效，这也是更容易切进 emoji 中间的原因）；
    5. 实在没有就往回退到 ``limit``。

    每级都要求落在 ``limit`` 的后半段（``>= limit // 2``），否则宁可用上一级 ——
    避免为了对齐块边界而丢掉大半内容。

    截断点最后还要过一次字形回退（ZWJ 组合、变体选择符、组合记号、国旗对），
    免得把 👨‍👩‍👧 这类多码点字形切成两半。

    **只修围栏与行内反引号，绝不补 ``**``**：``**`` 在 markdown 里语义歧义（乘法、
    行内代码里的 ``**``、``*`` 与 ``**`` 混用都会让计数失真），补错的闭合符比不补更糟；
    核心自己给正文做同类处理时也只碰围栏与反引号。

    调用方必须先经 :func:`_cap` 归一 ``limit`` —— 这里 ``limit <= 0`` 保持「原样返回」
    的 fail-open 行为，只作为最后一道兜底，生产路径不会走到（:func:`unified_panel` 已归一）。
    """
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    # 切点可能被块级/字形回退拉到 limit 之前，所以丢弃量必须按**实际切点**算 ——
    # 用 len(text) - limit 会在回退时少报（实测实际丢 108、文案声称 68）。
    cut = _glyph_safe(text, _block_boundary(text, limit))
    head = _close_code_spans(text[:cut])
    return f"{head.rstrip()}\n> {_i18n.t(key, n=len(text) - cut)}"


#: 行首块级标记：截断点落在这类行之前，不会把块切成两半。
_BLOCK_START_MARKS = ("#", "-", "*", "+", ">", "|")

#: 字形回退最多回溯多少个码点。
_GLYPH_BACK_LIMIT = 8


def _block_boundary(text: str, limit: int) -> int:
    """在 ``limit`` 附近找一个**块级安全**的截断位置（返回下标）。"""
    window = text[:limit]
    floor = limit // 2
    pos = window.rfind("\n\n")
    if pos >= floor:
        return pos
    for index in range(len(window) - 1, floor - 1, -1):
        if window[index] != "\n":
            continue
        tail = window[index + 1:]
        if tail.startswith("```") or tail[:1] in _BLOCK_START_MARKS:
            return index
    pos = window.rfind("\n")
    if pos >= floor:
        return pos
    for index in range(len(window) - 1, floor - 1, -1):
        if window[index].isspace():
            return index
    return limit


def _glyph_safe(text: str, pos: int) -> int:
    """把截断点从**字形中间**挪出来，最多回溯 ``_GLYPH_BACK_LIMIT`` 个码点。

    ZWJ 序列（👨‍👩‍👧）、变体选择符（U+FE0E/FE0F）、组合记号都是「多个码点组成一个
    可见字形」，按码点硬切会渲染出两个 emoji 或豆腐块。国旗是两个 regional indicator
    组成一对，也不能切散。
    """
    backed = 0
    while pos > 0 and backed < _GLYPH_BACK_LIMIT:
        ch = text[pos - 1]
        if ch == "\u200d" or ch in ("\ufe0e", "\ufe0f") or unicodedata.combining(ch):
            pos -= 1
            backed += 1
            continue
        if ("\U0001F1E6" <= ch <= "\U0001F1FF" and pos >= 2
                and "\U0001F1E6" <= text[pos - 2] <= "\U0001F1FF"):
            pos -= 2
            backed += 2
            continue
        break
    return pos


def _close_code_spans(text: str) -> str:
    """闭合被截断打断的代码围栏与行内反引号；**不碰 ``**``**（语义歧义，见 docstring）。"""
    if not text:
        return text
    # 先剥掉完整的 ```…``` 区段，再数尾部未闭合的那个
    stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    if stripped.count("```") % 2:
        text += "\n```"
        stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    if stripped.count("`") % 2:
        text += "`"
    return text


# --------------------------------------------------------------------------- #
# 2.0：回复卡（流式 + 统一面板）
# --------------------------------------------------------------------------- #
def _summary_of(text: str, *, fallback: str = "") -> Dict[str, Any]:
    """2.0 卡片的 ``config.summary`` —— 官方 SDK 与 HFC 都强制带，漏了会出问题。

    ⚠️ 归一化后为空时**必须给兜底文案**：``{"content": ""}`` 等于通知栏空白，
    正是这个字段要防的那件事。而空文本在真实路径上会出现两次 —— native 流式的
    seed 帧（用空文本建卡），以及归档点落在末尾时的 finalize 帧
    （``_ld_stream_frame`` 里写的 ``display or " "``）。
    """
    flat = " ".join(str(text or "").split())
    return {"content": flat[:SUMMARY_MAX] or fallback}


def _fence_scan(text: str) -> List["tuple[int, int]"]:
    """**行首扫描**出行围栏区间（``\\`\\`\\``` / `~~~`，含未闭合的）。

    为什么要换成扫描而不是配对正则（R6a 审计中-2 的三条实测形状，全都能把代码内容改坏）：

      * **未闭合围栏必须延伸到文末**。上游的 `ensure_closed_code_fences` 只覆盖经
        `_send_or_edit` 的帧；核心的「边界收尾」（回合中途的澄清/审批边界）直接把
        ``self._accumulated`` 原样送出来（`gateway/stream_consumer.py:484/486`）。
        审计实测：`先看这段：\\n```python\\n# 磁盘检查\\ndf -h ** 2>/dev/null\\n` 在收尾帧里
        被改成 `**磁盘检查**` + `df -h  2>/dev/null` —— 用户先看到原文，收尾时突然变样。
      * **四反引号及以上的围栏**是 CommonMark 里嵌三反引号块的唯一合法写法
        （`\\`\\`\\`\\`` … `\\`\\`\\``\\n…\\n\\`\\`\\`` … `\\`\\`\\`\\``），配对正则只认三反引号 ⇒ 里层 `\\`\\`\\`` 与
        外层配成一对，块内容整段被当成正文。
      * **`~~~` 围栏**与反引号围栏同义，配对正则完全不认。

    闭合侧按 CommonMark 放宽：**同一字符、数量不少于开始标记**的行即为闭合
    （`\\`\\`\\`\\`` 块里裸的 `\\`\\`\\`` 行不算闭合）—— 宁可**多**认成一个围栏（少做卫生），
    也不要把围栏内容当正文改掉，两个方向的代价不对称。
    """
    spans: List["tuple[int, int]"] = []
    fence_char = ""
    fence_len = 0
    fence_start = 0
    pos = 0
    size = len(text)
    while pos < size:
        line_end = text.find("\n", pos)
        if line_end < 0:
            line_end = size
        if fence_char:
            stripped = text[pos:line_end].strip()
            if (stripped and stripped[0] == fence_char
                    and stripped.count(fence_char) == len(stripped)
                    and len(stripped) >= fence_len):
                fence_char = ""
                spans.append((fence_start, line_end))
            pos = line_end + 1
            continue
        line = text[pos:line_end]
        indent = 0
        while indent < len(line) and line[indent] == " " and indent < 4:
            indent += 1
        after = line[indent:]                 # 标记行去掉至多 3 个前导空格后的剩余部分
        if indent < 4 and after[:1] in ("`", "~"):
            char = after[0]
            run = 0
            while run < len(after) and after[run] == char:
                run += 1
            # ⚠️ 这里踩过一次，两件事必须分开：
            #   ① **开标记行的合法条件**是「信息串里不含反引号」（CommonMark），
            #      所以 `` ```python `` 是合法开围栏。第一版写成「去掉 `run` 之后什么都不许剩」，
            #      于是**所有带语言名的围栏都开不起来**：`_code_spans` 把 `` ```python ``
            #      当正文、把后面的 `` ``` `` 当成「未闭合围栏的开头」，整篇代码内容照样被改坏
            #      （实测 `'```python\ndf -h ** 2>/dev/null\n# 注释\n```'` ⇒ `# 注释` 变成 `**注释**`）。
            #   ② 判段必须从 `after[run:]` 起算：`line[indent + run:]` 会在 `run` 已自增后
            #      **多切一个字符**（`` ```python `` 被读成 `ython`）。同一类错。
            if run >= 3 and (char != "`" or "`" not in after[run:]):
                fence_char = char
                fence_len = run
                fence_start = pos
        pos = line_end + 1
    if fence_char:
        # 未闭合：**一直到文末**都算代码区
        spans.append((fence_start, size))
    return spans


def _code_spans(text: str) -> List["tuple[int, int]"]:
    """所有**代码区**（围栏 + 行内代码）的跨度，按起点排序、两两不重叠。

    为什么需要它（R6a）：围栏/行内代码里的 `**` 与 `#` **不是 markdown 语法**，
    对它们做卫生会把终端输出、代码块内容改坏 —— 那是不可接受的数据损坏。

    复杂度是**线性**的（R6a 审计低-5 实测旧实现 56KB 病态输入 5.29s）：行内代码这一步
    不再对每个围栏做一次 `any(...)` 线性查找，而是先把围栏区间的兜底判定压到一个
    **单调前移的指针**上（围栏区间已排序且互不重叠，指针只前进不回退）。
    """
    fences = _fence_scan(text)                # 从小到大、两两不重叠（逐行扫描的产物）
    where = 0
    spans: List["tuple[int, int]"] = []
    for match in _INLINE_CODE_RE.finditer(text):
        at = match.start()
        while where < len(fences) and fences[where][1] <= at:
            where += 1
        if where < len(fences) and fences[where][0] <= at < fences[where][1]:
            continue                          # 落在围栏里 ⇒ 不是行内代码
        spans.append((at, match.end()))
    merged = sorted(fences + spans)
    out: List["tuple[int, int]"] = []
    for start, end in merged:
        if out and start <= out[-1][1]:       # 相邻/重叠就并起来（`_outside_code` 要求不重叠）
            if end > out[-1][1]:
                out[-1] = (out[-1][0], end)
            continue
        out.append((start, end))
    return out


def _outside_code(text: str, spans) -> str:
    """把代码区抠掉后的文本（**只用于判断**，不用于输出）。"""
    out, pos = [], 0
    for start, end in spans:
        out.append(text[pos:start])
        pos = end
    out.append(text[pos:])
    return "".join(out)


def sanitize_markdown(text: str) -> str:
    """markdown 卫生（**纯函数**；只在**完整文本**的写入上用，绝不用在流式中间帧上）。

    为什么只能用在这些地方：它做的两件事（删游离 `**`、降级 H1–H3）都会**改写文本**，
    而流式帧的文本必须是**前缀链**（上游按「最后一次成功发出的帧文本是可见前缀」记账，
    见 `_ld_stream_frame` 的说明）——中间帧改写会让前缀链断掉，表现为「回答重发一遍」。
    所以判据是「这份文本是不是**完整文本**」，不是「这是哪条调用路径」（R6a 审计中-3）：

      * **可以做**：`send()`（整条消息）、`edit_message(finalize=True)`（收尾整卡）、
        `/stop` 的中止重绘（正文来自我们自己的追踪表，收尾帧写的就是同一段）、
        以及 native 收尾那一帧（`cardkit` / `patch` 两条传输都走它）；
      * **不许做**：`edit_message(finalize=False)` 与 CardKit 的**元素帧** —— 它们写的是
        累积帧的中间态，改一个字节就断前缀链。

    只做两件事（**不补围栏**：上游 `ensure_closed_code_fences` 已经补过，重复补是 R6a
    审计的纠正；而它覆盖不到的那条路 —— 核心的「边界收尾」直接把累积原文送出来 ——
    我们靠「未闭合围栏也算代码区」保护代码内容）：
      ① **删掉**代码区之外游离的那个 `**`；
      ② 代码区之外的 **H1–H3** 降级成加粗行。

    ⚠️ 三个坑（前两个原型实测、第三个审计实测，别重犯）：
      * **不能「补一个 `**` 收尾」**：补出来的收尾落在整段最后，会把它后面的**全部内容**
        吞进加粗（实测 `# 标题\n正文：**A、B\n## 建议` ⇒ `**建议****`，既难看又改语义）；
      * **顺序不能反**：必须先删游离 `**`、再降级标题 —— 降级会给标题行凭空加一对 `**`，
        把奇偶性搅乱，那时就再也分不出哪个是游离的了；
      * **删也要删对那个**：删除的判据见 :func:`_drop_unpaired_bold`（删**第一个**候选）。

    **幂等的适用范围**（R6a 审计中-4：原来那句「幂等（`f(f(x)) == f(x)`）」是**假的**，
    最小复现 `'## 用 ``` 开围栏\n## 建议\n```\ncode\n```\n'`）—— 修完之后用审计自己的
    两条语料脚本实测（`/tmp/audit-r6a/probe_sanitize.py` 与 `probe_nonidem.py`，
    只把 `sys.path` 指向本仓库）：

      * 块拼接穷举（13 个字母表项、长度 1–3）**2379 条 → 0 条非幂等**（修之前 4 条）；
      * 审计的现实感语料 6 万篇 → **0 条**（含「标题体里带 ``` 」的两种片段：修之前 **19.29%**）；
      * 随机 20 万条畸形输入（字母表含反引号/井号/波浪线）→ **1 条**。

    那 1 条的形状是「单独一个 `**` 夹在两对空行内代码之间」（`'aA \u200b``**``~'`）：
    两个 `**` 之间没有任何可读内容，**删哪个都不改变用户看到的字**，只是「代码区跨度」
    的划分变了（`` ```` `` 被并成一个跨度）。这条**不是**假的不变量，而是**写下来的已知边界**：
    单测把该形状列进显式清单（`_KNOWN_NONIDEMPOTENT_SHAPES`），其余语料一律要求幂等成立。
    """
    if not isinstance(text, str) or not text:
        return text
    return _demote_headings(_drop_unpaired_bold(text))


def _drop_unpaired_bold(text: str) -> str:
    """删掉代码区之外游离的那个 `**` —— **删第一个候选**（不补，理由见 :func:`sanitize_markdown`）。

    为什么是**第一个**而不是最后一个（R6a 审计中-1：删最后一个**比不改更糟**）：
    「模型开头漏了一个 `**`、后面又老老实实用了一对合法加粗」是最常见的形态，那时
    候选有三个（游离的开头、合法对的开关），删**最后一个** = 把**合法对的闭合标记**拆掉
    ⇒ 漏掉的那个 `**` 与合法对的**开**标记配成一对，中间整段正文被吞进加粗：

        in : '**磁盘占用前三：A、B\\n重点关注 **/var** 这个目录'
        删最后一个: '**磁盘占用前三：A、B\\n重点关注 **/var 这个目录'   ← 整段被吞进加粗
        删第一个  : '磁盘占用前三：A、B\\n重点关注 **/var** 这个目录'   ← 正是想要的结果

    奇偶性判据本身分不出候选，所以「删哪一个」是个**业务决定**，必须钉在测试里
    （三条候选的形状，变异 `R6a-9`）——审计实测：把这里改成「删最后一个」四门禁**全绿**，
    因为原来的用例在删之前只有一个候选。

    ⚠️ 诚实边界：多个游离标记同时存在时（例如 `'a **b **c **d'`）删第一个**不是**幂等的
    —— 第二遍又会出现新的「第一个」。这类输入本身没有唯一正确的 markdown 解释，
    所以这里只保证「真实形态（游离标记 + 后文合法加粗）删对那一个」，不保证病态长串。
    """
    spans = _code_spans(text)
    if _outside_code(text, spans).count("**") % 2 == 0:
        return text
    for index in range(0, len(text) - 1):
        if text.startswith("**", index) and not any(start <= index < end
                                                    for start, end in spans):
            return text[:index] + text[index + 2:]
    return text


def _demote_headings(text: str) -> str:
    """H1–H3 降级成加粗行 —— 只看**代码区之外**的行。

    四条与「正文内容一个字都不许吞」以及**幂等**有关的判据（R6a 审计低-3/低-4 + 中-4）：

      * **首尾只清 ASCII 空格/制表符**（`strip(" \\t")`，不是 `str.strip()`）——正文里的
        全角空格 `# 　标题` 是**正文内容**，吞掉就是改内容（审计实测 `'# \\u3000全角空格标题'`
        ⇒ `'**全角空格标题**'`）；
      * **剥掉 CommonMark 的「闭合法标题」尾随序列**再包加粗：`# 标题 ####` 里的 `####`
        本来**不显示**，不剥就变成加粗里肉眼可见的噪声（净增噪点）。只认「空白 + 若干 `#`
        + 行尾」这一种形状（`'\\s+#+$'` 要求 `#` 之前有空白），并且**剥完会露出另一个标题行
        时就不剥**（`'# # ```'` ⇒ `'# ```'` ⇒ `'```'` 那种连锁）；判据必须看**原始的标题体**
        而不是 strip 之后的结果（`'# # '` 剥完是 `'#'`，单看却不像标题 —— 按 strip 后的结果
        判的话穷举 92 条幂等反例一条都拦不住，实测）；
      * **标题体只有标记与空白时原样不动**（`'# # '`、`'# # **'`）—— 这类输入没有可读的
        标题正文，动一下的后果是**下一遍又动一次**；
      * **标题体里有反引号 / `~~~` 时不做加粗降级**（幂等的**适用范围**）：降级会给标题行
        凭空插一对 `**`，标题体里的反引号会让**下一遍扫描**的行内代码边界改变 ⇒ 刚插进去的
        `**` 被当成游离标记删掉。审计的最小复现（普通片段拼装下 **19.29%** 命中）：

            x       = '## 用 ``` 开围栏\\n## 建议\\n```\\ncode\\n```\\n'
            f(x)    = '**用 ``` 开围栏**\\n## 建议\\n```\\ncode\\n```\\n'
            f(f(x)) = '用 ``` 开围栏**\\n## 建议\\n```\\ncode\\n```\\n'  ← 开头的 `**` 没了

        ⚠️ 这类标题**连行首标记一起留着**（不是「去掉 `#` 直接送出去」）：去掉 `#` 之后
        `'# ```'` 会变成 `'```'` —— 那是一个**围栏开始标记**，第二遍扫描的代码区定义就变了
        （未闭合围栏一路吃到文末），于是 `f(f(x)) != f(x)`（`'# # ```'` 实测）。
        一个标题保住它的 `#` 总比让整段文本被重新分词好；这类标题本来就带围栏标记，
        加粗与降级对它都没有意义。

        这几条只**去掉行首标记、不插入任何字符**，是这类标题下最保守的动作
        （丢的是观感上的加粗，换来的是「幂等」在真实语料上成立：修完之后
        `probe_sanitize.py` 的块拼接穷举 2379 条 = **0 条非幂等**，含反引号标题的现实语料
        6 万篇 = **0 条**）。
    """
    spans = _code_spans(text)

    def _sub(match) -> str:
        if any(start <= match.start() < end for start, end in spans):
            return match.group(0)
        body = match.group(2).strip(" \t")
        if not _HEADING_START_RE.match(match.group(2)):
            body = _HEADING_CLOSING_RE.sub("", body)
        if not body or _MARKER_ONLY_RE.match(body):
            return match.group(0)
        if _FENCE_ISH_RE.search(body):
            # 这类标题里的围栏标记本身就说明这一行不是普通标题行 ⇒ **原样留着**
            # （理由见 docstring：去掉 `#` 会凭空造出一个围栏开始标记）
            return match.group(0)
        return f"**{body}**"
    return _HEADING_RE.sub(_sub, text)


def summary_text(text: str) -> str:
    """`config.summary` 的**内容**（会话列表里那行预览文字）—— 归一化 + 截断 + 空则给兜底。

    与 :func:`_summary_of` 同一套口径（同一个常量、同一段 flatten），只是把「文本」单独暴露出来：
    R7 的进展更新要**比较前后是否相同**（相同就不发那次写），拿字典比也行，但拿字符串更直白。
    """
    return _summary_of(text, fallback=DEFAULT_TITLE)["content"]


def card(*, elements: Sequence[Dict[str, Any]], template: str = "blue",
         title: Optional[str] = DEFAULT_TITLE, streaming: Optional[bool] = None,
         update_multi: bool = True, summary: str = "",
         print_frequency_ms: Any = DEFAULT_PRINT_FREQUENCY_MS) -> Dict[str, Any]:
    """2.0 卡片：``config`` / （可选）``header`` / ``body.elements``。

    ``title=None`` 表示**不要卡片级 header** —— 决策 D2 走这条路：模型名与统计搬进
    ``collapsible_panel`` 自己的 header，卡片只剩正文 + 面板（最接近 aiduPOP 的观感，
    也是「状态色放边框」这个选择的配套）。``header`` 是 2.0 的可选字段，不给完全合法。

    **不带按钮行** —— 需要接点击的卡片请用 :func:`legacy_card`。
    """
    config: Dict[str, Any] = {"wide_screen_mode": True}
    if update_multi:
        # 多端同步更新：所有客户端都看到同一份最新卡片。
        config["update_multi"] = True
    if streaming is not None:
        config["streaming_mode"] = bool(streaming)
    # 打字机只挂在**流式帧**上，理由见 DEFAULT_PRINT_FREQUENCY_MS 的说明
    if streaming and _positive(print_frequency_ms):
        config["streaming_config"] = streaming_config(print_frequency_ms)
    # 流式卡必须带 summary，否则通知栏空白、且部分场景会被拒。
    # 内容为空时（seed 帧 / 空 finalize 帧）退回卡片标题，不能留空串。
    if streaming or summary:
        config["summary"] = _summary_of(summary, fallback=str(title or DEFAULT_TITLE))
    node: Dict[str, Any] = {
        "schema": SCHEMA,
        "config": config,
        "body": {"elements": list(elements)},
    }
    if title:
        # ``title`` 可以是**字符串**，也可以是 :func:`i18n.i18n_text` 的双语节点。
        # ⚠️ 后者曾经被直接塞进 ``content`` 里，产出 ``{"content": {"tag": ...}}`` 这种
        # 嵌套结构 —— 飞书**拒收**（``230099 / ErrCode 200621 parse card json err``），
        # 而本地单测只核「有没有 header」看不出来。2026-09-13 由 `probe_render.py` 的
        # 真机探针抓到（⑬⑭ 两张真 2.0 澄清卡全被拒）。``legacy_card`` 一直是对的，
        # 这里补齐同样的处理。
        title_node = (title if isinstance(title, dict)
                      else {"tag": "plain_text", "content": str(title)})
        node["header"] = {"template": template, "title": title_node}
    return node


#: 「答案还没开始」时正文区的占位文案键（i18n）。理由见 :func:`answer_or_pending`。
_PENDING_TEXT_KEY = "stream.pending"


def answer_or_pending(answer: str, streaming: bool) -> str:
    """正文为空且**还在流式**时，给一个占位文案（空白卡看起来像卡住了）。

    aiduPOP 的「即时响应」（它的效果图 1）不只是「卡出现得早」，还包括**一眼看出它在干活**：
    它的卡片在等待期显示占位文案，正文一到就被替换。我们此前建卡后正文只有一个空格
    （:func:`md` 为了避免元素为空而补的），用户看到的是一张**近乎空白**的卡 ——
    效果 1a 的「即时」在观感上打了折扣。

    ⚠️ **收尾帧（``streaming=False``）绝不带占位**：否则一个真的没有正文的回合
    （被中止、或模型只输出了思考）会永远停在「正在生成…」，那是明确的错误信息。
    这也是为什么判据挂在 ``streaming`` 上，而不是「正文为空」上。
    """
    if streaming and not str(answer or "").strip():
        return _i18n.t(_PENDING_TEXT_KEY)
    return answer


def reply_card(answer: str, *, streaming: bool = False, panel: Optional[Dict[str, Any]] = None,
               footer: Optional[str] = None,
               print_frequency_ms: Any = DEFAULT_PRINT_FREQUENCY_MS) -> Dict[str, Any]:
    """正文回复卡：正文区 + 可选统一面板 + 可选脚注。

    **没有卡片级 header**（决策 D2）。原来标题固定是 "Hermes"，既没信息量又占一行，
    现在模型名/轮数/工具数/耗时全在面板头里。
    """
    elements: List[Dict[str, Any]] = [md(answer_or_pending(answer, streaming))]
    if panel:
        elements.append(panel)
    if footer:
        elements.append(footnote(footer))
    return card(elements=elements, title=None, streaming=streaming, summary=answer,
                print_frequency_ms=print_frequency_ms)


#: 飞书 Card 2.0 的**硬上限**：整张卡里（含嵌套）带 ``tag`` 键的对象总数 ≤ 200。
#: 超了不是「截断」，而是**整张卡完全不渲染**。
#:
#: **2026-09-13 真机实测（tests/probe_render.py --elements）**，不是抄来的。
#: 探针按 (196, 200, 201, 204) 四档递进、递归计数后实际发出的是同样这四个数，
#: 输出为：**196 ✅ · 200 ✅ · 201 ❌ · 204 ❌**（拒收时
#: ``code=230099`` · ``ext=ErrCode: 11310; ErrMsg: element exceeds the limit``）。
#: ⇒ 墙在 200/201 之间，官方口径的 200 成立。
#: （早先这里写过「198 收下 / 202 拒收」—— 那是另一次阶梯的输出，与当前探针口径对不上，
#: 已按可复现的数字改写。下一次飞书收紧上限时，用同一个阶梯就能复现新墙的位置。）
#: ``230099`` 是**不可重试**的确定性错误（同样内容重试必然同样失败），别塞进退避集合。
#:
#: ⚠️ 计数必须是**递归数所有含 tag 的对象**，不是数 ``body.elements`` 的长度：
#: 折叠面板里的每个 markdown 都算一个，只数顶层会低估几倍 —— 上面那两次实测用的
#: 就是「一个折叠面板塞 N 个子元素」的真实形状。
FEISHU_ELEMENT_LIMIT = 200

#: 给正文 / 脚注 / 收尾留的余量（收尾帧会补脚注，不能刚好卡在 200）。
_ELEMENT_LIMIT_RESERVE = 6

#: 面板**之外**那张卡的固定元素数（递归口径）：正文 markdown 1 + 面板本体 1 +
#: 面板标题 plain_text 1 + 面板标题的 icon 1 + 脚注 1 = 5。
#: 面板能放多少个子元素必须用它来算，**不能**用「上限减个大概」——
#: `panel_room` 与 `fit_reply_card` 的 `element_limit` 口径不一致时，会出现
#: 「面板自己以为放得下、降载阶梯却把整块面板摘掉」的 3~4 个元素的缝
#: （2026-09-13 审计实测：steps=190 → tier=no-panel、190 个元素的推理面板全没了）。
_CARD_FIXED_ELEMENTS = 5

#: 面板里可能出现的「…更早的 N 步已折叠」提示行，也要算进子元素预算。
_PANEL_HINT_ELEMENTS = 1

#: 面板**子元素**的可用额度：与 :func:`fit_reply_card` 的判据同源，保证「面板自认为
#: 放得下」⇒「降载阶梯也认」。留 1 个给折叠提示，最终整卡正好落在 element_limit 上。
_PANEL_CHILDREN_ROOM = (FEISHU_ELEMENT_LIMIT - _ELEMENT_LIMIT_RESERVE
                        - _CARD_FIXED_ELEMENTS - _PANEL_HINT_ELEMENTS)

#: 卡片 JSON 的 UTF-8 字节预算。飞书对 interactive 卡有大小上限，超了会被拒收。
#: **量纲必须是字节**：``ensure_ascii=False`` 下汉字占 3 字节，按字符估会低估 3 倍。
#:
#: ⚠️ 这个数字**仍是保守猜测，尚无权威依据** —— 审计核实过：Hermes 源码里没有任何
#: 卡片大小常量（唯一相关错误码 `230099` 的官方注释是「failed to create card content」，
#: 是**内容创建失败**的通用码，不是大小上限），官方飞书适配器也没有 native streaming
#: 可供参照（它只有纯文本的 `MAX_MESSAGE_LENGTH = 8000`）。
#: 取值权衡：**过小会在正常长度的回答上静默摘掉整个面板**（早先取 20000 时，
#: ~4600 汉字的正文就会把「推理+工具」面板整块摘掉，而此前那种卡片是能正常发出的），
#: 过大则会在飞书拒收时让每一帧都失败，而**一帧失败会永久关掉本回合的 native 流式**
#: （核心的行为，见 stream_consumer_transport）—— 那比丢面板更糟。
#: 所以先取一个「正常回答不动、超大才降载」的宽松值；`tests/probe_render.py`
#: 现在会实测并打印每张探针卡的字节数，用真机数据把它钉死。
CARD_BYTE_BUDGET = 40000

#: 飞书**实测**的卡片 JSON 硬上限（utf-8 字节）。2026-09-13 真机阶梯（`--bytes`）：
#: **128000 仍 `code=0`**（create 与 patch 都是），**160000 被拒**：
#: ``230025 The length of the message content reaches its limit``。
#: 所以真实上限落在 (128000, 160000] 之间，取 128000 是**保守**的那一侧。
#:
#: 它只在一处用得上：正文已经超过 :data:`CARD_BYTE_BUDGET` 时，我们**仍然要给状态色
#: 留一个落脚点**（见 :func:`status_shell`）—— 那一档不受软预算约束，只受这道硬墙约束。
#: `core/adapter.py` 的 `_MAX_TRACKED_TEXT` 也以它为准（「发得出去的卡就存得下正文」）。
FEISHU_CARD_BYTE_LIMIT = 128000


def code_spans(text: str):
    """**公开**入口：所有代码区（围栏 + 行内代码）的跨度，按起点排序。

    R6a 的 markdown 卫生用它避开代码区；R4 的**切卡点**也用它 —— 切在围栏中间会让两张卡
    各自的 markdown 残缺（前半段围栏没闭合、后半段凭空多出一段代码）。私有实现留在本模块，
    别处不要再抄一份正则。
    """
    return _code_spans(text)


def count_elements(node: Any) -> int:
    """递归数出卡片里**所有**带 ``tag`` 键的对象（嵌套面板的子元素全算）。

    见 :data:`FEISHU_ELEMENT_LIMIT`：这是飞书的硬墙，撞上不是截断而是整卡不渲染。
    """
    if isinstance(node, dict):
        total = 1 if "tag" in node else 0
        for value in node.values():
            total += count_elements(value)
        return total
    if isinstance(node, (list, tuple)):
        return sum(count_elements(item) for item in node)
    return 0


def card_bytes(node: Dict[str, Any]) -> int:
    """卡片 JSON 的 utf-8 字节数（发送时用的就是这份序列化）。"""
    try:
        return len(json.dumps(node, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return 0


def status_shell(panel: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """把完整面板缩成**只保留状态色**的最小面板（标题 + 一行状态文字，约 300 字节）。

    为什么必须存在这个东西（2026-09-13 真机实测，新加的 ``--stop-redraw`` 探针抓到的）：
    状态色（绿/红/黄）的载体**只有** ``collapsible_panel.border.color``，而正文一旦超过
    :data:`CARD_BYTE_BUDGET`，:func:`fit_reply_card` 的降载阶梯会把面板**整块**摘掉
    ⇒ 那张卡**没有任何颜色**。于是出现一个很荒谬的组合：``/stop`` 时我们确实发了
    ``message.patch``（code=0，第七路审计的阻断项修好了），可**载荷里根本没有颜色**
    —— 用户看到的还是「没变色」。换句话说：光把正文留住只解决了一半，
    另一半是**别把颜色的载体丢在降载里**。

    这一档的取舍：面板的数据（推理/工具/页脚）照旧不要，只留下「一个带颜色的框 +
    一行状态文字」。成本约 300 字节，而这时卡片通常已经 40KB 以上（软预算的 15% 都不到），
    相对飞书 128000 的硬上限可以忽略。
    """
    if not isinstance(panel, dict):
        return None
    border = panel.get("border")
    color = (border or {}).get("color") if isinstance(border, dict) else None
    if not color or color == BORDER_NEUTRAL:
        return None                      # 没有状态色就没有要保住的东西
    status = next((key for key, value in STATUS_BORDERS.items() if value == color), None)
    if status is None:
        return None
    header = panel.get("header") if isinstance(panel.get("header"), dict) else {}
    title = header.get("title")
    if not isinstance(title, dict):
        title = _i18n.i18n_text("panel.title")
    return collapsible(title, [md(_i18n.t(_STATUS_TEXT_KEYS.get(status, "panel.title")))],
                       expanded=False, border_color=color)


def fit_reply_card(answer: str, *, streaming: bool = False,
                   panel: Optional[Dict[str, Any]] = None, footer: Optional[str] = None,
                   budget: int = CARD_BYTE_BUDGET,
                   element_limit: int = FEISHU_ELEMENT_LIMIT - _ELEMENT_LIMIT_RESERVE,
                   print_frequency_ms: Any = DEFAULT_PRINT_FREQUENCY_MS,
                   ) -> "tuple[Dict[str, Any], str]":
    """构造回复卡，超预算时**分级丢装饰**。返回 ``(card, 降级档位)``。

    档位：``ok`` → ``no-panel`` → ``bare`` → ``over-budget``。
    ⚠️ ``over-budget`` 档**不是**裸卡：它会带一个只保状态色的小面板（:func:`status_shell`，
    除非加上它就越过飞书硬上限）。别在这里按「裸卡」假设写调用方逻辑。
    降载由**两道独立的墙**触发：字节预算（我们自己的保守值）与元素数硬上限（飞书 200）。
    两者任一超了就往下丢装饰 —— 元素那一侧撞上不是「卡片变小」而是**整张卡不渲染**。

    **刻意不截断正文。** 按审计核实：Hermes 官方明确把 native 流式下的长度责任推给适配器
    （``gateway/stream_consumer.py`` 的注释写着 "Native streaming bypasses this: the adapter
    truncates against the stream protocol's own limit"），而官方 ``send()`` 本身是**分块**的
    （``splits_long_messages``）。所以正文过长时正确的做法是**让它发失败**，由核心的
    fail-open 链回落到官方 ``send()`` → 分块发送（退化成多条纯文本，但**内容完整**）。
    静默截断答案会让用户以为模型就说了这么多 —— 这正是 ``docs/lessons.md`` 里最怕的失败模式。
    """
    attempts = ((panel, footer, "ok"),
                (None, footer, "no-panel"),
                (None, None, "bare"))
    for panel_try, footer_try, tier in attempts:
        node = reply_card(answer, streaming=streaming, panel=panel_try, footer=footer_try,
                          print_frequency_ms=print_frequency_ms)
        # 两道**独立**的墙：字节数（我们自己的保守预算）与元素数（飞书硬上限 200）。
        # 后者是 2026-09-13 真机量出来的（202 个元素 → 230099 / ErrCode 11310），
        # 撞上它不是「卡片变小」而是**整张卡不渲染**，所以必须一起判。
        if card_bytes(node) <= budget and count_elements(node) <= element_limit:
            return node, tier
    # 连装饰全摘都超预算：正文本身太大。**照常返回**，让发送失败去走官方回落
    # （官方会分块），而不是在这里把答案切掉。
    bare = reply_card(answer, streaming=streaming, print_frequency_ms=print_frequency_ms)
    # ……但**状态色必须留住**（这是 `status_shell` 的用途，理由见它的 docstring）。
    # 这一档故意**绕开软预算**（正文已经超了，再省那 300 字节没有意义），
    # 只守飞书的**实测硬上限**：两张卡都贴边时，多这 300 字节可能把一张能发的卡顶成
    # 「拒收 → 回落纯文本」，那就不划算。所以判据是「加完之后还在硬墙之内」。
    shell = status_shell(panel)
    if shell is not None:
        with_shell = reply_card(answer, streaming=streaming, panel=shell,
                                print_frequency_ms=print_frequency_ms)
        if card_bytes(with_shell) <= FEISHU_CARD_BYTE_LIMIT:
            return with_shell, "over-budget"
    return bare, "over-budget"


def _round_title(index: int, elapsed_ms: Any) -> str:
    """推理轮的标题行：``第 N 轮 · 6.2s``（耗时是**相对时长**，不是时刻）。

    脏值不参与运算；单值封顶 24h，避免 ``/1000.0`` 溢出把面板带走（Phase 1 审计 M-1）。
    """
    base = _i18n.t("panel.round_n", n=index)
    if isinstance(elapsed_ms, int) and not isinstance(elapsed_ms, bool) and elapsed_ms > 0:
        return f"{base} · {format_elapsed(max(0.1, min(elapsed_ms, 86_400_000) / 1000.0))}"
    return base


#: CardKit 实体卡的**固定元素 id**（结构在建实体时定死，之后只按 id 写内容）。
#: 见 docs/plan-6-effects.md「阶段 9」与 2026-09-13 的「重大更正」：**整卡替换**会关闭流式会话
#: （`card_element.patch/create/update` 与 `card.batch_update` **不受此限** —— 见 plan-v1 的 R2）
#: （真机实测 `300309`），所以结构不能边流边改。
CARDKIT_ANSWER_ID = "answer"
CARDKIT_PANEL_ID = "panel"
CARDKIT_PANEL_BODY_ID = "panel_body"
#: 面板的**第二块**：工具步骤列表（R3 收窄版）。
#: 为什么拆成两个元素：面板正文里的推理文本**逐字在长**（轮次标题的耗时每秒还在变），
#: 于是 `panel_body` 几乎每帧都要重写；而工具行**只在工具开始/结束时才变**。合成一个
#: markdown 时，那几十行工具摘要会**跟着推理一起每帧重发**（实测 ≈2.7KB/帧），客户端也要
#: 把整块重绘。拆开后两块走**同一次** `card.batch_update`（逻辑写次数一次都不增加），
#: 而「内容没变就不写」的去重让工具块只在工具事件那一帧才发。
CARDKIT_PANEL_TOOLS_ID = "panel_tools"
CARDKIT_FOOTER_ID = "footer"

#: 会被**流式写入**的元素 id（建实体时创建、之后按 id 写内容）。
#: adapter 从建出来的卡 JSON 里按这份清单抽元素表（结构单一来源），
#: 所以「加一个新的流式元素」= 在这里登记 + 在 `cardkit_entity_card` 里建出来，两处。
CARDKIT_STREAM_IDS = (CARDKIT_ANSWER_ID, CARDKIT_PANEL_BODY_ID,
                      CARDKIT_PANEL_TOOLS_ID, CARDKIT_FOOTER_ID)


def _rounds_elapsed_ms(rounds: Sequence[Dict[str, Any]]) -> int:
    """可见推理轮的总耗时（毫秒）；脏值 / 非正数直接跳过，不编 0。

    单值封顶 24h：坏 hook 里的 ``10**400`` 会让 ``/1000.0`` 抛 OverflowError，
    进而让整块面板静默消失（2026-09-17 审计 M3）。
    """
    total = 0
    for item in rounds:
        if not isinstance(item, dict):
            continue
        value = item.get("elapsed_ms")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            total += value
    return min(total, 86_400_000)


def _dense_lines(text: str) -> str:
    """面板文本排版收密：把连续空行压成一个换行（只用于面板，不改正文）。

    模型推理常带 ``\\n\\n`` 段落间距，飞书 markdown 会把这些空行原样渲染成
    「每行之间空一行」；面板是收纳过程信息，过疏反而难读。只压缩连续换行，
    不合并单换行，也不动代码块语义之外的字符。
    """
    s = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    return _re.sub(r"\n{2,}", "\n", s)


def _thinking_heading(rounds: Sequence[Dict[str, Any]], reasoning: str = "") -> str:
    """推理块的灰色小标题：``💭 思考 · 1.6s``（没耗时就写 ``💭 思考``）。"""
    if not rounds and not reasoning:
        return ""
    elapsed_ms = _rounds_elapsed_ms(rounds)
    key = "panel.sec_thinking" if elapsed_ms > 0 else "panel.sec_thinking_plain"
    label = (_i18n.t(key, elapsed=format_elapsed(elapsed_ms / 1000.0))
             if elapsed_ms > 0 else _i18n.t(key))
    return _colorize(label, "grey")


def _tools_heading(count: Any) -> str:
    """工具块的灰色小标题：``🛠️ 工具执行 · 3 步``（脏值安全：非 int / bool 都不显示）。"""
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        return ""
    key = "panel.sec_tools_one" if count == 1 else "panel.sec_tools"
    return _colorize(_i18n.t(key, n=count), "grey")


def panel_rounds_markdown(*, reasoning: str = "", rounds: Sequence[Dict[str, Any]] = (),
                          max_reasoning_chars: int = MAX_REASONING_CHARS,
                          include_text: bool = True) -> str:
    """面板里**推理轮**那一块的 markdown（R3 收窄版：这一块单独一个元素）。

    与 :func:`panel_markdown` 的推理段**逐字节同源**（后者现在就是把它和
    :func:`panel_tools_markdown` 拼起来），所以「拆成两个元素」不会改变用户看到的内容，
    只改变**载体与重发频率**。
    """
    max_reasoning_chars = _cap(max_reasoning_chars, MAX_REASONING_CHARS)
    lines: List[str] = []
    all_rounds = [item for item in rounds if isinstance(item, dict)]
    round_list = [item for item in all_rounds if str(item.get("text") or "")]
    # show_reasoning=false（``include_text=False``）⇒ **只留灰色摘要行**（时长来自所有轮），
    # 正文一个字都不进面板。摘要行必须留着：它是「推理被关掉」与「这一回合没推理」的区别所在。
    heading = _thinking_heading(round_list if include_text else all_rounds, reasoning)
    if heading:
        lines.append(heading)
    if include_text and round_list:
        keep = max(1, min(len(round_list), max_reasoning_chars // _MIN_ROUND_CHARS))
        share = max(_MIN_ROUND_CHARS, max_reasoning_chars // keep)
        dropped = len(round_list) - keep
        if dropped > 0:
            lines.append(_i18n.t("panel.rounds_trimmed", n=dropped))
        for index, item in enumerate(round_list[-keep:], start=dropped + 1):
            body = _dense_lines(truncate(str(item.get("text") or ""), share))
            lines.append(f"**{_round_title(index, item.get('elapsed_ms'))}**\n{body}")
    elif reasoning and include_text:
        lines.append(_dense_lines(truncate(reasoning, max_reasoning_chars)))
    return "\n".join(lines)


_FONT_TAG_RE = _re.compile(r"</?font[^>]*>", _re.IGNORECASE)


def _strip_font_tags(text: str) -> str:
    """去掉颜色标签，供小上限截断前的降级（避免切出半个 `<font>`）。"""
    return _FONT_TAG_RE.sub("", str(text or ""))


def panel_tools_markdown(*, tools: Sequence[str] = (),
                         max_tool_chars: int = MAX_TOOL_RESULT_CHARS,
                         max_steps: int = MAX_PANEL_STEPS) -> str:
    """面板里**工具步骤**那一块的 markdown（R3 收窄版：这一块单独一个元素）。

    裁减规则与 :func:`panel_markdown` 的工具段**逐字节同源**：超上限时保留**最近的**
    ``max_steps`` 步，并在前面加一行「已省略 N 步」的提示（`panel.trimmed`）——
    ⚠️ 提示在**前面**、保留的是**最近**的，这个方向不许改（改了就等于对用户说反话）。
    """
    max_tool_chars = _cap(max_tool_chars, MAX_TOOL_RESULT_CHARS)
    max_steps = _cap(max_steps, MAX_PANEL_STEPS)
    lines: List[str] = []
    steps = [str(item) for item in tools]
    heading = _tools_heading(len(steps))
    if heading:
        lines.append(heading)
    if steps and len(steps) > max_steps:
        lines.append(_i18n.t("panel.trimmed", n=len(steps) - max_steps))
        steps = steps[-max_steps:]
    for item in steps:
        # 小 max_tool_result_chars 下先剥标签再截断：宁可丢颜色，也不留下半个 <font>
        if "<font" in item and len(item) > max_tool_chars:
            item = _strip_font_tags(item)
        lines.append(truncate(item, max_tool_chars))
    return "\n".join(lines)


def panel_markdown(*, reasoning: str = "", rounds: Sequence[Dict[str, Any]] = (),
                   tools: Sequence[str] = (),
                   max_reasoning_chars: int = MAX_REASONING_CHARS,
                   max_tool_chars: int = MAX_TOOL_RESULT_CHARS,
                   max_steps: int = MAX_PANEL_STEPS,
                   include_reasoning_text: bool = True) -> str:
    """面板内容的 **markdown 文本**（**普通卡**与 `patch` 传输用，一个元素装全部）。

    为什么需要它：CardKit 只能按 ``element_id`` 往元素里**写文本**，不能边流边改结构，
    所以那个「折叠面板」里的内容必须是一个 markdown 子元素的字符串。这里复用与
    :func:`unified_panel` 同样的截断/收轮规则（`_cap` + `truncate` + `_round_title`），
    保证两条路径**看到的内容一致**，只是载体不同（多个元素 vs 一个 markdown）。

    ⚠️ **实体卡（CardKit）不再用这个函数**（R3 收窄版起）：它拆成 `panel_body`
    （推理，:func:`panel_rounds_markdown`）+ `panel_tools`（工具行，:func:`panel_tools_markdown`）
    两个元素（`adapter._ld_panel_parts`）。

    ⚠️⚠️ **它现在在生产里没有调用方**（2026-09-14 R3 代码审计实测）：普通卡 / `patch` 传输 /
    `/stop` 重绘 / 降级 / 收尾的面板**全部**走 :func:`unified_panel`（它有自己的一套截断逻辑），
    而实体卡走 `_ld_panel_parts`。本函数目前只被**单测与探针**使用 —— 所以：
      * **别**把它当成「几条车道共用的面板渲染」来推理（旧版注释就是这么写错的）；
      * 改面板渲染规则时，**真正要改的是 `_ld_panel_parts` 与 `unified_panel` 两处**；
      * 想删掉它得先把单测/探针的引用一起清掉（现在留着是为了避免一次「顺手删」把
        两条路径的输出对比丢掉）。
    """
    parts = [part for part in (
        panel_rounds_markdown(reasoning=reasoning, rounds=rounds,
                              max_reasoning_chars=max_reasoning_chars,
                              include_text=include_reasoning_text),
        panel_tools_markdown(tools=tools, max_tool_chars=max_tool_chars,
                             max_steps=max_steps),
    ) if part]
    return "\n".join(parts)


def cardkit_entity_card(answer: str, panel_text: str, *, streaming: bool = True,
                        status: Any = None, expanded: bool = False,
                        panel: bool = True, footer_text: Optional[str] = None,
                        panel_tools_text: Optional[str] = None) -> Dict[str, Any]:
    """**CardKit 实体卡**的 JSON（结构固定：正文元素 + 折叠面板 + 面板里的 markdown + 页脚）。

    结构固定是有原因的（真机实测）：`card_element.content` 只能按 id 写内容；而
    `message.patch` / `card.update` 这类**整卡替换会关闭流式会话**（再写元素得 `300309`）——
    注意**元素级**接口（`card_element.patch`/`create`/`update`、`card.batch_update`）不受此限。
    所以：流式期间只写这几个元素的**内容**（装饰合并成一次 `card.batch_update`），
    收尾才用 patch 整卡替换（那一刻流式本来也结束了）。

    元素清单（R2 起）——**建实体时定死，之后只能按 id 写内容**：
      * `answer`（正文，必在）；
      * `panel`（可折叠面板；`panel=False` 时整个不进卡）+ 面板里的**两个** markdown：
        `panel_body`（推理轮，R3 收窄版）与 `panel_tools`（工具行列表）。两个都在这一刻建出来
        —— **流式期间只改内容、不做结构性写入**；
      * `footer`（`footer_text is None` 时不进卡；开着但这一刻没数据 ⇒ 空串占位，元素留着等后续帧写）
        —— **流式期间页脚就能更新**，这是 R2 的用户可见收益。

    面板的边框色按状态给（**状态色载体也在这一刻进卡**，灰边起步；收尾帧的整卡替换才上色）。

    ``panel=False``（对应 ``unified_panel: false``）时**整个面板元素不进卡**。判据是配置而不是
    ``panel_text`` 空不空：面板内容为空是**正常中间态**（还没有工具/推理数据），那一刻的元素
    必须留着、等后面的帧往里写；而关掉面板的人不该在流式期间一直看着一个空面板头。

    ``footer_text is None``（对应 ``footer: false``）时**页脚元素不进卡**。注意与面板同一条纪律：
    判据是「要不要这个元素」，不是「这一帧有没有内容」—— 页脚在流式早期通常还没数据
    （钩子还没触发），但元素必须先在卡里，之后才写得进去。
    ⚠️ 调用方必须记住这个结构（元素 id 不在卡里时写它会得 `300313`）；元素表由
    ``adapter._ck_elems_from_card()`` **从这张卡里抽**，所以两处不会分叉。
    """
    elements: List[Dict[str, Any]] = [
        {"tag": "markdown", "element_id": CARDKIT_ANSWER_ID,
         "content": answer_or_pending(answer, streaming)},
    ]
    if panel:
        elements.append(
            {"tag": "collapsible_panel", "element_id": CARDKIT_PANEL_ID,
             "expanded": bool(expanded),
             "header": {"title": _i18n.i18n_text("panel.title"),
                        "vertical_align": "center",
                        "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined",
                                 "size": "16px 16px"},
                        "icon_position": "right", "icon_expanded_angle": -180},
             "border": {"color": border_for_status(status), "corner_radius": "8px"},
             "padding": "8px 8px 8px 8px",
             # ⚠️ 面板里是**两个** markdown 子元素（R3 收窄版）：`panel_body` 放推理轮、
             # `panel_tools` 放工具行。两个都**在建实体时定死**（之后只按 id 写内容，绝不
             # 做结构性写入），所以「元素表由建出来的卡 JSON 派生」这条机制原样成立。
             # 顺序 = panel_body → panel_tools，与 `panel_markdown` 的拼接顺序**逐行一致**
             # （先推理块、后工具行），所以用户看到的内容与拆分前没有差别。
             "elements": [{"tag": "markdown", "element_id": CARDKIT_PANEL_BODY_ID,
                           "content": panel_text or " "},
                          {"tag": "markdown", "element_id": CARDKIT_PANEL_TOOLS_ID,
                           "content": panel_tools_text or " "}]})
    if footer_text is not None:
        elements.append({"tag": "markdown", "element_id": CARDKIT_FOOTER_ID,
                         "content": footer_text or " ", "text_size": "notation"})
    return {
        "schema": SCHEMA,
        "config": {"streaming_mode": bool(streaming), "update_multi": True,
                   "summary": _summary_of(answer, fallback=DEFAULT_TITLE)},
        "body": {"elements": elements},
    }


def unified_panel(*, reasoning: str = "", rounds: Sequence[Dict[str, Any]] = (),
                  tools: Sequence[str] = (),
                  expanded: bool = False,
                  status: Any = None,
                  summary: Optional[Union[str, Dict[str, Any]]] = None,
                  include_reasoning_text: bool = True,
                  max_reasoning_chars: int = MAX_REASONING_CHARS,
                  max_tool_chars: int = MAX_TOOL_RESULT_CHARS,
                  max_steps: int = MAX_PANEL_STEPS,
                  ) -> Optional[Dict[str, Any]]:
    """统一面板：把推理过程和工具调用合并进一个可折叠块。

    这是「统一面板」功能的本体 —— 正文区保持干净，过程信息全部收进底部一个面板，
    而不是推理一个面板、工具另一个面板。没有内容时返回 None（调用方据此不渲染）。

    传了 ``rounds`` 就**按轮分段渲染**（每轮一个耗时标题行），这是 aiduPOP 的观感：
    「一轮 = 一段连续推理，被正文或工具打断」。没传就退回把 ``reasoning`` 当一整段渲染。
    每轮分到的截断额度是 ``max_reasoning_chars`` 的均分，且**渲染轮数收在
    ``max_reasoning_chars // _MIN_ROUND_CHARS`` 以内**（更早的轮补一行「…更早的 N 轮已折叠」）
    —— 这样「推理文本上限」这个配置约束的是**总量**，不会因为轮数变多而整体膨胀。

    所有进来的长文本都过 :func:`truncate`：面板是「收纳」不是「倾倒」，
    真跑一个长任务，原始推理和工具输出能把卡片撑到几十屏。

    三个上限都先过 :func:`_cap`：配置写 0 / 负数等于退回默认上限，**不是**取消上限
    （取消上限会让卡片被飞书拒收，后果见 :func:`_cap`）。
    """
    max_reasoning_chars = _cap(max_reasoning_chars, MAX_REASONING_CHARS)
    max_tool_chars = _cap(max_tool_chars, MAX_TOOL_RESULT_CHARS)
    max_steps = _cap(max_steps, MAX_PANEL_STEPS)
    inner: List[Dict[str, Any]] = []
    # 面板**自己**也要为飞书的元素硬墙让路（见 :data:`FEISHU_ELEMENT_LIMIT`）：
    # 面板的元素 = 轮次行 + 步骤行 + 可能的折叠提示。`max_panel_steps` 是用户可配的，
    # 配大了会把整张卡顶废（超限是**整卡不渲染**，不是截断），所以这里先算可用额度。
    # 额度与 :func:`fit_reply_card` 的判据**同源**（`_PANEL_CHILDREN_ROOM`）：
    # 面板自认为放得下 ⇒ 阶梯也认，不会出现「面板被整块摘掉」的缝。
    panel_room = max(2, _PANEL_CHILDREN_ROOM)

    all_rounds = [item for item in rounds if isinstance(item, dict)]
    round_list = [item for item in all_rounds if str(item.get("text") or "")]
    # `show_reasoning=false` ⇒ 只留灰色摘要行（时长来自所有轮），正文一段都不进面板。
    thinking_heading = _thinking_heading(round_list if include_reasoning_text else all_rounds,
                                         reasoning)
    if thinking_heading:
        inner.append(md(thinking_heading))
    if include_reasoning_text and round_list:
        # 每轮至少给 _MIN_ROUND_CHARS 才读得下去，但这意味着**轮数必须收住**：
        # 旧写法 share = max(120, 预算 // N) 在 N > 预算/120 时让渲染总量恒等于 120·N，
        # 与配置无关 —— 默认预算 1200 时第 11 轮起线性膨胀，实测 40 轮撑到 7845 字符，
        # 把 1500 字的正文一起顶穿字节预算 → fit_reply_card 降载到 no-panel，
        # **整个推理面板消失**（只剩一行 INFO 日志，正是本项目最怕的静默降级）。
        # 所以宁可有界地少显示历史轮：渲染轮数 ≤ 预算 // _MIN_ROUND_CHARS。
        # 两个约束一起收：字符预算（均分额度）与元素硬墙（给步骤行留出位置，
        # 有工具时至少留 1 行，没工具时轮次可以吃满面板额度）。
        # 头部已放入的 thinking 分区标题也要从轮次额度里扣掉（2026-09-17 审计 M5：
        # 不扣会与 fit_reply_card 的元素墙产生一个 2 元素的缝）。
        heading_room = 1 if thinking_heading else 0
        keep = max(1, min(len(round_list),
                          max_reasoning_chars // _MIN_ROUND_CHARS,
                          max(1, panel_room - heading_room - (1 if tools else 0) - 1)))
        share = max(_MIN_ROUND_CHARS, max_reasoning_chars // keep)
        dropped = len(round_list) - keep
        if dropped > 0:
            inner.append(md(_i18n.t("panel.rounds_trimmed", n=dropped)))
        for index, item in enumerate(round_list[-keep:], start=dropped + 1):
            body = _dense_lines(truncate(str(item.get("text") or ""), share))
            inner.append(md(f"**{_round_title(index, item.get('elapsed_ms'))}**\n{body}"))
    elif reasoning and include_reasoning_text:
        inner.append(md(_dense_lines(truncate(reasoning, max_reasoning_chars))))
    steps = [str(item) for item in tools]
    remaining = panel_room - len(inner)
    if remaining <= 0:
        steps = []
    elif steps and remaining == 1:
        # 只剩 1 格：只放最近一步，不加「N 步」标题（避免有标题没步骤）
        steps = steps[-1:]
    elif steps:
        tools_heading = _tools_heading(len(steps))
        if tools_heading:
            inner.append(md(tools_heading))
            remaining -= 1
        cap = max(0, min(max_steps, remaining))
        if cap <= 0:
            steps = []
        elif len(steps) > cap:
            # 保留最近几步：排查问题基本只看尾部，早期的步骤价值随时间递减。
            if cap >= 2:
                inner.append(md(_i18n.t("panel.trimmed", n=len(steps) - cap)))
                steps = steps[-(cap - 1):]
            else:
                steps = steps[-cap:]
    for item in steps:
        # 小 max_tool_result_chars 下先剥标签再截断：宁可丢颜色，也不留下半个 <font>
        if "<font" in item and len(item) > max_tool_chars:
            item = _strip_font_tags(item)
        inner.append(md(truncate(item, max_tool_chars)))
    border = border_for_status(status)
    if not inner and not status:
        return None
    if not inner:
        # 有结局但没有过程数据：补一行状态文字 —— 面板不能是空的（空面板飞书会拒，
        # 而且用户看不到任何东西），状态色也需要一个可见的落点。
        inner.append(md(_i18n.t(_STATUS_TEXT_KEYS.get(str(status or ""), "panel.title"))))
    # 标题必须是 plain_text；优先级：调用方给的摘要行 > 固定的「执行详情」。
    if summary:
        # summary 可以是字符串（旧调用方）或 i18n 节点（adapter 的 `_ld_panel_summary` 走这条，
        # 这样外层标题在中英文客户端各显示各的）。节点必须是 plain_text。
        title: Dict[str, Any] = (summary if isinstance(summary, dict)
                                 else {"tag": "plain_text", "content": str(summary)})
    elif tools:
        # 英文单复数：1 tool call / N tool calls
        key = "panel.title_tools_one" if len(tools) == 1 else "panel.title_tools"
        title = _i18n.i18n_text(key, n=len(tools))
    else:
        title = _i18n.i18n_text("panel.title")
    return collapsible(title, inner, expanded=expanded, border_color=border)


# --------------------------------------------------------------------------- #
# 1.0：澄清交互卡（必须接点击，所以整条链路都用 legacy 方言）
# --------------------------------------------------------------------------- #
def legacy_card(*, elements: Sequence[Dict[str, Any]], template: str = "orange",
                title: Union[str, Dict[str, Any]] = DEFAULT_TITLE,
                update_multi: bool = True) -> Dict[str, Any]:
    """legacy 1.0 卡片 —— **顶层 ``elements``，绝不含 ``schema`` / ``body``**。

    混进 ``schema: "2.0"`` 会让飞书按 2.0 解析，从而拒绝 ``action`` 按钮行，
    按钮点击也就永远送不到服务端。这是本模块最需要守住的不变量。

    ``title`` 可给 :func:`i18n.i18n_text` 的双语节点（1.0 header 接受
    ``i18n_content``，真机已证）。
    """
    config: Dict[str, Any] = {"wide_screen_mode": True}
    if update_multi:
        config["update_multi"] = True
    title_node = title if isinstance(title, dict) else {"tag": "plain_text", "content": title}
    return {
        "config": config,
        "header": {"template": template, "title": title_node},
        "elements": list(elements),
    }


#: markdown 里会被当成**语法**解释的字符（澄清卡的问题与选项标签要按字面显示）
_MD_ESCAPE_CHARS = "\\`*_~[]|"


def escape_inline_md(text: str) -> str:
    """把**一句话**（澄清卡的问题 / 选项标签）转义成能安全插进 markdown 的字面量。

    用途很窄、也必须有分寸：只用于澄清卡里那两处「一句话」的位置 —— 它们**不是完整回答**，
    不该走 :func:`sanitize_markdown`（那一套是给整篇正文做围栏识别 / 加粗平衡 / 标题降级的）。
    这里只做一件事：把会被当成语法的字符加上反斜杠。

    只转义 **反斜杠、反引号、`*`、`_`、`~`、`[`、`]`、`|`**：这几个在飞书 markdown 里
    是真语法（加粗 / 斜体 / 删除线 / 链接 / 表格 / 代码）。`#` `-` `>` `.` **只在行首**
    才有效，而我们的插入点是 ``❓ 问题`` 与 ``1. 标签``（都在行首标记**之后**），所以不动它们
    —— 多转义只会让用户看到一堆反斜杠。

    ⚠️ **只改展示那一份**：回调里的 ``question`` / ``answer``（以及选项的 ``value``）
    必须是**原文** —— 转义过的答案回给核心就对不上了。它**不幂等**（``*`` → ``\*`` →
    ``\\*``），所以**只在渲染时调一次**。
    """
    if not text:
        return text
    return "".join(("\\" + ch) if ch in _MD_ESCAPE_CHARS else ch for ch in str(text))


def _clarify_question_md(question: str) -> Dict[str, Any]:
    """澄清卡的**问题行** —— 两个方言共用这一个构造点（R11-C2 收敛）。

    以前 1.0 与 2.0 **各写了一遍** ``md(f"❓ {question}")``：同一个隐患两处真相，
    改一处漏一处是迟早的事（``*`` / ``[`` 会把渲染搞乱，两个方言同样中招）。
    """
    return md(f"\u2753 {escape_inline_md(question)}")


def _clarify_elements(question: str, choices: Sequence[str], *, clarify_id: str,
                      session_key: str) -> List[Dict[str, Any]]:
    buttons: List[Dict[str, Any]] = []
    for idx, choice in enumerate(choices, start=1):
        buttons.append(button(
            f"{idx}. {choice}",
            {"larkdeck_action": "clarify", "clarify_id": clarify_id,
             "session_key": session_key, "question": question, "answer": str(choice)},
            btype="primary" if idx == 1 else "default",
        ))
    buttons.append(button(
        _i18n.i18n_text("clarify.other"),
        {"larkdeck_action": "clarify", "clarify_id": clarify_id,
         "session_key": session_key, "question": question, "answer": OTHER_VALUE},
    ))
    return [_clarify_question_md(question), action_row(buttons)]


def clarify_card(question: str, choices: Sequence[str], *, clarify_id: str,
                 session_key: str, multi: bool = False) -> Dict[str, Any]:
    """带按钮的澄清卡（**1.0 方言**）。

    按钮 value 里带 ``larkdeck_action="clarify"``，由 LarkDeckMixin 重写的
    ``_on_card_action_trigger`` 拦截并调用 ``resolve_gateway_clarify()``。
    """
    elements = _clarify_elements(question, choices,
                                 clarify_id=clarify_id, session_key=session_key)
    elements.append(note_i18n("clarify.multi_hint" if multi else "clarify.hint"))
    return legacy_card(elements=elements, template="orange",
                       title=_i18n.i18n_text("clarify.header"))


def _clarify_choice_pairs(choices: Sequence[str]) -> List[Tuple[str, str]]:
    """澄清选项的**唯一归一化入口**：(显示标签, 提交值)。

    ⚠️ **卡面上那份可见列表与下拉里的 options 必须都从这里来**（R11-C1）：两处各算一次
    的话，用户在卡上看到的编号与真正提交的值会不一致 —— 而且那种不一致**四门禁抓不住**
    （两边都合法，只是不对应），只有用户点错才发现。判据由单测钉住（列表文本 == 下拉标签）。

    去重：``select_static`` 的 ``value`` **不可重复**（官方文档明写「否则交互异常、
    服务端无法区分选了哪个」），所以重复的选项只留第一个。
    """
    pairs: List[Tuple[str, str]] = []
    seen: set = set()
    for idx, choice in enumerate(choices, start=1):
        text = str(choice)
        if text in seen:
            continue
        seen.add(text)
        # ⚠️ **展示标签先折叠空白**（审计 B1），提交值仍是**原文**：
        #    选项是模型给的自由文本，多行选项会让「卡面列表 == 下拉标签」这条同源判据失效
        #    （列表凭空多一行），更糟的是 `# `/`- `/`> ` 落到**行首**就变成 markdown 语法 ——
        #    而 `escape_inline_md` 不转义这几个字符，前提正是「插入点永远在行首标记之后」。
        label_text = " ".join(text.split()) or text
        pairs.append((f"{idx}. {label_text}", text))
    return pairs


def _clarify_options(pairs: Sequence[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """2.0 下拉的选项：显示用带序号，**回调值用原始选项文本**（答案要的是规范标签）。"""
    return [{"text": {"tag": "plain_text", "content": label}, "value": value}
            for label, value in pairs]


def _clarify_choice_list(pairs: Sequence[Tuple[str, str]]) -> str:
    """**卡面上可见**的选项列表（R11-C1）。

    起因：默认方言翻成 2.0 之后，选项文本只活在下拉里 —— **不点开就看不出有哪几个选项**
    （用户对着截图追问过这件事）。所以下拉之上再放一份同源的纯文本列表。
    ⚠️ 只能是 ``markdown``：2.0 卡里**不许出现 1.0 的 ``note``/``action`` 行**（飞书拒收，
    见 AGENTS.md 不变量 5），而 ``footnote()``/``md()`` 都是 2.0 元素。
    """
    # 标签是 markdown ⇒ 必须转义；下拉那边是 `plain_text` ⇒ 不转义（同一份 pairs，
    # 各自按自己的方言渲染，这就是「同源」而不是「同一串字节」）
    return "\n".join(escape_inline_md(label) for label, _ in pairs)


def clarify_card_2(question: str, choices: Sequence[str], *, clarify_id: str,
                   session_key: str, multi: bool = False) -> Dict[str, Any]:
    """带下拉/输入框的澄清卡（**2.0 方言**，组件级 ``behaviors`` 接回调）。

    ⚠️ **默认方言就是 2.0**（2026-09-13 翻的，前置是真机点过一次 —— ``AGENTS.md`` 不变量 5
    要求「要接服务端点击的卡片」必须有真机证据，那条纪律是被「抄第三方注释」坑出来的）。
    本文档上一版还写着「默认不启用 / 默认 1.0」，**已经过期**（2026-09-15 更正）。
    拦截逻辑一行都不用改：
    ``behaviors`` 的 ``value`` 会**原样**成为 ``event.action.value``，所以
    ``larkdeck_action`` 那个拦截键照旧成立；用户选了什么则在 ``action.option``，
    输入框内容在 ``action.input_value``（见 ``adapter._ld_clarify_answer``）。

    多选用 ``multi_select_static``（回调的 ``option`` 是**列表**，要拼成 JSON 数组
    —— 与网关那条「文字回答多选」的规范一致，见 ``clarify_gateway`` 的
    ``_coerce_multi_select_text``）。多选时**不给**自由输入框：混在一起会与
    「编号/标签」的解析规则打架。
    """
    value: Dict[str, Any] = {"larkdeck_action": "clarify", "clarify_id": clarify_id,
                             "session_key": session_key, "question": question}
    # ⚠️ 归一化**只做一次**，下拉与卡面列表共用（R11-C1 的同源要求）
    pairs = _clarify_choice_pairs(choices)
    selector: Dict[str, Any] = {
        "tag": "multi_select_static" if multi else "select_static",
        "placeholder": {"tag": "plain_text",
                        "content": _i18n.t("clarify.pick_multi" if multi else "clarify.pick")},
        "options": _clarify_options(pairs),
        "behaviors": [{"type": "callback", "value": dict(value)}],
    }
    # 卡面上那份**可见**的选项列表（与下拉同源）：不点开也能看到有哪几个选项
    elements: List[Dict[str, Any]] = [_clarify_question_md(question),
                                      md(_clarify_choice_list(pairs)), selector]
    if not multi:
        elements.append({
            "tag": "input",
            "label": _i18n.i18n_text("clarify.other"),
            "placeholder": {"tag": "plain_text", "content": _i18n.t("clarify.other_hint")},
            # 只带路由键与澄清标识：**模式由载荷推导**（有 `action.input_value` 就是
            # 自由文本），所以这里不再写一个没人读的 `free_text` 标志键
            # （2026-09-13 审计：那个键全仓只有写入点、没有读取点）。
            "behaviors": [{"type": "callback", "value": dict(value)}],
        })
    # 脚注按方言分流（R11-C1）：2.0 卡上**没有按钮**，说「点按钮」就是撒谎
    elements.append(footnote(_i18n.t(
        "clarify.multi_hint" if multi else "clarify.hint_2")))
    return card(elements=elements, template="orange",
                title=_i18n.i18n_text("clarify.header"), summary=question)


def clarify_resolved_card_2(*, question: str, answer: Any, user_name: str) -> Dict[str, Any]:
    """2.0 的已答复卡 —— **必须与待答卡同方言**，否则回调里回填的那一帧会被飞书丢弃。"""
    return card(elements=[_clarify_question_md(question),
                          md(f"\u2705 **{_clarify_answer_label(answer)}**\u3000\u2014\u3000{user_name}")],
                template="green", title=_i18n.i18n_text("clarify.header"), summary=question)


def _clarify_answer_label(answer: Any) -> str:
    """把（可能来自多选 JSON 的）答案整理成给人看的一行。"""
    text = str(answer or "")
    if text.startswith("["):
        try:
            items = json.loads(text)
            if isinstance(items, list):
                text = "、".join(str(item) for item in items)
        except Exception:
            pass
    return _i18n.t("clarify.other") if text == OTHER_VALUE else text


def clarify_resolved_card(*, question: str, answer: str, user_name: str) -> Dict[str, Any]:
    """点击后原地替换的已答复卡。

    必须与待答卡同为 1.0 方言 —— 回调里回填的卡片也走同一条 legacy 轨迹，
    换成 2.0 会被飞书丢弃（HFC 的 ``interaction callback card suppressed`` 就是踩了这个）。
    """
    label = _clarify_answer_label(answer)
    return legacy_card(
        elements=[_clarify_question_md(question), md(f"\u2705 **{label}**\u3000\u2014\u3000{user_name}")],
        template="green", title=_i18n.i18n_text("clarify.header"),
    )
