# SNAPSHOT（v0.7.3）：用于证据 a2290da 的 scratch 发布脚本。
# 路径常量是 Zayn 本机设置（已脱敏为 <HOME>）；库内仅作审计/复现记录。

#!/usr/bin/env python3
"""v0.7.3 发布日执行器（用户终验通过后才跑；默认 dry-run，`--go` 才动手）。

为什么要有它：发布当天要按固定顺序做 6 件事，其中「重启网关后等自检通过」必须有界等待
（本项目禁 sleep 轮询），而且每一步都要留证据。脚本把顺序、判据、证据一次性固定下来，
避免手工粘贴时漏步/顺序错。

用法::

    $PY ~/.larkdeck-scratch/release-v0.7.3.py            # dry-run：只做只读核对
    $PY ~/.larkdeck-scratch/release-v0.7.3.py --check    # 同上（显式）
    $PY ~/.larkdeck-scratch/release-v0.7.3.py --go       # 真跑：打 tag/push/release/部署/重启

判据（任一不满足就停，不进下一步）：
  1. `run_fast.py --full` 八步全 OK；账本 523/523、待跑 0；`--preflight` 535/535（脚本按 len(MUTATIONS) 动态断言）；
  2. 工作树干净、`origin/main` 是本地 HEAD 的祖先（fast-forward）、`gh auth status` 通过；
  3. tag 不存在（存在就要求人工确认，脚本不覆盖）；
  4. `.deploy` 干净、重启后 150s 内有**晚于重启时刻**的 `启动自检通过` 行、进程在。
"""
from __future__ import annotations

import argparse
import datetime as dt
import ast
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import time

PY = "<HOME>/.hermes/hermes-agent/venv/bin/python3"
HERMES = "<HOME>/.hermes/hermes-agent/venv/bin/hermes"
REPO = pathlib.Path("<HOME>/Code/larkdeck")
DEPLOY = REPO / ".deploy"
AGENT_LOG = pathlib.Path.home() / ".hermes" / "logs" / "agent.log"
TAG = "v0.7.3"
TAG_MSG = ("LarkDeck v0.7.3：工具细节行 markdown/plain_text 两宿主 x-small（真机确认更小）、"
           "Error/Result 块逐行 inline code + x-small（fenced 代码块字号固定）；已知系统提示"
           "（Gateway/升级/DB/cron/后台任务等）整卡不渲染面板/状态头/页脚，真实回合状态词不变；"
           "帧状态显式注入收尾竞态；V073 系列变异全红（数量见账本）+ golden 重冻结")


def log(stage: str, msg: str) -> None:
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {stage} {msg}", flush=True)


