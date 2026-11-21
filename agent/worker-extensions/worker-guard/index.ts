/**
 * worker-guard: only loaded into team_pool workers (not the main agent).
 *
 * Enforces two rules that prompts alone do not enforce:
 * 1. obvious high-rate scans (ffuf/hydra/nmap/...) are blocked unless the
 *    command carries an explicit safe rate/thread cap;
 * 2. any flag{...} that appears in a bash/read tool output is appended to this
 *    job's candidates-<job>.md (the main can still verify/submit it).
 *
 * The main extension's prompt-injection-guard is loaded separately.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const JOB = process.env.TEAM_WORKER_JOB || "";
const BOX = process.env.TEAM_BOX_DIR || "";
const FLAG_RE = /flag\{[^}\s]{4,}\}/gi;
const MAX_FLAGS = 10;

function num(cmd: string, re: RegExp): number | null {
	const m = cmd.match(re);
	if (!m) return null;
	const n = Number(m[1]);
	return Number.isFinite(n) ? n : null;
}

function rateReason(cmd: string): string | null {
	const c = cmd.toLowerCase();
	const cap = (label: string, threads: number | null, rate: number | null) =>
		`${label} exceeds the worker cap (threads/concurrency <=10, rate <=20): got threads=${threads ?? "default"} rate=${rate ?? "unset"}. Add an explicit safe cap.`;

	if (/\bffuf\b/.test(c)) {
		const t = num(cmd, /(?:^|\s)-t\s*(\d+)/) ?? num(cmd, /--threads[=\s]+(\d+)/);
		const r = num(cmd, /(?:^|\s)-rate\s*(\d+)/) ?? num(cmd, /--rate[=\s]+(\d+)/);
		if (r === null || r > 20 || (t !== null && t > 10)) return cap("ffuf", t, r);
	}
	if (/\bferoxbuster\b/.test(c)) {
		const t = num(cmd, /--threads[=\s]+(\d+)/);
		const r = num(cmd, /--rate-limit[=\s]+(\d+)/);
		if (r === null || r > 20 || t === null || t > 10) return cap("feroxbuster", t, r);
	}
	if (/\bgobuster\b/.test(c)) {
		const t = num(cmd, /(?:^|\s)-t\s*(\d+)/);
		if (t !== null && t > 10) return cap("gobuster", t, null);
	}
	if (/\b(?:hydra|medusa)\b/.test(c)) {
		const t = num(cmd, /(?:^|\s)-t\s*(\d+)/);
		if (t === null || t > 10) return cap("hydra/medusa", t, null);
	}
	if (/\bnmap\b/.test(c)) {
		const r = num(cmd, /--min-rate[=\s]+(\d+)/);
		if (r !== null && r > 20) return cap("nmap", null, r);
	}
	if (/\bxargs\b/.test(c)) {
		const p = num(cmd, /(?:^|\s)-P\s*(\d+)/);
		if (p !== null && p > 10) return cap("xargs -P", p, null);
	}
	if (/\bcurl\b/.test(c)) {
		const p = num(cmd, /--parallel-max[=\s]+(\d+)/);
		if (p !== null && p > 10) return cap("curl --parallel-max", p, null);
	}
	if (/\b(?:ab|wrk|siege)\b/.test(c)) {
		const p = num(cmd, /(?:^|\s)-c\s*(\d+)/);
		if (p !== null && p > 10) return cap("load tool", p, null);
	}
	if (/\bmasscan\b/.test(c)) {
		const r = num(cmd, /--rate[=\s]+(\d+)/);
		if (r === null || r > 20) return cap("masscan", null, r);
	}
	if (/\bwfuzz\b/.test(c)) {
		const t = num(cmd, /(?:^|\s)-t\s*(\d+)/);
		if (t !== null && t > 10) return cap("wfuzz", t, null);
	}
	if (/\bdirsearch\b/.test(c)) {
		const t = num(cmd, /(?:^|\s)-t\s*(\d+)/);
		if (t !== null && t > 10) return cap("dirsearch", t, null);
	}
	if (/\bhttpx\b/.test(c)) {
		const t = num(cmd, /(?:^|\s)-threads[=\s]+(\d+)/);
		if (t !== null && t > 10) return cap("httpx", t, null);
	}
	if (/\bnuclei\b/.test(c)) {
		const t = num(cmd, /(?:^|\s)-c\s*(\d+)/);
		const r = num(cmd, /-rate-limit[=\s]+(\d+)/);
		if ((t !== null && t > 10) || (r !== null && r > 20)) return cap("nuclei", t, r);
	}
	if (/\bparallel\b/.test(c)) {
		const j = num(cmd, /(?:^|\s)-j\s*(\d+)/);
		if (j !== null && j > 10) return cap("parallel -j", j, null);
	}
	if (/\bcurl\b/.test(c) && /(?:^|\s)-Z\b/.test(c)) {
		const p = num(cmd, /--parallel-max[=\s]+(\d+)/);
		if (p === null || p > 10) return cap("curl -Z", p, null);
	}
	return null;
}

function collectText(event: any): string {
	const content = event?.content;
	if (!Array.isArray(content)) return "";
	const parts: string[] = [];
	for (const b of content) {
		if (b && typeof b === "object" && b.type === "text" && typeof b.text === "string") {
			parts.push(b.text);
		}
	}
	return parts.join("\n");
}

function recordFlags(text: string): void {
	if (!JOB || !BOX || !text) return;
	const found: string[] = [];
	for (const m of text.matchAll(FLAG_RE)) {
		const f = m[0];
		if (!found.includes(f)) found.push(f);
		if (found.length >= MAX_FLAGS) break;
	}
	if (found.length === 0) return;
	try {
		fs.mkdirSync(BOX, { recursive: true });
		const p = path.join(BOX, `candidates-${JOB}.md`);
		let old = "";
		try {
			old = fs.readFileSync(p, "utf8");
		} catch {
			/* new file */
		}
		const fresh = found.filter((f) => !old.includes(f));
		if (fresh.length === 0) return;
		fs.appendFileSync(
			p,
			"\n" + fresh.map((f) => `FLAG_CANDIDATE ${f} | auto-scanned from tool output`).join("\n") + "\n",
			"utf8",
		);
	} catch {
		/* best effort */
	}
}

export default function workerGuard(pi: ExtensionAPI) {
	pi.on("tool_call", async (event: any) => {
		if (event?.toolName !== "bash") return;
		const cmd = String(event.input?.command || "");
		if (!cmd) return;
		const why = rateReason(cmd);
		if (why) return { block: true, reason: `[worker-guard] ${why}` };
	});

	pi.on("tool_result", async (event: any) => {
		if (event?.toolName !== "bash" && event?.toolName !== "read") return;
		recordFlags(collectText(event));
	});
}
