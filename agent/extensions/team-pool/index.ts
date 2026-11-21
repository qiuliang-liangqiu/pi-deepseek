/**
 * Local twin of the tbench image's team_dispatch/team_poll/team_cancel.
 * Tools call ~/.pi/agent/bin/team_pool.py (detached pi children, jobs.json).
 * tool_result watches start_challenge / curl start and calls `adopt`, which
 * schedules the 120s triage close or fans out recon+exploit+verifier.
 */
import { execFile } from "node:child_process";
import * as fs from "node:fs";
import { promisify } from "node:util";
import * as os from "node:os";
import * as path from "node:path";
import { Type } from "typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const exec = promisify(execFile);
const ROOT = path.join(os.homedir(), ".pi", "agent");
const PY = process.env.PYTHON || "python";
const POOL = path.join(ROOT, "bin", "team_pool.py");
const EXT_VERSION = "2026-11-20.3";

async function pool(args: string[], timeout = 30000): Promise<string> {
  try {
    const { stdout, stderr } = await exec(PY, [POOL, ...args], { timeout, windowsHide: true });
    return (stdout || stderr || "").trim();
  } catch (e: any) {
    return `team_pool error: ${e?.message || e}`;
  }
}

function textOf(content: any): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.map((c) => (typeof c === "string" ? c : c?.text || "")).join("\n");
  try { return JSON.stringify(content); } catch { return ""; }
}

