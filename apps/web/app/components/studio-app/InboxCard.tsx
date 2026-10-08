"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { Level, type TrustLevel } from "@/components/studio/Level";
import { Pill } from "@/components/studio/Pill";
import { STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { plainText, isUuid } from "@/lib/application/command-search";
import { useProjectRefs } from "@/lib/application";
import { acceptRefProposal, resolveDecision, supersedeDecision, useDecisionDetail } from "@/lib/application/studio-compare-hooks";
import { proposedMoves } from "@/lib/application/studio-compare";
import { decideProposal, useInboxInvalidation, type StudioInboxItem } from "@/lib/application/studio-inbox-hooks";
import {
  PROPOSED_BY_LABEL, actionRoute, answerLines, comparableRunIds, disabledReason, evidenceLinks, findAction, inboxCompareHref, lookup, mapInboxError, questionHref, refVersionsFor, subjectLink,
} from "@/lib/application/studio-inbox";
import { ActionKeys, type PlainError } from "@/lib/application/studio-wizard";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

type Mode = "accept" | "reject" | "revert" | "supersede";
const MODE_LABEL: Record<Mode, string> = { accept: "Approve", reject: "Reject", revert: "Revert", supersede: "Supersede" };
const DONE_TEXT: Record<Mode, string> = {
  accept: "Approved. The decision was recorded.", reject: "Rejected. The decision was recorded.",
  revert: "Reverted. The rule's value is back in place.", supersede: "Corrected. A new record supersedes the old one.",
};

function Answer({ title, tone, answer }: { title: string; tone: "det" | "ai"; answer: Record<string, unknown> | null | undefined }) {
  const lines = answerLines(answer);
  return (
    <div>
      <h4><Pill tone={tone}>{title}</Pill></h4>
      {lines.length ? (
        <dl className="kv">{lines.map((line, index) => <div key={`${index}:${line.key}`}><dt>{line.key}</dt><dd>{line.value}</dd></div>)}</dl>
      ) : <p className="muted">{tone === "ai" ? "No AI answer is recorded (AI off or not asked)." : "No rule answer is recorded."}</p>}
    </div>
  );
}

/** The write panel only mounts when a person starts an action, so cards do not fetch details for nothing. */
function DecidePanel({ item, mode, keys, onClose, onDone }: { item: StudioInboxItem; mode: Mode; keys: ActionKeys; onClose: () => void; onDone: (text: string) => void }) {
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
  const needsReason = item.kind === "decision_proposal" || mode === "supersede";
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
          if (!isUuid(proposalId)) throw new Error("This item has no proposal to decide.");
          await decideProposal({ action: route === "proposal_accept" ? "accept" : route === "proposal_reject" ? "reject" : "revert", proposalId, rationale, key, refVersions: route === "proposal_accept" && versions ? versions : undefined });
          break;
      }
      keys.done(mode);
      onDone(DONE_TEXT[mode]);
      invalidate();
    } catch (caught) {
      setError(mapInboxError(caught));
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };

  return (
    <form className="form" aria-label={`${MODE_LABEL[mode]} ${plainText(item.summary, 80)}`} onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <p className="muted">
        {mode === "supersede" ? "A correction appends a new record; the old one stays, marked superseded." : mode === "revert" ? "Revert restores the rule's value by branching the run that used the AI value." : "This appends a record; nothing is edited."}
        {needsReason ? " A reason is required." : " A reason is optional."}
      </p>
      {needsRefs && (detail.isError || refs.isError) ? <Banner tone="warn">Could not read the current refs this moves. Reload and try again, or decide it from the project&apos;s Decisions page.</Banner> : null}
      <label className="field"><span>Reason (recorded)</span><textarea rows={2} maxLength={2000} required={needsReason} value={reason} disabled={busy} onChange={(e) => setReason(e.target.value)} /></label>
      <div className="toolbar">
        <button type="button" className="btn" disabled={busy} onClick={onClose}>Back</button>
        <button type="submit" className={`btn ${mode === "reject" ? "" : "primary"}`} disabled={busy || waitingForRefs || (needsReason && !reason.trim())}>
          {busy ? "Saving…" : `Confirm ${MODE_LABEL[mode].toLowerCase()}`}
        </button>
      </div>
      {error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null}
    </form>
  );
}

