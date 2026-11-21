/**
 * loop-guard：把“工具调用防沉迷”从纪律变成即时反馈。
 *
 * 背景（腾讯云智能渗透黑客松冠军复盘）：LLM 会反复执行同一条命令（尤其 curl）
 * 来低效试错，长轮次里还会“降智”。人肉盯不住，就让 hook 在结果里插一句。
 *
 * 模式（环境变量 LOOP_GUARD）：
 *   warn  —— 只提示（非默认）。
 *   block —— 默认。>= LOOP_GUARD_WARN 提醒，>= LOOP_GUARD_BLOCK 直接 block。
 *   off   —— 关闭。
 *
 * 只统计 bash，按“去掉首尾空白 + 压缩连续空白”后的命令做 key；
 * 不同命令会稀释计数（滑动窗口），有意的轮询/复现可忽略提醒。
 */
import {
	isBashToolResult,
	isToolCallEventType,
	type ExtensionAPI,
	type ExtensionContext,
} from "@earendil-works/pi-coding-agent";

type Mode = "warn" | "block" | "off";

const rawMode = (process.env.LOOP_GUARD ?? "block").toLowerCase();
const MODE: Mode = rawMode === "off" || rawMode === "block" ? rawMode : "warn";
const WINDOW = Math.max(2, Number(process.env.LOOP_GUARD_WINDOW ?? "12") || 12);
const WARN_AT = Math.max(2, Number(process.env.LOOP_GUARD_WARN ?? "3") || 3);
const BLOCK_AT = Math.max(WARN_AT + 1, Number(process.env.LOOP_GUARD_BLOCK ?? "5") || 5);

/** 最近 WINDOW 次 bash 命令的 key（含当前这次） */
const ring: string[] = [];
/** toolCallId -> 该次调用的重复次数（>= WARN_AT 才记录） */
const warned = new Map<string, number>();

function norm(command: string): string {
	return command.trim().replace(/\s+/g, " ");
}

function countInRing(key: string): number {
	let n = 0;
	for (const k of ring) if (k === key) n++;
	return n;
}

function hint(times: number): string {
	return [
		`[loop-guard] 同一条命令在最近 ${WINDOW} 次 bash 调用里出现 ${times} 次。`,
		`如果是在重试同一个假设：先写下“这次想验证什么/上次为什么失败”，再换一个真正不同的动作`,
		`（加 -v、换 payload/端口/路径、换工具，或先读源码/日志）。`,
		`连续 3 次没有新信息就该换方向或 hold，别把时间耗在同一条命令上。`,
	].join("");
}

export default function (pi: ExtensionAPI) {
	if (MODE === "off") return;

	pi.on("session_start", async (_event, ctx: ExtensionContext) => {
		try {
			if (ctx.hasUI) ctx.ui.setStatus("loop-guard", `loop-guard: ${MODE}`);
		} catch {
			// UI 失败不影响 guard
		}
	});

	pi.on("session_end", async () => {
		ring.length = 0;
		warned.clear();
	});

	pi.on("tool_call", async (event) => {
		if (!isToolCallEventType("bash", event)) return;
		const command = event.input.command;
		if (typeof command !== "string" || !command.trim()) return;

		const key = norm(command);
		const times = countInRing(key) + 1;

		// 自我跳过：loop-guard 自己的探测命令不参与统计
		if (!key.startsWith("echo loop-guard-probe")) {
			ring.push(key);
			if (ring.length > WINDOW) ring.shift();
		}

		if (times >= WARN_AT) warned.set(event.toolCallId, times);

		if (MODE === "block" && times >= BLOCK_AT) {
			return {
				block: true,
				reason: `${hint(times)} 已达到 block 阈值（${BLOCK_AT}），本次调用被拦下。先换动作，或把结论写进笔记再继续。`,
			};
		}
	});

	pi.on("tool_result", async (event) => {
		if (!isBashToolResult(event)) return;
		const times = warned.get(event.toolCallId);
		if (!times) return;
		warned.delete(event.toolCallId);
		return {
			content: [...event.content, { type: "text" as const, text: "\n" + hint(times) }],
			details: event.details,
			structuredContent: event.structuredContent,
		};
	});
}
