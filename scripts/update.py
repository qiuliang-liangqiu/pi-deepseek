#!/usr/bin/env python3
"""Update an existing install from a newer checkout, without losing user config.

    python scripts/update.py                  # 计划 + 询问 + 执行
    python scripts/update.py --dry-run        # 只看会改什么
    python scripts/update.py --yes            # 不问
    python scripts/update.py --record-only    # 只写基线清单（bootstrap 用）
    python scripts/update.py --keep-local     # 遇到本地改动：保留本地，不覆盖
    python scripts/update.py --prune          # 删除上游已移除的文件（默认保留并报告）
    python scripts/update.py --preflight      # 只跑本地工具完整性检查，不改文件

Release: python scripts/update.py --record-only --agent-dir agent
         (writes agent/.workbench.json; commit it with the release)
    python scripts/update.py --ignore-hold    # 忽略 agent/.update-hold（默认拒绝更新）

Only these are touched (the "config layer"):
    AGENTS.md  VERSION  agents/  bin/  extensions/  prompts/  skills/

Never touched: auth.json, settings.json, mcp.json, models.json, toolkit.json,
sessions/, teams/, backups/ — plus anything you added outside the list above.

Conflict handling
-----------------
A manifest at <agent-dir>/.workbench.json records the sha256 of every file we
installed. On update:

  installed == manifest hash   -> safe to replace (you never edited it)
  installed != manifest hash   -> you edited it locally:
        * identical to the new file -> skip
        * otherwise            -> copy to <file>.local-<stamp>, then replace
                                  (or keep yours with --keep-local)

  No baseline (unknown) is treated like a local edit: copy to .local-<stamp>
  before replacing, so a missing .workbench.json never silently loses local work.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MANIFEST = ".workbench.json"
LAYOUT = ["AGENTS.md", "VERSION", "agents", "bin", "extensions", "prompts", "skills", "worker-extensions", "knowledge"]
PROTECTED = {"auth.json", "settings.json", "mcp.json", "models.json", "models-store.json",
             "toolkit.json", "triage_state.json", "sessions", "teams", "backups", ".workbench.json"}


def sha(p: Path) -> str:
    # Line endings are not content: hash CRLF-normalised bytes so a Windows
    # checkout, a Linux clone and a copied install all agree (otherwise every
    # other machine would report content_diff for the whole tree).
    data = p.read_bytes().replace(bytes((13, 10)), bytes((10,)))
    return hashlib.sha256(data).hexdigest()

def repo_commit(repo: Path) -> str:
    try:
        p = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                           capture_output=True, timeout=20)
        return p.stdout.decode().strip() or "unversioned"
    except Exception:
        return "unversioned"


def source_files(repo: Path) -> dict[str, Path]:
    src = repo / "agent"
    out: dict[str, Path] = {}
    for name in LAYOUT:
        p = src / name
        if not p.exists():
            continue
        if p.is_file():
            out[name] = p
            continue
        for f in p.rglob("*"):
            if not f.is_file() or "__pycache__" in f.parts:
                continue
            name = f.name
            if name.endswith((".pyc", ".pyo")) or ".bak" in name or ".local-" in name:
                continue
            if name.startswith((".prompt-", ".DS_Store")):
                continue
            rel_parts = f.relative_to(src).parts
            if any(part.startswith("parked") for part in rel_parts):
                continue
            out[f.relative_to(src).as_posix()] = f
    return out


def load_manifest(agent: Path) -> dict:
    try:
        return json.loads((agent / MANIFEST).read_text(encoding="utf-8"))
    except Exception:
        return {}


def repo_version(repo: Path) -> str:
    try:
        first = (repo / "agent" / "VERSION").read_text(encoding="utf-8-sig").splitlines()[0].strip()
        return first
    except Exception:
        return ""


def write_manifest(agent: Path, repo: Path, files: dict[str, Path]) -> None:
    data = {
        "schema": "workbench/1",
        "commit": repo_commit(repo),
        "version": repo_version(repo),
        "repo": str(repo),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": {rel: sha(p) for rel, p in sorted(files.items())},
    }
    agent.mkdir(parents=True, exist_ok=True)
    (agent / MANIFEST).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                  encoding="utf-8")


def plan_update(agent: Path, sources: dict[str, Path]) -> list[dict]:
    manifest = load_manifest(agent)
    known = manifest.get("files") or {}
    rows = []
    for rel, src in sorted(sources.items()):
        dst = agent / rel
        new_hash = sha(src)
        old_hash = known.get(rel)
        if not dst.exists():
            state = "new"
            cur = ""
        else:
            cur = sha(dst)
            if cur == new_hash:
                state = "same"
            elif old_hash and cur == old_hash:
                state = "changed"          # 用户没改过，直接覆盖
            elif old_hash:
                state = "conflict"         # 用户改过
            else:
                state = "unknown"          # 没有基线，无法判断是否改过
        rows.append({"rel": rel, "src": src, "dst": dst, "state": state,
                     "cur": cur, "new": new_hash, "old": old_hash or ""})
    return rows


def _run_ok(argv: list[str], timeout: int = 60, env: dict | None = None) -> tuple[bool, str]:
    try:
        p = subprocess.run(argv, capture_output=True, timeout=timeout, env=env)
        detail = (p.stdout + p.stderr).decode("utf-8", "replace").strip().splitlines()
        return p.returncode == 0, (detail[-1][:200] if detail else "")
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def smoke(agent: Path) -> list[tuple[str, bool]]:
    """Local completeness checks (files, syntax, JSON, core scripts).

    Missing files are failures here (the old version skipped them). External
    binaries/packages are not checked; use the toolkit for that.
    """
    py = sys.executable
    out: list[tuple[str, bool]] = []

    for rel in ("AGENTS.md", "VERSION"):
        out.append((f"file {rel}", (agent / rel).is_file()))

    bin_dir = agent / "bin"
    if not bin_dir.is_dir():
        out.append(("bin/", False))
    else:
        for p in sorted(bin_dir.glob("*.py")):
            try:
                compile(p.read_text(encoding="utf-8", errors="replace"), str(p), "exec")
                out.append((f"py {p.name}", True))
            except Exception:  # noqa: BLE001
                out.append((f"py {p.name}", False))

    # Config JSON: optional files are parsed only when present.
    for rel in ("settings.json", "mcp.json", "toolkit.json", "run_gate.json",
                "triage_state.json", "models-store.json", "install/managed-install.json"):
        p = agent / rel
        if not p.exists():
            continue
        try:
            json.loads(p.read_text(encoding="utf-8", errors="replace"))
            out.append((f"json {rel}", True))
        except Exception:  # noqa: BLE001
            out.append((f"json {rel}", False))

    ext_root = agent / "extensions"
    if ext_root.is_dir():
        for d in sorted(ext_root.iterdir()):
            if not d.is_dir() or d.name.startswith((".", "_", "parked")):
                continue
            has = (d / "index.ts").is_file() or (d / "index.js").is_file()
            out.append((f"ext {d.name}", has))
        checker = bin_dir / "check_extensions.mjs"
        node = shutil.which("node")
        if checker.is_file() and node:
            ok, _detail = _run_ok([node, "--no-warnings", str(checker), str(ext_root)])
            out.append(("ext parse", ok))
    else:
        out.append(("extensions/", False))

    worker_root = agent / "worker-extensions"
    if worker_root.is_dir():
        out.append(("ext worker-guard", (worker_root / "worker-guard" / "index.ts").is_file()))
    else:
        out.append(("worker-extensions/", False))

    runtime = [
        ("scan_injection", [py, str(bin_dir / "scan_injection.py"), "--help"]),
        ("triage_gate", [py, str(bin_dir / "triage_gate.py"), "status"]),
        ("run_gate", [py, str(bin_dir / "run_gate.py"), "status", "--quiet"]),
        ("team_pool", [py, str(bin_dir / "team_pool.py"), "doctor"]),
        ("platform_audit", [py, str(bin_dir / "platform_audit.py"), "--help"]),
        ("platform_approve", [py, str(bin_dir / "platform_approve.py"), "--help"]),
    ]
    for name, argv in runtime:
        if not Path(argv[1]).is_file():
            out.append((name, False))
            continue
        ok, _detail = _run_ok(argv)
        out.append((name, ok))

    # The old argparse bug accepted `--help` (exits 0) but not the real
    # `submit` subcommand. A QA dry-run is side-effect free: the fixed parser
    # emits would_submit (0); the broken parser returns 2.
    pd = bin_dir / "platform_do.py"
    if not pd.is_file():
        out.append(("platform_do", False))
    else:
        tmp = Path(tempfile.mkdtemp(prefix="pi-preflight-"))
        try:
            answer = tmp / "answer.txt"
            answer.write_text("preflight-answer", encoding="utf-8")
            env = {**os.environ, "HOME": str(tmp), "USERPROFILE": str(tmp)}
            ok, _detail = _run_ok(
                [py, str(pd), "submit", "--qa", "--competition", "preflight",
                 "--challenge", "preflight", "--answer-file", str(answer), "--dry-run"],
                env=env,
            )
            out.append(("platform_do", ok))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return out


def print_smoke(results: list[tuple[str, bool]]) -> bool:
    for name, passed in results:
        print(f"  {'✓' if passed else '!'} {name}")
    return all(passed for _, passed in results)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--agent-dir",
                    default=str(Path(os.environ.get("PI_AGENT_DIR") or (Path.home() / ".pi" / "agent"))))
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", "-y", action="store_true")
    ap.add_argument("--keep-local", action="store_true")
    ap.add_argument("--prune", action="store_true")
    ap.add_argument("--record-only", action="store_true")
    ap.add_argument("--ignore-hold", action="store_true",
                    help="ignore agent/.update-hold")
    ap.add_argument("--preflight", action="store_true",
                    help="only run local completeness checks (files/syntax/JSON/scripts) and exit")
    args = ap.parse_args()

    agent = Path(args.agent_dir).expanduser()

    if args.preflight:
        print(f"agent   {agent}")
        ok = print_smoke(smoke(agent))
        print("\n本地工具检查通过。" if ok else "\n本地工具检查有失败项。")
        return 0 if ok else 1

    repo = Path(args.repo).expanduser().resolve()
    sources = source_files(repo)
    if not sources:
        print(f"no agent/ sources under {repo}", file=sys.stderr)
        return 2

    if args.record_only:
        write_manifest(agent, repo, sources)
        print(f"manifest written: {agent / MANIFEST} ({len(sources)} files)")
        return 0

    hold = agent / ".update-hold"
    if hold.exists() and not args.ignore_hold:
        print(f"agent has {hold.name}; refusing to update (read it, or pass --ignore-hold): {hold}",
              file=sys.stderr)
        return 1

    manifest = load_manifest(agent)
    print(f"repo    {repo} @ {repo_commit(repo)}")
    print(f"agent   {agent}")
    print(f"baseline {manifest.get('commit', 'none')} ({len(manifest.get('files') or {})} files)")

    rows = plan_update(agent, sources)
    buckets = {k: [r for r in rows if r["state"] == k]
               for k in ("new", "changed", "conflict", "unknown", "same")}
    print(f"\n计划：新增 {len(buckets['new'])} · 更新 {len(buckets['changed'])} · "
          f"冲突 {len(buckets['conflict'])} · 不变 {len(buckets['same'])}")
    for r in buckets["conflict"]:
        print(f"  ! 本地改过: {r['rel']}")
    if buckets["unknown"]:
        print(f"  ! 另有 {len(buckets['unknown'])} 个文件没有基线（旧安装或你手动同步过），"
              f"升级会先备份再覆盖")
    if args.prune:
        stale = sorted(set(manifest.get("files") or {}) - set(sources))
        if stale:
            print(f"\n--prune：将删除上游已移除的 {len(stale)} 个文件")
            for rel in stale[:10]:
                print(f"  - {rel}")

    todo = buckets["new"] + buckets["changed"] +         ([] if args.keep_local else (buckets["conflict"] + buckets["unknown"]))
    if not todo and not (args.prune and (set(manifest.get('files') or {}) - set(sources))):
        print("\n已经是最新，无需更新。")
        if args.dry_run:
            return 0
        return 0

    if args.dry_run:
        for r in todo:
            print(f"  {r['state']:<8} {r['rel']}")
        print("\n本地工具检查：")
        print_smoke(smoke(agent))
        return 0

    if not args.yes:
        try:
            if input(f"\n更新以上 {len(todo)} 个文件？[Y/n] ").strip().lower() == "n":
                print("已取消")
                return 0
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = agent / "backups" / f"{stamp}-update"
    copied = 0
    for r in todo:
        if r["dst"].exists():
            rel = r["dst"].relative_to(agent)
            bp = backup / rel
            bp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(r["dst"], bp)
        if r["state"] in ("conflict", "unknown"):
            keep = r["dst"].with_name(r["dst"].name + f".local-{stamp}")
            shutil.copy2(r["dst"], keep)
            print(f"  ! 保留你的版本: {keep.relative_to(agent)}")
        r["dst"].parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(r["src"], r["dst"])
        copied += 1
    if args.prune:
        for rel in sorted(set(manifest.get("files") or {}) - set(sources)):
            p = agent / rel
            if p.exists():
                p.unlink()
                print(f"  - 删除 {rel}")

    if not backup.exists():
        backup = None
    write_manifest(agent, repo, sources)
    print(f"\n已更新 {copied} 个文件" + (f"，备份在 {backup}" if backup else ""))

    ok = print_smoke(smoke(agent))
    if not ok:
        print("\n本地工具检查有失败项（不一定由本次更新造成）。")
        print("先修失败项；只有当更新后才出现的新失败，才按下面回滚：")
        if backup:
            print(f"  cp -a {backup}/. {agent}/")
        return 1
    print("\n完成。重启 pi 或执行 /reload 让扩展生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
