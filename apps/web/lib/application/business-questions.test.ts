import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  BUSINESS_QUESTIONS_HREF, BUSINESS_WORDS, CLOSE_WORDS, ERROR_BY_CODE, GENERIC_WRITE_ERROR, QUESTION_TEXT_MAX, REPLY_WORDS, SENT_UNREADABLE, SHARED_WORDS,
  STUDIO_WORDS, checkQuestionText, chosenSubject, closedSentence, linkChoices, questionRow, replySide, sentButUnreadable, staleAfterClose, personLabel, questionErrorSentence, questionEventWords, questionListView, questionStatus, questionText,
  replyRows, studioQuestionHref, subjectHref, subjectWords, type QuestionLike,
} from "./business-questions.ts";
import { businessQuestionBy, businessQuestionHref, businessQuestionSentence, inboxTitle, outcomeWords } from "./studio-inbox.ts";
import { activityHref, activitySentence } from "./studio-home.ts";
import { eventRow, LOG_EVENT_KINDS } from "./studio-history.ts";

// Business-view words (v7) and wording rules: no ML vocabulary, no AI wording.
const BANNED = /\b(release|champion|ref|refs|digest|holdout|hold-out|feature contract|trust check|cv|cross-validation|candidate|experiment|folds?|accuracy|fresh estimate|L[0-3])\b/i;
const AI_WORDS = /\b(ai|assistant|agent|llm|gpt|bot|automatic(ally)?)\b/i;
const P1 = "11111111-1111-4111-8111-111111111111";
const Q1 = "22222222-2222-4222-8222-222222222222";
const Q2 = "33333333-3333-4333-8333-333333333333";
const Q3 = "44444444-4444-4444-8444-444444444444";
const M1 = "55555555-5555-4555-8555-555555555555";
const L1 = "66666666-6666-4666-8666-666666666666";
const R1 = "77777777-7777-4777-8777-777777777777";

const question = (extra: Partial<QuestionLike> = {}): QuestionLike => ({
  id: Q1, project_id: P1, project_name: "Churn\u202e\u0007  forecast", you_reply_as: "business", subject: null, text: "Is family_5g treated the same?", status: "open", asked_by: { is_you: false, side: "data_team" },
  reply_count: 0, last_reply_at: null, created_at: "2026-10-17T09:00:00Z", closed_at: null, closed_by: null, version: 1, can_reply: true, can_close: false,
  ...extra,
});
const apiError = (status: number, code?: string, message = "server words <b>never shown</b>") => ({ status, body: code ? { error: { code, message } } : null });

test("states: loading, forbidden, not found, failed, empty, ready (open first, then answered, then closed)", () => {
  const words = { ...STUDIO_WORDS };
  assert.equal(questionListView({ pending: true, errorStatus: null, data: undefined }, words).kind, "loading");
  assert.deepEqual(questionListView({ pending: false, errorStatus: 403, data: undefined }, words), { kind: "forbidden", message: STUDIO_WORDS.forbidden });
  assert.deepEqual(questionListView({ pending: false, errorStatus: 404, data: undefined }, words), { kind: "error", message: STUDIO_WORDS.notFound });
  assert.deepEqual(questionListView({ pending: false, errorStatus: 404, data: undefined }, BUSINESS_WORDS), { kind: "error", message: BUSINESS_WORDS.failed });
  assert.deepEqual(questionListView({ pending: false, errorStatus: 0, data: undefined }, words), { kind: "error", message: STUDIO_WORDS.failed });
  assert.deepEqual(questionListView({ pending: false, errorStatus: null, data: { items: [], has_more: false } }, BUSINESS_WORDS), { kind: "empty", message: BUSINESS_WORDS.empty });
  const view = questionListView({ pending: false, errorStatus: null, data: { has_more: true, items: [
    question({ id: Q1, status: "closed", closed_by: { is_you: true, side: "data_team" }, can_reply: true, can_close: true }),
    question({ id: Q2, status: "answered" }),
    question({ id: Q3, status: "open" }),
    question({ id: "not-a-uuid" }),
    question({ id: "__proto__" }),
  ] } }, BUSINESS_WORDS);
  assert.equal(view.kind, "ready");
  if (view.kind !== "ready") return;
  assert.deepEqual(view.rows.map((r) => r.id), [Q3, Q2, Q1]);
  assert.equal(view.more, BUSINESS_WORDS.more);
  // A closed question takes no replies and cannot be closed again, whatever the flags say.
  assert.equal(view.rows[2].canReply, false);
  assert.equal(view.rows[2].canClose, false);
  assert.equal(view.rows[2].closedBy, "You");
  assert.equal(view.rows[2].youReplyAs, null);
  assert.equal(closedSentence(view.rows[2].closedBy), "Closed by you. It takes no more replies.");
  assert.equal(closedSentence("A business user"), "Closed by a business user. It takes no more replies.");
  assert.equal(closedSentence(null), "Closed. It takes no more replies.");
  assert.equal(view.rows[0].projectName, "Churn forecast"); // untrusted name: plain text
  assert.equal(BUSINESS_WORDS.more, "Only the first 100 are shown: open questions first, then the most recently changed.");
});

