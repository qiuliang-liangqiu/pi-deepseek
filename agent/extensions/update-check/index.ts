/**
 * update-check：自动检测 pi-agent workbench 的新版本，并让用户决定是否升级。
 *
 * 版本源是仓库里的 `agent/VERSION`（安装后落在 ~/.pi/agent/VERSION）。
 * 每次 session 启动时异步跑一次 `bin/workbench_update.py check --json`：
 *   - 有新版本：footer 显示 `wb ↑ <remote>`，并提示 `/wb-update`
 *   - 检测结果缓存 6 小时（PI_UPDATE_TTL），缺网时自动降级，不阻塞会话
 *
 * 命令：
 *   /wb-check   强制联网检查一次
 *   /wb-update  显示更新计划，确认后执行（备份 + 本地改动保留）
 *
 * 关闭自动检查：PI_UPDATE_CHECK=off（命令仍然可用）。
 * 本机带 `.update-hold` 时视为“配置源头”：不提示、不跟随仓库版本，
 * 本地改动由本机自己的发布工具推出（不随仓库分发）。
 * 这个扩展只读版本字符串；升级必须经过 /wb-update 的确认框。
 */
import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const AGENT_DIR = path.resolve(HERE, "..", "..");
const SCRIPT = path.join(AGENT_DIR, "bin", "workbench_update.py");
const STATE_FILE = path.join(AGENT_DIR, ".update-notified.json");
const STATUS_KEY = "workbench-update";
// 显式带上安装目录：脚本默认 ~/.pi/agent，但 PI_AGENT_DIR 可能被改过
const AGENT_ARGS = ["--agent-dir", AGENT_DIR];
const MODE = (process.env.PI_UPDATE_CHECK ?? "on").toLowerCase();
const NOTIFY_TTL_MS = 24 * 3600 * 1000;

type CheckInfo = {
	status: "update_available" | "content_diff" | "up_to_date" | "unknown_local" | "offline" | string;
	hold?: boolean;
	local?: string | null;
	remote?: string | null;
	cached?: boolean;
	stale?: boolean;
	error?: string;
	manifest_url?: string;
	manifest_version?: string;
	manifest_commit?: string;
	manifest_error?: string;
	content_count?: number;
	content_changed?: string[];
	content_missing?: string[];
	local_ahead?: boolean;
};

type RunResult = { code: number; stdout: string; stderr: string };

function pythonCandidates(): string[] {
	const env = (process.env.PI_PYTHON || "").trim();
	const base = [env, process.platform === "win32" ? "python" : "python3", "python"].filter(Boolean);
	return [...new Set(base)];
}

function run(args: string[], timeout = 60_000): Promise<RunResult> {
	const candidates = pythonCandidates();
	const attempt = (i: number): Promise<RunResult> =>
		new Promise((resolve) => {
			if (i >= candidates.length) {
				resolve({ code: -1, stdout: "", stderr: "python not found" });
				return;
			}
			execFile(
				candidates[i],
				[SCRIPT, ...args],
				{
					timeout,
					windowsHide: true,
					maxBuffer: 8 << 20,
					env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
				},
				(err, stdout, stderr) => {
					const out = String(stdout ?? "");
					const errOut = String(stderr ?? "");
					if (err && (err as NodeJS.ErrnoException).code === "ENOENT") {
						void attempt(i + 1).then(resolve);
						return;
					}
					const raw = (err as { code?: unknown } | null)?.code;
					const code = typeof raw === "number" ? raw : err ? -1 : 0;
					resolve({ code, stdout: out, stderr: errOut });
				},
			);
		});
	return attempt(0);
}

function parseCheck(result: RunResult): CheckInfo | null {
	const lines = result.stdout.split(/\r?\n/).filter((l) => l.trim().startsWith("{"));
	for (let i = lines.length - 1; i >= 0; i--) {
		try {
			const obj = JSON.parse(lines[i]);
			if (obj && typeof obj.status === "string") return obj as CheckInfo;
		} catch {
			// 继续找下一行
		}
	}
	return null;
}

