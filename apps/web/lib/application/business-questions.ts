/**
 * Questions to the business (D7b): the data team asks the business users of the workspace; they reply. Pure: no React,
 * no fetch, so it runs under `npm run test:components`. Person to person only.
 *
 * Question and reply text is untrusted: it is cleaned here and only ever rendered as a text node (line breaks kept with
 * CSS, never markup). People are "you", "a business user" or "a person on the data team", never an id, name or email.
 * Error sentences are fixed per error code; the server's own message is never shown. Tables are read with own keys only.
 */
import { safeInternalHref } from "../../components/studio/safe-href.ts";
import { isUuid, plainText, projectHref } from "./command-search.ts";

/** Same limit as the server (characters, counted as code points like the server does). */
export const QUESTION_TEXT_MAX = 2000;

const own = <T>(table: Readonly<Record<string, T>>, key: unknown): T | undefined =>
  typeof key === "string" && Object.hasOwn(table, key) ? table[key] : undefined;

/** Words of the Business view section. A test checks them with the business-view word rules. */
export const BUSINESS_WORDS = {
  title: "Questions from the data team",
  intro: "Your data team asks about their projects here. Your reply goes to them and cannot be changed after it is sent.",
  loading: "Loading the questions…",
  failed: "The questions could not be loaded. Try again in a moment.",
  forbidden: "Questions are not open to your account in this workspace.",
  empty: "No questions from the data team yet. When they ask you something, it shows here and you can reply.",
  replyLabel: "Your reply",
  send: "Send the reply",
  sending: "Sending…",
  sent: "Your reply was sent to the data team.",
  answered: "A business user replied. The data team can follow up here.",
  more: "Only the first 100 are shown: open questions first, then the most recently changed.",
} as const;

/** Words of the Studio "Ask the business" page. */
export const STUDIO_WORDS = {
  title: "Ask the business",
  subtitle: "Ask the business users of this workspace a question about this project. They reply in the Business view.",
  guidePurpose: "A place to ask the people who use the results, and to keep their answers next to the project.",
  guideHowTo: "Write one clear question, link it to the model in use or a prediction list if it is about one, and send it. Close it when it is settled.",
  guideYouGet: "Their replies in this list and, for questions you asked, in your Inbox. Replies are kept as they were written; nothing is edited.",
  composeTitle: "New question",
  askLabel: "Your question",
  linkLabel: "About (optional)",
  noLink: "Nothing specific",
  modelInUse: "The model in use",
  latestList: "The latest prediction list",
  ask: "Send the question",
  asking: "Sending…",
  asked: "Sent. The business users of this workspace see it in the Business view.",
  cannotAsk: "Only members of this workspace's data team can ask the business. You can read the questions and replies.",
  listTitle: "Questions and replies",
  loading: "Loading the questions…",
  failed: "The questions could not be loaded. Try again in a moment.",
  forbidden: "You cannot see the questions of this project.",
  notFound: "This project was not found in your workspace.",
  empty: "No questions to the business yet. Ask one above; the business users see it in the Business view.",
  answered: "A business user replied. Add a follow-up, or close the question when it is settled.",
  more: "Only the first 100 are shown: open questions first, then the most recently changed.",
} as const;

/** Words of the reply box, by the side the API says the reply would be written as (`you_reply_as`). */
export const REPLY_WORDS = {
  business: { label: "Your reply", send: "Send the reply", sending: "Sending…", sent: "Your reply was sent to the data team." },
  data_team: { label: "Follow-up", send: "Send the follow-up", sending: "Sending…", sent: "Your follow-up was added." },
} as const;
export type ReplySide = keyof typeof REPLY_WORDS;
export function replySide(value: unknown): ReplySide | null {
  return value === "business" || value === "data_team" ? value : null;
}

