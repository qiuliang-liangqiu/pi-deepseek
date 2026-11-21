/**
 * platform-guard policy: gate raw platform *flag submissions*.
 *
 * Only flag-submission endpoints are blocked. Authoring / image upload /
 * instance lifecycle / evaluation writes are allowed: they are platform
 * operations, not score submissions, and blocking them breaks upload/eval
 * workflows.
 *
 * Submissions must go through bin/platform_do.py:
 *  - service target (host:port): exact instance-service match -> auto submit;
 *  - instance-only / attachment-only: ask the user, then --approval.
 *
 * This is a best-effort gate, not a sandbox. It blocks the normal tool paths
 * (bash, MCP submit tools, codemode, write/edit). A determined process can still
 * bypass it; the audit log and the TTY-only approval are the real backstops.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

export type GuardMode = "off" | "warn" | "block";
export type Hit = { kind: string; reason: string };

const PLATFORM_HOSTS = [
	"ctfy.tsinghua.edu.cn",
	"ctf2.dasctf.com",
	"datacon.org.cn",
];

/** Only flag-submission endpoints are gated. */
const SUBMISSION_PATHS = [
	"/api/v1/submissions",
	"/api/v1/qa/submissions",
	"/submissions",
	"/qa/submissions",
];

const MCP_SUBMIT_RE = /^mcp__(?:ctf2|ctfy)__.*submit/i;

const CODEMODE_SUBMIT_RE = /(?:mcp__(?:ctf2|ctfy)__[A-Za-z0-9_]*submit)/i;

const SANCTIONED_RE =
	/^\s*(?:"[^"]*python(?:\d+(?:\.\d+)*)?(?:\.exe)?"|\S*python\S*)\s+(?:"[^"]*platform_do\.py"|\S*platform_do\.py)\s+submit\b/i;

const TOKEN_LEAK_RE =
	/(?:\becho\b|\bprintf\b|\bprint\b|\bcat\b|\btype\b|\bmore\b|\bGet-Content\b|\benv\b|\bset\b)[\s\S]{0,240}(?:CTFY_TOKEN|BENCHMARK_TOKEN)/i;

