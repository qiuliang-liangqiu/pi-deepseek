#!/usr/bin/env python3
"""platform_approve.py: create a one-time approval for an instance-only submission.

Run this YOURSELF in an interactive terminal. It refuses when stdin is not a TTY,
so an agent cannot silently approve its own submission via a pipe/here-string.

Usage:
  python ~/.pi/agent/bin/platform_approve.py \
    --instance-id <instance-id> \
    --challenge <challenge-id> \
    [--competition <competition-id>] \
    --answer-file <file-with-flag>

Then give the printed approval path to the agent:
  python ~/.pi/agent/bin/platform_do.py submit --instance-id <id> \
    --answer-file <file> --approval <approval-path>
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path

APPROVALS = Path.home() / ".pi" / "agent" / "approvals"


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a one-time approval (user TTY only).")
    parser.add_argument("--instance-id", required=True, help="instance id, or 'qa' for a QA challenge")
    parser.add_argument("--challenge", required=True, help="challenge id")
    parser.add_argument("--competition", default="", help="competition id (optional)")
    parser.add_argument("--answer-file", required=True, help="file containing the flag")
    parser.add_argument("--ttl", type=int, default=600, help="approval lifetime in seconds (default 600)")
    args = parser.parse_args()

    if not sys.stdin.isatty():
        print(
            "REFUSED: platform_approve.py must be run by the user in an interactive terminal.\n"
            "The agent must not create approvals via pipes, here-strings, or scripts.",
            file=sys.stderr,
        )
        return 2

    try:
        answer = Path(args.answer_file).read_text(encoding="utf-8", errors="replace").strip()
    except Exception as exc:  # noqa: BLE001
        print(f"cannot read answer file: {exc}", file=sys.stderr)
        return 1
    if not answer:
        print("answer file is empty", file=sys.stderr)
        return 1

    ans_hash = hashlib.sha256(answer.encode("utf-8")).hexdigest()
    print("Authorize this submission?")
    print(f"  instance : {args.instance_id}")
    print(f"  challenge: {args.challenge}")
    if args.competition:
        print(f"  competition: {args.competition}")
    print(f"  answer sha256: {ans_hash}")
    try:
        got = input("Type APPROVE to authorize: ").strip()
    except EOFError:
        got = ""
    if got != "APPROVE":
        print("not authorized", file=sys.stderr)
        return 1

    APPROVALS.mkdir(parents=True, exist_ok=True)
    nonce = secrets.token_hex(16)
    rec = {
        "action": "submit",
        "instance_id": args.instance_id,
        "challenge_id": args.challenge,
        "competition_id": args.competition or "",
        "answer_sha256": ans_hash,
        "expires": time.time() + max(30, args.ttl),
        "nonce": nonce,
        "created_by": "user-tty",
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    path = APPROVALS / f"{nonce}.json"
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
