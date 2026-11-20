/**
 * read-guard 的命令识别：只处理“纯 cat 单个文本文件”这一种无歧义场景。
 *
 * 明确不做的事：
 *  - 不拦 `cat f | grep` / `cat f > out` / `cat a b` / heredoc / 二进制
 *  - 不拦 head/tail/sed（它们常被用于抽样/脚本，误伤成本高）
 *  - 不拦变量路径、通配符、命令替换（无法静态确认）
 */
import * as fs from "node:fs";
import * as path from "node:path";

export type GuardMode = "off" | "warn" | "block";

export interface Inspection {
	absolutePath: string;
	displayPath: string;
	size: number;
}

/** 这些字符一出现，就不是“单个文件的纯 cat”，直接放行。 */
const FORBIDDEN = /[|&;<>`$\n\r]/;

function tokenize(input: string): string[] | null {
	const out: string[] = [];
	let cur = "";
	let quote: "'" | '"' | null = null;
	for (let i = 0; i < input.length; i++) {
		const ch = input[i];
		if (quote) {
			if (ch === quote) quote = null;
			else cur += ch;
			continue;
		}
		if (ch === "'" || ch === '"') {
			quote = ch;
			continue;
		}
		if (/\s/.test(ch)) {
			if (cur) {
				out.push(cur);
				cur = "";
			}
			continue;
		}
		cur += ch;
	}
	if (quote) return null;
	if (cur) out.push(cur);
	return out;
}

function looksBinary(file: string): boolean {
	let fd: number | null = null;
	try {
		fd = fs.openSync(file, "r");
		const buf = Buffer.alloc(4096);
		const n = fs.readSync(fd, buf, 0, buf.length, 0);
		return buf.subarray(0, n).includes(0);
	} catch {
		return true;
	} finally {
		if (fd !== null) {
			try {
				fs.closeSync(fd);
			} catch {
				// ignore
			}
		}
	}
}

/** 返回 null = 不是“纯 cat 单文本文件”，不处理。 */
export function analyzeBashCommand(command: string, cwd: string, minBytes = 0): Inspection | null {
	if (!command || !cwd || FORBIDDEN.test(command)) return null;
	// 注释/多命令/转义先放过，宁可漏报不误伤
	if (command.includes("#") || command.includes("\\")) return null;

	const tokens = tokenize(command.trim());
	if (!tokens || tokens.length !== 2) return null;
	const [cmd, rawPath] = tokens;
	if (cmd !== "cat" && cmd !== "cat.exe") return null;
	if (!rawPath || rawPath.startsWith("-")) return null;
	if (/[*?~]/.test(rawPath)) return null;

	const abs = path.resolve(cwd, rawPath);
	const lower = abs.toLowerCase();
	if (
		lower.startsWith("/dev/") ||
		lower.startsWith("/proc/") ||
		lower.startsWith("/sys/") ||
		lower.endsWith("/nul") ||
		lower.endsWith("\\nul")
	) {
		return null;
	}

	let st: fs.Stats;
	try {
		st = fs.statSync(abs);
	} catch {
		return null;
	}
	if (!st.isFile() || st.size < Math.max(0, minBytes)) return null;
	if (looksBinary(abs)) return null;

	return {
		absolutePath: abs,
		displayPath: path.relative(cwd, abs) || rawPath,
		size: st.size,
	};
}

export function formatReadGuard(hit: Inspection, blocked: boolean): string {
	const rel = hit.displayPath.replaceAll("\\", "/");
	const call = `read(path=${JSON.stringify(rel)}, offset=1, limit=2000)`;
	if (blocked) {
		return `[read-guard] 已拦截“纯 cat 文本文件查看”。请改用 ${call}。read 有行号+截断；cat 只用于管道/拼接/二进制。`;
	}
	return `[read-guard] 这是纯 cat 文本文件查看，建议改用 ${call}。read 有行号+截断；cat 只用于管道/拼接/二进制。`;
}