/** Closing (Studio): a confirm step, then the result is announced. */
export const CLOSE_WORDS = {
  close: "Close the question",
  confirm: "Close this question? It will take no more replies, from anyone.",
  yes: "Yes, close it",
  keep: "Keep it open",
  closing: "Closing…",
  done: "Closed.",
} as const;

export const SHARED_WORDS = {
  showReplies: "Show the conversation",
  hideReplies: "Hide the conversation",
  noReplies: "No replies yet.",
  repliesFailed: "The replies could not be loaded. Try again in a moment.",
  repliesLoading: "Loading the replies…",
  asked: "Asked",
  limitNote: (used: number) => `${used.toLocaleString("en-US")} of ${QUESTION_TEXT_MAX.toLocaleString("en-US")} characters`,
} as const;

/* ---------- text ---------- */

// Controls other than tab and line feed, C1 controls, line/paragraph separators and format characters
// (bidi overrides, zero-width, BOM, tag characters); the emoji joiner U+200D is kept. Built from escapes.
const UNSAFE_DISPLAY = new RegExp("(?!\\u200d)[\\u0000-\\u0008\\u000b-\\u001f\\u007f-\\u009f\\u2028\\u2029\\p{Cf}]", "gu");

/** Untrusted question or reply text for display: plain text, line breaks kept (rendered with `white-space: pre-wrap`), capped. */
export function questionText(value: unknown, max = QUESTION_TEXT_MAX): string {
  // Blank lines are kept as written.
  const text = String(value ?? "").replace(/\r\n?/g, "\n").replace(UNSAFE_DISPLAY, "").trim();
  const chars = [...text];
  return chars.length > max ? `${chars.slice(0, max - 1).join("")}…` : text;
}

const EDGE_SPACE = new RegExp("^[\\s\\u001c-\\u001f\\u0085]+|[\\s\\u001c-\\u001f\\u0085]+$", "g");
const REFUSED = new RegExp("(?![\\u200d\\n\\t])[\\p{Cc}\\p{Cf}\\p{Cs}]", "u");

export type TextCheck = { ok: boolean; text: string; used: number; problem: string | null };

/**
 * What the person typed, checked like the server checks it: line endings to LF, outer whitespace trimmed, 1 to 2,000
 * characters, no control or format characters. `text` is what is sent.
 */
export function checkQuestionText(raw: string): TextCheck {
  const normalized = String(raw ?? "").replace(/\r\n?/g, "\n");
  // Python's strip() also removes U+001C-U+001F and U+0085 at the ends (they are whitespace there), so do the same.
  const text = normalized.trim().replace(EDGE_SPACE, "");
  const used = [...text].length;
  if (used === 0) return { ok: false, text, used, problem: null };
  if (used > QUESTION_TEXT_MAX) return { ok: false, text, used, problem: `Shorten it to ${QUESTION_TEXT_MAX.toLocaleString("en-US")} characters or fewer.` };
  // The server does not trim a byte order mark (JavaScript's trim does), so one anywhere is refused here too.
  if (REFUSED.test(text) || normalized.includes("\ufeff")) return { ok: false, text, used, problem: "Remove the hidden control characters (they often come with pasted text)." };
  return { ok: true, text, used, problem: null };
}

/* ---------- people, status, subject ---------- */

export type PersonLike = { is_you?: boolean | null; side: string };

/** "You", "A business user" or "A person on the data team"; never an id, a name or an email. */
export function personLabel(person: PersonLike | null | undefined): string {
  if (!person) return "A person";
  if (person.is_you === true) return "You";
  return person.side === "business" ? "A business user" : person.side === "data_team" ? "A person on the data team" : "A person";
}

/** "Closed by you. It takes no more replies." (or by a business user / a person on the data team; plain "Closed." when unknown). */
export function closedSentence(label: string | null | undefined): string {
  const by = !label ? "" : label === "You" ? " by you" : ` by ${label.charAt(0).toLowerCase()}${label.slice(1)}`;
  return `Closed${by}. It takes no more replies.`;
}

