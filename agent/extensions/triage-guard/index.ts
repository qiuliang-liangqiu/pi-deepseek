/**
 * Local T-Sec triage guard (for this pi install, not the tbench image).
 *
 * While `~/.pi/agent/triage_state.json` says triage is active, block a flag
 * submission. Start/close are allowed so the agent can run the 30-minute
 * shallow-fingerprint sweep. The gate is deliberately narrow: it does not
 * block bash generally, only a raw /challenges/submit call or submit_flag.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const STATE = path.join(os.homedir(), ".pi", "agent", "triage_state.json");

function active(): boolean {
	try {
		const s = JSON.parse(fs.readFileSync(STATE, "utf8"));
		return Boolean(s) && !s.done && Date.now() / 1000 < Number(s.phase_end || 0);
	} catch {
		return false;
	}
}

export default function triageGuard(pi: ExtensionAPI) {
	pi.on("tool_call", async (event: any) => {
		if (!active()) return;
		const name = String(event.toolName || "");
		if (name === "submit_flag") {
			return { block: true, reason: "TRIAGE active: sweep the 50 challenges first; run triage_gate.py done or wait for the phase to end before submitting." };
		}
		if (name === "bash") {
			const cmd = String(event.input?.command || "");
			const submits =
				/openapi\/v1\/challenges\/submit/.test(cmd) ||
				/(?:^|[\/\s])sub\.py\b/.test(cmd) ||
				/framework\.cli\s+submit\b/.test(cmd);
			if (submits && !/triage_gate\.py/.test(cmd)) {
				return { block: true, reason: "TRIAGE active: submissions (submit_flag, /challenges/submit, work/sub.py, framework.cli submit) are blocked until triage_gate.py done or the phase ends." };
			}
		}
	});
}
