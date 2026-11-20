---
name: portfolio
description: "How to allocate time across many short challenges: breadth-first fingerprinting, the high-value backlog, the 15-minute stall indicator, touch/auto-close, the second-flag window and the endgame freeze. Load this when deciding whether to keep, open or close a box."
---

# portfolio — allocation before exploitation

Measured on 50-challenge / 8-hour runs: the median successful stint is about
5 minutes, but failed stints and parked boxes consume most of the wall clock.
The solver is rarely slow; it spends time in the wrong places.

## Read the clock

Every tool result ends with

```
[clock t+2h14m | budget 5h46m left | open: demo-02 hard 18m/1h00m STALL auto 12m, demo-11 medium 3m/20m auto 17m | solved 4 | score 1200 | BACKLOG demo-16 (800 pts) | think high]
```

- `STALL` = 15+ minutes without an accepted flag.
- `auto` = the harness deadline. A completed box (`is_completed`/`flag_total`)
  closes **10 s after the last flag**. A box with flags but not completed is
  kept for remaining flags; a 600 s solved-hold backstop closes it. An
  unsolved box closes **15 minutes after the last `touch_challenge`**.
- `BACKLOG` = the highest-score challenge that has never been opened; start it
  before another code at 10/30/50% of the budget.

## The rules

1. **30-minute triage, then A→B with one hard slot.** First 2 minutes: list
   and sort. First 25–30 minutes: 3 lanes, one box each, 30–60 seconds per
   challenge with `~/.pi/agent/bin/triage.sh`; write A/B/C/D; close and rotate.
   **Enforced:** a box opened in the first 30 minutes is `TRIAGE` and the
   harness closes it at the 120-second probe deadline; auto-recon is off.
   All 50 get a shallow fingerprint — do not spend 2 hours. If every open
   challenge has a card before 30 minutes, call `triage_complete` and start
   solving A→B early. Otherwise after 30 minutes take A, then B; keep one
   slot for C. At 50% of the budget the clock names
   the highest-score unopened box (`BACKLOG`, soft suggestion).
2. **Stall gate = no new primitive, not total time.** 12–15 minutes without a
   new fact → three lines in `notes.md` and close. If new facts keep arriving,
   `touch_challenge(code, what)` resets the 15-minute auto-close and the chain
   may run to the tier limit (easy 5/10, medium 10/20, hard 30/60). Do not cut
   a live chain; do not call `touch` for mere activity.
3. **No parked boxes.** Close is never blocked; notes are optional. A slot is
   for work, not storage. Before a long background job, `touch_challenge` so the
   stall timer does not close the box mid-job.
4. **Completed means `is_completed`/`flag_total`, not 2 flags.** This paper has
   per-challenge totals like 1/6, 2/6, 3/6. If `is_completed: false`, keep the
   box and continue for the remaining flags. `start_challenge` refuses a
   completed code; `force: true` only with evidence.
5. **Remaining flags.** After a correct flag, continue until `is_completed` or
   `flag_total`; single-flag boxes close 10 s after the last flag. 180 s is only
   a fallback when the total is unknown; do not discard a multi-flag box.
6. **Endgame freeze.** Last 45 minutes: no fresh recon. Only a box whose notes
   contain a concrete next move, or a known-cheap fingerprint. Finishing an
   open box beats opening three new ones.

## Team mode (v12) — one team per box

- One box = one team; workers do not move to another box. **3 workers/box
  default** (recon+exploit+verifier); triage 1/box; drop to 1–2 on 429/CPU;
  max 3 boxes × 3 = 9.
- Use `team_dispatch` + `team_poll`, not `subagent tasks` (that waits for the
  slowest). On a done job: submit/dispatch next; on box done: close, cancel
  its jobs, start the next challenge.
- First flag is not completion on 100/50: keep the box until
  `is_completed`/`flag_total`; close on the total, not 2 flags/180s.
- Only the main has platform tools; workers only bash/read/write, no token,
  no start/close/submit. Blackboard `/work/teams/<code>/`; main reads
  summaries, not transcripts.
- Auto-team: `TBENCH_AUTO_TEAM=all` (default) dispatches recon+exploit+
  verifier on every start; `hard` only one recon on hard/600+; no duplicate.
- Close scoping: `close_challenge(A)` cancels only A's jobs; B/C slow jobs
  untouched.
- Discussion: 2 rounds, 200 words, no new fact -> stop.

## Closing notes (three lines, optional but cheap)

```
## <unique_code> — closed
entry: <URL/port/route>
fingerprint: <product/version/auth>
tried: <payloads> -> <contradiction>; next: <one concrete move>
```

`close_challenge(code, notes="…")` appends this. If you omit it, the box still
closes and the next reopen starts cold.

## What not to do

- Do not spend more than two rounds of a credential hunt without asking where
  the credential should come from.
- Do not open a box just to fill a free slot.
- Do not batch reconnaissance; rotate one box at a time.
- Do not close a box 30 seconds after a correct flag when the paper has two
  flags per challenge — but do not fight the auto-close either; submit the
  second flag before the window ends.
