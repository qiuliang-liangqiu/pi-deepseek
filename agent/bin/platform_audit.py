#!/usr/bin/env python3
"""platform_audit.py: read-only audit of platform submissions and instances.

Examples:
  python ~/.pi/agent/bin/platform_audit.py
  python ~/.pi/agent/bin/platform_audit.py --json --since 2026-11-20
  python ~/.pi/agent/bin/platform_audit.py --instances
  python ~/.pi/agent/bin/platform_audit.py --verify
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The script directory is on sys.path when run directly.
from platform_do import (  # type: ignore
    AUDIT_PATH,
    READY_STATUS,
    get_instance,
    get_instances,
    service_host,
    service_port,
    service_sni,
    services_of,
    status_of,
)


def load_entries(since: str | None, limit: int | None) -> list[dict]:
    if not AUDIT_PATH.exists():
        return []
    out: list[dict] = []
    for line in AUDIT_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue
        if since and str(rec.get("ts", "")) < since:
            continue
        out.append(rec)
    if limit:
        out = out[-limit:]
    return out


def cmd_instances() -> int:
    try:
        items = get_instances()
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    rows = []
    for inst in items:
        svcs = services_of(inst)
        rows.append(
            {
                "id": inst.get("id"),
                "challenge_id": inst.get("challenge_id"),
                "competition_id": inst.get("competition_id"),
                "status": status_of(inst),
                "ready": status_of(inst) in READY_STATUS,
                "team_id": inst.get("team_id"),
                "services": [
                    {
                        "host": service_host(s),
                        "port": service_port(s),
                        "sni": service_sni(s),
                        "label": s.get("label"),
                        "access": s.get("access"),
                    }
                    for s in svcs
                ],
            }
        )
    print(json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def cmd_verify(entries: list[dict]) -> int:
    bad = 0
    for rec in entries:
        if rec.get("status") != "submitted":
            continue
        iid = rec.get("instance_id")
        chal = rec.get("challenge_id")
        if not iid:
            continue
        inst = get_instance(str(iid))
        if not inst:
            print(f"MISMATCH gone       instance={iid} challenge={chal} submission={rec.get('submission_id')}")
            bad += 1
            continue
        got = inst.get("challenge_id")
        ok = str(got) == str(chal)
        print(
            f"{'OK      ' if ok else 'MISMATCH'} instance={iid} challenge={got} "
            f"status={status_of(inst)} submission={rec.get('submission_id')}"
        )
        if not ok:
            bad += 1
    return 0 if bad == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only platform audit.")
    parser.add_argument("--json", action="store_true", help="print raw audit entries as JSON")
    parser.add_argument("--since", help="only entries with ts >= this ISO prefix")
    parser.add_argument("--limit", type=int, help="only the last N entries")
    parser.add_argument("--instances", action="store_true", help="list current instances and services")
    parser.add_argument("--verify", action="store_true", help="verify submitted audit entries against current instances")
    args = parser.parse_args()

    if args.instances:
        return cmd_instances()

    entries = load_entries(args.since, args.limit)
    if args.verify:
        return cmd_verify(entries)
    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    if not entries:
        print(f"no audit entries at {AUDIT_PATH}")
        return 0
    for rec in entries:
        print(
            " | ".join(
                [
                    str(rec.get("ts", "")),
                    str(rec.get("status", "")),
                    str(rec.get("mode", "")),
                    f"target={rec.get('target') or '-'}",
                    f"instance={rec.get('instance_id') or '-'}",
                    f"challenge={rec.get('challenge_id') or '-'}",
                    f"correct={rec.get('correct')}",
                    f"rank={rec.get('solve_rank')}",
                    f"sub={rec.get('submission_id') or '-'}",
                ]
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
