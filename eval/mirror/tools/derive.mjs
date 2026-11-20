// Regenerate prompt/, tools/, reference/ and manifest.json from a pi session file.
// Usage: PI_SESSION_FILE=<session.jsonl> node mirror/tools/derive.mjs
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

const SESSION = process.env.PI_SESSION_FILE;
if (!SESSION) throw new Error("set PI_SESSION_FILE to a pi session .jsonl");
const OUT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const sha = (s) => crypto.createHash("sha256").update(s).digest("hex");
const ensure = (d) => fs.mkdirSync(d, { recursive: true });

const lines = fs.readFileSync(SESSION, "utf8").split("\n").filter(Boolean).map(JSON.parse);
const sys = lines.find((o) => o.type === "message" && o.message.role === "system").message;
const order = Object.keys(sys.sections);

ensure(path.join(OUT, "prompt/sections"));
const parts = [];
for (const key of order) {
  const body = sys.sections[key];
  fs.writeFileSync(path.join(OUT, "prompt/sections", `${key}.md`), body, "utf8");
  parts.push(body);
}
const systemMd = parts.join("\n\n") + "\n";
fs.writeFileSync(path.join(OUT, "prompt/system.md"), systemMd, "utf8");

ensure(path.join(OUT, "tools"));
const tools = sys.toolsAdded;
fs.writeFileSync(path.join(OUT, "tools/tools.json"), JSON.stringify(tools, null, 2) + "\n", "utf8");

ensure(path.join(OUT, "reference"));
const trace = fs.readFileSync(SESSION, "utf8");
fs.writeFileSync(path.join(OUT, "reference/trace.jsonl"), trace, "utf8");

const manifest = {
  schemaVersion: 1,
  kind: "agent-mirror",
  id: `pi/${process.env.PI_SESSION_ID}`,
  createdAt: new Date().toISOString(),
  harness: {
    name: "pi",
    version: "1.5.2",
    installRoot: process.env.PI_MANAGED_INSTALL_ROOT,
    env: { PI_CODING_AGENT: process.env.PI_CODING_AGENT, PI_REASONING_LEVEL: process.env.PI_REASONING_LEVEL },
  },
  model: {
    provider: process.env.PI_PROVIDER,
    id: process.env.PI_MODEL,
    thinkingLevel: lines.find((o) => o.type === "thinking_level_change")?.thinkingLevel ?? null,
    reasoningLevel: process.env.PI_REASONING_LEVEL ?? null,
  },
  runtime: { platform: process.platform, node: process.version, shell: "bash (git-bash on win32)", cwd: process.cwd() },
  prompt: {
    source: "session system message .sections",
    order,
    join: "\\n\\n",
    assembledSha256: sha(systemMd),
    assembledBytes: Buffer.byteLength(systemMd),
    sections: Object.fromEntries(order.map((k) => [k, { sha256: sha(sys.sections[k]), bytes: Buffer.byteLength(sys.sections[k]) }])),
    caveat: "pi stores the prompt as sections; the exact transport-level concatenation is harness-internal. This bundle uses '\\n\\n' in section order.",
  },
  tools: tools.map((t) => ({
    name: t.name,
    parameters: t.parameters,
    constrainedSampling: t.constrainedSampling ?? null,
    descriptionSha256: sha(t.description),
  })),
  mcpServers: ["mcp__ctf2"],
  reference: [{ path: "reference/trace.jsonl", sha256: sha(trace), messages: lines.filter((o) => o.type === "message").length }],
  limitations: [
    "tool result rendering and truncation are pi-side behavior, not part of the prompt",
    "subagent/persona wrappers add their own preamble, so agents/mirror.md is an approximation",
    "MCP server mcp__ctf2 tools were reachable via codemode but are not bundled",
  ],
};
fs.writeFileSync(path.join(OUT, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n", "utf8");
console.log("wrote", OUT, "\nprompt bytes", manifest.prompt.assembledBytes, "sha", manifest.prompt.assembledSha256.slice(0, 16));