test("status words and tones; unknown values and inherited keys fall back", () => {
  assert.deepEqual(questionStatus("open"), { label: "Waiting for a reply", tone: "warn", rank: 0 });
  assert.equal(questionStatus("answered").label, "Answered");
  assert.equal(questionStatus("closed").tone, "gray");
  for (const odd of ["__proto__", "constructor", "toString", "hasOwnProperty", "", null, 3]) assert.equal(questionStatus(odd).label, "State not known");
  assert.equal(subjectWords({ kind: "__proto__", id: M1 }), null);
  assert.equal(subjectWords({ kind: "constructor", id: M1 }), null);
  assert.equal(subjectWords({ kind: "model_version", id: M1 }), "About a model");
  assert.equal(subjectWords({ kind: "monitoring_window", id: M1 }), "About a monitoring week");
  // Business view words: no technical subject names.
  assert.equal(subjectWords({ kind: "batch_prediction", id: L1 }, "business"), "About the latest list at the time of asking");
  assert.equal(subjectWords({ kind: "model_version", id: M1 }, "business"), "About the model");
  assert.equal(subjectWords({ kind: "__proto__", id: M1 }, "business"), null);
  assert.equal(questionRow(question({ subject: { kind: "batch_prediction", id: L1 } }), "business").subject, "About the latest list at the time of asking");
});

test("the reply box follows the side the API says the reply is written as (you_reply_as)", () => {
  assert.equal(replySide("business"), "business");
  assert.equal(replySide("data_team"), "data_team");
  for (const odd of ["__proto__", "admin", null, undefined, 1]) assert.equal(replySide(odd), null);
  assert.equal(questionRow(question({ you_reply_as: "data_team" })).youReplyAs, "data_team");
  assert.equal(REPLY_WORDS.data_team.label, "Follow-up");
  assert.equal(REPLY_WORDS.business.label, "Your reply");
  // No side (a viewer, platform staff, an older read): no reply box even if can_reply says yes.
  const none = questionRow(question({ you_reply_as: null }));
  assert.equal(none.canReply, false);
  assert.equal(none.youReplyAs, null);
  assert.equal(questionRow(question({ you_reply_as: "constructor" })).canReply, false);
  assert.equal(questionRow(question({ status: "closed", you_reply_as: "business" })).youReplyAs, null);
});

test("people are you, a business user or a person on the data team; never an id, a name or an email", () => {
  assert.equal(personLabel({ is_you: true, side: "business" }), "You");
  assert.equal(personLabel({ side: "business" }), "A business user");
  assert.equal(personLabel({ is_you: false, side: "data_team" }), "A person on the data team");
  assert.equal(personLabel({ side: "someone@example.com" }), "A person");
  assert.equal(personLabel(null), "A person");
  const replies = replyRows([
    { id: R1, text: "Yes\n\nfor now", author: { is_you: false, side: "business" }, created_at: "2026-10-17T10:00:00Z" },
    { id: "bad", text: "dropped", author: { side: "business" }, created_at: "2026-10-17T10:00:00Z" },
  ]);
  assert.deepEqual(replies, [{ id: R1, text: "Yes\n\nfor now", author: "A business user", side: "business", at: "2026-10-17T10:00:00Z" }]);
});

