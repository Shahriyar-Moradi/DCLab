"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { Level, type TrustLevel } from "@/components/studio/Level";
import { Pill } from "@/components/studio/Pill";
import { STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { plainText, isUuid } from "@/lib/application/command-search";
import { useProjectRefs, useStudioProjects } from "@/lib/application";
import { useAiOff } from "@/lib/application/studio-home-hooks";
import { acceptRefProposal, resolveDecision, supersedeDecision, useDecisionDetail } from "@/lib/application/studio-compare-hooks";
import { proposedMoves } from "@/lib/application/studio-compare";
import { decideProposal, useInboxInvalidation, useRunNamer, type StudioInboxItem } from "@/lib/application/studio-inbox-hooks";
import {
  DRIFT_STATS_NOTE, actionRoute, agreementPill, answerLines, approvalNote, comparableRunIds, disabledReason, driftSentence, driftStats, evidenceLinks, findAction, inboxCompareHref, inboxTitle, lookup,
  improveItemHref, improveItemSentence, mapInboxError, monitoringHref, outcomeWords, questionHref, refVersionsFor, resolverPill, retrainItemSentence, subjectLink, suggestedBy,
} from "@/lib/application/studio-inbox";
import { ActionKeys, type PlainError } from "@/lib/application/studio-wizard";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

type Mode = "accept" | "reject" | "revert" | "supersede";
const MODE_LABEL: Record<Mode, string> = { accept: "Approve", reject: "Reject", revert: "Undo", supersede: "Correct" };
/** D5c: the retrained model's card says Switch / Keep for the same approve / reject writes. */
const label = (kind: string, mode: Mode) => (kind === "challenger_ready" && mode === "accept" ? "Switch" : kind === "challenger_ready" && mode === "reject" ? "Keep" : MODE_LABEL[mode]);
const DONE_TEXT: Record<Mode, string> = {
  accept: "Approved. The decision was recorded.", reject: "Rejected. The decision was recorded.",
  revert: "Undone. The rules' value is back in place.", supersede: "Corrected. A new History entry replaces the old one.",
};

function Answer({ title, tone, answer, aiOff }: { title: string; tone: "det" | "ai"; answer: Record<string, unknown> | null | undefined; aiOff?: boolean | null }) {
  const lines = answerLines(answer);
  return (
    <div>
      <h4><Pill tone={tone}>{title}</Pill></h4>
      {lines.length ? (
        <dl className="kv">{lines.map((line, index) => <div key={`${index}:${line.key}`}><dt>{line.key}</dt><dd>{line.value}</dd></div>)}</dl>
      ) : <p className="muted">{tone === "ai" ? (aiOff ? "AI is switched off, so there is no AI answer." : "No AI answer is recorded.") : "No rule answer is recorded."}</p>}
    </div>
  );
}

/** The write panel only mounts when a person starts an action, so cards do not fetch details for nothing. */
function DecidePanel({ item, title, mode, keys, onClose, onDone }: { item: StudioInboxItem; title: string; mode: Mode; keys: ActionKeys; onClose: () => void; onDone: (text: string) => void }) {
  const action = findAction(item, mode)!;
  const route = actionRoute(item, mode);
  const viaRefs = route === "ref_move_accept";
  const needsRefs = viaRefs || route === "proposal_accept";
  const decisionId = action.path_params.decision_id;
  const detail = useDecisionDetail(viaRefs ? item.source.id : null);
  const refs = useProjectRefs(needsRefs ? (item.project_id ?? undefined) : undefined);
  const invalidate = useInboxInvalidation();
  const flight = useRef(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const needsReason = item.kind === "decision_proposal" || item.kind === "challenger_ready" || mode === "supersede";
  const waitingForRefs = (viaRefs && !detail.data) || (needsRefs && !refs.data);

  const submit = async () => {
    if (flight.current || (needsReason && !reason.trim()) || waitingForRefs || route === null) return;
    flight.current = true;
    setBusy(true);
    setError(null);
    try {
      const rationale = reason.trim();
      const versions = refs.data ? refVersionsFor(refs.data.items) : null;
      // The key is per item and action; the fingerprint covers everything the request sends, so a retry replays.
      const key = keys.keyFor(mode, `${item.id}:${rationale}:${JSON.stringify(versions)}`);
      const proposalId = action.path_params.proposal_id;
      switch (route) {
        case "ref_move_accept": {
          const moves = proposedMoves({ effective_state: "proposed", decision_type: detail.data!.decision_type, details: detail.data!.details });
          if (!isUuid(item.project_id) || moves.length === 0) throw new Error("This ref move cannot be accepted from the inbox. Open the project's decisions.");
          await acceptRefProposal({ projectId: item.project_id, proposalId: item.source.id, moves, refs: refs.data!.items, rationale, key });
          break;
        }
        case "decision_accept":
        case "decision_reject":
          if (!isUuid(decisionId)) throw new Error("This item has no decision to resolve.");
          await resolveDecision({ action: route === "decision_reject" ? "reject" : "accept", decisionId, rationale, key });
          break;
        case "decision_supersede":
          if (!isUuid(decisionId)) throw new Error("This item has no decision to correct.");
          await supersedeDecision({ decisionId, rationale, key });
          break;
        case "proposal_accept":
        case "proposal_reject":
        case "proposal_revert":
          if (!isUuid(proposalId)) throw new Error("This item has no suggestion to decide.");
          await decideProposal({ action: route === "proposal_accept" ? "accept" : route === "proposal_reject" ? "reject" : "revert", proposalId, rationale, key, refVersions: route === "proposal_accept" && versions ? versions : undefined });
          break;
      }
      keys.done(mode);
      onDone(item.kind === "challenger_ready" ? (mode === "accept" ? "Switched. The retrained model is the model in use; it is recorded in History." : "Kept. The model in use stays; it is recorded in History.") : DONE_TEXT[mode]);
      invalidate();
    } catch (caught) {
      setError(mapInboxError(caught));
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };

  return (
    <form className="form" aria-label={`${label(item.kind, mode)} ${title}`} onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <p className="muted">
        {approvalNote(item, route, mode) ?? "This is recorded in History."} Nothing recorded is edited.
        {needsReason ? " A reason is required." : " A reason is optional."}
      </p>
      {needsRefs && (detail.isError || refs.isError) ? <Banner tone="warn">Could not read the versions currently in use. Reload and try again, or decide it from the project&apos;s History.</Banner> : null}
      <label className="field"><span>Reason (recorded)</span><textarea rows={2} maxLength={2000} required={needsReason} value={reason} disabled={busy} onChange={(e) => setReason(e.target.value)} /></label>
      <div className="toolbar">
        <button type="button" className="btn" disabled={busy} onClick={onClose}>Back</button>
        <button type="submit" className={`btn ${mode === "reject" ? "" : "primary"}`} disabled={busy || waitingForRefs || (needsReason && !reason.trim())}>
          {busy ? "Saving…" : `Confirm ${label(item.kind, mode).toLowerCase()}`}
        </button>
      </div>
      {error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null}
    </form>
  );
}

/** Runs named by the item's own evidence; shown only when two or more exist (the compare view needs two). */
function CompareLink({ item, title }: { item: StudioInboxItem; title: string }) {
  const href = inboxCompareHref(item.project_id, comparableRunIds(item));
  return href ? <Link className="btn" href={href}>See comparison<span className="sr-only"> for {title}</span></Link> : null;
}

export function InboxCard({ item, canDecide, onDone }: { item: StudioInboxItem; canDecide: boolean; onDone: (text: string) => void }) {
  const [mode, setMode] = useState<Mode | null>(null);
  // Idempotency keys live with the card (per action), so Back and reopen after a timeout reuse the key and replay.
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const aiOff = useAiOff();
  const projects = useStudioProjects();
  const runName = useRunNamer(item.project_id);
  const project = isUuid(item.project_id) ? projects.data?.find((p) => p.id === item.project_id) : undefined;
  const subject = subjectLink(item, runName);
  const evidence = evidenceLinks(item, runName);
  const title = inboxTitle(item, item.subject.kind === "experiment" && isUuid(item.subject.id) ? runName(item.subject.id) : null);
  const drift = item.kind === "drift_alert";
  const retrainText = retrainItemSentence(item);
  const improveText = improveItemSentence(item);
  const loopHref = improveItemHref(item);
  const by = drift ? "Flagged by the weekly monitoring rule" : suggestedBy(item.proposed_by, item.kind);
  const agreement = agreementPill(item.agreement, item.tab, aiOff);
  const resolver = resolverPill(item.resolved_by, item.tab);
  const stats = drift ? driftStats(item.monitoring) : [];
  const monitoring = drift || retrainText ? monitoringHref(item.project_id) : null;
  const auto = item.tab === "applied_automatically";
  // The AI panel is dropped only when AI is known off and nothing was recorded; a recorded AI answer is evidence and stays.
  const showAi = !(aiOff === true && !item.ai_answer);
  const answered = item.rule_answer || item.ai_answer || item.kind === "agent_proposal";
  const buttons: Mode[] = (["accept", "reject", "revert", "supersede"] as const).filter((name) => findAction(item, name));
  const answerAction = findAction(item, "answer");
  const answerHref = answerAction ? questionHref(item) : null;
  const reasons = [...new Set(buttons.map((name) => disabledReason(item, name, canDecide)).filter((r): r is string => !!r))];
  const notes = [...new Set(buttons.map((name) => approvalNote(item, actionRoute(item, name), name)).filter((n): n is string => !!n))];
  const canUndo = !!findAction(item, "revert");
  return (
    <article className="card" aria-label={title}>
      <h3>
        {title}
        <span className="sp">
          <Pill tone={lookup(STATUS_TONE, item.status, "gray")}>{outcomeWords(item.status)}</Pill>
          {typeof item.level === "number" && item.level >= 0 && item.level <= 3 ? <> <Level level={item.level as TrustLevel} /></> : null}
        </span>
      </h3>
      <p className="muted">
        {project ? <>{plainText(project.name, 80)} · </> : null}{formatWhen(item.occurred_at)}
        {item.decision_point_key ? <> · about {plainText(item.decision_point_key.replaceAll("_", " "), 60)}</> : null}
        {item.expires_at ? <> · expires {formatWhen(item.expires_at)}</> : null}
      </p>
      {by || agreement || resolver ? (
        <p className="v7-inbox-pills">
          {by ? <Pill tone={item.proposed_by === "rule" ? "det" : item.proposed_by === "person" || item.proposed_by === "run" ? "gray" : "ai"}>{by}</Pill> : null}
          {agreement ? <span data-testid="inbox-agreement"><Pill tone={agreement.tone}>{agreement.label}</Pill></span> : null}
          {resolver ? <span data-testid="inbox-resolver"><Pill tone={item.resolved_by?.kind === "rule" ? "det" : "gray"}>{resolver}</Pill></span> : null}
          {item.proposed_by === "agent" ? <span className="muted"> Its text is not verified.</span> : null}
        </p>
      ) : null}
      {drift ? <p data-testid="drift-sentence">{driftSentence(item)}</p> : null}
      {retrainText ? <p data-testid="retrain-sentence">{retrainText}</p> : null}
      {improveText ? <p data-testid="improve-sentence">{improveText}</p> : null}
      {stats.length ? (
        <dl className="v7-inbox-stats" aria-label="The week's numbers">
          {stats.map((st) => <div key={st.label}><dt>{st.label}</dt><dd>{st.value}</dd></div>)}
        </dl>
      ) : null}
      {stats.length ? <p className="muted">{DRIFT_STATS_NOTE}</p> : null}
      {auto ? <p role="note"><b>Applied automatically.</b> {item.status === "reverted" ? "It was undone." : canUndo ? "Nobody approved it first. Undo puts the previous value back." : "Nobody approved it first. No undo is offered for this one here."}</p> : null}
      {subject || evidence.length ? (
        <p>
          <b>Evidence:</b>{" "}
          {[...(subject ? [subject] : []), ...evidence.filter((e) => e.key !== subject?.key)].map((link, index) => (
            <span key={link.key}>{index ? " · " : ""}{link.href ? <Link href={link.href}>{link.label}</Link> : <span>{link.label}</span>}</span>
          ))}
        </p>
      ) : null}
      {answered ? (
        <div className={`grid ${showAi ? "cols-2" : ""}`} aria-label="What the rules say beside what the AI says">
          <Answer title="What the rules say" tone="det" answer={item.rule_answer} />
          {showAi ? <Answer title="What the AI says (advice only)" tone="ai" answer={item.ai_answer} aiOff={aiOff} /> : null}
        </div>
      ) : null}
      {item.answers_truncated ? <p className="muted">An answer was too large to show in full.</p> : null}
      {mode ? <DecidePanel item={item} title={title} mode={mode} keys={keys} onClose={() => setMode(null)} onDone={(text) => { setMode(null); onDone(text); }} /> : (
        <>
          {notes.map((note) => <p key={note} className="muted">{note}</p>)}
          <div className="toolbar">
            {buttons.map((name) => {
              const action = findAction(item, name)!;
              return (
                <button key={name} type="button" className={`btn ${name === "accept" ? "primary" : ""}`} disabled={!action.allowed || actionRoute(item, name) === null} onClick={() => setMode(name)}>
                  {label(item.kind, name)}<span className="sr-only"> {title}</span>
                </button>
              );
            })}
            {answerHref ? <Link className="btn primary" href={answerHref}>Answer on the run page</Link> : null}
            {monitoring ? <Link className={`btn ${item.tab === "needs_decision" && item.kind !== "challenger_ready" ? "primary" : ""}`} href={monitoring}>Open Monitoring<span className="sr-only"> for {title}</span></Link> : null}
            {loopHref ? <Link className={`btn ${item.status === "waiting_user" ? "primary" : ""}`} href={loopHref}>Open the loop<span className="sr-only"> for {title}</span></Link> : null}
            <CompareLink item={item} title={title} />
          </div>
        </>
      )}
      {reasons.map((reason) => <p key={reason} className="muted" role="note">{reason}</p>)}
    </article>
  );
}
