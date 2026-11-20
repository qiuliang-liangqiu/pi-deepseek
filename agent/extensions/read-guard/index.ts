/**
 * read-guard：把“文本文件查看用 read，不要用 cat”从提示词变成即时反馈。
 *
 * 默认 READ_GUARD=warn：不改命令，只在 bash 结果尾部追加一行提示。
 * READ_GUARD=block：纯 `cat <单个文本文件>` 直接 block，reason 给出可复制的 read 调用。
 * READ_GUARD=off：关闭。
 *
 * 只处理无歧义的“cat 单文件”：
 *   拦截/提醒：cat package.json、cat ./a.txt
 *   放行：cat f | grep x、cat a b、cat f > out、cat img.png、cat $F、cat *.log
 */
import { isBashToolResult, isToolCallEventType, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { analyzeBashCommand, formatReadGuard, type GuardMode } from "./policy.ts";

const rawMode = (process.env.READ_GUARD ?? "warn").toLowerCase();
const MODE: GuardMode = rawMode === "off" || rawMode === "block" ? rawMode : "warn";
const MIN_BYTES = Math.max(0, Number(process.env.READ_GUARD_MIN_BYTES ?? "0") || 0);

export default function (pi: ExtensionAPI) {
	if (MODE === "off") return;

	pi.on("session_start", async (_event, ctx) => {
		try {
			if (ctx.hasUI) ctx.ui.setStatus("read-guard", `read-guard: ${MODE}`);
		} catch {
			// UI 状态失败不影响 guard 本身
		}
	});

	pi.on("tool_call", async (event, ctx) => {
		if (MODE !== "block") return;
		if (!isToolCallEventType("bash", event)) return;
		const command = event.input.command;
		if (typeof command !== "string") return;
		const hit = analyzeBashCommand(command, ctx.cwd, MIN_BYTES);
		if (!hit) return;
		return { block: true, reason: formatReadGuard(hit, true) };
	});

	pi.on("tool_result", async (event, ctx) => {
		if (MODE !== "warn") return;
		if (!isBashToolResult(event)) return;
		const command = event.input.command;
		if (typeof command !== "string") return;
		const hit = analyzeBashCommand(command, ctx.cwd, MIN_BYTES);
		if (!hit) return;
		return {
			content: [...event.content, { type: "text" as const, text: formatReadGuard(hit, false) }],
			details: event.details,
			structuredContent: event.structuredContent,
		};
	});
}
