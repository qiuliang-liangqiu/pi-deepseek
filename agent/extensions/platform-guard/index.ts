/**
 * platform-guard: code-level gate for competition platform writes.
 *
 * Policy (agreed with the user):
 *  - Service targets (host:port): before submitting, platform_do.py must find
 *    exactly one ready instance of this team whose services contain the same
 *    host:port (and tls_sni). On match it submits automatically.
 *  - Instance-only / attachment-only targets: never auto-submit. The agent asks
 *    the user; the user approves with platform_approve.py, then platform_do.py
 *    submits with --approval.
 *  - Never match by challenge name. No match / ambiguous / not own -> no submit.
 *
 * The extension blocks raw writes and redirects to bin/platform_do.py.
 * PLATFORM_GUARD=warn|off for debugging; default block.
 */
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { analyzeToolCall, formatHit, type GuardMode } from "./policy.ts";

const rawMode = (process.env.PLATFORM_GUARD ?? "block").toLowerCase();
const MODE: GuardMode = rawMode === "off" || rawMode === "warn" ? rawMode : "block";

const warned = new Set<string>();

const POLICY_MESSAGE = [
	"Platform flag-submission policy (enforced by platform-guard):",
	"1. Service targets with host:port: use bin/platform_do.py submit --target HOST:PORT --answer-file FILE.",
	"   The script queries the platform, requires exactly one ready own-team instance whose services match host:port (and tls_sni), then submits automatically.",
	"2. Instance-only / attachment-only targets: never auto-submit. Run platform_do.py with --instance-id to get the plan, ask the user, and use --approval FILE created by the user with platform_approve.py.",
	"3. Never submit based on challenge name. No match / ambiguous / not own team -> do not submit; report the reason.",
	"4. Raw curl/requests/MCP submit calls are blocked. Authoring / image upload / instance lifecycle / evaluation writes are allowed and are not gated here.",
].join("\n");

export default function (pi: ExtensionAPI) {
	if (MODE === "off") return;

	pi.on("session_start", async (_event, ctx: ExtensionContext) => {
		try {
			if (ctx.hasUI) ctx.ui.setStatus("platform-guard", `platform-guard: ${MODE}`);
		} catch {
			// UI status must not break the guard
		}
	});

	pi.on("before_agent_start", async () => {
		return {
			message: {
				customType: "platform-guard-policy",
				content: POLICY_MESSAGE,
				display: false,
			},
		};
	});

	pi.on("tool_call", async (event, ctx) => {
		const anyEvent = event as any;
		const toolName = String(anyEvent.toolName ?? "");
		const input = anyEvent.input ?? {};
		const cwd = (ctx as any)?.cwd ?? process.cwd();
		const hit = analyzeToolCall(toolName, input, cwd);
		if (!hit) return;

		if (MODE === "block") {
			return { block: true, reason: formatHit(hit) };
		}

		// warn: let it run, annotate the result
		if (anyEvent.toolCallId) warned.add(String(anyEvent.toolCallId));
	});

	pi.on("tool_result", async (event) => {
		if (MODE !== "warn") return;
		const anyEvent = event as any;
		const id = String(anyEvent.toolCallId ?? "");
		if (!id || !warned.has(id)) return;
		warned.delete(id);
		return {
			content: [
				...(Array.isArray(anyEvent.content) ? anyEvent.content : []),
				{ type: "text" as const, text: "\n[platform-guard] " + POLICY_MESSAGE },
			],
			details: anyEvent.details,
			structuredContent: anyEvent.structuredContent,
		};
	});

	pi.on("session_end", async () => {
		warned.clear();
	});
}
