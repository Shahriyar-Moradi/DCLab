import assert from "node:assert/strict";
import test from "node:test";

import {
  BETA_NOTE, SSE_MAX_CHARS, STREAM_ENDED, SseParser, assistantError, proposalError, stripBidi, assistantGate, conversationCostText, dclabHref, foldTurns, isBetaOff, knownEvents, money,
  pageContextFor, readFlag, replySegments, screenSuggestions, toolWords, turnCostText, writeFlag, type AssistantEvent,
} from "./studio-assistant.ts";

const P = "11111111-1111-4111-8111-111111111111";
const E = "22222222-2222-4222-8222-222222222222";
const T = "33333333-3333-4333-8333-333333333333";
const X = "44444444-4444-4444-8444-444444444444";
const sse = (type: string, seq: number, data: object) => `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify(data)}\n\n`;

/* ---------- gating ---------- */

const gov = (over: Record<string, unknown> = {}) => ({
  ai_enabled_setting: true, policy_unavailable: null, assistant_beta: true,
  switches: { platform_ai_blocking: null, workspace: [] as Array<{ switch_key: string; state: string; held_by_incident: boolean }> }, ...over,
});

test("gate: the panel is offered only when AI is available AND the workspace has the beta; unknown is hidden", () => {
  assert.equal(assistantGate(gov()), "available");
  assert.equal(assistantGate(null), "hidden");
  assert.equal(assistantGate(undefined), "hidden");
  assert.equal(assistantGate(gov({ assistant_beta: false })), "hidden");
  assert.equal(assistantGate(gov({ assistant_beta: undefined })), "hidden");
  assert.equal(assistantGate(gov({ ai_enabled_setting: false })), "hidden");
  assert.equal(assistantGate(gov({ policy_unavailable: "policy_unavailable" })), "hidden");
  assert.equal(assistantGate(gov({ switches: { platform_ai_blocking: "global_ai", workspace: [] } })), "hidden");
  assert.equal(assistantGate(gov({ switches: { platform_ai_blocking: null, workspace: [{ switch_key: "all_ai", state: "off", held_by_incident: false }] } })), "hidden");
  assert.equal(assistantGate(gov({ switches: { platform_ai_blocking: null, workspace: [{ switch_key: "all_ai", state: "on", held_by_incident: true }] } })), "hidden");
  // Another key off (one helper) does not hide the whole assistant.
  assert.equal(assistantGate(gov({ switches: { platform_ai_blocking: null, workspace: [{ switch_key: "agent:experiment_critic", state: "off", held_by_incident: false }] } })), "available");
  assert.match(BETA_NOTE, /^Beta:/);
  // The assistant's own helper, use or provider switched off also hides it.
  for (const key of ["agent:lead", "purpose:assistant.turn", "provider:anthropic"]) {
    const g = { ...gov({ switches: { platform_ai_blocking: null, workspace: [{ switch_key: key, state: "off", held_by_incident: false }] } }), model_in_use: { provider: "anthropic" } };
    assert.equal(assistantGate(g), "hidden", key);
  }
});

/* ---------- SSE parser ---------- */

test("SSE: events split anywhere across chunks, LF / CRLF / CR line ends, comments ignored, turn_id lifted out of data", () => {
  const stream = `: keep-alive\r\n${sse("turn_started", 0, { turn_id: T, templated: false }).replaceAll("\n", "\r\n")}${sse("tool_call", 1, { turn_id: T, call: 1, tool: "get_project" }).replaceAll("\n", "\r")}${sse("turn_done", 2, { turn_id: T, status: "completed", error_code: null, proposal_ids: [] })}`;
  for (const size of [1, 2, 3, 7, 1000]) {
    const parser = new SseParser();
    const out: AssistantEvent[] = [];
    for (let i = 0; i < stream.length; i += size) out.push(...parser.feed(stream.slice(i, i + size)));
    out.push(...parser.end());
    assert.deepEqual(out.map((e) => [e.type, e.seq, e.turn_id]), [["turn_started", 0, T], ["tool_call", 1, T], ["turn_done", 2, T]], `chunk ${size}`);
    assert.equal(Object.hasOwn(out[1].data, "turn_id"), false);
    assert.equal(out[1].data.tool, "get_project");
    assert.equal(parser.malformed, 0);
  }
});

