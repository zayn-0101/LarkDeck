"""变异验证器：每条**新加的断言**都必须能证明「把对应的修复撤掉就变红」。

用法::

    python3 tests/mutate_check.py            # 只跑清单里的变异
    python3 tests/mutate_check.py -k P1      # 名字里含 P1 的

为什么要有它（本项目的反复踩坑）：
  * 第七路审计实测 50 条变异里 **23 条四个门禁全绿** —— 断言写着、但对该变异没有判别力；
  * 更要命的是阻断项本身：`_MAX_TRACKED_TEXT` 被同文件另一句赋值覆盖，
    而回归用例的 fixture 恰好卡在被覆盖后的阈值上 ⇒ 那条「修复」从未真正生效。
所以规矩是：**先写变异，再写断言**；撤掉修复必须红。

实现要点（踩过的坑）：
  * 快照目录**必须叫 `larkdeck`** —— `check_override.py` 把仓库父目录加进 PYTHONPATH，
    目录名不对的话 `import larkdeck` 会解析到**未变异的基线**（曾导致 18 条假 GREEN）；
  * 每个变异放在**独立父目录**下，避免 `__pycache__` 串味；跑之前清一次。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent

#: (名字, 相对文件, 原文, 替换成, 期望其中哪个门禁变红)
#: 期望值只用于打印对照；判定标准是「至少一门红」。
MUTATIONS = [
    # ---- HK：钩子→指标层的字段透传（页脚窗口靠它解析） ---------------------- #
    # 2026-09-14 线上：页脚恒显示 `ctx x/128k`（真实 1M）。判据是「撤掉透传就必须红」——
    # 注意 `check_hooks.py` **抓不到**这条：它自己把 base_url 塞进派发载荷，
    # 而缺陷在「回调 → 指标层」这一跳，只有 `test_units` 那条直打真回调的用例看得见。
    ("HK-页脚不再透传 base_url（窗口退化成家族兜底 128K）", "core/hooks.py",
     '            base_url=payload.get("base_url", ""),\n', "", "test_units"),
    # ---- P1 阻断：阈值被覆盖 / 阈值不再贴着飞书硬上限 ------------------------ #
    ("P1-阈值被后面的旧赋值覆盖（历史阻断项的形态）", "core/adapter.py",
     "_MAX_TEXT_ENTRIES = 16",
     "_MAX_TEXT_ENTRIES = 16\n_MAX_TRACKED_TEXT = _cards.CARD_BYTE_BUDGET",
     "test_units"),
    ("P1-阈值高到超过飞书硬上限", "core/adapter.py",
     "_CARD_BYTES_OVERHEAD = 1024", "_CARD_BYTES_OVERHEAD = -100000",
     "test_units"),
    ("P1-阈值退回降载预算", "core/adapter.py",
     "_MAX_TRACKED_TEXT = _FEISHU_CARD_BYTE_LIMIT - _CARD_BYTES_OVERHEAD",
     "_MAX_TRACKED_TEXT = _cards.CARD_BYTE_BUDGET",
     "test_units"),
    # ---- P8：日志单位口径 ---------------------------------------------------- #
    ("P8-告警的原始口径传字符数", "core/adapter.py",
     '            _log_note_text_skipped(size, len(body.encode("utf-8", "ignore")))',
     "            _log_note_text_skipped(size, len(body))",
     "test_units"),
    # ---- P2：越界静默 -------------------------------------------------------- #
    ("P2-越界告警降级成 debug", "core/adapter.py",
     '    logger.warning("[larkdeck] streaming_print_ms 配置有问题：%s —— 已退回 %dms"',
     '    logger.debug("[larkdeck] streaming_print_ms 配置有问题：%s —— 已退回 %dms"',
     "test_units"),
    ("P2b-坏值（转不动）不再报警", "core/adapter.py",
     "    if value is None and raw is not None:",
     "    if False:",
     "test_units"),
    # ---- P3：config_schema 解析漏判 ------------------------------------------ #
    ("P3a-加一个含连字符的 yaml-only 键", "plugin.yaml",
     "  cards:\n", "  brand-new-key:\n    type: boolean\n    default: true\n  cards:\n",
     "test_units"),
    ("P3a-加一个含数字的 yaml-only 键", "plugin.yaml",
     "  cards:\n", "  brand2:\n    type: string\n    default: \"x\"\n  cards:\n",
     "test_units"),
    ("P3b-把一个键挪到别的键之后", "plugin.yaml",
     "  cards:\n", "  reactions-x:\n    default: true\n  cards:\n",
     "test_units"),
    ("P3c-type 与代码默认值不符", "plugin.yaml",
     "  streaming_print_ms:\n    type: integer",
     "  streaming_print_ms:\n    type: boolean",
     "test_units"),
    ("P3d-整数默认值写成带引号", "plugin.yaml",
     "    default: 15\n", "    default: \"15\"\n", "test_units"),
    ("P3e-整数默认值写成浮点", "plugin.yaml",
     "    default: 15\n", "    default: 15.0\n", "test_units"),
    ("P3f-布尔默认值写成 1", "plugin.yaml",
     "  cards:\n    type: boolean\n    default: true",
     "  cards:\n    type: boolean\n    default: 1",
     "test_units"),
    ("P3g-删掉一个键", "plugin.yaml",
     "  reactions:\n    type: boolean\n    default: true\n", "", "test_units"),
    # ---- P4：M22 归因 + 直接打被保护的那层 ----------------------------------- #
    ("P4-删掉 _positive 的 nan/inf 半段", "core/cards.py",
     "    return number == number and number not in (float(\"inf\"), float(\"-inf\")) and number > 0",
     "    return number > 0", "test_units"),
    ("P4-streaming_config 不再夹取", "core/cards.py",
     "    if not 1 <= value <= PRINT_FREQUENCY_MAX_MS:\n        value = DEFAULT_PRINT_FREQUENCY_MS",
     "    if False:\n        value = DEFAULT_PRINT_FREQUENCY_MS",
     "test_units"),
    # ---- P5：probe_report 形状 + 两条 WARNING -------------------------------- #
    ("P5a-probe_report 不再上报 missing_reactions", "core/compat.py",
     "    report[\"missing_reactions\"] = [n for n in REACTION_ADAPTER_ATTRS if not _has(cls, n)]",
     "    report[\"missing_reactions\"] = []", "check_override"),
    ("P5a2-probe_report 删掉 missing_reactions 键", "core/compat.py",
     "    report[\"missing_reactions\"] = [n for n in REACTION_ADAPTER_ATTRS if not _has(cls, n)]",
     "    pass", "check_override"),
    ("P5b-删掉 reactions 那条 WARNING", "core/adapter.py",
     "    if missing_reactions:\n        logger.warning", "    if False:\n        logger.warning",
     "test_units"),
    ("P5c-两条 WARNING 的列表串了", "core/adapter.py",
     "\"`reactions: false` 会静默失效（「处理中」表情照旧）\", missing_reactions)",
     "\"`reactions: false` 会静默失效（「处理中」表情照旧）\", missing_signal)",
     "test_units"),
    ("P5d-报告缺键时不再报警", "core/adapter.py",
     "    if absent:\n        logger.warning(\"[larkdeck] 能力探测报告缺少",
     "    if False:\n        logger.warning(\"[larkdeck] 能力探测报告缺少",
     "test_units"),
    ("P5e-REACTION_ADAPTER_ATTRS 不再登记 _reactions_enabled", "core/compat.py",
     "REACTION_ADAPTER_ATTRS: Tuple[str, ...] = (\n    \"_reactions_enabled\",\n)",
     "REACTION_ADAPTER_ATTRS: Tuple[str, ...] = ()",
     "test_units"),
    # ---- 第十四轮（真机 --stop-redraw 抓到的）状态色载体 --------------------- #
    ("S1-超预算档不再补状态小面板", "core/cards.py",
     "        if card_bytes(with_shell) <= FEISHU_CARD_BYTE_LIMIT:",
     "        if False:",
     "test_units"),
    ("S2-小面板不受硬上限约束", "core/cards.py",
     "        if card_bytes(with_shell) <= FEISHU_CARD_BYTE_LIMIT:",
     "        if True:",
     "test_units"),
    ("S3-status_shell 不认状态色", "core/cards.py",
     "    if not color or color == BORDER_NEUTRAL:",
     "    if not color or color == BORDER_NEUTRAL or True:",
     "test_units"),
    ("S4-status_shell 丢掉状态文字", "core/cards.py",
     "    return collapsible(title, [md(_i18n.t(_STATUS_TEXT_KEYS.get(status, \"panel.title\")))],\n                       expanded=False, border_color=color)",
     "    return collapsible(title, [], expanded=False, border_color=color)",
     "test_units"),
    # ---- 第八路审计的 43 条变异里「全绿」的那些，逐条补上门禁 ------------------ #
    ("A01-硬上限改到远超实测包络", "core/cards.py",
     "FEISHU_CARD_BYTE_LIMIT = 128000", "FEISHU_CARD_BYTE_LIMIT = 999999",
     "test_units"),
    ("A03-余量放大到 50000（窗口被压小）", "core/adapter.py",
     "_CARD_BYTES_OVERHEAD = 1024", "_CARD_BYTES_OVERHEAD = 50000",
     "test_units"),
    ("A05-余量缩到实测开销 298", "core/adapter.py",
     "_CARD_BYTES_OVERHEAD = 1024", "_CARD_BYTES_OVERHEAD = 298",
     "test_units"),
    ("A07-阈值改成手写死值", "core/adapter.py",
     "_MAX_TRACKED_TEXT = _FEISHU_CARD_BYTE_LIMIT - _CARD_BYTES_OVERHEAD",
     "_MAX_TRACKED_TEXT = 127000",
     "test_units"),
    ("A08-正文只留 1 份", "core/adapter.py",
     "_MAX_TEXT_ENTRIES = 16", "_MAX_TEXT_ENTRIES = 1",
     "test_units"),
    # ⚠️ 曾经在这里的「B14-超限改成截断正文」（`body = ""` → `body = body[:1000]`）已移出矩阵：
    #    它在本验证器里被判成「四个门禁全部崩溃、没有任何失败标记」（复现不出根因，疑与满负载下
    #    子进程异常退出有关），而**它的判别力是有实证的** —— 手工跑同一变异时
    #    `test_long_body_still_redraws_on_stop_and_never_degrades_silently` 打出
    #    `FAIL  … 存了个半截正文（1000 字符）—— 不许截断`。为避免验证器常驻 exit=1，
    #    这里只留说明；要复核就手工改那一行再跑 test_units。
    ("C18-上限改成字面量 2001（与 cards 分叉）", "core/adapter.py",
     "    high = _cards.PRINT_FREQUENCY_MAX_MS", "    high = 2001",
     "test_units"),
    ("C24-坏值不再报警", "core/adapter.py",
     '        _log_print_ms_once(f"{raw!r} 不是可用的数字", default)',
     "        pass",
     "test_units"),
    ("D24-删掉一个键的 type 声明", "plugin.yaml",
     "  streaming_print_ms:\n    type: integer\n", "  streaming_print_ms:\n",
     "test_units"),
    ("D26-default 改折叠标量", "plugin.yaml",
     "    default: 15\n", "    default: >\n      15\n",
     "test_units"),
    ("D27-解析器不再响亮失败", "tests/test_units.py",
     "            raise _unsupported(line)\n        if re.match(r\"^ {4,}- \", line):",
     "            continue\n        if re.match(r\"^ {4,}- \", line):",
     "test_units"),
    ("D28-段尾顶格行静默 break", "tests/test_units.py",
     "            if re.match(r\"^[A-Za-z_][A-Za-z0-9_.-]*:\", line):\n                break\n            raise _unsupported(line)",
     "            break",
     "test_units"),
    ("E37-PROBE_REPORT_KEYS 少一项", "core/compat.py",
     '    "missing_callback", "missing_signal", "missing_reactions", "missing_display_chrome",\n'
     '    "session_attribution_ok", "core_interrupt_lookup",\n)',
     '    "missing_callback", "missing_signal", "missing_display_chrome",\n'
     '    "session_attribution_ok", "core_interrupt_lookup",\n)',
     "test_units"),
    # ---- 第十五轮：首帧占位 + 面板耗时段 -------------------------------------- #
    ("T1-建卡不再显示占位（空白卡）", "core/cards.py",
     "    if streaming and not str(answer or \"\").strip():",
     "    if False:",
     "test_units"),
    ("T2-收尾帧也带占位（空答案永远「正在生成」）", "core/cards.py",
     "    if streaming and not str(answer or \"\").strip():",
     "    if not str(answer or \"\").strip():",
     "test_units"),
    ("T3-面板标题丢掉耗时段", "core/adapter.py",
     "                duration=duration,", "                duration=None,",
     "check_hooks"),
    ("T4-footer_line 不再渲染耗时", "core/cards.py",
     "    if isinstance(duration, (int, float)) and duration >= 0.1:\n        parts.append(f\"{syms['duration']} {format_elapsed(float(duration))}\")",
     "    if False:\n        parts.append(f\"{syms['duration']} {format_elapsed(float(duration or 0))}\")",
     "check_hooks"),
    ("T5-每轮耗时标题不再渲染", "core/cards.py",
     "        return f\"{base} · {format_elapsed(max(0.1, min(elapsed_ms, 86_400_000) / 1000.0))}\"",
     "        return base",
     "test_units"),
    # ---- 第九路审计：口径统一 / 复合不变量 / 壳分支 / 诊断日志 ---------------- #
    ("F1-追踪判据退回原始 utf-8 口径", "core/adapter.py",
     "        size = _card_body_bytes(body)",
     "        size = len(body.encode(\"utf-8\", \"ignore\"))",
     "test_units"),
    ("F1b-判据不再做 JSON 转义", "core/adapter.py",
     "        return len(json.dumps(str(body or \"\"), ensure_ascii=False).encode(\"utf-8\", \"ignore\"))",
     "        return len(str(body or \"\").encode(\"utf-8\", \"ignore\"))",
     "test_units"),
    ("F2-壳可以长到超过余量（复合不变量）", "core/cards.py",
     "    return collapsible(title, [md(_i18n.t(_STATUS_TEXT_KEYS.get(status, \"panel.title\")))],\n                       expanded=False, border_color=color)",
     "    return collapsible(title, [md(_i18n.t(_STATUS_TEXT_KEYS.get(status, \"panel.title\"))),\n                               md(\"填充\" * 160)],\n                       expanded=False, border_color=color)",
     "test_units"),
    ("F7-壳分支丢掉打字机配置", "core/cards.py",
     "        with_shell = reply_card(answer, streaming=streaming, panel=shell,\n                                print_frequency_ms=print_frequency_ms)",
     "        with_shell = reply_card(answer, streaming=streaming, panel=shell)",
     "test_units"),
    ("F8-降载日志的档位写死成 ok", "core/adapter.py",
     "                   tier, elements, _cards.FEISHU_ELEMENT_LIMIT, size,",
     "                   \"ok\", elements, _cards.FEISHU_ELEMENT_LIMIT, size,",
     "test_units"),
    ("F8b-降载日志不打字节数", "core/adapter.py",
     "                   tier, elements, _cards.FEISHU_ELEMENT_LIMIT, size,",
     "                   tier, elements, _cards.FEISHU_ELEMENT_LIMIT, 0,",
     "test_units"),
    ("F6-_as_int 丢掉 OverflowError 捕获", "core/adapter.py",
     "    except (TypeError, ValueError, OverflowError):\n        return None\n    if number != number",
     "    except (TypeError, ValueError):\n        return None\n    if number != number",
     "test_units"),
    # ---- F9：跨模块常量被抄成字面量（防漂移闸门）----------------------------- #
    ("F9-adapter 抄字面量硬上限", "core/adapter.py",
     "_FEISHU_CARD_BYTE_LIMIT = _cards.FEISHU_CARD_BYTE_LIMIT",
     "_FEISHU_CARD_BYTE_LIMIT = 128000",
     "test_units"),
    ("F9b-打字机上限抄字面量", "core/adapter.py",
     "    high = _cards.PRINT_FREQUENCY_MAX_MS", "    high = 2000",
     "test_units"),
    ("F9c-追踪阈值抄字面量", "core/adapter.py",
     "_MAX_TRACKED_TEXT = _FEISHU_CARD_BYTE_LIMIT - _CARD_BYTES_OVERHEAD",
     "_MAX_TRACKED_TEXT = 126976",
     "test_units"),
    # ---- 完整回合的**可见帧序列**（check_hooks 新增的那一节）------------------- #
    ("SEQ1-seed 帧不再带等待期占位", "core/cards.py",
     "    if streaming and not str(answer or \"\").strip():",
     "    if False:",
     "check_hooks"),
    ("SEQ2-收尾帧不再画状态色", "core/adapter.py",
     '                status=snap.get("status"),',
     "                status=None,",
     "check_hooks"),
    # ⚠️ R4 起收尾帧写的是**本卡那一段**（`tail_visible`），锚点跟着改（不然就是「清单与源码脱节」）
    ("SEQ3-收尾帧不关 streaming_mode", "core/adapter.py",
     # ⚠️ 锚点在 2026-09-15 晚**重新对准过一次**：短码那批改动把收尾卡的页脚从
     #    `_ld_footer()` 换成了 `_ld_frame_footer(state)` ⇒ 旧锚点失效（`❓ 锚点没找到` = 红，
     #    规则上它与假绿同级：跑不到的变异等于没验）。
     "            card = self._ld_build_card(tail_visible or \" \", streaming=False,\n                                       panel=self._ld_panel(chat, report_empty=True),\n                                       footer=self._ld_frame_footer(state))",
     "            card = self._ld_build_card(tail_visible or \" \", streaming=True,\n                                       panel=self._ld_panel(chat, report_empty=True),\n                                       footer=self._ld_frame_footer(state))",
     "check_hooks"),
    # ⚠️ **2026-09-21 重新指向**（全量变异实测：老锚点打在函数**顶部**那句**死代码**上 ——
    #    structured 分支不读它、legacy 分支会覆盖它 ⇒ 等价变异，🟢 是正确判定）。
    #    真正的载体是 legacy 分支里那一句（16 空格缩进），打在它上面 `test_units` 必须红。
    ("SEQ4-/stop 重绘不再带中止色（legacy 分支丢强制面板）", "core/adapter.py",
     "                panel = self._ld_panel(chat, report_empty=True) or _cards.unified_panel(\n                    status=_panel.STATUS_STOPPED)",
     "                panel = None",
     "test_units"),
    # ---- 第十路审计：真判据 / 崩溃分类 / 口径 / 调用点 / 码表 ------------------ #
    ("A1a-判据退回「只看近似阈值」", "core/adapter.py",
     "        if size > _HOPELESS_BYTES or not _stop_redraw_would_paint(\n"
     "                body, panel=panel, footer=footer,\n"
     "                structured_card=structured_card):",
     "        if size > _MAX_TRACKED_TEXT:",
     "test_units"),
    ("A1b-真判据恒真（不丢正文）", "core/adapter.py",
     "        if size > _HOPELESS_BYTES or not _stop_redraw_would_paint(\n"
     "                body, panel=panel, footer=footer,\n"
     "                structured_card=structured_card):",
     "        if size > _HOPELESS_BYTES:",
     "test_units"),
    ("A1c-真判据不再检查有没有颜色", "core/adapter.py",
     '        return \'"collapsible_panel"\' in json.dumps(node, ensure_ascii=False)',
     "        return True",
     "test_units"),
    # 对照项：在**用例覆盖的尺寸区间**内，legacy 近似壳与「降载后真卡」给出同样结论
    # （两侧的差异只在装饰体积那几百字节的窗口里）⇒ 撤掉这条接线门禁抓不住，是**等价**而非盲区。
    # 真正钉住 structured 判据的是 V4-30（超限也说能画）。

    ("V4-32-预加载提示首字后不删（正文上方留一行多余的字）", "core/adapter.py",
     '        elif live.get("ck_loading", False) and visible:',
     '        elif False:  # V4-32 mutated',
     "test_units"),
    ("V4-31-结构化标题退回硬编码中文（英文客户端看中文）", "core/adapter.py",
     '            title = _i18n.i18n_text(\n'
     '                "panel.summary_both_one" if total_tools == 1 else "panel.summary_both",',
     '            title = _i18n.t(  # V4-31 mutated（退回单语字符串）\n'
     '                "panel.summary_both_one" if total_tools == 1 else "panel.summary_both",',
     "test_units"),
    ("V4-30-structured 判据无视字节墙（超限也说能画）", "core/adapter.py",
     '            return _cards.card_bytes(structured_card) <= _cards.FEISHU_CARD_BYTE_LIMIT',
     '            return True  # V4-30 mutated',
     "test_units"),

    # ⚠️ 2026-09-18：原先是「对照项」（当时 `fit_reply_card` 的裸卡回落似乎覆盖了这句）。
    # F1 修复后 `_stop_redraw_would_paint` 会给结果套 `apply_text_profile`（+~300B），
    # 而 `fit_reply_card` 的字节判据发生在套 profile **之前** ⇒ 显式硬上限检查不再冗余。
    ("A1d-真判据不再单独检查字节上限（profile 字节把卡顶过硬墙）", "core/adapter.py",
     "        if _cards.card_bytes(node) > _cards.FEISHU_CARD_BYTE_LIMIT:\n            return False",
     "        if False:\n            return False",
     "test_units"),

    ("A2-判据口径退回原始 utf-8", "core/adapter.py",
     '        return len(json.dumps(str(body or ""), ensure_ascii=False).encode("utf-8", "ignore"))',
     '        return len(str(body or "").encode("utf-8", "ignore"))',
     "test_units"),
    ("A2b-判据口径用 ensure_ascii=True", "core/adapter.py",
     '        return len(json.dumps(str(body or ""), ensure_ascii=False).encode("utf-8", "ignore"))',
     '        return len(json.dumps(str(body or ""), ensure_ascii=True).encode("utf-8", "ignore"))',
     "test_units"),
    ("A15-降载日志调用点传 0 字节", "core/adapter.py",
     "            _log_degrade_once(tier, _cards.count_elements(card), _cards.card_bytes(card))",
     "            _log_degrade_once(tier, _cards.count_elements(card), 0)",
     "test_units"),
    ("A16-降载日志调用点档位写死", "core/adapter.py",
     "            _log_degrade_once(tier, _cards.count_elements(card), _cards.card_bytes(card))",
     '            _log_degrade_once("ok", _cards.count_elements(card), _cards.card_bytes(card))',
     "test_units"),
    ("A17-瞬态码表删掉官方限流码", "core/adapter.py",
     "_TRANSIENT_CODES = frozenset({230020, 99991400, 300309, 300317})",
     "_TRANSIENT_CODES = frozenset({99991400, 300309, 300317})",
     "test_units"),
    ("A18-瞬态码表混入确定性拒收", "core/adapter.py",
     "_TRANSIENT_CODES = frozenset({230020, 99991400, 300309, 300317})",
     "_TRANSIENT_CODES = frozenset({230020, 99991400, 300309, 300317, 230099})",
     "test_units"),
    ("A5-真判据在 cards.py 里再抄一份数字", "core/cards.py",
     "FEISHU_CARD_BYTE_LIMIT = 128000",
     "FEISHU_CARD_BYTE_LIMIT = 128000\n_FEISHU_WALL_COPY = 128000",
     "test_units"),
    # ---- 阶段 9：CardKit 传输（撤掉任何一条保证都必须变红）------------------- #
    ("CK2-正文写失败被吞（不回落）", "core/adapter.py",
     '            wrote = await self._ld_ck_write(card_id, answer.element_id, answer.content, seq)\n'
     '            # 正文这一次逻辑写**永远不跳**（它是提交点，见上面守卫那段说明），但照旧记进滑窗：\n'
     '            # 守卫要算的是「这一秒真的欠了飞书多少次写」，漏记正文等于给自己虚报余量。\n'
     '            _ck_window_note(state_ref, time.monotonic())\n'
     '            if not wrote.ok:',
     '            wrote = await self._ld_ck_write(card_id, answer.element_id, answer.content, seq)\n'
     '            # 正文这一次逻辑写**永远不跳**（它是提交点，见上面守卫那段说明），但照旧记进滑窗：\n'
     '            # 守卫要算的是「这一秒真的欠了飞书多少次写」，漏记正文等于给自己虚报余量。\n'
     '            _ck_window_note(state_ref, time.monotonic())\n'
     '            if False:',
     "test_units"),
    ("CK3-序号不递增（成功路径不推进计数器）", "core/adapter.py",
     '                                          "last": text, "last_at": now,\n'
     '                                          "ck_seq": seq_after,',
     '                                          "last": text, "last_at": now,\n'
     '                                          "ck_seq": _ck_seq(state),',
     "test_units"),
    ("CK4-不写面板元素（面板永不更新）", "core/adapter.py",
     '    if _cards.CARDKIT_PANEL_BODY_ID in elems:\n'
     '        ops.append(_CkOp(_cards.CARDKIT_PANEL_BODY_ID, panel_text or " ", _CK_ROLE_PANEL))',
     "    if False:\n        pass",
     "test_units"),
    # ---- 第十一路审计续：`unified_panel: false` 在 cardkit 下也必须真的没有面板 ---------- #
    ("CK7-建实体时面板开关写死成 True（无视统一面板配置）", "core/adapter.py",
     '                                         panel=bool(_cfg("unified_panel")),',
     '                                         panel=True,',
     "test_units"),
    ("CK8-面板关掉后仍然去写面板元素（真机 300313 ⇒ 整回合打回纯文本）", "core/adapter.py",
     '    if _cards.CARDKIT_PANEL_BODY_ID in elems:',
     '    if True:',
     "test_units"),
    ("CK7b-cards.py 里把面板参数当恒真（结构不看配置）", "core/cards.py",
     '    ]\n    if panel:\n        elements.append(\n            {"tag": "collapsible_panel", "element_id": CARDKIT_PANEL_ID,',
     '    ]\n    if True:\n        elements.append(\n            {"tag": "collapsible_panel", "element_id": CARDKIT_PANEL_ID,',
     "test_units"),
    # 默认值是 2026-09-13 翻成 cardkit 的（审计 + 真机生产路径探针都过了）。这条变异的
    # 意思不是「cardkit 更差」，而是「**默认值必须是个显式决定**」：谁把它顺手改回去
    # （或改坏成别的值），这条断言就得红。
    ("CK6-默认传输被顺手改回 patch（或改坏）", "core/adapter.py",
     '    "native_transport": "cardkit",', '    "native_transport": "patch",',
     "test_units"),
    ("CK9-_ld_transport 里写死一条传输（运行时不再跟随配置/默认）", "core/adapter.py",
     '        return "cardkit" if str(_cfg_raw("native_transport") or "").strip() == "cardkit" else "patch"',
     '        return "patch"',
     "test_units"),
    # 启动自检那行日志是「这个进程到底在跑哪条传输」的唯一自证手段（真机上没有别的办法看见）。
    ("CK10-启动自检不再自报 native 传输", "core/adapter.py",
     '        detail += f" · native 传输 {LarkDeckMixin._ld_transport()}"',
     '        pass',
     "check_override"),
    # 第十二路审计（可行性那一路）实测的**现网回归**：手写清单漏字段 = 静默关掉能力。
    # R1 的地基：golden trace 夹具必须能抓到「写入形状的任何漂移」。
    ("CK25-写入请求的 uuid 形状变了（golden trace 必须抓到）", "core/adapter.py",
     '                                       f"ld-{card_id}-{element_id}-{sequence}"),',
     '                                       f"ld-{card_id}-{element_id}"),',
     "test_units"),
    ("CK26-提交点在前（正文先写、装饰后写）", "core/adapter.py",
     '            batch_res = await self._ld_ck_batch(card_id, fresh, seq)',
     '            if answer is not None:\n'
     '                await self._ld_ck_write(card_id, answer.element_id, answer.content, seq)\n'
     '            batch_res = await self._ld_ck_batch(card_id, fresh, seq)',
     "test_units"),
    ("CK24-平台 entry 透传退回手写清单（丢掉 standalone_sender_fn 等）", "core/adapter.py",
     '        if field.name in _IDENTITY_ENTRY_FIELDS:',
     '        if field.name not in ("validate_config", "required_env", "install_hint"):',
     "check_override"),
    ("CK22-自报的传输与实际生效的不一致（日志说 patch，跑的却是 cardkit）", "core/adapter.py",
     '        detail += f" · native 传输 {LarkDeckMixin._ld_transport()}"',
     '        detail += " · native 传输 patch"',
     "check_override"),
    # 空 card_id 的坏卡形态：撤掉这道闸门就会拿空 id 去发实体卡（第十一路审计的【低】项）。
    ("CK11-建实体拿到空 card_id 也照样发实体卡", "core/adapter.py",
     '        if _ld_response_code(made) != 0 or not card_id:\n'
     '            # R11-B2：**按内层码留痕**。',
     '        if _ld_response_code(made) != 0:\n'
     '            # R11-B2：**按内层码留痕**。',
     "test_units"),
    # ---- 第十二路审计：CardKit 写路径没有限流退避（patch 路径有）⇒ 不对称 ---------------- #
    ("CK12-元素写入不再退避重试（撞一次限流整回合掉 native）", "core/adapter.py",
     '''        resp = await self._ld_write_with_retry(
            lambda: reqs.write_element(card_id, element_id, content, int(sequence),
                                       f"ld-{card_id}-{element_id}-{sequence}"),
            self._client.cardkit.v1.card_element.content,
            f"写元素 {element_id}")''',
     '''        resp = await self._run_blocking(
            self._client.cardkit.v1.card_element.content,
            reqs.write_element(card_id, element_id, content, int(sequence),
                               f"ld-{card_id}-{element_id}-{sequence}"))''',
     "test_units"),
    ("CK13-限流退避表里混进结构性码（300309 白等三轮才回落）", "core/adapter.py",
     '_WRITE_RETRY_CODES = frozenset({230020, 99991400})',
     '_WRITE_RETRY_CODES = frozenset({230020, 99991400, 300309, 300317})',
     "test_units"),
    ("CK14-建实体不再退避重试（限流一次就没卡了）", "core/adapter.py",
     '''        made = await self._ld_write_with_retry(
            lambda: reqs.create_card(json.dumps(card, ensure_ascii=False)),
            self._client.cardkit.v1.card.create, "建实体")''',
     '''        made = await self._run_blocking(
            self._client.cardkit.v1.card.create,
            reqs.create_card(json.dumps(card, ensure_ascii=False)))''',
     "test_units"),
    # ---- R2：每帧写入预算 + 页脚元素开关 + 装饰失败的 DEAD 语义 ---------------------- #
    ("R2-1-装饰不走 batch（改走 content，而且只写第一个元素）", "core/adapter.py",
     '            batch_res = await self._ld_ck_batch(card_id, fresh, seq)',
     '            batch_res = await self._ld_ck_write(card_id, fresh[0].element_id, fresh[0].content, seq)',
     "test_units"),
    ("R2-2-页脚元素不看 `footer` 配置（关掉了还在卡里留个空壳）", "core/adapter.py",
     '                                         footer_text=("" if _cfg("footer") else None))\n',
     '                                         footer_text="")\n',
     "test_units"),
    ("R2-3-装饰失败升级成整帧失败（买下「DM 两张卡」那条链）", "core/adapter.py",
     '                self._ld_ck_mark_dead(state_ref, dead_ops)',
     '                return False, seq, fresh[0]',
     "test_units"),
    ("R2-4-装饰失败不标死（后续每帧继续白试同一个元素）", "core/adapter.py",
     '        dead.update(op.element_id for op in ops)',
     '        pass',
     "test_units"),
    ("R2-5-标死的元素仍然每帧重试（把 ck_dead 过滤去掉）", "core/adapter.py",
     '            live_elems = [e for e in elems if e not in dead]',
     '            live_elems = list(elems)',
     "test_units"),
    ("R2-6-页脚内容不进 ops（页脚元素永远是空壳）", "core/adapter.py",
     '        ops.append(_CkOp(_cards.CARDKIT_FOOTER_ID, footer_text or " ", _CK_ROLE_DECOR))',
     '        pass',
     "test_units"),
    # ---- R2：装饰的「未变化不重写」+ 记账规则 + 写入预算的两个数字 ------------------- #
    ("R2-7-装饰每帧无条件重写（未变化不重写失效 ⇒ 稳态下写入翻倍）", "core/adapter.py",
     '        fresh = [op for op in decor if sent.get(op.element_id) != op.content]',
     '        fresh = list(decor)',
     "test_units"),
    ("R2-8-去重只看元素 id、不比内容（装饰改了却永远不再写 ⇒ 静默冻结）", "core/adapter.py",
     '        fresh = [op for op in decor if sent.get(op.element_id) != op.content]',
     '        fresh = [op for op in decor if op.element_id not in sent]',
     "test_units"),
    ("R2-9-记账不留历史（本帧只写面板 ⇒ 页脚的记录被抹掉，下一帧白写一次）", "core/adapter.py",
     '                state_ref["ck_decor"] = {**sent,\n'
     '                                         **{op.element_id: op.content for op in fresh}}',
     '                state_ref["ck_decor"] = {op.element_id: op.content for op in fresh}',
     "test_units"),
    ("R2-10-把卡级写入上限的口径悄悄调大（预算跟着变松）", "core/adapter.py",
     '_CK_WRITES_PER_SECOND = 10',
     '_CK_WRITES_PER_SECOND = 40',
     "test_units"),
    ("R2-11-建实体不再守元素墙（真机 300305 ⇒ 整卡被拒、这一帧什么都没有）", "core/adapter.py",
     '    if _cards.count_elements(card) > _cards.FEISHU_ELEMENT_LIMIT:',
     '    if False:',
     "test_units"),
    ("R2-12-元素墙只数顶层元素（面板里塞满子元素就溜过去了）", "core/adapter.py",
     '    if _cards.count_elements(card) > _cards.FEISHU_ELEMENT_LIMIT:',
     '    if len(card.get("body", {}).get("elements") or []) > _cards.FEISHU_ELEMENT_LIMIT:',
     "test_units"),
    ("R2-13-记账提前到 batch 调用之前（没写成功也算「已写」⇒ 静默冻结的种子）", "core/adapter.py",
     '            batch_res = await self._ld_ck_batch(card_id, fresh, seq)',
     '            state_ref["ck_decor"] = {**sent,\n'
     '                                     **{op.element_id: op.content for op in fresh}}\n'
     '            batch_res = await self._ld_ck_batch(card_id, fresh, seq)',
     "test_units"),
    ("R2-14-退避表少一项（调用放大效应的最坏值变了，而文档口径没变）", "core/adapter.py",
     '_TRANSIENT_BACKOFF = (0.1, 0.3, 0.6)',
     '_TRANSIENT_BACKOFF = (0.1, 0.3)',
     "test_units"),
    # ---- R5：DEGRADE 车道 / 撤回守卫 / 坏元素点名 / 追踪表上界 ------------------------ #
    ("R5-1-卡级死法仍然 fail-open（不降级 ⇒ 用户后面看到纯文本）", "core/adapter.py",
     '                if wrote.code in _CARD_DEATH_CODES:\n'
     '                    state_ref["ck_degrade"] = wrote.code',
     '                if False:\n'
     '                    state_ref["ck_degrade"] = wrote.code',
     "test_units"),
    ("R5-2-降级时另建一张卡（DM 里两张卡）", "core/adapter.py",
     '                result = await self._ld_update_card(chat, message_id, card)',
     '                result = await self._ld_send_card(chat, card)',
     "test_units"),
    ("R5-3-降级但不清 card_id（之后每帧仍然写已关的会话 ⇒ 每帧失败）", "core/adapter.py",
     '                self._ld_stream_put(key, {**live_state, "ck_degrade": degrade_code, "card_id": "",\n'
     '                                          "ck_seq": seq_after, "last": text, "last_at": now,',
     '                self._ld_stream_put(key, {**live_state, "ck_degrade": degrade_code,\n'
     '                                          "ck_seq": seq_after, "last": text, "last_at": now,',
     "test_units"),
    ("R5-4-撤回守卫不生效（消息没了还继续往它写）", "core/adapter.py",
     '            if _ld_response_code(response) in _WITHDRAWN_CODES:',
     '            if False:',
     "test_units"),
    ("R5-5-撤回之后自己补发一条（DM 两张卡）", "core/adapter.py",
     '                self._ld_drop_message(message_id, _ld_response_code(response))\n'
     '                return result',
     '                self._ld_drop_message(message_id, _ld_response_code(response))\n'
     '                await self._ld_update_card(chat_id, message_id, card)\n'
     '                return result',
     "test_units"),
    ("R5-6-批量失败时无视 msg 点名，整批标死（好的装饰也一起冻）", "core/adapter.py",
     '                hit = [op for op in fresh if op.element_id == blamed] if blamed else []',
     '                hit = [] if blamed else []',
     "test_units"),
    ("R5-7-点名的坏元素认错（把 blamed 当成好元素放过去，写它每帧 300313）", "core/adapter.py",
     '                hit = [op for op in fresh if op.element_id == blamed] if blamed else []',
     '                hit = [op for op in fresh if op.element_id != blamed] if blamed else []',
     "test_units"),
    ("R5-8-追踪表没有上界（长驻进程慢性泄漏）", "core/adapter.py",
     '_MAX_TRACKED = 512',
     '_MAX_TRACKED = 10 ** 9',
     "test_units"),
    ("R5-10-降级 patch 打错卡（打到别的 message_id ⇒ 老卡静默冻结）", "core/adapter.py",
     '                result = await self._ld_update_card(chat, message_id, card)',
     '                result = await self._ld_update_card(chat, "om_other_card", card)',
     "test_units"),
    ("R5-11-装饰撞上卡级死法却不降级（后续每帧往关掉的会话写正文）", "core/adapter.py",
     '                if batch_res.code in _CARD_DEATH_DECOR_CODES:\n'
     '                    state_ref["ck_degrade"] = batch_res.code',
     '                if False:\n'
     '                    state_ref["ck_degrade"] = batch_res.code',
     "test_units"),
    ("R5-12-卡级死法集只剩会话已关（序号冲突/元素不存在不再降级）", "core/adapter.py",
     '_CARD_DEATH_CODES = frozenset({300309, 300313, 300317})',
     '_CARD_DEATH_CODES = frozenset({300309})',
     "test_units"),
    ("R5-13-装饰告警不再列出受影响的元素（排查时不知道冻了哪几个）", "core/adapter.py",
     '    ids = "、".join(op.element_id for op in ops)',
     '    ids = ""',
     "test_units"),
    ("R5-14-解析出的坏 id 不核对是否在本批（截断/误伤 ⇒ 标死好元素、放走坏元素）", "core/adapter.py",
     '                hit = [op for op in fresh if op.element_id == blamed] if blamed else []',
     '                hit = list(fresh) if blamed else []',
     "test_units"),
    ("R5-15-坏 id 解析不设码门槛（别的码的 msg 里出现 elementID 就照解析）", "core/adapter.py",
     '        if self.code != 300313:\n            return None',
     '        if False:\n            return None',
     "test_units"),
    ("R5-16-活跃度不再刷新（长静默回合被当泄漏流回收 ⇒ 另发一张新卡）", "core/adapter.py",
     '            state.setdefault("alive_at", time.monotonic())\n'
     '            state["alive_at"] = time.monotonic()',
     '            pass',
     "test_units"),
    # ---- 显示 chrome：核心的工具行默认不进正文（用户在真机上明确要求过） ---------------- #
    # ---- R7：页脚扩展默认关、缺数据不许编 0 ------------------------------------------- #
    ("R7-1-页脚指标无视配置（默认 off 也照 full 渲染）", "core/adapter.py",
     '            mode = str(_cfg_raw("footer_metrics") or "off").strip().lower()',
     '            mode = "full"',
     "test_units"),
    ("R7-2-认不出的取值当成 full（不猜却放大了）", "core/adapter.py",
     '            if mode not in ("off", "basic", "full"):\n'
     '                mode = "off"          # 认不出的值按 off（不猜、不放大）',
     '            if mode not in ("off", "basic", "full"):\n'
     '                mode = "full"',
     "test_units"),
    ("R7-4-会话预览传裸字符串（真机回 300122，预览整条失效）", "core/adapter.py",
     '            res = await self._ld_ck_settings(card_id, {"content": text}, seq, retry=False,\n'
     '                                             state_ref=state_ref)',
     '            res = await self._ld_ck_settings(card_id, text, seq, retry=False,\n'
     '                                             state_ref=state_ref)',
     "test_units"),
    ("R7-5-会话预览不限频（每帧都写 ⇒ 超每帧写入预算）", "core/adapter.py",
     '        if isinstance(last_at, (int, float)) and now - float(last_at) < interval:\n'
     '            return seq, fields',
     '        if False:\n'
     '            return seq, fields',
     "test_units"),
    ("R7-6-会话预览写失败升级成整帧失败（为了列表预览掉纯文本）", "core/adapter.py",
     '            fields = {"ck_summary_dead": True}\n'
     '            _log_ck_summary_failed_once(res.code)',
     '            fields = {"ck_summary_dead": True}\n'
     '            _log_ck_summary_failed_once(res.code)\n'
     '            raise RuntimeError("preview dead")',
     "test_units"),
    ("R7-7-会话预览不占序号（与元素写入账本脱钩 ⇒ 真机撞号）", "core/adapter.py",
     '        seq += 1\n'
     '        try:\n'
     '            res = await self._ld_ck_settings(card_id, {"content": text}, seq, retry=False,\n'
     '                                             state_ref=state_ref)',
     '        try:\n'
     '            res = await self._ld_ck_settings(card_id, {"content": text}, seq, retry=False,\n'
     '                                             state_ref=state_ref)',
     "test_units"),
    # ---- R6a：markdown 卫生（只在收尾帧、只删不补、代码区不动）------------------------- #
    # ⚠️ 锚点 2026-09-14 改过一次：`display = text` 那一行现在是**正文净化**的入口
    # （R11-A7），卫生那一步在收尾帧上做（`_sanitize_for_send(display[tail_offset:])`）。
    # 所以这条变异改成「收尾那一帧不做卫生」—— 它盯的性质没变：改写前缀会让前缀链断掉。
    ("R6a-1-流式收尾帧不做卫生（前缀链断掉 ⇒ 回答重发一遍）", "core/adapter.py",
     '            tail_visible = _sanitize_for_send(display[tail_offset:])',
     '            tail_visible = display[tail_offset:]',
     "test_units"),
    ("R6a-2-改回「补一个 `**` 收尾」（把尾巴吞进加粗）", "core/cards.py",
     '    for index in range(0, len(text) - 1):\n'
     '        if text.startswith("**", index) and not any(start <= index < end\n'
     '                                                    for start, end in spans):\n'
     '            return text[:index] + text[index + 2:]\n'
     '    return text',
     '    return text.rstrip("\\n") + "**"',
     "test_units"),
    ("R6a-3-卫生不管代码区（把代码块里的 `#`/`**` 也改坏）", "core/cards.py",
     '    spans = _code_spans(text)\n'
     '    if _outside_code(text, spans).count("**") % 2 == 0:',
     '    spans = []\n'
     '    if text.count("**") % 2 == 0:',
     "test_units"),
    ("R6a-4-先降级标题再删游离 `**`（奇偶性被搅乱 ⇒ 删错标记）", "core/cards.py",
     '    return _demote_headings(_drop_unpaired_bold(text))',
     '    return _drop_unpaired_bold(_demote_headings(text))',
     "test_units"),
    # ---- R6a 审计收口（高-1 / 中-1..中-4 / 低-1..低-6，2026-09-14）-------------------- #
    # 每条都是「把这次补上的修复撤掉」，并且**都实测过变红**（数字见交付说明）。
    ('R6a-5-未闭合围栏不延伸到文末（核心的边界收尾会把代码内容改坏）', 'core/cards.py',
     '    if fence_char:\n        # 未闭合：**一直到文末**都算代码区\n        spans.append((fence_start, size))',
     '    if fence_char:\n        spans.append((fence_start, fence_start))',
     'test_units'),
    ('R6a-6-四反引号围栏不认（里层 ``` 被当成外层闭合 ⇒ 代码内容当正文）', 'core/cards.py',
     '            if run >= 3 and (char != "`" or "`" not in after[run:]):',
     '            if run == 3 and (char != "`" or "`" not in after[run:]):',
     'test_units'),
    ('R6a-7-`~~~` 围栏不认（波浪线围栏里的 #/** 被当成 markdown）', 'core/cards.py',
     '        if indent < 4 and after[:1] in ("`", "~"):',
     '        if indent < 4 and after[:1] == "`":',
     'test_units'),
    ('R6a-9-删最后一个游离 `**`（拆掉后文合法加粗对 ⇒ 整段被吞进加粗）', 'core/cards.py',
     '    for index in range(0, len(text) - 1):',
     '    for index in range(len(text) - 2, -1, -1):',
     'test_units'),
    ('R6a-10-`edit_message` 两条路径一起做卫生（中间帧前缀链断掉）', 'core/adapter.py',
     '            if finalize:\n                content = _sanitize_for_send(content)',
     '            content = _sanitize_for_send(content)',
     'test_units'),
    ('R6a-11-`send()` 不做卫生（同一段文本两条路径长得不一样）', 'core/adapter.py',
     '            # 判据是「这份文本是不是**完整文本**」，不是「这是哪条路径」（见 cards.sanitize_markdown）。\n            content = _sanitize_for_send(content)',
     '            # 判据是「这份文本是不是**完整文本**」，不是「这是哪条路径」（见 cards.sanitize_markdown）。\n            content = content',
     'test_units'),
    ('R6a-12-`/stop` 重绘不做卫生（中止的回合看不到任何卫生）', 'core/adapter.py',
     '            card = self._ld_build_card(_sanitize_for_send(text) or " ", streaming=False,',
     '            card = self._ld_build_card(text or " ", streaming=False,',
     'test_units'),
    ('R6a-13-不剥标题体的尾随闭合法 `#`（`# 标题 ####` 把 #### 露成可见噪声）', 'core/cards.py',
     '        if not _HEADING_START_RE.match(match.group(2)):\n            body = _HEADING_CLOSING_RE.sub("", body)',
     '        if False:\n            body = _HEADING_CLOSING_RE.sub("", body)',
     'test_units'),
    ('R6a-14-用 `str.strip()` 清标题体（吞掉正文里的全角空格）', 'core/cards.py',
     r'        body = match.group(2).strip(" \t")',
     '        body = match.group(2).strip()',
     'test_units'),
    ('R6a-15-卫生后不过字节闸门（超限的收尾卡被拒 ⇒ 整条回答掉成纯文本）', 'core/adapter.py',
     '    if size > _cards.FEISHU_CARD_BYTE_LIMIT:\n        _log_sanitize_reverted_once(size)\n        return text',
     '    if False:\n        _log_sanitize_reverted_once(size)\n        return text',
     'test_units'),
    ('R6a-17-行内代码的排除回到 O(围栏数 × 行内代码数)（收尾帧被拖慢）', 'core/cards.py',
     '        while where < len(fences) and fences[where][1] <= at:\n            where += 1\n        if where < len(fences) and fences[where][0] <= at < fences[where][1]:',
     '        if any(start <= at < end for start, end in fences):',
     'test_units'),
    ('R6a-20-`_code_spans` 退化成恒返回空列表（代码区判据整个失效）', 'core/cards.py',
     '    merged = sorted(fences + spans)',
     '    return []',
     'test_units'),
    ('R6a-18-进度表某行的「证据」列被掏空（记录空洞化却骗过旧门禁）', 'docs/plan-v1.md',
     '| R6a markdown 卫生 | ✅ 完成（**写完整文本的每条路径**都卫生；删**第一个**游离 `**` 而不补、H1–H3 降级、代码区零改动（含未闭合/四反引号/`~~~` 围栏）、幂等、卫生后过同口径字节闸门）；对抗审计后收口 **1 高 + 4 中 + 4 低** | 单测 6 条 · 变异 `R6a-1..R6a-18` 共 **17 条全红** · 门禁 **162/162** + OVERRIDE/HOOKS/CLARIFY E2E 全绿 · 审计明细见「R6a 对抗审计」一节（表格降载**有意不做**，理由见上） |',
     '| R6a markdown 卫生 | ✅ 完成（**写完整文本的每条路径**都卫生；删**第一个**游离 `**` 而不补、H1–H3 降级、代码区零改动（含未闭合/四反引号/`~~~` 围栏）、幂等、卫生后过同口径字节闸门）；对抗审计后收口 **1 高 + 4 中 + 4 低** |   |',
     'test_units'),
    ("R7-3-缺数据编成 0（页脚显示「⚡ 0%」这种假读数）", "core/adapter.py",
     '                cache=metric_snap.get("cache_pct") if mode in ("basic", "full") else None,',
     '                cache=(metric_snap.get("cache_pct") or 0) if mode in ("basic", "full") else None,',
     "test_units"),
    # R7 审计收口（中-2 / 中-3 / 高-1 / 低-7）：每条都实测过「撤掉之后四门禁全绿」。
    ("R7-8-预览写的异常不再兜底（一次连接重置就把整帧拖红 ⇒ 掉纯文本）", "core/adapter.py",
     '            res = _CkResult(False, 0, str(exc))',
     '            raise',
     "test_units"),
    ("R7-9-预览写成功但不记账（限频被彻底废掉 ⇒ 之后每帧都写）", "core/adapter.py",
     '            fields = {"ck_summary": text, "ck_summary_at": now}',
     '            fields = {}',
     "test_units"),
    ("R7-10-预览写失败时把序号退回去（下一次写撞号 ⇒ 静默半更新）", "core/adapter.py",
     '            fields = {"ck_summary_dead": True}\n'
     '            _log_ck_summary_failed_once(res.code)',
     '            seq -= 1\n'
     '            fields = {"ck_summary_dead": True}\n'
     '            _log_ck_summary_failed_once(res.code)',
     "test_units"),
    ("R7-11-预览写的去重键退化成常量（服务端当成重发 ⇒ 列表永远停在第一条）", "core/adapter.py",
     '            return reqs.settings_card(card_id, payload, int(seq), f"ld-{card_id}-s{seq}")',
     '            return reqs.settings_card(card_id, payload, int(seq), f"ld-{card_id}-s")',
     "test_units"),
    ("R7-12-页脚把「真的 0% 命中」当成缺数据（真实读数被吞掉）", "core/adapter.py",
     '                cache=metric_snap.get("cache_pct") if mode in ("basic", "full") else None,',
     '                cache=(metric_snap.get("cache_pct") or None) if mode in ("basic", "full") else None,',
     "test_units"),
    ("R7-13-预览也跟着退避重试（白白给这一帧加最多 ≈1 秒）", "core/adapter.py",
     '            res = await self._ld_ck_settings(card_id, {"content": text}, seq, retry=False,\n'
     '                                             state_ref=state_ref)',
     '            res = await self._ld_ck_settings(card_id, {"content": text}, seq,\n'
     '                                             state_ref=state_ref)',
     "test_units"),
    ("PL-1-无视配置一律转发父类（工具行又并进正文）", "core/adapter.py",
     '            if _cfg("progress_lines_in_body"):\n'
     '                return super().format_tool_event(\n'
     '                    event, mode=mode, preview_max_len=preview_max_len)',
     '            if True:\n'
     '                return super().format_tool_event(\n'
     '                    event, mode=mode, preview_max_len=preview_max_len)',
     "check_override"),
    ("PL-2-工具行开关默认翻成 true（用户会看到正文区又滚工具行）", "core/adapter.py",
     '    "progress_lines_in_body": False,',
     '    "progress_lines_in_body": True,',
     "test_units"),
    ("R5-17-降级那一帧丢掉本帧算出的标死（同一件事两处真相）", "core/adapter.py",
     '                self._ld_stream_put(key, {**live_state, "ck_degrade": degrade_code, "card_id": "",\n'
     '                                          "last": text, "last_at": now, "ck_seq": seq_after,',
     '                self._ld_stream_put(key, {**state, "ck_degrade": degrade_code, "card_id": "",\n'
     '                                          "last": text, "last_at": now, "ck_seq": seq_after,',
     "test_units"),
    ("R5-9-追踪表按创建时刻淘汰（长回合的卡被踢 ⇒ 卡片永久冻结）", "core/adapter.py",
     '                                key=lambda kv: kv[1].get("last", kv[1].get("t0", 0.0)))',
     '                                key=lambda kv: kv[1].get("t0", 0.0))',
     "test_units"),
    # R1 审计（U1/U2/U4/W5/W6）实测「四门禁全绿」的五种改法，逐条钉住。
    ("U1-空元素表/一个 op 都写不出去被当作成功（卡片静默冻死、无日志、不回落）", "core/adapter.py",
     '        if answer is None and not fresh:',
     '        if False:',
     "test_units"),
    ("U2-序号读取退回裸 int()（坏值会抛 ⇒ 整回合掉 native）", "core/adapter.py",
     '            ok, seq_after, failed = await self._ld_ck_apply(card_id, ops, _ck_seq(state),',
     '            ok, seq_after, failed = await self._ld_ck_apply(card_id, ops, int(state.get("ck_seq") or 0),',
     "test_units"),
    ("U4-角色判据放宽（正文失败被误诊成面板失败）", "core/adapter.py",
     '            if failed is not None and failed.role == _CK_ROLE_PANEL:',
     '            if failed is not None:',
     "test_units"),
    ("U4b-失败文案退化成常量（真机上分不清哪个元素死了）", "core/adapter.py",
     '        what = {_CK_ROLE_ANSWER: "正文", _CK_ROLE_PANEL: "面板"}.get(self.role, "元素")\n'
     '        return f"CardKit 写{what}元素失败（{self.element_id}）"',
     '        return "CardKit 写元素失败"',
     "test_units"),
    ("W5-失败帧把序号写死成 1（下一帧撞号 ⇒ 300317 ⇒ 掉 native）", "core/adapter.py",
     '                                      "ck_decor": live_state.get("ck_decor") or {},\n'
     '                                      "ck_seq": seq_after})',
     '                                      "ck_decor": live_state.get("ck_decor") or {},\n'
     '                                      "ck_seq": 1})',
     "test_units"),
    ("W6-面板内容被写成常量（内容整条丢失）", "core/adapter.py",
     '        ops.append(_CkOp(_cards.CARDKIT_PANEL_BODY_ID, panel_text or " ", _CK_ROLE_PANEL))',
     '        ops.append(_CkOp(_cards.CARDKIT_PANEL_BODY_ID, " ", _CK_ROLE_PANEL))',
     "test_units"),
    ("W6b-正文内容被写成常量", "core/adapter.py",
     '        answer_text = _cards.answer_or_pending(display, streaming) or " "\n'
     '        ops.append(_CkOp(_cards.CARDKIT_ANSWER_ID, answer_text, _CK_ROLE_ANSWER))',
     '        answer_text = " "\n'
     '        ops.append(_CkOp(_cards.CARDKIT_ANSWER_ID, answer_text, _CK_ROLE_ANSWER))',
     "test_units"),
    ("U3-元素表改成「另读一遍配置」（与建出来的卡是两个真相源）", "core/adapter.py",
     '                                          "ck_elems": _ck_elems_from_card(card_json),',
     '                                          "ck_elems": [_cards.CARDKIT_ANSWER_ID,\n'
     '                                                       _cards.CARDKIT_PANEL_BODY_ID],',
     "test_units"),
    # R7 数据层：派生值算错会让页脚显示假信息（比不显示更坏）。
    # ⚠️ 名字**不许**与 adapter 那两条 R7-1/R7-2 重名（R7 审计低-4）：`mutate_check.py -k R7-1`
    # 会一次跑两条、报告里没法按名字唯一定位。数据层这两条统一加 `-D` 前缀。
    ("R7-D1-TTFB 的差值方向反了（负差被当成 None，页脚永远没有首字延迟）", "core/context.py",
     '    delta = later - earlier',
     '    delta = earlier - later',
     "test_units"),
    ("R7-D2-`_as_float` 不再挡 inf（`round(inf)` 会抛 OverflowError 炸掉渲染）", "core/context.py",
     '    if number != number or number in (float("inf"), float("-inf")):   # NaN / ±Inf\n'
     '        return None\n'
     '    return number',
     '    return number',
     "test_units"),
    # ---- 第十二路审计的三条门禁缺口（每条都实测过「撤掉修复四门禁全绿」）----------------- #
    ("CK15-建实体不再守字节硬上限（144KB 的卡真的发出去）", "core/adapter.py",
     '    if _cards.card_bytes(card) > _cards.FEISHU_CARD_BYTE_LIMIT:',
     '    if False:',
     "test_units"),
    # ⚠️ **锚点在 R3 收窄版落地时重对准过**：实体卡的面板改由 `_ld_panel_parts` 渲染之后，
    # `_ld_panel_markdown`（原锚点）在生产里**一个调用方都没有**了 —— 变异打在孤儿函数上
    # 等于打在死代码上（那正是 `CK23` 教过的形态：撤掉「修复」四门禁照旧全绿）。
    # 现在锚点对着**真正写进卡的那条路径**，判据是「配了上限却没用上就必须红」。
    ("CK16-实体卡面板无视用户的三个上限（推理块与工具块都不截断）", "core/adapter.py",
     '''                _cards.panel_rounds_markdown(
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
                ),''',
     '''                _cards.panel_rounds_markdown(
                    reasoning=str(snap.get("reasoning") or ""),
                    rounds=snap.get("rounds") or [],
                ),
                _cards.panel_tools_markdown(
                    tools=steps,
                ),''',
     "test_units"),
    ("CK17-面板元素失败告警不再限流（每帧一条刷爆日志）", "core/adapter.py",
     '    if now - getattr(_log_ck_panel_write_failed_once, "_at", 0.0) < 60.0:\n        return',
     '    if False:\n        return',
     "test_units"),
    # ---- 第十二路审计（第二路）实测的三条：锚点被丢 / 打字机开关 / 去重键 ------------------ #
    ("CK19-建实体时把回复锚点丢掉（回答不再挂在提问下面）", "core/adapter.py",
     '        anchor = str(reply_to or "").strip()\n        if anchor:',
     '        anchor = ""\n        if anchor:',
     "test_units"),
    ("CK20-实体卡不再开流式会话（逐字打字机的总开关）", "core/cards.py",
     '        "config": {"streaming_mode": bool(streaming), "update_multi": True,',
     '        "config": {"streaming_mode": False, "update_multi": True,',
     "test_units"),
    ("CK21-发实体卡不带 uuid（重试会多发一张冻结卡）", "core/adapter.py",
     '                .msg_type("interactive").uuid(f"ld-msg-{card_id}")',
     '                .msg_type("interactive")',
     "test_units"),
    # ⚠️ R4 起「超上限」分两半（切得开就切卡、切不开才 fail-open），这条变异钉的是**后半**：
    #    把闸门整个拆掉 ⇒ 一帧超大的增量会直接往元素里写（必被飞书拒）
    # ⚠️ 这条变异的锚点**换过三次**，每次都踩了同一个坑（记下来）：
    #   ① 原本钉的是「正文超过硬上限时 fail-open」那道闸门；R4 引入切卡后它变成**死代码**
    #      （切卡判据已经把两种情况收口 ⇒ 恒假），于是这条变异在整棵树上 🟢 —— 死代码让
    #      「撤掉修复必须变红」这条纪律失效（R8 收口那一轮实测发现）。
    #   ② 对准**真正**那道闸门（切不开时的早返回）之后，它又一次变绿：R9 的补丁重基在更早的
    #      提交上、`git apply --3way` 把死代码那一段判给了「theirs」，**静默回滚了删除**。
    #   ③ 现在对准的是**切不开时那道闸门**，且死代码已删 —— 只要有人再把它加回来，
    #      这条变异就会变绿（这就是它的守卫作用）。
    ("CK23-一帧塞不下时不再 fail-open（往一张新卡里硬写超上限的增量）", "core/adapter.py",
     '                if split_state is None:\n'
     '                    # 切不开 ⇒ 这一帧只能回落。**字节数照旧打出来**（运维第一眼要的就是数字），\n'
     '                    # 复用另一条闸门的那句日志（同一件事：正文超过单卡能装下的量）。\n'
     '                    _log_ck_over_budget_once(_card_body_bytes(visible))\n'
     '                    return self._ld_stream_fail("正文超过单卡硬上限且这一帧切不出新卡")',
     '                if split_state is None:\n'
     '                    pass',
     "test_units"),
    ("CK18-超预算告警不再限流", "core/adapter.py",
     '    if now - getattr(_log_ck_over_budget_once, "_at", 0.0) < 60.0:\n        return',
     '    if False:\n        return',
     "test_units"),
    # ---- R3 收窄版：面板拆两块（推理 / 工具）后必须守住的四件事 -------------------- #
    # 拆开的**唯一**目的是「工具事件只重写工具块、推理增长只重写推理块」，所以四条变异
    # 分别打：结构（两块都在卡里）、写入计划（工具有没有自己的 op）、方向（谁跟谁混了）、
    # 以及「推理块里混进工具行」这条**唯一能证伪「两块其实是同一块」**的形态。
    ("R3-1-面板只建一块（工具块没有自己的元素 ⇒ 拆开白做）", "core/cards.py",
     '                          {"tag": "markdown", "element_id": CARDKIT_PANEL_TOOLS_ID,\n'
     '                           "content": panel_tools_text or " "}]})',
     '                          ]})',
     "test_units"),
    ("R3-2-写入计划里丢掉工具块（工具行永远不更新）", "core/adapter.py",
     '    if _cards.CARDKIT_PANEL_TOOLS_ID in elems:\n'
     '        ops.append(_CkOp(_cards.CARDKIT_PANEL_TOOLS_ID, panel_tools_text or " ", _CK_ROLE_PANEL))',
     '    if False:\n        pass',
     "test_units"),
    ("R3-3-两块写反（工具内容写进推理块、推理内容写进工具块）", "core/adapter.py",
     '        ops.append(_CkOp(_cards.CARDKIT_PANEL_BODY_ID, panel_text or " ", _CK_ROLE_PANEL))\n'
     '    if _cards.CARDKIT_PANEL_TOOLS_ID in elems:\n'
     '        ops.append(_CkOp(_cards.CARDKIT_PANEL_TOOLS_ID, panel_tools_text or " ", _CK_ROLE_PANEL))',
     '        ops.append(_CkOp(_cards.CARDKIT_PANEL_BODY_ID, panel_tools_text or " ", _CK_ROLE_PANEL))\n'
     '    if _cards.CARDKIT_PANEL_TOOLS_ID in elems:\n'
     '        ops.append(_CkOp(_cards.CARDKIT_PANEL_TOOLS_ID, panel_text or " ", _CK_ROLE_PANEL))',
     "test_units"),
    ("R3-4-推理块把工具行也一起装进去（回到「一个 markdown 装全部」）", "core/adapter.py",
     '                _cards.panel_rounds_markdown(\n'
     '                    # show_reasoning=false ⇒ 只留 `💭 思考 · 1.6s` 摘要行，正文一个字不上卡\n'
     '                    reasoning=str(snap.get("reasoning") or ""),\n'
     '                    rounds=snap.get("rounds") or [],\n'
     '                    include_text=show_reasoning,\n'
     '                    max_reasoning_chars=_cfg_int("max_reasoning_chars", _cards.MAX_REASONING_CHARS),\n'
     '                ),',
     '                _cards.panel_markdown(\n'
     '                    reasoning=str(snap.get("reasoning") or ""),\n'
     '                    rounds=snap.get("rounds") or [],\n'
     '                    tools=steps,\n'
     '                    max_reasoning_chars=_cfg_int("max_reasoning_chars", _cards.MAX_REASONING_CHARS),\n'
     '                    max_tool_chars=_cfg_int("max_tool_result_chars", _cards.MAX_TOOL_RESULT_CHARS),\n'
     '                    max_steps=_cfg_int("max_panel_steps", _cards.MAX_PANEL_STEPS),\n'
     '                ),',
     "test_units"),
    ("R3-5-工具块忘了截断（长工具输出把卡片顶爆）", "core/cards.py",
     '    for item in steps:\n'
     '        # 小 max_tool_result_chars 下先剥标签再截断：宁可丢颜色，也不留下半个 <font>\n'
     '        if "<font" in item and len(item) > max_tool_chars:\n'
     '            item = _strip_font_tags(item)\n'
     '        lines.append(truncate(item, max_tool_chars))\n'
     '    return "\\n".join(lines)',
     '    for item in steps:\n        lines.append(item)\n'
     '    return "\\n".join(lines)',
     "test_units"),
    ("R10-4-删掉每回合自检汇总（三个症状又变回只能靠用户截图）", "core/adapter.py",
     '            _log_turn_selfcheck(chat, self._ld_transport(), int(state.get("frames") or 0) + 1,\n                                strips=int(state.get("strips") or 0) + (1 if stripped else 0),\n                                trace=_ld_trace_id(message_id))',
     '',
     "test_units"),
    ("R10-1-面板状态退回模块局部（插件被加载两次时钩子写一份、卡片读另一份）", "core/panel.py",
     '_STATE: Dict[str, Dict[str, Any]] = _SHARED["panel_state"]',
     '_STATE: Dict[str, Dict[str, Any]] = {}',
     "test_units"),
    ("R10-3-页脚指标退回模块局部（卡片上永远没有页脚那行）", "core/context.py",
     '_LATEST: Dict[str, Any] = _shared_box()["ctx_latest"]',
     '_LATEST: Dict[str, Any] = {}',
     "test_units"),
    ("R10-2-写卡账本退回模块局部（卡片永远说「累计 0 帧」）", "core/context.py",
     '_STATUS: Dict[str, Any] = _shared_status()',
     '_STATUS: Dict[str, Any] = dict(_STATUS_DEFAULTS)',
     "test_units"),
    ("R3-7-降级车道改用实体卡形状（把面板两块塞进普通卡载荷）", "core/adapter.py",
     '                card = self._ld_build_card(visible, streaming=True,\n                                           panel=self._ld_panel(chat),\n                                           footer=self._ld_frame_footer(state))',
     '                card = _cards.cardkit_entity_card(visible, "", streaming=True,\n                                                 panel_tools_text="")',
     "test_units"),
    ("R3-6-工具块的裁减方向写反（丢掉最近的、保留最早的）", "core/cards.py",
     '        lines.append(_i18n.t("panel.trimmed", n=len(steps) - max_steps))\n'
     '        steps = steps[-max_steps:]',
     '        steps = steps[:max_steps]\n'
     '        lines.append(_i18n.t("panel.trimmed", n=len(steps) - max_steps))',
     "test_units"),
    # ---- 第十一路审计：CardKit 的三条「门禁说绿、真机说 300301」----------------- #
    ("M30-两个元素 id 撞车", "core/cards.py",
     'CARDKIT_ANSWER_ID = "answer"\nCARDKIT_PANEL_ID = "panel"\nCARDKIT_PANEL_BODY_ID = "panel_body"',
     'CARDKIT_ANSWER_ID = "answer"\nCARDKIT_PANEL_ID = "panel"\nCARDKIT_PANEL_BODY_ID = "answer"',
     "test_units"),
    ("M31-面板 body 的 id 抄错字面量", "core/cards.py",
     'CARDKIT_PANEL_BODY_ID = "panel_body"',
     'CARDKIT_PANEL_BODY_ID = "panel-body"',
     "test_units"),
    # R1 重构后，「失败被吞」的唯一入口变成 `_ld_ck_apply` 的返回值。
    ("CK27-失败那一帧不回写序号（下一帧撞同一个 uuid ⇒ 静默半更新）", "core/adapter.py",
     '                                      "ck_decor": live_state.get("ck_decor") or {},\n'
     '                                      "ck_seq": seq_after})',
     '                                      "ck_decor": live_state.get("ck_decor") or {}})',
     "test_units"),
    ("M07-正文写失败被吞（当作成功继续，不回落）", "core/adapter.py",
     '                return False, seq, answer',
     '                return True, seq, None',
     "test_units"),
    ("M07b-装饰写失败不再留痕（「正文在长、装饰冻结」没了唯一线索）", "core/adapter.py",
     '                _log_ck_decor_write_failed_once(dead_ops, batch_res.code,\n'
     '                                                blamed if hit else None)',
     '                pass',
     "test_units"),
    # ---- P6：黄金路径耗时 ---------------------------------------------------- #
    ("P6-轮耗时恒为 0", "core/panel.py",
     "    current[\"elapsed_ms\"] = max(0, int((now - float(current.get(\"started\") or now)) * 1000))",
     "    current[\"elapsed_ms\"] = 0",
     "check_hooks"),
    # R7 审计「仍未收口」的三项（每一项都实测过「撤掉之后四门禁全绿」）。
    ("R7-14-预览限频窗口被改成 1 秒（写入预算随之翻倍，附录 B 失效）", "core/adapter.py",
     '_CK_SUMMARY_INTERVAL = 5.0',
     '_CK_SUMMARY_INTERVAL = 1.0',
     "test_units"),
    ("R7-15-CardKit 请求构造器少了 settings_card（预览整条静默失效）", "core/adapter.py",
     '            settings_card=_settings_card,',
     '',
     "check_override"),
    ("R7-16-会话预览的两个模型并回同一个 import 块（老 SDK 缺它会关掉整条传输）",
     "core/adapter.py",
     '        try:\n'
     '            from lark_oapi.api.cardkit.v1 import (SettingsCardRequest,\n'
     '                                                  SettingsCardRequestBody)\n'
     '        except Exception:\n'
     '            SettingsCardRequest = SettingsCardRequestBody = None       # type: ignore[assignment]',
     '        try:\n'
     '            from lark_oapi.api.cardkit.v1 import _never_used_name_  # noqa: F401\n'
     '        except Exception:\n'
     '            SettingsCardRequest = SettingsCardRequestBody = None       # type: ignore[assignment]',
     "check_override"),
    # ---- R9：自检账本 + `/larkdeck` 命令卡 -------------------------------------------- #
    # 这一层的失败形态是「静默」的（钩子被改名 / 帧失败掉纯文本），而它存在的唯一理由就是
    # 让用户能**问**出来。所以每条都要能被门禁抓住 —— 尤其「自检自己失效」那两条。
    ("R9-1-`/larkdeck` 命令不再注册（自检入口整条消失）", "core/adapter.py",
     '            handle_cmd = register_command(\n'
     '                LARKDECK_COMMAND, _ld_command_card,\n'
     '                description=_i18n.t("cmd.description"),\n'
     '                args_hint="[status|config|help]")',
     '            handle_cmd = None',
     "check_override"),
    ("R9-2-没有记录时写「正常」（一张永远说健康的自检卡）", "core/context.py",
     '    if (not isinstance(ts, (int, float)) or isinstance(ts, bool)\n'
     '            or ts < _EPOCH_FLOOR):      # 含负数与 0：`_EPOCH_FLOOR` 见上面的理由（低-3）\n'
     '        return _i18n.t("status.none")',
     '    if (not isinstance(ts, (int, float)) or isinstance(ts, bool)\n'
     '            or ts < _EPOCH_FLOOR):\n'
     '        return "正常"',
     "test_units"),
    ("R9-3-入站心跳不再记账（心跳永远「无记录」）", "core/hooks.py",
     '        _context.note_inbound()',
     '        pass',
     "test_units"),
    ("R9-4-首发建卡不记账（短回答的回合会被报成「一次都没写」）", "core/adapter.py",
     '        if getattr(result, "success", False) and getattr(result, "message_id", ""):\n'
     '            _context.note_frame_ok()\n'
     '        return result',
     '        if False:\n'
     '            _context.note_frame_ok()\n'
     '        return result',
     "test_units"),
    ("R9-5-`send()` 报「成功」但没拿到 message_id 时也算写卡（卡在飞书侧没有落点）", "core/adapter.py",
     '        if getattr(result, "success", False) and getattr(result, "message_id", ""):',
     '        if getattr(result, "success", False) or True:',
     "test_units"),
    ("R9-6-帧失败不记账（用户问「为什么掉纯文本」时答不出来）", "core/adapter.py",
     '        _context.note_frame_fail(reason)',
     '        pass',
     "test_units"),
    ("R9-7-版本号改成写死的常量（卡片自信地报一个错的版本）", "core/adapter.py",
     '    match = _PLUGIN_VERSION_RE.search(text)',
     '    match = re.match(r"(?P<v>\\d+\\.\\d+\\.\\d+)", "1.2.3")',
     "test_units"),
    ("R9-8-不认识的命令参数被静默当成 status（用户以为参数生效了）", "core/adapter.py",
     '        if arg not in ("", "status"):\n'
     '            return "\\n".join([_i18n.t("cmd.unknown", arg=arg), _i18n.t("cmd.help")])',
     '        if False:\n'
     '            return "\\n".join([_i18n.t("cmd.unknown", arg=arg), _i18n.t("cmd.help")])',
     "test_units"),
    # ---- R9 对抗审计（2026-09-14）收口：两条**零门禁**的记账 + 心跳位置 + 口径 ------------- #
    # 这一批的来由：审计把下面 R9-9 / R9-12 两处记账换成 `pass`，**四个门禁全绿** ——
    # 而默认传输（cardkit）下那意味着 `/larkdeck status` 永远说「最近写卡：无记录」，
    # 用户眼前却躺着一张逐字卡片。所以每条记账点都必须有自己的变异。
    ("R9-9-cardkit 的 seed 建实体不记账（默认传输下首发帧等于不存在）", "core/adapter.py",
     '                _context.note_frame_ok()      # R9：建实体 + 发实体卡 = 这一帧真的有东西发出去了',
     '                pass',
     "test_units"),
    ("R9-10-cardkit 的元素写那一帧不记账（真机主路径不记，状态卡永远说「无记录」）", "core/adapter.py",
     '                _context.note_frame_ok()          # R9：元素通道这一帧真的写出去了',
     '                pass',
     "test_units"),
    ("R9-11-元素写成功但装饰拿到卡级死法那一帧不记账（降级决定落地的那一帧）", "core/adapter.py",
     '                _context.note_frame_ok()          # R9：正文元素写成功、降级决定已落地',
     '                pass',
     "test_units"),
    ("R9-12-`_ld_update_card` 成功却不记账（patch 帧 / 收尾 / 降级补写 / 非流式 edit 全丢）", "core/adapter.py",
     '            if getattr(result, "success", False):\n'
     '                # 重试次数**不**进账本：账本记的是「这一次真的写出去了」，不是「发了几次 HTTP」\n'
     '                # （口径见 `context.note_frame_ok` 与 README「写卡帧数」一条）。\n'
     '                _context.note_frame_ok()',
     '            if getattr(result, "success", False):\n'
     '                pass',
     "test_units"),
    ("R9-13-帧路径**又**记一笔（同一帧记两次：账本虚高，正是把记账下移后最容易引入的病）", "core/adapter.py",
     '                self._ld_stream_put(key, {**live_state, "ck_degrade": degrade_code, "card_id": "",\n'
     '                                          "last": text, "last_at": now, "ck_seq": seq_after,\n'
     '                                          "frames": int(state.get("frames") or 0) + 1})\n'
     '                # 正文元素**已经写成功**（`ok` 为真），所以这一帧确实有东西到了飞书；',
     '                self._ld_stream_put(key, {**live_state, "ck_degrade": degrade_code, "card_id": "",\n'
     '                                          "last": text, "last_at": now, "ck_seq": seq_after,\n'
     '                                          "frames": int(state.get("frames") or 0) + 1})\n'
     '                _context.note_frame_ok()\n'
     '                # 正文元素**已经写成功**（`ok` 为真），所以这一帧确实有东西到了飞书；',
     "test_units"),
    ("R9-14-`/stop` 的中止重绘自己再记一笔（同一帧被记两次：用户可见的那次重绘虚高）", "core/adapter.py",
     '            result = await self._ld_update_card(chat, message_id, card)\n'
     '            if result is None or not getattr(result, "success", False):\n'
     '                logger.warning("[larkdeck] 中止态卡片更新未成功（%s）",',
     '            _context.note_frame_ok()\n'
     '            result = await self._ld_update_card(chat, message_id, card)\n'
     '            if result is None or not getattr(result, "success", False):\n'
     '                logger.warning("[larkdeck] 中止态卡片更新未成功（%s）",',
     "test_units"),
    ("R9-15-「没有活跃流可收尾」被记成写卡失败（正常返回被染成故障）", "core/adapter.py",
     '        if state is None:\n'
     '            if finalize:\n'
     '                # 没有活跃流可收尾：交核心回落（send/edit 会正常发出）。这是**正常路径**',
     '        if state is None:\n'
     '            if finalize:\n'
     '                _context.note_frame_fail("没有活跃流可收尾（交核心回落）")\n'
     '                # 没有活跃流可收尾：交核心回落（send/edit 会正常发出）。这是**正常路径**',
     "test_units"),
    ("R9-16-心跳退回「回调最后一行」（兄弟模块抛异常就被吞掉）", "core/hooks.py",
     '    try:\n        _context.note_inbound()\n        source = getattr(payload.get("event"), "source", None)',
     '    try:\n        source = getattr(payload.get("event"), "source", None)',
     "test_units"),
    ("R9-17-心跳加回 `chat_id` 前置条件（载荷不全的消息不再计入心跳）", "core/hooks.py",
     '    try:\n'
     '        _context.note_inbound()\n'
     '        source = getattr(payload.get("event"), "source", None)\n'
     '        store = payload.get("session_store")',
     '    try:\n'
     '        source = getattr(payload.get("event"), "source", None)\n'
     '        if not str(getattr(source, "chat_id", "") or ""):\n'
     '            return\n'
     '        _context.note_inbound()\n'
     '        store = payload.get("session_store")',
     "test_units"),
    ("R9-18-版本读不到时静默消失（卡片看起来跟一切正常一样）", "core/adapter.py",
     '        name = "🃏 larkdeck v" + (version or _i18n.t("cmd.version_unknown"))',
     '        name = "🃏 larkdeck" + (f" v{version}" if version else "")',
     "test_units"),
    ("R9-19-`_when` 的判据退回 `ts > 0`（脏的小正数渲染成一个看着像真时刻的 1970 年日期）", "core/context.py",
     '    if (not isinstance(ts, (int, float)) or isinstance(ts, bool)\n'
     '            or ts < _EPOCH_FLOOR):      # 含负数与 0：`_EPOCH_FLOOR` 见上面的理由（低-3）',
     '    if not isinstance(ts, (int, float)) or isinstance(ts, bool) or ts <= 0:',
     "test_units"),
    ("R9-20-卡片不再说清数字是进程级累计（用户拿别人会话的失败原因查自己的卡）", "core/adapter.py",
     '        return "\\n".join([header, _i18n.t("cmd.scope")]\n'
     '                          + _ld_diagnosis_lines()\n'
     '                          + _probe_status_lines()\n'
     '                          + _context.status_lines())',
     '        return "\\n".join([header]\n'
     '                          + _ld_diagnosis_lines()\n'
     '                          + _probe_status_lines()\n'
     '                          + _context.status_lines())',
     "check_override"),
    ("R9-21-启动自检硬编码「命令已注册」（不看真实注册结果，而运维会信这句话）", "core/adapter.py",
     '        _cmd_registered = bool(COMMAND.get("registered"))',
     '        _cmd_registered = True',
     "check_override"),
    ("R9-22-兜底里的 `str(原因)` 自己抛（病态异常穿透处理器，用户什么也看不到）", "core/adapter.py",
     '        try:\n'
     '            reason = str(exc)\n'
     '        except Exception:  # pragma: no cover - 只有病态异常对象会走到\n'
     '            reason = _i18n.t("cmd.failed_no_reason")',
     '        reason = str(exc)',
     "test_units"),
    ("R9-23-help 退回绝对措辞「仅空闲态可用」（CLI / TUI 里不成立，用户会以为敲了没用）", "core/i18n.py",
     '"⚠️ 在飞书网关里，**生成回答期间**发的命令会被当成普通输入排队到回合结束"',
     '"⚠️ 仅空闲态可用。不在飞书网关里也一样。"',
     "test_units"),
    ("R9-24-写卡文案退回「累计 N 次」（把**帧数**说成 API 调用次数）", "core/i18n.py",
     '"status.frame_ok":    {ZH: "最近写卡：{when} · 累计 {n} 帧真的有写出（帧数，不是 API 调用次数）",',
     '"status.frame_ok":    {ZH: "最近写卡：{when} · 累计 {n} 次",',
     "test_units"),
    # ---- R8①② 点击路径：内联换卡的能力探测 + 失败态只弹 toast -------------------------- #
    # 这一层跑在 SDK 回调线程上：抛一次就炸掉「别人的卡」的点击；而「失败态换卡」会让一次
    # 迟到的重复点击把已确认的卡退回待答（用户可见且不可逆）。
    ("R8-1-失败态改成内联换卡（迟到点击会把已确认退回待答）", "core/adapter.py",
     '            return self._ld_toast_or_noop(kind="error", text_key="clarify.toast_failed")',
     '            return self._ld_card_response_safe({"schema": "2.0"})',
     "test_units"),
    ("R8-2-toast 类型不再区分（失败提示画成 info）", "core/adapter.py",
     '            toast.type = str(kind or "info")',
     '            toast.type = "info"',
     "test_units"),
    ("R8-3-不再探测核心能否收下卡片（老签名上直接 TypeError）", "core/adapter.py",
     '            if _compat.accepts_positional(build, 1):',
     '            if True:',
     "test_units"),
    ("R8-4-toast 只给 content 不给 i18n（非中文客户端看到中文）", "core/adapter.py",
     '            if isinstance(locales, dict) and locales:\n'
     '                toast.i18n = dict(locales)',
     '            if False:\n'
     '                toast.i18n = dict(locales)',
     "test_units"),
    ("R8-5-没有 toast 能力时不退回（点击响应变成空）", "core/adapter.py",
     '        response = self._ld_toast_response(kind=kind, text_key=text_key)\n'
     '        if response is not None:\n'
     '            return response\n'
     '        return self._ld_card_response_safe()',
     '        return self._ld_toast_response(kind=kind, text_key=text_key)',
     "test_units"),
    ("R8-6-测试把 staticmethod 还原成普通函数（污染同一进程里后续所有用例）",
     "tests/test_units.py",
     '        original_builder = adapter.LarkDeckMixin.__dict__["_ld_build_resolved_card"]',
     '        original_builder = adapter.LarkDeckMixin._ld_build_resolved_card',
     "test_units"),
    # ---- R4：卡链（超长回答封旧卡 + 开新卡，正文只写本卡那一段）------------------------- #
    # ⚠️ 锚点在 R3 收窄版落地时**重对准过一次**（那一行整行被重写成「面板两块按关键字传」）：
    #    锚点失效 = 红（本项目规矩），而它当时确实让全量跑退出码 1 —— 重对准是唯一正确处置。
    ("R4-1-新卡重放整段（用户把前半段再看一遍）", "core/adapter.py",
     '            ops = _ck_plan(visible, _panel_body, live_elems, self._ld_frame_footer(state),',
     '            ops = _ck_plan(display, _panel_body, live_elems, self._ld_frame_footer(state),',
     "test_units"),
    ("R4-2-封旧卡忘了关流式态（旧卡永远停在「正在生成」）", "core/adapter.py",
     '                                   streaming=False,',
     '                                   streaming=True,',
     "test_units"),
    ("R4-3-切点不看尾巴预算（发一张必被飞书拒的卡）", "core/adapter.py",
     '    if _card_body_bytes(text[offset + best:]) > tail_budget:\n'
     '        return None',
     '    if False:\n'
     '        return None',
     "test_units"),
    # ⚠️ 锚点在 R9/ R6a 收口后改过一次：收尾帧现在先过 `_sanitize_for_send`（卫生 + 卫生后
    # 的字节闸门）再取本卡那一段 ⇒ 变异要落在**取段**那一步上，而不是卫生那一步。
    ("R4-4-收尾帧重放整段", "core/adapter.py",
     '            tail_visible = _sanitize_for_send(display[tail_offset:])',
     '            tail_visible = _sanitize_for_send(display)',
     "test_units"),
    ("R4-5-切点不避开代码围栏（两张卡的 markdown 各自残缺）", "core/adapter.py",
     '        if text[index - 1] != "\\n" or _inside(index):',
     '        if text[index - 1] != "\\n":',
     "test_units"),
    ("R4-6-patch 车道（降级之后）重放整段", "core/adapter.py",
     '        card = self._ld_build_card(visible, streaming=True,\n'
     '                                   panel=self._ld_panel(chat, report_empty=bool(finalize)),',
     '        card = self._ld_build_card(display, streaming=True,\n'
     '                                   panel=self._ld_panel(chat, report_empty=bool(finalize)),',
     "test_units"),
    ("R4-7-降级分支（元素通道死法）重放整段", "core/adapter.py",
     '                card = self._ld_build_card(visible, streaming=True,',
     '                card = self._ld_build_card(display, streaming=True,',
     "test_units"),
    # ---- R8 对抗审计（第十二路）收口新增：每条都对应一条**曾经零判别力**的声明 --- #
    # 为什么这八条值得单列：审计自造变异实测它们**撤掉后四门禁全绿** —— 也就是说
    # 代码里那几行「声明了纪律」的语句，当时一条门禁都没看着（`docs/lessons.md` 推论 6）。
    ("R8-7-撤掉未授权用户的拦截（群里任何人都能替你答澄清）", "core/adapter.py",
     '        if not self._is_interactive_operator_authorized(open_id):',
     '        if False:',
     "test_units"),
    ("R8-8-空提交（mode=none）重回完全静默（本项目头号失败模式）", "core/adapter.py",
     '            return self._ld_toast_or_noop(kind="error", text_key="clarify.toast_empty")',
     '            return self._ld_card_response_safe()',
     "test_units"),
    ("R8-9-失败提示不再优先于「其他」提示（失效的澄清也叫用户去打字）", "core/adapter.py",
     '        if not committed:\n            logger.warning("[larkdeck] 澄清提交未生效',
     '        if not committed and not is_other:\n            logger.warning("[larkdeck] 澄清提交未生效',
     "test_units"),
    ("R8-10-撤掉内联换卡调用点的异常兜底（异常穿透 ⇒ 交回内置 ⇒ 载荷被打进会话）",
     "core/adapter.py",
     '                    return build(card_data)\n                except Exception as exc:',
     '                    return build(card_data)\n                except ():',
     "test_units"),
    ("R8-11-NO_PENDING 与 REJECTED_* 不再分流（对已消失的澄清说「请重试」）",
     "core/adapter.py",
     '                    text_key="clarify.toast_no_pending"\n'
     '                    if outcome == _compat.CLARIFY_TEXT_NO_PENDING\n'
     '                    else "clarify.toast_rejected")',
     '                    text_key="clarify.toast_rejected")',
     "test_units"),
    ("R8-12-「其他」提示退回硬编码的 2.0 文案（1.0 卡上提一个不存在的输入框）",
     "core/adapter.py",
     '            return self._ld_toast_or_noop(kind="info", text_key=self._ld_typing_text_key())',
     '            return self._ld_toast_or_noop(kind="info", text_key="clarify.toast_typing")',
     "test_units"),
    ("R8-13-退化路径（无法内联换卡）重回完全静默（用户点了没反应 ⇒ 再点吃误报）",
     "core/adapter.py",
     '        response = self._ld_toast_response(kind="success", text_key="clarify.toast_submitted")\n'
     '        if response is not None:\n'
     '            return response\n',
     '        response = None\n',
     "test_units"),
    ("R8-14-失败 toast 又变成「请重试」（再点一次永远不可能成功，与卡片现状自相矛盾）",
     "core/i18n.py",
     '    "clarify.toast_failed": {ZH: "这条澄清已被处理或已过期，无需重复点击",\n'
     '                             EN: "Already handled or expired — no need to tap again"},',
     '    "clarify.toast_failed": {ZH: "提交未生效：可能已被处理或过期，请重试",\n'
     '                             EN: "Could not submit: already handled or expired — please retry"},',
     "test_units"),
    # ---- R11-B1/B2/B3：三件通用加固 ----
    ("B3-失败分支从旧状态出发（`live_state` 里新写的键静默消失）", "core/adapter.py",
     '            self._ld_stream_put(key, {**live_state,\n'
     '                                      "last": state.get("last", ""),',
     '            self._ld_stream_put(key, {**state,\n'
     '                                      "last": state.get("last", ""),',
     "test_units"),
    # ---- R11-B2：`300315` 是**包装码** ⇒ 必须解析内层码（真机两种形状各跑两遍） ----
    # ⚠️ 判别力全压在「重复 id」那条形状上：**只看外层码**的实现会把「我们自己的 id 炒了」
    #    说成「元素到顶」，两者的修法相反（前者要提前算预算，后者是我们的 bug）。
    ("B2-1-容量满只看外层码（`300315` 两种病被判成同一种）", "core/adapter.py",
     '        return self.code == _CAPACITY_WRAPPER_CODE and self.inner_code() == _CAPACITY_CODE',
     '        return self.code in (_CAPACITY_CODE, _CAPACITY_WRAPPER_CODE)',
     "test_units"),
    ("B2-2-内层码解析不出返回 0（0 在飞书语义里是**成功** ⇒ 失败被读成成功）",
     "core/adapter.py",
     '        return int(found[-1]) or None',
     '        return int(found[-1]) if found else 0',
     "test_units"),
    ("B2-3-容量码进重试表（确定性失败变成重试：白等 ≈1.0s 再 fail-open）",
     "core/adapter.py",
     '_WRITE_RETRY_CODES = frozenset({230020, 99991400})',
     '_WRITE_RETRY_CODES = frozenset({230020, 99991400, 300315})',
     "test_units"),
    # ⚠️ 下面四条是 B2 审计的中-1 补的：审计**手工**验证过「撤掉这几处 ⇒ test_units 变红」，
    #    但它们当时**没有变异条目** —— 那就等于「行为回归只靠单点断言兜着」，一旦有人重构
    #    正则或那句文案，没有任何变异会提醒（本项目「先写变异，再写断言」的纪律）。
    ("B2-5-正则去掉 `\\b`（`ErrCode: 11310` 这种前缀粘连也被当成内层码）", "core/adapter.py",
     '_CK_INNER_CODE_RE = re.compile(r"\\bcode\\s*:\\s*(\\d+)", re.IGNORECASE)',
     '_CK_INNER_CODE_RE = re.compile(r"code\\s*:\\s*(\\d+)", re.IGNORECASE)',
     "test_units"),
    ("B2-6-内层码取第一个匹配（尾部那个权威码被前面的描述码顶掉）", "core/adapter.py",
     '        return int(found[-1]) or None',
     '        return int(found[0]) or None',
     "test_units"),
    ("B2-7-内层码解析不出时返回 0 而不是 None（0 = 成功语义 ⇒ 失败被读成成功）",
     "core/adapter.py",
     '        return int(found[-1]) or None',
     '        return int(found[-1])',
     "test_units"),
    ("B2-8-被拒原因不再分辨内层码（容量满与 id 错共用一句「被拒」⇒ 归因消失）",
     "core/adapter.py",
     '    if res.capacity_exceeded():\n        return "元素数到顶（200 是递归口径的硬墙）"',
     '    if res.capacity_exceeded():\n        return "被服务端拒绝"',
     "test_units"),
    ("尾巴-1-空面板诊断退回「每一帧都报」（建卡 seed 帧必然为空 ⇒ 每回合一条噪声淹没真凭据）",
     "core/adapter.py",
     '            if not (_body or _tools_text) and report_empty:',
     '            if not (_body or _tools_text):',
     "test_units"),
    ("B2-4-容量码当卡级死法（正文列从 FATAL 变 DEGRADE ⇒ 用户盯着一张不再更新的卡）",
     "core/adapter.py",
     '_CARD_DEATH_CODES = frozenset({300309, 300313, 300317})',
     '_CARD_DEATH_CODES = frozenset({300309, 300313, 300317, 300315})',
     "test_units"),
    # ---- R11-B1：滑窗写入守卫（两条边界 + 「稳态不该触发」）-------------------- #
    # ⚠️ 守卫的判据是**墙钟**（1 秒内 ≤10 次逻辑写），所以专用用例是**构造**出顶满的窗口来命中它；
    #    真机稳态 ≈8.2 次/秒 < 10 ⇒ 它当前不可达（那是设计：给 Phase C 的运行时 create 留余量）。
    # ⚠️ 下面四条是 **B1 审计（高-1 / 高-2 / 中-4）** 补的：审计实测「记账面被摘掉 / 正文被
    #    顺手跳过 / 守卫被整体撤掉」三种改法**四门禁全绿** —— 消费侧守住了，喂养侧没人管。
    #    对一个「给 Phase C 预留的闸门」来说，喂养侧悄悄坏掉 = 守卫退化成装饰品。
    ("B1-0-守卫被整体撤掉（恒为 allow ⇒ 滑窗形同不存在）", "core/adapter.py",
     '        if fresh and not _ck_window_allow(state_ref, time.monotonic()):',
     '        if False:',
     "test_units"),
    ("B1-4-窗口只修剪不记账（`append` 被摘掉 ⇒ 窗口永远空 ⇒ 守卫永不触发）",
     "core/adapter.py",
     '    _ck_window_prune(state_ref, now).append(now)',
     '    _ck_window_prune(state_ref, now)',
     "test_units"),
    ("B1-5-让出装饰的同一帧顺手连正文也不写（跳过提交点 ⇒ 用户永远看不到那一段）",
     "core/adapter.py",
     '            _log_ck_window_skip_once([op.element_id for op in fresh],\n'
     '                                     len(_ck_window(state_ref)))\n',
     '            _log_ck_window_skip_once([op.element_id for op in fresh],\n'
     '                                     len(_ck_window(state_ref)))\n'
     '            return True, seq, None\n',
     "test_units"),
    ("B1-6-窗口长度被改小（判据从「1 秒 ≤10」静默缩水 ⇒ 限速失效）", "core/adapter.py",
     '_CK_WINDOW_SECONDS = 1.0',
     '_CK_WINDOW_SECONDS = 0.2',
     "test_units"),
    ("B1-7-预览那一笔不入账（窗口少记 ≈0.2 次/秒 ⇒ 守卫对真实速率失明）", "core/adapter.py",
     '        if isinstance(state_ref, dict):\n'
     '            _ck_window_note(state_ref, time.monotonic())',
     '        if False:\n'
     '            _ck_window_note(state_ref, time.monotonic())',
     "test_units"),
    ("B1-1-让出的写也记账（`ck_decor` 被写上 ⇒ 去重逻辑永久跳过 ⇒ 装饰静默冻结）",
     "core/adapter.py",
     '            state_ref["ck_window_skips"] = int(state_ref.get("ck_window_skips") or 0) + 1\n',
     '            state_ref["ck_decor"] = {**sent,\n'
     '                                     **{op.element_id: op.content for op in fresh}}\n'
     '            state_ref["ck_window_skips"] = int(state_ref.get("ck_window_skips") or 0) + 1\n',
     "test_units"),
    ("B1-2-跳过被当成失败（这一帧返回 False ⇒ 核心停用本回合 native、用户掉成纯文本）",
     "core/adapter.py",
     '            _log_ck_window_skip_once([op.element_id for op in fresh],\n'
     '                                     len(_ck_window(state_ref)))\n',
     '            _log_ck_window_skip_once([op.element_id for op in fresh],\n'
     '                                     len(_ck_window(state_ref)))\n'
     '            return False, seq, None\n',
     "test_units"),
    ("B1-3-守卫无条件触发（稳态下装饰全被让出 ⇒ 面板/页脚在真机上默默掉帧）",
     "core/adapter.py",
     '        if fresh and not _ck_window_allow(state_ref, time.monotonic()):',
     '        if fresh and not False:',
     "test_units"),

    # ---- R11-A1：世代快照（「哪一份模块对象是活的」必须是读得出来的数字）----
    ("R13-1-加载序号写死（世代标记退回装饰品）", "core/panel.py",
     '_LOAD_SEQ: int = int(_SHARED["load_seq"])',
     '_LOAD_SEQ: int = 1',
     "test_units"),
    ("R13-1b-本模块序号改去读盒子里的当前值（两份模块对象报同一个数 = 世代标记失效）",
     "core/panel.py",
     '    return _LOAD_SEQ\n',
     '    return int(_SHARED.get("load_seq") or 0)\n',
     "test_units"),
    ("R13-2-拿不到 manager 时编一个数字（而不是如实说「读不到」）", "core/adapter.py",
     '        parts.append("manager=读不到（不在 Hermes 环境里）")',
     '        parts.append(f"manager={id(object())}")',
     "test_units"),

    # ---- R11-A0 / A6：进程内状态的清点（锁与容器同源、键清单一处真相、自套娃）----
    ("R12-1-面板锁退回模块局部（两世代各一把 ⇒ 互斥失效，实测能撞出字典迭代异常）",
     "core/panel.py",
     '_LOCK: threading.Lock = _SHARED["panel_lock"]',
     '_LOCK = threading.Lock()',
     "test_units"),
    ("R12-2-指标层的锁退回模块局部", "core/context.py",
     '_LOCK: threading.Lock = _shared_box()["context_lock"]',
     '_LOCK = threading.Lock()',
     "test_units"),
    ("R12-3-「正在探测」集合退回模块局部（另一份 discard 不掉 ⇒ 该模型永不再被探测）",
     "core/context.py",
     '_INFLIGHT: set = _shared_box()["ctx_inflight"]',
     '_INFLIGHT: set = set()',
     "test_units"),
    ("R12-4-注册结论退回模块局部（自检按世代给出两个不同的答案）", "core/adapter.py",
     'HOOKS: Dict[str, bool] = _panel.shared_box()["adapter_hooks"]',
     'HOOKS: Dict[str, bool] = {}',
     "test_units"),
    ("R12-5-盒子多出一个没人声明的键（键清单两处真相的最小形态）", "core/panel.py",
     '_ANSWERS_LAST: list = _SHARED.setdefault("answers_last", [""])',
     '_ANSWERS_LAST: list = _SHARED.setdefault("answers_last_typo", [""])',
     "test_units"),
    ("R12-6-合并适配器时不再剥旧层（自套娃 ⇒ `super()` 回退路径执行两遍）", "core/adapter.py",
     '    official = _official_base_class(base_cls)',
     '    official = base_cls',
     "test_units"),
    ("R12-7-只按对象同一性认「自己的层」（跨世代那份同名不同对象 ⇒ 剥不掉）",
     "core/adapter.py",
     '            if item is LarkDeckMixin or getattr(item, "__name__", "") in names:',
     '            if item is LarkDeckMixin:',
     "test_units"),
    # ---- R11-A7：正文净化（**有证据**地剥掉核心叠加的工具进度块）----
    # 用户选的是「一个核心配置都不动」⇒ 这件事只能在插件侧做，而它**有可能吞掉正文**，
    # 所以每一条修复都必须有变异守着（撤掉一处 ⇒ 至少一个门禁变红）。
     ("R11-1-正文净化整个撤掉（帧文本原样渲染）", "core/adapter.py",
      '        display = self._ld_body_text(text, chat, finalize=finalize, stream_state=state)',
      '        display = text',
      "test_units"),
    ("R11-2-工具窗口判据撤掉（没有工具事件也照剥）", "core/adapter.py",
     '    if finalize or not text or not tool_pending or not complete:',
     '    if finalize or not text or not complete:',
     "test_units"),
    ("R11-3-判据退回「按分隔符切」（模型自己写的分隔线之后的正文被吞）", "core/adapter.py",
     '    if not text.startswith(accumulated):\n'
     '        return text\n'
     '    tail = text[len(accumulated):]\n'
     '    if not tail.startswith(_CORE_PROGRESS_SEP) or len(tail) <= len(_CORE_PROGRESS_SEP):\n'
     '        return text\n'
     '    return accumulated',
     '    if _CORE_PROGRESS_SEP not in text:\n'
     '        return text\n'
     '    return text.split(_CORE_PROGRESS_SEP)[0]',
     "test_units"),
    ("R11-4-「累积完整」判据撤掉（冻结之后的真答案被吞）", "core/adapter.py",
     '    if finalize or not text or not tool_pending or not complete:',
     '    if finalize or not text or not tool_pending:',
     "test_units"),
    # R11-9（R11-A7 尾巴）：**收尾帧护栏撤掉**。这是本组里唯一「失败不可逆」的一条 ——
    # 核心对 finalize 是乐观记账（`_record_turn_final_payload` / `delivered_final_matches`），
    # 它认为送达成功、**不会再补发**；而那一帧核心发的是纯 `self._accumulated`
    # （`stream_consumer.py:791-798` 只有 `tick.is_interim` 才合成进度块），所以剥掉的
    # 只可能是**模型自己写的正文**。判别力由单测 ㉘⑦ 提供（陈旧前缀场景：累积只到前半段、
    # 模型自己写了分隔线之后继续写 ⇒ 旧四个条件全部成立 ⇒ 收尾卡里后半段消失）。
    ("R11-9-收尾帧护栏撤掉（finalize 帧也照剥 ⇒ 静默吞正文且核心不再补发）", "core/adapter.py",
     '    if finalize or not text or not tool_pending or not complete:',
     '    if not text or not tool_pending or not complete:',
     "test_units"),
    ('G1-22-条件③放宽成「子串」（帧里含累积就剥 ⇒ 会吞掉前面的正文）', 'core/adapter.py',
     '    if not text.startswith(accumulated):',
     '    if accumulated not in text:',
     'test_units'),
    ('G1-23-条件④的「分隔符之后还有内容」撤掉（裸分隔符也照剥 = 剥掉模型写的分隔线）',
     'core/adapter.py',
     '    if not tail.startswith(_CORE_PROGRESS_SEP) or len(tail) <= len(_CORE_PROGRESS_SEP):',
     '    if not tail.startswith(_CORE_PROGRESS_SEP):',
     'test_units'),
    # ⚠️ **2026-09-21 重新指向过**（审计 A 实测：`-k G2-10` 21.6s 全绿 🟢 —— 用户口径翻转后
    #    「退回基数页脚」= 退回**正确**行为，变异与现行为等价、没有判别力）。现在指向
    #    「又在 `/stop` 重绘那一帧把短码挂回去」：这是用户明确否掉的行为，必须被
    #    test_units 的整卡扫描（`test_v4_17b` / `test_stop_redraw_and_edit_message_keep_the_trace_id`）抓住。
    ('G2-10-`/stop` 重绘那一帧又挂回短码（用户明确否掉的行为）', 'core/adapter.py',
     '                card = self._ld_build_card(_sanitize_for_send(text) or " ", streaming=False,\n'
     '                                           panel=panel, footer=stopped_footer)',
     '                card = self._ld_build_card(\n'
     '                    _sanitize_for_send(text) or " ", streaming=False, panel=panel,\n'
     '                    footer=(stopped_footer or "") + f" · \\U0001f516"'
     ' f" {_ld_trace_id(message_id)}")  # G2-10 mutated',
     'test_units'),
    # ⚠️ **锚点在 2026-09-15 晚重新对准过一次**（全量跑发现 `G2-11` 是 🟢 全绿）：
    #    旧锚点是 `if self._ld_transport() == "cardkit":`，那一行在 `state is None` 的
    #    **非 finalize** 支上，而对应用例喂的是 `finalize=True` —— 它在上面那句
    #    `if finalize: return False` 就返回了，**根本走不到**这个锚点 ⇒ 变异插进去的那句
    #    「记一笔」从来没被执行过。这是「锚点唯一、却打在**另一条分支**上」的形态：
    #    `count(old) == 1` 拦不住它（旧规矩只防歧义，防不住「找对了文件、打错了分支」）。
    #    ⇒ 锚点必须落在**那条用例真的会经过的语句**上。
    ('G2-11-正常路径（没有活跃流可收尾）也记一次掉回纯文本（口径与文档相反）', 'core/adapter.py',
     '                # 就是照这个口径写的；`/stop` 重绘成功后内核若再送 finalize 帧，也落在这一支。\n'
     '                return False',
     '                # 就是照这个口径写的；`/stop` 重绘成功后内核若再送 finalize 帧，也落在这一支。\n'
     '                _context.note_plaintext_fallback("正常交还")\n'
     '                return False',
     'test_units'),
    ('G1-21-帮助文案退回「三条心跳记录」（与状态卡的六条不一致）', 'core/i18n.py',
     '版本 / 生效传输 / 钩子 / 六条记录',
     '版本 / 生效传输 / 钩子 / 三条心跳记录',
     'test_units'),
    ('G1-19-分隔符常量被改错（判据与实现同源 ⇒ 真机上静默不再剥）', 'core/adapter.py',
     '_CORE_PROGRESS_SEP = "\\n\\n---\\n"',
     '_CORE_PROGRESS_SEP = "\\n---\\n"',
     'test_units'),
    ('G1-20-自检把每一帧都记成「剥了」（凭据虚高 ⇒ 真机上失去判别力）', 'core/adapter.py',
     '                                strips=int(state.get("strips") or 0) + (1 if stripped else 0),',
     '                                strips=int(state.get("strips") or 0) + 1,',
     'test_units'),
    ('G2-9-建实体退回「按这一刻有没有数据」决定页脚元素（重启后第一回合永远没有页脚与短码）',
     'core/adapter.py',
     '                                         footer_text=("" if _cfg("footer") else None))',
     '                                         footer_text=self._ld_footer())',
     'test_units'),
    # ---- 审计 B1/B2 的守卫 ----
    ('G1-17-选项展示标签不再折叠空白（含换行的选项破坏同源 + 行首 markdown 注入）',
     'core/cards.py',
     '        pairs.append((f"{idx}. {label_text}", text))',
     '        pairs.append((f"{idx}. {text}", text))',
     'test_units'),
    ('G1-18-已答复卡的问题行退回不转义（点一下就从按字面显示变成露出语法）', 'core/cards.py',
     r'''    return card(elements=[_clarify_question_md(question),''',
     r'''    return card(elements=[md(f"\u2753 {question}"),''',
     'test_units'),
    # ---- 审计逼出来的**真泄漏**的守卫（A1/A2/A5/A7）----
    ('G1-13-ENV 值类退回不吃转义（`KEY="值"` 凭据原文完整露出）', 'core/panel.py',
     r'''    r"(?![A-Za-z0-9_])=((?:[^\\\s,;'\"]|\\.)*)")''',
     r'''    r"(?![A-Za-z0-9_])=([^\s\"',;]+)")''',
     'test_units'),
    ('G1-14-头部规则撤掉（`Cookie:` / `Authorization:` 整条漏脱）', 'core/panel.py',
     '''    text = _REDACT_HEADER_RE.sub(lambda m: m.group(1) + _REDACT_VALUE, text)\n''',
     '', 'test_units'),
    ('G1-15-家目录规则丢掉左边界（URL 里的 /home/ 被截断成不存在的 URL）', 'core/panel.py',
     r'''r"(?:^|(?<=[\s\"'=(:,]))/(?:Users|home)/[^/\s\"']+"''',
     r'''r"/(?:Users|home)/[^/\s\"']+"''',
     'test_units'),
    ('G1-16-脱敏扫描不再限量（最坏形状把 fail-closed 钩子拖到几十毫秒）', 'core/panel.py',
     '''    text = redact_inline_secrets(text[: _REDACT_SCAN_CHARS])''',
     '''    text = redact_inline_secrets(text)''', 'test_units'),
    # ⚠️ G1-11/G1-12 也是**审计逼出来的**：它的自制变异台把值类收紧成 `[^"\\]*` 时**全绿**，
    # 说明原来没有任何用例覆盖「值里带反斜杠的凭据」；而顺着它查下去发现了真正的缺陷
    # （`[^"]*` 在转义引号处提前收尾 ⇒ 半截凭据外露）。这两条一起把「值类」这件事钉住。
    ('G1-11-JSON 值类退回 `[^"]*`（转义引号处提前收尾 ⇒ 半截凭据外露）', 'core/panel.py',
     r'''    r'"\s*:\s*"((?:[^"\\]|\\.)*)"')''',
     r'''    r'"\s*:\s*"([^"]*)"')''',
     'test_units'),
    ('G1-12-JSON 值类收紧成不含反斜杠（带反斜杠的凭据完全不脱敏）', 'core/panel.py',
     r'''    r'"\s*:\s*"((?:[^"\\]|\\.)*)"')''',
     r'''    r'"\s*:\s*"([^"\\]*)"')''',
     'test_units'),
    ('G1-10-1.0 按钮的编号从 0 开始（另一处 enumerate —— 同一个隐患两处真相）',
     'core/cards.py',
     '    buttons: List[Dict[str, Any]] = []\n    for idx, choice in enumerate(choices, start=1):',
     '    buttons: List[Dict[str, Any]] = []\n    for idx, choice in enumerate(choices, start=0):',
     'test_units'),

    # ⚠️ G1-9 是**自查补的**：它最初**没有**任何断言守着 —— 卡面与下拉依然「同源」，
    # 四门禁全绿，而用户照着卡面回「2」会拿到第 1 个选项（核心按 1 基下标解析）。
    ('G1-9-选项编号从 0 开始（卡面编号与核心的 1 基解析错位 ⇒ 用户照着回数字会答错）', 'core/cards.py',
     '    pairs: List[Tuple[str, str]] = []\n    seen: set = set()\n    for idx, choice in enumerate(choices, start=1):',
     '    pairs: List[Tuple[str, str]] = []\n    seen: set = set()\n    for idx, choice in enumerate(choices, start=0):',
     'test_units'),

    # ---- 第二组（G2）续：status 的 uptime / 掉回纯文本 / 错误码 top-N ----
    ('G2-5-帧失败不再记「掉回纯文本」（用户可见的掉卡次数永远是 0）', 'core/adapter.py',
     '        _context.note_response_code(code)\n        _context.note_plaintext_fallback(reason)',
     '        _context.note_response_code(code)',
     'test_units'),
    ('G2-6-帧失败不再记错误码（top-N 永远是空的）', 'core/adapter.py',
     '        _context.note_response_code(code)\n',
     '',
     'test_units'),
    ('G2-7-`reset()` 把进程起点也清掉（uptime 永远显示「刚重启」）', 'core/context.py',
     '        "codes": {}, "code_total": 0,\n    })',
     '        "codes": {}, "code_total": 0, "started_at": time.time(),\n    })',
     'test_units'),
    ('G2-8-错误码表没有上限（坏上游能把表撑爆）', 'core/context.py',
     '            if key not in codes and len(codes) >= _MAX_CODE_KEYS:',
     '            if key not in codes and len(codes) >= 10 ** 9:',
     'test_units'),

    # ---- 第二组（G2）：澄清卡 escape + 帧页脚短码 ----
    ('G2-1-澄清卡的问题不再转义（markdown 语法被解释）', 'core/cards.py',
     '    return md(f"\\u2753 {escape_inline_md(question)}")',
     '    return md(f"\\u2753 {question}")',
     'test_units'),
    ('G2-2-可见选项列表的标签不再转义', 'core/cards.py',
     '    return "\\n".join(escape_inline_md(label) for label, _ in pairs)',
     '    return "\\n".join(label for label, _ in pairs)',
     'test_units'),
    # ⚠️ 2026-09-21 **修锚点**：老锚点只抄了这条 return 的**第一行** ⇒ 替换后留下两行悬空实参
    #    （`started=` / `status=`）⇒ **语法错误** ⇒ 五门禁只报「💥 只有崩溃」，什么都没验。
    #    （同一天还修了 V2-1 的同型问题。）锚点必须是**完整语句**。
    ('G2-3-帧页脚又挂回短码（用户口径：页脚不要这个）', 'core/adapter.py',
     '        return self._ld_footer(chat_id=str(state.get("chat_id") or ""),\n'
     '                               started=state.get("t0"),\n'
     '                               status=state.get("status"))',
     '        base = self._ld_footer(chat_id=str(state.get("chat_id") or ""),\n'
     '                               started=state.get("t0"),\n'
     '                               status=state.get("status")) or ""\n'
     '        return f"{base} · \\U0001f516 {_ld_trace_id(state.get(chr(109)+chr(101)+chr(115)+chr(115)+chr(97)+chr(103)+chr(101)+chr(95)+chr(105)+chr(100)))}"',
     'test_units'),
    ('G2-4-自检行不再报卡短码', 'core/adapter.py',
     '        snap.get("frame_ok_count"), transport, frames, strips, trace or "无",',
     '        snap.get("frame_ok_count"), transport, frames, strips, "",',
     'test_units'),

    # ---- 第一组（G1）：澄清卡可见选项 + 脚注按方言 + 工具参数脱敏 ----
    # 三项都是「计划里有 / 对方有，而我们漏了」的呈现与安全缺口，所以每条修复
    # 都必须有变异守着（撤掉一处 ⇒ 至少一个门禁变红）。
    # ⚠️ G1-4 曾经是 🟢：断言只直接调 `redact_inline_secrets`，把 `_args_preview`
    # 里那行调用整个撤掉也照样全绿 —— **验了函数、没验接线**。现在多一条走真实
    # 入口的断言。G1-5/G1-8 同理：用例原来用数字值（`4096`），JSON 规则本就匹配
    # 不到 ⇒ 变异与用例同时是空的。
    ('G1-1-卡面可见选项列表被撤掉（选项又只活在下拉里）', 'core/cards.py',
     'md(_clarify_choice_list(pairs)), selector]',
     'selector]',
     'test_units'),
    ('G1-2-卡面列表与下拉不同源（列表另算一遍、没去重）', 'core/cards.py',
     'md(_clarify_choice_list(pairs)), selector]',
     'md(_clarify_choice_list([(f"{i}. {str(c)}", str(c)) for i, c in enumerate(choices, start=1)])), selector]',
     'test_units'),
    ('G1-3-2.0 脚注退回旧文案（没有按钮的卡又说「点按钮」）', 'core/cards.py',
     '"clarify.multi_hint" if multi else "clarify.hint_2"',
     '"clarify.multi_hint" if multi else "clarify.hint"',
     'test_units'),
    ('G1-4-参数脱敏的**接线**被撤掉（函数还在、预览不调它）', 'core/panel.py',
     '    text = redact_inline_secrets(text[: _REDACT_SCAN_CHARS])',
     '    text = text  # 脱敏被撤掉',
     'test_units'),
    ('G1-5-脱敏判据从「键名以凭据词结尾」放宽成「键名含凭据词」', 'core/panel.py',
     "    r'access[_-]?key|client[_-]?secret|private[_-]?key|credential|cookie|authorization))'",
     "    r'access[_-]?key|client[_-]?secret|private[_-]?key|credential|cookie|authorization)[A-Za-z0-9_.\\-]*?)'",
     'test_units'),
    ('G1-6-Bearer 规则撤掉（Authorization 头原样露出）', 'core/panel.py',
     '    text = _REDACT_BEARER_RE.sub(lambda m: m.group(1) + _REDACT_VALUE, text)\n',
     '',
     'test_units'),
    ('G1-7-家目录前缀折叠撤掉（/Users/<名字>/ 原样露出）', 'core/panel.py',
     '    return _REDACT_HOME_RE.sub("~", text)',
     '    return text',
     'test_units'),
    ('G1-8-ENV 侧判据同样放宽（MAX_TOKENS=… 这类正常内容被涂）', 'core/panel.py',
     '    r"api[_-]?key|access[_-]?key|client[_-]?secret|private[_-]?key|credential|cookie))"\n    r"(?![A-Za-z0-9_])=((?:[^\\\\\\s,;\'\\"]|\\\\.)*)")',
     '    r"api[_-]?key|access[_-]?key|client[_-]?secret|private[_-]?key|credential|cookie)[A-Za-z0-9_]*)"\n    r"(?![A-Za-z0-9_])=((?:[^\\\\\\s,;\'\\"]|\\\\.)*)")',
     'test_units'),

    # ⚠️ 期望门禁是 **check_hooks** 而不是 test_units：这条变异改的是 `hooks.py` 的接线，
    # 而单测是直接调数据层的（它验的是「工具结束**这个事实**不许关窗口」）。
    # 谁改的谁负责 —— 归因写准，才不会被「反正变红了」糊过去（第十路审计的教训）。
    ("R11-5-工具一结束就关窗口（收尾那一帧恰好就是有进度行的帧）", "core/hooks.py",
     '        _panel.record_tool_finished(payload.get("session_id", ""),',
     '        _panel.record_answer_delta(payload.get("session_id", ""),\n'
     '                                   payload.get("turn_id", ""), "")\n'
     '        _panel.record_tool_finished(payload.get("session_id", ""),',
     "check_hooks"),
    ("R11-6-自检不再报「剥了几帧」（真机上再没有凭据）", "core/adapter.py",
     '                                strips=int(state.get("strips") or 0) + (1 if stripped else 0),\n'
     '                                trace=_ld_trace_id(message_id))',
     '                                strips=0,\n'
     '                                trace=_ld_trace_id(message_id))',
     "test_units"),
    ("R11-8-配置项 progress_lines_in_body 又变成空转（文档说的与做的不一致）", "core/adapter.py",
     '        if _cfg("progress_lines_in_body"):\n            return text\n',
     '',
     "test_units"),
    ("R11-7-钩子不传正文增量（正文累积永远为空 ⇒ 真机上永不剥）", "core/hooks.py",
     '            _panel.record_answer_delta(session_id, turn_id, payload.get("delta", ""))',
     '            _panel.record_answer_delta(session_id, turn_id)',
     "check_hooks"),
    # ⚠️ PA-1 与 R11-7 打的是**同一行**，但病不一样：R11-7 是「没传」，
    # PA-1 是「传了、但顺手规整了一下」—— 后者才是最难发现的那种：累积看着有内容、
    # 前缀判据也在跑，只是**不再与核心逐字节同源**（核心在工具轮边界会自己插一个
    # `"\n\n"`，`.strip()` 正好把它吃掉）⇒ 剥的时候少一个字节就对不上，
    # 而真机症状是「进度行偶尔留在正文里」这种看起来像玄学的东西。
    # 抓它的是 `check_hooks` 里新增的**前提核对**那一格（驱动核心真实投递链路做对照）。
    ("PA-1-正文增量入账前被「顺手规整」（累积与核心不再逐字节同源）", "core/hooks.py",
     '            _panel.record_answer_delta(session_id, turn_id, payload.get("delta", ""))',
     '            _panel.record_answer_delta(session_id, turn_id, payload.get("delta", "").strip())',
     "check_hooks"),
    # ⚠️ PA-2 守的是前提核对的**归属**（2026-09-16 对抗审计中-1 实测出来的缺口）：
    # 把正文仓库的「按会话分桶」整个拆掉（`_answer_bucket_locked` 里换成一把常量键），
    # 修改前的前提核对**照样绿** —— 因为它当时用 `answer_state("")` 读，走的是进程级
    # 「最近活跃」指针，读到的正是「上一个写过正文的桶」。修法：**先绑定 `chat_id -> session_id`
    # （真机上由 `pre_gateway_dispatch` 绑定）再按 chat 读**，并自证归属落在目标会话上。
    ("PA-2-正文仓库的按会话分桶被换成一把常量键（跨会话串台）", "core/panel.py",
     '    item = _ANSWERS.get(sid)\n'
     '    if not isinstance(item, dict) or str(item.get("turn") or "") != tid:',
     '    sid = "larkdeck-collapsed"   # 变异：拆掉按会话分桶\n'
     '    item = _ANSWERS.get(sid)\n'
     '    if not isinstance(item, dict) or str(item.get("turn") or "") != tid:',
     "check_hooks"),
    # ---- 第十三路审计（效果组 Y1b/X14/X18..Y5/Z）实测出来的断言缺口 ------------------ #
    # 这一批**全部**是「代码是对的、但没有任何判据守着」—— 撤掉修复四门禁照样全绿。
    # 共性：判据的作用域**没跨出模块自己**（拿被测常量算上界 / 拿同模块函数比对象 /
    # 只验函数不验接线 / 输入构造让错误分支恒不可达）。三种形态在本项目反复出现，
    # 所以每条都配一个变异，不许只写断言。
    ("X14a-send() 发送未成功不再记「掉回纯文本」（用户看到纯文本的主路径漏账）", "core/adapter.py",
     '            _context.note_plaintext_fallback("send 未成功")\n',
     '',
     "test_units"),
    ("X14b-send() 抛异常那条支路不再记「掉回纯文本」（且原因里没有异常类型）", "core/adapter.py",
     '            _context.note_plaintext_fallback(f"send 异常：{type(exc).__name__}")\n',
     '',
     "test_units"),
    ("X18-错误码表上限 24→2（top-N 静默退化成 top-2；原断言拿常量自己算上界）", "core/context.py",
     "_MAX_CODE_KEYS = 24",
     "_MAX_CODE_KEYS = 2",
     "test_units"),
    ("X19-账本快照退回浅拷贝（读者改快照即污染共享账本）", "core/context.py",
     '    with _LOCK:\n        return dict(_STATUS, codes=dict(_STATUS.get("codes") or {}))',
     '    with _LOCK:\n        return dict(_STATUS)',
     "test_units"),
    ("X20-账本的 codes 退回与 _STATUS_DEFAULTS 共享（清一次等于清全部）", "core/context.py",
     '        box["status"]["codes"] = {}\n',
     '',
     "test_units"),
    ("X21-掉回纯文本的原因串不再单行归一（换行把 `#` 顶到行首变成 markdown 语法）", "core/context.py",
     '    text = " ".join(str(reason or "").split())[:200]',
     '    text = str(reason or "")[:200]',
     "test_units"),
    ("Y5-_dur 的脏值护栏只剩 NaN 那一半（0/-1 编出「已运行 56 年」，未来时间戳显示「刚重启」）", "core/context.py",
     '    if started != started or started < _EPOCH_FLOOR or started > now:',
     '    if started != started:',
     "test_units"),
    # ⚠️ **2026-09-21 重新指向过**（同 G2-10：口径翻转后「退回基数页脚」= 正确行为 ⇒ 🟢 等价变异）。
    #    现在指向「收尾整卡那一帧把短码挂回去」—— 那是用户**最后看到**的卡。
    ("Y20-收尾整卡那一帧又挂回短码（用户最后看到的那张卡）", "core/adapter.py",
     '            card = self._ld_build_card(tail_visible or " ", streaming=False,\n'
     '                                       panel=self._ld_panel(chat, report_empty=True),\n'
     '                                       footer=self._ld_frame_footer(state))\n'
     '            result = await self._ld_update_card(chat, message_id, card)\n'
     '            if result is None or not getattr(result, "success", False):\n'
     '                return self._ld_stream_fail(\n'
     '                    f"收尾帧失败（{getattr(result, \'error\', \'unknown\')}）")',
     '            card = self._ld_build_card(tail_visible or " ", streaming=False,\n'
     '                                       panel=self._ld_panel(chat, report_empty=True),\n'
     '                                       footer=(self._ld_frame_footer(state) or "")'
     ' + f" · \\U0001f516 {_ld_trace_id(state.get(\'message_id\'))}")\n'
     '            result = await self._ld_update_card(chat, message_id, card)\n'
     '            if result is None or not getattr(result, "success", False):\n'
     '                return self._ld_stream_fail(\n'
     '                    f"收尾帧失败（{getattr(result, \'error\', \'unknown\')}）")',
     "test_units"),
    # ---- 探针 ⑮ 的**凭据链**（2026-09-16）：那一格是「请真人点一次」，而一击只能点一次 ——
    #      派发断了的话用户点完什么都看不到，而我们会误判成「点击没到服务端」⇒
    #      **把一次成功的真机实验读成失败**，再据此决定「澄清卡不加按钮形态」。
    ("Y21-探针点击不再留凭据（真人点完，我们拿不到任何判定依据 ⇒ 会把成功读成失败）",
     "core/adapter.py",
     '            if _cards.is_probe_value(value):      # 判据只有一处（见 cards.is_probe_value）\n                return self._ld_log_probe_click(event=event, action=action)',
     '',
     "test_units"),
    ("Y22-插件 logger 关掉向 root 传播（那行**永远到不了 agent.log**，而门禁察觉不到）",
     "core/adapter.py",
     'logger = logging.getLogger("larkdeck")\n',
     'logger = logging.getLogger("larkdeck")\nlogger.propagate = False      # 变异：关掉传播\n',
     "test_units"),
    ("Y23-探针判据从「真值性」放宽成「键存在」（探针自检与适配器分叉 ⇒ 点击静默无凭据）",
     "core/cards.py",
     '    return isinstance(value, dict) and bool(value.get(PROBE_VALUE_KEY))',
     '    return isinstance(value, dict) and PROBE_VALUE_KEY in value',
     "test_units"),
    ("Y24-脱敏扫描窗口被调到不大于展示窗口（同形状立刻变成**可见的未脱敏凭据**）",
     "core/panel.py",
     "_REDACT_SCAN_CHARS = 4096",
     "_REDACT_SCAN_CHARS = 80",
     "test_units"),
    ("Y25-澄清卡编号改成「去重后从 1 重排」（数字连续了，但卡面开始**说谎**）",
     "core/cards.py",
     '        pairs.append((f"{idx}. {label_text}", text))',
     '        pairs.append((f"{len(pairs) + 1}. {label_text}", text))   # 变异：去重后从 1 重排',
     "test_units"),
    ("P1a-1-把 `_CkOp` 失败码回填撤掉，改回对失败 op 调 `_CkResult.inner_code()`（具体元素与码丢失）",
     "core/adapter.py",
     '                code=(failed.code if failed else None))',
     '                code=(failed.inner_code() if failed else None))',
     "test_units"),
    ("P1a-2-`/larkdeck status` 不再输出能力探测摘要（探测结论重新变回被动日志）",
     "core/adapter.py",
     '                          + _probe_status_lines()\n'
     '                          + _context.status_lines())',
     '                          + []\n'
     '                          + _context.status_lines())',
     "test_units"),
    ("P1a-3-成功接管后不写 probe 快照（status 永远显示「未探测」，已接管状态不可达）",
     "core/adapter.py",
     '    report["adopted"] = True\n'
     '    PROBE_REPORT.clear()\n'
     '    PROBE_REPORT.update(report)',
     '    report["adopted"] = True',
     "test_units"),
    ("P1a-4-必需接口缺失早退时不写 probe 快照（最关键的负面结论上不了卡）",
     "core/adapter.py",
     '    report["adopted"] = False\n'
     '    PROBE_REPORT.clear()\n'
     '    PROBE_REPORT.update(report)\n'
     '    _log_probe_report(report)\n'
     '    if not report.get("ok"):',
     '    report["adopted"] = False\n'
     '    _log_probe_report(report)\n'
     '    if not report.get("ok"):',
     "test_units"),
    ("P1b-1-缺 clarify_id 的点击退回静默（用户点下去没有任何反应）",
     "core/adapter.py",
     '            return self._ld_toast_or_noop(kind="error", text_key="clarify.toast_missing_id")',
     '            return self._ld_card_response_safe()',
     "test_units"),
    ("P1b-2-未授权点击退回静默（用户不知道自己的点击被拒）",
     "core/adapter.py",
     '            return self._ld_toast_or_noop(kind="error", text_key="clarify.toast_unauthorized")',
     '            return self._ld_card_response_safe()',
     "test_units"),
    ("P1b-3-loop 未就绪退回静默（用户点了没反应、也没有提示）",
     "core/adapter.py",
     '            return self._ld_toast_or_noop(kind="error", text_key="clarify.toast_unavailable")',
     '            return self._ld_card_response_safe()',
     "test_units"),
    ("P1b-4-status 心跳行不再显示「距上次多久」（渠道静默断连失去年龄信号）",
     "core/context.py",
     '        _i18n.t("status.inbound", when=_when(snap.get("inbound_at")),\n'
     '                age=_dur(snap.get("inbound_at")),\n'
     '                n=int(snap.get("inbound_count") or 0)),',
     '        _i18n.t("status.inbound", when=_when(snap.get("inbound_at")),\n'
     '                n=int(snap.get("inbound_count") or 0)),',
     "test_units"),
    ("P1b-5-核心查找名探测永远返回 True（核心改名后 status 仍报「在位」）",
     "core/compat.py",
     '    return bool(_CORE_INTERRUPT_LOOKUP_RE.search(source))',
     '    return True',
     "test_units"),
    ("P1b-6-status 不再显示核心中断查找名状态（改名静默失灵重新无信号）",
     "core/adapter.py",
     '        _i18n.t("probe.signal", state=lookup_state),\n'
     '    ]',
     '    ]',
     "test_units"),
    ("P1b-7-字号档位不给元素加 text_size 引用（配置了档位但正文不变）",
     "core/cards.py",
     '            if tag in ("markdown", "lark_md"):\n'
     '                if "text_size" not in node:\n'
     '                    node["text_size"] = tokens[role]\n'
     '                return',
     '            if tag in ("markdown", "lark_md"):\n'
     '                if False:\n'
     '                    node["text_size"] = tokens[role]\n'
     '                return',
     "test_units"),
    ("P1b-8-CardKit 建实体不接字号档位（配置了但流式卡不变）",
     "core/adapter.py",
     '        # P1b：设备字号档位（建实体时定死 text_size；之后只写内容，不做结构性 patch）。\n'
     '        card = _cards.apply_text_profile(card, _cfg_raw("text_profile"))\n',
     '',
     "test_units"),
    ("P1b-9-普通卡建卡不接字号档位（收尾/回退卡与流式卡字号不一致）",
     "core/adapter.py",
     '        # P1b：设备字号档位只改 config.style + 元素 text_size 引用；off/未知档位不动卡片。\n'
     '        card = _cards.apply_text_profile(card, _cfg_raw("text_profile"))\n',
     '',
     "test_units"),
    # ---- P2：AP-lite 主题 / 聚合诊断 / 配置刷新 ------------------------------ #
    ("P2-1-默认主题退回 neutral（用户点单的 ap_lite 静默消失）",
     "core/adapter.py",
     '    "theme": "ap_lite",',
     '    "theme": "neutral",',
     "test_units"),
    ("P2-2-neutral 也强行加工具图标（缺省 tool_step 调用不再逐字不变）",
     "core/cards.py",
     '    if not icons:\n        return ""',
     '    if False:\n        return ""',
     "test_units"),
    ("P2-3-未知主题不再退回 neutral（把用户笔误当合法主题用）",
     "core/cards.py",
     '    return name if name in _THEME_SYMBOLS else THEME_NEUTRAL',
     '    return name if name in _THEME_SYMBOLS else THEME_AP_BUBBLE',
     "test_units"),
    ("P2-4-工具分类退回子串包含（`false` 会被误判成 read，图标与语义分叉）",
     "core/cards.py",
     '        if any(token in tokens for token in keys):',
     '        if any(key in str(name or "").lower() for key in keys):',
     "test_units"),
    ("P2-5-面板折叠标题不接思考/工具摘要（退回固定「执行详情」）",
     "core/adapter.py",
     '            if count:\n                if rounds or reasoning:',
     '            if False:\n                if rounds or reasoning:',
     "test_units"),
    # ---- CLS 观感（2026-09-17）：带颜色的状态词 / 灰色细节行 / 截断预览的有界提取 ----
    ("CLS-1-工具状态退回旧 emoji 前缀（Succeeded/Running/Failed 状态词消失）",
     "core/cards.py",
     '    line = f"{icon}**{_detail_safe(_tool_label(name, theme))}**"',
     '    line = f"{mark} {icon}{_detail_safe(_tool_label(name, theme))}"',
     "test_units"),
    ("CLS-2-工具细节退回反引号代码块（CLS 的灰色小字消失）",
     "core/cards.py",
     '        line += "\\n" + _colorize(f"↳ {detail}", "grey")',
     '        line += f"\\n`{detail}`"',
     "test_units"),
    ("CLS-3-被截断的 JSON 预览不做有界提取（命令 / skill 名字整段消失）",
     "core/cards.py",
     '        value = _preview_value(text, category)\n        if value is None:\n            return ""',
     '        value = None\n        if value is None:\n            return ""',
     "test_units"),
    ("CLS-4-工具分区标题整块丢掉（步数看不见）",
     "core/cards.py",
     '    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:\n        return ""\n    key = "panel.sec_tools_one" if count == 1 else "panel.sec_tools"',
     '    if True:\n        return ""\n    key = "panel.sec_tools_one" if count == 1 else "panel.sec_tools"',
     "test_units"),
    ("CLS-5-未知工具状态解包崩溃（cancelled 工具把整块面板带走）",
     "core/cards.py",
     '    status_text, color = _TOOL_STATUS_STYLES.get(\n'
     '        str(status or ""), (str(status or "").strip().capitalize() or "Unknown", "grey"))',
     '    status_text, color = _TOOL_STATUS_STYLES.get(str(status or ""))',
     "test_units"),
    ("CLS-6-工具耗时不挡 NaN/Inf（坏 hook 数据让面板计算报错或显示假值）",
     "core/cards.py",
     '    if not math.isfinite(ms) or ms <= 0:\n        return ""',
     '    if ms <= 0:\n        return ""',
     "test_units"),
    ("CLS-7-推理轮总耗时不再封顶（10**400 让 /1000.0 溢出）",
     "core/cards.py",
     '            total += value\n    return min(total, 86_400_000)',
     '            total += value\n    return total',
     "test_units"),
    ("CLS-8-分区标题不占面板元素额度（元素墙重新出现 2 个元素的缝）",
     "core/cards.py",
     '        heading_room = 1 if thinking_heading else 0',
     '        heading_room = 0',
     "test_units"),
    ("CLS-9-已知类别的预览退回「第一个值」兜底（把 note 当命令展示）",
     "core/cards.py",
     '    if category in _TOOL_DETAIL_KEYS:\n        return None',
     '    if False:\n        return None',
     "test_units"),
    ("CLS-10-合法 JSON 缺类别键时回退任意首标量（note 被当命令展示）",
     "core/cards.py",
     '            if value is None and (category or "") not in _TOOL_DETAIL_KEYS:',
     '            if value is None:',
     "test_units"),
    ("CLS-11-截断预览不再解 \\uXXXX（CJK 命令细节显示成字面 u4e2d）",
     "core/cards.py",
     '            if nxt == "u" and i + 5 < len(value):',
     '            if False:',
     "test_units"),
    ("CLS-12-工具动作词退回中文（CLS 英文动作词 + i18n 边界失效）",
     "core/cards.py",
     '    "read": "Read file",',
     '    "read": "读取文件",',
     "test_units"),
    ("CLS-13-neutral cancelled 退回未知 •（状态符号表与真实状态集分叉）",
     "core/cards.py",
     '                      "cancelled": "⛔", "canceled": "⛔", "timeout": "⏰", "skipped": "⏭"}',
     '                      "cancelled": "•", "canceled": "⛔", "timeout": "⏰", "skipped": "⏭"}',
     "test_units"),
    ("CLS-14-panel_color_tags 不推给 cards（D3 降级在生产不可达）",
     "core/adapter.py",
     '    _cards.set_color_tags_enabled(bool(_cfg("panel_color_tags")))',
     '    pass',
     "test_units"),
    ("CLS-15-颜色降级短路失效（关开关仍输出 <font>）",
     "core/cards.py",
     '    if not _COLOR_TAGS_ENABLED:\n        return text',
     '    if False:\n        return text',
     "test_units"),
    ("CLS-33-panel_color_tags 默认被静默关回 false（真机颜色又变纯文本）",
     "core/adapter.py",
     '    "panel_color_tags": True,',
     '    "panel_color_tags": False,',
     "test_units"),
    ("CLS-35-编号/标题等 ASCII 标记被当核心进度吞掉（符号类别护栏被放宽）",
     "core/adapter.py",
     '    if not head or not unicodedata.category(head[0]).startswith("S"):\n        return False',
     '    if not head or not (head[0].isascii() or unicodedata.category(head[0]).startswith("S")):\n        return False',
     "test_units"),
    ("CLS-36-裸围栏不再与 running terminal 交叉验证（模型首个代码块被剥空）",
     "core/adapter.py",
     '    return saw_header or "terminal" in running_names',
     '    return True',
     "test_units"),
    ("CLS-37-正文净化改用 snapshot 的其他会话工具名单（跨会话误剥模型正文）",
     "core/adapter.py",
     '            for item in _panel.answer_tools(chat):',
     '            for item in (_panel.snapshot(chat) or {}).get("tools") or []:',
     "test_units"),
    ("CLS-38-任意 emoji+ASCII 词+冒号被当核心工具行（模型 Note/Plan 首行被吞）",
     "core/adapter.py",
     '    return False\n\n\ndef _looks_like_core_progress_only',
     '    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*\\s*(?::|\\.\\.\\.|\\()", rest))\n\n\ndef _looks_like_core_progress_only',
     "test_units"),
    ("CLS-39-verb 行不再与同会话工具交叉验证（模型 `📖 Reading list` 被吞）",
     "core/adapter.py",
     '        if not (_VERB_TO_TOOLS.get(phrase, frozenset()) & name_set):\n            continue',
     '        if False:\n            continue',
     "test_units"),
    ("CLS-40-符号类别护栏失效（中文/非 emoji 头被当核心进度）",
     "core/adapter.py",
     '    if not head or not unicodedata.category(head[0]).startswith("S"):\n        return False',
     '    if not head:\n        return False',
     "test_units"),
    ("CLS-41-搜索类 verb 不要求 ` for ` 连接词（`Searching the web tutorial` 被吞）",
     "core/adapter.py",
     '            if rest.startswith(phrase + " for "):\n                return True',
     '            if rest.startswith(phrase + " "):\n                return True',
     "test_units"),
    ("CLS-42-流式空正文帧又写空格（正文占位消失，卡片像坏掉）",
     "core/adapter.py",
     '        answer_text = _cards.answer_or_pending(display, streaming) or " "',
     '        answer_text = display or " "',
     "test_units"),
    ("CLS-43-空白累积 + 前导分隔符的进度帧不再剥（真机 15:17 的 terminal 代码块回到正文）",
     "core/adapter.py",
     '        if at >= 0 and not text[:at].strip():\n'
     '            candidate = text[at + len(_CORE_PROGRESS_SEP):]',
     '        if False:\n'
     '            candidate = text[at + len(_CORE_PROGRESS_SEP):]',
     "test_units"),
    ("CLS-34-空累积的核心进度帧不再剥（terminal 代码块又画进答案）",
     "core/adapter.py",
     '    if not accumulated.strip():\n'
     '        # No **real** answer text yet ⇒ core\'s composed frame is the progress block.\n'
     '        # Usually the empty accumulated part is dropped and the frame has no separator,\n'
     '        # but core can hold whitespace-only text (e.g. a leading "\\n" delta that the\n'
     '        # scrubber kept), and then its composer emits `"\\n\\n---\\n" + progress` — the\n'
     '        # exact real-device frame of 2026-09-18 15:17 (leading rule + search lines +\n'
     '        # terminal block).  Normalise away that whitespace-only prefix before the shape\n'
     '        # check; if the prefix contains any non-whitespace (real model text) we fall\n'
     '        # through to fail-open and never cut it off.\n'
     '        candidate = text\n'
     '        at = text.find(_CORE_PROGRESS_SEP)\n'
     '        if at >= 0 and not text[:at].strip():\n'
     '            candidate = text[at + len(_CORE_PROGRESS_SEP):]\n'
     '        # Empty accumulated/text still means no answer: fail-open.  A leading\n'
     '        # separator with an empty tail is not evidence of progress by itself.\n'
     '        if candidate and _looks_like_core_progress_only(candidate, tools, running):\n'
     '            return ""\n'
     '        return text',
     '    if not accumulated.strip():\n'
     '        return text',
     "test_units"),
    ("CLS-16-STATUS_ERROR 映射成完成（❌ 执行出错静默变 ✅）",
     "core/adapter.py",
     '        _panel.STATUS_ERROR: "panel.status_error",',
     '        _panel.STATUS_ERROR: "panel.status_ok",',
     "test_units"),
    ("CLS-17-非法 \\uXXXX 不校验（负码点/半截代理崩溃）",
     "core/cards.py",
     '                if not _re.fullmatch(r"[0-9a-fA-F]{4}", hex4):',
     '                if False:',
     "test_units"),
    ("CLS-18-neutral 坏 duration 不再挡 NaN/Inf（面板整块丢失）",
     "core/cards.py",
     '    if not math.isfinite(ms) or ms <= 0:\n        return None',
     '    if ms <= 0:\n        return None',
     "test_units"),
    ("CLS-19-每轮耗时不再封顶（10**400 让轮标题溢出）",
     "core/cards.py",
     '        return f"{base} · {format_elapsed(max(0.1, min(elapsed_ms, 86_400_000) / 1000.0))}"',
     '        return f"{base} · {format_elapsed(max(0.1, elapsed_ms / 1000.0))}"',
     "test_units"),
    ("CLS-20-已知类别首键是 dict/list 时不再继续找可用标量",
     "core/cards.py",
     '                if (candidate is not None and not isinstance(candidate, (dict, list))\n'
     '                        and str(candidate).strip()):',
     '                if (candidate is not None\n'
     '                        and str(candidate).strip()):',
     "test_units"),
    ("CLS-30-小上限截断不再先剥 color 标签（切出半个 <font>）",
     "core/cards.py",
     '        if "<font" in item and len(item) > max_tool_chars:\n            item = _strip_font_tags(item)\n        lines.append(truncate(item, max_tool_chars))',
     '        lines.append(truncate(item, max_tool_chars))',
     "test_units"),
    ("CLS-21-Load skill 动作词退回中文（D2 表不再一致）",
     "core/cards.py",
     '    "skill": "Load skill",',
     '    "skill": "加载技能",',
     "test_units"),
    ("CLS-22-工具耗时 >24h 分支被跳过（耗尽时长显示成分钟数）",
     "core/cards.py",
     '    if seconds >= 86400:\n        return ">24h"',
     '    if False:\n        return ">24h"',
     "test_units"),
    ("CLS-23-_tools_heading 脏值 guard 失效（None 产生 {n} 占位）",
     "core/cards.py",
     '    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:\n        return ""',
     '    if False:\n        return ""',
     "test_units"),
    ("CLS-24-仅推理时返回带 {elapsed} 占位的标题",
     "core/adapter.py",
     '                return _i18n.i18n_text("panel.sec_thinking_plain")',
     '                return _i18n.i18n_text("panel.sec_thinking")',
     "test_units"),
    ("CLS-25-折叠标题英文复数退化（steps -> step）",
     "core/i18n.py",
     '                           EN: "💭 Thought {elapsed} · 🛠️ Tools · {n} steps"},',
     '                           EN: "💭 Thought {elapsed} · 🛠️ Tools · {n} step"},',
     "test_units"),
    ("CLS-31-success 状态词被改成 Success/grey",
     "core/cards.py",
     '    "success": ("Succeeded", "green"),',
     '    "success": ("Success", "grey"),',
     "test_units"),
    ("CLS-32-timeout 状态词被改成 Timeout/grey",
     "core/cards.py",
     '    "timeout": ("Timed out", "red"),',
     '    "timeout": ("Timeout", "grey"),',
     "test_units"),
    ("CLS-26-canceled 质量样式表被改成 Failed/red",
     "core/cards.py",
     '    "canceled": ("Cancelled", "grey"),',
     '    "canceled": ("Failed", "red"),',
     "test_units"),
    ("CLS-27-lone surrogate 不再转 U+FFFD（产出非法 UTF-8 字符）",
     "core/cards.py",
     '                if 0xD800 <= codepoint <= 0xDFFF:\n                    codepoint = 0xFFFD',
     '                if False:\n                    codepoint = 0xFFFD',
     "test_units"),
    ("CLS-28-截断 \\u 不再保留反斜杠（\\u12 变成 u12）",
     "core/cards.py",
     '            if nxt == "u":',
     '            if False:',
     "test_units"),
    ("CLS-29-skipped 退回未知 •（与 unknown 同形）",
     "core/cards.py",
     '                      "cancelled": "⛔", "canceled": "⛔", "timeout": "⏰", "skipped": "⏭"}',
     '                      "cancelled": "⛔", "canceled": "⛔", "timeout": "⏰", "skipped": "•"}',
     "test_units"),
    ("P2-6-status 卡不再渲染聚合诊断（新看板只活在被测函数里）",
     "core/adapter.py",
     '        return "\\n".join([header, _i18n.t("cmd.scope")]\n'
     '                          + _ld_diagnosis_lines()\n'
     '                          + _probe_status_lines()\n'
     '                          + _context.status_lines())',
     '        return "\\n".join([header, _i18n.t("cmd.scope")]\n'
     '                          + _probe_status_lines()\n'
     '                          + _context.status_lines())',
     "test_units"),
    ("P2-7-能力异常不再加 ⚠️（坏掉的链路看起来和健康一样）",
     "core/adapter.py",
     '            ("⚠️ " if capability_bad else "") + _i18n.t(\n'
     '                "diag.capability",',
     '            ("" if capability_bad else "") + _i18n.t(\n'
     '                "diag.capability",',
     "test_units"),
    ("P2-8-运行失败不再加 ⚠️（失败计数和零失败看起来一样）",
     "core/adapter.py",
     '            ("⚠️ " if runtime_bad else "") + _i18n.t(\n'
     '                "diag.runtime",',
     '            ("" if runtime_bad else "") + _i18n.t(\n'
     '                "diag.runtime",',
     "test_units"),
    ("P2-11-配置刷新读失败不再整次取消（留下半套新配置）",
     "core/adapter.py",
     '    if errors:\n        return _i18n.t("config.reload_failed", keys=", ".join(sorted(errors)))',
     '    if False:\n        return _i18n.t("config.reload_failed", keys=", ".join(sorted(errors)))',
     "test_units"),
    ("P2-12-刷新不再回写默认值（被官方删掉的键永远留在内存里）",
     "core/adapter.py",
     '    fresh = dict(_DEFAULTS)\n    fresh.update(found)',
     '    fresh = dict(previous)\n    fresh.update(found)',
     "test_units"),
    ("P2-13-聚合行不再显示入站年龄（静默断连失去唯一相对信号）",
     "core/adapter.py",
     '    age = _context.age_text(snap.get("inbound_at"))\n'
     '    if age == _i18n.t("status.none"):\n'
     '        return _i18n.t("diag.inbound_none")\n'
     '    return _i18n.t("diag.inbound_ago", age=age)',
     '    return _i18n.t("diag.inbound_none")',
     "test_units"),
    ("P2-15-read_terminal 被画成键盘（读取类工具与图标语义分叉）",
     "core/cards.py",
     '    ("read", ("read", "cat", "head", "tail", "open", "ls")),',
     '    ("read", ("cat", "head", "tail", "open", "ls")),',
     "test_units"),
    ("P2-16-context override 归零不传下去（reload/set 谎报生效）",
     "core/adapter.py",
     '    pinned = _cfg_int("context_max_override", 0)\n'
     '    # ⚠️ **无条件**推下去（pinned=0 ⇒ None=取消覆盖）。审计 A1 实测：只在 `if pinned`\n'
     '    # 时调用，会让「官方删键 / 归零 reload」在内存里显示成功、运行时却仍钉着旧上限，\n'
     '    # 直到重启进程 —— 正是「卡片不许撒谎」要消灭的形态。\n'
     '    _context.set_context_override(pinned or 0)',
     '    pinned = _cfg_int("context_max_override", 0)\n'
     '    if pinned:\n'
     '        _context.set_context_override(pinned)',
     "test_units"),
    ("P2-17-聊天侧只读拒绝被绕过（config set 重新摸到写入路径）",
     "core/adapter.py",
     '    if action in ("set", "write"):\n'
     '        # 明确说「不提供聊天侧写入」，而不是含糊地报「不认识」：用户要的是知道怎么做。\n'
     '        return _i18n.t("config.read_only_set")',
     '    if action in ("set", "write"):\n'
     '        return _i18n.t("config.unknown_action", arg=action)',
     "test_units"),
    ("P2-18-reload 不披露 env 遮蔽（用户以为刷新后生效了）",
     "core/adapter.py",
     '    if shadowed:\n'
     '        result += "\\n" + _i18n.t("config.reload_env_shadowed", keys=", ".join(shadowed))',
     '    if False:\n'
     '        result += "\\n" + _i18n.t("config.reload_env_shadowed", keys=", ".join(shadowed))',
     "test_units"),
    ("P2-19-空 env 吞掉 reload 提示（官方已改但卡片不提示）",
     "core/adapter.py",
     '        if (key in official and not env_active\n'
     '                and _cfg_canonical(value) != _cfg_canonical(official[key])):',
     '        if (key in official and env is None\n'
     '                and _cfg_canonical(value) != _cfg_canonical(official[key])):',
     "test_units"),
    # ---- P3：cron / 无网关 standalone sender ------------------------------- #
    ("P3-1-standalone sender 不再走卡片工厂（cron 投递退回纯文本）",
     "core/adapter.py",
     '            adapter = factory(pconfig)\n',
     '            adapter = (fallback(pconfig) if callable(fallback) else None)\n',
     "test_units"),
    ("P3-2-媒体附件被静默丢弃（有附件时不回落内置 sender）",
     "core/adapter.py",
     '        media = list(media_files or [])\n'
     '        if media:\n'
     '            # 媒体附件不在本阶段内：整条交给内置 sender（不静默丢附件）。\n'
     '            return await _fallback("larkdeck standalone media fallback unavailable")',
     '        media = list(media_files or [])\n'
     '        if False:\n'
     '            return await _fallback("larkdeck standalone media fallback unavailable")',
     "test_units"),
    ("P3-3-standalone 不补 SDK client（真机 cron 文本投递返回 Not connected）",
     "core/adapter.py",
     '        if _compat.ensure_standalone_client(adapter) is None:\n'
     '            _log_standalone_client_fallback_once()\n'
     '            return await _fallback("larkdeck standalone SDK client unavailable")',
     '        if False:\n'
     '            _log_standalone_client_fallback_once()\n'
     '            return await _fallback("larkdeck standalone SDK client unavailable")',
     "test_units"),
    # v0.7.1 V0：三配置键必须被生产读取且未实现前告警；token 表必须被 check_cardview 锁住。

    ("V0-2-show_reasoning=true 告警被静默（配置被吞）", "core/adapter.py",
     '    if enabled:\n'
     '        _warn_visual_once("show_reasoning",',
     '    if False:\n'
     '        _warn_visual_once("show_reasoning",',
     "test_units"),
    ("V0-3-card_status_header=false 告警被静默（配置被吞）", "core/adapter.py",
     '    if not enabled:\n'
     '        _warn_visual_once("card_status_header",',
     '    if False:\n'
     '        _warn_visual_once("card_status_header",',
     "test_units"),
    ("V0-4-token 表 panel_radius 漂移（check_cardview 必须红）",
     "docs/audits/v0.7.1-visual/visual-tokens.json",
     '"panel_radius": "5px"',
     '"panel_radius": "8px"',
     "check_cardview"),
    ("V0-5-删掉 _ld_show_reasoning 生产调用点（配置静默）", "core/adapter.py",
     '        show_reasoning = _ld_show_reasoning()   # §9.3：DEGRADE/旧车道同样要过这条过滤',
     '        show_reasoning = True  # V0-5 mutated（不再读配置）',
     "test_units"),
    ("V0-6-删掉 _ld_card_status_header_enabled 生产调用点（配置静默）", "core/adapter.py",
     '        _ld_card_status_header_enabled()  # V0：生产读取配置；V2 前无观感差异',
     '        pass  # V0-6 mutated',
     "test_units"),
    # ⚠️ **2026-09-21 重新指向**：老写法把整条帧路径强制成 legacy 车道 ⇒ 整套用例跑成
    #    非结构化行为，120s 超时被记成「💥 只有崩溃」（什么都没验）。改成**窄**变异：
    #    degraded 回合同样走结构化帧 —— 那正是 `engine_stamp=degraded` 这条安全网要防的事。
    ("V0-7-degraded 回合也走结构化帧（降级安全网失效）", "core/adapter.py",
     '        if engine == "structured" and not (state and state.get("engine_stamp") == "degraded"):',
     '        if engine == "structured":  # V0-7 mutated',
     "test_units"),
    ("V0-8-read 图标 token 漂移（check_cardview 必须红）",
     "docs/audits/v0.7.1-visual/visual-tokens.json",
     '"read": "file-link-text_outlined"',
     '"read": "folder_outlined"',
     "check_cardview"),
    ("V0-9-status stopped 标题 emoji 漂移（check_cardview 必须红）",
     "docs/audits/v0.7.1-visual/visual-tokens.json",
     '"title_zh": "⛔ 已停止"',
     '"title_zh": "已停止"',
     "check_cardview"),
    ("V0-10-视觉三键从 /larkdeck config 视图里消失（用户看不到自己的配置）",
     "core/adapter.py",
     '        value = _cfg_raw(key, default)',
     '        value = _cfg_raw(key, default)\n'
     '        if key in _VISUAL_TRANSITION_KEYS:\n'
     '            continue  # V0-10 mutated',
     "test_units"),
    ("V0-11-生产 tool 状态色 green 被改 blue（check_cardview 必须红）",
     "core/cards.py",
     '    "ok": ("Succeeded", "green"),',
     '    "ok": ("Succeeded", "blue"),',
     "check_cardview"),
    ("V0-12-生产 tool 状态映射 ok 键被删（check_cardview 必须断言红）",
     "core/cards.py",
     '    "ok": ("Succeeded", "green"),\n',
     '',
     "check_cardview"),
    ("V0-13-token 表 element_anchors.answer 键被删（check_cardview 必须断言红）",
     "docs/audits/v0.7.1-visual/visual-tokens.json",
     '    "answer": "answer",\n',
     '',
     "check_cardview"),
    ("V0-14-panel_header.format 漂移（check_cardview 必须红）",
     "docs/audits/v0.7.1-visual/visual-tokens.json",
     '"format": "💭 思考 {elapsed}s · 🛠️ 工具执行 · {n} 步"',
     '"format": "💭 思考 {elapsed}s · 🛠️ 工具执行 · {n} 项"',
     "check_cardview"),

    ("V0-16-edit_message 里的 header 生产调用点被删（配置静默）",
     "core/adapter.py",
     '        回答被重发一遍。判据是「这份文本是不是完整文本」，不是「这是哪条调用路径」。\n'
     '        """\n'
     '        _ld_visual_engine()\n'
     '        _ld_card_status_header_enabled()\n'
     '        _ld_show_reasoning()',
     '        回答被重发一遍。判据是「这份文本是不是完整文本」，不是「这是哪条调用路径」。\n'
     '        """\n'
     '        _ld_visual_engine()\n'
     '        pass  # V0-16 mutated\n'
     '        _ld_show_reasoning()',
     "test_units"),

    ("V1-1-structured seed 分支被关（退回 legacy 卡结构）", "core/adapter.py",
     '        if structured_view is not None:',
     '        if False:  # V1-1 mutated',
     "test_units"),
    ("V1-2-结构化面板变化时不发 partial_update（工具行不出现）", "core/adapter.py",
     '        if view.panel_enabled and signature != state.get("ck_panel_sig"):',
     '        if False:  # V1-2 mutated',
     "test_units"),
    ("V1-3-结构化帧不写 answer content（打字机停住）", "core/adapter.py",
     '        wrote = await self._ld_ck_write(card_id, _cards.CARDKIT_ANSWER_ID, answer_text, seq)',
     '        wrote = _CkResult(True, 0, "")  # V1-3 mutated',
     "test_units"),
    ("V1-4-结构化面板圆角常量漂移（check_cardview 必须红）", "core/cardview.py",
     'PANEL_RADIUS = "5px"',
     'PANEL_RADIUS = "9px"',
     "check_cardview"),
    ("V1-5-结构化面板间距常量漂移（check_cardview 必须红）", "core/cardview.py",
     'PANEL_SPACING = "4px"',
     'PANEL_SPACING = "0px"',
     "check_cardview"),
    ("V1-6-结构化帧 sequence 重置（复用/回退）", "core/adapter.py",
     '        seq = _ck_seq(state)\n'
     '        live = dict(state)',
     '        seq = 0  # V1-6 mutated\n'
     '        live = dict(state)',
     "test_units"),
    ("V1-7-entity_skeleton 把 panel 排到 answer 前（提交点顺序）", "core/cardview.py",
     '    elements.append(panel_shell(view.panel))',
     '    elements.insert(0, panel_shell(view.panel))',
     "check_cardview"),
    ("V1-8-structured finalize 不清理回合状态（后续 /stop 误伤已完成卡）",
     "core/adapter.py",
     '            self._ld_stream_pop(key)\n'
     '            self._ld_forget(str(state.get("message_id") or ""))',
     '            pass  # V1-8 mutated',
     "test_units"),
    ("V1-9-structured finalize 不关闭 streaming_mode", "core/adapter.py",
     '            final_card["config"]["streaming_mode"] = False',
     '            pass  # V1-9 mutated',
     "test_units"),
    ("V1-10-结构化 DEGRADE 不 patch 同卡（只 stamp 后冻结）", "core/adapter.py",
     '        updated = await self._ld_update_card(chat, str(state.get("message_id") or ""), fallback)',
     '        updated = None  # V1-10 mutated',
     "test_units"),
    # ⚠️ 锚点必须是**带上下文的完整语句**（2026-09-21 两次漂移的教训）：单用
    #    `self._ld_heartbeat_cancel(key)` 在文件里有 4 处；只用 `final_card = ...` 两行，
    #    替换成「插一句 pass」其实**没有删掉 cancel** ⇒ 变异变成等价、判 🟢（假绿）。
    #    这里连 `if finalize:` 与那句 V2 注释一起锚，替换后 cancel 真的消失。
    ("V2-1-finalize 不 cancel 心跳（终态后仍可能写卡）", "core/adapter.py",
     '        if finalize:\n'
     '            # V2 修复：先 cancel 心跳，再发终态 patch，避免 tick 在 patch 后落回 processing 面板。\n'
     '            self._ld_heartbeat_cancel(key)',
     '        if finalize:\n'
     '            pass  # V2-1 mutated（不 cancel 心跳）',
     "test_units"),
    ("V2-2-card_status_header=false 被忽略（状态条恒显）", "core/adapter.py",
     '            header_enabled=_ld_card_status_header_enabled(),',
     '            header_enabled=True,  # V2-2 mutated',
     "test_units"),
    ("V3-1-post_tool_call 不采集 result（Result 块永远空）", "core/hooks.py",
     '                                    result=payload.get("result"),\n'
     '                                    error_type=str(payload.get("error_type") or ""),\n'
     '                                    error_message=str(payload.get("error_message") or ""))',
     '                                    error_type=str(payload.get("error_type") or ""),\n'
     '                                    error_message=str(payload.get("error_message") or ""))',
     "test_units"),
    ("V3-2-result 入库前不脱敏（密钥上卡）", "core/panel.py",
     '            result_block = redact_inline_secrets(str(raw or ""))[:600]',
     '            result_block = str(raw or "")[:600]',
     "test_units"),

    ("V4-1-结构化面板不做步数 trim（长回合撞元素墙）", "core/adapter.py",
     '        max_steps = max(1, min(_cfg_int("max_panel_steps", 20), 20))',
     '        max_steps = 10 ** 9  # V4-1 mutated',
     "test_units"),
    # ------------------------------------------------------------- V4.1 并发收口（面板 200770）
    # 真机证据（2026-09-21 10:40:01 · 日志里只剩一个 `code=200770`）：探针
    # `tests/probe_concurrent.py` 复现出 msg = `this UUID has been recently consumed`，
    # 并给出对照「同 seq 不同 uuid / 纯并发 ⇒ 300317」（300317 是卡级死法 ⇒ 会整卡降级）。
    # 五条分别对应：帧路径不拿锁 / 心跳不跳过 / 心跳序号算错 / 心跳不回写账本 / 丢 msg。
    ("V4-2-帧路径不拿回合写锁（心跳与帧同时算号）", "core/adapter.py",
     '        async with self._ld_card_lock(key):\n            return await self._ld_stream_frame_structured_locked(',
     '        if True:  # V4-2 mutated\n            return await self._ld_stream_frame_structured_locked(',
     "test_units"),
    ("V4-3-心跳不跳过持锁拍（排队写同号）", "core/adapter.py",
     '        if lock.locked():\n            return "skip"',
     '        if False:  # V4-3 mutated\n            return "skip"',
     "test_units"),
    ("V4-4-心跳序号不加一（原地重写旧号）", "core/adapter.py",
     '            seq = _ck_seq(state) + 1',
     '            seq = _ck_seq(state)  # V4-4 mutated',
     "test_units"),
    ("V4-5-心跳不回写账本（下一拍复用同一号）", "core/adapter.py",
     '            updated["ck_seq"] = seq',
     '            updated["ck_seq"] = state.get("ck_seq")  # V4-5 mutated',
     "test_units"),
    ("V4-6-装饰失败日志不带 msg（真机只剩一个码）", "core/adapter.py",
     '                    [_CkOp("panel", "", _CK_ROLE_PANEL)], res.code, msg=res.msg)\n                return "failed"',
     '                    [_CkOp("panel", "", _CK_ROLE_PANEL)], res.code, msg="")  # V4-6 mutated\n                return "failed"',
     "test_units"),
    # ---------------------------------------------------------- V4.2 页脚（时长 / 短码 / 状态词汇）
    # 真机截图（2026-09-21 10:2x）：`✅ 已完成 · 🧠 deepseek-flash · ctx 20.5k/1m · 2%` ——
    # 用户指定的顺序是「状态 → 时长 → 模型 → ctx → 短码」，中间少 `⏱`、末尾少 `🔖`。
    ("V4-7-结构化页脚不传 started（时长那一段消失）", "core/adapter.py",
     '        base_footer = self._ld_footer(chat_id=chat, started=started, status=status) or ""',
     '        base_footer = self._ld_footer(chat_id=chat, status=status) or ""  # V4-7 mutated',
     "test_units"),
    ("V4-8-结构化页脚又挂回短码（用户口径：页脚不要这个）", "core/adapter.py",
     '            footer=base_footer,',
     '            footer=f"{base_footer} · \\U0001f516 {_ld_trace_id(message_id)}",  # V4-8 mutated',
     "test_units"),
    ("V4-9-页脚状态词表不认 completed（状态段静默消失）", "core/adapter.py",
     '        "completed": "panel.status_ok",',
     '        # "completed": "panel.status_ok",  # V4-9 mutated',
     "test_units"),
    # ------------------------------------------------- V4.4 非流式车道 + show_reasoning 全车道
    # 真机证据（用户 2026-09-21 的 /stop 回复截图）：回落车道还是旧 markdown 面板，
    # 且 show_reasoning=false 被绕过（推理正文上了卡）。
    ("V4-10-非流式车道不渲染结构化元素树（回落到旧面板）", "core/adapter.py",
     '        if _ld_visual_engine() == "structured":\n            try:\n                status = _ld_view_status(chat_id, default=status)',
     '        if False:  # V4-10 mutated\n            try:\n                status = _ld_view_status(chat_id, default=status)',
     "test_units"),
    ("V4-11-cardkit 旧车道的推理块不过滤（正文上卡）", "core/adapter.py",
     '                    include_text=show_reasoning,',
     '                    include_text=True,  # V4-11 mutated',
     "test_units"),
    ("V4-12-普通卡/降级车道不过滤推理正文", "core/adapter.py",
     '                include_reasoning_text=show_reasoning,\n                tools=steps,\n'
     '                expanded=_cfg("panel_expanded"),',
     '                include_reasoning_text=True,  # V4-12 mutated\n                tools=steps,\n'
     '                expanded=_cfg("panel_expanded"),',
     "test_units"),
    ("V4-13-纯 reasoning 字符串分支绕过过滤（正文漏上卡）", "core/cards.py",
     '    elif reasoning and include_reasoning_text:',
     '    elif reasoning:  # V4-13 mutated',
     "test_units"),
    # ------------------------------------------------- V4.5 结局色 / 配置开关 / 状态表（审计 B）
    ("V4-14-结构化收尾写死 completed（失败回合绿头）", "core/adapter.py",
     '        status = _ld_view_status(chat, default="processing" if not finalize else "completed")',
     '        status = "completed" if finalize else "processing"  # V4-14 mutated',
     "test_units"),
    ("V4-15-工具状态表缺 blocked/timeout（红变灰、词漂移）", "core/cardview.py",
     '            "blocked": ("Blocked", "red"),\n', '',
     "test_units"),
    ("V4-16-unified_panel=false 仍出面板（配置被吃掉）", "core/adapter.py",
     '            panel_enabled=bool(_cfg("unified_panel")),',
     '            panel_enabled=True,  # V4-16 mutated',
     "test_units"),
    ("V4-17-max_panel_steps 被写死（配置被吃掉）", "core/adapter.py",
     '        max_steps = max(1, min(_cfg_int("max_panel_steps", 20), 20))',
     '        max_steps = 20  # V4-17 mutated',
     "test_units"),
    # ---------------------------------------- V4.6 审计 A 报告（正文流式 / 死亡降级 / 心跳留痕 / uuid 命名空间）
    ("V4-18-结构化路径不补种答案世代（own 下正文永不流式）", "core/adapter.py",
     '        if (self._ld_body_source() == "own"\n                and int(state.get("answer_gen") or 0) == 0):',
     '        if False:  # V4-18 mutated',
     "test_units"),
    ("V4-19-正文卡级死法不降级（掉 native、卡冻住）", "core/adapter.py",
     '        if not wrote.ok and wrote.code in _CARD_DEATH_CODES:',
     '        if False:  # V4-19 mutated',
     "test_units"),
    ("V4-20-心跳把写失败伪装成 unchanged/stop", "core/adapter.py",
     '                if res.code in _CARD_DEATH_DECOR_CODES:\n                    self._ld_stream_put(key, dict(',
     '                if False:\n                    self._ld_stream_put(key, dict(',
     "test_units"),
    ("V4-21-面板 partial 与 settings 共用 uuid 命名空间", "core/adapter.py",
     'f"ld-{card_id}-p{seq}"',
     'f"ld-{card_id}-s{seq}"',
     "test_units"),
    # ------------------------------------------------- V4.7 默认翻 structured + legacy 配置退役
    ("V4-22-legacy 配置键仍能选回旧引擎（配置路径没删）", "core/adapter.py",
     '    if raw == "legacy":\n        _warn_visual_once("visual_engine-retired",',
     '    if raw == "legacy":\n        return "legacy"  # V4-22 mutated\n        _warn_visual_once("visual_engine-retired",',
     "test_units"),
    ("V4-23-默认引擎退回 legacy（plugin.yaml 与代码不一致）", "core/adapter.py",
     '    "visual_engine": "structured",',
     '    "visual_engine": "legacy",  # V4-23 mutated',
     "test_units"),
    # 审计 C 的 E1：心跳只做 `locked()` 预检、不真的持锁 ⇒ 反向交错会撞出同 (seq, uuid)
    ("V4-24-心跳只预检不持锁（反向交错撞号）", "core/adapter.py",
     '        async with lock:\n            state = self._ld_stream_get(key)',
     '        if True:  # V4-24 mutated\n            state = self._ld_stream_get(key)',
     "test_units"),
    # ---------------------------------------- V4.8 用户真机口径（占位符 / Result 大块 / 图标 token）
    ("V4-25-structured 又渲染 ⏳ 占位符（用户嫌丑）", "core/adapter.py",
     '        answer_text = visible or " "',
     '        answer_text = _cards.answer_or_pending(visible, not finalize) or " "  # V4-25 mutated',
     "test_units"),
    ("V4-26-成功步又挂 Result 大代码块（用户嫌丑）", "core/cardview.py",
     '    if step.error_block:\n        elements.append(_tool_output_div(step.error_block, "Error"))',
     '    for label, block in (("Error", step.error_block), ("Result", step.result_block)):\n'
     '        if block:\n            elements.append(_tool_output_div(block, label))  # V4-26 mutated',
     "test_units"),
    ("V4-33-折叠提示用 plain_text（真机 300313，长回合卡片必坏）", "core/cardview.py",
     '        elements.append({"tag": "markdown", "content": view.collapsed_hint,',
     '        elements.append({"tag": "plain_text", "content": view.collapsed_hint,',
     "test_units"),
    ("V4-28-结构化卡不做分级降载（长正文直接撞字节墙）", "core/adapter.py",
     '        tiers = (("ok", True, True), ("no-panel", False, True), ("bare", False, False))',
     '        tiers = (("ok", True, True),)  # V4-28 mutated',
     "test_units"),
    ("V4-27-图标匹配退回子串语义（mem0_search 被误判成 search）", "core/adapter.py",
     '        for alias, token in _cardview.ICON_ALIASES:\n'
     '            if normalized == alias or normalized.startswith(alias + "_"):',
     '        for alias, token in _cardview.ICON_ALIASES:\n'
     '            if alias in normalized:  # V4-27 mutated（退回子串匹配）',
     "test_units"),
    ("V4-34-未知工具图标回落成 tool_02（与 CLS 观感不一致）", "core/cardview.py",
     'ICON_FALLBACK = "setting-inter_outlined"',
     'ICON_FALLBACK = "tool_02"  # V4-34 mutated',
     "test_units"),














    # ---- v0.7.2 P1/P2/P3/P4：审计 A/C 指出的「绿变异」反向收口 ----------------------
    # 每一条都对应一个**实测过五门禁全绿**的写法（审计 C 的原文），现在必须实红。
    ('V4-17B-error 回合的折叠提示里挂短码（整卡扫描抓它）', 'core/adapter.py',
     '        base_footer = self._ld_footer(chat_id=chat, started=started, status=status) or ""\n'
     '        return _cardview.CardView(',
     '        base_footer = self._ld_footer(chat_id=chat, started=started, status=status) or ""\n'
     '        if status == "error" and message_id:\n'
     '            panel.collapsed_hint = (\n'
     '                f"{panel.collapsed_hint} · \\U0001f516 {str(message_id)[-6:]}").strip()\n'
     '        return _cardview.CardView(',
     'test_units'),
    ('V4-40-预加载提示删除失败也把标志清掉（提示永远摘不掉、不再重试）', 'core/adapter.py',
     '            if _hint_res.ok or _hint_res.bad_element_id() == _cardview.LOADING_HINT_ID:\n'
     '                live["ck_loading"] = False',
     '            if True:  # V4-40 mutated（不看删除结果，直接清标志）\n'
     '                live["ck_loading"] = False',
     'test_units'),
    ('V4-41-300313 一律当成「元素已删掉」（把 P0 那种子元素类型拒收吞掉）', 'core/adapter.py',
     '            if _hint_res.ok or _hint_res.bad_element_id() == _cardview.LOADING_HINT_ID:',
     '            if _hint_res.ok or _hint_res.code == 300313:  # V4-35 mutated',
     'test_units'),
    ('V4-42-加载指示退回静态图标（用户口径：会动、无文字）', 'core/cardview.py',
     '    key = spinner_img_key()',
     '    key = ""  # V4-36 mutated（退回 standard_icon，不动）',
     'test_units'),
    ('V4-43-加载指示退回文案（用户口径：无文字）', 'core/cardview.py',
     '        "text": {"tag": "plain_text", "content": " "},',
     '        "text": {"tag": "plain_text", "content": "正在加载上下文..."},  # V4-37 mutated',
     'test_units'),
    ('V4-44-`exec` 图标指向 robot（全表逐条钉住后必须红）', 'core/cardview.py',
     '    ("exec", "setting_outlined"),',
     '    ("exec", "robot_outlined"),  # V4-38 mutated',
     'test_units'),
    ('V4-45-表单提交只抄路由键（答案丢失 ⇒ 空提交 toast）', 'core/adapter.py',
     '                for key in (ACTION_KEY, "clarify_id", "session_key", "question", "answer"):',
     '                for key in (ACTION_KEY, "clarify_id"):  # V4-39 mutated',
     'test_units'),
    ('P5-出站留痕不再记录卡片成功分支（「这条以什么形态发出去」不可证伪）', 'core/adapter.py',
     '                _log_outbound("card", chat_id, content, message_id)\n', '', 'test_units'),
    ('P5-edit_message 成功分支不留痕（用户看到的每一次改写都查不到）', 'core/adapter.py',
     '                _log_outbound("edit", chat_id, content, message_id)\n', '', 'test_units'),
    ('P5-出站限流的 key 不含 chat（多会话并发时证据被吃掉）', 'core/adapter.py',
     '    key = f"outbound-{kind}-{chat_id}"',
     '    key = f"outbound-{kind}"', 'test_units'),
    ('V4-46-工具行退回 div.icon（用户 2026-09-21 明确否掉的渲染方式）', 'core/cardview.py',
     '    return {\n        "tag": "div",\n'
     '        "text": {"tag": "lark_md", "content": content, "text_size": PANEL_TEXT_SIZE},\n    }',
     '    return {\n        "tag": "div",\n'
     '        "icon": {"tag": "standard_icon", "token": step.icon_token, "color": ICON_COLOR},\n'
     '        "text": {"tag": "lark_md", "content": content, "text_size": PANEL_TEXT_SIZE},\n    }',
     'test_units'),
    ('V4-47-工具图标退化成同一个兜底 emoji（per-tool 对应关系丢失）', 'core/cardview.py',
     '    return ICON_EMOJI.get(str(token or ""), ICON_EMOJI.get(ICON_FALLBACK, "🔧"))',
     '    return ICON_EMOJI.get(ICON_FALLBACK, "🔧")  # V4-47 mutated',
     'test_units'),
    ('V4-48-fit 的 tier 把调用方关掉的装饰又打开（审计 B 阻断项：空面板/配置失效）', 'core/adapter.py',
     '            view.panel_enabled = bool(panel_on and requested_panel)\n'
     '            view.footer_enabled = bool(footer_on and requested_footer)',
     '            view.panel_enabled = panel_on\n'
     '            view.footer_enabled = footer_on',
     'test_units'),
    ('V4-49-`/stop` 重绘的终态卡不关流式态（审计 B 实测 config.streaming_mode=true）', 'core/adapter.py',
     '                card = self._ld_fit_structured_card(stopped_view)\n'
     '                # 中止重绘是**终态**：必须关掉流式态（legacy 分支的 `streaming=False` 一直在做，\n'
     '                # 结构化分支漏了 —— 审计 B 实测 `config.streaming_mode=true`）。\n'
     '                card["config"]["streaming_mode"] = False',
     '                card = self._ld_fit_structured_card(stopped_view)',
     'test_units'),
    ('V4-51-出站留痕限流窗口放宽到 1 小时（证据被压住、「每次出站留一行」不可证伪）', 'core/adapter.py',
     '_OUTBOUND_LOG_INTERVAL_S = 30.0',
     '_OUTBOUND_LOG_INTERVAL_S = 3600.0  # V4-51 mutated',
     'test_units'),
    ('V4-52-真实工具名 delegate_task 退回兜底图标（用户可见落差）', 'core/cardview.py',
     '    ("delegate", "robot_outlined"),           # delegate_task（子代理）',
     '',
     'test_units'),
    ('INST-1-install.sh 的 FILES 漏掉 core/cardview.py（--copy 装出残缺插件）', 'install.sh',
     '       core/__init__.py core/adapter.py core/cards.py core/cardview.py core/i18n.py',
     '       core/__init__.py core/adapter.py core/cards.py core/i18n.py',
     'test_units'),
    ('V4-53-面板展开状态写死收起（panel_expanded 配置被静默吞掉）', 'core/cardview.py',
     '        "expanded": bool(_expanded),',
     '        "expanded": False,  # V4-53 mutated',
     'test_units'),
    ('V4-54-页脚开关被面板绑死（panel=false + footer=true 时连页脚一起丢）', 'core/adapter.py',
     '            view.footer_enabled = bool(footer_on and requested_footer)',
     '            view.footer_enabled = bool(footer_on and requested_panel)  # V4-54 mutated',
     'test_units'),
    ('V0-7b-降级安全网只对非收尾帧生效（收尾帧又回结构化元素通道）', 'core/adapter.py',
     '        if engine == "structured" and not (state and state.get("engine_stamp") == "degraded"):',
     '        if engine == "structured" and not (state and state.get("engine_stamp") == "degraded" and not finalize):  # V0-7b',
     'test_units'),

]

