"use client";

import Link from "next/link";
import { Banner } from "@/components/studio/Banner";
import { Level } from "@/components/studio/Level";
import { Pill } from "@/components/studio/Pill";
import { formatWhen } from "@/app/components/studio-app/StudioParts";
import { plainText } from "@/lib/application/command-search";
import { shownLevel } from "@/lib/application/studio-ai-settings";
import { BETA_LABEL, pageContextFor, screenSuggestions, toolWords } from "@/lib/application/studio-assistant";
import { ApiError } from "@/lib/infrastructure/api-client";
import { useOpenProposals } from "@/lib/application/studio-assistant-hooks";
import { inboxTitle, suggestedBy } from "@/lib/application/studio-inbox";

/**
 * Per-screen "AI suggestions" (item 21): suggestions that ALREADY exist for what this screen shows, read-only. It never
 * creates, accepts or rejects anything; each item is decided in the Inbox. Off by default (a per-person toggle).
 */
export function AiSuggestions({ pathname }: { pathname: string }) {
  const { projectId } = pageContextFor(pathname);
  const proposals = useOpenProposals(projectId, true);
  const items = proposals.data ? screenSuggestions(proposals.data.items, pathname) : [];
  return (
    <section className="card flat ai-suggest" aria-label="AI suggestions for this screen" data-testid="ai-suggestions">
      <h2>AI suggestions for this screen <Pill tone="ai">{BETA_LABEL}</Pill></h2>
      <p className="small muted">Suggestions that already exist for what this screen shows. Nothing here acts on its own: decide each one in the Inbox.</p>
      {proposals.isPending ? <p role="status">Loading suggestions…</p> : null}
      {proposals.isError && proposals.error instanceof ApiError && proposals.error.status === 403 ? <Banner tone="info">Your role cannot read suggestions in this workspace.</Banner> : null}
      {proposals.isError && !(proposals.error instanceof ApiError && proposals.error.status === 403) ? <Banner tone="warn">Suggestions could not be loaded. The Inbox lists them too.</Banner> : null}
      {proposals.data && items.length === 0 ? <div className="empty">No open suggestions for this screen.</div> : null}
      {items.length ? (
        <ul className="plain-list">
          {items.map((p) => (
            <li key={p.id}>
              <b>{inboxTitle({ kind: "agent_proposal", subject: p.subject, summary: "", proposal_type: p.proposal_type, status: p.status })}</b>{" "}
              <Level level={shownLevel(p.level_at_proposal)} /> <Pill tone="ai">{suggestedBy(p.proposed_by, "agent_proposal")}</Pill>
              <span className="small muted"> · {p.tool_name ? `${toolWords(p.tool_name)} · ` : ""}{formatWhen(p.created_at)}</span>
              {p.proposed_rationale ? <span className="small"> · Reason (not verified): {plainText(p.proposed_rationale, 200)}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
      <p className="small"><Link href="/inbox">Open the Inbox to decide</Link></p>
    </section>
  );
}
