/**
 * tool-check：会话启动时按“工具装在哪个环境”自动检查，缺工具就弹选项。
 *
 * 流程（session_start，只有 TUI 才弹）：
 *   1. Agent 自己跑在 Ubuntu/Kali/macOS → 检查当前系统，齐全给一条检测通过提示，缺了就弹
 *      「一键自动安装 / 手动安装（给命令）/ 稍后」。
 *   2. Agent 跑在 Windows → 先找 Linux 环境：
 *        WSL（装了但没启动就先替你启动）→ 进去检查工具；
 *        然后是 ~/.ssh/config 里能免密连上的主机。
 *      齐全给一条检测通过提示；不齐全 → 列出缺什么 + 自动/手动选项。
 *   3. 找不到 Linux 环境 → 弹「未检测到 Linux 环境」，两个选项：
 *        「工具装在 Ubuntu/Kali」→ 给启动步骤，启动后重新检测；
 *        「工具用本机 Windows」→ 检查本机，缺了同样给自动/手动。
 *
 * 命令：/tools-check 复检并报告；/tools-setup 手动选目标 + 安装。
 * 开关：PI_TOOL_CHECK=off / debug；PI_TOOL_CHECK_ASK=off 只留 footer、不弹提示。
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
// 跑分/triage 会话里不能弹窗打断（run-gate：全自动跑分不许停下来问用户）
const BENCH = !!(
	process.env.BENCHMARK_TOKEN ||
	process.env.BENCHMARK_BASE_URL ||
	process.env.TBENCH_TRIAGE ||
	process.env.TBENCH_AUTO_TEAM
);
const ASK = (process.env.PI_TOOL_CHECK_ASK ?? (BENCH ? "off" : "on")).toLowerCase() !== "off";
const SNOOZE_MS = 6 * 3600 * 1000;
const SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=accept-new"];

type NeedsAlt = { id: string; why?: string; use?: string };
type Target = { key: string; label: string; kind?: string; ready?: boolean; note?: string; os_name?: string };
type Probe = { id: string; probe: string; module?: string; alt?: string[]; platforms?: string[] };
type WslRow = { name: string; state?: string; version?: number; default?: boolean };
type EnvInfo = {
	os?: string;
	pretty?: string;
	distro?: string;
	wsl?: boolean;
	wsl_distro?: string;
	container?: boolean;
	admin?: boolean;
	sudo?: boolean;
	sudo_nopass?: boolean;
	pkg?: string[];
	wsl_distros?: string[];
	wsl_detail?: WslRow[];
	ssh_hosts?: string[];
};
type ToolInfo = {
	platform?: string;
	checked?: number;
	ok?: string[];
	missing?: string[];
	needs_alt?: NeedsAlt[];
	covered?: { id: string; by: string }[];
	env?: EnvInfo;
	targets?: Target[];
	probes?: Probe[];
	groups?: Record<string, string>;
	script?: string;
	hint?: string;
	catalog_version?: number;
	generated_at?: number;
};
type Cache = {
	at?: number;
	fingerprint?: string;
	info?: ToolInfo;
	script?: string | undefined;
	pref?: string;
	last_state?: string;
	ok_at?: number;
	snooze_until?: number;
	snooze_fp?: string;
	notified_fingerprint?: string;
	notified_at?: number;
};
type RunResult = { code: number; stdout: string; stderr: string; errno?: string };
type Selection = {
	label: string;
	targetArgs: string[];
	kind: string;
	remote: boolean;
	prefix?: string[];
	sudo: boolean;
	auto: boolean;
	blocked?: string;
};
type Ui = {
	hasUI: boolean;
	ui: {
		setStatus: (key: string, value?: string) => void;
		notify: (message: string, type?: "info" | "warning" | "error") => void;
		select: (title: string, options: string[], opts?: { timeout?: number }) => Promise<string | undefined>;
		confirm: (title: string, message: string, opts?: { timeout?: number }) => Promise<boolean>;
		input: (title: string, placeholder?: string) => Promise<string | undefined>;
		setWorkingMessage: (message?: string) => void;
	};
};

function pythonCandidates(): string[] {
	const env = (process.env.PI_PYTHON || "").trim();
	const base = [env, process.platform === "win32" ? "python" : "python3", "python"].filter(Boolean);
	return [...new Set(base)];
}

function exec(argv: string[], timeout = 60_000): Promise<RunResult> {
	return new Promise((resolve) => {
		if (!argv.length) {
			resolve({ code: -1, stdout: "", stderr: "empty argv" });
			return;
		}
		execFile(argv[0], argv.slice(1), { timeout, maxBuffer: 4 << 20, windowsHide: true }, (error, stdout, stderr) => {
			const e = error as NodeJS.ErrnoException & { code?: number | string } | null;
			resolve({
				code: typeof e?.code === "number" ? e.code : error ? 1 : 0,
				stdout: String(stdout ?? ""),
				stderr: String(stderr ?? ""),
				errno: typeof e?.code === "string" ? e.code : undefined,
			});
		});
	});
}

async function runPython(args: string[], timeout = 20_000): Promise<RunResult> {
	let last: RunResult = { code: -1, stdout: "", stderr: "no python interpreter found" };
	for (const py of pythonCandidates()) {
		const res = await exec([py, ...args], timeout);
		if (res.errno === "ENOENT" || res.errno === "EACCES") {
			last = res;
			continue;
		}
		return res;
	}
	return last;
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
	const alt = [...(info.needs_alt || []).map((n) => n.id)].sort().join(",");
	return `${info.platform || "?"}:${info.checked ?? "?"}:${missing}|${alt}`;
}

async function checkNow(): Promise<ToolInfo | null> {
	if (!fs.existsSync(SCRIPT)) return null;
	const result = await runPython([SCRIPT, "check", "--json"]);
	const info = parseInfo(result.stdout);
	if (!info && DEBUG) console.error(`[tool-check] tools_check.py rc=${result.code} ${result.stderr.slice(-300)}`);
	return info;
}

function describeEnv(info: ToolInfo): string {
	const env = info.env;
	if (!env) return info.platform || "?";
	const bits = [env.pretty || env.os || "?"];
	if (env.wsl) bits.push("WSL" + (env.wsl_distro ? `: ${env.wsl_distro}` : ""));
	if (env.container) bits.push("容器");
	if (env.admin) bits.push("root");
	return bits.join(" · ");
}

function targetSummary(info: ToolInfo): string {
	const targets = info.targets || [];
	if (!targets.length) return "终端跑安装脚本";
	const kinds = new Set(targets.map((t) => t.kind || ""));
	if (kinds.has("apt") || kinds.has("brew")) return "本机 apt / brew（不弹菜单）";
	const names: string[] = [];
	for (const t of targets) {
		let name = "";
		if (t.kind === "ssh-new") name = "SSH 输入 host";
		else if (t.kind === "wsl-install") name = t.key.includes("kali") ? "Kali(WSL)" : "Ubuntu(WSL)";
		else if (t.kind === "ssh") name = `SSH: ${t.key.replace(/^ssh:/, "")}`;
		else if (t.kind === "docker") name = "Docker";
		else if (t.kind === "winget") name = "Windows 本机";
		else name = t.label.replace(/（.*?）/g, "");
		if (name && !names.includes(name)) names.push(name);
	}
	return names.slice(0, 5).join(" · ");
}

function groupCounts(ids: string[], groups: Record<string, string>): string {
	const m = new Map<string, number>();
	for (const id of ids) {
		const g = groups[id] || "other";
		m.set(g, (m.get(g) || 0) + 1);
	}
	return [...m.entries()]
		.sort((a, b) => b[1] - a[1])
		.map(([g, n]) => `${g} ${n}`)
		.join(" · ");
}

function coveredText(covered: { id: string; by: string }[] | undefined): string {
	const list = covered || [];
	if (!list.length) return "";
	const pairs = list.slice(0, 8).map((c) => `${c.id}→${c.by}`).join(", ");
	return pairs + (list.length > 8 ? ` 等 ${list.length} 个` : "");
}

function missingBlock(
	label: string,
	ids: string[],
	groups: Record<string, string> = {},
	covered: { id: string; by: string }[] = [],
): string {
	const shown = ids.slice(0, 10).join(", ");
	const more = ids.length > 10 ? " …" : "";
	const by = ids.length > 10 ? `（${groupCounts(ids, groups)}）` : "";
	const lines = [`${label} · 缺 ${ids.length} 个工具${by}`, `  缺失：${shown}${more}`];
	const ct = coveredText(covered);
	if (ct) lines.push(`  已用替代：${ct}`);
	return lines.join("\n");
}

function localSummary(info: ToolInfo, ids: string[]): string {
	const missing = info.missing || [];
	const alt = (info.needs_alt || []).map((n) => n.id);
	const groups = info.groups || {};
	const lines = [`本机 ${describeEnv(info)} · 缺 ${ids.length} 个（${groupCounts(ids, groups)}）`];
	if (missing.length) lines.push(`  缺失（${missing.length}）：${missing.slice(0, 10).join(", ")}${missing.length > 10 ? " …" : ""}`);
	else lines.push("  本机可安装的已齐；其余需要 Linux 环境");
	if (alt.length) lines.push(`  需 Linux（${alt.length}）：${groupCounts(alt, groups)}（WSL / VM / Docker）`);
	const ct = coveredText(info.covered);
	if (ct) lines.push(`  已用替代：${ct}`);
	lines.push(`  装到哪：${targetSummary(info)}`);
	return lines.join("\n");
}

async function jsonTargets(script: string): Promise<Target[]> {
	if (!script || !fs.existsSync(script)) return [];
	const result = await runPython([script, "--json-targets"], 120_000);
	try {
		const rows = JSON.parse(result.stdout) as Target[];
		return Array.isArray(rows) ? rows.filter((r) => r && r.key && r.label) : [];
	} catch {
		return [];
	}
}

function mergeTargets(base: Target[], extra: Target[]): Target[] {
	const out: Target[] = [];
	const seen = new Set<string>();
	for (const t of [...base, ...extra]) {
		if (seen.has(t.key)) continue;
		seen.add(t.key);
		out.push(t);
	}
	return out;
}

/** 安装脚本可能在本 cwd 看不到的 checkout 里（另一个 pi 窗口启动的）。 */
function resolveScript(info: ToolInfo): string {
	const candidates = [info.script || "", readCache().script || "", process.env.PI_SETUP_TOOLS || ""];
	for (const cand of candidates) {
		if (cand && fs.existsSync(cand)) return cand;
	}
	return "";
}