#: **对照项**：行为等价的改动（合法 YAML 变体等），期望四门禁**全绿**。
#: 与 MUTATIONS 分开成两张表 —— 判断依据是它属于哪张表，不是名字里有没有某个字。
CONTROLS = [
    # 等价变异（2026-09-21 收口复核实测）：`reasoning` 与 `rounds` 在数据层是**同时非空**的
    # （`panel.record_reasoning()` 会立刻产生一个推理轮）⇒ 判据里 `or reasoning` 对
    # **可到达的状态**没有影响（实测：删掉它，那条「只有 reasoning」的断言照样绿）。
    # 留着这条对照，是为了把这个「看似有洞、其实不可达」的结论固化下来。
    ('C-对照：静态空面板判据丢掉 reasoning（该状态不可达）', 'core/adapter.py',
     '    return bool(snap.get("tools") or snap.get("rounds") or snap.get("reasoning"))',
     '    return bool(snap.get("tools") or snap.get("rounds"))',
     ''),
    # 从 MUTATIONS 搬来（它没有目标门禁 ⇒ 全绿是预期；留在 MUTATIONS 里会让
    # `--upgrade-inherited` / full 模式把它当失败，`full_audit_at` 永远刷不上）。
    ("C-对照：structured 判据不传真卡（区间内等价）", "core/adapter.py",
     '                structured_card=structured_card):',
     '                structured_card=None):  # control',
     ""),
    # 实测等价（2026-09-21 增量跑）：`ToolStepView.result_block` 在结构化渲染里**从未被读**
    # （`grep -n result_block core/cardview.py` 只有 dataclass 字段声明那一行）⇒ 传空串不改行为。
    ("C-对照：工具块不传 result_block（结构化渲染里这个字段是死的）", "core/adapter.py",
     '                result_block=_cards.truncate(str(item.get("result_block") or ""), cap),',
     '                result_block="",',
     ''),
    # ---- v0.7.2 增量跑判定的「等价变异」（从 MUTATIONS 移来；判 🟢 是**正确**结果）---------
    # 共同点：`visual_engine=legacy` 自 v0.7.1 起已退役，`_ld_visual_engine()` 现在**永远**返回
    # structured —— 那几处调用只剩「退休告警」这一个副作用，删掉调用 / 改兜底默认值都不改行为。
    # 它们曾挂在 MUTATIONS 里，每次全量都报 🟢（「断言没有判别力」）—— 那是拿等价变异考门禁。
    ('C-对照：`_ld_visual_engine()` 兜底默认改 legacy（只多一条退休告警，行为不变）',
     'core/adapter.py',
     '    raw = str(_cfg_raw("visual_engine") or "structured").strip().lower()',
     '    raw = str(_cfg_raw("visual_engine") or "legacy").strip().lower()',
     ''),
    ('C-对照：删掉 supports_native_streaming 里的 visual_engine 调用点（返回值未用，只剩告警）',
     'core/adapter.py',
     '        _ld_visual_engine()  # V0：公共探测入口也读一次，覆盖 native 关闭/早退路径',
     '        pass  # 等价',
     ''),

    # 这两条「撤掉也全绿」是**结果等价**，不是门禁漏洞：外层的 try/fail-open 链
    # （`send_stream_frame` 的 except → `_ld_stream_fail` → 返回 False）会把它们接住，
    # 结果同样是「frame 返回 False、核心回落」—— 不变量 2（绝不丢消息）在两种形态下都成立。
    ("C-对照：建实体失败仍当作成功（被 message_id 检查 + 外层 fail-open 接住）",
     "core/adapter.py",
     "                if made is None:\n                    # 任何一步失败都交给核心回落（这是**契约**：帧失败 ⇒ 本回合改走 edit/send）\n                    return self._ld_stream_fail(\"CardKit 建实体/发实体卡失败\")",
     "                if made is None:\n                    made = (self._ld_send_card, \"\")", ""),
    ("C-对照：没有 SDK 时不显式 fail-open（被外层 except 接住）", "core/adapter.py",
     "        reqs = self._ld_ck_requests()\n        if reqs is None:\n            return None                      # 没有 SDK ⇒ fail-open 回落（不猜、不抛）",
     "        reqs = self._ld_ck_requests()", ""),
    ("C-对照：default 加行内注释（合法 YAML）", "plugin.yaml",
     "    default: 15\n", "    default: 15  # 毫秒\n", ""),
    # R7 审计中-3 的 ④：预览的「内容没变就不发」被撤掉 ⇒ 仍然全绿，因为限频窗口先挡住了
    # （5 秒内最多多写一次同样内容）。结果等价，不是门禁漏洞 —— 但**写在这里**，
    # 这样下一个人不用再花一轮实验去发现它。
    ("C-对照：预览的内容去重被撤（限频先挡住了，结果等价）", "core/adapter.py",
     '        if not text or text == state_ref.get("ck_summary"):',
     '        if not text:',
     ""),
    # ⑦：失败时也顺手更新 `ck_summary_at` ⇒ 无害（`ck_summary_dead` 已经短路了后续一切尝试）。
    ("C-对照：预览失败时也更新限频戳（dead 已短路，结果等价）", "core/adapter.py",
     '            fields = {"ck_summary_dead": True}\n'
     '            _log_ck_summary_failed_once(res.code)',
     '            fields = {"ck_summary_dead": True, "ck_summary_at": now}\n'
     '            _log_ck_summary_failed_once(res.code)',
     ""),
    # 进度表的**合法改写**：阶段名后面加全角括号，信息一个字都没少 —— 旧门禁把
    # `"| R5 "`（含半角空格）当字面量比对，于是这里会**假红**（审计低-6 实测）。
    ("C-对照：进度表阶段名后加全角括号（信息等价，旧门禁在这里假红）", "docs/plan-v1.md",
     '| R5 健壮性 |', '| R5（健壮性） |', ""),
    ("C-对照：纯注释改动", "core/adapter.py",
     "#: 卡片按钮 value 里的动作键；只认自己这一个，其余一律回落给内置实现。",
     "#: 卡片按钮 value 里的动作键；只认自己这一个，其余一律回落给内置实现。（注释改动）", ""),
]


