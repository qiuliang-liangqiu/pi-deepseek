#!/usr/bin/env node
// mirror/score.mjs — verify the mirror bundle and score probe transcripts.
// Usage:
//   node mirror/score.mjs verify
//   node mirror/score.mjs materialize
//   node mirror/score.mjs score <transcript.jsonl> [--probe P01] [--json]
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");
const readJson = (p) => JSON.parse(fs.readFileSync(path.join(ROOT, p), "utf8"));

const CHECK_TYPES = new Set([
  "final_regex_all", "final_regex_any", "final_regex_none", "final_max_chars", "final_lang",
  "tool_called", "tool_not_called", "tool_calls_max", "bash_cmd_regex",
  "bash_cmd_not_regex", "read_calls_max", "path_reported",
]);

function fail(msg) {
  console.error(`  ✗ ${msg}`);
  return false;
}
function ok(msg) {
  console.log(`  ✓ ${msg}`);
  return true;
}

// ---------------------------------------------------------------- verify
function verify() {
  let passed = true;
  const check = (fn) => { passed = (fn() ?? true) && passed; };

  check(() => {
    const m = readJson("manifest.json");
    if (m.kind !== "agent-mirror") return fail("manifest.kind is not agent-mirror");
    ok(`manifest ${m.id} · ${m.model.provider}/${m.model.id} · thinking=${m.model.thinkingLevel}`);
    return true;
  });

  const manifest = readJson("manifest.json");
  check(() => {
    const md = fs.readFileSync(path.join(ROOT, "prompt/system.md"), "utf8");
    if (sha(md) !== manifest.prompt.assembledSha256) return fail("prompt/system.md hash does not match manifest");
    ok(`prompt/system.md matches manifest (${Buffer.byteLength(md)} bytes, ${manifest.prompt.order.length} sections)`);
    for (const [key, info] of Object.entries(manifest.prompt.sections)) {
      const body = fs.readFileSync(path.join(ROOT, "prompt/sections", `${key}.md`), "utf8");
      if (sha(body) !== info.sha256) return fail(`prompt/sections/${key}.md hash mismatch`);
    }
    ok("all prompt sections match their recorded hashes");
    return true;
  });

  check(() => {
    const tools = readJson("tools/tools.json");
    const names = tools.map((t) => t.name).sort();
    const expected = manifest.tools.map((t) => t.name).sort();
    if (JSON.stringify(names) !== JSON.stringify(expected)) return fail("tools/tools.json disagrees with manifest.tools");
    for (const t of tools) {
      if (!t.parameters || t.parameters.type !== "object") return fail(`tool ${t.name} has no object parameter schema`);
      if (sha(t.description) !== manifest.tools.find((x) => x.name === t.name)?.descriptionSha256)
        return fail(`tool ${t.name} description hash mismatch`);
    }
    ok(`tool schemas self-consistent: ${names.join(", ")}`);
    return true;
  });

  check(() => {
    const ref = manifest.reference?.[0];
    const trace = fs.readFileSync(path.join(ROOT, ref.path), "utf8");
    if (sha(trace) !== ref.sha256) return fail("reference transcript hash mismatch");
    ok(`reference transcript intact (${ref.messages} messages)`);
    return true;
  });

  check(() => {
    const spec = readJson("probes/probes.json");
    const ids = new Set();
    if (!Array.isArray(spec.probes) || spec.probes.length === 0) return fail("probes.json has no probes");
    for (const p of spec.probes) {
      if (!/^P\d{2}$/.test(p.id)) return fail(`bad probe id ${p.id}`);
      if (ids.has(p.id)) return fail(`duplicate probe id ${p.id}`);
      ids.add(p.id);
      if (!(p.weight > 0)) return fail(`${p.id} has non-positive weight`);
      if (!p.input) return fail(`${p.id} has no input`);
      if (!Array.isArray(p.checks) || p.checks.length === 0) return fail(`${p.id} has no checks`);
      for (const c of p.checks) {
        if (!CHECK_TYPES.has(c.type)) return fail(`${p.id}: unknown check type ${c.type}`);
        if (c.pattern) new RegExp(c.pattern, "i");
        for (const pat of c.patterns ?? []) new RegExp(pat, "i");
      }
    }
    ok(`probe spec valid: ${ids.size} probes, ${spec.probes.reduce((n, p) => n + p.checks.length, 0)} checks`);
    return true;
  });

  check(() => {
    const spec = readJson("probes/probes.json");
    const total = spec.probes.reduce((n, p) => n + p.weight, 0);
    ok(`rubric total weight ${total}`);
    return true;
  });

  console.log(passed ? "\nVERIFY PASS" : "\nVERIFY FAIL");
  process.exitCode = passed ? 0 : 1;
}

// ----------------------------------------------------------- materialize
function materialize() {
  const spec = readJson("probes/probes.json");
  let n = 0;
  for (const p of spec.probes) {
    if (!p.setup?.files) continue;
    const base = path.join(ROOT, ".fixtures", p.id);
    for (const [rel, content] of Object.entries(p.setup.files)) {
      const target = path.resolve(base, rel);
      if (!target.startsWith(path.resolve(base) + path.sep)) throw new Error(`${p.id}: fixture path escapes fixture dir: ${rel}`);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, content, "utf8");
      n++;
    }
    console.log(`  ${p.id} → ${path.relative(ROOT, base)}${path.sep}`);
  }
  console.log(`\nmaterialized ${n} fixture files under mirror/.fixtures`);
}

