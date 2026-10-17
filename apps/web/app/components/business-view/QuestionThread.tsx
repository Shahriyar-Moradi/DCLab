"use client";

/**
 * One question to the business with its conversation (D7b), used by the Business view and by Studio's "Ask the business".
 * Question and reply text is untrusted: rendered only as text nodes (line breaks kept with CSS), never as markup.
 * The API decides who may reply or close (`can_reply` / `you_reply_as` / `can_close` only shape the controls). Writes are
 * single-flight with one Idempotency-Key per action, kept per question (so a retry after a timeout replays); closing has a
 * confirm step and sends `If-Match` with the newest version this page has read. Focus stays put while sending (the text
 * box is read-only, not disabled); after closing it moves to the question's heading and "Closed." is announced.
 */
import Link from "next/link";
import { useEffect, useRef, useState, type RefObject } from "react";
import { Pill } from "@/components/studio/Pill";
import {
  CLOSE_WORDS, REPLY_WORDS, SHARED_WORDS, checkQuestionText, closedSentence, questionErrorSentence, questionRow, replyRows, sentButUnreadable,
  staleAfterClose, type Audience, type QuestionRow, type ReplySide,
} from "@/lib/application/business-questions";
import { closeQuestion, replyToQuestion, useQuestion, useQuestionWrites } from "@/lib/application/business-questions-hooks";
import { utcWhen } from "@/lib/application/format-time";
import { ActionKeys } from "@/lib/application/studio-wizard";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

export const TEXT_STYLE = { whiteSpace: "pre-wrap", overflowWrap: "anywhere", margin: 0 } as const;

export type ThreadWords = { answered?: string };

/**
 * The text box. The count is outside the label and linked with `aria-describedby` (not announced on every key); the
 * problem with the text, if any, has its own polite live region whose content changes only when the problem does.
 */
export function QuestionTextField({ id, label, value, onChange, busy, inputRef }: {
  id: string; label: string; value: string; onChange: (v: string) => void; busy: boolean; inputRef?: RefObject<HTMLTextAreaElement>;
}) {
  const check = checkQuestionText(value);
  return (
    <div className="field">
      <label htmlFor={id}><span>{label}</span></label>
      <textarea
        id={id} ref={inputRef} rows={3} value={value} readOnly={busy} aria-busy={busy || undefined}
        aria-invalid={check.problem ? true : undefined} aria-describedby={`${id}-count ${id}-problem`}
        onChange={(event) => onChange(event.target.value)}
      />
      <small id={`${id}-count`} className="muted">{SHARED_WORDS.limitNote(check.used)}</small>
      <small id={`${id}-problem`} role="status" aria-live="polite">{check.problem ?? ""}</small>
    </div>
  );
}

function ReplyForm({ questionId, side, keys }: { questionId: string; side: ReplySide; keys: ActionKeys }) {
  const words = REPLY_WORDS[side];
  const flight = useRef(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const written = useQuestionWrites();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "done" | "error"; text: string } | null>(null);
  const check = checkQuestionText(text);
  const submit = async () => {
    if (flight.current || !check.ok) return;
    flight.current = true;
    setBusy(true);
    setMessage(null);
    try {
      const detail = await replyToQuestion({ questionId, text: check.text, key: keys.keyFor("reply", check.text) });
      keys.done("reply");
      setText("");
      setMessage({ kind: "done", text: words.sent });
      written(detail);
    } catch (caught) {
      if (sentButUnreadable(caught)) {
        keys.done("reply");
        setText("");
        written(null, questionId);
      }
      setMessage({ kind: "error", text: questionErrorSentence(caught) });
    } finally {
      flight.current = false;
      setBusy(false);
      box.current?.focus();
    }
  };
  return (
    <form className="form" aria-label={words.label} onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <QuestionTextField id={`reply-${questionId}`} label={words.label} value={text} onChange={(value) => { setText(value); setMessage(null); }} busy={busy} inputRef={box} />
      <div className="toolbar">
        <button type="submit" className="btn primary" aria-disabled={busy || !check.ok} onClick={(event) => { if (busy || !check.ok) event.preventDefault(); }}>
          {busy ? words.sending : words.send}
        </button>
      </div>
      <p role="status">{message?.kind === "done" ? message.text : ""}</p>
      {message?.kind === "error" ? <p role="alert">{message.text}</p> : null}
    </form>
  );
}