function parseStart(name: string, input: any, content: any): { code: string; addr: string } | null {
  const cmd = String(input?.command || "");
  // Only a real start call may adopt a box. Reading a log/notes/jobs.json that
  // merely contains `unique_code=...` and `container_addr` must not schedule a
  // close for some other box (old code matched any tool output).
  const isStartTool = name === "start_challenge" || name.endsWith("start_challenge");
  const isStartBash =
    name === "bash" &&
    /(?:^|[\s;&|])(curl|wget|python|python3|http)\b/.test(cmd) &&
    /openapi\/v1\/challenges\/start/.test(cmd) &&
    !/(?:^|[\s;&|])(cat|grep|rg|head|tail|less|sed)\b/.test(cmd);
  if (!isStartTool && !isStartBash) return null;

  const blob = textOf(content) + "\n" + textOf(input) + "\n" + cmd;
  let code = input?.unique_code || input?.code || "";
  if (!code) {
    const m = cmd.match(/unique_code=([A-Za-z0-9_.-]+)/) || blob.match(/unique_code=([A-Za-z0-9_.-]+)/);
    if (m) code = decodeURIComponent(m[1]);
  }
  const a =
    blob.match(/"container_addr"\s*:\s*"([^"]+)"/) ||
    blob.match(/"container_addr"\s*:\s*\[\s*"([^"]+)"/) ||
    blob.match(/container_addr["']?\s*[:=]\s*["']?([A-Za-z0-9_.:\-]+)/);
  if (!code || !a) return null;
  return { code, addr: a[1] };
}

export default function teamPool(pi: ExtensionAPI) {
  let lastPoll = 0;
  let clockCache = { t: 0, s: "" };

  async function getClock(): Promise<string> {
    if (Date.now() - clockCache.t < 2000) return clockCache.s;
    const s = await pool(["clock"], 5000);
    clockCache = { t: Date.now(), s };
    return s;
  }

  pi.on("session_start", async () => {
    try {
      fs.mkdirSync(path.join(ROOT, "teams"), { recursive: true });
      const sid = process.env.PI_SESSION_ID || "unknown";
      fs.writeFileSync(
        path.join(ROOT, "teams", `.team-pool-ext-${sid}`),
        `${EXT_VERSION} ${sid} ${new Date().toISOString()}
`,
        "utf8",
      );
    } catch {
      /* best effort */
    }
    lastPoll = Date.now();
    await pool(["poll"], 120000);
    clockCache = { t: 0, s: "" };
  });

  pi.registerTool({
    name: "team_dispatch",
    label: "Team dispatch",
    description: "Start one detached recon/exploit/verifier pi child for a box. Returns a job id. Prefer this over the batch subagent tool.",
    parameters: Type.Object({
      code: Type.String({ description: "challenge unique_code" }),
      agent: Type.String({ description: "recon | exploit | verifier" }),
      task: Type.String({ description: "task, incl. box address and files to read" }),
      timeout: Type.Optional(Type.Number({ description: "seconds; 0 = default 1800s (TEAM_JOB_TIMEOUT)" })),
    }),
    async execute(_id, p) {
      const out = await pool(["dispatch", p.code, p.agent, p.task, "--timeout", String(p.timeout ?? 0)]);
      // The main starts boxes via tsec.py/framework/curl, not team_pool. Track
      // the code+address from the task so the enforcer/reaper can see it without
      // dispatching a second set of workers.
      const addr = String(p.task).match(/((?:\d{1,3}\.){3}\d{1,3}(?::\d+)?)/)?.[1];
      if (addr) await pool(["track", p.code, addr]);
      clockCache = { t: 0, s: "" };
      return { content: [{ type: "text", text: out }], details: {} };
    },
  });

  pi.registerTool({
    name: "team_track",
    label: "Team track",
    description: "Record a box started outside team_pool (tsec.py/framework/curl): code + address, no dispatch. Use when auto-adopt missed the start result.",
    parameters: Type.Object({
      code: Type.String({ description: "challenge unique_code" }),
      addr: Type.String({ description: "container_addr / host:port" }),
    }),
    async execute(_id, p) {
      const out = await pool(["track", p.code, p.addr]);
      clockCache = { t: 0, s: "" };
      return { content: [{ type: "text", text: out }], details: {} };
    },
  });

  pi.registerTool({
    name: "team_poll",
    label: "Team poll",
    description: "Poll local team jobs and print statuses. Use wait=0 for an immediate snapshot.",
    parameters: Type.Object({ wait: Type.Optional(Type.Number({ description: "seconds to wait" })) }),
    async execute(_id, p) {
      const out = await pool(["poll", "--wait", String(p.wait ?? 0)], 120000);
      lastPoll = Date.now();
      clockCache = { t: 0, s: "" };
      return { content: [{ type: "text", text: out }], details: {} };
    },
  });

  pi.registerTool({
    name: "team_enforce",
    label: "Team enforce",
    description:
      "Run the local watchdog once. Dry-run by default; close=true acts. Rules: completed 10s, hard deadline, stall 900s, solved-hold 600s, close retry. Use --since to limit to boxes opened after an epoch.",
    parameters: Type.Object({
      close: Type.Optional(Type.Boolean({ description: "Actually close; default false (dry-run)" })),
      since: Type.Optional(Type.Number({ description: "Only boxes opened at/after this epoch (seconds)" })),
      includeNotes: Type.Optional(
        Type.Boolean({ description: "Also manage boxes with no opened_at, using notes.md mtime (opt-in)" }),
      ),
    }),
    async execute(_id, p) {
      const args = ["enforce"];
      if (p.close) args.push("--close");
      if (typeof p.since === "number") args.push("--since", String(p.since));
      if (p.includeNotes) args.push("--include-notes");
      const out = await pool(args, 120000);
      clockCache = { t: 0, s: "" };
      return { content: [{ type: "text", text: out }], details: {} };
    },
  });

  pi.registerTool({
    name: "team_hold",
    label: "Team hold",
    description: "Protect one box from the local enforcer (writes teams/<code>/.hold). Use only with a concrete next move; unhold when done.",
    parameters: Type.Object({
      code: Type.String({ description: "challenge unique_code" }),
      reason: Type.Optional(Type.String({ description: "why it is held / next move" })),
    }),
    async execute(_id, p) {
      const out = await pool(["hold", p.code, p.reason || ""]);
      clockCache = { t: 0, s: "" };
      return { content: [{ type: "text", text: out }], details: {} };
    },
  });

  pi.registerTool({
    name: "team_unhold",
    label: "Team unhold",
    description: "Remove the hold marker for one box.",
    parameters: Type.Object({ code: Type.String({ description: "challenge unique_code" }) }),
    async execute(_id, p) {
      const out = await pool(["unhold", p.code]);
      clockCache = { t: 0, s: "" };
      return { content: [{ type: "text", text: out }], details: {} };
    },
  });

  pi.registerTool({
    name: "team_panic",
    label: "Team panic",
    description: "Close every box this local pool knows about (opened_at, not already closed/reaped). Uses the platform token; no dry-run.",
    parameters: Type.Object({}),
    async execute() {
      const out = await pool(["panic"], 120000);
      clockCache = { t: 0, s: "" };
      return { content: [{ type: "text", text: out }], details: {} };
    },
  });

  pi.registerTool({
    name: "team_reconcile",
    label: "Team reconcile",
    description: "If the platform task is already finished (invalid_state), clear local stale opened markers without calling close. Live tasks are left untouched.",
    parameters: Type.Object({}),
    async execute() {
      const out = await pool(["reconcile"], 60000);
      clockCache = { t: 0, s: "" };
      return { content: [{ type: "text", text: out }], details: {} };
    },
  });

  pi.registerTool({
    name: "team_cancel",
    label: "Team cancel",
    description: "Cancel one job id or all jobs for a code; kills the child tree.",
    parameters: Type.Object({ target: Type.Optional(Type.String()) }),
    async execute(_id, p) {
      const out = await pool(p.target ? ["cancel", p.target] : ["cancel"]);
      clockCache = { t: 0, s: "" };
      return { content: [{ type: "text", text: out }], details: {} };
    },
  });

  pi.registerTool({
    name: "team_adopt",
    label: "Team adopt",
    description: "Register an already-started box. During triage it schedules the 120s close; otherwise it dispatches 3 workers.",
    parameters: Type.Object({ code: Type.String(), addr: Type.Optional(Type.String()) }),
    async execute(_id, p) {
      const out = await pool(["adopt", p.code, p.addr || ""]);
      clockCache = { t: 0, s: "" };
      return { content: [{ type: "text", text: out }], details: {} };
    },
  });

  pi.on("tool_result", async (event: any) => {
    const ev = parseStart(String(event.toolName || ""), event.input, [event.content, event.details, event.structuredContent]);
    if (ev) await pool(["adopt", ev.code, ev.addr]);
    // Reconcile dead supervisors periodically; doing it on every tool result
    // would spawn a Python + tasklist per call. 45s is enough to keep the
    // footer honest without touching latency.
    if (Date.now() - lastPoll > 45000) {
      lastPoll = Date.now();
      await pool(["poll"], 120000);
      clockCache = { t: 0, s: "" };
    }
    const clock = await getClock();
    if (!clock) return;
    // Suppress only the truly empty status line; keep CANDIDATE/STALL/RECON/ENF.
    if (clock.split("|", 1)[0].trim() === "team 0r/0d") return;
    const blocks = Array.isArray(event.content) ? event.content : [{ type: "text", text: textOf(event.content) }];
    return {
      content: [...blocks, { type: "text", text: `\n[${clock} | ext ${EXT_VERSION}]` }],
      // Replacing content without structuredContent drops it (pi docs).
      details: event.details,
      structuredContent: event.structuredContent,
    };
  });
}