function mainScript(info: ToolInfo): string {
	return resolveScript(info) || "scripts/setup-tools.py";
}

function logPath(kind = "setup"): string {
	const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
	return path.join(AGENT_DIR, `tools-${kind}-${stamp}.log`);
}

function tail(text: string, lines = 6): string {
	const all = (text || "").split(/\r?\n/).filter((l) => l.trim());
	return all.slice(-lines).join("\n");
}

function clip(text: string, lines = 12): string {
	const all = (text || "").split(/\r?\n/);
	if (all.length <= lines) return all.join("\n");
	return all.slice(0, lines).join("\n") + `\n…（其余 ${all.length - lines} 行见日志）`;
}

function setStatus(ctx: Ui, value?: string): void {
	ctx.ui.setStatus(STATUS_KEY, value);
}

/** 齐全时也给一条可见反馈：证明检测真的跑过（每个会话一次，不节流）。 */
function completeFeedback(ctx: Ui, info: ToolInfo, where = "", covered?: { id: string; by: string }[]): void {
	const st = readCache();
	setStatus(ctx, where ? `tools ok · ${where}` : "tools ok");
	writeCache({ ...st, last_state: "ok", ok_at: Date.now() });
	const ct = coveredText(covered || info.covered);
	ctx.ui.notify(
		`工具检测通过：${where || describeEnv(info)} · ${info.checked ?? "?"} 项齐全` + (ct ? `
  已用替代：${ct}` : ""),
		"info",
	);
}

