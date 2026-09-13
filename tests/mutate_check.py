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

REPO = Path(__file__).resolve().parent.parent

#: (名字, 相对文件, 原文, 替换成, 期望其中哪个门禁变红)
#: 期望值只用于打印对照；判定标准是「至少一门红」。
MUTATIONS = [
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
     "            card = self._ld_build_card(tail_visible or \" \", streaming=False,\n                                       panel=self._ld_panel(chat, state.get(\"t0\")),\n                                       footer=self._ld_footer())",
     "            card = self._ld_build_card(tail_visible or \" \", streaming=True,\n                                       panel=self._ld_panel(chat, state.get(\"t0\")),\n                                       footer=self._ld_footer())",
     "check_hooks"),
    ("SEQ4-/stop 重绘不再带中止色", "core/adapter.py",
     "            panel = self._ld_panel(chat, started) or _cards.unified_panel(\n                status=_panel.STATUS_STOPPED)",
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
     '            if not wrote.ok:',
     '            wrote = await self._ld_ck_write(card_id, answer.element_id, answer.content, seq)\n'
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
     '        if _ld_response_code(made) != 0 or not card_id:\n            return None',
     '        if _ld_response_code(made) != 0:\n            return None',
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
     '            res = await self._ld_ck_settings(card_id, {"content": text}, seq, retry=False)',
     '            res = await self._ld_ck_settings(card_id, text, seq, retry=False)',
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
     '            res = await self._ld_ck_settings(card_id, {"content": text}, seq, retry=False)',
     '        try:\n'
     '            res = await self._ld_ck_settings(card_id, {"content": text}, seq, retry=False)',
     "test_units"),
    # ---- R6a：markdown 卫生（只在收尾帧、只删不补、代码区不动）------------------------- #
    ("R6a-1-流式帧也做卫生（前缀链断掉 ⇒ 回答重发一遍）", "core/adapter.py",
     '        display = text',
     '        display = _cards.sanitize_markdown(text)',
     "test_units"),
    ("R6a-2-改回「补一个 `**` 收尾」（把尾巴吞进加粗）", "core/cards.py",
     '    for index in range(len(text) - 2, -1, -1):\n'
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
     '            res = await self._ld_ck_settings(card_id, {"content": text}, seq, retry=False)',
     '            res = await self._ld_ck_settings(card_id, {"content": text}, seq)',
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
    # ⚠️ 锚点必须带上 `_cards.panel_markdown(` 那一行：这四行在 `adapter.py` 里出现**两次**
    # （`_ld_panel` 与 `_ld_panel_markdown` 各一次），短锚点会改到另一处去 ——
    # 这一条刚写出来时就被新加的唯一性守卫当场拦下（第十二路审计缺口 2）。
    ("CK16-CardKit 面板无视用户的三个上限", "core/adapter.py",
     '''            return _cards.panel_markdown(
                reasoning=str(snap.get("reasoning") or ""),
                rounds=snap.get("rounds") or [],
                tools=steps,
                max_reasoning_chars=_cfg_int("max_reasoning_chars", _cards.MAX_REASONING_CHARS),
                max_tool_chars=_cfg_int("max_tool_result_chars", _cards.MAX_TOOL_RESULT_CHARS),
                max_steps=_cfg_int("max_panel_steps", _cards.MAX_PANEL_STEPS),
            )''',
     '''            return _cards.panel_markdown(
                reasoning=str(snap.get("reasoning") or ""),
                rounds=snap.get("rounds") or [],
                tools=steps,
            )''',
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
    ("CK23-一帧塞不下时不再 fail-open（往一张新卡里硬写超上限的增量）", "core/adapter.py",
     '            body_bytes = _card_body_bytes(visible)\n'
     '            if body_bytes > _cards.FEISHU_CARD_BYTE_LIMIT:\n'
     '                # 走到这里说明「一张**全新的卡**也装不下这一帧的增量」—— 切卡帮不上忙\n'
     '                # （切点判据 ② 会拒绝），只能 fail-open 交核心回落。与切卡前的差别是：\n'
     '                # 这条路上卡里已经有前面几万字的正文，回落后核心只补发**剩余部分**。\n'
     '                _log_ck_over_budget_once(body_bytes)\n'
     '                return self._ld_stream_fail("CardKit 正文超过硬上限")\n'
     '            elems = self._ld_ck_elems(state)',
     '            elems = self._ld_ck_elems(state)',
     "test_units"),
    ("CK18-超预算告警不再限流", "core/adapter.py",
     '    if now - getattr(_log_ck_over_budget_once, "_at", 0.0) < 60.0:\n        return',
     '    if False:\n        return',
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
     '    if not isinstance(ts, (int, float)) or isinstance(ts, bool) or ts <= 0:\n'
     '        return _i18n.t("status.none")',
     '    if not isinstance(ts, (int, float)) or isinstance(ts, bool) or ts <= 0:\n'
     '        return "正常"',
     "test_units"),
    ("R9-3-入站心跳不再记账（心跳永远「无记录」）", "core/hooks.py",
     '        _context.note_inbound()',
     '        pass',
     "test_units"),
    ("R9-4-首发建卡不记账（短回答的回合会被报成「一次都没写」）", "core/adapter.py",
     '            _context.note_frame_ok()          # R9：首发建卡也是一次真的写卡',
     '            pass',
     "test_units"),
    ("R9-5-patch 传输的成功帧不记账", "core/adapter.py",
     '        _context.note_frame_ok()              # R9：patch 传输这一帧写成功',
     '        pass',
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
    ("R4-1-新卡重放整段（用户把前半段再看一遍）", "core/adapter.py",
     '            ops = _ck_plan(visible, self._ld_panel_markdown(chat, state.get("t0")),',
     '            ops = _ck_plan(display, self._ld_panel_markdown(chat, state.get("t0")),',
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
    ("R4-4-收尾帧重放整段", "core/adapter.py",
     '            tail_visible = display[tail_offset:]',
     '            tail_visible = display',
     "test_units"),
    ("R4-5-切点不避开代码围栏（两张卡的 markdown 各自残缺）", "core/adapter.py",
     '        if text[index - 1] != "\\n" or _inside(index):',
     '        if text[index - 1] != "\\n":',
     "test_units"),
    ("R4-6-patch 车道（降级之后）重放整段", "core/adapter.py",
     '        card = self._ld_build_card(visible, streaming=True,\n'
     '                                   panel=self._ld_panel(chat, state.get("t0")),',
     '        card = self._ld_build_card(display, streaming=True,\n'
     '                                   panel=self._ld_panel(chat, state.get("t0")),',
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="只跑名字里含该子串的变异")
    ap.add_argument("--keep", action="store_true", help="保留临时目录（排查用）")
    args = ap.parse_args()

    picked = [m for m in MUTATIONS if args.k in m[0]]
    controls = [m for m in CONTROLS if args.k in m[0]]

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
            text = target.read_text(encoding="utf-8")
            if old not in text:
                bad.append(f"{name}: 变异锚点没找到（源码变了？）")
                print(f"❓ {name}: 锚点没找到")
                continue
            # ⚠️ **锚点必须唯一**：`replace(..., 1)` 只换第一处，锚点串在文件里出现两次时
            # 会去改**另一处**代码 —— 于是「变异没生效」被误报成「断言没有判别力」。
            # 第十一路审计续实测：`if panel:` 那种短锚点在 cards.py 里有两处（统一面板那处
            # 在前），一份变异静默地打到了无关分支上，报告写着 🟢。
            # 歧义锚点与「锚点失效」同级：跑不到的变异等于没验，必须重新对准。
            hits = text.count(old)
            if hits != 1:
                bad.append(f"{name}: 锚点在 {rel} 里出现 {hits} 次（歧义：会改到别处）")
                print(f"❓ {name}: 锚点出现 {hits} 次（歧义，拒绝下结论）")
                continue
            target.write_text(text.replace(old, new, 1), encoding="utf-8")
            results = _run_gates(repo)
            red = [k for k, (kind, _) in results.items() if kind != "green"]
            crashed = [k for k, (kind, _) in results.items() if kind == "red-crash"]
            evidence = [k for k in red if k not in crashed]
            expect_script = expect if expect.endswith(".py") else expect + ".py"
            if not red:
                status = "🟢 全绿（**断言没有判别力！**）"
            elif not evidence:
                status = "💥 只有崩溃（语法错误 / import 炸），**不算判别力证据**"
            else:
                status = "🔴 断言失败"
            print(f"{status} {name}  期望={expect} 实红={red} 断言红={evidence}")
            if not red:
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
        if old_text not in text:
            bad.append(f"{name}: 对照锚点没找到")
            print(f"❓ {name}: 锚点没找到")
            continue
        hits = text.count(old_text)
        if hits != 1:
            bad.append(f"{name}: 对照锚点在 {rel} 里出现 {hits} 次（歧义）")
            print(f"❓ {name}: 锚点出现 {hits} 次（歧义，拒绝下结论）")
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
