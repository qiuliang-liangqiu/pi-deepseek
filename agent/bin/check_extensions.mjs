#!/usr/bin/env node
// check_extensions.mjs — 解析校验 pi 扩展的 TypeScript 文件（只解析，不执行）。
//
//   node bin/check_extensions.mjs ~/.pi/agent/extensions
//   node bin/check_extensions.mjs extensions/update-check/index.ts
//
// 用 Node 自带的类型擦除器（node:module stripTypeScriptTypes）做完整语法解析：
// 半写入 / 未闭合字符串 / 少括号这类错误会在这里失败，而不是等你重启 pi 才炸。
// 退出码：0 全部通过，1 有文件解析失败，2 用法错误。

import fs from "node:fs";
import path from "node:path";
import { stripTypeScriptTypes } from "node:module";

const args = process.argv.slice(2);
if (args.length === 0) {
	console.error("usage: check_extensions.mjs <dir-or-file> [...]");
	process.exit(2);
}

const SKIP_DIRS = new Set(["node_modules", "__pycache__", ".git", "backups"]);

function walk(target, out) {
	const st = fs.statSync(target);
	if (st.isFile()) {
		if (target.endsWith(".ts") || target.endsWith(".mts")) out.push(target);
		return;
	}
	for (const entry of fs.readdirSync(target, { withFileTypes: true })) {
		const p = path.join(target, entry.name);
		if (entry.isDirectory()) {
			if (!SKIP_DIRS.has(entry.name)) walk(p, out);
		} else if (entry.isFile() && /\.(ts|mts)$/.test(entry.name)) {
			out.push(p);
		}
	}
}

const files = [];
for (const a of args) {
	try {
		walk(path.resolve(a), files);
	} catch (e) {
		console.error(`cannot read ${a}: ${e.message}`);
		process.exit(2);
	}
}
if (files.length === 0) {
	console.log("no .ts files found");
	process.exit(0);
}

let bad = 0;
for (const f of files) {
	try {
		stripTypeScriptTypes(fs.readFileSync(f, "utf8"), { mode: "transform" });
	} catch (e) {
		bad += 1;
		console.log(`FAIL ${f}: ${e.message}`);
	}
}
console.log(`${bad ? "FAIL" : "OK"} ${files.length - bad}/${files.length} files parsed`);
process.exitCode = bad ? 1 : 0;
