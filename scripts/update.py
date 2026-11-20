#!/usr/bin/env python3
"""Update an existing install from a newer checkout, without losing user config.

    python scripts/update.py                  # 计划 + 询问 + 执行
    python scripts/update.py --dry-run        # 只看会改什么
    python scripts/update.py --yes            # 不问
    python scripts/update.py --record-only    # 只写基线清单（bootstrap 用）
    python scripts/update.py --keep-local     # 遇到本地改动：保留本地，不覆盖
    python scripts/update.py --prune          # 删除上游已移除的文件（默认保留并报告）

Only these are touched (the "config layer"):
    AGENTS.md  agents/  bin/  extensions/  prompts/  skills/

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
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

MANIFEST = ".workbench.json"
LAYOUT = ["AGENTS.md", "agents", "bin", "extensions", "prompts", "skills"]
PROTECTED = {"auth.json", "settings.json", "mcp.json", "models.json", "models-store.json",
             "toolkit.json", "triage_state.json", "sessions", "teams", "backups", ".workbench.json"}


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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
            if f.is_file() and "__pycache__" not in f.parts:
                out[f.relative_to(src).as_posix()] = f
    return out


def load_manifest(agent: Path) -> dict:
    try:
        return json.loads((agent / MANIFEST).read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_manifest(agent: Path, repo: Path, files: dict[str, Path]) -> None:
    data = {
        "schema": "workbench/1",
        "commit": repo_commit(repo),
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


def smoke(agent: Path) -> list[tuple[str, bool]]:
    py = sys.executable
    checks = [
        ("scan_injection", [py, str(agent / "bin" / "scan_injection.py"), "--help"]),
        ("triage_gate", [py, str(agent / "bin" / "triage_gate.py"), "status"]),
    ]
    out = []
    for name, argv in checks:
        if not Path(argv[1]).exists():
            continue
        try:
            rc = subprocess.run(argv, capture_output=True, timeout=60).returncode
            out.append((name, rc == 0))
        except Exception:
            out.append((name, False))
    return out


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
    args = ap.parse_args()

    agent = Path(args.agent_dir).expanduser()
    repo = Path(args.repo).expanduser().resolve()
    sources = source_files(repo)
    if not sources:
        print(f"no agent/ sources under {repo}", file=sys.stderr)
        return 2

    if args.record_only:
        write_manifest(agent, repo, sources)
        print(f"manifest written: {agent / MANIFEST} ({len(sources)} files)")
        return 0

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
        if r["state"] == "conflict":
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

    ok = True
    for name, passed in smoke(agent):
        print(f"  {'✓' if passed else '!'} {name}")
        ok &= passed
    if not ok:
        print("\n冒烟有失败；用备份回滚：")
        if backup:
            print(f"  cp -a {backup}/. {agent}/")
        return 1
    print("\n完成。重启 pi 或执行 /reload 让扩展生效。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