test("SSE: multi-line data joins with newlines; a final event without a blank line is flushed by end()", () => {
  const parser = new SseParser();
  assert.deepEqual(parser.feed('event: assistant_message\ndata: {"turn_id":"t","message":\ndata: "hi"}'), []);
  const out = parser.end();
  assert.equal(out.length, 1);
  assert.equal(out[0].data.message, "hi");
  assert.equal(out[0].seq, -1); // no id line
});

test("SSE: unknown types, non-object JSON, broken JSON and oversized events are skipped and counted, never thrown", () => {
  const parser = new SseParser();
  const out = parser.feed(
    `${sse("message", 0, { a: 1 })}event: tool_call\ndata: [1,2]\n\nevent: tool_call\ndata: null\n\nevent: tool_call\ndata: {broken\n\n${sse("turn_done", 3, { turn_id: T, status: "completed" })}`,
  );
  assert.deepEqual(out.map((e) => e.type), ["turn_done"]);
  assert.equal(parser.malformed, 4);
  const big = new SseParser();
  const huge = `event: assistant_message\ndata: ${JSON.stringify({ message: "x".repeat(SSE_MAX_CHARS + 10) })}\n\n${sse("turn_done", 1, { turn_id: T })}`;
  assert.deepEqual(big.feed(huge).map((e) => e.type), ["turn_done"]);
  assert.equal(big.malformed, 1);
});

test("SSE: a __proto__ key in event data never pollutes Object.prototype and is never read", () => {
  const parser = new SseParser();
  const out = parser.feed('event: assistant_message\ndata: {"turn_id":"t","__proto__":{"polluted":true},"constructor":{"x":1},"message":"ok"}\n\n');
  assert.equal(({} as Record<string, unknown>).polluted, undefined);
  assert.equal(Object.hasOwn(out[0].data, "__proto__"), false);
  assert.equal(foldTurns(out)[0].replies[0].text, "ok");
  // Inherited names on a plain object are not fields.
  const turns = foldTurns([{ type: "assistant_message", turn_id: "t", seq: 0, data: Object.create({ message: "inherited" }) }]);
  assert.deepEqual(turns[0].replies, []);
});

/* ---------- folding events into turns ---------- */

test("turns: user text, replies, steps in words, fixed refusal sentences, proposals once, failure words by code", () => {
  const ev = (type: string, data: Record<string, unknown>, turn = T): AssistantEvent => ({ type: type as AssistantEvent["type"], turn_id: turn, seq: 0, data });
  const turns = foldTurns([
    ev("user_message", { message: "why is precision down?" }),
    ev("turn_started", {}),
    ev("tool_call", { tool: "get_monitoring" }),
    ev("tool_call", { tool: "get_monitoring" }),
    ev("tool_call", { tool: "<b>weird_tool</b>" }),
    ev("call_refused", { target: "tool", code: "capability_denied" }),
    ev("call_refused", { target: "tool", code: "something new <script>" }),
    ev("step_rejected", { check: "citations", reasons: ["link_not_cited"] }),
    ev("budget_exhausted", { scope: "thread", limit: 1000000 }),
    ev("proposal_created", { proposal_id: E }),
    ev("proposal_created", { proposal_id: "not-a-uuid" }),
    ev("assistant_message", { message: "See [Run 1](dclab://experiment/" + E + ").", templated: false }),
    ev("turn_done", { status: "failed", error_code: "turn_stream_ended", proposal_ids: [E, X] }),
  ]);
  assert.equal(turns.length, 1);
  const turn = turns[0];
  assert.equal(turn.userText, "why is precision down?");
  assert.deepEqual(turn.steps, ["read monitoring", "<b>weird tool</b>"]);
  assert.deepEqual(turn.notes, [
    "A step was refused: the assistant may not use that here.", "A step was refused by the rules.",
    "A draft step failed a safety check and was not used.", "This conversation reached its spending limit. Start a new conversation to go on.",
  ]);
  assert.deepEqual(turn.proposalIds, [E, X]);
  assert.equal(turn.replies.length, 1);
  assert.equal(turn.done, true);
  assert.match(turn.failed!, /^The live view ended early/);
  assert.equal(toolWords("__proto__"), "proto");
  assert.equal(toolWords("constructor"), "constructor");
  assert.equal(toolWords(42), "ran a step");
});

