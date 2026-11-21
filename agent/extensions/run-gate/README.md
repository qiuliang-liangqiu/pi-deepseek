# run-gate

Code-level “do not stop a full-auto run early” gate.

The policy and platform state live in `bin/run_gate.py`; this extension only
turns the gate result into a tool block for:

- `finish` / `finish_run` / `end_run` (and direct finish endpoints);
- `close_challenge`;
- `team_pool.py close|api_close`;
- `framework.cli close <code>`.

## Rules

- Finish is blocked while any opened challenge is not
  `is_completed` / `flags >= flag_total`.
- Closing a box with partial flags is blocked unless the gate sees:
  - `run_gate.py abandon --code <c> --stall --reason '...'` with a fresh
    three-line note (`notes.md` updated within 15 min, containing stall / no new
    primitive / give-up), or
  - `run_gate.py env-fail --code <c> --evidence '...'`, or
  - a one-shot TTY approval:
    `run_gate.py approve --action close --code <c>`.

A 0-flag box is still closable by the normal stall/tier rules. `RUN_GATE=off`
or a TTY approval (`run_gate.py approve --action disarm` then `run_gate.py
disarm`) is the human escape hatch. The extension blocks `disarm`, `approve`,
`team_panic`, and `framework.cli panic` from the model's tool path.

## Commands

```bash
python ~/.pi/agent/bin/run_gate.py status
python ~/.pi/agent/bin/run_gate.py check --action finish
python ~/.pi/agent/bin/run_gate.py check --action close --code a-01
python ~/.pi/agent/bin/run_gate.py abandon --code a-01 --stall --reason "15m no primitive"
python ~/.pi/agent/bin/run_gate.py env-fail --code a-01 --evidence "connection refused"
python ~/.pi/agent/bin/run_gate.py approve --action finish     # user's TTY only
python ~/.pi/agent/bin/run_gate.py selftest
```

Exit codes for `check`: `0` allow, `1` block, `2` inert.

`bin/team_pool.py enforce` uses the same rule: a box with flags and a known
total is no longer auto-closed at the tier hard cap; it closes only when
`teams/<code>/abandon.json` exists. The unknown-total 600 s solved-hold remains
as the documented fallback.

## 多会话隔离（.owner）

多个 pi 会话共用同一个 `teams/` 目录时，每关只应管自己开的箱：

- `team_pool.py start|adopt|track` 在拿到会话 id（`PI_SESSION_ID`）时写
  `teams/<code>/.owner`；
- 扩展在 `session_start` 从 `ctx.sessionManager.getSessionId()` 取 id，并给每次
  `check` 传 `--session <id>`；
- `run_gate.py` 跳过 `.owner` 属于**别的**会话的箱，`status` 会单独列出「跳过（别的会话开的）」。

安全默认：拿不到 session id、或箱没有 `.owner`（旧箱）时，行为与以前完全一致
（所有箱都算本会话的），宁可多拦也不误放。CLI 也可手动指定：
`run_gate.py status --session <id>`。