def _prepare(dest_parent: Path) -> Path:
    """把工作树拷成 ``<dest_parent>/larkdeck``（目录名必须是 larkdeck，见模块 docstring）。"""
    dest = dest_parent / "larkdeck"
    # ⚠️ `.deploy` 必须排除（2026-09-21 实测量出来的）：它是部署 worktree，里面有**整份源码**
    #    ⇒ 每份拷贝白多 ~5.5MB（占 ~40%），全量跑下来光 /tmp 就能堆到 3GB+，还把磁盘拖慢。
    shutil.copytree(REPO, dest, ignore=shutil.ignore_patterns(
        ".git", ".deploy", "__pycache__", "*.pyc", ".pytest_cache", "docs/deliveries"))
    return dest


#: 门禁失败的两种形态必须分开看：**断言失败**是判别力证据，**崩溃**（语法错误 / import 炸）
#: 只是「你把代码弄坏了」—— 第八路审计实测：语法错误型变异会让四个门禁全红
#: （`invalid syntax (adapter.py, line 99)`），如果把它也算「抓住了」，那这个验证器就在骗人。
#: 「代码被弄坏了、门禁根本没跑到断言」的标志。**刻意不含裸的 `Traceback`** ——
#: `test_units.py` 在**正常通过**时也会打印 traceback（它有意覆盖异常回落路径，
#: 实测一次绿跑里就有两处），所以拿它当崩溃信号会把每一轮都误判成崩溃。
#: `Failed to load plugin` 与语法错误措辞是第十路审计实测出来的：变异把源码弄成语法错误时，
#: `check_override` / `check_hooks` 捕获加载器异常后只打自己的友好文案，光看它们的
#: `FAIL: ` 分辨不出「加载失败」与「断言失败」。
_LOAD_FAIL_MARKERS = ("Failed to load plugin", "SyntaxError", "invalid syntax",
                      "IndentationError", "expected ':'", "ImportError",
                      "ModuleNotFoundError")
