"use client";

/**
 * Ask the business (D7b): the data team asks the business users of this workspace a question about the project,
 * optionally linked to the model in use or the latest prediction list, and reads their replies here (and in the Inbox).
 * Reads: `GET /v1/projects/{id}/questions`, `GET /v1/questions/{id}`; writes: ask, follow-up, close (If-Match). The API
 * decides who may ask (ML-write); the form is only hidden for people whose session says they cannot. Text is untrusted.
 */
import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import { Card } from "@/components/studio/Card";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { QuestionTextField, QuestionThread } from "@/app/components/business-view/QuestionThread";
import { useProjectRefs, useSession } from "@/lib/application";
import { formatDay } from "@/lib/application/business-outcomes";
import { useProjectOutcomes } from "@/lib/application/business-outcomes-hooks";
import { STUDIO_WORDS, checkQuestionText, chosenSubject, linkChoices, questionErrorSentence, questionListView, sentButUnreadable } from "@/lib/application/business-questions";
import { askQuestion, useProjectQuestions, useQuestionWrites } from "@/lib/application/business-questions-hooks";
import { isUuid } from "@/lib/application/command-search";
import { ActionKeys } from "@/lib/application/studio-wizard";
import { ApiError } from "@/lib/infrastructure/api-client";
import { CAPABILITIES, hasCapability } from "@/lib/infrastructure/capabilities";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

function Compose({ projectId }: { projectId: string }) {
  const refs = useProjectRefs(projectId);
  const outcomes = useProjectOutcomes(projectId);
  const list = outcomes.data?.weekly_list ?? null;
  const choices = linkChoices({ refs: refs.data?.items, latestList: list ? { id: list.id, label: formatDay(list.completed_at) ? `scored ${formatDay(list.completed_at)}` : null } : null });
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const box = useRef<HTMLTextAreaElement>(null);
  const written = useQuestionWrites();
  const [text, setText] = useState("");
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const check = checkQuestionText(text);
  const submit = async () => {
    if (flight.current || !check.ok) return;
    flight.current = true;
    setBusy(true);
    setError(null);
    setDone(false);
    try {
      const subject = chosenSubject(choices, link);
      const key = keys.keyFor("ask", `${check.text}\u0000${subject ? `${subject.kind}:${subject.id}` : ""}`);
      written(await askQuestion({ projectId, text: check.text, subject, key }));
      keys.done("ask");
      setText("");
      setLink("");
      setDone(true);
    } catch (caught) {
      if (sentButUnreadable(caught)) {
        keys.done("ask");
        setText("");
        setLink("");
        written(null);
      }
      setError(questionErrorSentence(caught));
    } finally {
      flight.current = false;
      setBusy(false);
      box.current?.focus();
    }
  };
  return (
    <Card title={STUDIO_WORDS.composeTitle}>
      <form className="form" aria-label={STUDIO_WORDS.composeTitle} onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <QuestionTextField id="ask-the-business" label={STUDIO_WORDS.askLabel} value={text} onChange={(value) => { setText(value); setDone(false); }} busy={busy} inputRef={box} />
        <label className="field">
          <span>{STUDIO_WORDS.linkLabel}</span>
          <select value={link} aria-disabled={busy || undefined} onChange={(event) => { if (!busy) setLink(event.target.value); }}>
            {choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
          </select>
        </label>
        <div className="toolbar">
          <button type="submit" className="btn primary" aria-disabled={busy || !check.ok} onClick={(event) => { if (busy || !check.ok) event.preventDefault(); }}>
            {busy ? STUDIO_WORDS.asking : STUDIO_WORDS.ask}
          </button>
        </div>
        <p role="status">{done ? STUDIO_WORDS.asked : ""}</p>
        {error ? <p role="alert">{error}</p> : null}
      </form>
    </Card>
  );
}

function QuestionsInner() {
  const { id } = useParams<{ id: string }>();
  const projectId = isUuid(id) ? id : undefined;
  const requested = useSearchParams().get("question");
  const { user } = useSession();
  // Platform staff never ask (the API refuses them too); the API decides for everyone else.
  const canAsk = hasCapability(user, CAPABILITIES.workspaceExecuteMl) && !hasCapability(user, CAPABILITIES.platformRead);
  const read = useProjectQuestions(projectId);
  const view = questionListView(
    { pending: read.isPending, errorStatus: read.isError ? (read.error instanceof ApiError ? read.error.status : 0) : null, data: read.data },
    STUDIO_WORDS,
  );
  return (
    <>
      <PageHead title={STUDIO_WORDS.title} subtitle={STUDIO_WORDS.subtitle} />
      <PageGuide purpose={STUDIO_WORDS.guidePurpose} howTo={STUDIO_WORDS.guideHowTo} youGet={STUDIO_WORDS.guideYouGet} />
      {!user ? null : projectId && canAsk ? <Compose projectId={projectId} /> : <p className="muted" role="note">{STUDIO_WORDS.cannotAsk}</p>}
      <Card title={STUDIO_WORDS.listTitle}>
        {view.kind === "loading" ? <p role="status">{view.message}</p> : null}
        {view.kind === "forbidden" ? <p role="note">{view.message}</p> : null}
        {view.kind === "error" ? <p role="alert">{view.message}</p> : null}
        {view.kind === "empty" ? <p>{view.message}</p> : null}
        {view.kind === "ready" && view.more ? <p className="muted">{view.more}</p> : null}
      </Card>
      {view.kind === "ready"
        ? view.rows.map((row) => (
            <QuestionThread key={row.id} row={row} words={STUDIO_WORDS} showSubjectLink allowClose initiallyOpen={isUuid(requested) && requested === row.id} scrollIntoView={isUuid(requested) && requested === row.id} />
          ))
        : null}
    </>
  );
}

export default function AskTheBusinessPage() {
  return (
    <Suspense fallback={<p role="status">{STUDIO_WORDS.loading}</p>}>
      <QuestionsInner />
    </Suspense>
  );
}
