# platform-guard

Code-level gate for **platform flag submissions** (CTFY / CTF2 / DataCon).

Authoring / image upload / instance start-stop / evaluation writes are **not**
gated: they are platform operations, not score submissions, and blocking them
breaks upload/eval workflows.

## Policy (agreed with the user)

1. **Service target (`host:port`)**  
   Before submitting, `bin/platform_do.py` must query the platform and find
   **exactly one** instance that is:
   - `status == ready`,
   - owned by this team,
   - has a service whose `host:port` (and `tls_sni` if given) exactly matches the
     target that was actually attacked,
   - has the same `challenge_id` (never matched by challenge name).

   On a unique match: submit automatically, with that `instance_id`.

2. **Instance-only / attachment-only target** (no port)  
   Never auto-submit. `platform_do.py --instance-id` prints an `ask` plan and exits
   with code 10. The user runs `bin/platform_approve.py` in their own terminal
   (TTY required) to create a one-time approval; then the agent reruns with
   `--approval <file>`.

3. **Never match by challenge name.**  
   0 matches / multiple matches / not this team / not ready → do not submit;
   report the reason.

## What is blocked

Only flag-submission intent:

- `bash` commands that call `/submissions` or `/qa/submissions` on a platform host.
- `bash` commands that run a local script containing such a raw submission call.
- MCP submit tools: `ctf2_submit_flag`, `ctf2_submit_competition_flag`, and the
  `mcp__ctfy__*submit*` equivalents.
- `codemode` scripts that call those MCP submit tools or raw `/submissions`.
- `write` / `edit` of `platform-guard`, `platform_do.py`, `platform_audit.py`,
  `platform_approve.py`, or `~/.pi/agent/approvals/`.
- Commands that print `CTFY_TOKEN` / `BENCHMARK_TOKEN`.

## What is allowed

- Authoring / challenge image upload (`/authored-challenges`, `/authoring/*`).
- Instance start / stop / renew / delete.
- Evaluation / check endpoints (`/instances/*/check`, `/eval/*`).
- Any GET / read.
- Flag submissions through `platform_do.py` (which applies the address-binding rules).

## Allowed submission path

```bash
# service challenge: exact host:port match, auto-submit on success
python ~/.pi/agent/bin/platform_do.py submit \
  --target 1.2.3.4:443 --sni example.internal \
  --answer-file /path/to/flag.txt

# instance-only challenge: ask first
python ~/.pi/agent/bin/platform_do.py submit \
  --instance-id <uuid> --answer-file /path/to/flag.txt
# -> status: ask, exit 10

# user creates approval in their own terminal
python ~/.pi/agent/bin/platform_approve.py \
  --instance-id <uuid> --challenge AUTHORED-XXXX --answer-file /path/to/flag.txt
# -> prints approval path

# agent submits with the approval
python ~/.pi/agent/bin/platform_do.py submit \
  --instance-id <uuid> --answer-file /path/to/flag.txt \
  --approval ~/.pi/agent/approvals/<nonce>.json
```

Audit / verification:

```bash
python ~/.pi/agent/bin/platform_audit.py            # recent attempts
python ~/.pi/agent/bin/platform_audit.py --instances
python ~/.pi/agent/bin/platform_audit.py --verify
```

## Modes

- `PLATFORM_GUARD=block` (default): block raw submissions.
- `PLATFORM_GUARD=warn`: allow but annotate the result.
- `PLATFORM_GUARD=off`: disable (not recommended).

## Limits

This is a best-effort gate, not a sandbox. It runs with the same OS permissions as
the agent; a determined process can still bypass it by editing files or speaking
raw sockets. The audit log and the TTY-only approval are the backstops. Do not
edit the guard files from an agent session; the user should edit them manually.