#: 每个门禁**通过时**必须打印的那一行（用来判 green）。第十路审计指出：只看退出码
#: 会把「门禁根本没跑起来」当成通过 —— 必须要求它自己的收尾语出现。
_PASS_MARKERS = {
    "test_units.py": "passed",
    "check_override.py": "OVERRIDE OK",
    "check_hooks.py": "HOOKS OK",
    "check_clarify_e2e.py": "CLARIFY E2E OK",
    "check_cardview.py": "CARDVIEW OK",
    # 图标表 vs **CLS 源码**（审计 C2 实测：把生产表与我们的冻结 JSON 同时改坏时，前五支全绿
    # ⇒ 只有「直连 CLS」这一支能抓。它以前躺在五门禁之外 ⇒ 等于可被形式化满足。）
    "check_cls_alignment.py": "CLS ALIGN OK",
}

#: 每个门禁**失败时**会打的标记（断言失败/测试报错）。缺了它就说明门禁没跑到断言那一步
#: —— 而 check_override/check_hooks 捕获加载器异常后只打自己的友好文案
#: （`FAIL: Failed to load plugin … invalid syntax`），所以**不能**把它们的输出当成
#: 「有断言失败」。第十路审计实测：纯语法错误型变异曾被算成判别力证据。
_FAIL_MARKERS = {
    # 只认断言失败；裸 `ERROR `（未捕获异常）是崩溃，不能算判别力证据。
    "test_units.py": ("AssertionError", "FAIL  "),
    "check_override.py": ("FAIL: ",),
    "check_hooks.py": ("FAIL: ",),
    # ⚠️ 这个门禁**不打** `FAIL: `（带冒号）：它 `check()` 里的逐条失败是 `FAIL  `（两个空格，
    # `check_clarify_e2e.py` 的 check 函数）加上收尾的 `FAILED: N 项 -> …`。
    # 第十二路审计（R8）实测：原来这里配的是 `("FAIL: ",)` ⇒ **永远匹配不上** ⇒
    # 这个门禁的**任何断言失败都被 `_classify` 算成 `red-crash`**。三个后果：
    # ① 不会造成假绿（green 要求看到 `CLARIFY E2E OK` 且退出码 0）；
    # ② 报告里的「断言红」列对这条门禁**恒为空**，按这一列做归因会一直错；
    # ③ 当某个变异**只有这个门禁抓得住**时，`main()` 走 `elif not evidence:` ⇒ 退出码 1，
    #    人会去找一个不存在的问题。
    # 实测复现（变异 `R8-1`，它只被这个门禁抓住）：改标记之前那一行是
    # `💥 只有崩溃 …算判别力证据`，改之后是 `🔴 断言失败 … 断言红=['check_clarify_e2e.py']`。
    "check_clarify_e2e.py": ("FAIL  ", "FAILED:"),
    "check_cardview.py": ("AssertionError",),
    "check_cls_alignment.py": ("CLS 对齐 FAIL", "AssertionError"),
}


