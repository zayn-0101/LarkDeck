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
import os
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
     '    "session_attribution_ok",\n)',
     '    "missing_callback", "missing_signal", "missing_display_chrome",\n'
     '    "session_attribution_ok",\n)',
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
     "    if isinstance(duration, (int, float)) and duration >= 0.1:\n        parts.append(f\"⏱ {format_elapsed(float(duration))}\")",
     "    if False:\n        parts.append(f\"⏱ {format_elapsed(float(duration or 0))}\")",
     "check_hooks"),
    ("T5-每轮耗时标题不再渲染", "core/cards.py",
     "        return f\"{base} · {format_elapsed(max(0.1, elapsed_ms / 1000.0))}\"",
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
     "            card = self._ld_build_card(tail_visible or \" \", streaming=False,\n                                       panel=self._ld_panel(chat, state.get(\"t0\"),\n                                                            report_empty=True),\n                                       footer=self._ld_frame_footer(state))",
     "            card = self._ld_build_card(tail_visible or \" \", streaming=True,\n                                       panel=self._ld_panel(chat, state.get(\"t0\"),\n                                                            report_empty=True),\n                                       footer=self._ld_frame_footer(state))",
     "check_hooks"),
    ("SEQ4-/stop 重绘不再带中止色", "core/adapter.py",
     "            panel = self._ld_panel(chat, started, report_empty=True) or _cards.unified_panel(\n                status=_panel.STATUS_STOPPED)",
     "            panel = None",
     "check_hooks"),
    # ---- 第十路审计：真判据 / 崩溃分类 / 口径 / 调用点 / 码表 ------------------ #
    ("A1a-判据退回「只看近似阈值」", "core/adapter.py",
     "        if size > _HOPELESS_BYTES or not _stop_redraw_would_paint(body):",
     "        if size > _MAX_TRACKED_TEXT:",
     "test_units"),
    ("A1b-真判据恒真（不丢正文）", "core/adapter.py",
     "        if size > _HOPELESS_BYTES or not _stop_redraw_would_paint(body):",
     "        if size > _HOPELESS_BYTES:",
     "test_units"),
    ("A1c-真判据不再检查有没有颜色", "core/adapter.py",
     '        return \'"collapsible_panel"\' in json.dumps(node, ensure_ascii=False)',
     "        return True",
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
     '                cache=snap.get("cache_pct") if mode in ("basic", "full") else None,',
     '                cache=(snap.get("cache_pct") or 0) if mode in ("basic", "full") else None,',
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
     '                cache=snap.get("cache_pct") if mode in ("basic", "full") else None,',
     '                cache=(snap.get("cache_pct") or None) if mode in ("basic", "full") else None,',
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
     '        ops.append(_CkOp(_cards.CARDKIT_ANSWER_ID, display or " ", _CK_ROLE_ANSWER))',
     '        ops.append(_CkOp(_cards.CARDKIT_ANSWER_ID, " ", _CK_ROLE_ANSWER))',
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
                    reasoning=str(snap.get("reasoning") or ""),
                    rounds=snap.get("rounds") or [],
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
     '                    reasoning=str(snap.get("reasoning") or ""),\n'
     '                    rounds=snap.get("rounds") or [],\n'
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
     '    for item in steps:\n        lines.append(truncate(item, max_tool_chars))\n'
     '    return "\\n\\n".join(lines)',
     '    for item in steps:\n        lines.append(item)\n'
     '    return "\\n\\n".join(lines)',
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
     '                card = self._ld_build_card(visible, streaming=True,\n                                           panel=self._ld_panel(chat, state.get("t0")),\n                                           footer=self._ld_frame_footer(state))',
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
     '                description=_i18n.t("cmd.description"), args_hint="[status|help]")',
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
     '        return "\\n".join([header, _i18n.t("cmd.scope")] + _context.status_lines())',
     '        return "\\n".join([header] + _context.status_lines())',
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
     '    original_builder = adapter.LarkDeckMixin.__dict__["_ld_build_resolved_card"]',
     '    original_builder = adapter.LarkDeckMixin._ld_build_resolved_card.__func__',
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
     '                                   panel=self._ld_panel(chat, state.get("t0"),\n'
     '                                                        report_empty=bool(finalize)),',
     '        card = self._ld_build_card(display, streaming=True,\n'
     '                                   panel=self._ld_panel(chat, state.get("t0"),\n'
     '                                                        report_empty=bool(finalize)),',
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
     '        display = self._ld_body_text(text, chat, finalize=finalize)',
     '        display = text',
     "test_units"),
    ("R11-2-工具窗口判据撤掉（没有工具事件也照剥）", "core/adapter.py",
     '    if finalize or not text or not accumulated or not tool_pending or not complete:',
     '    if finalize or not text or not accumulated or not complete:',
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
     '    if finalize or not text or not accumulated or not tool_pending or not complete:',
     '    if finalize or not text or not accumulated or not tool_pending:',
     "test_units"),
    # R11-9（R11-A7 尾巴）：**收尾帧护栏撤掉**。这是本组里唯一「失败不可逆」的一条 ——
    # 核心对 finalize 是乐观记账（`_record_turn_final_payload` / `delivered_final_matches`），
    # 它认为送达成功、**不会再补发**；而那一帧核心发的是纯 `self._accumulated`
    # （`stream_consumer.py:791-798` 只有 `tick.is_interim` 才合成进度块），所以剥掉的
    # 只可能是**模型自己写的正文**。判别力由单测 ㉘⑦ 提供（陈旧前缀场景：累积只到前半段、
    # 模型自己写了分隔线之后继续写 ⇒ 旧四个条件全部成立 ⇒ 收尾卡里后半段消失）。
    ("R11-9-收尾帧护栏撤掉（finalize 帧也照剥 ⇒ 静默吞正文且核心不再补发）", "core/adapter.py",
     '    if finalize or not text or not accumulated or not tool_pending or not complete:',
     '    if not text or not accumulated or not tool_pending or not complete:',
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
    ('G2-10-`/stop` 重绘退回基数页脚（最可能被截图的那一帧丢掉短码）', 'core/adapter.py',
     '                                       panel=panel,\n                                       footer=self._ld_frame_footer({"message_id": message_id}))',
     '                                       panel=panel, footer=self._ld_footer())',
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
    ('G2-3-帧页脚不再带本卡短码（截图与日志又对不上）', 'core/adapter.py',
     '        return f"{base} · \\U0001f516 {trace}"',
     '        return base',
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
    ("Y20-收尾整卡替换退回基数页脚（用户**最后看到**的那张卡丢短码）", "core/adapter.py",
     '                                       footer=self._ld_frame_footer(state))\n'
     '            result = await self._ld_update_card(chat, message_id, card)\n'
     '            if result is None or not getattr(result, "success", False):\n'
     '                return self._ld_stream_fail(\n'
     '                    f"收尾帧失败（{getattr(result, \'error\', \'unknown\')}）")',
     '                                       footer=self._ld_footer())\n'
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
]

#: **对照项**：行为等价的改动（合法 YAML 变体等），期望四门禁**全绿**。
#: 与 MUTATIONS 分开成两张表 —— 判断依据是它属于哪张表，不是名字里有没有某个字。
CONTROLS = [
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
    # 行为等价：真判据里那句字节上限检查其实被「卡里有没有面板」覆盖了 ——
    # `fit_reply_card` 装不下壳时会退回**裸卡**（没有面板），所以两种写法结果相同。
    # 第十路审计式的核对：这类「撤掉也全绿」的变异应当被承认为**等价**，而不是硬找门禁。
    ("C-对照：真判据不再单独检查字节上限（被面板判据覆盖）", "core/adapter.py",
     "        if _cards.card_bytes(node) > _cards.FEISHU_CARD_BYTE_LIMIT:\n            return False",
     "        if False:\n            return False", ""),
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
    shutil.copytree(REPO, dest, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", "*.pyc", ".pytest_cache", "docs/deliveries"))
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
}

#: 每个门禁**失败时**会打的标记（断言失败/测试报错）。缺了它就说明门禁没跑到断言那一步
#: —— 而 check_override/check_hooks 捕获加载器异常后只打自己的友好文案
#: （`FAIL: Failed to load plugin … invalid syntax`），所以**不能**把它们的输出当成
#: 「有断言失败」。第十路审计实测：纯语法错误型变异曾被算成判别力证据。
_FAIL_MARKERS = {
    "test_units.py": ("AssertionError", "FAIL  ", "ERROR "),
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
}


def _classify(script: str, proc: "subprocess.CompletedProcess") -> str:
    """``red-assert``（真有判别力）/ ``red-crash``（没跑到断言，不算证据）/ ``green``。

    判据（第十/第九路审计两轮修正后的形态）：

      * **green** 必须看到该门禁自己的收尾语（``OVERRIDE OK`` / ``HOOKS OK`` / ``passed``…）
        —— 光看退出码会把「根本没跑起来」算成通过；
      * **red-assert** 要求看到该门禁的**失败标记**（``AssertionError`` / ``FAIL  `` /
        ``ERROR `` / ``FAIL: ``）。三个 ``check_*`` 在**加载失败**（语法错误等）时只打自己的
        友好文案、不打断言标记，于是那种变异会落到下面一支；
      * **red-crash** = 非零退出 + 没有任何失败标记，或输出里有崩溃标记（traceback /
        SyntaxError / ImportError）。**崩溃不是判别力证据** —— 它只说明「你把代码弄坏了」，
        而这一批的教训正是：把崩溃算成「被门禁抓住」会掩盖真正的假绿。
    """
    blob = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0 and _PASS_MARKERS[script] in blob:
        return "green"
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


def _run_gates(repo: Path) -> "dict[str, tuple[int, str]]":
    out = {}
    for script in ("test_units.py", "check_override.py", "check_hooks.py",
                   "check_clarify_e2e.py"):
        proc = subprocess.run([sys.executable, str(repo / "tests" / script)],
                              capture_output=True, text=True, cwd=str(repo.parent),
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        tail = (proc.stdout or proc.stderr).strip().splitlines()
        out[script] = (_classify(script, proc), tail[-1] if tail else "")
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


def _shape_error(entry) -> Optional[str]:
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
        shape = _shape_error(entry)
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

    # ⚠️ **`-k` 一条都没命中 ⇒ 不许报绿**（审计 D7，既有缺陷）：以前会打印
    #    「全部 0 条变异都被门禁抓住 ✅」+ `exit 0` —— 一个**拼错的 `-k`**（`-k ZZZ`）与
    #    「真的全绿」长得一模一样。这是本项目最恨的「绿而无判别力」，所以直接算失败。
    for _entry in picked + controls:
        _shape = _shape_error(_entry)
        if _shape:
            print(f"❌ {_shape}")
            print("   （预检 `--preflight` 也会报同一个问题：两处共用 `_shape_error`）")
            return 2
    if args.k and not picked and not controls:
        print(f"❌ `-k {args.k!r}` 一条变异/对照都没命中 —— 什么都没验，不给「全绿」结论。")
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
    baseline = _run_gates(REPO)
    print(f"基线自校验：{baseline}")
    baseline_red = {k: v for k, v in baseline.items() if v[0] != "green"}
    if baseline_red:
        print("❌ 基线不是绿的，本次不给任何结论（否则每条变异都会「被抓住」）：")
        for name, (kind, last) in baseline_red.items():
            print(f"   {name}: {kind} · {last}")
        print("   注意：本验证器跑的是**工作树**，未提交的改动会一起被验。")
        return 2

    bad = []
    tmp_root = Path(tempfile.mkdtemp(prefix="larkdeck-mut-"))
    try:
        for idx, (name, rel, old, new, expect) in enumerate(picked):
            parent = tmp_root / f"mut{idx:02d}"
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
            results = _run_gates(repo)
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
    finally:
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