def run(cmd: list, *, cwd: pathlib.Path = REPO, timeout: float = 120.0,
        check: bool = True) -> tuple:
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise SystemExit(f"❌ 命令失败（exit {p.returncode}）：{' '.join(cmd)}\n"
                         f"{(p.stdout or '')[-800:]}{(p.stderr or '')[-800:]}")
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def wait_for_selfcheck(since: float, cap_s: float = 150.0) -> tuple:
    """有界等待：`启动自检通过` 行且时间戳晚于 since。返回 (是否命中, 行)。"""
    deadline = time.monotonic() + cap_s
    while time.monotonic() < deadline:
        try:
            text = AGENT_LOG.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        hits = [l for l in text.splitlines() if "启动自检通过" in l]
        if hits:
            line = hits[-1]
            m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
            if m:
                ts = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
                if ts >= since - 1:
                    return True, line
        time.sleep(2.0)          # 有界等待器的内部节拍（不是外部轮询脚本）
    return False, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="真跑（打 tag/push/release/部署/重启）")
    ap.add_argument("--check", action="store_true", help="只做只读核对（默认）")
    args = ap.parse_args()
    dry = not args.go

    # ---- 0. 只读前置 --------------------------------------------------------
    log("0)", "只读前置核对")
    head = run(["git", "rev-parse", "--short", "HEAD"])[1].strip()
    status = run(["git", "status", "--porcelain=v1"])[1].strip()
    if status:
        raise SystemExit(f"❌ 工作树不干净，先提交或还原：\n{status[:400]}")
    anc = subprocess.run(["git", "merge-base", "--is-ancestor", "origin/main", "HEAD"],
                         cwd=REPO).returncode == 0
    if not anc:
        raise SystemExit("❌ origin/main 不是本地 HEAD 的祖先（远端有分叉）—— 人工处理")
    notes = REPO / "docs" / "releases" / "v0.7.3.md"
    if not notes.exists():
        raise SystemExit(f"❌ 缺发布说明 {notes}")
    pv = re.search(r"^version:\s*(\S+)", (REPO / "plugin.yaml").read_text(
        encoding="utf-8"), flags=re.M)
    if not pv or pv.group(1).lstrip("v") != TAG.lstrip("v"):
        raise SystemExit(f"❌ plugin.yaml version={pv.group(1) if pv else None!r} != {TAG}")
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    if "## [0.7.2] - " not in changelog or "## [Unreleased] - v0.7.3" not in changelog:
        raise SystemExit("❌ CHANGELOG.md 缺「[0.7.2] 已收口 + [Unreleased] - v0.7.3 已开」"
                         "（或 v0.7.1 之类的旧 Unreleased 标题仍在最前）")
    tags = run(["git", "tag", "-l", TAG])[1].strip()
    _, gh = run(["gh", "auth", "status"], timeout=30)
    if "Logged in" not in gh:
        raise SystemExit("❌ gh 未登录（发布要它）")
    dep = run(["git", "-C", str(DEPLOY), "status", "--porcelain=v1"])[1].strip()
    if dep:
        raise SystemExit(f"❌ .deploy 工作树不干净 —— 不能在未提交的部署树上发布：\n{dep[:400]}")
    log("0)", f"HEAD={head} · 工作树干净 · origin/main 可 fast-forward · tag {TAG} "
              f"{'已存在 ⚠️' if tags else '不存在 ✓'} · .deploy clean ✓")

    # ---- 1. 门禁 + 账本 -----------------------------------------------------
    log("1)", "门禁（run_fast --full）+ 账本（preflight / ledger-status）")
    _, out = run([PY, "tests/run_fast.py", "--full"], timeout=600)
    bad = [l for l in out.splitlines() if l.startswith("[FAIL")]
    # ⚠️ 别用 `l.split()[1]`：`[OK  ] test_units  4.32s` 按空白切出来是 `['[OK','',']',
    # 'test_units', ...]` ⇒ 拿到的是 `]` 而不是用例名（2026-09-22 dry-run 实测踩到）。
    ok_names = sorted(re.findall(r"^\[OK\s*\]\s+(\S+)", out, flags=re.M))
    # ⚠️ 只查「没有 [FAIL]」不够：门禁被改名 / 少跑一步 / 输出被截断都会照样通过
    # （计划 §2 末行③：**恰好 8 个 `[OK]` 且名字集合已知**）。
    expect_names = sorted(["test_units", "check_own_body", "mutate_preflight", "check_override",
                           "check_hooks", "check_clarify_e2e", "check_cardview",
                           "check_cls_alignment"])
    if bad or ok_names != expect_names:
        raise SystemExit(f"❌ 门禁未全绿/步数不对（FAIL={bad}；OK={ok_names}）")
    log("1)", f"run_fast --full：恰好 {len(ok_names)} 步全 OK ✓（名字集合与已知清单一致）")
    _, cnt = run([PY, "-c", "import sys; sys.path.insert(0, \"tests\"); import mutate_check; "
                 "print(len(mutate_check.MUTATIONS), len(mutate_check.CONTROLS))"], timeout=60)
    try:
        n_mut, n_controls = (int(x) for x in cnt.split())
    except Exception as exc:
        raise SystemExit(f"❌ 读不到变异/对照数：{cnt!r} ({exc})")
    _, pre = run([PY, "tests/mutate_check.py", "--preflight"], timeout=120)
    _, led = run([PY, "tests/mutate_check.py", "--ledger-status"], timeout=120)
    m = re.search(r"(\d+)/(\d+) 条可跳过.*?待跑 (\d+) 条", led, flags=re.S)
    m_pre = re.search(r"锚点对账：(\d+)/(\d+) 可用", pre)
    if not m_pre or "全部锚点存在且唯一" not in pre or not m:
        raise SystemExit(f"❌ 账本/预检输出异常：\n{pre[-400:]}\n{led[-400:]}")
    if int(m_pre.group(2)) != n_mut + n_controls or int(m_pre.group(1)) != int(m_pre.group(2)):
        raise SystemExit(f"❌ preflight 总数 {m_pre.group(0)!r} != {n_mut}+{n_controls}（动态断言失败）")
    log("1)", f"锚点 {m_pre.group(1)}/{m_pre.group(2)} 可用（{n_mut} 变异 + {n_controls} 对照）· "
              f"账本 {m.group(1)}/{m.group(2)} 可跳过 · 待跑 {m.group(3)}")
    if (m.group(1), m.group(3)) != (m.group(2), "0"):
        raise SystemExit("❌ 账本没到全量覆盖，不发布")

    # 1a) 账本 `_meta` 硬核对（计划 §6.10.9 + C3-9）：
    #   ① full_audit_at 是 HEAD 祖先（允许其后只提交 docs/账本/新增探针）；
    #   ② `full_audit_tree` == `git rev-parse <fa>^{tree}` —— 发布在 D 上跑，不能拿 HEAD 树比；
    #   ③ `tree_dirty is False`、条目 verdict 全 red-assert 且 at==fa、继承 0、条数==MUTATIONS。
    ledger = json.loads((REPO / "tests" / "mutation-verdicts.json").read_text(encoding="utf-8"))
    entries = ledger.get("entries") or {}
    meta = ledger.get("_meta") or {}
    n_assert, n_all = len(entries), int(m.group(2))
    fa = meta.get("full_audit_at") or ""
    head_full = run(["git", "rev-parse", "HEAD"])[1].strip()
    if not re.fullmatch(r"[0-9a-f]{7,40}", fa):
        raise SystemExit(f"❌ full_audit_at={fa!r} 不是 7-40 位十六进制 commit 短/全码")
    fa_type = run(["git", "cat-file", "-t", fa])[1].strip()
    if fa_type != "commit":
        raise SystemExit(f"❌ full_audit_at={fa!r} 不是 commit（git cat-file -t={fa_type!r}）")
    ledger_commits = run(["git", "log", "--format=%H", f"{fa}..HEAD", "--",
                          "tests/mutation-verdicts.json"])[1].split()
    if ledger_commits != [head_full]:
        raise SystemExit(f"❌ 盖章后账本被 {len(ledger_commits)} 个 commit 改过"
                         f"（期望恰好当前 HEAD 这一个）：{[c[:8] for c in ledger_commits]}")
    if n_assert != n_all or n_all != n_mut:
        raise SystemExit(f"❌ 账本条目数 {n_assert} != 变异总数 {n_all} != len(MUTATIONS) {n_mut}")
    anc = bool(fa) and subprocess.run(["git", "merge-base", "--is-ancestor", fa, "HEAD"],
                                      cwd=REPO).returncode == 0
    if not anc:
        raise SystemExit(f"❌ full_audit_at={fa!r} 不是 HEAD={head!r} 的祖先（账本与代码脱节）")
    if meta.get("tree_dirty") is not False:
        raise SystemExit(f"❌ 账本 tree_dirty={meta.get('tree_dirty')!r} —— 全量跑时树不干净")
    fat = str(meta.get("full_audit_tree") or "")
    real_tree = run(["git", "rev-parse", f"{fa}^{{tree}}"])[1].strip()
    if not fat or fat != real_tree:
        raise SystemExit(f"❌ full_audit_tree={fat!r} != git rev-parse {fa}^{{tree}}={real_tree!r}")
    drift = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=MD", f"{fa}..HEAD", "--",
         "core", "tests", "plugin.yaml", "tools", "docs/audits/v0.7.3",
         ":(exclude)tests/mutation-verdicts.json", ":(exclude)tests/probe_*.py"],
        cwd=REPO, capture_output=True, text=True).stdout.strip()
    if drift:
        raise SystemExit(f"❌ 自 full_audit_at={fa} 起，指纹相关路径被改过 ⇒ 账本作废：\n{drift}")
    n_inh = sum(1 for v in entries.values() if (v.get("at") or "") != fa)
    if n_inh:
        raise SystemExit(f"❌ 有 {n_inh} 条条目的 at != full_audit_at({fa}) —— 必须同一棵树")
    bad_verdicts = [k for k, v in entries.items() if (v.get("verdict") or "") != "red-assert"]
    if bad_verdicts:
        raise SystemExit(f"❌ 有 {len(bad_verdicts)} 条非 red-assert 条目：{bad_verdicts[:8]}")
    log("1a)", f"账本 _meta：full_audit_at={fa}（祖先 ✓）· tree_dirty=False ✓ · "
              f"full_audit_tree={fat[:12]} == {fa}^{{tree}} ✓ · {n_assert} 条 red-assert · "
              f"指纹路径未改 ✓ · 继承 {n_inh} 条 ✓")
    # 1b) 加固版合并/校验器（现场重算三指纹 + 日志覆盖 + 12 名对照全绿）
    # ⚠️ 合并器**固化在仓内**（`tools/merge_ledger4.py`，2026-09-22 从 scratch 搬进来）：
    # 它不在就**硬失败** —— 依赖一份 scratch 路径等于「下次发布时证据链悄悄断掉」
    # （计划 §2 末行处置⑤）。
    v4 = pathlib.Path(REPO, "tools", "merge_ledger4.py")
    if not v4.exists():
        raise SystemExit(f"❌ 缺合并器 {v4}（证据链断了，先去把它固化进仓）")
    d = pathlib.Path.home() / ".larkdeck-scratch" / "v0.7.3"
    evdir = d / f"evidence-{fa}"
    ev_path = evdir / "full-run-evidence.json"
    if not ev_path.exists():
        raise SystemExit(f"❌ 缺全量证据 {ev_path}")
    ev = json.loads(ev_path.read_text(encoding="utf-8"))
    if (ev.get("head_short") or "") != fa:
        raise SystemExit(f"❌ 证据 head={ev.get('head_short')!r} != full_audit_at={fa!r}")
    if (ev.get("full_audit_tree") or "") != fat:
        raise SystemExit(f"❌ 证据 tree={ev.get('full_audit_tree')!r} != full_audit_tree={fat!r}")
    if not evdir.is_dir():
        raise SystemExit(f"❌ 缺证据目录 {evdir}")
    probs = ev.get("problems")
    if probs != []:
        raise SystemExit(f"❌ runner 自报 problems 非空：{probs!r}")
    rc = ev.get("shard_rc") or {}
    if "timeout" in rc or any(rc.get(str(i)) != 0 for i in range(1, 7)):
        raise SystemExit(f"❌ shard_rc 非全 0：{rc!r}")
    if ev.get("merge_dry_rc") != 0 or ev.get("merge_rc") != 0:
        raise SystemExit(f"❌ merge rc 非 0：dry={ev.get('merge_dry_rc')!r} write={ev.get('merge_rc')!r}")
    if ev.get("n_inh") != 0 or ev.get("tree_dirty") is not False:
        raise SystemExit(f"❌ 证据 n_inh/tree_dirty 异常：{ev.get('n_inh')!r}/{ev.get('tree_dirty')!r}")
    led_now = hashlib.sha256((REPO / "tests" / "mutation-verdicts.json").read_bytes()).hexdigest()
    if ev.get("ledger_sha256_at_write") != led_now:
        raise SystemExit("❌ 账本 sha256 与盖章时不一致（章后账本被改过）："
                         f"now={led_now[:12]} evidence={str(ev.get('ledger_sha256_at_write'))[:12]}")
    envfp = ev.get("env_fp") or {}
    if envfp.get("merge_tool_sha256") != hashlib.sha256(
            (v4).read_bytes()).hexdigest():
        raise SystemExit("❌ 合并器与盖章时不是同一份（tools/merge_ledger4.py 变了）")
    if envfp.get("mutate_check_sha256") != hashlib.sha256(
            (REPO / "tests" / "mutate_check.py").read_bytes()).hexdigest():
        raise SystemExit("❌ mutate_check.py 与盖章时不是同一份")
    runner_now = hashlib.sha256((pathlib.Path.home() / ".larkdeck-scratch" /
                                   "v0.7.3-run_full_v2.py").read_bytes()).hexdigest()
    if envfp.get("runner_script_sha256") != runner_now:
        raise SystemExit("❌ 执行中的 runner 与盖章时不是同一份")
    release_now = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
    captured = envfp.get("release_script_sha256") or ""
    if captured:
        if captured != release_now:
            raise SystemExit("❌ 执行中的 release 脚本与盖章时不是同一份")
    else:
        # runner 的 release_script_sha256 因 scratch 路径写错为空（见 p3-full-run-<fa>.md
        # 「发布脚本 sha256」行）；改为用 git 锚定的审计文档做替代校验。
        md = REPO / "docs" / "audits" / "v0.7.3" / f"p3-full-run-{fa}.md"
        m = re.search(r"release_script_sha256_at_write=([0-9a-f]{64})", md.read_text(encoding="utf-8")) if md.exists() else None
        if not m or m.group(1) != release_now:
            raise SystemExit(f"❌ release 脚本未被指纹钉住：env_fp 为空且 {md} 对不上")
        log("1b)", "⚠️ runner 未捕获 release 脚本哈希（路径 bug）；已用 git 锚定文档校验通过")
    arts = ev.get("artifacts") or {}
    need_art = {f"{k}{i}" for k in ("log", "seed", "inventory") for i in range(1, 7)}
    if not need_art <= set(arts):
        raise SystemExit(f"❌ 证据 artifacts 缺项：{sorted(need_art - set(arts))}")
    for key, rec in (ev.get("artifacts") or {}).items():
        ap = pathlib.Path(rec.get("path") or "")
        if not ap.exists():
            if key.startswith("fixed_"):
                continue
            raise SystemExit(f"❌ 证据产物缺失 {key}: {ap}")
        h = hashlib.sha256(ap.read_bytes()).hexdigest()
        if h != rec.get("sha256"):
            raise SystemExit(f"❌ 证据产物被改 {key}: {ap} 现={h[:12]} 记={str(rec.get('sha256'))[:12]}")
    cmd = [PY, str(v4), "--head", fa, "--base", "tests/mutation-verdicts.json"]
    for i in range(1, 7):
        cmd += ["--fresh", str(evdir / f"seed{i}.json"), "--log", str(evdir / f"shard{i}.log")]
    _, v4out = run(cmd, cwd=REPO, timeout=300)
    ok_line = next((l for l in v4out.splitlines() if l.startswith("✅")), "")
    if not ok_line:
        raise SystemExit(f"❌ 加固合并校验没通过：\n{v4out[-600:]}")
    at_line = next((l for l in v4out.splitlines() if "at 取值：" in l), "")
    try:
        ats = ast.literal_eval(at_line.split("at 取值：", 1)[1].strip())
    except Exception:
        ats = []
    if ats != [fa]:
        raise SystemExit(f"❌ merge 的 at 取值不是 [fa]：{at_line!r}（seed 未命中本轮？）")
    log("1b)", ok_line.strip())

    # ⚠️ 这个校验必须在 `if dry: return` **之前**（审计 C2）：否则 `--check` 根本不覆盖它，
    # 而它正是「P6 先部署、再打 tag」那条顺序的机械保证。
    dep_head = run(["git", "-C", str(DEPLOY), "rev-parse", "HEAD"])[1].strip()
    head_full = run(["git", "rev-parse", "HEAD"])[1].strip()
    if dep_head != head_full:
        raise SystemExit(f"❌ .deploy={dep_head[:7]} != HEAD={head_full[:7]}："
                         f"先部署到本 HEAD（含用户真机终验）再发布；--check 同样硬失败")
    log("1c)", f"P4 前置：.deploy 已指到 HEAD（{dep_head[:7]}）✓")

    if dry:
        log("—", "dry-run 结束（--go 才执行 tag/push/release/部署/重启）")
        return 0

    # ---- 2. tag + push ------------------------------------------------------
    log("2)", f"打 tag {TAG} 并推送")
    if tags:
        raise SystemExit(f"❌ tag {TAG} 已存在：人工确认后再处理（脚本不覆盖 tag）")
    run(["git", "tag", "-a", TAG, "-m", TAG_MSG])
    run(["git", "push", "origin", "main"], timeout=300)
    run(["git", "push", "origin", TAG], timeout=300)
    log("2)", "已推送 main + tag")

    # ---- 3. release ---------------------------------------------------------
    log("3)", "创建 GitHub Release")
    _, out = run(["gh", "release", "create", TAG, "--title", f"LarkDeck {TAG}",
                  "--notes-file", "docs/releases/v0.7.3.md"], timeout=300)
    log("3)", out.strip().splitlines()[-1] if out.strip() else "release created")

    # ---- 4. .deploy 指到 tag ------------------------------------------------
    log("4)", ".deploy 指向 tag 提交")
    commit = run(["git", "rev-parse", f"{TAG}^{{commit}}"])[1].strip()
    run(["git", "-C", str(DEPLOY), "checkout", "-q", commit], timeout=120)
    now = run(["git", "-C", str(DEPLOY), "rev-parse", "HEAD"])[1].strip()
    if now != commit:
        raise SystemExit(f"❌ .deploy 现在 {now[:7]}，期望 {commit[:7]}")
    log("4)", f".deploy = {commit[:7]} ✓")

    # ---- 5. 重启网关 + 有界等待自检 ----------------------------------------
    log("5)", "重启网关")
    started = time.time()
    run([HERMES, "gateway", "restart"], timeout=180)
    ok, line = wait_for_selfcheck(started, cap_s=150.0)
    if not ok:
        raise SystemExit("❌ 150s 内没等到晚于重启时刻的「启动自检通过」—— 人工检查")
    log("5)", f"自检通过：{line[:160]}")

    # ---- 6. live 核对 -------------------------------------------------------
    log("6)", "live 核对")
    n = subprocess.run(["pgrep", "-f", "hermes_cli.main gateway run"],
                       capture_output=True, text=True).stdout.split()
    log("6)", f"网关进程 {len(n)} 个；请让用户发一条真实消息，确认卡片形态（live verified）")
    log("6)", "完成：push + tag + release + 部署 + 重启都留了痕（本文输出即证据）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