def _classify(script: str, proc: "subprocess.CompletedProcess") -> str:
    """``red-assert``（真有判别力）/ ``red-crash``（没跑到断言，不算证据）/ ``green``。

    判据（第十/第九路审计两轮修正后的形态）：

      * **green** 必须看到该门禁自己的收尾语（``OVERRIDE OK`` / ``HOOKS OK`` / ``passed``…）
        —— 光看退出码会把「根本没跑起来」算成通过；
      * **red-assert** 要求看到该门禁的**失败标记**（``AssertionError`` / ``FAIL  `` /
        ``FAIL: ``）。三个 ``check_*`` 在**加载失败**（语法错误等）时只打自己的友好文案、
        不打断言标记，于是那种变异会落到下面一支；
      * **red-crash** = 非零退出 + 没有任何失败标记，或输出里有崩溃标记（traceback /
        SyntaxError / ImportError），或 ``test_units.py`` 输出里有行首 ``ERROR ``（自研
        runner 用它表示未捕获异常）。**崩溃不是判别力证据** —— 它只说明「你把代码弄坏了」，
        而这一批的教训正是：把崩溃算成「被门禁抓住」会掩盖真正的假绿。
    """
    blob = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0 and _PASS_MARKERS[script] in blob:
        return "green"
    # ⚠️ 审计 C（2026-09-17）实测的隐藏崩溃：test_units 的一次运行可能同时打
    # `FAIL  ` 和行首 `ERROR `（一个测试断言失败、另一个未捕获异常）。如果只按
    # 「有 FAIL 就算断言红」，这条运行会被记成纯断言红，manifest 的 crash=0
    # 就会被读成「0 个测试崩溃」—— 实际不是。行首 `ERROR ` 一律算 red-crash，
    # 即使同一份输出里还有别的断言失败。
    if script == "test_units.py" and any(line.startswith("ERROR ") for line in blob.splitlines()):
        return "red-crash"
    broken = any(marker in blob for marker in _LOAD_FAIL_MARKERS)
    asserted = any(marker in blob for marker in _FAIL_MARKERS[script])
    # ⚠️ **结构性判据优先于子串判据**（2026-09-15 实测的误判，方向与假绿相反但同样是错）：
    #    `test_units.py` 的**正常输出**里就可能出现裸的 `ImportError` —— 它有测试专门覆盖
    #    「拿不到 SDK ⇒ fail-open」。于是 `broken` 为真，一次**真的断言失败**会被判成
    #    「💥 只有崩溃、不算判别力证据」⇒ **把有牙的变异记成没牙**（实测：G1-19 就是被这样
    #    误判的，手工复现明明是干净的 AssertionError + `195/196 passed`）。
    #    判据改成：**先问「这个门禁到底跑起来了吗」** —— `test_units.py` 跑起来就会打
    #    `N/M passed` 这个收尾语，三个 `check_*` 则各打自己的 OK 语。没跑起来（源码都没加载）
    #    时它们**不会**打收尾语，所以上一段那条「加载失败会被当成断言失败」的防线仍然成立。
    _ran = (_PASS_MARKERS[script] in blob)
    if asserted and _ran:
        return "red-assert"
    # 「没跑到断言」有两种：压根没打失败标记，或者源码根本没加载起来（后者会让
    # `check_*` 打出一串看起来像断言的 `FAIL: ` 文案 —— 第十路审计实测的那种假证据）。
    if broken or not asserted:
        return "red-crash"
    return "red-assert"


