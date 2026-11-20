/**
 * CTFY-0001 tools for pi.
 *
 * The platform hands the agent a briefing in $CTFY_WORK_DIR and grades
 * answers through its REST API. pi has no notion of either, so this
 * extension adds the three tool calls the contract expects:
 *
 *   submit_answer  → POST /api/v1/submissions (or /qa/submissions)
 *   get_challenge  → GET  /api/v1/challenges/{id}   (gated questions)
 *   finish         → write result.json, end the run
 *
 * State is kept in $CTFY_WORK_DIR/.ctfy-state.json so the adapter can
 * produce a result.json even if pi dies before `finish` is called.
 */

import fs from "node:fs";
import path from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const WORK = (process.env.CTFY_WORK_DIR || "/ctfy/work").trim();
const STATE_PATH = path.join(WORK, ".ctfy-state.json");
const RESULT_PATH = path.join(WORK, "result.json");

function readJSON(p: string): any {
	try {
		return JSON.parse(fs.readFileSync(p, "utf8"));
	} catch {
		return {};
	}
}

const TASK = readJSON(path.join(WORK, "task.json"));
const MODE = (process.env.CTFY_RUN_MODE || TASK.run_mode || "instance").trim();
const PLATFORM = (process.env.CTFY_PLATFORM_URL || TASK.platform_url || "").replace(/\/+$/, "");
const TOKEN = (process.env.CTFY_TOKEN || "").trim();
const CHALLENGE_ID = (process.env.CTFY_EVAL_CHALLENGE_ID || TASK?.challenge?.id || "").trim();
const COMPETITION_ID = (process.env.CTFY_EVAL_COMPETITION_ID || TASK.competition_id || "").trim();
const INSTANCE = (process.env.CTFY_EVAL_TARGET_INSTANCE || TASK.target_instance || "").trim();

type State = {
	answers: Record<string, string>;
	solved: string[];
	fully_solved: boolean;
	turns: number;
	tool_calls_made: number;
	stop_reason: string;
	writeup: string;
};

function loadState(): State {
	const s = readJSON(STATE_PATH);
	return {
		answers: s.answers && typeof s.answers === "object" ? s.answers : {},
		solved: Array.isArray(s.solved) ? s.solved : [],
		fully_solved: Boolean(s.fully_solved),
		turns: Number(s.turns) || 0,
		tool_calls_made: Number(s.tool_calls_made) || 0,
		stop_reason: typeof s.stop_reason === "string" ? s.stop_reason : "done",
		writeup: typeof s.writeup === "string" ? s.writeup : "",
	};
}

function saveState(state: State): void {
	try {
		fs.mkdirSync(WORK, { recursive: true });
		const tmp = STATE_PATH + ".tmp";
		fs.writeFileSync(tmp, JSON.stringify(state), "utf8");
		fs.renameSync(tmp, STATE_PATH);
	} catch {
		/* the adapter rewrites a result.json regardless */
	}
}

function writeResult(state: State): void {
	const payload: Record<string, unknown> = {
		schema_version: 3,
		answers: state.answers,
		solved: state.solved,
		fully_solved: state.fully_solved,
		turns: state.turns,
		tool_calls_made: state.tool_calls_made,
		input_tokens: 0,
		output_tokens: 0,
		stop_reason: state.stop_reason,
	};
	if (state.writeup) payload.writeup = state.writeup;
	try {
		const tmp = RESULT_PATH + ".tmp";
		fs.writeFileSync(tmp, JSON.stringify(payload), "utf8");
		fs.renameSync(tmp, RESULT_PATH);
	} catch {
		/* nothing else to do */
	}
	saveState(state);
}

async function api(method: string, route: string, body?: unknown): Promise<{ ok: boolean; status: number; data: any }> {
	if (!PLATFORM || !TOKEN) {
		return { ok: false, status: 0, data: { error: "CTFY_PLATFORM_URL or CTFY_TOKEN is missing" } };
	}
	try {
		const resp = await fetch(PLATFORM + "/api/v1" + route, {
			method,
			headers: {
				Authorization: `Bearer ${TOKEN}`,
				...(body === undefined ? {} : { "Content-Type": "application/json" }),
			},
			body: body === undefined ? undefined : JSON.stringify(body),
			signal: AbortSignal.timeout(30_000),
		});
		const text = await resp.text();
		let data: any;
		try {
			data = JSON.parse(text);
		} catch {
			data = { raw: text.slice(0, 2000) };
		}
		return { ok: resp.ok, status: resp.status, data };
	} catch (err) {
		return { ok: false, status: 0, data: { error: String(err) } };
	}
}