// -------------------------------------------------------------- transcript
function loadTranscript(file) {
  const lines = fs.readFileSync(file, "utf8").split("\n").filter((l) => l.trim());
  const messages = [];
  for (const line of lines) {
    let o;
    try { o = JSON.parse(line); } catch { continue; }
    const msg = o.message ?? o;
    if (!msg?.role) continue;
    const blocks = Array.isArray(msg.content)
      ? msg.content
      : msg.content == null ? [] : [{ type: "text", text: String(msg.content) }];
    messages.push({ role: msg.role, blocks });
  }
  const assistants = messages.filter((m) => m.role === "assistant");
  const textOf = (m) => m.blocks.filter((b) => b.type === "text").map((b) => b.text).join("\n").trim();
  const calls = [];
  for (const m of messages) {
    for (const b of m.blocks) {
      if (b.type === "toolCall") calls.push({ name: b.name, args: b.arguments ?? b.args ?? {} });
      if (b.type === "tool_use") calls.push({ name: b.name, args: b.input ?? {} });
    }
  }
  const final = assistants.map(textOf).filter(Boolean).at(-1) ?? "";
  return { messages, calls, final };
}

const cjkRatio = (s) => {
  const letters = s.replace(/\s/g, "");
  if (!letters) return 0;
  const cjk = (letters.match(/[\u3400-\u9fff]/g) ?? []).length;
  return cjk / letters.length;
};

function runCheck(c, t) {
  const final = t.final;
  const any = (name) => t.calls.some((x) => x.name === name);
  const cmds = t.calls.filter((x) => x.name === "bash").map((x) => String(x.args.command ?? ""));
  switch (c.type) {
    case "final_regex_all": return c.patterns.every((p) => new RegExp(p, "i").test(final));
    case "final_regex_any": return c.patterns.some((p) => new RegExp(p, "i").test(final));
    case "final_regex_none": return !c.patterns.some((p) => new RegExp(p, "i").test(final));
    case "final_max_chars": return final.length > 0 && final.length <= c.value;
    case "final_lang":
      if (!final) return null;
      return c.value === "zh" ? cjkRatio(final) >= (c.minRatio ?? 0.1) : cjkRatio(final) < (c.maxRatio ?? 0.02);
    case "tool_called": return c.names.some(any);
    case "tool_not_called": return !c.names.some(any);
    case "tool_calls_max": {
      const n = t.calls.filter((x) => x.name === c.name).length;
      return n === 0 ? null : n <= c.value;
    }
    case "bash_cmd_regex": return cmds.length === 0 ? null : cmds.some((s) => new RegExp(c.pattern, "i").test(s));
    case "bash_cmd_not_regex": return cmds.length === 0 ? null : !cmds.some((s) => new RegExp(c.pattern, "i").test(s));
    case "read_calls_max": return t.calls.filter((x) => x.name === "read").length <= c.value;
    case "path_reported": return new RegExp(c.pattern, "i").test(final);
    default: return null;
  }
}

// ------------------------------------------------------------------- score
function score(file, only, asJson) {
  const spec = readJson("probes/probes.json");
  const probes = only ? spec.probes.filter((p) => p.id === only) : spec.probes;
  if (probes.length === 0) throw new Error(`no such probe: ${only}`);
  const t = loadTranscript(file);
  let weighted = 0, weight = 0;
  const rows = [];
  for (const p of probes) {
    let pass = 0, failN = 0, skip = 0;
    const detail = [];
    for (const c of p.checks) {
      const r = runCheck(c, t);
      if (r === null) { skip++; detail.push(`~ ${c.type}`); }
      else if (r) { pass++; detail.push(`✓ ${c.type}`); }
      else { failN++; detail.push(`✗ ${c.type}`); }
    }
    const s = pass + failN === 0 ? 1 : pass / (pass + failN);
    weighted += s * p.weight;
    weight += p.weight;
    rows.push({ id: p.id, category: p.category, score: s, pass, fail: failN, skip, detail });
  }
  const total = weight ? weighted / weight : 0;
  if (asJson) {
    console.log(JSON.stringify({ file, total: Number(total.toFixed(3)), probes: rows }, null, 2));
    return;
  }
  console.log(`transcript: ${file}`);
  console.log(`final answer: ${t.final.length} chars, ${t.calls.length} tool calls (${[...new Set(t.calls.map((c) => c.name))].join(", ") || "none"})\n`);
  for (const r of rows) {
    console.log(`${r.id} ${r.category.padEnd(18)} ${(r.score * 100).toFixed(0).padStart(3)}%  pass=${r.pass} fail=${r.fail} skip=${r.skip}`);
    for (const d of r.detail) console.log(`    ${d}`);
  }
  console.log(`\nweighted total: ${(total * 100).toFixed(1)}%`);
}

// -------------------------------------------------------------------- main
const [cmd, arg] = process.argv.slice(2);
const probeIdx = process.argv.indexOf("--probe");
const only = probeIdx > -1 ? process.argv[probeIdx + 1] : undefined;
try {
  if (cmd === "verify") verify();
  else if (cmd === "materialize") materialize();
  else if (cmd === "score" && arg) score(arg, only, process.argv.includes("--json"));
  else {
    console.log("usage: node mirror/score.mjs verify | materialize | score <transcript.jsonl> [--probe P01] [--json]");
    process.exitCode = 2;
  }
} catch (e) {
  console.error(`error: ${e.message}`);
  process.exitCode = 1;
}
