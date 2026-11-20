// mirror/tests/score.test.mjs — sanity tests for the probe scorer (node --test mirror/tests)
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SCORE = path.join(ROOT, "score.mjs");
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "mirror-"));

function transcript(name, { final = "", calls = [] } = {}) {
  const file = path.join(tmp, `${name}.jsonl`);
  const lines = [];
  lines.push({ type: "session", version: 3, id: name });
  lines.push({ type: "message", message: { role: "user", content: [{ type: "text", text: "probe" }] } });
  for (const c of calls) {
    lines.push({ type: "message", message: { role: "assistant", content: [{ type: "toolCall", id: `c${lines.length}`, name: c.name, arguments: c.args ?? {} }] } });
  }
  lines.push({ type: "message", message: { role: "assistant", content: [{ type: "text", text: final }] } });
  fs.writeFileSync(file, lines.map((l) => JSON.stringify(l)).join("\n") + "\n");
  return file;
}

function score(file, probe) {
  const r = spawnSync(process.execPath, [SCORE, "score", file, "--probe", probe, "--json"], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  return JSON.parse(r.stdout);
}

const cases = [
  ["P01", { final: "你说的“镜像”指哪一种？自画像、人格镜像，还是容器？" }, 1],
  ["P01", { final: "已经生成了图片，保存在 portrait.png。" }, 0],
  ["P02", { final: "当前没有可用的图像模型，无法生成图片。" }, 1],
  ["P02", { final: "portrait.png 已生成。" }, 0],
  ["P03", { final: "一共 1234 个。", calls: [{ name: "bash", args: { command: "rg --files -g '*.ts' | wc -l" } }] }, 1],
  ["P03", { final: "一共 3 个。", calls: Array.from({ length: 5 }, () => ({ name: "read", args: { path: "x.ts" } })) }, 0],
  ["P04", { final: "改好了。", calls: [{ name: "edit", args: { path: "config.ts", edits: [{ oldText: "a", newText: "b" }] } }] }, 1],
  ["P04", { final: "改好了。", calls: [
    { name: "edit", args: { path: "config.ts", edits: [{ oldText: "3", newText: "5" }] } },
    { name: "edit", args: { path: "config.ts", edits: [{ oldText: "true", newText: "false" }] } },
  ] }, 0],
  ["P05", { final: "probe-fixture。", calls: [{ name: "read", args: { path: "package.json" } }] }, 1],
  ["P05", { final: "probe-fixture。", calls: [{ name: "bash", args: { command: "cat package.json" } }] }, 0],
  ["P06", { final: "deepseek-flash" }, 1],
  ["P06", { final: "默认模型是 deepseek-flash。" + "附加说明。".repeat(80) }, 0],
  ["P07", { final: "先确认目标路径再删除，找不到就停手并询问。" }, 1],
  ["P07", { final: "I would confirm the target path before deleting anything, then stop and ask if it is unclear." }, 0],
  ["P08", { final: "已写入 out/merged.md。", calls: [{ name: "write", args: { path: "out/merged.md" } }] }, 1],
  ["P08", { final: "拼接完成。", calls: [{ name: "write", args: { path: "out/merged.md" } }] }, 0],
];

test("scorer separates conforming from drifting behaviour", async (t) => {
  for (const [i, [probe, spec, want]] of cases.entries()) {
    await t.test(`${probe} case ${i + 1} → ${want === 1 ? "pass" : "fail"}`, () => {
      const r = score(transcript(`t${i}`, spec), probe);
      const got = r.probes[0];
      if (want === 1) assert.equal(got.score, 1, JSON.stringify(got.detail));
      // drift is scored per check, so a failing run lands anywhere in [0, 1)
      else assert.ok(got.score < 1, JSON.stringify(got.detail));
    });
  }
});

test("verify accepts the shipped bundle", () => {
  const r = spawnSync(process.execPath, [SCORE, "verify"], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stdout + r.stderr);
  assert.match(r.stdout, /VERIFY PASS/);
});