export default function ctfyTools(pi: ExtensionAPI) {
	const bump = (state: State) => {
		state.tool_calls_made += 1;
		saveState(state);
	};

	pi.registerTool({
		name: "submit_answer",
		label: "Submit Answer",
		description:
			"Grade one answer for one question through the CTFY platform right now. Returns correct / attempts_remaining / fully_solved. Use it the moment you have a flag; do not batch submissions until the end.",
		promptSnippet: "Submit one flag/answer for one question and get graded immediately",
		promptGuidelines: [
			"Call submit_answer as soon as you have a plausible flag; wrong attempts are limited so submit real-looking flags, not placeholders.",
			"After a correct submission with gated questions, call get_challenge to unlock the next question.",
		],
		parameters: Type.Object({
			question_id: Type.String({ description: 'Question id from task.json or get_challenge, e.g. "flag".' }),
			answer: Type.String({ description: "The flag or answer string to grade." }),
		}),
		async execute(_toolCallId, params) {
			const state = loadState();
			bump(state);
			const qid = (params.question_id || "flag").trim();
			const answer = String(params.answer ?? "").trim();
			let route: string;
			let body: Record<string, unknown>;
			if (MODE === "qa") {
				route = "/qa/submissions";
				body = {
					challenge_id: CHALLENGE_ID,
					competition_id: COMPETITION_ID,
					question_id: qid || "answer",
					answer,
				};
			} else {
				route = "/submissions";
				body = { instance_id: INSTANCE, question_id: qid, answer };
			}
			const res = await api("POST", route, body);
			if (res.ok) {
				state.answers[qid] = answer;
				const solved = res.data?.correct === true || res.data?.status === "correct";
				if (solved && !state.solved.includes(qid)) state.solved.push(qid);
				if (res.data?.fully_solved !== undefined) state.fully_solved = Boolean(res.data.fully_solved);
				if (solved) state.fully_solved = state.fully_solved || Boolean(res.data?.fully_solved);
			}
			writeResult(state);
			const text = res.ok
				? JSON.stringify(res.data)
				: `submission failed: HTTP ${res.status} ${JSON.stringify(res.data).slice(0, 1500)}`;
			return {
				content: [{ type: "text", text }],
				isError: !res.ok,
				details: { question_id: qid, solved: state.solved },
			};
		},
	});

	pi.registerTool({
		name: "get_challenge",
		label: "Refresh Challenge",
		description:
			"Re-read the challenge from the platform: questions, descriptions and attachments as they are now. Call after every correct answer when gated questions are in play.",
		promptSnippet: "Refresh the challenge record to unlock gated questions",
		parameters: Type.Object({}),
		async execute() {
			const state = loadState();
			bump(state);
			if (!CHALLENGE_ID) {
				return { content: [{ type: "text", text: "no challenge id in this run" }], isError: true, details: {} };
			}
			const res = await api("GET", `/challenges/${encodeURIComponent(CHALLENGE_ID)}`);
			const text = res.ok ? JSON.stringify(res.data).slice(0, 20_000) : `HTTP ${res.status} ${JSON.stringify(res.data).slice(0, 1000)}`;
			return { content: [{ type: "text", text }], isError: !res.ok, details: {} };
		},
	});

	pi.registerTool({
		name: "finish",
		label: "Finish Run",
		description:
			"End this run: merge any unsent answers, write result.json and exit. Call it when every visible question is answered, the budget is nearly spent, or the run mode's work is done.",
		promptSnippet: "Write result.json and end the run",
		promptGuidelines: [
			"Call finish before the wall clock runs out; an unfinished run scores nothing from result.json.",
		],
		parameters: Type.Object({
			stop_reason: Type.String({ description: 'Short reason, e.g. "done", "budget", "blocked".' }),
			writeup: Type.Optional(Type.String({ description: "Short writeup: what worked, what blocked you, key offsets/paths." })),
			answers: Type.Optional(
				Type.Record(Type.String(), Type.String(), {
					description: "Optional question_id -> answer map for anything not submitted yet.",
				}),
			),
		}),
		async execute(_toolCallId, params) {
			const state = loadState();
			bump(state);
			state.stop_reason = (params.stop_reason || "done").trim() || "done";
			if (params.writeup) state.writeup = String(params.writeup);
			if (params.answers && typeof params.answers === "object") {
				for (const [qid, ans] of Object.entries(params.answers)) {
					if (qid && ans) state.answers[String(qid)] = String(ans);
				}
			}
			writeResult(state);
			// Tell the adapter the run is over: pi in print mode would otherwise
			// keep calling the model after `finish` returned.
			try {
				fs.writeFileSync(path.join(WORK, ".ctfy-done"), state.stop_reason, "utf8");
			} catch {
				/* the adapter still has the timeout backstop */
			}
			return {
				content: [
					{
						type: "text",
						text: `result.json written: solved=[${state.solved.join(", ")}] stop_reason=${state.stop_reason}`,
					},
				],
				details: {},
			};
		},
	});

	pi.on("turn_end", () => {
		const state = loadState();
		state.turns += 1;
		saveState(state);
	});
}