test("untrusted text: plain text, line breaks kept, controls and bidi/zero-width characters removed, capped", () => {
  assert.equal(questionText("a\r\nb\rc"), "a\nb\nc");
  assert.equal(questionText("<script>alert(1)</script>"), "<script>alert(1)</script>"); // shown as text, never parsed
  assert.equal(questionText("ok\u202eevil\u200b\u0007\u2028x\ufeff"), "okevilx");
  assert.equal(questionText("family \u{1F468}\u200d\u{1F469}"), "family \u{1F468}\u200d\u{1F469}");
  assert.equal(questionText("a\n\n\n\n\nb"), "a\n\n\n\n\nb"); // blank lines kept as written
  assert.equal(questionText(null), "");
  const long = questionText("x".repeat(QUESTION_TEXT_MAX + 50));
  assert.equal([...long].length, QUESTION_TEXT_MAX);
  assert.ok(long.endsWith("…"));
});

test("length and character checks match the server (2000 characters, code points, trimmed)", () => {
  assert.deepEqual(checkQuestionText("   "), { ok: false, text: "", used: 0, problem: null });
  assert.equal(checkQuestionText("  Is this right?\r\n").text, "Is this right?");
  assert.equal(checkQuestionText("x".repeat(QUESTION_TEXT_MAX)).ok, true);
  const over = checkQuestionText("x".repeat(QUESTION_TEXT_MAX + 1));
  assert.equal(over.ok, false);
  assert.match(over.problem ?? "", /2,000 characters or fewer/);
  // Emoji count as one character each (code points), like the server.
  assert.equal(checkQuestionText("\u{1F600}".repeat(QUESTION_TEXT_MAX)).ok, true);
  assert.equal(checkQuestionText("tab\tand\nnew line").ok, true);
  for (const bad of ["bell\u0007", "rtl\u202eoverride", "zero\u200bwidth", "bom\ufeff", "c1\u0085x", "\ufeffleading bom"]) {
    const check = checkQuestionText(bad);
    assert.equal(check.ok, false, bad);
    assert.match(check.problem ?? "", /control characters/);
  }
  assert.equal(checkQuestionText("joiner \u{1F468}\u200d\u{1F469}").ok, true);
  // Like Python's strip(): U+001C-U+001F and U+0085 at the ends are trimmed, not refused.
  assert.deepEqual([checkQuestionText("\u001cedge\u0085").ok, checkQuestionText("\u001cedge\u0085").text], [true, "edge"]);
  assert.equal(SHARED_WORDS.limitNote(1234), "1,234 of 2,000 characters");
});

test("error sentences are fixed by code, then by status; the server's message is never used", () => {
  assert.equal(questionErrorSentence(apiError(403, "question_ask_not_permitted")), "Only members of this workspace's data team can ask the business.");
  // A 2xx whose body this app could not read: the write happened.
  assert.equal(questionErrorSentence({ status: 201, body: {} }), SENT_UNREADABLE);
  assert.equal(SENT_UNREADABLE, "Sent, but the page could not show it. Reload the page.");
  assert.ok(sentButUnreadable({ status: 200 }) && !sentButUnreadable({ status: 409 }) && !sentButUnreadable(null));
  assert.ok(staleAfterClose(apiError(412)) && staleAfterClose(apiError(409, "question_closed")) && !staleAfterClose(apiError(403)));
  assert.equal(questionErrorSentence(apiError(403, "QUESTION_REPLY_NOT_PERMITTED")), "Your role cannot reply to this question.");
  assert.match(questionErrorSentence(apiError(403, "question_close_not_permitted")), /person who asked, or an owner or administrator/);
  assert.match(questionErrorSentence(apiError(409, "question_closed")), /closed, so it takes no more replies/);
  assert.match(questionErrorSentence(apiError(409, "question_reply_limit")), /100 replies/);
  assert.match(questionErrorSentence(apiError(409, "too_many_open_questions")), /100 questions that are not closed/);
  assert.match(questionErrorSentence(apiError(422, "question_text_invalid")), /between 1 and 2,000 characters/);
  assert.match(questionErrorSentence(apiError(422, "question_subject_not_found")), /linked item/);
  assert.match(questionErrorSentence(apiError(403, "session_required")), /signed-in session/);
  assert.match(questionErrorSentence(apiError(412, "precondition_failed")), /changed while you were looking/);
  assert.match(questionErrorSentence(apiError(412)), /changed while you were looking/);
  assert.match(questionErrorSentence(apiError(428, "precondition_required")), /out of date/);
  assert.match(questionErrorSentence(apiError(404, "not_found")), /not in your workspace/);
  assert.equal(questionErrorSentence(apiError(403, "__proto__")), "You cannot do this here.");
  assert.equal(questionErrorSentence(apiError(500, "constructor")), GENERIC_WRITE_ERROR);
  assert.equal(questionErrorSentence(new Error("network <img>")), GENERIC_WRITE_ERROR);
  assert.equal(questionErrorSentence(null), GENERIC_WRITE_ERROR);
  for (const status of [400, 401, 403, 404, 409, 412, 422, 428, 429, 500]) assert.ok(!questionErrorSentence(apiError(status, "x", "LEAK")).includes("LEAK"));
});

