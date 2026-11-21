/**
 * tool-check：会话启动时检查本机缺哪些 CTF / 跑分工具，只提示、不安装。
 *
 * 每次 session 启动时异步跑一次 `bin/tools_check.py check --json`（纯本地、无网络）：
 *   - 有缺失：footer 显示 `tools !N`，并提示前几个缺失项 + 安装命令
 *   - 全都在：清掉 footer 标记
 * 结果缓存 24 小时（PI_TOOL_CHECK_TTL 毫秒可覆盖），同一份缺失清单 24 小时只提示一次，
 * 不会每个会话都弹。
 *
 * 命令：
 *   /tools-check   立刻重查一次并列出缺失项
 *
 * 关闭：PI_TOOL_CHECK=off（命令仍然可用）；PI_TOOL_CHECK=debug 打印失败原因。
 * 这个扩展只读 `shutil.which` 的结果，绝不安装任何东西 —— 装软件要用户自己决定
 * （仓库里的 scripts/setup-tools.py）。
 */
import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const AGENT_DIR = path.resolve(HERE, "..", "..");
const SCRIPT = path.join(AGENT_DIR, "bin", "tools_check.py");
const STATE_FILE = path.join(AGENT_DIR, ".tools-check.json");
const STATUS_KEY = "tools-check";

const MODE = (process.env.PI_TOOL_CHECK ?? "on").toLowerCase();
const DEBUG = MODE === "debug";
const TTL_MS = Number(process.env.PI_TOOL_CHECK_TTL || 24 * 3600 * 1000) || 24 * 3600 * 1000;
const NOTIFY_TTL_MS = 24 * 3600 * 1000;

type NeedsAlt = { id: string; why?: string; use?: string };
type ToolInfo = {
	platform?: string;
	checked?: number;
	ok?: string[];
	missing?: string[];
	needs_alt?: NeedsAlt[];
	hint?: string;
	catalog_version?: number;
	generated_at?: number;
};
type Cache = { at?: number; fingerprint?: string; info?: ToolInfo; notified_fingerprint?: string; notified_at?: number };
type RunResult = { code: number; stdout: string; stderr: string };

function pythonCandidates(): string[] {
	const env = (process.env.PI_PYTHON || "").trim();
	const base = [env, process.platform === "win32" ? "python" : "python3", "python"].filter(Boolean);
	return [...new Set(base)];
}

function run(args: string[], timeout = 20_000): Promise<RunResult> {
	const candidates = pythonCandidates();
	const attempt = (i: number): Promise<RunResult> =>
		new Promise((resolve) => {
			if (i >= candidates.length) {
				resolve({ code: -1, stdout: "", stderr: "no python interpreter found" });
				return;
			}
			execFile(candidates[i], args, { timeout, windowsHide: true, maxBuffer: 1 << 20 }, (error, stdout, stderr) => {
				const e = error as NodeJS.ErrnoException & { code?: number | string } | null;
				const missing = e && (e.code === "ENOENT" || e.code === "EACCES");
				if (missing) {
					attempt(i + 1);
					return;
				}
				resolve({
					code: typeof e?.code === "number" ? e.code : error ? 1 : 0,
					stdout: String(stdout ?? ""),
					stderr: String(stderr ?? ""),
				});
			});
		});
	return attempt(0);
}

function readCache(): Cache {
	try {
		const data = JSON.parse(fs.readFileSync(STATE_FILE, "utf8")) as Cache;
		return data && typeof data === "object" ? data : {};
	} catch {
		return {};
	}
}

function writeCache(next: Cache): void {
	try {
		fs.writeFileSync(STATE_FILE, JSON.stringify(next) + "\n", "utf8");
	} catch {
		// 写不了状态不影响会话
	}
}

function parseInfo(text: string): ToolInfo | null {
	const body = (text || "").trim();
	if (!body) return null;
	try {
		const data = JSON.parse(body) as ToolInfo;
		if (!data || !Array.isArray(data.missing)) return null;
		return data;
	} catch {
		return null;
	}
}

function fingerprint(info: ToolInfo): string {
	const missing = [...(info.missing || [])].sort().join(",");
	return `${info.platform || "?"}:${info.checked ?? "?"}:${missing}`;
}

async function checkNow(): Promise<ToolInfo | null> {
	if (!fs.existsSync(SCRIPT)) {
		if (DEBUG) console.error(`[tool-check] missing ${SCRIPT}`);
		return null;
	}
	const result = await run([SCRIPT, "check", "--json"]);
	const info = parseInfo(result.stdout);
	if (!info) {
		if (DEBUG) console.error(`[tool-check] tools_check.py failed rc=${result.code} ${result.stderr.slice(-300)}`);
		return null;
	}
	return info;
}

function describe(info: ToolInfo, limit = 6): string {
	const missing = info.missing || [];
	const shown = missing.slice(0, limit).join(", ");
	const more = missing.length > limit ? ` 等 ${missing.length} 个` : "";
	const alt = (info.needs_alt || []).length
		? `；${(info.needs_alt || []).map((n) => n.id).slice(0, 4).join(", ")} 需要 WSL/VM/Docker`
		: "";
	const hint = info.hint ? `。安装：${info.hint}` : "";
	return `缺 ${missing.length} 个工具：${shown}${more}${alt}${hint}`;
}

export default function (pi: ExtensionAPI) {
	if (MODE === "off") return;

	pi.on("session_start", async (_event, ctx) => {
		void (async () => {
			const cached = readCache();
			const fresh = typeof cached.at === "number" && Date.now() - cached.at < TTL_MS;
			let info = fresh && cached.info ? cached.info : null;
			if (!info) {
				info = await checkNow();
				if (!info) return;
				writeCache({ ...readCache(), at: Date.now(), fingerprint: fingerprint(info), info });
			}
			if (!ctx.hasUI) return;

			const missing = info.missing || [];
			ctx.ui.setStatus(STATUS_KEY, missing.length ? `tools !${missing.length}` : undefined);
			if (!missing.length) return;

			const key = fingerprint(info);
			const state = readCache();
			const notifiedRecently =
				state.notified_fingerprint === key &&
				typeof state.notified_at === "number" &&
				Date.now() - state.notified_at < NOTIFY_TTL_MS;
			if (notifiedRecently) return;
			writeCache({ ...state, notified_fingerprint: key, notified_at: Date.now() });
			ctx.ui.notify(`${describe(info)}（/tools-check 复检）`, "warning");
		})().catch((error) => {
			if (DEBUG) console.error(`[tool-check] ${error instanceof Error ? error.message : error}`);
		});
	});

	pi.registerCommand("tools-check", {
		description: "重查本机缺失的 CTF / 跑分工具（只提示，不安装）",
		handler: async (_args, ctx) => {
			const info = await checkNow();
			if (!info) {
				ctx.ui.notify("工具检查失败：bin/tools_check.py 不存在或 python 不可用", "error");
				return;
			}
			writeCache({ ...readCache(), at: Date.now(), fingerprint: fingerprint(info), info });
			ctx.ui.setStatus(STATUS_KEY, (info.missing || []).length ? `tools !${(info.missing || []).length}` : undefined);
			if (!(info.missing || []).length) {
				const alt = (info.needs_alt || []).length
					? `；另有 ${(info.needs_alt || []).length} 个需要 WSL/VM/Docker`
					: "";
				ctx.ui.notify(`工具齐全（${info.checked ?? "?"} 项检查完成）${alt}`, "info");
				return;
			}
			ctx.ui.notify(describe(info, 12), "warning");
		},
	});
}
