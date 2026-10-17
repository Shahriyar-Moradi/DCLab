"use client";

import { useRef, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { Level } from "@/components/studio/Level";
import { Pill } from "@/components/studio/Pill";
import { formatWhen } from "@/app/components/studio-app/StudioParts";
import { useProjectRefs } from "@/lib/application";
import { plainText } from "@/lib/application/command-search";
import { shownLevel } from "@/lib/application/studio-ai-settings";
import { BETA_LABEL, money, proposalError, toolWords, type AssistantError } from "@/lib/application/studio-assistant";
import { useProposal } from "@/lib/application/studio-assistant-hooks";
import { answerLines, approvalNote, inboxTitle, outcomeWords, refVersionsFor, suggestedBy } from "@/lib/application/studio-inbox";
import { decideProposal, useInboxInvalidation } from "@/lib/application/studio-inbox-hooks";
import { ActionKeys } from "@/lib/application/studio-wizard";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";
import { useQueryClient } from "@tanstack/react-query";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";

function minutes(seconds: number): string {
  return `at most ${Math.max(1, Math.ceil(seconds / 60))} minute${seconds <= 60 ? "" : "s"}`;
}

/**
 * A suggestion the assistant made in this conversation (D6b U2), with the Inbox's decision logic: the same accept / reject
 * routes, approval notes, error sentences and per-action Idempotency-Keys. Nothing happens until a person confirms.
 */
export function AssistantProposalCard({ proposalId, projectId }: { proposalId: string; projectId: string }) {
  const proposal = useProposal(proposalId);
  const refs = useProjectRefs(projectId);
  const invalidate = useInboxInvalidation();
  const client = useQueryClient();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const [busy, setBusy] = useState<"accept" | "reject" | null>(null);
  const [error, setError] = useState<AssistantError | null>(null);
  const [done, setDone] = useState<string | null>(null);

  if (proposal.isPending) return <p className="muted" role="status">Loading a suggestion…</p>;
  if (proposal.isError || !proposal.data) return <Banner tone="warn">This suggestion could not be loaded. It is also in the Inbox.</Banner>;
  const p = proposal.data;
  const title = inboxTitle({ kind: "agent_proposal", subject: p.subject, summary: "", proposal_type: p.proposal_type, status: p.status });
  const args = answerLines(p.tool_arguments ?? null);
  // Only a suggestion of this conversation's project is decided here (the API checks it too).
  const sameProject = p.project_id === projectId;

  const decide = async (action: "accept" | "reject") => {
    if (flight.current || !sameProject || (action === "accept" && !refs.data)) return;
    flight.current = true;
    setBusy(action);
    setError(null);
    try {
      const versions = action === "accept" && refs.data ? refVersionsFor(refs.data.items) : undefined;
      const key = keys.keyFor(action, `${p.id}:${JSON.stringify(versions ?? null)}`);
      await decideProposal({ action, proposalId: p.id, rationale: "", key, refVersions: versions });
      keys.done(action);
      setDone(action === "reject" ? "Rejected. Nothing else changed." : p.proposal_type === "ToolCallProposal" ? "Accepted. It ran as you and was recorded in History." : "Accepted. It was recorded in History.");
      invalidate();
      void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", "proposal", p.id) });
    } catch (caught) {
      setError(proposalError(caught));
    } finally {
      flight.current = false;
      setBusy(null);
    }
  };

  return (
    <article className="card asst-card" aria-label={title}>
      <h3>
        {title}
        <span className="sp"><Pill tone={p.open ? "warn" : "gray"}>{outcomeWords(p.status)}</Pill> <Level level={shownLevel(p.level_at_proposal)} /></span>
      </h3>
      <p className="small"><Pill tone="ai">{suggestedBy(p.proposed_by, "agent_proposal") ?? `Suggested by the assistant (${BETA_LABEL})`}</Pill> <span className="muted">{formatWhen(p.created_at)}</span></p>
      {p.tool_name ? <p><b>Action:</b> {toolWords(p.tool_name)}</p> : null}
      {args.length ? (
        <dl className="kv" aria-label="What it would send">{args.map((line, i) => <div key={`${i}:${line.key}`}><dt>{line.key}</dt><dd>{line.value}</dd></div>)}</dl>
      ) : null}
      {p.proposed_rationale ? <p className="small"><span className="muted">The assistant&apos;s reason (not verified):</span> {plainText(p.proposed_rationale, 600)}</p> : null}
      {typeof p.estimated_cost_micros === "number" || typeof p.estimated_duration_s === "number" ? (
        <p className="small muted">
          Estimate before it runs:{typeof p.estimated_cost_micros === "number" ? ` AI cost ${money(p.estimated_cost_micros)}` : ""}
          {typeof p.estimated_duration_s === "number" ? `${typeof p.estimated_cost_micros === "number" ? ";" : ""} ${minutes(p.estimated_duration_s)}` : ""}.
        </p>
      ) : null}
      {p.open && !done && !sameProject ? <p className="small muted">This suggestion belongs to another project. Decide it in the Inbox.</p> : null}
      {p.open && !done && sameProject ? (
        <>
          <p className="small muted">{approvalNote({ kind: "agent_proposal", proposal_type: p.proposal_type }, "proposal_accept", "accept")} {approvalNote({ kind: "agent_proposal" }, "proposal_reject", "reject")}</p>
          <div className="toolbar">
            <button type="button" className="btn primary" disabled={busy !== null || !refs.data} onClick={() => void decide("accept")}>
              {busy === "accept" ? "Saving…" : "Accept"}<span className="sr-only"> {title}</span>
            </button>
            <button type="button" className="btn" disabled={busy !== null} onClick={() => void decide("reject")}>
              {busy === "reject" ? "Saving…" : "Reject"}<span className="sr-only"> {title}</span>
            </button>
          </div>
        </>
      ) : null}
      {done ? <p role="status">{done}</p> : null}
      {error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null}
    </article>
  );
}