test("turns: an AI-off template turn and a thread-level limit note are done without turn_done; steps read their result; proposals once across turns", () => {
  const ev = (type: string, data: Record<string, unknown>, turn: string): AssistantEvent => ({ type: type as AssistantEvent["type"], turn_id: turn, seq: 0, data });
  const turns = foldTurns([
    ev("user_message", { message: "what can you do" }, "a"),
    ev("assistant_message", { message: "AI is off", templated: true }, "a"),
    ev("budget_exhausted", { scope: "thread", limit: "cost_micros" }, "thread"),
    ev("user_message", { message: "go" }, "b"),
    ev("tool_call", { call: 1, tool: "get_project" }, "b"),
    ev("tool_result", { call: 1, tool: "get_project", ok: false, code: "timeout" }, "b"),
    ev("tool_call", { call: 2, tool: "run_experiment" }, "b"),
    ev("tool_result", { call: 2, tool: "run_experiment", ok: true, proposal_id: E }, "b"),
    ev("proposal_created", { proposal_id: E }, "b"),
    ev("turn_done", { status: "completed", proposal_ids: [E] }, "c"),
  ]);
  assert.deepEqual(turns.map((t) => [t.turnId, t.done]), [["a", true], ["thread", true], ["b", false], ["c", true]]);
  assert.deepEqual(turns[2].steps, ["read the project (did not work)", "drafted a run"]);
  assert.deepEqual(turns[2].proposalIds, [E]);
  assert.deepEqual(turns[3].proposalIds, [], "a proposal is shown once, on its first turn");
});

test("text: bidirectional override controls are dropped from replies and user text", () => {
  const rlo = String.fromCharCode(0x202e);
  const fsi = String.fromCharCode(0x2068);
  assert.equal(stripBidi(`a${rlo}b${fsi}c`), "abc");
  const turns = foldTurns([
    { type: "user_message", turn_id: "a", seq: 0, data: { message: `hi${rlo}there` } },
    { type: "assistant_message", turn_id: "a", seq: 1, data: { message: `exe.${rlo}txt` } },
  ]);
  assert.equal(turns[0].userText, "hithere");
  assert.equal(turns[0].replies[0].text, "exe.txt");
});

test("turns: events of two turns stay apart in arrival order; unknown event types from a read are dropped", () => {
  const events = knownEvents([
    { type: "user_message", turn_id: "a", seq: 0, data: { message: "one" } },
    { type: "made_up", turn_id: "a", seq: 1, data: { message: "x" } },
    { type: "user_message", turn_id: "b", seq: 0, data: { message: "two" } },
    { type: "assistant_message", turn_id: "a", seq: 2, data: { message: "first answer", templated: true } },
  ]);
  const turns = foldTurns(events);
  assert.deepEqual(turns.map((t) => [t.turnId, t.userText, t.replies.map((r) => r.text)]), [["a", "one", ["first answer"]], ["b", "two", []]]);
  assert.equal(turns[0].replies[0].templated, true);
});

/* ---------- dclab:// link mapper ---------- */

test("links: only [label](dclab://kind/uuid) becomes an internal link; everything else stays literal text", () => {
  const msg = `Run [Run 1](dclab://experiment/${E}) beat [the goal](dclab://problem_spec/${X}); see [x](https://evil.example) and <img src=x onerror=alert(1)> and [y](javascript:alert(1)) and [z](dclab://finding/${E}) and [w](dclab://experiment/not-a-uuid).`;
  const segs = replySegments(msg, P);
  const links = segs.filter((s) => s.kind === "link");
  assert.deepEqual(links, [
    { kind: "link", label: "Run 1", href: `/projects/${P}/experiments/${E}` },
    { kind: "link", label: "the goal", href: `/projects/${P}/goal` },
    { kind: "link", label: "z", href: null },
  ]);
  const text = segs.filter((s) => s.kind === "text").map((s) => (s as { text: string }).text).join("");
  assert.match(text, /\[x\]\(https:\/\/evil\.example\)/);
  assert.match(text, /<img src=x onerror=alert\(1\)>/);
  assert.match(text, /\[y\]\(javascript:alert\(1\)\)/);
  assert.match(text, /\[w\]\(dclab:\/\/experiment\/not-a-uuid\)/);
  // Joined back, nothing is lost.
  assert.equal(segs.map((s) => (s.kind === "text" ? s.text : `[${s.label}]`)).join("").includes("beat"), true);
});