#: 单支门禁的硬超时（秒）。⚠️ 汇总串里的数字必须用它算，别写死（终审 A 实测曾写死 120）。
_GATE_TIMEOUT_S = 45.0

GATE_ORDER = ["test_units.py", "check_override.py", "check_hooks.py",
              "check_clarify_e2e.py", "check_cardview.py", "check_cls_alignment.py"]


def _run_one_gate(repo: Path, script: str) -> "tuple[int, str]":
    """跑单支门禁（**有界**：`_GATE_TIMEOUT_S`（45s）硬超时 —— 与「禁 sleep、等待必须有界」同一条纪律）。"""
    try:
        proc = subprocess.run([sys.executable, str(repo / "tests" / script)],
                              capture_output=True, text=True, cwd=str(repo.parent),
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                              # 超时是**中止**、不是判定（💥 不记账，留给 `--only` 定向复核）。
                              # 120s 时实测有变异把整轮拖到小时级（用户明确要求提速）⇒ 45s。
                              timeout=_GATE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return ("red-crash", f"超时 >{_GATE_TIMEOUT_S:.0f}s：{script}")
    tail = (proc.stdout or proc.stderr).strip().splitlines()
    return (_classify(script, proc), tail[-1] if tail else "")


#: 增量验证账本（提交进库）：`变异名 -> {fp, verdict, gate, at}`。
#: 为什么要有它（用户 2026-09-21 直接质疑「每次跑一两个小时的所谓全量矩阵」）：
#: 全量矩阵的**信息价值**只在「这段代码区域自上次判定以来变过吗」——没变过的区域，
#: 上次的判定仍然成立。所以指纹按**锚点所在区域**（±_FP_WINDOW 行）算，不是整文件：
#: 改一处无关注释不该让 475 条变异全部重跑。
#: 账本路径可用环境变量覆盖（分片并行时各写各的，最后合并 —— 避免两个进程互相覆盖）。
LEDGER_PATH = pathlib.Path(os.environ.get("LARKDECK_LEDGER_PATH")
                           or (REPO / "tests" / "mutation-verdicts.json"))
_FP_WINDOW = 15


def _anchor_region(rel: str, old: str) -> "str | None":
    """锚点所在代码区域（±_FP_WINDOW 行 + 锚点自身）。锚点找不到 ⇒ None（preflight 会报）。

    ⚠️ **必须按完整 `old` 定位**（终审 C 实测 22/470 条记录取错位置）：早先只看 `old` 的
    **第一行**，于是像 `        try:` 这种首行在文件里多处出现的锚点会命中**别处**的代码 ——
    指纹算的是那一处的区域，真正被打入变异的那一段怎么改都不会被发现（`--delta` 照报「已验」，
    而门禁其实是红的）。现在改成：在整份文件文本里找 `old` 的偏移，再换算成行号取窗口。
    """
    try:
        text = (REPO / rel).read_text(encoding="utf-8")
    except OSError:
        return None
    idx = text.find(old)
    if idx < 0:
        return None
    lines = text.splitlines()
    span = max(1, len(old.splitlines()))
    start_line = text.count("\n", 0, idx)          # 0-based 行号
    lo = max(0, start_line - _FP_WINDOW)
    hi = min(len(lines), start_line + span + _FP_WINDOW)
    return "\n".join(lines[lo:hi])


def _fingerprint(rel: str, old: str, new: str) -> str:
    region = _anchor_region(rel, old)
    if region is None:
        return "anchor-missing"
    h = hashlib.sha256()
    h.update(region.encode("utf-8"))
    h.update(b"\x00")
    h.update(old.encode("utf-8"))
    h.update(b"\x00")
    h.update(new.encode("utf-8"))
    return h.hexdigest()[:16]


def _head_short() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO),
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return ""


