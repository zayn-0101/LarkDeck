# SNAPSHOT（v0.7.3）：用于证据 a2290da 的 scratch runner。
# 路径常量按维护者本机设置脱敏为 <HOME>；库内仅作审计/复现记录，install.sh 不安装 tools/*。

#!/usr/bin/env python3
"""v0.7.3 P3 全量变异（v2：先验片、后盖章；每轮独立目录 + 证据哈希 + 禁旧 at 白名单）。

审计 357edffb 后的加固版 runner。与 v1 的差别：
  * 每片日志/账本写进 `run-<head>-<stamp>/`，不再直接覆盖固定文件名；成功后才会把
    seed{i}.json / shard{i}.log 复制到 release 脚本读取的固定路径（并另存带 head 的证据目录）；
  * **先验证 6 片 rc、日志条数/名字集合/对照、seed 条数/verdict/at、冻结 HEAD/干净树，
    全部通过才调用 `merge_ledger4.py --write`**（v1 是先 merge 后校验）；
  * 合并**不带旧 fa 的 --allow-at**：base 里所有条目都必须在 fresh 里以当前 head 重记，
    否则 merge 失败 —— 不允许继承盖章；
  * 逐条解析 `🔴` 行，要求声明门禁出现在该行的 `断言红=[...]` 里（审计 P1 归属漂移守卫）；
  * 完事对 6 份 log、6 份 seed、6 份 inventory 算 sha256 写进 evidence，之后任何覆盖都
    能被对账（固定文件名被覆盖不再等于证据消失）。

只读校验 + 只写 `~/.larkdeck-scratch/v0.7.3/`；本脚本不 commit、不 checkout、不 sleep 轮询
（Popen.wait() 阻塞等待）。用法： $PY run_full_v2.py <expected_head_short>
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import signal
import ast
import subprocess
import sys
import time

PY = "<HOME>/.hermes/hermes-agent/venv/bin/python3"
REPO = pathlib.Path("<HOME>/Code/larkdeck")
SCRATCH = pathlib.Path.home() / ".larkdeck-scratch" / "v0.7.3"
SHARDS = 6
FORBIDDEN = ("💥", "🟢", "❓", "❌ 对照变红了", "基线不是绿的", "归因不准", "变异没生效")


def run(cmd, *, cwd=REPO, env=None, timeout=None):
    p = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True,
                       timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def parse_red_line(line: str):
    body = line[len("🔴 断言失败 "):]
    name = body.split("  期望=")[0].strip()
    m = re.search(r"期望=(\S+)\s+实红=(\[[^\]]*\])\s+断言红=(\[[^\]]*\])", body)
    if not m:
        return name, None, None, None
    expect = m.group(1)
    return name, expect, m.group(2), m.group(3)


def main() -> int:
    expected = (sys.argv[1] if len(sys.argv) > 1 else "").strip().upper()
    status = run(["git", "status", "--porcelain=v1"])[1].strip()
    if status:
        raise SystemExit(f"❌ 工作树不干净，拒绝全量跑：\n{status[:400]}")
    head = run(["git", "rev-parse", "HEAD"])[1].strip()
    head_short = run(["git", "rev-parse", "--short", "HEAD"])[1].strip()
    tree = run(["git", "rev-parse", "HEAD^{tree}"])[1].strip()
    if expected and not (head.startswith(expected) or head_short.upper() == expected):
        raise SystemExit(f"❌ HEAD={head[:12]} 与期望 {expected!r} 不符（拒绝在错误提交上跑）")
    n_mut, n_ctl = (int(x) for x in run(
        [PY, "-c", "import sys; sys.path.insert(0, 'tests'); import mutate_check; "
         "print(len(mutate_check.MUTATIONS), len(mutate_check.CONTROLS))"])[1].split())
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"{head_short}-{stamp}"
    run_dir = SCRATCH / f"run-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    started_at = dt.datetime.now().isoformat(timespec="seconds")
    t0 = time.time()
    print(f"[{stamp[9:15]}] run_id={run_id} HEAD={head_short} tree={tree[:12]} "
          f"变异={n_mut} 对照={n_ctl} 目录={run_dir}", flush=True)

    # 先拿 inventory（不跑门禁），用于逐片名字集合与门禁归属对账
    inventories = {}
    for i in range(1, SHARDS + 1):
        inv_path = run_dir / f"inventory{i}.json"
        rc, out = run([PY, "tests/mutate_check.py", "--shard", f"{i}/{SHARDS}",
                       "--inventory", str(inv_path)])
        if rc != 0 or not inv_path.exists():
            raise SystemExit(f"❌ inventory {i} 失败：{out[-400:]}")
        inventories[i] = json.loads(inv_path.read_text(encoding="utf-8"))
        sel = [e for e in inventories[i]["entries"] if e.get("kind") == "mutation"]
        if len(sel) != inventories[i]["selected_mutations"]:
            raise SystemExit(f"❌ inventory {i} 自洽失败")

    pre_rc, pre_out = run([PY, "tests/run_fast.py", "--full"], timeout=600)
    pre_sha = hashlib.sha256(pre_out.encode("utf-8")).hexdigest()
    if pre_rc != 0:
        (run_dir / "manifest.json").write_text(json.dumps(
            {"run_id": run_id, "head": head, "head_short": head_short, "tree": tree,
             "preflight_full_rc": pre_rc, "preflight_full_tail": pre_out[-4000:],
             "preflight_full_sha256": pre_sha}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        raise SystemExit(f"❌ 开跑前 run_fast --full 不全绿 rc={pre_rc}：\n{pre_out[-1500:]}")
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] 开跑前 run_fast --full 全绿 "
          f"sha256={pre_sha[:12]}", flush=True)

    logs = [run_dir / f"shard{i}.log" for i in range(1, SHARDS + 1)]
    seeds = [run_dir / f"seed{i}.json" for i in range(1, SHARDS + 1)]
    procs = []
    for i in range(1, SHARDS + 1):
        logf = open(logs[i - 1], "w", encoding="utf-8")
        logf.write(f"# larkdeck run_id={run_id} head={head_short} tree={tree} "
                   f"shard={i}/{SHARDS} started={started_at}\n")
        logf.flush()
        env = {**os.environ, "LARKDECK_LEDGER_PATH": str(seeds[i - 1]),
               "PYTHONDONTWRITEBYTECODE": "1", "LARKDECK_RUN_ID": run_id}
        p = subprocess.Popen(
            [PY, "-u", "tests/mutate_check.py", "--shard", f"{i}/{SHARDS}",
             "--update-ledger"],
            cwd=str(REPO), env=env, stdout=logf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True)
        procs.append((i, p, logf))
    results = {}
    for i, p, logf in procs:
        try:
            rc = p.wait(timeout=3600)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except OSError:
                p.kill()
            rc = p.wait()
            results.setdefault("timeout", []).append(i) if isinstance(results, dict) else None
        logf.close()
        results[str(i)] = rc
    wall_s = round(time.time() - t0, 1)
    ended_at = dt.datetime.now().isoformat(timespec="seconds")
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] 分片退出码={results} 墙钟={wall_s}s",
          flush=True)

    problems: list[str] = []
    if any(rc != 0 for rc in results.values()):
        problems.append(f"分片 rc 非全 0：{results}")
    head2 = run(["git", "rev-parse", "HEAD"])[1].strip()
    tree2 = run(["git", "rev-parse", "HEAD^{tree}"])[1].strip()
    status2 = run(["git", "status", "--porcelain=v1"])[1].strip()
    if (head2, tree2) != (head, tree):
        problems.append(f"运行期间 HEAD/tree 变化：{head[:7]}/{tree[:7]} -> {head2[:7]}/{tree2[:7]}")
    if status2:
        problems.append(f"运行期间工作树变脏：\n{status2[:300]}")

    env_fp = {
        "python": sys.version.split()[0],
        "merge_tool_sha256": sha256_file(REPO / "tools" / "merge_ledger4.py"),
        "mutate_check_sha256": sha256_file(REPO / "tests" / "mutate_check.py"),
        "runner_script_sha256": sha256_file(pathlib.Path(__file__)),
        "release_script_sha256": (sha256_file(SCRATCH / "release-v0.7.3.py")
                                  if (SCRATCH / "release-v0.7.3.py").exists() else ""),
        "larkdeck_env": sorted(k for k in os.environ if k.startswith("LARKDECK_")),
    }
    cls_repo = pathlib.Path.home() / "Code" / "hermes-lark-streaming"
    if (cls_repo / ".git").exists():
        env_fp["cls_repo_head"] = run(["git", "-C", str(cls_repo), "rev-parse", "HEAD"])[1].strip()
    manifest = {"run_id": run_id, "head": head, "head_short": head_short, "tree": tree,
                "started_at": started_at, "ended_at": ended_at, "wall_s": wall_s,
                "shard_rc": results, "n_mut": n_mut, "n_ctl": n_ctl, "env_fp": env_fp,
                "preflight_full_rc": pre_rc, "preflight_full_sha256": pre_sha}
    all_red_names: list[str] = []
    seen_gate: dict[str, str] = {}
    for i in range(1, SHARDS + 1):
        text = logs[i - 1].read_text(encoding="utf-8", errors="replace")
        expected_header = (f"# larkdeck run_id={run_id} head={head_short} tree={tree} "
                           f"shard={i}/{SHARDS} started={started_at}")
        first_line = (text.splitlines() or [""])[0]
        if first_line != expected_header:
            problems.append(f"shard{i} 头部与本轮不符：{first_line!r}")
        expect_entries = [e for e in inventories[i]["entries"] if e.get("kind") == "mutation"]
        expect_names = [str(e["name"]) for e in expect_entries]
        expect_gate = {str(e["name"]): str(e.get("gate") or "") for e in expect_entries}
        for bad in FORBIDDEN:
            if bad in text:
                problems.append(f"shard{i} 日志出现禁止标记 {bad!r}")
        m = re.search(r"分片 \d+/6：选中 (\d+) 条变异 \+ (\d+) 条对照", text)
        if not m or int(m.group(1)) != len(expect_names):
            problems.append(f"shard{i} 选中条数异常：{m.group(0) if m else '无行'}"
                            f" vs inventory {len(expect_names)}")
        red_lines = [ln for ln in text.splitlines() if ln.startswith("🔴 断言失败 ")]
        if len(red_lines) != len(expect_names):
            problems.append(f"shard{i} 红线条数 {len(red_lines)} != inventory {len(expect_names)}")
        for ln in red_lines:
            name, expect, real_raw, asserted = parse_red_line(ln)
            if name not in expect_gate:
                problems.append(f"shard{i} 红行名字不在 inventory：{name}")
                continue
            gate = expect_gate[name]
            gate_norm = gate if gate.endswith(".py") else gate + ".py"
            if gate_norm not in (asserted or ""):
                problems.append(f"shard{i} 归属漂移：{name} 期望 {gate_norm}，断言红={asserted}")
            first = ""
            try:
                real_list = ast.literal_eval(real_raw) if real_raw else []
                first = str(real_list[0]) if real_list else ""
            except Exception:
                first = ""
            if first != gate_norm:
                problems.append(
                    f"shard{i} 首红门禁不等于声明门禁：{name} 声明 {gate_norm} 首红 {first!r}")
            seen_gate[name] = gate_norm
            all_red_names.append(name)
        ctl_green = text.count("⚪ 对照全绿（符合预期） ")
        if ctl_green != 2:
            problems.append(f"shard{i} 对照绿行 {ctl_green} != 2")
        if "❌ 对照变红了" in text:
            problems.append(f"shard{i} 有对照变红")
        seed = seeds[i - 1]
        if not seed.exists():
            problems.append(f"shard{i} seed 缺失")
            continue
        entries = json.loads(seed.read_text(encoding="utf-8")).get("entries") or {}
        if len(entries) != len(expect_names):
            problems.append(f"shard{i} seed 条数 {len(entries)} != {len(expect_names)}")
        if set(entries) != set(expect_names):
            problems.append(f"shard{i} seed 名字集合 != inventory")
        for name, v in entries.items():
            if v.get("verdict") != "red-assert":
                problems.append(f"shard{i} seed {name} verdict={v.get('verdict')!r}")
            if (v.get("at") or "") != head_short:
                problems.append(f"shard{i} seed {name} at={v.get('at')!r} != {head_short}")
    if len(set(all_red_names)) != n_mut:
        problems.append(f"红名去重后 {len(set(all_red_names))} != 变异总数 {n_mut}")

    manifest["problems"] = problems
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if problems:
        print("❌ 片级校验失败，**不合并、不盖章**：", flush=True)
        for p in problems[:12]:
            print("  -", p, flush=True)
        return 1

    # 片级全过才 merge（不带旧 fa 的 --allow-at：不允许任何继承盖章）
    base_cmd = [PY, str(REPO / "tools" / "merge_ledger4.py"),
                "--head", head_short, "--base", "tests/mutation-verdicts.json"]
    for i in range(1, SHARDS + 1):
        base_cmd += ["--fresh", str(seeds[i - 1]), "--log", str(logs[i - 1])]
    drc, dout = run(base_cmd, timeout=300)
    manifest["merge_dry_rc"] = drc
    manifest["merge_dry_tail"] = dout[-1200:]
    if drc != 0 or not any(ln.startswith("✅") for ln in dout.splitlines()):
        print(f"❌ 合并 dry-run 失败 rc={drc}（账本未动）：\n{dout[-1200:]}", flush=True)
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1
    cmd = base_cmd + ["--write"]
    mrc, mout = run(cmd, timeout=300)
    manifest["merge_cmd"] = cmd
    manifest["merge_rc"] = mrc
    manifest["merge_tail"] = mout[-2000:]
    if mrc != 0 or not any(ln.startswith("✅") for ln in mout.splitlines()):
        print(f"❌ 合并失败 rc={mrc}：\n{mout[-1200:]}", flush=True)
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1

    ledger = json.loads((REPO / "tests" / "mutation-verdicts.json").read_text(encoding="utf-8"))
    entries = ledger.get("entries") or {}
    meta = ledger.get("_meta") or {}
    from collections import Counter
    got_dist = Counter((g if str(g).endswith(".py") else str(g) + ".py")
                       for g in (v.get("gate") for v in entries.values()))
    want_dist = Counter(seen_gate.values())
    post = {
        "n_entries": len(entries), "full_audit_at": meta.get("full_audit_at"),
        "full_audit_tree": meta.get("full_audit_tree"),
        "tree_dirty": meta.get("tree_dirty"),
        "n_inh": sum(1 for v in entries.values()
                     if (v.get("at") or "") != (meta.get("full_audit_at") or "")),
        "bad_verdicts": [k for k, v in entries.items() if v.get("verdict") != "red-assert"],
        "gate_dist": dict(got_dist),
    }
    ok = (len(entries) == n_mut and post["full_audit_at"] == head_short
          and post["full_audit_tree"] == tree and post["tree_dirty"] is False
          and post["n_inh"] == 0 and not post["bad_verdicts"] and got_dist == want_dist)
    manifest["post_merge"] = post
    manifest["ledger_sha256_at_write"] = sha256_file(
        REPO / "tests" / "mutation-verdicts.json")
    if not ok:
        print(f"❌ 合并后账本校验失败：{json.dumps(post, ensure_ascii=False)[:800]}", flush=True)
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return 1

    # 冻结证据：先算哈希，再复制到固定名（release 脚本读取）与带 head 的证据目录
    artifacts = {}
    for i in range(1, SHARDS + 1):
        for kind, p in (("log", logs[i - 1]), ("seed", seeds[i - 1]),
                        ("inventory", run_dir / f"inventory{i}.json")):
            artifacts[f"{kind}{i}"] = {"path": str(p), "sha256": sha256_file(p),
                                       "bytes": p.stat().st_size,
                                       "mtime_ns": p.stat().st_mtime_ns}
    evidence = {**manifest, "artifacts": artifacts,
                "full_audit_at": post.get("full_audit_at"),
                "full_audit_tree": post.get("full_audit_tree"),
                "tree_dirty": post.get("tree_dirty"),
                "n_inh": post.get("n_inh"),
                "n_entries": post.get("n_entries")}
    ev_path = run_dir / "full-run-evidence.json"
    for i in range(1, SHARDS + 1):
        fs = SCRATCH / f"seed{i}.json"
        fl = SCRATCH / f"shard{i}.log"
        fs.write_bytes(seeds[i - 1].read_bytes())
        fl.write_bytes(logs[i - 1].read_bytes())
        artifacts[f"fixed_seed{i}"] = {"path": str(fs), "sha256": sha256_file(fs),
                                       "bytes": fs.stat().st_size,
                                       "mtime_ns": fs.stat().st_mtime_ns}
        artifacts[f"fixed_log{i}"] = {"path": str(fl), "sha256": sha256_file(fl),
                                      "bytes": fl.stat().st_size,
                                      "mtime_ns": fl.stat().st_mtime_ns}
    ev_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    (SCRATCH / "full-run-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (SCRATCH / f"full-run-evidence.{head_short}.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    evdir = SCRATCH / f"evidence-{head_short}"
    evdir.mkdir(exist_ok=True)
    for i in range(1, SHARDS + 1):
        (evdir / f"seed{i}.json").write_bytes(seeds[i - 1].read_bytes())
        (evdir / f"shard{i}.log").write_bytes(logs[i - 1].read_bytes())
        (evdir / f"inventory{i}.json").write_bytes((run_dir / f"inventory{i}.json").read_bytes())
    (evdir / "full-run-evidence.json").write_bytes(ev_path.read_bytes())
    (evdir / "sha256.txt").write_text(
        "".join(f"{v['sha256']}  {k}\n" for k, v in sorted(artifacts.items())),
        encoding="utf-8")
    print(f"✅ P3 完成：{n_mut}/{n_mut} red-assert、12 对照、at={head_short}、"
          f"tree={tree[:12]}、树脏={post['tree_dirty']}、继承={post['n_inh']}、"
          f"墙钟={wall_s}s、证据={evdir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