test("links: every route is built from UUIDs only; kinds without a page and inherited names get no href", () => {
  assert.equal(dclabHref("model_version", E, P), `/projects/${P}/models/${E}`);
  assert.equal(dclabHref("dataset_version", E, P), `/projects/${P}/data/${E}`);
  assert.equal(dclabHref("split_plan", E, P), `/projects/${P}/splits/${E}`);
  assert.equal(dclabHref("decision", E, P), `/projects/${P}/decisions?record=${E}`);
  assert.equal(dclabHref("proposal", E, P), "/inbox");
  for (const kind of ["__proto__", "constructor", "toString", "candidate", "prediction"]) assert.equal(dclabHref(kind, E, P), null, kind);
  assert.equal(dclabHref("experiment", E, "../evil"), null);
  assert.equal(dclabHref("experiment", "x", P), null);
  assert.deepEqual(replySegments(`[a](dclab://experiment/${E})`, null), [{ kind: "link", label: "a", href: null }]);
});

/* ---------- page context ---------- */

test("page context: the screen and route ids only, UUIDs required", () => {
  assert.deepEqual(pageContextFor(`/projects/${P}/experiments/${E}`), { projectId: P, context: { screen: "experiment", experiment_id: E } });
  assert.deepEqual(pageContextFor(`/projects/${P}/models/${E}?tab=score`), { projectId: P, context: { screen: "model", model_version_id: E } });
  assert.deepEqual(pageContextFor(`/projects/${P}/data/${E}`), { projectId: P, context: { screen: "data", dataset_id: E } });
  assert.deepEqual(pageContextFor(`/projects/${P}/splits/${E}`), { projectId: P, context: { screen: "goal", split_plan_id: E } });
  assert.deepEqual(pageContextFor(`/projects/${P}/monitoring`), { projectId: P, context: { screen: "monitoring" } });
  assert.deepEqual(pageContextFor(`/projects/${P}/experiments/compare`), { projectId: P, context: { screen: "experiments" } });
  assert.deepEqual(pageContextFor(`/projects/${P}/pipeline/${E}`), { projectId: P, context: { screen: "experiments" } });
  assert.deepEqual(pageContextFor(`/projects/${P}/__proto__`), { projectId: P, context: { screen: "project" } });
  assert.deepEqual(pageContextFor("/inbox"), { projectId: null, context: { screen: "inbox" } });
  assert.deepEqual(pageContextFor("/projects/not-a-uuid/data"), { projectId: null, context: null });
  assert.deepEqual(pageContextFor("/governance"), { projectId: null, context: null });
});

/* ---------- cost wording ---------- */

test("cost: dollars from micros, 'upper bound' when the price is unconfirmed, 'estimate' otherwise; templated turns used no AI", () => {
  assert.equal(money(190_000), "$0.19");
  assert.equal(money(4_000), "less than $0.01");
  assert.equal(money(0), "$0.00");
  assert.equal(money(Number.NaN), "$0.00");
  assert.equal(conversationCostText(190_000, true, 1_000_000), "Cost of this conversation: $0.19 of $1.00 (upper bound: the model's price is not confirmed yet)");
  assert.equal(conversationCostText(190_000, false), "Cost of this conversation: $0.19 (estimate from DCLab's own records)");
  assert.equal(conversationCostText(0, false, "1000"), "Cost of this conversation: $0.00 (estimate from DCLab's own records)");
  assert.equal(turnCostText(20_000, false, true), "$0.02 (upper bound)");
  assert.equal(turnCostText(20_000, false, false), "$0.02 (estimate)");
  assert.equal(turnCostText(0, true, true), "No AI used");
  assert.equal(turnCostText(null, false, false), "Cost not recorded");
});

/* ---------- errors ---------- */

const apiError = (status: number, code?: string, message = "server text <b>x</b>") => ({ status, body: code ? { error: { code, message } } : null });