def _region_at_ref(rel: str, old: str, ref: str) -> "str | None":
    """`git show <ref>:<rel>` 里的**同一段**区域（用于「继承上次发布的判定」）。

    为什么需要（用户 2026-09-21：「就没有省时一点、聪明一点的办法吗」）：全量矩阵的
    信息价值只在「这段区域变过吗」——
      * 区域在 `ref` 与 HEAD 逐字节相同 ⇒ **上次发布时跑的那一轮**对本区域仍然成立，
        标 `inherited(ref)`（**可审计**：账本里写明继承自哪个 ref；它不是「跑过了」）；
      * 区域变了（或变异是新增的）⇒ 必须现在跑。
    周期性全量审计仍然要做（大版本 / 账本里的 `full_audit_at` 过期时），它不是每版必跑。
    """
    try:
        blob = subprocess.run(["git", "show", f"{ref}:{rel}"], cwd=str(REPO),
                              capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return None
    if not blob:
        return None
    lines = blob.splitlines()
    first = (old.splitlines() or [old])[0]
    span = max(1, len(old.splitlines()))
    for i, line in enumerate(lines):
        if first in line:
            lo = max(0, i - _FP_WINDOW)
            hi = min(len(lines), i + span + _FP_WINDOW)
            return "\n".join(lines[lo:hi])
    return None


def _mutation_names_at_ref(ref: str) -> "set[str]":
    """`git show <ref>:tests/mutate_check.py` 里当时存在的变异名（用于继承资格校验）。"""
    try:
        blob = subprocess.run(["git", "show", f"{ref}:tests/mutate_check.py"], cwd=str(REPO),
                              capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return set()
    return set(re.findall(r'^\s*\(["\']([^"\']+)["\']', blob, re.M))


def _seed_inherited(ref: str) -> int:
    """把「区域自 `ref` 以来没变」的变异标成 `inherited(ref)`（不覆盖已 red-assert 的条目）。

    ⚠️ 资格校验（终审 C 的蛰伏路径）：**该 ref 的清单里必须已经有这条变异** —— 否则
    「新加的变异 + 它的区域恰好没变」会被直接标成已验，之后 `--delta` 永远跳过它
    （而我们从来没在那一版跑过它）。
    """
    entries = _load_ledger()
    at_ref = _mutation_names_at_ref(ref)
    stamped = 0
    for name, rel, old, new, _expect in MUTATIONS:
        if at_ref and name not in at_ref:
            continue
        rec = entries.get(name) or {}
        fp_now = _fingerprint(rel, old, new)
        if rec.get("verdict") == "red-assert" and rec.get("fp") == fp_now:
            continue
        if rec.get("verdict") == "inherited" and rec.get("fp") == fp_now \
                and rec.get("at") == ref:
            continue
        region_now = _anchor_region(rel, old)
        region_ref = _region_at_ref(rel, old, ref)
        if region_now is not None and region_ref is not None and region_now == region_ref:
            entries[name] = {"fp": fp_now, "verdict": "inherited", "gate": "",
                             "at": ref, "note": "区域自该 ref 以来逐字节未变"}
            stamped += 1
    _save_ledger(entries)
    return stamped


def _gate_cases(gate: str) -> str:
    """门禁文件里**用例名集合**（`def test_xxx`）的指纹 —— 继承判定的测试侧那一半。

    为什么不用整文件指纹（2026-09-21 实测的教训）：整文件指纹太脆 —— **加一个新用例**就会
    让 406 条 `test_units` 变异全部作废重跑，而**加用例只可能增强抓取力、不可能把红变绿**。
    真正会让旧判定失效的只有「当年抓住它的用例被**删除/改名/削弱**」。
    ⇒ 判据改成：**当年那批用例名今天还在吗**。删/改名 ⇒ 重跑；只加新用例 ⇒ 继续跳过
    （削弱同名用例的函数体是**已披露的残余**，由周期性全量直跑兜底）。
    `check_*` 脚本没有 `def test_`，退回整文件指纹（它们很少改）。
    """
    path = REPO / "tests" / (gate if gate.endswith(".py") else gate + ".py")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "missing"
    names = re.findall(r"^def (test_\w+)", text, re.M)
    if not names:
        return "sha:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return "cases:" + ",".join(sorted(names))


def _gate_fp(gate: str) -> str:
    """门禁文件自身的指纹 —— 继承判定的**测试侧**那一半。

    为什么必须有（本轮协议的自审）：只按**生产代码区域**继承有个洞 —— 某条变异当年可能
    只被一个**后来被改写**的用例抓住，今天它已经绿了，而我们还在拿旧结论跳过它。
    修法：每条 **red-assert** 记账时同时记下**当时那个门禁文件**的指纹；`--delta` 只在
    「代码区域指纹 + 门禁文件指纹**都对上**」时才跳过。
    ⚠️ 历史遗留的 `inherited(...)` 条目没有门禁指纹（当年没记）⇒ 它们仍按代码区域跳过，
    这是**已披露的残余风险**，由周期性全量直跑兜底（`--ledger-status` 会单独报这个数）。
    """
    if not gate:
        return ""
    path = REPO / "tests" / (gate if gate.endswith(".py") else gate + ".py")
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


#: 门禁真正依赖的**测试侧 helper / fixture**（终审 A 的反例：只把 `write_golden_trace.py` 的
#: `--check` 改成直接 return 0，就能让一条真·该红的变异变绿，而账本仍按「门禁文件指纹」跳过它）。
#: 这些文件变了 ⇒ 所有依赖它们的判定一律作废重跑（它们本来就只在「行为有意变更」时才会动）。
_HELPER_FILES = (
    "tests/write_golden_trace.py",
    "tests/golden_cardkit_trace.json",
    "docs/audits/v0.7.2/tool-icons.json",
    "docs/audits/v0.7.2/footer-contract.json",
)


def _helper_fp() -> str:
    """helper / fixture 内容的指纹（缺文件记 `missing`，不静默当相等）。"""
    h = hashlib.sha256()
    for rel in _HELPER_FILES:
        path = REPO / rel
        try:
            h.update(path.read_bytes())
        except OSError:
            h.update(b"missing")
        h.update(b"\x00")
    return h.hexdigest()[:16]


def _load_ledger() -> dict:
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8")).get("entries") or {}
    except Exception:
        return {}


def _save_ledger(entries: dict, *, full_audit_at: str = "") -> None:
    meta = {"_note": "变异验证账本：fp = 锚点区域的指纹（±15 行 + old/new）。"
                     "fp 未变 ⇒ 该区域的判定仍然成立，增量模式跳过。"
                     "`full_audit_at` = 最后一次**不做增量、全量直跑**的提交——"
                     "继承判定（inherited）的兜底就是它，应该周期性刷新。"}
    try:
        meta["full_audit_at"] = json.loads(LEDGER_PATH.read_text(encoding="utf-8")).get(
            "_meta", {}).get("full_audit_at", "")
    except Exception:
        meta["full_audit_at"] = ""
    if full_audit_at:
        meta["full_audit_at"] = full_audit_at
    LEDGER_PATH.write_text(json.dumps({
        "_meta": meta, "entries": dict(sorted(entries.items())),
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _delta_split(picked: list, entries: dict) -> "tuple[list, list]":
    """把选中变异分成 (需要重跑, 已验可跳过)。只把 **red-assert** 记成已验。

    ⚠️ `expect == ""` 的条目是**对照**（写在 MUTATIONS 表里、但没有目标门禁）：
    它们按定义不会变红，也永远不该出现在「待跑」里 —— 否则 `--ledger-status` 永远报非零。
    """
    todo, skipped = [], []
    for m in picked:
        name, rel, old, new, _expect = m
        if not _expect:
            skipped.append(name)
            continue        # `expect == ""` 是**对照**（形状校验已禁止它出现在 MUTATIONS）
        if _anchor_problem(rel, old):
            todo.append(m)   # 锚点脱节/歧义 ⇒ 一律重跑（绝不能拿它当「已验」）
            continue
        rec = entries.get(name) or {}
        # red-assert = 本轮真跑过（**代码区域 + 门禁文件**双指纹）；inherited = 只按代码区域
        # 继承（历史条目没记门禁指纹 —— 已披露的残余，靠周期性全量直跑兜底）
        fp_ok = rec.get("fp") == _fingerprint(rel, old, new)
        if rec.get("verdict") == "red-assert":
            # 测试侧：**当年那批用例名今天还在吗**（只加新用例不算失效——加用例只增强抓取力）
            want, have = rec.get("gate_fp") or "", _gate_cases(rec.get("gate") or "")
            if want.startswith("cases:") and have.startswith("cases:"):
                ok_gate = set(want[6:].split(",")) <= set(have[6:].split(","))
            else:
                ok_gate = want == have
            ok = fp_ok and ok_gate and rec.get("helper_fp") == _helper_fp()
        else:
            ok = rec.get("verdict") == "inherited" and fp_ok
        if ok:
            skipped.append(name)
        else:
            todo.append(m)
    return todo, skipped


def _run_gates_first_red(repo: Path, preferred: "list[str]",
                         expand: bool = True) -> "dict[str, tuple[int, str]]":
    """先跑**声明的目标门禁**；如果它绿了，再按其余门禁继续跑，**遇第一支红即停**。

    为什么不是「只跑目标门禁」（审计 A 的反面意见，成立）：`cf236b1` 的 `only` 模式会让
    「目标门禁抓不住、只有别的门禁抓得住」的变异被误报成 🟢，违背本文件的判定契约
    （**至少一门红**）。为什么也不是「跑满五支」：`-k V0-4` 的目标门禁 0.1s 就判红，
    剩下 14s 纯属浪费。这个策略两者兼得：**红得早 ⇒ 立刻收工；目标门禁绿 ⇒ 继续找红**。
    """
    # `expand=False`（`--target-only`）：只跑声明的目标门禁就收工 —— 快 3-5 倍。
    # ⚠️ 这个模式判**红**有效（目标门禁真的红了）；判**绿**不能当「守住」，
    # 所以绿的会被账本拒收、并由 `--delta` 的第二趟用完整模式复核。
    rest = [s for s in GATE_ORDER if s not in preferred] if expand else []
    out: "dict[str, tuple[int, str]]" = {}
    for script in list(preferred) + rest:
        out[script] = _run_one_gate(repo, script)
        if out[script][0] != "green":
            break
    return out


def _run_gates(repo: Path, only: "list[str] | None" = None) -> "dict[str, tuple[int, str]]":
    """跑门禁。``only`` 给定时**只跑那几支**（基线自校验用；变异判定走 `_run_gates_first_red`）。

    为什么要有这个开关（2026-09-21 审计 C 实测）：过去每条变异都跑满五支门禁 ——
    实测 `-k V0-4`（目标门禁是 cardview，0.1 秒就能判红）**实跑 66 秒**，其中
    `test_units 4.1s / override 2.1s / hooks 4.8s / clarify 4.2s` 全是不必要的。
    ⚠️ 基线自校验**仍然跑满五支**（无 `-k` 时）——那是「改动前系统是绿的」这个前提本身；
    带 `-k` 时基线只跑并集里的那几支（阶段内提速），**发布/阶段收尾必须无 `-k` 全量**。
    """
    scripts = only or list(GATE_ORDER)
    out = {}
    for script in scripts:
        out[script] = _run_one_gate(repo, script)
    return out


def _anchor_problem(rel: str, old: str, text: Optional[str] = None) -> Optional[str]:
    """锚点对账 —— 返回 ``None`` 表示可用，否则返回**给人读的原因**。

    ⚠️ **一处真相**：这个判据同时服务两处 —— 全量循环里的逐条检查，以及 :func:`preflight`
    的纯文本预检。以前它只写在循环里，于是「3 秒就能查完的事」必须等一次全量跑
    （310 条 × 四门禁 ≈ 65 分钟）才暴露（2026-09-15 实测：7 条失效锚点就是这么拖到深夜的）。
    两处各抄一遍，正是本项目最恨的「同一件事两处真相」—— 所以抽成函数。

    ``text`` 由调用方给（**不是**函数自己去读同一个地方）：全量循环查的是 ``_prepare()``
    拷出来的**快照**，预检查的是**工作树** —— 两者当前逐字节相同，但把「查哪一份」
    写死在函数里就会悄悄改变循环的语义。

    两种问题**都算红**，但理由不同（`AGENTS.md` 里写了）：
      * **没找到** = 清单与源码**脱节**（源码改了、原文串已不存在）⇒ 跑不到的变异等于没验；
      * **出现多次** = `replace(..., 1)` 只换**第一处** ⇒ 变异打到别处去，而报告照常打印
        「🟢 断言没有判别力」，**结论正好写反**（真发生的是「变异没生效」）。
    """
    if text is None:
        target = REPO / rel
        if not target.exists():
            return f"目标文件不存在：{rel}"
        text = target.read_text(encoding="utf-8")
    hits = text.count(old)
    if hits == 0:
        return "锚点没找到（源码变了？）"
    if hits != 1:
        return f"锚点在 {rel} 里出现 {hits} 次（歧义：会改到别处）"
        # ⚠️ **指纹侧守卫**（2026-09-21 终审 C 的 H1）：算区域指纹用的锚点必须**包含完整 old**。
    # 早先 `_anchor_region` 只看 old 的第一行 ⇒ 22/470 条记录的指纹算在**另一个同形代码块**上，
    # 于是「变异真的打进去、门禁真的红」而 `--delta` 照报「已验跳过」。这条守卫让同类改法立刻红。
    region = _anchor_region(rel, old)
    if region is None or old not in region:
        return ("指纹锚点对不上：`_anchor_region` 取到的区域不含完整 old"
                "（会算出别处代码的指纹 ⇒ `--delta` 误判「已验」）")
    return None


def _noop_reason(rel: str, old: str, new: str) -> Optional[str]:
    """这条变异**根本没改到代码**吗？—— 返回原因，或 ``None``（=它真的改了东西）。

    为什么要单独判（审计 D1/D3）：全量跑现在把「撤掉修复却全绿」一律打印成
    🟢「**断言没有判别力**」—— 但对下面两类，**真相不是那个**：变异的替换串与原文**逐字节等价**
    （撤了个寂寞），或锚点整段落在**注释**里（改的是注释，代码一个字节没动）。
    两种情况都会让报告**把结论写反**（`docs/lessons.md` 推论 ③ 描述的正是这个形态：
    真发生的是「变异没生效」，报告写的是「断言没判别力」）。
    ⚠️ 只对 ``MUTATIONS`` 用它 —— ``CONTROLS`` 里的「纯注释改动」是**故意的**等价对照，
    对它报「没生效」反而是误诊。
    """
    if new.strip() == old.strip():
        return "原文与替换**逐字节等价**（撤了个寂寞 ⇒ 变异不会生效）"
    import io
    import tokenize
    try:
        src = (REPO / rel).read_text(encoding="utf-8")
    except OSError:
        return None
    first = old.splitlines()[0].strip() if old.splitlines() else ""
    if not first:
        return None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT and first in tok.string:
                return "锚点整段落在**注释**里（改的是注释，代码一个字节没动）"
    except Exception:                       # tokenize 在畸形输入上会抛 —— 那是别的问题，别吞成结论
        return None
    return None


def _shape_error(entry, kind: str = "变异") -> "str | None":
    """条目形状校验（`--preflight` 与主循环共用）。

    ⚠️ 2026-09-21 终审 C：`expect == ""` 的条目**不许**出现在 `MUTATIONS` 里 —— 它会
    ①永远不跑、②被 `_delta_split` 无条件算成「已跳过」、③让 `--ledger-status` 虚报覆盖、
    ④让 `--upgrade-inherited`/full 模式把它当失败（`bad` 非空 ⇒ `full_audit_at` 永远刷不上）。
    对照（等价变异）本来就该写进 `CONTROLS` 表。
    """
    if kind == "变异" and len(entry) >= 5 and entry[4] == "":
        return (f"MUTATIONS 里的条目不许留空 expect（那是**对照**，请写进 CONTROLS）："
                f"{entry[0]!r}")
    return _shape_error_impl(entry)


def _shape_error_impl(entry) -> Optional[str]:
    """清单条目的**形状**校验（审计 D1）。

    预检用 `... , *_rest`（容忍任意元数），而全量跑用**恰好 5 元的解包** ⇒ 有人给条目多加一个
    字段时，预检报 ✅ 而全量跑第一行就 `ValueError: too many values to unpack` ——
    「一处真相」在**元数**上变成了两处。两处都调这一个函数，形状问题就不可能只被一边发现。
    """
    if not isinstance(entry, (tuple, list)) or len(entry) != 5:
        return f"条目形状不对：期望 (名字, 文件, 原文, 替换, 期望) 共 5 项，实际 {len(entry)} 项"
    return None


def preflight() -> int:
    """**只做纯文本锚点对账**：不跑任何门禁、不建快照目录、约 3 秒。

    为什么值得一条独立入口：本验证器的结论**只有两种方式失效** —— ①断言没判别力（假绿）、
    ②锚点失效/歧义（结论正好写反）。第 ② 种的判据**只要读文件**，却一直要等一次全量跑
    （310 条 × 四门禁 ≈ 65 分钟）才顺带发现。2026-09-15 实测的代价：7 条失效锚点 +
    1 条「锚点唯一却打错分支」把一批改动拖到深夜才敢提交。

    ⚠️ 它**只能**证明「锚点还在、且唯一」——**证明不了**「变异打在正确的分支上」、
    也证明不了「撤掉修复真的会变红」。那两件事只有跑门禁才知道，所以它是**前置筛子**，
    不是全量跑的替代品（`AGENTS.md`：跑不到的变异等于没验，而跑得到的变异还得真跑）。
    """
    picked = [(m, "变异") for m in MUTATIONS] + [(m, "对照") for m in CONTROLS]
    bad = []
    for entry, kind in picked:
        shape = _shape_error(entry, kind)
        if shape:
            print(f"❓ [{kind}] {shape}")
            bad.append(f"[{kind}] 第 {picked.index((entry, kind)) + 1} 条 —— {shape}")
            continue
        name, rel, old, _new, _rest = entry
        why = _anchor_problem(rel, old)
        if why:
            bad.append(f"{name}: {why}")
            print(f"❓ {name}\n   {why}")
        else:
            print(f"✅ {name}")
    print(f"\n锚点对账：{len(picked) - len(bad)}/{len(picked)} 可用"
          f"（变异 {len(MUTATIONS)} + 对照 {len(CONTROLS)}）")
    if bad:
        print("\n结论：清单与源码脱节（或锚点歧义 / 条目形状不对）——**全量跑之前先修这里**。")
        for line in bad:
            print(" -", line)
        return 1
    # ⚠️ **这条告诫不能省**（审计 D4）：早返回排在基线自校验之前是有意的（否则 0.1 秒的价值就没了），
    #    代价是「锚点 ✅ + exit 0」与「这棵树根本跑不了」可以**同时成立** —— 实测把
    #    `core/i18n.py` 弄成语法错误时，这里照样 318/318 ✅、exit 0，而全量跑立刻 `EXIT=2`
    #    「基线不是绿的」。按本项目自己的纪律（「没记录就写无记录，绝不写正常」），
    #    别让这个 ✅ 兼作「树是绿的」。
    print("✅ 全部锚点存在且唯一")
    print("⚠️ 仅此而已：**没跑任何门禁、也没做基线自校验**。它只回答「锚点还在不在、唯不唯一」"
          "这**一个**问题 ——")
    print("   锚点落在注释里 / 落在用例不经过的分支上 / 撤掉后压根不会变红，它都看不出来。"
          "**别拿它当提交前的绿灯**（那是 `tests/mutate_check.py` 全量跑的事）。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="只跑名字里含该子串的变异")
    ap.add_argument("--keep", action="store_true", help="保留临时目录（排查用）")
    ap.add_argument("--shard", default="", help="分片：i/n（1-based），用于后台并行")
    ap.add_argument("--list", action="store_true", dest="list_only",
                    help="只列出本分片/本 -k 选中的变异，不跑门禁")
    ap.add_argument("--inventory", default="",
                    help="把选中变异的机器可读 inventory 写到该 JSON 路径后退出")
    ap.add_argument("--upgrade-inherited", action="store_true", dest="upgrade_inherited",
                    help="把 `inherited(ref)` 条目当成待跑（补记门禁侧指纹 ⇒ 之后才算真·可跳过）。"
                         "历史继承条目没有门禁指纹，这一步是把那份残余风险关掉")
    ap.add_argument("--delta", action="store_true",
                    help="增量模式：只跑「锚点区域指纹变了 / 从未验过」的变异（其余跳过）；"
                         "全量矩阵只在 --full-audit 或大版本时跑")
    ap.add_argument("--update-ledger", action="store_true", dest="update_ledger",
                    help="把本轮判为 red-assert 的变异写进 tests/mutation-verdicts.json")
    ap.add_argument("--seed-inherited", default="", dest="seed_inherited",
                    help="把「锚点区域自该 git ref（如 v0.7.1）以来逐字节未变」的变异标成 "
                         "inherited(ref)（可审计的继承，不等于本轮跑过），随后 --delta 只跑真正的增量")
    ap.add_argument("--ledger-status", action="store_true", dest="ledger_status",
                    help="只看账本覆盖情况（多少条已验、多少条待跑），不跑门禁")
    ap.add_argument("--target-only", action="store_true", dest="target_only",
                    help="只跑变异声明的目标门禁（快 3-5 倍）；⚠️ 判绿不写账本，"
                         "绿的会被挑出来用完整「第一支红」模式复核")
    ap.add_argument("--preflight", action="store_true",
                    help="只做纯文本锚点对账（0.1 秒级）后就退出，不跑任何门禁、"
                         "也不做基线自校验；⚠️ 不受 -k 影响（有意：它还负责补 -k 子集"
                         "看不到别处坏锚点的盲区）")
    args = ap.parse_args()

    # ⚠️ 必须排在**基线自校验之前** —— 预检的全部价值就是「3 秒」，排在后面就白搭了。
    if args.preflight:
        return preflight()

    picked = [m for m in MUTATIONS if args.k in m[0]]
    controls = [m for m in CONTROLS if args.k in m[0]]

    if args.seed_inherited:
        n = _seed_inherited(args.seed_inherited)
        entries = _load_ledger()
        print(f"已标记 inherited({args.seed_inherited})：+{n} 条 ⇒ 账本共 {len(entries)} 条")
        todo, skipped = _delta_split(MUTATIONS, entries)
        print(f"真正的增量：{len(todo)} 条待跑 / {len(skipped)} 条可跳过")
        return 0

    if args.ledger_status:
        # 先做形状校验：否则一条 `expect==""` 混进 MUTATIONS 时，这里会报出一个**虚高的
        # 覆盖率**（`--preflight` 会拒，但 status 是大家日常看的那条命令）。收口复核实测。
        _shape_problems = [(m[0], _shape_error(m, "变异")) for m in MUTATIONS]
        _shape_problems = [(n, why) for n, why in _shape_problems if why]
        if _shape_problems:
            print("❌ 清单形状有问题，先修清单再看覆盖：")
            for name, why in _shape_problems[:5]:
                print(f"   - {name}: {why}")
            return 2
        entries = _load_ledger()
        todo, skipped = _delta_split(MUTATIONS, entries)
        n_assert = sum(1 for m in MUTATIONS
                       if (entries.get(m[0]) or {}).get("verdict") == "red-assert")
        n_inh = sum(1 for m in MUTATIONS
                    if (entries.get(m[0]) or {}).get("verdict") == "inherited")
        n_nogate = sum(1 for m in MUTATIONS
                       if (entries.get(m[0]) or {}).get("verdict") == "inherited"
                       and not (entries.get(m[0]) or {}).get("gate_fp"))
        print(f"账本覆盖：{len(MUTATIONS) - len(todo)}/{len(MUTATIONS)} 条可跳过"
              f"（本轮真跑过 {n_assert} + 继承 {n_inh}）；待跑 {len(todo)} 条"
              + (f"；⚠️ {n_nogate} 条继承条目**没有门禁侧指纹**（残余：门禁文件若被改写，"
                 f"它们不会被自动挑出来重跑）" if n_nogate else ""))
        if skipped[:3]:
            print(f"  例（可跳过）：{skipped[:3]}")
        if todo[:5]:
            print(f"  例（待跑）：{[m[0] for m in todo[:5]]}")
        return 0

    if args.upgrade_inherited:
        entries = _load_ledger()
        # `expect == ""` 的条目是**对照**（写在 MUTATIONS 表里但没有目标门禁）：
        # 全绿是它的**预期结果**，所以既不能当待跑（否则 `bad` 非空 ⇒ exit 1），
        # 也不能用来刷 `full_audit_at`（终审 A 的伪造反例）。
        picked = [m for m in picked
                  if m[4] and (entries.get(m[0]) or {}).get("verdict") != "red-assert"]
        print(f"升级模式：把 {len(picked)} 条 inherited / 缺失条目当待跑"
              f"（补门禁侧指纹；fresh 的 red-assert 不动）")

    if args.delta:
        entries = _load_ledger()
        todo, skipped = _delta_split(picked, entries)
        print(f"增量模式：待跑 {len(todo)} 条 / 跳过 {len(skipped)} 条"
              f"（已验且锚点区域指纹未变 = {len(entries)} 条账本）")
        picked = todo
        if args.k and not picked:
            print(f"✅ `-k {args.k!r}` 命中的变异都已在账本里（指纹未变）—— 无需重跑。")
            return 0

    # ⚠️ `-k` 未命中必须在 shard/inventory/list 之前拦截，否则空 inventory 会被误读为覆盖完成。
    if args.k and not picked:
        print(f"❌ `-k {args.k!r}` 没有命中任何变异（只命中对照 {len(controls)} 条）—— "
              "对照不能替代变异证据，不给「全绿」结论。")
        print(f"   （清单里共 {len(MUTATIONS)} 条变异 + {len(CONTROLS)} 条对照；"
              f"用 `--preflight` 可以看全部名字）")
        return 2
    if not args.k and not picked and not controls:
        print("❌ 清单是空的 —— 没有可跑的变异，不给结论。")
        return 2

    # v0.7.1 V0：分片（i/n）只影响选择，不改每条的执行方式。
    if args.shard:
        try:
            shard_i, shard_n = (int(x) for x in args.shard.split("/", 1))
            if shard_i < 1 or shard_n < 1 or shard_i > shard_n:
                raise ValueError
        except ValueError:
            print(f"❌ --shard 需要 i/n（1-based，i<=n），得到 {args.shard!r}")
            return 2
        picked = [m for idx, m in enumerate(picked) if idx % shard_n == shard_i - 1]
        controls = [m for idx, m in enumerate(controls) if idx % shard_n == shard_i - 1]
        print(f"分片 {shard_i}/{shard_n}：选中 {len(picked)} 条变异 + {len(controls)} 条对照")
        if not picked and not controls:
            print("ℹ️ 本分片为空（合法，不是失败）")
            return 0

    if args.inventory:
        import hashlib
        inv = []
        control_set = set(id(m) for m in CONTROLS)
        for entry in picked + controls:
            name, rel, old, _new, expect = entry
            inv.append({"name": name, "file": rel, "gate": expect,
                        "anchor_sha256": hashlib.sha256(old.encode("utf-8")).hexdigest(),
                        "kind": "control" if id(entry) in control_set else "mutation"})
        payload = {
            "filter": args.k,
            "shard": args.shard or "all",
            "total_mutations": len(MUTATIONS),
            "total_controls": len(CONTROLS),
            "selected_mutations": len(picked),
            "selected_controls": len(controls),
            "entries": inv,
        }
        Path(args.inventory).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                        encoding="utf-8")
        print(f"inventory written: {args.inventory}（{len(inv)} 条，分片 {payload['shard']}）")
        return 0

    if args.list_only:
        for name, rel, _old, _new, expect in picked + controls:
            print(f"{name}\t{rel}\t{expect}")
        print(f"共 {len(picked)} 条变异 + {len(controls)} 条对照")
        return 0

    # ⚠️ **`-k` 一条都没命中 ⇒ 不许报绿**（审计 D7，既有缺陷）：以前会打印
    #    「全部 0 条变异都被门禁抓住 ✅」+ `exit 0` —— 一个**拼错的 `-k`**（`-k ZZZ`）与
    #    「真的全绿」长得一模一样。这是本项目最恨的「绿而无判别力」，所以直接算失败。
    for _entry in picked + controls:
        _shape = _shape_error(_entry, "变异" if _entry in picked else "对照")
        if _shape:
            print(f"❌ {_shape}")
            print("   （预检 `--preflight` 也会报同一个问题：两处共用 `_shape_error`）")
            return 2
    if args.k and not picked:
        print(f"❌ `-k {args.k!r}` 没有命中任何变异（只命中对照 {len(controls)} 条）—— "
              "对照不能替代变异证据，不给「全绿」结论。")
        print(f"   （清单里共 {len(MUTATIONS)} 条变异 + {len(CONTROLS)} 条对照；"
              f"用 `--preflight` 可以看全部名字）")
        return 2
    if not args.k and not picked:
        print("❌ 清单是空的 —— 没有可跑的变异，不给结论。")
        return 2

    # ⚠️ **基线守卫**（第八路审计实测出来的假绿源头）：基线自己就是红的时候，
    # 每条变异当然都「变红」—— 于是这个验证器会满口「全部被门禁抓住 ✅」。
    # 实测复现：把历史阻断项那句老赋值塞回基线（test_units 115/116），再跑 `-k P1`
    # 依然报全绿 + exit 0。所以基线必须是绿的，否则直接退出、不给结论。
    # 基线只跑**本次选中的变异**会用到的门禁（提速；`-k V0-4` 过去要跑满五支 ≈26s，
    # 现在只跑 cardview ≈1s）。不传 `-k` 时并集就是全部五支，行为不变。
    _baseline_only = sorted({(e[4] if e[4].endswith(".py") else e[4] + ".py")
                             for e in (picked + controls) if e[4]}) or None
    baseline = _run_gates(REPO, only=_baseline_only)
    print(f"基线自校验：{baseline}")
    baseline_red = {k: v for k, v in baseline.items() if v[0] != "green"}
    if baseline_red:
        print("❌ 基线不是绿的，本次不给任何结论（否则每条变异都会「被抓住」）：")
        for name, (kind, last) in baseline_red.items():
            print(f"   {name}: {kind} · {last}")
        print("   注意：本验证器跑的是**工作树**，未提交的改动会一起被验。")
        return 2

    bad = []
    verified: dict = {}
    tmp_root = Path(tempfile.mkdtemp(prefix="larkdeck-mut-"))
    try:
        for idx, (name, rel, old, new, expect) in enumerate(picked):
            parent = tmp_root / f"mut{idx:02d}"
            if idx:                      # 只留上一代：磁盘占用从 O(N) 降到 O(1)（否则 238 份 ≈ 1.6GB）
                shutil.rmtree(tmp_root / f"mut{idx - 1:02d}", ignore_errors=True)
            parent.mkdir(parents=True)
            repo = _prepare(parent)
            target = repo / rel
            # ⚠️ 文件不存在要**走逐条记账**，不能让它裸 traceback 把整轮打断（审计 D2：
            #    以前是 `FileNotFoundError` 直接抛出去，没有逐条 ❓ 行、也没有结论段）。
            try:
                text = target.read_text(encoding="utf-8")
            except OSError as exc:
                bad.append(f"{name}: 目标文件读不到（{exc.strerror or exc}）：{rel}")
                print(f"❓ {name}: 目标文件读不到：{rel}")
                continue
            # ⚠️ 判据与 `--preflight` **共用** `_anchor_problem`（一处真相）：查的是**快照**那份文本。
            # 两种问题都算红 —— 「没找到」= 清单与源码脱节；「出现多次」= `replace(..., 1)`
            # 只换第一处 ⇒ 变异打到别处去，而报告照常打印 🟢（**结论正好写反**）。
            # 第十一路审计续实测：`if panel:` 那种短锚点在 cards.py 里有两处（统一面板那处在前），
            # 一份变异静默地打到了无关分支上，报告写着 🟢。
            why = _anchor_problem(rel, old, text)
            if why:
                bad.append(f"{name}: {why}")
                print(f"❓ {name}: {why}")
                continue
            target.write_text(text.replace(old, new, 1), encoding="utf-8")
            _only = [expect if expect.endswith(".py") else expect + ".py"] if expect else None
            results = _run_gates_first_red(repo, preferred=_only or list(GATE_ORDER),
                                           expand=not args.target_only)
            red = [k for k, (kind, _) in results.items() if kind != "green"]
            crashed = [k for k, (kind, _) in results.items() if kind == "red-crash"]
            evidence = [k for k in red if k not in crashed]
            expect_script = expect if expect.endswith(".py") else expect + ".py"
            _noop = _noop_reason(rel, old, new)
            if not red and _noop:
                # ⚠️ **真相不是「断言没判别力」，而是「变异没生效」**（审计 D3）：
                #    替换串与原文等价、或锚点整段在注释里 ⇒ 代码一个字节没动，四门禁当然全绿。
                #    以前这两种都打印 🟢「断言没有判别力」——**结论正好写反**（本项目的头号误诊形态）。
                status = f"⚪ 变异没生效（{_noop}）——**不是**断言没判别力"
            elif not red:
                status = "🟢 全绿（**断言没有判别力！**）"
            elif not evidence:
                status = "💥 只有崩溃（语法错误 / import 炸），**不算判别力证据**"
            else:
                status = "🔴 断言失败"
            print(f"{status} {name}  期望={expect} 实红={red} 断言红={evidence}")
            if not red and _noop:
                bad.append(f"{name}: 变异没生效（{_noop}）—— 这条等于没验，修好它再说")
            elif not red:
                bad.append(f"{name}: 撤掉修复后四门禁仍然全绿 —— 断言没有判别力")
            elif not evidence:
                bad.append(f"{name}: 只有崩溃、没有断言失败 —— 不能算被门禁抓住")
            elif expect_script not in evidence:
                print(f"   ⚠️ 期望 {expect} 变红，实际是 {evidence}（也算被守住了，但归因不准）")
            if evidence:      # red-assert ⇒ 记进账本（绿/崩溃都不记，下一次增量还会重跑）
                verified[name] = {
                    "fp": _fingerprint(rel, old, new),
                    "verdict": "red-assert",
                    "gate": evidence[0],
                    "gate_fp": _gate_cases(evidence[0]),
                    "helper_fp": _helper_fp(),
                    "at": _head_short(),
                }
    finally:
        _full = (not args.delta and not args.k and not args.upgrade_inherited
                 and not bad and len(picked) == len([m for m in MUTATIONS if m[4]]))
        if (args.update_ledger and verified) or _full:
            entries = _load_ledger()
            entries.update(verified)
            _save_ledger(entries, full_audit_at=_head_short() if _full else "")
            print(f"账本已更新：+{len(verified)} 条 red-assert ⇒ 共 {len(entries)} 条"
                  + (f"；full_audit_at={_head_short()}" if _full else ""))
        if args.keep:
            print(f"临时目录保留在 {tmp_root}")
        else:
            shutil.rmtree(tmp_root, ignore_errors=True)

    # 对照项（**单独一张表**，不靠名字里的关键字）：它们是**行为等价**的改动，
    # 必须四门禁全绿 —— 用来证明门禁不会假红。第九路审计实测：原来靠 `"对照" in name`
    # 判定，于是把任意一条「抓不住的变异」改个名字就能被跳过（可滥用）。
    for idx, (name, rel, old_text, new_text, _unused) in enumerate(controls):
        parent = tmp_root / f"ctl{idx:02d}"
        parent.mkdir(parents=True)
        repo = _prepare(parent)
        target = repo / rel
        text = target.read_text(encoding="utf-8")
        why = _anchor_problem(rel, old_text, text)      # 与变异循环、`--preflight` 共用同一判据
        if why:
            bad.append(f"{name}: 对照项{why}")
            print(f"❓ {name}: {why}")
            continue
        target.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        results = _run_gates(repo)
        red = [k for k, (kind, _) in results.items() if kind != "green"]
        if red:
            bad.append(f"{name}: 等价改动竟然让门禁变红 —— 门禁有假红")
            print(f"❌ 对照变红了（假红！） {name} 实红={red}")
        else:
            print(f"⚪ 对照全绿（符合预期） {name}")

    if bad:
        print("\n结论：")
        for line in bad:
            print(" -", line)
        return 1
    print(f"\n全部 {len(picked)} 条变异都被门禁抓住 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
