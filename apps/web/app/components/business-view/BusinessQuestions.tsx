"use client";

/**
 * Business view "Questions from the data team" (D7b): `GET /v1/questions` (this workspace's questions to its business
 * users, all projects, open first). Open questions show their conversation and reply box at once; answered and closed
 * ones open on request. A data-team person viewing this page gets the follow-up box (`you_reply_as`), never the business
 * wording. Every text is untrusted and rendered as plain text by `QuestionThread`.
 */
import { useEffect, useRef } from "react";
import { Card } from "@/components/studio/Card";
import { BUSINESS_WORDS, questionListView } from "@/lib/application/business-questions";
import { useWorkspaceQuestions } from "@/lib/application/business-questions-hooks";
import { ApiError } from "@/lib/infrastructure/api-client";
import { QuestionThread } from "./QuestionThread";

const OPEN_AT_ONCE = 10;
const BODY = { fontSize: "1.1rem", lineHeight: 1.6 } as const;

export function BusinessQuestions({ enabled }: { enabled: boolean }) {
  const read = useWorkspaceQuestions(enabled);
  const view = questionListView(
    { pending: read.isPending, errorStatus: read.isError ? (read.error instanceof ApiError ? read.error.status : 0) : null, data: read.data },
    BUSINESS_WORDS,
    "business",
  );
  const section = useRef<HTMLElement>(null);
  const scrolled = useRef(false);
  const settled = view.kind !== "loading";
  // `/outcomes#questions` (the sidebar link, the Inbox card): the browser jumps before the list exists, so scroll once it has loaded.
  useEffect(() => {
    if (!settled || scrolled.current || typeof window === "undefined" || window.location.hash !== "#questions") return;
    scrolled.current = true;
    section.current?.scrollIntoView({ block: "start" });
  }, [settled]);
  return (
    <section ref={section} id="questions" aria-labelledby="business-questions-title" style={{ display: "grid", gap: 12, scrollMarginTop: 16 }}>
      <Card title={<span id="business-questions-title">{BUSINESS_WORDS.title}</span>}>
        <p style={BODY}>{BUSINESS_WORDS.intro}</p>
        {view.kind === "loading" ? <p role="status">{view.message}</p> : null}
        {view.kind === "forbidden" ? <p role="note">{view.message}</p> : null}
        {view.kind === "error" ? <p role="alert">{view.message}</p> : null}
        {view.kind === "empty" ? <p style={BODY}>{view.message}</p> : null}
        {view.kind === "ready" && view.more ? <p className="muted">{view.more}</p> : null}
      </Card>
      {view.kind === "ready"
        ? view.rows.map((row, index) => (
            <QuestionThread key={row.id} row={row} words={BUSINESS_WORDS} audience="business" showProject initiallyOpen={row.status === "open" && index < OPEN_AT_ONCE} />
          ))
        : null}
    </section>
  );
}
