#!/usr/bin/env python3
"""Local T-Sec triage gate (for the pi agent itself, not the tbench image).

  python triage_gate.py start [--minutes 30] [--probe 120]
  python triage_gate.py beat <code> [what]
  python triage_gate.py check          # exit 1 while triage is active and incomplete
  python triage_gate.py done
  python triage_gate.py status

The companion extension `extensions/triage-guard/` blocks submit_flag and
`/openapi/v1/challenges/submit` while `check` would exit 1.
"""
from __future__ import annotations
import argparse, json, os, sys, time
from pathlib import Path

STATE = Path.home() / ".pi" / "agent" / "triage_state.json"
TEAMS = STATE.parent / "teams"

def load():
    try: return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception: return {}

def save(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")

def active(s):
    return bool(s) and not s.get("done") and time.time() < float(s.get("phase_end") or 0)

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("start"); st.add_argument("--minutes", type=float, default=30); st.add_argument("--probe", type=float, default=120)
    bt = sub.add_parser("beat"); bt.add_argument("code"); bt.add_argument("what", nargs="?", default="")
    sub.add_parser("check"); sub.add_parser("done"); sub.add_parser("status")
    a = ap.parse_args()
    s = load()
    if os.environ.get("TEAM_WORKER") == "1" and a.cmd in ("start", "beat", "done"):
        print("[triage] workers may not change triage state", file=sys.stderr)
        return 1
    if a.cmd == "start":
        now = time.time()
        s = {"started": now, "phase_end": now + a.minutes * 60, "probe": a.probe, "done": False, "cards": {}}
        save(s); print(f"[triage] active for {a.minutes:g} min; probe {a.probe:g}s/box")
        return 0
    if a.cmd == "beat":
        s.setdefault("cards", {})[a.code] = {"ts": time.time(), "what": a.what}
        save(s); print(f"[triage] card {a.code}: {a.what or '(no detail)'}")
        return 0
    if a.cmd == "done":
        if not (s.get("cards") or {}):
            print("[triage] no cards: beat at least one box before done")
            return 1
        missing = []
        if TEAMS.is_dir():
            for d in TEAMS.iterdir():
                if not d.is_dir() or not (d / "opened_at").exists():
                    continue
                if (d / "closed").exists() or (d / "reaped.json").exists():
                    continue
                cards = [c for c in (d / "triage.txt", d / "triage.md") if c.exists()]
                if not cards:
                    missing.append(d.name)
                    continue
                try:
                    opened = float((d / "opened_at").read_text(encoding="utf-8").strip())
                    if max(c.stat().st_mtime for c in cards) < opened - 5:
                        missing.append(d.name)  # stale card from an earlier run
                except (OSError, ValueError):
                    pass
        if missing:
            print("[triage] missing cards for open boxes: " + ", ".join(sorted(missing)))
            print("        run triage.sh / write triage.txt, or close the box first")
            return 1
        s["done"] = True; save(s); print("[triage] done"); return 0
    if a.cmd == "status":
        print(json.dumps(s, ensure_ascii=False, indent=1)); return 0
    # check
    if not s:
        print("[triage] no state: start with `triage_gate.py start` before a T-Sec run")
        return 0
    if s.get("done"):
        print("[triage] done"); return 0
    left = float(s.get("phase_end") or 0) - time.time()
    if left <= 0:
        print(f"[triage] phase over ({len(s.get('cards') or {})} cards)"); return 0
    print(f"[triage] ACTIVE: {left/60:.1f} min left, {len(s.get('cards') or {})} cards; no submit until done/phase end")
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
