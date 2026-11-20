#!/usr/bin/env python3
"""Compatibility shim: the tool catalog now lives in scripts/setup-tools.py.

Kept so older docs/commands (and bootstrap smoke tests) keep working:

    python scripts/toolkit.py --check              -> setup-tools --list
    python scripts/toolkit.py --count-missing      -> just the number (ASCII)
    python scripts/toolkit.py --install jq,ffuf    -> install those ids
    python scripts/toolkit.py --install web        -> install a whole group
    python scripts/toolkit.py --install all        -> everything missing

Everything else (WSL / VM / SSH targets, --markdown, --json) is in setup-tools.py.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load():
    spec = importlib.util.spec_from_file_location("setup_tools", HERE / "setup-tools.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["setup_tools"] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--missing", action="store_true")
    ap.add_argument("--count-missing", action="store_true")
    ap.add_argument("--install", nargs="?", const="__picker__", metavar="IDS")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", "-y", action="store_true")
    ap.add_argument("--allow-sudo", action="store_true")
    args, _rest = ap.parse_known_args()

    st = load()
    ts = st.targets()
    if not ts:
        print("no supported target detected", file=sys.stderr)
        return 2
    target = ts[0]

    if args.count_missing:
        rows = st.make_plan(target, None, None)
        print(sum(1 for r in rows if not r["installed"]))
        return 0

    if args.install:
        spec = args.install
        ids = groups = None
        if spec not in ("__picker__", "all", ""):
            parts = [x.strip() for x in spec.split(",") if x.strip()]
            ids = [p for p in parts if p in st.BY_ID]
            groups = [p for p in parts if p in st.GROUPS]
            if not ids and not groups:
                print(f"unknown tool/group: {spec}", file=sys.stderr)
                return 2
        rows = st.make_plan(target, ids or None, groups or None)
        st.show_plan(rows, target)
        todo = sum(1 for r in rows if not r["installed"])
        if not args.yes and not args.dry_run:
            try:
                prompt = chr(10) + f"安装以上 {todo} 项？[Y/n] "
                if input(prompt).strip().lower() == "n":
                    return 0
            except (EOFError, KeyboardInterrupt):
                return 0
        failed = st.install(target, rows, args.allow_sudo, args.dry_run)
        if failed:
            print("failed: " + ", ".join(sorted(set(failed))))
            return 1
        return 0

    rows = st.make_plan(target, None, None)
    st.show_plan(rows, target)
    if args.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "tool"} | {"id": r["tool"].id}
                          for r in rows], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