test("hrefs: UUIDs only, internal only", () => {
  assert.equal(studioQuestionHref(P1, Q1), `/projects/${P1}/questions?question=${Q1}`);
  assert.equal(studioQuestionHref(P1, "javascript:alert(1)"), `/projects/${P1}/questions`);
  assert.equal(studioQuestionHref("//evil.example", Q1), null);
  assert.equal(studioQuestionHref("__proto__", Q1), null);
  assert.equal(subjectHref(P1, { kind: "model_version", id: M1 }), `/projects/${P1}/models/${M1}`);
  assert.equal(subjectHref(P1, { kind: "batch_prediction", id: L1 }), `/projects/${P1}/predictions`);
  assert.equal(subjectHref(P1, { kind: "monitoring_window", id: L1 }), `/projects/${P1}/monitoring`);
  assert.equal(subjectHref(P1, { kind: "model_version", id: "../../admin" }), null);
  assert.equal(subjectHref(P1, { kind: "__proto__", id: M1 }), null);
  assert.equal(subjectHref("not-a-uuid", { kind: "model_version", id: M1 }), null);
  assert.equal(BUSINESS_QUESTIONS_HREF, "/outcomes#questions");
});

test("link choices come from reads that exist; only an offered choice is sent", () => {
  const choices = linkChoices({
    refs: [{ ref_kind: "dataset", target: { id: M1 } }, { ref_kind: "champion_model", target: { kind: "model_version", id: M1 } }],
    latestList: { id: L1, label: "scored 7 Oct 2026" },
  });
  assert.deepEqual(choices.map((c) => c.label), ["Nothing specific", "The model in use", "The latest prediction list (scored 7 Oct 2026)"]);
  assert.deepEqual(chosenSubject(choices, `model_version:${M1}`), { kind: "model_version", id: M1 });
  assert.deepEqual(chosenSubject(choices, `batch_prediction:${L1}`), { kind: "batch_prediction", id: L1 });
  assert.equal(chosenSubject(choices, ""), null);
  assert.equal(chosenSubject(choices, `model_version:${Q1}`), null);
  assert.equal(chosenSubject(choices, "__proto__"), null);
  assert.deepEqual(linkChoices({ refs: [{ ref_kind: "champion_model", target: { id: "bad" } }], latestList: { id: "bad" } }).map((c) => c.value), [""]);
  assert.deepEqual(linkChoices({}).map((c) => c.value), [""]);
});

test("Inbox cards for question_asked / question_answered: fixed words and safe hrefs", () => {
  const asked = { kind: "question_asked", project_id: P1, source: { kind: "business_question", id: Q1 }, subject: { kind: "project", id: P1 }, summary: "x", status: "open" };
  const answered = { ...asked, kind: "question_answered", status: "answered" };
  assert.equal(inboxTitle(asked), "A question from the data team");
  assert.equal(inboxTitle(answered), "A business user replied to your question");
  assert.equal(outcomeWords("open"), "waiting for a reply");
  assert.equal(outcomeWords("answered"), "answered");
  assert.match(businessQuestionSentence(asked) ?? "", /asked the business users/);
  assert.match(businessQuestionSentence(answered) ?? "", /replied to a question you asked/);
  assert.equal(businessQuestionSentence({ kind: "drift_alert" }), null);
  assert.equal(businessQuestionBy("question_asked"), "Asked by a person on the data team");
  assert.equal(businessQuestionBy("question_answered"), "Replied by a business user");
  assert.equal(businessQuestionHref(asked), "/outcomes#questions");
  assert.equal(businessQuestionHref(answered), `/projects/${P1}/questions?question=${Q1}`);
  assert.equal(businessQuestionHref({ ...answered, source: { kind: "experiment", id: Q1 } }), null);
  assert.equal(businessQuestionHref({ ...answered, source: { kind: "business_question", id: "x/../../admin" } }), null);
  assert.equal(businessQuestionHref({ ...answered, project_id: "//evil.example" }), null);
  assert.equal(businessQuestionHref({ ...answered, kind: "__proto__" }), null);
  assert.equal(businessQuestionHref({ ...answered, source: null }), null);
});

