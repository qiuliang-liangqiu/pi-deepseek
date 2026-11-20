#!/usr/bin/env python3
"""Environment report for pi-agent workbench.

    python scripts/doctor.py            # human readable
    python scripts/doctor.py --json     # machine readable
    python scripts/doctor.py --strict   # exit 1 on any warning

Checks: python/node/pi, agent dir contents, essential files, PI_WORKBENCH,
settings/auth presence (never prints contents), toolbox import, mirror manifest.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = Path(os.environ.get("PI_AGENT_DIR") or (Path.home() / ".pi" / "agent"))
WORKBENCH = Path(os.environ.get("PI_WORKBENCH") or (ROOT / "toolbox"))

OK, WARN, FAIL = "ok", "warn", "fail"
rows: list[tuple[str, str, str]] = []


def add(name: str, status: str, detail: str = "") -> None:
    rows.append((name, status, detail))


def run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else ""
    except Exception:
        return None


def _utf8_stdout() -> None:
    """Windows consoles default to GBK; never crash on a checkmark."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    # ---- runtime
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    add("python", OK if sys.version_info >= (3, 8) else FAIL, py)

    node = run(["node", "-v"])
    add("node", OK if node else WARN, node or "not found (eval/mirror skipped)")

    pi = run(["pi", "--version"])
    add("pi", OK if pi else WARN, pi or "not on PATH (npm i -g @earendil-works/pi-coding-agent)")

    # ---- agent dir
    add("agent dir", OK if AGENT.is_dir() else FAIL, str(AGENT))
    for item in ("AGENTS.md", "agents", "bin", "extensions", "prompts", "skills"):
        p = AGENT / item
        add(f"  {item}", OK if p.exists() else WARN, "" if p.exists() else "missing - run scripts/bootstrap")

    for name in ("stall_gate.py", "triage_gate.py", "scan_injection.py", "team_pool.py"):
        p = AGENT / "bin" / name
        add(f"  bin/{name}", OK if p.exists() else WARN)

    # ---- config presence (never contents)
    for name, needed in (("settings.json", True), ("auth.json", True), ("mcp.json", False)):
        p = AGENT / name
        if p.exists():
            add(f"  {name}", OK, f"{p.stat().st_size} bytes")
        else:
            add(f"  {name}", WARN if needed else OK, "missing" if needed else "optional")

    # ---- toolbox
    add("PI_WORKBENCH", OK if WORKBENCH.is_dir() else FAIL, str(WORKBENCH))
    add("  framework", OK if (WORKBENCH / "framework" / "cli.py").exists() else FAIL)
    add("  docs", OK if (WORKBENCH / "docs" / "SOP.md").exists() else WARN, "SOP/PLAYBOOK/PITFALLS")
    base = os.environ.get("TSEC_BASE_URL")
    add("TSEC_BASE_URL", OK if base else WARN, base or "unset (toolbox needs it; no default)")

    try:
        sys.path.insert(0, str(WORKBENCH))
        import framework.cli  # noqa: F401
        add("  framework import", OK)
    except Exception as exc:  # pragma: no cover - diagnostic path
        add("  framework import", FAIL, f"{type(exc).__name__}: {exc}"[:80])

    # ---- eval mirror
    manifest = ROOT / "eval" / "mirror" / "manifest.json"
    add("eval/mirror", OK if manifest.exists() else WARN, "run: node eval/mirror/score.mjs verify")

    # ---- secrets
    if (ROOT / "scripts" / "check_no_secrets.py").exists():
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_no_secrets.py"), "--quiet"],
            capture_output=True, text=True,
        )
        add("no-secrets scan", OK if out.returncode == 0 else FAIL,
            "clean" if out.returncode == 0 else out.stdout.strip().splitlines()[0][:70])

    # ---- output
    worst = FAIL if any(s == FAIL for _, s, _ in rows) else (WARN if any(s == WARN for _, s, _ in rows) else OK)
    if args.json:
        print(json.dumps({"status": worst, "checks": [{"name": n, "status": s, "detail": d} for n, s, d in rows]},
                         ensure_ascii=False, indent=2))
    else:
        icon = {OK: "✓", WARN: "!", FAIL: "✗"}
        width = max(len(n) for n, _, _ in rows) + 2
        print(f"pi-agent doctor  ({worst})")
        for name, status, detail in rows:
            print(f"  {icon[status]} {name:<{width}} {detail}")
        if worst != OK:
            print("\nnext: bash scripts/bootstrap.sh   (or -File scripts/bootstrap.ps1)")
    return 1 if (args.strict and worst != OK) else 0


if __name__ == "__main__":
    raise SystemExit(main())