test("errors: fixed sentences by code then status; the server's message is never shown; beta-off is recognised", () => {
  assert.equal(assistantError(apiError(403, "assistant_beta_off")).title, "The assistant beta is not on for this workspace");
  assert.equal(isBetaOff(apiError(403, "ASSISTANT_BETA_OFF")), true);
  assert.equal(isBetaOff(apiError(403, "forbidden")), false);
  assert.equal(assistantError(apiError(409, "turn_in_progress")).title, "An answer is still being written");
  assert.equal(assistantError(apiError(429, "thread_limit_reached")).title, "This conversation reached its limit");
  assert.equal(assistantError(apiError(429, "user_turn_limit")).title, "You reached the hourly limit");
  const concurrent = { status: 429, body: { error: { code: "user_turn_limit", message: "x", details: { limit: "concurrent_turns" } } } };
  assert.equal(assistantError(concurrent).title, "Another answer of yours is still being written");
  assert.equal(assistantError(apiError(429, "user_turn_limit_concurrent")).title, "Too many requests", "an internal key is not a server code");
  assert.match(STREAM_ENDED.detail, /still being written or saved/);
  assert.equal(assistantError(apiError(429)).title, "Too many requests");
  assert.equal(assistantError(apiError(503, "assistant_unavailable")).title, "The assistant is not ready in this workspace");
  assert.equal(assistantError(apiError(503, "assistant_busy")).title, "Too many answers are being written");
  assert.equal(assistantError(apiError(503)).title, "The assistant is unavailable right now");
  assert.equal(assistantError(apiError(409, "new_code")).title, "Something changed at the same time");
  assert.equal(assistantError(apiError(422, "page_context_invalid")).title, "This screen could not be shared with the assistant");
  assert.equal(assistantError(apiError(404, "not_found")).title, "This conversation is not available");
  for (const code of ["__proto__", "constructor", "toString"]) assert.equal(assistantError(apiError(500, code)).title, "The assistant could not answer", code);
  assert.equal(assistantError(new TypeError("Failed to fetch")).title, "The assistant could not answer");
  for (const status of [403, 404, 409, 422, 429, 503, 500]) {
    const sentence = assistantError(apiError(status, "x", "LEAK"));
    assert.doesNotMatch(`${sentence.title} ${sentence.detail}`, /LEAK|<b>/);
  }
});

test("suggestion card errors: fixed sentences by code then status, never the server's message", () => {
  assert.equal(proposalError(apiError(403, "owner_only")).title, "Only the person who had this conversation can accept it");
  assert.equal(proposalError(apiError(409, "proposal_expired")).title, "This suggestion expired");
  assert.equal(proposalError(apiError(428)).title, "The page is out of date");
  assert.equal(proposalError(apiError(412)).title, "This changed while you were looking");
  assert.equal(proposalError(apiError(500, "__proto__")).title, "This could not be saved");
  for (const status of [400, 403, 404, 409, 412, 422, 428, 429, 500]) {
    const e = proposalError(apiError(status, "zzz", "LEAK <b>"));
    assert.doesNotMatch(`${e.title} ${e.detail}`, /LEAK|<b>/);
  }
});

/* ---------- per-user flag and per-screen suggestions ---------- */

test("flag: off by default, survives storage that throws or is missing", () => {
  const store = new Map<string, string>();
  const ok = () => ({ getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) });
  assert.equal(readFlag(ok, "k"), false);
  writeFlag(ok, "k", true);
  assert.equal(readFlag(ok, "k"), true);
  const throwing = () => { throw new Error("blocked"); };
  assert.equal(readFlag(throwing, "k"), false);
  assert.doesNotThrow(() => writeFlag(throwing, "k", true));
  assert.equal(readFlag(() => null, "k"), false);
});

test("suggestions: open proposals of this project about what the screen shows, at most five; never closed ones", () => {
  const prop = (id: string, kind: string, subjectId: string | null, open = true, project = P) => ({ id, project_id: project, open, subject: { kind, id: subjectId } });
  const list = [
    prop("a", "experiment", E), prop("b", "experiment", X), prop("c", "experiment", E, false), prop("d", "experiment", E, true, X),
    prop("e", "dataset_version", X), prop("f", "project", null),
  ];
  assert.deepEqual(screenSuggestions(list, `/projects/${P}/experiments/${E}`).map((p) => p.id), ["a"]);
  assert.deepEqual(screenSuggestions(list, `/projects/${P}/experiments`).map((p) => p.id), ["a", "b"]);
  assert.deepEqual(screenSuggestions(list, `/projects/${P}/data`).map((p) => p.id), ["e"]);
  assert.deepEqual(screenSuggestions(list, `/projects/${P}`).map((p) => p.id), ["f"]);
  assert.deepEqual(screenSuggestions(list, `/projects/${P}/decisions`), []);
  assert.deepEqual(screenSuggestions(list, "/inbox"), []);
  const many = Array.from({ length: 9 }, (_, i) => prop(String(i), "experiment", E));
  assert.equal(screenSuggestions(many, `/projects/${P}/experiments`).length, 5);
});