function markMissing(): void {
	writeCache({ ...readCache(), last_state: "missing" });
}

function writeState(info: ToolInfo, patch: Cache = {}): void {
	const script = resolveScript(info);
	writeCache({ ...readCache(), at: Date.now(), fingerprint: fingerprint(info), info, script: script || undefined, ...patch });
}

function isSnoozed(info: ToolInfo): boolean {
	const st = readCache();
	return !!st.snooze_until && Date.now() < st.snooze_until && st.snooze_fp === fingerprint(info);
}

function snooze(info: ToolInfo): void {
	writeState(info, { snooze_until: Date.now() + SNOOZE_MS, snooze_fp: fingerprint(info) });
}

/* ------------------------------------------------------------------ probing */

function shq(text: string): string {
	return "'" + text.replace(/'/g, "'\\''") + "'";
}

function probeScript(probes: Probe[], platform = "linux"): string {
	const items = probes.filter((p) => !p.platforms || p.platforms.includes(platform));
	if (!items.length) return "";
	const pairs = items.map((p) => `${p.probe}:${p.id}`).join(" ");
	const mods = items.filter((p) => p.module).map((p) => `${p.id}:${p.probe}:${p.module}`);
	const modIds = items.filter((p) => p.module).map((p) => p.id).join(" ");
	// 用户级 bin（go install / pip --user / gem --user-install）不在非交互 PATH 里，也要算已装
	const parts = [
		'MODIDS="' + modIds + '"; for pair in ' +
			pairs +
			'; do t="${pair%%:*}"; id="${pair##*:}"; case " $MODIDS " in *" $id "*) continue;; esac; command -v "$t" >/dev/null 2>&1 || [ -x "$HOME/go/bin/$t" ] || [ -x "$HOME/.local/bin/$t" ] || ls "$HOME"/.local/share/gem/ruby/*/bin/"$t" >/dev/null 2>&1 || printf "%s " "$id"; done',
	];
	if (mods.length) {
		parts.push(
			'for pair in ' +
				mods.join(" ") +
				'; do id="${pair%%:*}"; rest="${pair#*:}"; t="${rest%%:*}"; m="${rest##*:}"; command -v "$t" >/dev/null 2>&1 && continue; [ -x "$HOME/go/bin/$t" ] && continue; [ -x "$HOME/.local/bin/$t" ] && continue; ls "$HOME"/.local/share/gem/ruby/*/bin/"$t" >/dev/null 2>&1 && continue; python3 -c "import importlib.util as u,sys; sys.exit(0 if u.find_spec(sys.argv[1]) else 1)" "$m" >/dev/null 2>&1 || printf "%s " "$id"; done',
		);
	}
	return parts.join("; ");
}

/** Returns null when the probe itself failed (not a "no tools" answer). */
async function probeExec(prefix: string[], probes: Probe[], mode: "exec" | "ssh"): Promise<string[] | null> {
	const script = probeScript(probes);
	if (!script) return [];
	const argv =
		mode === "ssh"
			? [...prefix, `bash -lc ${shq(script)}`]
			: [...prefix, "bash", "-lc", script];
	const res = await exec(argv, 90_000);
	if (res.code !== 0) return null;
	return [...new Set(res.stdout.trim().split(/\s+/).filter(Boolean))];
}

/** Missing list with Kali-only tools folded into installed equivalents (rizin→radare2, ...). */
async function probeRemote(
	prefix: string[],
	info: ToolInfo,
	mode: "exec" | "ssh",
): Promise<{ missing: string[]; covered: { id: string; by: string }[] } | null> {
	const probes = info.probes || [];
	const raw = await probeExec(prefix, probes, mode);
	if (!raw) return null;
	const alt: Record<string, string[]> = {};
	for (const p of probes) if (p.alt?.length) alt[p.id] = p.alt;
	const altIds = new Set<string>();
	for (const id of raw) for (const a of alt[id] || []) altIds.add(a);
	let installedAlts = new Set<string>();
	if (altIds.size) {
		const sub = probes.filter((p) => altIds.has(p.id));
		const altMissing = await probeExec(prefix, sub, mode);
		if (altMissing) installedAlts = new Set([...altIds].filter((a) => !altMissing.includes(a)));
	}
	const missing: string[] = [];
	const covered: { id: string; by: string }[] = [];
	for (const id of raw) {
		const by = (alt[id] || []).find((a) => installedAlts.has(a));
		if (by) covered.push({ id, by });
		else missing.push(id);
	}
	return { missing, covered };
}

function localSelection(info: ToolInfo): Selection | null {
	const env = info.env || {};
	const targets = info.targets || [];
	if (env.os === "windows") {
		const t = targets.find((x) => x.kind === "winget") || targets.find((x) => x.key === "windows");
		if (!t) return null;
		return { label: t.label, targetArgs: ["--target", t.key], kind: "winget", remote: false, sudo: false, auto: true };
	}
	if (env.os === "linux" || env.os === "darwin") {
		const t = targets.find((x) => x.kind === "brew") || targets.find((x) => x.kind === "apt");
		if (!t) return null;
		const needsSudo = env.os === "linux" && !env.admin;
		const auto = !needsSudo || !!env.sudo_nopass;
		return {
			label: t.label,
			targetArgs: ["--target", t.key],
			kind: t.kind || "apt",
			remote: false,
			sudo: needsSudo,
			auto,
			blocked: auto ? "" : "需要 sudo 密码，扩展里没有 TTY",
		};
	}
	return null;
}

async function findLinux(ctx: Ui, info: ToolInfo): Promise<{ sel: Selection; missing: string[]; covered: { id: string; by: string }[] } | null> {
	const probes = info.probes || [];
	const rows = [...(info.env?.wsl_detail || [])];
	rows.sort((a, b) => (a.state === "Running" ? 0 : 1) - (b.state === "Running" ? 0 : 1));
	for (const row of rows.slice(0, 2)) {
		if (row.state !== "Running") {
			ctx.ui.notify(`检测到 Linux 环境（WSL: ${row.name}，${row.state || "未启动"}），正在启动…`, "info");
			const started = await exec(["wsl.exe", "-d", row.name, "--", "true"], 180_000);
			if (started.code !== 0) {
				ctx.ui.notify(
					`WSL ${row.name} 启动失败：${tail(started.stderr || started.stdout, 2) || `exit ${started.code}`}`,
					"warning",
				);
				continue;
			}
		}
		const prefix = ["wsl.exe", "-d", row.name, "-u", "root", "--"];
		const res = await probeRemote(prefix, info, "exec");
		if (res) {
			return {
				sel: { label: `WSL: ${row.name}`, targetArgs: ["--target", `wsl:${row.name}`], kind: "wsl", remote: true, prefix, sudo: false, auto: true },
				missing: res.missing,
				covered: res.covered,
			};
		}
	}
	for (const host of (info.env?.ssh_hosts || []).slice(0, 2)) {
		const alive = await exec(["ssh", ...SSH_OPTS, host, "true"], 15_000);
		if (alive.code !== 0) continue;
		const prefix = ["ssh", ...SSH_OPTS, host];
		const res = await probeRemote(prefix, info, "ssh");
		if (res) {
			return {
				sel: { label: `SSH: ${host}`, targetArgs: ["--target", `ssh:${host}`], kind: "ssh", remote: true, prefix, sudo: false, auto: true },
				missing: res.missing,
				covered: res.covered,
			};
		}
	}
	return null;
}

/* --------------------------------------------------------------- installing */

function installArgv(script: string, sel: Selection, ids: string[] | undefined, yes: boolean): string[] {
	const argv = [script, ...sel.targetArgs];
	if (ids && ids.length) argv.push("--only", [...new Set(ids)].join(","));
	if (yes) argv.push("--yes");
	if (sel.sudo) argv.push("--allow-sudo");
	return argv;
}

function manualText(script: string, sel: Selection, ids: string[] | undefined): string {
	const cmd = script || "<agent>/scripts/setup-tools.py";
	const only = ids && ids.length ? ` --only ${ids.join(",")}` : "";
	const plain = `python ${cmd} ${sel.targetArgs.join(" ")}${only}`;
	const sudo = sel.sudo ? " --allow-sudo" : "";
	return [
		`手动安装到 ${sel.label}：`,
		`  先看计划：${plain} --list`,
		`  再安装：  ${plain}${sudo}`,
		sel.kind === "ssh" ? "  SSH 需要免密或加 --ssh-password-file <文件>" : "",
		script ? "" : "  本机没有 scripts/setup-tools.py → 先 `/wb-update` 升级（新版本会随 agent 安装），再 `/tools-setup`",
	]
		.filter(Boolean)
		.join("\n");
}

async function runInstall(ctx: Ui, info: ToolInfo, script: string, sel: Selection, ids: string[] | undefined): Promise<void> {
	const argv = installArgv(script, sel, ids, true);
	const logfile = logPath();
	ctx.ui.notify(`开始自动安装到 ${sel.label}…${ids ? `（${ids.length} 个）` : ""}\n日志：${logfile}`, "info");
	ctx.ui.setWorkingMessage(`安装到 ${sel.label}…`);
	const res = await runPython(argv, 30 * 60 * 1000);
	ctx.ui.setWorkingMessage();
	try {
		fs.writeFileSync(logfile, `${argv.join(" ")}\n\n${res.stdout}\n${res.stderr}\n`, "utf8");
	} catch {
		// ignore
	}
	let left: number | null = null;
	if (sel.remote && sel.prefix) {
		const recheck = await probeRemote(sel.prefix, info, sel.kind === "ssh" ? "ssh" : "exec");
		left = recheck ? recheck.missing.length : null;
		setStatus(ctx, left === null ? undefined : left ? `tools !${left}` : `tools ok · ${sel.label}`);
	} else {
		const after = await checkNow();
		left = after?.missing?.length ?? null;
		setStatus(ctx, left ? `tools !${left}` : undefined);
	}
	const verb = res.code === 0 ? "完成" : `结束（exit ${res.code}）`;
	const leftText = left === null ? "复查失败" : `剩余缺失 ${left} 个`;
	ctx.ui.notify(`${verb}：${sel.label} · ${leftText}\n${tail(res.stdout, 5)}\n日志：${logfile}`, res.code === 0 && left === 0 ? "info" : "warning");
}

async function offerInstall(ctx: Ui, info: ToolInfo, script: string, sel: Selection, ids: string[]): Promise<void> {
	if (isSnoozed(info)) return;
	const canAuto = sel.auto && !!script;
	const autoLabel = canAuto ? `一键自动安装（${ids.length} 个）` : `一键自动安装（不可用：${sel.blocked || "找不到安装脚本"}）`;
	const choice = await ctx.ui.select(
		`${sel.label} 缺 ${ids.length} 个工具，怎么装？`,
		[autoLabel, "手动安装（只给我命令）", "稍后（6 小时内不再问）"],
		{ timeout: 60_000 },
	);
	if (!choice) return;
	if (choice.startsWith("一键")) {
		if (!sel.auto) {
			ctx.ui.notify((sel.blocked || "自动安装不可用") + "\n" + manualText(script, sel, ids), "warning");
			return;
		}
		void runInstall(ctx, info, script, sel, ids);
		return;
	}
	if (choice.startsWith("手动")) {
		ctx.ui.notify(manualText(script, sel, ids), "info");
		return;
	}
	snooze(info);
}

/* ------------------------------------------------------------- startup flow */

async function localBranch(ctx: Ui, info: ToolInfo, script: string): Promise<void> {
	const missing = info.missing || [];
	const alt = (info.needs_alt || []).map((n) => n.id);
	const all = [...missing, ...alt];
	if (!all.length) {
		completeFeedback(ctx, info);
		return;
	}
	markMissing();
	setStatus(ctx, `tools !${all.length}`);
	ctx.ui.notify(localSummary(info, all), "warning");
	const sel = localSelection(info);
	if (!sel || !missing.length) return;
	await offerInstall(ctx, info, script, sel, missing);
}

async function askLinuxOrLocal(ctx: Ui, info: ToolInfo): Promise<"retry" | "local" | "skip" | "defer"> {
	const choice = await ctx.ui.select(
		"未检测到 Linux 环境（WSL / 虚拟机 / SSH 都不可用）",
		["工具装在 Ubuntu/Kali（我先去启动它）", "工具用本机（Windows）", "稍后（6 小时内不再问）"],
		{ timeout: 60_000 },
	);
	if (!choice) return "defer"; // Esc / 60s 超时：不退避 6h，只跳过本次
	if (choice.startsWith("工具装在")) {
		ctx.ui.notify(
			[
				"先把 Linux 环境启动起来：",
				"  • WSL：管理员 PowerShell 跑 wsl --install -d Ubuntu（装完重启），之后 wsl -d Ubuntu",
				"  • 虚拟机：启动 VMware / VirtualBox，把 SSH 写进 ~/.ssh/config",
				"  • 远程机：保证 ssh <host> 能免密登录",
				"启动后选“是”会立刻重新检测并进入工具检查。",
			].join("\n"),
			"info",
		);
		return (await ctx.ui.confirm("Linux 环境已启动？重新检测", "选“是”会重新扫 WSL / SSH 并检查工具", { timeout: 120_000 }))
			? "retry"
			: "defer";
	}
	if (choice.startsWith("工具用本机")) return "local";
	snooze(info);
	return "skip";
}

async function startupFlow(ctx: Ui): Promise<void> {
	if (!ctx.hasUI) return;
	let rounds = 0;
	while (rounds < 3) {
		rounds += 1;
		ctx.ui.setWorkingMessage("检查本机 / Linux 环境与工具…");
		const info = await checkNow();
		if (!info) {
			ctx.ui.setWorkingMessage();
			return;
		}
		writeState(info);
		const localIds = [...(info.missing || []), ...(info.needs_alt || []).map((n) => n.id)];
		if (!ASK) {
			setStatus(ctx, localIds.length ? `tools !${localIds.length}` : undefined);
			ctx.ui.setWorkingMessage();
			return; // 只留 footer
		}
		const script = resolveScript(info);
		const env = info.env || {};

		if (env.os === "linux" || env.os === "darwin") {
			ctx.ui.setWorkingMessage();
			await localBranch(ctx, info, script);
			return;
		}
		if (env.os !== "windows") return;

		const found = await findLinux(ctx, info);
		ctx.ui.setWorkingMessage();
		if (found) {
			if (!found.missing.length) {
				completeFeedback(ctx, info, found.sel.label, found.covered);
				return;
			}
			markMissing();
			setStatus(ctx, `tools !${found.missing.length}`);
			ctx.ui.notify(missingBlock(`Linux 环境 ${found.sel.label}`, found.missing, info.groups, found.covered), "warning");
			// 没找到安装脚本也要弹：自动安装项显示“不可用”，手动项给出命令
			await offerInstall(ctx, info, script, found.sel, found.missing);
			return;
		}

		// 没有 Linux 环境：记住用户的偏好（本机）就不用每次问
		if (readCache().pref === "windows") {
			await localBranch(ctx, info, script);
			return;
		}
		if (isSnoozed(info)) {
			const ids = [...(info.missing || []), ...(info.needs_alt || []).map((n) => n.id)];
			if (ids.length) setStatus(ctx, `tools !${ids.length}`);
			return;
		}
		const choice = await askLinuxOrLocal(ctx, info);
		if (choice === "retry") continue;
		if (choice === "local") {
			writeState(info, { pref: "windows" });
			await localBranch(ctx, info, script);
			return;
		}
		if (choice === "defer") return;
		if (choice === "skip") {
			snooze(info);
			return;
		}
	}
	ctx.ui.notify("重新检测后仍未找到 Linux 环境；可以先做其它事，之后跑 /tools-setup 再试。", "warning");
}

/* ------------------------------------------------------------------- entry */

export default function (pi: ExtensionAPI) {
	if (MODE === "off") return;

	pi.on("session_start", async (_event, ctx) => {
		void startupFlow(ctx as unknown as Ui).catch((error) => {
			if (DEBUG) console.error(`[tool-check] ${error instanceof Error ? error.message : error}`);
		});
	});

	pi.registerCommand("tools-check", {
		description: "复检本机 / Linux 环境的工具完整性（只报告，不安装）",
		handler: async (_args, ctx) => {
			const ui = ctx as unknown as Ui;
			const info = await checkNow();
			if (!info) {
				ui.ui.notify("工具检查失败：bin/tools_check.py 不存在或 python 不可用", "error");
				return;
			}
			writeState(info);
			const env = info.env || {};
			if (env.os === "windows") {
				const found = await findLinux(ui, info);
				if (found) {
					if (!found.missing.length) {
						completeFeedback(ui, info, found.sel.label, found.covered);
						return;
					}
					markMissing();
					ui.ui.notify(missingBlock(`Linux 环境 ${found.sel.label}`, found.missing, info.groups, found.covered), "warning");
					setStatus(ui, `tools !${found.missing.length}`);
					return;
				}
			}
			const ids = [...(info.missing || []), ...(info.needs_alt || []).map((n) => n.id)];
			if (!ids.length) {
				completeFeedback(ui, info);
				return;
			}
			markMissing();
			setStatus(ui, `tools !${ids.length}`);
			ui.ui.notify(localSummary(info, ids), "warning");
		},
	});

	pi.registerCommand("tools-setup", {
		description: "选安装目标（Windows 本机 / WSL / SSH / Kali / 本机 apt）补齐缺失工具",
		handler: async (_args, ctx) => {
			const ui = ctx as unknown as Ui;
			const info = await checkNow();
			if (!info) {
				ui.ui.notify("工具检查失败：bin/tools_check.py 不存在或 python 不可用", "error");
				return;
			}
			const script = resolveScript(info);
			if (!script) {
				ui.ui.notify("找不到 scripts/setup-tools.py（可用 PI_SETUP_TOOLS 指定）", "error");
				return;
			}
			const env = info.env || {};
			const localIds = [...(info.missing || []), ...(info.needs_alt || []).map((n) => n.id)];

			// 先看有没有现成的 Linux 环境（会自动启动未运行的 WSL）
			ui.ui.setWorkingMessage("检查 Linux 环境与工具…");
			const found = env.os === "windows" ? await findLinux(ui, info) : null;
			ui.ui.setWorkingMessage();
			if (found && found.missing.length) {
				ui.ui.notify(missingBlock(`Linux 环境 ${found.sel.label}`, found.missing, info.groups, found.covered), "warning");
				await offerInstall(ui, info, script, found.sel, found.missing);
				return;
			}
			if (found && !found.missing.length) {
				completeFeedback(ui, info, found.sel.label, found.covered);
				return;
			}
			if (!localIds.length) {
				completeFeedback(ui, info);
				return;
			}

			const targets = mergeTargets(info.targets || [], await jsonTargets(script));
			if (!targets.length) {
				ui.ui.notify(`没探测到安装目标；终端跑 python ${script} --list`, "warning");
				return;
			}
			const labels = targets.map((t) => (t.ready === false && t.note ? `${t.label}（${t.note}）` : t.label));
			let target: Target | undefined;
			const local = targets.find((t) => t.kind === "apt" || t.kind === "brew");
			if ((env.os === "linux" || env.os === "darwin") && local) target = local;
			else if (targets.length === 1) target = targets[0];
			else {
				const pick = await ui.ui.select(`本机 ${describeEnv(info)} · 缺 ${localIds.length} 个，装到哪？`, labels);
				if (!pick) return;
				target = targets[labels.indexOf(pick)];
			}
			if (!target) return;

			let targetArgs: string[] = ["--target", target.key];
			if (target.kind === "ssh-new") {
				const who = await ui.ui.input("SSH 目标（user@host 或 ~/.ssh/config 别名）:", "user@host");
				if (!who) return;
				const [user, host] = who.includes("@") ? who.split("@", 2) : ["", who];
				targetArgs = ["--ssh-host", host, ...(user ? ["--ssh-user", user] : []), "--target", `ssh:${host}`];
			}
			const sel: Selection = {
				label: target.label,
				targetArgs,
				kind: target.kind || "other",
				remote: target.kind === "ssh" || target.kind === "wsl",
				sudo: target.kind === "apt" && !env.admin && !!env.sudo_nopass,
				auto: target.kind !== "apt" || !!env.admin || !!env.sudo_nopass,
				blocked: target.kind === "apt" && !env.admin && !env.sudo_nopass ? "需要 sudo 密码，扩展里没有 TTY" : "",
			};
			ui.ui.setWorkingMessage(`检查 ${target.label} 的安装计划…`);
			const plan = await runPython([script, ...targetArgs, "--list"], 300_000);
			ui.ui.setWorkingMessage();
			const planText = `${plan.stdout}\n${plan.stderr}`.trim();
			const logfile = logPath("plan");
			try {
				fs.writeFileSync(logfile, planText + "\n", "utf8");
			} catch {
				// ignore
			}
			if (plan.code !== 0 && !planText) {
				ui.ui.notify(`计划失败（exit ${plan.code}）：${tail(plan.stderr, 3)}`, "error");
				return;
			}
			const canInstall = (planText.match(/可装/g) || []).length;
			const fallback = (planText.match(/兜底/g) || []).length;
			const already = (planText.match(/已装/g) || []).length;
			const ok = await ui.ui.confirm(
				`安装到「${target.label}」？`,
				`计划：直接可装 ${canInstall}、go/pip 兜底 ${fallback}、已装 ${already}\n` +
					`前几行：\n${clip(planText, 8)}\n日志：${logfile}`,
			);
			if (!ok) {
				ui.ui.notify(`已取消，未安装（计划见 ${logfile}）`, "info");
				return;
			}
			if (!sel.auto) {
				ui.ui.notify((sel.blocked || "自动安装不可用") + "\n" + manualText(script, sel, undefined), "warning");
				return;
			}
			void runInstall(ui, info, script, sel, undefined);
		},
	});
}
