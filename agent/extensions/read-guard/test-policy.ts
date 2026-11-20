import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import { analyzeBashCommand } from "./policy.ts";

const root = fs.mkdtempSync(path.join(os.tmpdir(), "read-guard-"));
fs.writeFileSync(path.join(root, "package.json"), '{\n  "name": "probe-fixture"\n}\n');
fs.writeFileSync(path.join(root, "a.txt"), "a\n");
fs.writeFileSync(path.join(root, "b.txt"), "b\n");
fs.writeFileSync(path.join(root, "file with space.txt"), "space\n");
fs.writeFileSync(path.join(root, "img.png"), Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x00, 0x01]));

const cases: Array<[string, boolean]> = [
	["cat package.json", true],
	['cat "file with space.txt"', true],
	["cat package.json | grep name", false],
	["cat a.txt b.txt", false],
	["cat -n package.json", false],
	["cat img.png", false],
	["cat missing.txt", false],
	["cat package.json > out.txt", false],
	["cat package.json && echo ok", false],
	["cat $FILE", false],
	["cat /dev/null", false],
	["ls package.json", false],
	["cat .", false],
];

let failed = 0;
for (const [cmd, expected] of cases) {
	const hit = analyzeBashCommand(cmd, root, 0);
	const ok = Boolean(hit) === expected;
	console.log(`${ok ? "PASS" : "FAIL"} ${cmd} -> ${hit ? "hit" : "miss"}`);
	if (!ok) failed++;
}

// minBytes: package.json 约 30B，阈值 999999 应放过
assert.equal(analyzeBashCommand("cat package.json", root, 999999), null);
console.log("PASS minBytes threshold");

fs.rmSync(root, { recursive: true, force: true });
if (failed) {
	console.error(`${failed} case(s) failed`);
	process.exit(1);
}
console.log("all policy cases passed");