function readState(): { version?: string; at?: number } {
	try {
		return JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
	} catch {
		return {};
	}
}

function shouldNotify(key: string): boolean {
	const state = readState();
	const fresh = typeof state.at === "number" && Date.now() - state.at < NOTIFY_TTL_MS;
	if (state.version === key && fresh) return false;
	try {
		fs.writeFileSync(STATE_FILE, JSON.stringify({ version: key, at: Date.now() }) + "\n", "utf8");
	} catch {
		// 写不了状态就照常提示
	}
	return true;
}

function tail(text: string, maxLines = 22, maxChars = 1800): string {
	const lines = text.trim().split(/\r?\n/);
	const body = lines.slice(-maxLines).join("\n");
	return body.length > maxChars ? body.slice(-maxChars) : body;
}

async function checkNow(force: boolean): Promise<CheckInfo | null> {
	const result = await run(["check", "--json", ...AGENT_ARGS, ...(force ? ["--force"] : [])], 45_000);
	return parseCheck(result);
}

function describe(info: CheckInfo): string {
	const local = info.local || "未知";
	const remote = info.remote || "未知";
	if (info.status === "update_available") return `有新版本 ${local} -> ${remote}`;
	if (info.status === "content_diff")
		return `本地 Agent 与 GitHub 内容不同（${info.content_count ?? "?"} 个文件；本机 ${local}，远端 ${remote}）`;
	if (info.status === "up_to_date") return `已是最新 ${local}`;
	if (info.status === "unknown_local") return `本机没有版本文件（远端 ${remote}），先跑一次安装器`;
	if (info.status === "offline") return `取不到远端版本${info.error ? `（${info.error}）` : ""}`;
	return `状态：${info.status}`;
}

function actionable(info: CheckInfo): boolean {
	return info.status === "update_available" || info.status === "content_diff";
}

function notifyKey(info: CheckInfo): string {
	return `${info.status}:${info.manifest_commit || info.remote || ""}:${info.content_count || 0}`;
}

