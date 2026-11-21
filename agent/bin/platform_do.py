#!/usr/bin/env python3
"""platform_do.py: the only sanctioned path for CTFY/CTF2 platform writes.

Policy (agreed with the user):
  * service target (--target host:port): find exactly one ready instance of this
    team whose services contain the same host:port (and tls_sni if given), then
    submit automatically.
  * instance-only / attachment-only target (--instance-id): never auto-submit.
    Print an "ask" plan; the user must approve with platform_approve.py, then
    rerun with --approval FILE created by platform_approve.py.
  * never match by challenge name.

Read-only by default: every write is preceded by an explicit instance match and
written to ~/.pi/agent/platform-audit.jsonl (answer hash only, never the token).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

HOME = Path.home()
AUDIT_PATH = HOME / ".pi" / "agent" / "platform-audit.jsonl"

READY_STATUS = {"ready", "running", "up", "healthy", "active", "available"}
PLACEHOLDER_RE = re.compile(r"TEST_PLATFORM|PLACEHOLDER|EXAMPLE_FLAG|TODO_FLAG", re.I)

_BASE: str | None = None
_TOKEN: str | None = None


def _load_cfg() -> tuple[str, str]:
    global _BASE, _TOKEN
    if _BASE and _TOKEN:
        return _BASE, _TOKEN
    base = os.environ.get("CTFY_PLATFORM_URL", "").strip()
    token = os.environ.get("CTFY_TOKEN", "").strip()
    if not base or not token:
        cfg_path = HOME / ".pi" / "agent" / "mcp.json"
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(f"cannot read {cfg_path}: {exc}") from exc
        env = ((cfg.get("mcpServers") or {}).get("ctfy") or {}).get("env") or {}
        base = base or str(env.get("CTFY_PLATFORM_URL", "")).strip()
        token = token or str(env.get("CTFY_TOKEN", "")).strip()
    if not base or not token:
        raise SystemExit(
            "missing CTFY_PLATFORM_URL / CTFY_TOKEN "
            "(set env vars or configure mcpServers.ctfy.env in ~/.pi/agent/mcp.json)"
        )
    _BASE, _TOKEN = base.rstrip("/"), token
    return _BASE, _TOKEN


def _redact(text: str) -> str:
    if _TOKEN:
        return text.replace(_TOKEN, "<redacted>")
    return text


def api(method: str, path: str, body: Any | None = None) -> tuple[int, Any]:
    base, token = _load_cfg()
    url = base + "/api/v1" + path
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read()
            if not raw:
                return resp.status, {}
            try:
                return resp.status, json.loads(raw)
            except Exception:  # noqa: BLE001
                return resp.status, {"raw": raw.decode("utf-8", "replace")[:1000]}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            data = json.loads(raw) if raw else {}
        except Exception:  # noqa: BLE001
            data = {"raw": raw.decode("utf-8", "replace")[:1000]}
        return exc.code, data
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": _redact(str(exc))}


def _items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("items", "data", "instances", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def get_instances() -> list[dict[str, Any]]:
    status, data = api("GET", "/instances")
    if status != 200:
        raise RuntimeError(f"GET /instances -> HTTP {status}: {_redact(json.dumps(data, ensure_ascii=False)[:500])}")
    return _items(data)


def get_instance(instance_id: str) -> dict[str, Any] | None:
    status, data = api("GET", f"/instances/{urllib.parse.quote(instance_id)}")
    if status != 200:
        return None
    if isinstance(data, dict):
        inner = data.get("instance") or data.get("data") or data
        return inner if isinstance(inner, dict) else None
    return None


def status_of(inst: dict[str, Any]) -> str:
    return str(inst.get("status", "")).strip().lower()


def services_of(inst: dict[str, Any]) -> list[dict[str, Any]]:
    raw = inst.get("services")
    if isinstance(raw, list) and raw:
        return [x for x in raw if isinstance(x, dict)]
    iid = str(inst.get("id") or "")
    if iid:
        detail = get_instance(iid)
        if detail:
            inst.update({k: v for k, v in detail.items() if v is not None})
            raw = detail.get("services")
            if isinstance(raw, list):
                return [x for x in raw if isinstance(x, dict)]
    return []


def wait_ready(inst: dict[str, Any], wait_s: int) -> dict[str, Any]:
    if status_of(inst) in READY_STATUS:
        return inst
    deadline = time.time() + max(0, wait_s)
    while time.time() < deadline:
        time.sleep(2)
        iid = str(inst.get("id") or "")
        if not iid:
            break
        detail = get_instance(iid)
        if detail:
            inst.update({k: v for k, v in detail.items() if v is not None})
        if status_of(inst) in READY_STATUS:
            break
    return inst


def normalize_host(host: str) -> str:
    return host.strip().strip("[]").rstrip(".").lower()


def parse_target(target: str) -> tuple[str, int]:
    raw = target.strip()
    if not raw:
        raise ValueError("empty target")
    if "://" in raw:
        parsed = urllib.parse.urlparse(raw)
        host, port = parsed.hostname, parsed.port
        if not host or not port:
            raise ValueError("target needs host and port")
        return normalize_host(host), int(port)
    if raw.startswith("["):
        m = re.fullmatch(r"\[([^\]]+)\]:(\d+)", raw)
        if not m:
            raise ValueError("IPv6 target must be [addr]:port")
        return normalize_host(m.group(1)), int(m.group(2))
    if raw.count(":") == 1:
        host, port_s = raw.rsplit(":", 1)
        if not port_s.isdigit():
            raise ValueError("port is not numeric")
        return normalize_host(host), int(port_s)
    # bare IPv6 without port is ambiguous; require a port
    raise ValueError("target must be host:port (or [v6]:port)")


def service_host(svc: dict[str, Any]) -> str:
    for key in ("host", "hostname", "address", "ip", "server"):
        value = svc.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_host(value)
    return ""


def service_port(svc: dict[str, Any]) -> int | None:
    for key in ("port", "tcp_port"):
        value = svc.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    return None


def service_sni(svc: dict[str, Any]) -> str:
    for key in ("tls_sni", "sni", "server_name"):
        value = svc.get(key)
        if isinstance(value, str) and value.strip():
            return normalize_host(value)
    return ""


def service_matches(svc: dict[str, Any], host: str, port: int, sni: str | None) -> bool:
    if service_port(svc) != port:
        return False
    svc_host, svc_sni = service_host(svc), service_sni(svc)
    if sni:
        wanted = normalize_host(sni)
        if svc_sni:
            return svc_sni == wanted
        return svc_host == wanted
    return svc_host == host or (bool(svc_sni) and svc_sni == host)


def answer_sha256(answer: str) -> str:
    return hashlib.sha256(answer.encode("utf-8")).hexdigest()


def audit(entry: dict[str, Any]) -> None:
    entry = dict(entry)
    entry.setdefault("ts", _dt.datetime.now(_dt.timezone.utc).isoformat())
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with AUDIT_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        # auditing must not break the submission itself
        pass


def emit(obj: dict[str, Any], code: int = 0) -> None:
    audit(obj)
    print(json.dumps(obj, ensure_ascii=False, sort_keys=True))
    raise SystemExit(code)


def read_answer(path: str) -> str:
    try:
        answer = Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"cannot read answer file {path}: {exc}") from exc
    if not answer:
        raise SystemExit("answer file is empty")
    return answer


def validate_approval(path: str, instance_id: str, challenge_id: str, comp_id: str, ans_hash: str) -> dict[str, Any]:
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"cannot read approval {path}: {exc}") from exc
    if not isinstance(rec, dict):
        raise SystemExit("approval is not a JSON object")
    approvals_dir = (HOME / ".pi" / "agent" / "approvals").resolve()
    if Path(path).resolve().parent != approvals_dir:
        raise SystemExit("approval must live in ~/.pi/agent/approvals (created by platform_approve.py)")
    if rec.get("created_by") != "user-tty":
        raise SystemExit("approval was not created by platform_approve.py in a user TTY")
    if rec.get("action") != "submit":
        raise SystemExit("approval action mismatch")
    if str(rec.get("instance_id")) != str(instance_id):
        raise SystemExit("approval instance_id mismatch")
    if str(rec.get("challenge_id")) != str(challenge_id):
        raise SystemExit("approval challenge_id mismatch")
    if rec.get("competition_id") and comp_id and str(rec["competition_id"]) != str(comp_id):
        raise SystemExit("approval competition_id mismatch")
    if rec.get("answer_sha256") != ans_hash:
        raise SystemExit("approval answer hash mismatch")
    try:
        expires = float(rec.get("expires", 0))
    except (TypeError, ValueError):
        expires = 0
    if expires <= time.time():
        raise SystemExit("approval expired")
    # one-time: consume before the POST
    try:
        os.unlink(path)
    except OSError:
        pass
    return rec


def submit_instance(inst: dict[str, Any], answer: str, question: str, mode: str, target: str | None) -> None:
    comp = str(inst.get("competition_id") or "")
    chal = str(inst.get("challenge_id") or "")
    iid = str(inst.get("id") or "")
    if not comp or not chal or not iid:
        emit({"status": "error", "error": "instance missing competition_id/challenge_id/id", "mode": mode}, 1)
    body = {
        "competition_id": comp,
        "challenge_id": chal,
        "question_id": question,
        "answer": answer,
        "instance_id": iid,
    }
    status, resp = api("POST", "/submissions", body)
    if status != 200:
        emit(
            {
                "status": "http_error",
                "mode": mode,
                "target": target,
                "instance_id": iid,
                "challenge_id": chal,
                "competition_id": comp,
                "http_status": status,
                "response": resp,
                "answer_sha256": answer_sha256(answer),
            },
            1,
        )
    emit(
        {
            "status": "submitted",
            "mode": mode,
            "target": target,
            "instance_id": iid,
            "challenge_id": chal,
            "competition_id": comp,
            "team_id": inst.get("team_id"),
            "question": question,
            "answer_sha256": answer_sha256(answer),
            "correct": resp.get("correct"),
            "challenge_fully_solved": resp.get("challenge_fully_solved"),
            "solve_rank": resp.get("solve_rank"),
            "submission_id": resp.get("id"),
            "response": resp,
        },
        0,
    )


def cmd_submit(args: argparse.Namespace) -> None:
    answer = read_answer(args.answer_file)
    ans_hash = answer_sha256(answer)
    if PLACEHOLDER_RE.search(answer) and not args.allow_placeholder:
        emit(
            {
                "status": "placeholder_rejected",
                "reason": "answer looks like a test/placeholder flag (TEST_PLATFORM/PLACEHOLDER/EXAMPLE)",
                "answer_sha256": ans_hash,
            },
            12,
        )

    # QA path (no instance)
    if args.qa:
        if not args.competition or not args.challenge:
            emit({"status": "error", "error": "--qa needs --competition and --challenge"}, 1)
        if not args.approval:
            emit(
                {
                    "status": "ask",
                    "mode": "qa",
                    "reason": "QA challenge requires explicit user approval",
                    "challenge_id": args.challenge,
                    "competition_id": args.competition,
                    "answer_sha256": ans_hash,
                    "how": "run: python ~/.pi/agent/bin/platform_approve.py --instance-id qa --challenge <id> --competition <id> --answer-file <file>, then rerun with --approval <path>",
                },
                10,
            )
        if args.approval:
            validate_approval(args.approval, "qa", args.challenge, args.competition, ans_hash)
        body = {
            "competition_id": args.competition,
            "challenge_id": args.challenge,
            "question_id": args.question,
            "answer": answer,
        }
        status, resp = api("POST", "/qa/submissions", body)
        if status != 200:
            emit({"status": "http_error", "mode": "qa", "http_status": status, "response": resp, "answer_sha256": ans_hash}, 1)
        emit(
            {
                "status": "submitted",
                "mode": "qa",
                "challenge_id": args.challenge,
                "competition_id": args.competition,
                "question": args.question,
                "answer_sha256": ans_hash,
                "correct": resp.get("correct"),
                "submission_id": resp.get("id"),
                "response": resp,
            },
            0,
        )

    # instance-only path
    if args.instance_id:
        inst = get_instance(args.instance_id)
        if not inst:
            emit({"status": "no_match", "mode": "instance_only", "reason": "instance not found", "instance_id": args.instance_id}, 2)
        inst = wait_ready(inst, args.wait)
        if status_of(inst) not in READY_STATUS:
            emit(
                {
                    "status": "not_ready",
                    "mode": "instance_only",
                    "instance_id": args.instance_id,
                    "instance_status": status_of(inst),
                },
                4,
            )
        chal = str(inst.get("challenge_id") or "")
        comp = str(inst.get("competition_id") or "")
        if args.challenge and str(args.challenge) != chal:
            emit({"status": "no_match", "mode": "instance_only", "reason": "challenge mismatch", "instance_challenge": chal, "requested": args.challenge}, 2)
        if args.competition and str(args.competition) != comp:
            emit({"status": "no_match", "mode": "instance_only", "reason": "competition mismatch", "instance_competition": comp, "requested": args.competition}, 2)
        if args.team and inst.get("team_id") and str(args.team) != str(inst.get("team_id")):
            emit({"status": "no_match", "mode": "instance_only", "reason": "team mismatch", "instance_team": inst.get("team_id"), "requested": args.team}, 2)

        if not args.approval:
            emit(
                {
                    "status": "ask",
                    "mode": "instance_only",
                    "reason": "instance-only / attachment-only challenge requires explicit user approval",
                    "instance_id": args.instance_id,
                    "challenge_id": chal,
                    "competition_id": comp,
                    "team_id": inst.get("team_id"),
                    "answer_sha256": ans_hash,
                    "how": "ask the user to run: python ~/.pi/agent/bin/platform_approve.py --instance-id <id> --challenge <id> --competition <id> --answer-file <file> ; then rerun with --approval <path>",
                },
                10,
            )

        validate_approval(args.approval, args.instance_id, chal, comp, ans_hash)
        submit_instance(inst, answer, args.question, "instance_only", None)
        return

    # service path
    if not args.target:
        emit({"status": "error", "error": "need --target host:port, --instance-id, or --qa"}, 1)
    try:
        host, port = parse_target(args.target)
    except Exception as exc:  # noqa: BLE001
        emit({"status": "error", "error": f"bad target: {exc}"}, 1)

    try:
        instances = get_instances()
    except Exception as exc:  # noqa: BLE001
        emit({"status": "error", "error": _redact(str(exc))}, 1)

    candidates: list[dict[str, Any]] = []
    checked: list[dict[str, Any]] = []
    for inst in instances:
        inst = wait_ready(inst, args.wait)
        if status_of(inst) not in READY_STATUS:
            checked.append({"id": inst.get("id"), "status": status_of(inst), "challenge_id": inst.get("challenge_id")})
            continue
        if args.challenge and str(inst.get("challenge_id")) != str(args.challenge):
            checked.append({"id": inst.get("id"), "status": status_of(inst), "challenge_id": inst.get("challenge_id"), "skip": "challenge"})
            continue
        if args.competition and str(inst.get("competition_id")) != str(args.competition):
            checked.append({"id": inst.get("id"), "status": status_of(inst), "competition_id": inst.get("competition_id"), "skip": "competition"})
            continue
        if args.team and inst.get("team_id") and str(inst.get("team_id")) != str(args.team):
            checked.append({"id": inst.get("id"), "status": status_of(inst), "team_id": inst.get("team_id"), "skip": "team"})
            continue
        svcs = services_of(inst)
        matched = [s for s in svcs if service_matches(s, host, port, args.sni)]
        checked.append(
            {
                "id": inst.get("id"),
                "status": status_of(inst),
                "challenge_id": inst.get("challenge_id"),
                "services": [
                    {
                        "host": service_host(s),
                        "port": service_port(s),
                        "sni": service_sni(s),
                        "label": s.get("label"),
                    }
                    for s in svcs
                ],
                "matched": bool(matched),
            }
        )
        if matched:
            candidates.append(inst)

    if not candidates:
        emit(
            {
                "status": "no_match",
                "mode": "service",
                "target": args.target,
                "host": host,
                "port": port,
                "sni": args.sni,
                "checked": checked,
                "hint": "if this is an instance-only / attachment-only challenge, use --instance-id and ask the user",
            },
            2,
        )
    if len(candidates) > 1:
        emit(
            {
                "status": "ambiguous",
                "mode": "service",
                "target": args.target,
                "host": host,
                "port": port,
                "candidates": [
                    {"id": c.get("id"), "challenge_id": c.get("challenge_id"), "competition_id": c.get("competition_id"), "team_id": c.get("team_id")}
                    for c in candidates
                ],
                "checked": checked,
            },
            3,
        )

    inst = candidates[0]
    if args.dry_run:
        emit(
            {
                "status": "would_submit",
                "mode": "service",
                "target": args.target,
                "host": host,
                "port": port,
                "instance_id": inst.get("id"),
                "challenge_id": inst.get("challenge_id"),
                "competition_id": inst.get("competition_id"),
                "team_id": inst.get("team_id"),
                "answer_sha256": ans_hash,
            },
            0,
        )
    submit_instance(inst, answer, args.question, "service", args.target)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sanctioned platform write path (address-bound).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("submit", help="submit a flag")
    p.add_argument("--target", help="host:port of the service actually attacked")
    p.add_argument("--sni", help="TLS SNI (optional)")
    p.add_argument("--instance-id", help="instance id for instance-only challenges")
    p.add_argument("--qa", action="store_true", help="QA (non-instance) submission path")
    p.add_argument("--competition", help="competition id (optional; used for matching/QA)")
    p.add_argument("--challenge", help="challenge id (optional; never matched by name)")
    p.add_argument("--team", help="team id (optional)")
    p.add_argument("--question", default="flag", help="question id (default: flag)")
    p.add_argument("--answer-file", required=True, help="file containing the flag")
    p.add_argument("--approval", help="approval JSON created by platform_approve.py (instance-only/QA)")
    p.add_argument("--wait", type=int, default=20, help="seconds to wait for a starting instance (default 20)")
    p.add_argument("--dry-run", action="store_true", help="print the plan without submitting")
    p.add_argument("--allow-placeholder", action="store_true", help="allow TEST_PLATFORM/PLACEHOLDER-looking answers")
    args = p.parse_args()

    if args.cmd == "submit":
        cmd_submit(args)


if __name__ == "__main__":
    main()