test("Home and History: asked, replied and closed in fixed words, linked to the question", () => {
  const base = { id: `question_asked:${Q1}`, project_id: P1, occurred_at: "2026-10-17T09:00:00Z", actor: { kind: "person", is_you: true }, subject: { kind: "project", id: P1 }, link: { kind: "business_question", id: Q1 } };
  assert.equal(activitySentence({ ...base, kind: "question_asked", summary: "Question asked to the business" }, null), "Asked the business a question");
  // The reply's side is the item's status; the summary is never read.
  assert.equal(activitySentence({ ...base, kind: "question_replied", status: "business", summary: "x" }, null), "A business user replied to a question");
  assert.equal(activitySentence({ ...base, kind: "question_replied", status: "data_team", summary: "A business user replied" }, null), "Added a follow-up to a question to the business");
  for (const odd of [null, "__proto__", "someone"]) assert.equal(activitySentence({ ...base, kind: "question_replied", status: odd, summary: "A business user replied" }, null), "A reply was added to a question to the business");
  assert.equal(activitySentence({ ...base, kind: "question_closed", summary: "<b>x</b>" }, null), "Closed a question to the business");
  assert.equal(activityHref({ ...base, kind: "question_asked" }), `/projects/${P1}/questions?question=${Q1}`);
  assert.equal(activityHref({ ...base, kind: "question_asked", link: { kind: "business_question", id: "bad" } }), null);
  assert.equal(questionEventWords("drift_alert", null), null);
  for (const kind of ["question_asked", "question_replied", "question_closed"]) assert.ok(LOG_EVENT_KINDS.has(kind));
  const row = eventRow({ ...base, kind: "question_closed" }, P1, new Map());
  assert.equal(row?.what, "Closed a question to the business");
  assert.equal(row?.who, "You");
  assert.equal(row?.href, `/projects/${P1}/questions?question=${Q1}`);
  assert.match(row?.undo ?? "", /final/);
  assert.equal(eventRow({ ...base, kind: "question_replied", link: { kind: "experiment", id: Q1 } }, P1, new Map())?.href, null);
});

/** Visible text in the new components (JSX text and string props). */
function componentStrings(file: string): string[] {
  const source = readFileSync(new URL(file, import.meta.url), "utf8");
  return [...source.matchAll(/>([^<>{}]*[A-Za-z][^<>{}]*)</g)].map((m) => m[1].trim()).filter(Boolean);
}

test("words: business-view rules for the Business view, no AI wording anywhere, no innerHTML", () => {
  const business = [...Object.values(BUSINESS_WORDS), ...Object.values(REPLY_WORDS).flatMap((w) => Object.values(w)), ...Object.values(ERROR_BY_CODE),
    GENERIC_WRITE_ERROR, SENT_UNREADABLE, closedSentence("A business user"), SHARED_WORDS.showReplies, SHARED_WORDS.hideReplies, SHARED_WORDS.noReplies, SHARED_WORDS.repliesFailed, SHARED_WORDS.limitNote(10),
    ...componentStrings("../../app/components/business-view/BusinessQuestions.tsx"), ...componentStrings("../../app/components/business-view/QuestionThread.tsx")];
  for (const sentence of business) assert.ok(!BANNED.test(sentence), `business word rule: ${sentence}`);
  const studio = [...Object.values(STUDIO_WORDS), ...Object.values(CLOSE_WORDS)];
  assert.match(STUDIO_WORDS.guideYouGet, /for questions you asked, in your Inbox/);
  assert.equal(STUDIO_WORDS.cannotAsk.startsWith("Only members of this workspace's data team can ask the business."), true);
  for (const sentence of [...business, ...studio]) assert.ok(!AI_WORDS.test(sentence), `AI wording: ${sentence}`);
  for (const file of ["../../app/components/business-view/BusinessQuestions.tsx", "../../app/components/business-view/QuestionThread.tsx", "../../app/(studio)/projects/[id]/questions/page.tsx"]) {
    const source = readFileSync(new URL(file, import.meta.url), "utf8");
    // Built from parts so this test file itself never contains an HTML sink name (backend source scan).
    assert.ok(!new RegExp(["dangerously", "Set", "Inner", "HTML|", "inner", "HTML"].join("")).test(source), file);
    assert.ok(!/\.email\b|user_id/.test(source), file);
  }
});
