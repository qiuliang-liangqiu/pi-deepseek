#!/usr/bin/env python3
"""publish.py — 把本机 ~/.pi/agent 的配置层发布成仓库的新版本（本地 → 仓库）。

这是和 `scripts/update.py` 相反的方向：**本机是源头**，仓库是给别的机器消费的
快照。发布时会自动 bump `agent/VERSION`，别的机器下次启动就能检测到。

    python scripts/publish.py --dry-run          # 只看本机会改仓库哪些文件
    python scripts/publish.py                    # 询问后发布（默认 bump patch）
    python scripts/publish.py --yes              # 不问
    python scripts/publish.py --set 1.2.0        # 指定版本
    python scripts/publish.py --bump minor       # patch / minor / major / none
    python scripts/publish.py --paths skills/tsec-bench extensions/update-check
    python scripts/publish.py --skip-secrets     # 跳过 secrets 扫描（自己确认过）
    python scripts/publish.py --no-push          # 只提交不推送
    python scripts/publish.py --prune            # 删除仓库里本地已删除的文件

安全闸门（任何一步失败都不写版本、不提交）：
  1. 仓库 `agent/` 必须干净（没有你自己未提交的改动）；
  2. 本机扩展必须先通过 `bin/check_extensions.mjs`（半写入/语法错误直接拒绝）；
  3. 复制后跑 `scripts/check_no_secrets.py`，命中凭据/真实 flag/绝对家目录路径就回滚。

只发布这些（和 update.py 的配置层一致）：
  AGENTS.md  agents/  bin/  extensions/  prompts/  skills/  knowledge/  worker-extensions/
auth.json / settings.json / mcp.json / sessions/ / teams/ / backups/ / workbench.env 永远不碰。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ITEMS = ["AGENTS.md", "agents", "bin", "extensions", "prompts", "skills",
         "knowledge", "worker-extensions"]
SKIP_PARTS = {"__pycache__", "node_modules", ".git", "backups"}
SKIP_SUFFIX = {".pyc", ".pyo", ".DS_Store"}
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def skip(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if any(part in SKIP_PARTS for part in rel.split("/")):
        return True
    if name in SKIP_SUFFIX or name.startswith(".update-"):
        return True
    if ".local-" in name or ".bak" in name:
        return True
    return False


def collect(root: Path, paths: list[str] | None) -> dict[str, Path]:
    items = ITEMS
    if paths:
        items = []
        for p in paths:
            p = p.strip().strip("/")
            if p.startswith("agent/"):
                p = p[len("agent/"):]
            if p:
                items.append(p)
    out: dict[str, Path] = {}
    for item in items:
        src = root / item
        if not src.exists():
            continue
        if src.is_file():
            if not skip(item):
                out[item] = src
            continue
        for f in src.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(root).as_posix()
            if not skip(rel):
                out[rel] = f
    return out


def read_version(p: Path) -> str:
    try:
        v = p.read_text(encoding="utf-8-sig").strip().splitlines()[0].strip()
        return v
    except Exception:
        return ""


def bump_version(cur: str, part: str) -> str:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", cur or "0.0.0")
    major, minor, patch = (int(x) for x in (m.groups() if m else (0, 0, 0)))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", check=check)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    ap.add_argument("--agent-dir",
                    default=os.environ.get("PI_AGENT_DIR") or str(Path.home() / ".pi" / "agent"))
    ap.add_argument("--paths", nargs="*", default=None,
                    help="只发布这些路径（相对 agent/），默认全部配置层")
    ap.add_argument("--bump", choices=["patch", "minor", "major", "none"], default="patch")
    ap.add_argument("--set", dest="set_version", default="", help="指定版本，例如 1.2.0")
    ap.add_argument("--message", default="", help="提交信息（默认自动生成）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", "-y", action="store_true")
    ap.add_argument("--prune", action="store_true", help="删除仓库里本地已删除的文件")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--skip-secrets", action="store_true")
    ap.add_argument("--skip-parse", action="store_true")
    ap.add_argument("--skip-redact", action="store_true", help="跳过脱敏（自己确认过）")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    agent = Path(args.agent_dir).expanduser().resolve()
    src_root = Path(__file__).resolve().parents[1]
    if not (repo / ".git").exists():
        print(f"不是 git 仓库：{repo}", file=sys.stderr)
        return 2
    if not (agent / "AGENTS.md").exists():
        print(f"本机 agent 目录不像 workbench 安装：{agent}", file=sys.stderr)
        return 2

    local = collect(agent, args.paths)
    if not local:
        print(f"没有可发布的文件（{agent}）", file=sys.stderr)
        return 2
    repo_agent = repo / "agent"
    remote = {rel: repo_agent / rel for rel in local if (repo_agent / rel).exists()}

    new = sorted(set(local) - set(remote))
    changed = sorted(rel for rel in remote if sha(local[rel]) != sha(repo_agent / rel))
    same = len(local) - len(new) - len(changed)
    stale = sorted(
        f.relative_to(repo_agent).as_posix()
        for f in repo_agent.rglob("*")
        if f.is_file()
        and not skip(f.relative_to(repo_agent).as_posix())
        and f.relative_to(repo_agent).as_posix() not in local
        and f.relative_to(repo_agent).as_posix() != "VERSION"
        and f.relative_to(repo_agent).as_posix() != "README.md"
    )

    cur = read_version(repo_agent / "VERSION") or read_version(agent / "VERSION") or "0.0.0"
    if args.set_version:
        if not SEMVER_RE.match(args.set_version):
            print(f"--set 不是合法 semver：{args.set_version}", file=sys.stderr)
            return 2
        new_ver = args.set_version
    elif args.bump == "none":
        new_ver = cur
    else:
        new_ver = bump_version(cur, args.bump)

    print(f"repo    {repo}")
    print(f"agent   {agent}")
    print(f"version {cur} -> {new_ver}")
    print(f"\n计划：新增 {len(new)} · 更新 {len(changed)} · 不变 {same} · 仓库多出 {len(stale)}")
    for rel in new[:20]:
        print(f"  + {rel}")
    if len(new) > 20:
        print(f"  + ... 还有 {len(new) - 20} 个")
    for rel in changed[:20]:
        print(f"  ~ {rel}")
    if len(changed) > 20:
        print(f"  ~ ... 还有 {len(changed) - 20} 个")
    if stale:
        print(f"  仓库多出的文件（加 --prune 才删除）：{len(stale)}")
        for rel in stale[:10]:
            print(f"    - {rel}")

    dirty_version = new_ver != cur
    if not new and not changed and not dirty_version and not (args.prune and stale):
        print("\n没有变化，无需发布。")
        return 0
    if args.dry_run:
        return 0

    # 闸门 1：仓库 agent/ 必须干净，失败时才能安全回滚
    st = git(repo, "status", "--porcelain", "--", "agent", check=False)
    if st.stdout.strip():
        print("\n仓库 agent/ 有未提交改动，先处理（commit / stash）再发布：", file=sys.stderr)
        print(st.stdout.strip(), file=sys.stderr)
        return 1

    # 闸门 2：本机扩展必须先解析通过
    if not args.skip_parse:
        checker = src_root / "agent" / "bin" / "check_extensions.mjs"
        node = shutil.which("node")
        if checker.is_file() and node:
            r = subprocess.run([node, "--no-warnings", str(checker), str(agent / "extensions")],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            print("\n== 解析检查\n" + (r.stdout or r.stderr).strip())
            if r.returncode != 0:
                print("扩展解析失败，拒绝发布（先修好再重启 pi / 再发布）", file=sys.stderr)
                return 1

    if not args.yes:
        try:
            if input(f"\n把以上改动写入仓库并发布 {new_ver}？[Y/n] ").strip().lower() == "n":
                print("已取消")
                return 0
        except (EOFError, KeyboardInterrupt):
            print("\n已取消")
            return 0

    # 复制
    for rel, src in sorted(local.items()):
        dst = repo_agent / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    if args.prune:
        for rel in stale:
            p = repo_agent / rel
            if p.exists():
                p.unlink()
    (repo_agent / "VERSION").write_text(new_ver + "\n", encoding="utf-8")

    # 闸门 3a：脱敏（文档自动改写，代码里的命中仍会留下）
    redactor = repo / "scripts" / "redact.py"
    if not args.skip_redact and redactor.is_file():
        r = subprocess.run([sys.executable, str(redactor), "--apply",
                            "--agent-dir", str(repo_agent)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        print("\n== 脱敏\n" + (r.stdout or r.stderr).strip())
        if r.returncode != 0:
            print("\n脱敏后仍有命中（通常是代码文件），回滚仓库 agent/：", file=sys.stderr)
            git(repo, "checkout", "--", "agent", check=False)
            git(repo, "clean", "-fdq", "--", "agent", check=False)
            return 1

    # 闸门 3b：secrets / 真实 flag / 绝对路径
    if not args.skip_secrets:
        secret_checker = repo / "scripts" / "check_no_secrets.py"
        if secret_checker.is_file():
            r = subprocess.run([sys.executable, str(secret_checker), "--quiet"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                print("\n== secrets 扫描失败，回滚仓库 agent/：", file=sys.stderr)
                print((r.stdout or r.stderr).strip(), file=sys.stderr)
                git(repo, "checkout", "--", "agent", check=False)
                git(repo, "clean", "-fdq", "--", "agent", check=False)
                print("已回滚。把命中的文件脱敏后再发布，或确认无风险加 --skip-secrets。", file=sys.stderr)
                return 1

    # 清单：和发布内容一一对应（content_diff 靠它）
    manifest_builder = repo / "scripts" / "build_manifest.py"
    if manifest_builder.is_file():
        r = subprocess.run([sys.executable, str(manifest_builder), "--repo", str(repo)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        print("\n== 清单\n" + (r.stdout or r.stderr).strip())
        if r.returncode != 0:
            print("清单生成失败，回滚仓库 agent/", file=sys.stderr)
            git(repo, "checkout", "--", "agent", check=False)
            git(repo, "clean", "-fdq", "--", "agent", check=False)
            return 1

    # 提交
    git(repo, "add", "-A", "--", "agent")
    msg = args.message or f"release: v{new_ver}"
    body = [msg, "", f"publish from {socket.gethostname()}",
            f"files: +{len(new)} ~{len(changed)}" + (f" -{len(stale)}" if args.prune else "")]
    commit = git(repo, "commit", "-m", "\n".join(body), check=False)
    if commit.returncode != 0:
        print(commit.stdout + commit.stderr, file=sys.stderr)
        return 1
    print("\n" + (commit.stdout or "").strip().splitlines()[0])

    if not args.no_push:
        push = subprocess.run(["git", "-C", str(repo), "push", "origin", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if push.returncode != 0:
            print("push 失败（commit 已保留，可手动重试）：\n" + push.stderr, file=sys.stderr)
            return 1
        print("pushed: " + (push.stderr.strip().splitlines() or ["ok"])[-1])

    # 本机对齐：版本、通知状态、基线清单
    (agent / "VERSION").write_text(new_ver + "\n", encoding="utf-8")
    for name in (".update-check.json", ".update-notified.json"):
        try:
            (agent / name).unlink()
        except FileNotFoundError:
            pass
    updater = repo / "scripts" / "update.py"
    if updater.is_file():
        subprocess.run([sys.executable, str(updater), "--agent-dir", str(agent),
                        "--repo", str(repo), "--record-only"],
                       capture_output=True)
    print(f"\n本机 {agent / 'VERSION'} -> {new_ver}")
    print("其他机器下次启动会看到新版本提示；不会自动升级。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