export default function (pi: ExtensionAPI) {
	if (!fs.existsSync(SCRIPT)) {
		// 旧安装没有检查器：静默，不打扰会话
		if (process.env.PI_UPDATE_CHECK === "debug") {
			console.error(`[update-check] missing ${SCRIPT}`);
		}
		return;
	}

	pi.on("session_start", async (_event, ctx) => {
		if (MODE === "off") return;
		void (async () => {
			const info = await checkNow(false);
			if (!info || !ctx.hasUI) return;
			// .update-hold = 配置源头：本机才是最新，不提示跟随仓库
			if (info.hold) {
				ctx.ui.setStatus(STATUS_KEY, undefined);
				return;
			}
			if (info.status === "update_available" && info.remote) {
				ctx.ui.setStatus(STATUS_KEY, `wb ↑ ${info.remote}`);
				if (shouldNotify(notifyKey(info))) {
					ctx.ui.notify(
						`workbench 有新版本 ${info.remote}（本机 ${info.local}）。/wb-update 查看计划并升级`,
						"info",
					);
				}
			} else if (info.status === "content_diff") {
				ctx.ui.setStatus(STATUS_KEY, `wb ≠ ${info.content_count ?? "?"}`);
				if (shouldNotify(notifyKey(info))) {
					ctx.ui.notify(
						`本地 Agent 与 GitHub 内容不同（${info.content_count ?? "?"} 个文件）。/wb-update 查看计划；本地文件会保留 .local 副本`,
						"warning",
					);
				}
			} else if (info.status === "up_to_date") {
				ctx.ui.setStatus(STATUS_KEY, undefined);
			}
		})().catch(() => {
			// 网络/子进程失败不影响会话
		});
	});

	pi.registerCommand("wb-check", {
		description: "检查 pi-agent workbench 是否有新版本（忽略缓存）",
		handler: async (_args, ctx) => {
			const info = await checkNow(true);
			if (!info) {
				ctx.ui.notify("版本检查失败：检查器没有返回结果（看 ~/.pi/agent/bin/workbench_update.py）", "error");
				return;
			}
			if (info.hold) {
				ctx.ui.notify(
					`本机是配置源头（.update-hold）：本地 ${info.local} / 仓库 ${info.remote || "?"}。不会自动跟随仓库`，
					"info",
				);
				return;
			}
			ctx.ui.notify(describe(info), "info");
			if (actionable(info)) {
				ctx.ui.setStatus(
					STATUS_KEY,
					info.status === "content_diff" ? `wb ≠ ${info.content_count ?? "?"}` : `wb ↑ ${info.remote}`,
				);
			}
		},
	});

	pi.registerCommand("wb-update", {
		description: "查看并升级 pi-agent workbench（保留 auth/settings 与本地改动）",
		handler: async (_args, ctx) => {
			if (!ctx.hasUI) {
				ctx.ui.notify("升级需要交互确认；在终端里跑 bin/workbench_update.py apply", "error");
				return;
			}
			const info = await checkNow(true);
			if (!info) {
				ctx.ui.notify("版本检查失败，未执行升级", "error");
				return;
			}
			if (info.hold) {
				ctx.ui.notify(
					"本机带 .update-hold（配置源头），拒绝用仓库覆盖本地。需要发布时用本机自己的工具。",
					"warning",
				);
				return;
			}
			if (info.status === "up_to_date") {
				ctx.ui.notify(describe(info), "info");
				return;
			}
			if (!actionable(info)) {
				ctx.ui.notify(describe(info), "warning");
				return;
			}

			ctx.ui.notify("正在生成更新计划…", "info");
			const plan = await run(["apply", "--dry-run", ...AGENT_ARGS], 120_000);
			const planText = tail(plan.stdout || plan.stderr || "(没有输出)");
			const title =
				info.status === "content_diff"
					? `本地 Agent 与 GitHub 有 ${info.content_count ?? "?"} 个文件不同；应用远端版本？`
					: `升级 workbench ${info.local} -> ${info.remote}?`;
			const ok = await ctx.ui.confirm(
				title,
				`${planText}\n\n会先备份到 ~/.pi/agent/backups/，你本地改过的文件会保留 .local-<时间戳> 副本。`,
			);
			if (!ok) {
				ctx.ui.notify("已取消升级", "info");
				return;
			}

			ctx.ui.setStatus(STATUS_KEY, "wb updating…");
			const result = await run(["apply", "--yes", ...AGENT_ARGS], 600_000);
			if (result.code === 0) {
				// 更新脚本跑了但版本没变：多半是本地 checkout 过期 / git pull 失败
				const after = await checkNow(true);
				if (after && actionable(after)) {
					ctx.ui.setStatus(
						STATUS_KEY,
						after.status === "content_diff" ? `wb ≠ ${after.content_count ?? "?"}` : `wb ↑ ${after.remote}`,
					);
					ctx.ui.notify(
						`文件已更新，但本机版本仍是 ${after.local}（远端 ${after.remote}）：检查本地 checkout 是否落后，或在仓库目录跑 git pull 后重试 /wb-update`,
						"warning",
					);
					return;
				}
				ctx.ui.setStatus(STATUS_KEY, undefined);
				ctx.ui.notify(
					`workbench 已更新到 ${info.remote}。执行 /reload（或重启 pi）让新扩展/脚本生效。`,
					"info",
				);
			} else {
				ctx.ui.notify(`升级失败（exit ${result.code}）：\n${tail(result.stdout + "\n" + result.stderr, 14, 900)}`, "error");
				ctx.ui.setStatus(STATUS_KEY, `wb ↑ ${info.remote}`);
			}
		},
	});
}
