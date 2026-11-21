/**
 * run-gate: code-level "do not stop a full-auto run early" enforcement.
 *
 * The Python gate (`bin/run_gate.py`) owns the policy and the platform state.
 * This extension only turns the gate's exit code into a tool block:
 *   - `finish` / `finish_run` / `end_run` and direct finish endpoints;
 *   - `close_challenge` / `team_pool.py close|api_close` / `framework.cli close`.
 *
 * A partial multi-flag box cannot be closed until the gate sees a recorded
 * abandonment (fresh 3-line stall note / env-fail / TTY approval). A 0-flag box
 * is still closable by the normal stall gate. `RUN_GATE=off` and
 * `run_gate.py disarm` are the human escape hatches.
 */
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const exec = promisify(execFile);
const ROOT = path.join(os.homedir(), ".pi", "agent");
const PY = process.env.PYTHON || "python";
const GATE = path.join(ROOT, "bin", "run_gate.py");
const DISABLED = path.join(ROOT, "run_gate.disabled");

const POLICY = [
	"Run gate (code-enforced for full-auto benchmark runs):",
	"1. `finish` is blocked while any opened challenge is not is_completed / flags>=flag_total.",
	"2. Closing a box with partial flags is blocked unless `run_gate.py abandon` has fresh 3-line stall notes,",
	"   a recorded environment failure, or a one-shot TTY approval (`run_gate.py approve`).",
	"3. Allowed stops: 14-minute no-new-primitive stall gate, environment failure, triage 120s probe,",
	"   and instance-only submission approval (platform-guard).",
	"4. A human can disable the gate with RUN_GATE=off or `run_gate.py disarm`.",
].join("\n");

function gateOff(): boolean {
	const mode = (process.env.RUN_GATE || "").toLowerCase();
	return mode === "off" || mode === "0" || mode === "false" || mode === "no" || fs.existsSync(DISABLED);
}

async function gate(args: string[], timeout = 25000): Promise<{ rc: number; out: string }> {
	try {
		const { stdout, stderr } = await exec(PY, [GATE, ...args], { timeout, windowsHide: true });
		return { rc: 0, out: String(stdout || stderr || "").trim() };
	} catch (e: any) {
		const rc = typeof e?.code === "number" ? e.code : 1;
		return { rc, out: String(e?.stdout || e?.stderr || e?.message || e).trim() };
	}
}

function commandSegments(cmd: string): string[] {
	return cmd
		.split(/[\n;|]+|&&|\|\|/)
		.map((s) => s.trim())
		.filter(Boolean);
}

function actionableSegments(cmd: string): string[] {
	// A prefix such as `cat /dev/null;` must not turn the real call into a
	// “read command” and bypass the finish/close gate.
	return commandSegments(cmd).filter(
		(s) => !/^(?:cat|grep|rg|head|tail|less|sed)\b/.test(s),
	);
}