const DESTRUCTIVE_RE =
	/(?:>>?|\brm\b|\bmv\b|\bcp\b|\bsed\s+-i\b|\bSet-Content\b|\bOut-File\b|\btee\b|open\s*\([^)]*['"][wax])/i;

function tamperHit(command: string, cwd: string): boolean {
	if (!isGuardText(command)) return false;
	// A redirection is only tampering when its target is a guard/approval path.
	for (const target of redirectionTargets(command)) {
		if (!/^&[12]$/.test(target) && isGuardPath(target, cwd)) return true;
	}
	// Destructive verbs on a command line that names a guard file.
	if (DESTRUCTIVE_RE.test(command)) {
		if (/\b(?:rm|mv|cp|sed\s+-i|tee)\b/i.test(command)) return true;
		if (/open\s*\([^)]*['"][wax]/.test(command)) return true;
	}
	return false;
}

function hasWriteIntent(text: string): boolean {
	return (
		/(?:^|[\s;&|])-X\s*(?:POST|PUT|PATCH)\b/i.test(text) ||
		/--request\s+(?:POST|PUT|PATCH)\b/i.test(text) ||
		/(?:^|\s)(?:-d|--data(?:-raw|-binary|-urlencode)?|--json|-F|--form|-T|--upload-file)\b/.test(text) ||
		/(?:requests|httpx|session)\.(?:post|put|patch)\s*\(/i.test(text) ||
		/method\s*[:=]\s*["'](?:POST|PUT|PATCH)["']/i.test(text) ||
		/\b(?:POST|PUT|PATCH)\b/.test(text)
	);
}

const GUARD_ROOT = path.join(os.homedir(), ".pi", "agent", "extensions", "platform-guard");
const GUARD_FILES = [
	path.join(os.homedir(), ".pi", "agent", "bin", "platform_do.py"),
	path.join(os.homedir(), ".pi", "agent", "bin", "platform_audit.py"),
	path.join(os.homedir(), ".pi", "agent", "bin", "platform_approve.py"),
];
const APPROVALS_DIR = path.join(os.homedir(), ".pi", "agent", "approvals");

export function hasPlatformHost(text: string): boolean {
	const s = text.toLowerCase();
	return PLATFORM_HOSTS.some((h) => s.includes(h));
}

export function hasSubmissionIntent(text: string): boolean {
	const s = text.toLowerCase();
	return SUBMISSION_PATHS.some((p) => s.includes(p));
}

function redirectionTargets(command: string): string[] {
	const out: string[] = [];
	const re = /(?:^|[^0-9])[12]?>>?\s*("[^"]*"|'[^']*'|[^\s;&|]+)/g;
	let m: RegExpExecArray | null;
	while ((m = re.exec(command))) out.push(m[1].replace(/^["']|["']$/g, ""));
	return out;
}

export function isSanctionedPlatformDo(command: string, cwd = process.cwd()): boolean {
	if (!SANCTIONED_RE.test(command)) return false;
	// Do not let a newline or command substitution turn the sanctioned prefix
	// into a second command.
	if (/[\r\n`]/.test(command) || /\$\(/.test(command)) return false;
	if (/[;&|]/.test(command)) return false;
	// A redirection may write a log, but never a guard/approval file.
	for (const target of redirectionTargets(command)) {
		if (/^&[12]$/.test(target)) continue;
		if (isGuardPath(target, cwd)) return false;
	}
	return true;
}

function expandHome(p: string): string {
	if (p === "~") return os.homedir();
	if (p.startsWith("~/") || p.startsWith("~\\")) return path.join(os.homedir(), p.slice(2));
	return p;
}

function norm(p: string): string {
	return path.resolve(expandHome(p)).toLowerCase().replace(/[\\/]+$/, "");
}

export function isGuardPath(p: string, cwd: string): boolean {
	const abs = norm(path.resolve(cwd, expandHome(p)));
	if (abs === norm(GUARD_ROOT) || abs.startsWith(norm(GUARD_ROOT) + path.sep)) return true;
	if (abs === norm(APPROVALS_DIR) || abs.startsWith(norm(APPROVALS_DIR) + path.sep)) return true;
	return GUARD_FILES.some((f) => norm(f) === abs);
}

function isGuardText(text: string): boolean {
	const s = text.toLowerCase();
	return (
		s.includes("platform-guard") ||
		s.includes("platform_do.py") ||
		s.includes("platform_audit.py") ||
		s.includes("platform_approve.py") ||
		s.includes(".pi/agent/approvals") ||
		s.includes(".pi\\agent\\approvals") ||
		s.includes("approvals/")
	);
}

function scanScriptFile(command: string, cwd: string): Hit | null {
	const re =
		/(?:^|[;&|]\s*)(?:"[^"]*python(?:\d+(?:\.\d+)*)?(?:\.exe)?"|\S*python\S*|bash|sh|node|pwsh|powershell)\s+(?:-\S+\s+)*["']?([^\s"']+\.(?:py|sh|js|mjs|cjs|ts|ps1|bat|cmd))["']?/gi;
	let m: RegExpExecArray | null;
	while ((m = re.exec(command))) {
		const p = path.resolve(cwd, expandHome(m[1]));
		try {
			const st = fs.statSync(p);
			if (!st.isFile() || st.size > 512 * 1024) continue;
			const text = fs.readFileSync(p, "utf8");
			if (hasPlatformHost(text) && hasSubmissionIntent(text)) {
				return {
					kind: "raw-platform-submit-script",
					reason: `脚本 ${p} 里含有对平台 flag 提交接口的直接调用`,
				};
			}
		} catch {
			// ignore missing/unreadable files
		}
	}
	return null;
}

export function analyzeBashCommand(command: string, cwd: string): Hit | null {
	if (!command.trim()) return null;
	if (isSanctionedPlatformDo(command, cwd)) return null;

	if (TOKEN_LEAK_RE.test(command)) {
		return {
			kind: "token-leak",
			reason: "命令看起来会打印 CTFY_TOKEN / BENCHMARK_TOKEN；凭据不能进上下文或日志",
		};
	}

	if (tamperHit(command, cwd)) {
		return {
			kind: "guard-tamper",
			reason: "命令试图修改 platform-guard / platform_do / approval 文件；这些文件只能由用户手动改",
		};
	}

	if (hasPlatformHost(command) && hasSubmissionIntent(command) && hasWriteIntent(command)) {
		return {
			kind: "raw-platform-submit",
			reason: "命令直接对平台 flag 提交接口发请求（/submissions 或 /qa/submissions）",
		};
	}

	const scriptHit = scanScriptFile(command, cwd);
	if (scriptHit) return scriptHit;

	return null;
}

export function analyzeToolCall(toolName: string, input: any, cwd: string): Hit | null {
	if (MCP_SUBMIT_RE.test(toolName)) {
		return {
			kind: "mcp-submit",
			reason: `${toolName} 是平台 flag 提交工具；请改走 bin/platform_do.py`,
		};
	}

	if (toolName === "codemode") {
		const code = String(input?.code ?? "");
		if (CODEMODE_SUBMIT_RE.test(code)) {
			return {
				kind: "codemode-mcp-submit",
				reason: "codemode 脚本里调用了平台 flag 提交工具（submit_*）",
			};
		}
		if (hasPlatformHost(code) && hasSubmissionIntent(code)) {
			return {
				kind: "codemode-raw-submit",
				reason: "codemode 脚本里直接对平台 flag 提交接口发请求",
			};
		}
		return null;
	}

	if (toolName === "bash") {
		return analyzeBashCommand(String(input?.command ?? ""), cwd);
	}

	if (toolName === "write" || toolName === "edit") {
		const p = String(input?.path ?? input?.file_path ?? "");
		if (p && isGuardPath(p, cwd)) {
			return {
				kind: "guard-tamper",
				reason: "不能通过 write/edit 修改 platform-guard / platform_do / approval 文件",
			};
		}

		const parts: string[] = [];
		for (const value of [input?.content, input?.file_text, input?.text]) {
			if (typeof value === "string") parts.push(value);
		}
		if (Array.isArray(input?.edits)) {
			for (const e of input.edits) {
				if (typeof e?.newText === "string") parts.push(e.newText);
				if (typeof e?.oldText === "string") parts.push(e.oldText);
			}
		}
		const text = parts.join("\n");
		if (/\.(?:py|sh|js|mjs|cjs|ts|ps1|bat|cmd)$/i.test(p) && hasPlatformHost(text) && hasSubmissionIntent(text)) {
			return {
				kind: "raw-platform-submit-script",
				reason: "写入的脚本里包含对平台 flag 提交接口的直接调用；请用 bin/platform_do.py",
			};
		}
	}

	return null;
}

export function formatHit(hit: Hit): string {
	return [
		`[platform-guard] ${hit.reason}`,
		"平台 flag 提交必须走：python ~/.pi/agent/bin/platform_do.py submit ...",
		"服务题：--target HOST:PORT（按实例地址精确匹配，匹配通过自动提交）",
		"只有实例/附件的题：--instance-id 只输出计划；用户运行 platform_approve.py 生成 approval，再带 --approval 提交。",
		"上传镜像 / 出题 / 实例启停 / 评测不走这里，guard 不拦。",
	].join("\n");
}