type Tone = "ok" | "warn" | "gray";
const STATUS: Readonly<Record<string, { label: string; tone: Tone; rank: number }>> = {
  open: { label: "Waiting for a reply", tone: "warn", rank: 0 },
  answered: { label: "Answered", tone: "ok", rank: 1 },
  closed: { label: "Closed", tone: "gray", rank: 2 },
};
export function questionStatus(status: unknown): { label: string; tone: Tone; rank: number } {
  return own(STATUS, status) ?? { label: "State not known", tone: "gray", rank: 3 };
}

export type SubjectLike = { kind: string; id: string };
export type Audience = "studio" | "business";
const SUBJECT_WORDS: Readonly<Record<Audience, Readonly<Record<string, string>>>> = {
  studio: { model_version: "About a model", batch_prediction: "About a prediction list", monitoring_window: "About a monitoring week" },
  // Business view words: no technical names. A question can only be linked to the latest list when it is asked.
  business: { model_version: "About the model", batch_prediction: "About the latest list at the time of asking", monitoring_window: "About one week's checks" },
};
export function subjectWords(subject: SubjectLike | null | undefined, audience: Audience = "studio"): string | null {
  return subject ? own(SUBJECT_WORDS[audience], subject.kind) ?? null : null;
}

/** Studio only: where the linked item lives in the project (UUIDs only). */
export function subjectHref(projectId: unknown, subject: SubjectLike | null | undefined): string | null {
  if (!subject || !isUuid(subject.id)) return null;
  if (subject.kind === "model_version") return projectHref(projectId, "models", subject.id);
  if (subject.kind === "batch_prediction") return projectHref(projectId, "predictions");
  if (subject.kind === "monitoring_window") return projectHref(projectId, "monitoring");
  return null;
}

/** The Studio page of one question (`/projects/{id}/questions?question={id}`), UUIDs only. */
export function studioQuestionHref(projectId: unknown, questionId: unknown): string | null {
  const base = projectHref(projectId, "questions");
  if (!base) return null;
  return isUuid(questionId) ? safeInternalHref(`${base}?question=${questionId}`) : base;
}

/** The Business view's questions section. */
export const BUSINESS_QUESTIONS_HREF = "/outcomes#questions";

const REPLIED_WORDS: Readonly<Record<string, string>> = {
  business: "A business user replied to a question", data_team: "Added a follow-up to a question to the business",
};
/**
 * D7b: questions to the business in fixed words (no text, no names). A reply's side is the activity item's `status`
 * (`business` | `data_team`); anything else gets a neutral sentence. The server's summary is never read.
 */
export function questionEventWords(kind: string, status: string | null | undefined): string | null {
  if (kind === "question_asked") return "Asked the business a question";
  if (kind === "question_closed") return "Closed a question to the business";
  if (kind === "question_replied") return own(REPLIED_WORDS, status) ?? "A reply was added to a question to the business";
  return null;
}

/* ---------- list ---------- */

export type ReplyLike = { id: string; text: string; author: PersonLike; created_at: string };
export type QuestionLike = {
  id: string; project_id: string; project_name?: string | null; you_reply_as?: string | null; subject?: SubjectLike | null; text: string; status: string; asked_by: PersonLike;
  reply_count: number; last_reply_at?: string | null; created_at: string; closed_at?: string | null; closed_by?: PersonLike | null;
  version: number; can_reply: boolean; can_close: boolean;
};
export type QuestionRow = {
  id: string; projectId: string; projectName: string | null; youReplyAs: ReplySide | null; text: string; status: string; statusLabel: string; tone: Tone; askedBy: string;
  createdAt: string; replyCount: number; lastReplyAt: string | null; subject: string | null; subjectHref: string | null;
  closedBy: string | null; version: number; canReply: boolean; canClose: boolean;
};