function finishIntent(name: string, input: any): boolean {
	if (/^(?:mcp__[a-z0-9_]+__)?(?:finish|finish_run|end_run|finish_benchmark)$/i.test(name)) return true;
	if (name !== "bash") return false;
	const cmd = String(input?.command || "");
	return actionableSegments(cmd).some((s) =>
		/(?:openapi\/v1\/[^\s"']*(?:finish|end_run)|framework\.cli\s+finish|run_gate\.py\s+finish|tsec\.py\s+finish)/i.test(s),
	);
}

function closeTarget(name: string, input: any): string | null {
	if (/^(?:mcp__[a-z0-9_]+__)?close_challenge$/i.test(name)) {
		return String(input?.unique_code || input?.code || input?.challenge || input?.challenge_id || input?.challengeId || "");
	}
	if (name !== "bash") return null;
	const cmd = String(input?.command || "");
	const pats = [
		/openapi\/v1\/challenges\/close\?unique_code=([A-Za-z0-9_.-]+)/,
		/team_pool\.py\s+(?:api_)?close\s+["']?([A-Za-z0-9_.-]+)["']?/,
		/(?:-m\s+)?framework\.cli\s+close\s+["']?([A-Za-z0-9_.-]+)["']?/,
		/tsec\.py\s+close\s+["']?([A-Za-z0-9_.-]+)["']?/,
	];
	for (const seg of actionableSegments(cmd)) {
		for (const re of pats) {
			const m = seg.match(re);
			if (m) return decodeURIComponent(m[1]);
		}
	}
	return null;
}

function controlIntent(name: string, input: any): string | null {
	if (name === "team_panic") return "team_panic closes every box";
	if (name === "write" || name === "edit") {
		const p = String(input?.path || input?.file_path || "");
		if (/(?:^|[\\/])run_gate\.disabled$/.test(p)) return "writing run_gate.disabled";
		return null;
	}
	if (name !== "bash") return null;
	const cmd = String(input?.command || "");
	const segs = actionableSegments(cmd);
	if (segs.some((s) => /run_gate\.py\s+disarm\b/.test(s))) return "run_gate.py disarm";
	if (segs.some((s) => /run_gate\.py\s+approve\b/.test(s))) return "run_gate.py approve";
	if (segs.some((s) => /team_pool\.py\s+panic\b/.test(s))) return "team_pool.py panic";
	if (segs.some((s) => /(?:-m\s+)?framework\.cli\s+panic\b/.test(s))) return "framework.cli panic";
	if (
		segs.some((s) =>
			/run_gate\.disabled/.test(s) &&
			/(?:>>?|\btee\b|\brm\b|\bmv\b|\bcp\b|\btouch\b|\btruncate\b|\bSet-Content\b|\bOut-File\b)/.test(s),
		)
	)
		return "writing run_gate.disabled";
	return null;
}

export default function runGate(pi: ExtensionAPI) {
	let settleCount = 0;
	let settleWindow = 0;
	let lastSettle = 0;

	pi.on("before_agent_start", async () => {
		if (gateOff()) return;
		const r = await gate(["status", "--quiet"], 8000);
		if (r.rc !== 0 || /armed=False/i.test(r.out)) return;
		return {
			message: {
				customType: "run-gate-policy",
				content: POLICY + "\n\n" + r.out,
				display: false,
			},
		};
	});

	// The model can still end its turn with a question. This is the code-level
	// "do not stop to ask" part: while the gate reports unfinished work, force a
	// continuation with a concrete next action. Capped so a genuinely stuck run
	// cannot loop forever (the 14-minute stall gate still applies).
	pi.on("agent_before_settle", async (event: any) => {
		if (gateOff()) return;
		const now = Date.now();
		if (now - lastSettle < 30_000) return;
		if (now - settleWindow > 30 * 60 * 1000) {
			settleWindow = now;
			settleCount = 0;
		}
		if (settleCount >= 3) return;
		const r = await gate(["check", "--action", "finish"]);
		if (r.rc === 0) {
			settleCount = 0;
			return;
		}
		if (r.rc !== 1) return;
		settleCount += 1;
		lastSettle = now;
		const body = [
			`[run-gate] ${r.out}`,
			"Do not stop to ask the user. Continue with a concrete next move.",
			"If it has been 14+ minutes without a new primitive: write the three-line stall note, then",
			"`python ~/.pi/agent/bin/run_gate.py abandon --code <code> --stall --reason '...'` and switch boxes.",
		].join("\n");
		return {
			entries: [
				...(Array.isArray(event?.entries) ? event.entries : []),
				{ type: "custom_message", customType: "run-gate-continue", content: body, display: false },
			],
			continue: true,
		};
	});

	pi.on("tool_call", async (event: any) => {
		if (gateOff()) return;
		const name = String(event.toolName || "");
		const input = event.input ?? {};

		const ctl = controlIntent(name, input);
		if (ctl) {
			return {
				block: true,
				reason:
					`[run-gate] ${ctl} is a human-only takeover action while the run gate is armed. ` +
					"Run it in your own terminal (outside pi), or start pi with RUN_GATE=off.",
			};
		}

		if (finishIntent(name, input)) {
			const r = await gate(["check", "--action", "finish"]);
			if (r.rc === 1) {
				return { block: true, reason: `[run-gate] ${r.out}\n\n${POLICY}` };
			}
			return;
		}

		const code = closeTarget(name, input);
		if (code !== null) {
			const r = await gate(["check", "--action", "close", "--code", code]);
			if (r.rc === 1) {
				return { block: true, reason: `[run-gate] ${r.out}\n\n${POLICY}` };
			}
		}
	});
}