function CloseControl({ questionId, version, keys, onClosed }: { questionId: string; version: number; keys: ActionKeys; onClosed: () => void }) {
  const flight = useRef(false);
  const written = useQuestionWrites();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    if (flight.current) return;
    flight.current = true;
    setBusy(true);
    setError(null);
    try {
      written(await closeQuestion({ questionId, version, key: keys.keyFor("close", String(version)) }));
      keys.done("close");
      onClosed();
    } catch (caught) {
      // A stale page (409 / 412) or a close whose answer could not be read: re-read the question and the list.
      if (staleAfterClose(caught) || sentButUnreadable(caught)) written(null, questionId);
      setError(questionErrorSentence(caught));
    } finally {
      flight.current = false;
      setBusy(false);
      setConfirming(false);
    }
  };
  if (!confirming) {
    return (
      <>
        <button type="button" className="btn" onClick={() => { setError(null); setConfirming(true); }}>{CLOSE_WORDS.close}</button>
        {error ? <p role="alert">{error}</p> : null}
      </>
    );
  }
  return (
    <div role="group" aria-label={CLOSE_WORDS.close} style={{ display: "grid", gap: 8 }}>
      <p>{CLOSE_WORDS.confirm}</p>
      <div className="toolbar">
        <button type="button" className="btn primary" aria-disabled={busy} onClick={() => void run()}>{busy ? CLOSE_WORDS.closing : CLOSE_WORDS.yes}</button>
        <button type="button" className="btn" aria-disabled={busy} onClick={() => { if (!busy) setConfirming(false); }}>{CLOSE_WORDS.keep}</button>
      </div>
    </div>
  );
}

export function QuestionThread({ row, words, audience = "studio", initiallyOpen = false, scrollIntoView = false, showSubjectLink = false, showProject = false, allowClose = false }: {
  row: QuestionRow; words: ThreadWords; audience?: Audience; initiallyOpen?: boolean; scrollIntoView?: boolean; showSubjectLink?: boolean;
  showProject?: boolean; allowClose?: boolean;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const [announce, setAnnounce] = useState("");
  // One set of Idempotency-Keys per question, shared by its reply and close controls.
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const heading = useRef<HTMLHeadingElement>(null);
  const article = useRef<HTMLElement>(null);
  const detail = useQuestion(open ? row.id : null);
  // The newest read wins: the detail (after a write it holds the returned question) over the list row.
  const fresh = detail.data && detail.data.id === row.id ? detail.data : null;
  const current = fresh ? questionRow(fresh, audience) : row;
  const closed = current.status === "closed";
  const replies = replyRows(fresh?.replies);
  const count = fresh ? fresh.replies.length : row.replyCount;
  const panelId = `question-${row.id}-conversation`;
  const scrolled = useRef(false);
  useEffect(() => {
    if (!scrollIntoView || scrolled.current) return;
    scrolled.current = true;
    article.current?.scrollIntoView({ block: "start" });
  }, [scrollIntoView]);
  const onClosed = () => {
    setAnnounce(CLOSE_WORDS.done);
    heading.current?.focus();
  };
  return (
    <article ref={article} className="card" aria-label={`Question asked ${utcWhen(row.createdAt)}`} data-testid="business-question" style={{ scrollMarginTop: 16 }}>
      <h3 ref={heading} tabIndex={-1}>
        {SHARED_WORDS.asked} {utcWhen(row.createdAt)}
        <span className="sp"><Pill tone={current.tone}>{current.statusLabel}</Pill></span>
      </h3>
      <p className="muted">
        {current.askedBy}
        {showProject && current.projectName ? <> · Project: {current.projectName}</> : null}
        {current.subject ? <> · {showSubjectLink && current.subjectHref ? <Link href={current.subjectHref}>{current.subject}</Link> : current.subject}</> : null}
      </p>
      <p style={TEXT_STYLE} data-testid="question-text">{current.text}</p>
      {closed ? <p className="muted">{closedSentence(current.closedBy)}</p> : null}
      {current.status === "answered" && words.answered && (current.canReply || current.canClose) ? <p className="muted">{words.answered}</p> : null}
      <p className="sr-only" role="status">{announce}</p>
      <div className="toolbar">
        <button type="button" className="btn" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((value) => !value)}>
          {open ? SHARED_WORDS.hideReplies : `${SHARED_WORDS.showReplies} (${count})`}
        </button>
        {allowClose && current.canClose ? <CloseControl questionId={row.id} version={current.version} keys={keys} onClosed={onClosed} /> : null}
      </div>
      {open ? (
        <div id={panelId} style={{ display: "grid", gap: 12 }}>
          {detail.isPending ? <p role="status">{SHARED_WORDS.repliesLoading}</p> : null}
          {detail.isError ? <p role="alert">{SHARED_WORDS.repliesFailed}</p> : null}
          {fresh && replies.length === 0 ? <p className="muted">{SHARED_WORDS.noReplies}</p> : null}
          {replies.length ? (
            <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 10 }} aria-label="Replies, oldest first">
              {replies.map((reply) => (
                <li key={reply.id} className="card flat" data-testid="question-reply">
                  <p className="muted">{reply.author} · {utcWhen(reply.at)}</p>
                  <p style={TEXT_STYLE}>{reply.text}</p>
                </li>
              ))}
            </ol>
          ) : null}
          {fresh && current.canReply && current.youReplyAs ? <ReplyForm questionId={row.id} side={current.youReplyAs} keys={keys} /> : null}
        </div>
      ) : null}
    </article>
  );
}
