---
name: awd
description: "Attack-Defence match playbook: keep the flag service alive, patch fast, and submit round flags before the round ends. Load when run_mode is awd."
---

# awd

1. You are started once; keep running until the last round.
2. Get a shell on your own box; rotate the starting password immediately.
3. Read `get_awd_match` for `tick_count`, `tick_seconds`, round state.
4. Attack: `get_awd_targets`, exploit a rival, read `/answers/flag`, submit with
   `POST /competitions/{id}/awd/flags`. A flag from an earlier round is dead.
5. Defence: find the vulnerable service, patch it in place, run the challenge's
   `restart_command`, then verify the checker still gets a response.
6. Do not break the service while patching; SLA loss costs more than one flag.
7. Re-check the match clock and re-take boxes after each round rollover.