export function questionRow(q: QuestionLike, audience: Audience = "studio"): QuestionRow {
  const status = questionStatus(q.status);
  const canReply = q.can_reply === true && q.status !== "closed";
  return {
    id: q.id, projectId: q.project_id, projectName: plainText(q.project_name ?? "", 80) || null,
    youReplyAs: canReply ? replySide(q.you_reply_as) : null, text: questionText(q.text), status: q.status, statusLabel: status.label, tone: status.tone,
    askedBy: personLabel(q.asked_by), createdAt: q.created_at,
    replyCount: Number.isInteger(q.reply_count) && q.reply_count > 0 ? q.reply_count : 0,
    lastReplyAt: q.last_reply_at ?? null, subject: subjectWords(q.subject, audience), subjectHref: subjectHref(q.project_id, q.subject),
    closedBy: q.closed_by ? personLabel(q.closed_by) : null,
    version: q.version,
    // A closed question takes no replies and cannot be closed again, whatever the flags say.
    canReply: canReply && replySide(q.you_reply_as) !== null,
    canClose: q.can_close === true && q.status !== "closed",
  };
}

/** Rows to show: UUID ids only, open first, then answered, then closed (the server's order kept within each). */
export function questionRows(items: readonly QuestionLike[] | null | undefined, audience: Audience = "studio"): QuestionRow[] {
  return (items ?? [])
    .filter((q) => isUuid(q.id) && isUuid(q.project_id))
    .map((q, index) => ({ row: questionRow(q, audience), index }))
    .sort((a, b) => questionStatus(a.row.status).rank - questionStatus(b.row.status).rank || a.index - b.index)
    .map(({ row }) => row);
}

export function replyRows(replies: readonly ReplyLike[] | null | undefined): Array<{ id: string; text: string; author: string; side: string; at: string }> {
  return (replies ?? []).filter((r) => isUuid(r.id)).map((r) => ({
    id: r.id, text: questionText(r.text), author: personLabel(r.author), side: r.author?.side === "business" ? "business" : "data_team", at: r.created_at,
  }));
}

export type ListWords = { loading: string; failed: string; forbidden: string; empty: string; more: string; notFound?: string };
export type ListView =
  | { kind: "loading"; message: string }
  | { kind: "forbidden"; message: string }
  | { kind: "error"; message: string }
  | { kind: "empty"; message: string }
  | { kind: "ready"; rows: QuestionRow[]; more: string | null };

/** Loading, forbidden (403), not found (404, with words for it), failed, empty or the rows. */
export function questionListView(
  input: { pending: boolean; errorStatus: number | null; data: { items: readonly QuestionLike[]; has_more: boolean } | undefined },
  words: ListWords,
  audience: Audience = "studio",
): ListView {
  if (input.errorStatus !== null) {
    if (input.errorStatus === 403) return { kind: "forbidden", message: words.forbidden };
    if (input.errorStatus === 404 && words.notFound) return { kind: "error", message: words.notFound };
    return { kind: "error", message: words.failed };
  }
  if (input.pending || !input.data) return { kind: "loading", message: words.loading };
  const rows = questionRows(input.data.items, audience);
  if (rows.length === 0) return { kind: "empty", message: words.empty };
  return { kind: "ready", rows, more: input.data.has_more ? words.more : null };
}

/* ---------- link choices for a new question ---------- */

export type LinkChoice = { value: string; label: string; subject: SubjectLike | null };

/**
 * What a new question may be linked to, from reads that exist: the model in use (the project's champion ref) and the
 * latest prediction list (the Business view read). Monitoring weeks carry no id in any read, so they are not offered.
 */