/** Runs named by the item's own evidence; shown only when two or more exist (the compare view needs two). */
function CompareLink({ item }: { item: StudioInboxItem }) {
  const href = inboxCompareHref(item.project_id, comparableRunIds(item));
  return href ? <Link className="btn" href={href}>See comparison<span className="sr-only"> for {plainText(item.summary, 80)}</span></Link> : null;
}

export function InboxCard({ item, canDecide, onDone }: { item: StudioInboxItem; canDecide: boolean; onDone: (text: string) => void }) {
  const [mode, setMode] = useState<Mode | null>(null);
  // Idempotency keys live with the card (per action), so Back and reopen after a timeout reuse the key and replay.
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const subject = subjectLink(item);
  const evidence = evidenceLinks(item);
  const answered = item.rule_answer || item.ai_answer || item.kind === "agent_proposal";
  const buttons: Mode[] = (["accept", "reject", "revert", "supersede"] as const).filter((name) => findAction(item, name));
  const answerAction = findAction(item, "answer");
  const answerHref = answerAction ? questionHref(item) : null;
  const reasons = [...new Set(buttons.map((name) => disabledReason(item, name, canDecide)).filter((r): r is string => !!r))];
  return (
    <article className="card" aria-label={plainText(item.summary, 120)}>
      <h3>
        {plainText(item.summary, 160)}
        <span className="sp">
          {item.proposed_by ? <Pill tone={item.proposed_by === "rule" ? "det" : item.proposed_by === "person" || item.proposed_by === "run" ? "gray" : "ai"}>{lookup(PROPOSED_BY_LABEL, item.proposed_by, plainText(item.proposed_by, 24))}</Pill> : null}{" "}
          <Pill tone={lookup(STATUS_TONE, item.status, "gray")}>{item.status.replaceAll("_", " ")}</Pill>
          {typeof item.level === "number" && item.level >= 0 && item.level <= 3 ? <> <Level level={item.level as TrustLevel} /></> : null}
        </span>
      </h3>
      <p className="muted">
        {formatWhen(item.occurred_at)}
        {item.decision_point_key ? <> · decision point <span className="mono">{plainText(item.decision_point_key, 60)}</span></> : null}
        {item.expires_at ? <> · expires {formatWhen(item.expires_at)}</> : null}
      </p>
      {subject || evidence.length ? (
        <p>
          <b>Evidence:</b>{" "}
          {[...(subject ? [subject] : []), ...evidence.filter((e) => e.key !== subject?.key)].map((link, index) => (
            <span key={link.key}>{index ? " · " : ""}{link.href ? <Link href={link.href}>{link.label}</Link> : <span>{link.label}</span>}</span>
          ))}
        </p>
      ) : null}
      {answered ? (
        <div className="grid cols-2" aria-label="Rule answer beside AI answer">
          <Answer title="Rule answer" tone="det" answer={item.rule_answer} />
          <Answer title="AI answer (advisory)" tone="ai" answer={item.ai_answer} />
        </div>
      ) : null}
      {item.answers_truncated ? <p className="muted">An answer was too large to show in full.</p> : null}
      {mode ? <DecidePanel item={item} mode={mode} keys={keys} onClose={() => setMode(null)} onDone={(text) => { setMode(null); onDone(text); }} /> : (
        <div className="toolbar">
          {buttons.map((name) => {
            const action = findAction(item, name)!;
            return (
              <button key={name} type="button" className={`btn ${name === "accept" ? "primary" : ""}`} disabled={!action.allowed || actionRoute(item, name) === null} onClick={() => setMode(name)}>
                {MODE_LABEL[name]}<span className="sr-only"> {plainText(item.summary, 80)}</span>
              </button>
            );
          })}
          {answerHref ? <Link className="btn primary" href={answerHref}>Answer on the run page</Link> : null}
          <CompareLink item={item} />
        </div>
      )}
      {reasons.map((reason) => <p key={reason} className="muted" role="note">{reason}</p>)}
    </article>
  );
}