export function linkChoices(input: {
  refs?: ReadonlyArray<{ ref_kind: string; target: { kind?: string; id: string } }> | null;
  latestList?: { id: string; label?: string | null } | null;
}): LinkChoice[] {
  const choices: LinkChoice[] = [{ value: "", label: STUDIO_WORDS.noLink, subject: null }];
  const champion = (input.refs ?? []).find((ref) => ref.ref_kind === "champion_model");
  if (champion && isUuid(champion.target.id)) {
    choices.push({ value: `model_version:${champion.target.id}`, label: STUDIO_WORDS.modelInUse, subject: { kind: "model_version", id: champion.target.id } });
  }
  const list = input.latestList;
  if (list && isUuid(list.id)) {
    const label = list.label ? `${STUDIO_WORDS.latestList} (${list.label})` : STUDIO_WORDS.latestList;
    choices.push({ value: `batch_prediction:${list.id}`, label, subject: { kind: "batch_prediction", id: list.id } });
  }
  return choices;
}

/** The chosen link's subject, only when it is one of the offered choices. */
export function chosenSubject(choices: readonly LinkChoice[], value: string): SubjectLike | null {
  return choices.find((choice) => choice.value === value)?.subject ?? null;
}

/* ---------- errors ---------- */

export const ERROR_BY_CODE: Readonly<Record<string, string>> = {
  question_text_invalid: `Write between 1 and ${QUESTION_TEXT_MAX.toLocaleString("en-US")} characters, without hidden control characters.`,
  question_subject_not_found: "The linked item is not part of this project any more. Choose another link, or none.",
  too_many_open_questions: "This project has 100 questions that are not closed. Close some before asking more.",
  question_ask_not_permitted: "Only members of this workspace's data team can ask the business.",
  question_reply_not_permitted: "Your role cannot reply to this question.",
  question_close_not_permitted: "Only the person who asked, or an owner or administrator of the workspace, can close this question.",
  question_closed: "This question is closed, so it takes no more replies.",
  question_reply_limit: "This question has 100 replies, the most it can take. Ask a new question instead.",
  session_required: "This needs a signed-in session. Sign in again and retry.",
  not_found: "This question is not in your workspace any more. Reload the page.",
};
const ERROR_BY_STATUS: Readonly<Record<string, string>> = {
  "401": "Your session ended. Sign in again and retry.",
  "403": "You cannot do this here.",
  "404": "This question is not in your workspace any more. Reload the page.",
  "409": "This could not be saved because it changed in the meantime. Reload the page and try again.",
  "412": "This question changed while you were looking. Reload it and try again.",
  "422": "The text could not be sent. Check it and try again.",
  "428": "The page is out of date. Reload it and try again.",
  "429": "Too many requests in a short time. Wait a moment and try again.",
};
export const GENERIC_WRITE_ERROR = "It could not be sent. Try again in a moment.";
/** The write succeeded (2xx) but its response could not be read. */
export const SENT_UNREADABLE = "Sent, but the page could not show it. Reload the page.";

/** A 2xx whose body could not be read: the write happened. */
export function sentButUnreadable(error: unknown): boolean {
  const status = (error as { status?: unknown } | null)?.status;
  return typeof status === "number" && status >= 200 && status < 300;
}
/** A failed close that means the page is stale (re-read the question and the list). */
export function staleAfterClose(error: unknown): boolean {
  const status = (error as { status?: unknown } | null)?.status;
  return status === 409 || status === 412;
}

/** A fixed sentence for a failed write, by the API's error code, then by HTTP status. Never the server's own words. */
export function questionErrorSentence(error: unknown): string {
  const status = (error as { status?: unknown } | null)?.status;
  const body = (error as { body?: unknown } | null)?.body as { error?: { code?: unknown } } | null | undefined;
  if (sentButUnreadable(error)) return SENT_UNREADABLE;
  const code = typeof body?.error?.code === "string" ? body.error.code.toLowerCase() : "";
  const byCode = own(ERROR_BY_CODE, code);
  if (byCode && !(code === "not_found" && status !== 404)) return byCode;
  if (status === 412 || code === "precondition_failed") return ERROR_BY_STATUS["412"];
  return (typeof status === "number" ? own(ERROR_BY_STATUS, String(status)) : undefined) ?? GENERIC_WRITE_ERROR;
}
