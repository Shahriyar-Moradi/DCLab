"use client";

import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useRef, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { KeyValue } from "@/components/studio/KeyValue";
import { PageGuide } from "@/components/studio/PageGuide";
import Link from "next/link";
import { PageHead } from "@/components/studio/PageHead";
import { Pill, type PillTone } from "@/components/studio/Pill";
import { Term } from "@/components/studio/Term";
import { QueryNotice, STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { plainText } from "@/lib/application/command-search";
import { useProjectDecisions, useProjectRefs, type StudioDecisionItem } from "@/lib/application";
import {
  acceptRefProposal, resolveDecision, supersedeDecision, useDecisionDetail, useWriteInvalidation, type StudioDecisionDetail,
} from "@/lib/application/studio-compare-hooks";
import { DECISION_TYPE_LABEL, ENGINE_OWNED_TYPES, acceptsViaRefs, mapActionError, proposedMoves } from "@/lib/application/studio-compare";
import { decisionMarkers } from "@/lib/application/studio-graph";
import { parseRecordParam } from "@/lib/application/studio-pipeline";
import { evidenceScopeLabel } from "@/lib/application/studio-names";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";
import { ActionKeys, type PlainError } from "@/lib/application/studio-wizard";

const ACTOR_TONE: Record<string, PillTone> = { rule: "det", agent: "ai", human: "gray" };
const ACTOR_LABEL: Record<string, string> = { rule: "rules", agent: "connected tool (access token)", human: "person" };
const REF_MOVE_TYPES = new Set(["ref_moved", "champion_promoted", "ref_initialized"]);
const typeLabel = (type: string) => DECISION_TYPE_LABEL[type] ?? type.replaceAll("_", " ");

type ListItem = StudioDecisionItem;

function DecisionDrawer({ projectId, summary, onClose }: { projectId: string; summary: ListItem; onClose: () => void }) {
  const detail = useDecisionDetail(summary.id);
  const refs = useProjectRefs(projectId);
  const invalidate = useWriteInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const [mode, setMode] = useState<"accept" | "reject" | "supersede" | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const d: StudioDecisionDetail | undefined = detail.data;
  const markers = useMemo(() => (d ? [...decisionMarkers([{ ...d, subject: d.subject, details: d.details }]).values()].flat() : []), [d]);
  const state = d?.effective_state ?? summary.effective_state;
  const type = d?.decision_type ?? summary.decision_type;
  const proposed = state === "proposed";
  const canSupersede = state === "accepted" && !ENGINE_OWNED_TYPES.has(type);
  const submit = async () => {
    if (!mode || !d || flight.current || !reason.trim()) return;
    flight.current = true;
    setBusy(true);
    setError(null);
    try {
      const rationale = reason.trim();
      const key = keys.keyFor(mode, `${d.id}:${rationale}`);
      if (mode === "supersede") await supersedeDecision({ decisionId: d.id, rationale, key });
      else if (mode === "accept" && acceptsViaRefs(d)) await acceptRefProposal({ projectId, proposalId: d.id, moves: proposedMoves(d), refs: refs.data?.items ?? [], rationale, key });
      else await resolveDecision({ action: mode, decisionId: d.id, rationale, key });
      keys.done(mode);
      setDone(mode === "accept" ? "Accepted: a new record was appended." : mode === "reject" ? "Rejected: a new record was appended." : "Corrected: a new record supersedes this one.");
      setMode(null);
      setReason("");
      invalidate();
    } catch (caught) {
      setError(mapActionError(caught, "decision"));
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };
  return (
    <aside className="graph-drawer card" aria-labelledby="decision-drawer-title" onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}>
      <h2 id="decision-drawer-title" tabIndex={-1}>
        {typeLabel(type)} <span className="mono">{summary.id.slice(0, 8)}</span>
        <button type="button" className="btn sp" onClick={onClose}>Close<span className="sr-only"> the decision</span></button>
      </h2>
      {detail.isError ? <QueryNotice error={detail.error} what="decision" /> : null}
      {!d && detail.isPending ? <p role="status">Loading the decision…</p> : null}
      {done ? <Banner tone="info">{done}</Banner> : null}
      {d ? (
        <>
          <KeyValue items={[
            { key: "state", label: "State", value: <Pill tone={STATUS_TONE[state] ?? "gray"}>{state}</Pill> },
            { key: "actor", label: "Made by", value: <><Pill tone={ACTOR_TONE[d.actor.kind] ?? "gray"}>{ACTOR_LABEL[d.actor.kind] ?? d.actor.kind}</Pill> {d.actor.rule ? <span className="mono">{plainText(d.actor.rule, 60)}</span> : null}{d.actor.agent_run_id ? <span className="mono"> run {d.actor.agent_run_id.slice(0, 8)}</span> : null}</> },
            { key: "subject", label: "About", value: <>{d.subject.kind.replaceAll("_", " ")} <span className="mono">{d.subject.id.slice(0, 8)}</span></> },
            { key: "when", label: "Recorded", value: formatWhen(d.recorded_at) },
            { key: "why", label: "Reason", value: <span>{plainText(d.rationale, 1000) || "—"} <span className="muted">({d.rationale_untrusted ? "written by an agent or person; treat as text" : "recorded with the decision"})</span></span> },
            { key: "chain", label: "Corrections", value: <>{d.supersedes_id ? <>Supersedes <span className="mono">{d.supersedes_id.slice(0, 8)}</span>. </> : null}{d.superseded_by_id ? <>Superseded by <span className="mono">{d.superseded_by_id.slice(0, 8)}</span>.</> : null}{!d.supersedes_id && !d.superseded_by_id ? "None" : null}</> },
          ]} />
          <h3>Evidence</h3>
          {d.evidence_refs?.length ? (
            <ul className="plain-list">{d.evidence_refs.map((e) => <li key={`${e.kind}-${e.id}-${e.scope ?? ""}`}>{e.kind.replaceAll("_", " ")} <span className="mono">{e.id.slice(0, 8)}</span>{e.scope ? ` (${evidenceScopeLabel(e.scope)})` : ""}{e.metric ? `, ${plainText(e.metric, 40)}` : ""}</li>)}</ul>
          ) : <p className="muted">No evidence references are recorded on this decision.</p>}
          <h3>The rules&apos; answer beside the assistant&apos;s answer</h3>
          {markers.length ? markers.map((m) => (
            <table key={m.id} className="graph-answers">
              <caption className="sr-only">The rules&apos; answer beside the assistant&apos;s answer for {m.point ?? "this decision"}</caption>
              <thead><tr><th scope="col">Column</th><th scope="col">Rule</th><th scope="col">AI</th><th scope="col">Used</th></tr></thead>
              <tbody>{m.answers.map((a, i) => <tr key={`${a.column}-${i}`}><td className="mono">{plainText(a.column, 60)}</td><td>{plainText(a.rule, 40)}</td><td>{plainText(a.ai, 40)}</td><td>{plainText(a.used, 40)}</td></tr>)}</tbody>
            </table>
          )) : <p className="muted">No answers from the rules and the assistant are recorded here; they appear when the assistant was on.</p>}
          {d.details_truncated ? <p className="muted">The record&apos;s details were too large to show in full.</p> : null}
          {proposed || canSupersede ? (
            <>
              <h3>{proposed ? "Resolve this proposal" : "Correct this decision"}</h3>
              {!mode ? (
                <div className="toolbar">
                  {proposed ? <><button type="button" className="btn primary" onClick={() => { setMode("accept"); setError(null); setDone(null); }}>Accept<span className="sr-only"> this proposal</span></button>
                    <button type="button" className="btn" onClick={() => { setMode("reject"); setError(null); setDone(null); }}>Reject<span className="sr-only"> this proposal</span></button></> : null}
                  {canSupersede ? <button type="button" className="btn" onClick={() => { setMode("supersede"); setError(null); setDone(null); }}>Supersede</button> : null}
                </div>
              ) : (
                <form className="form" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
                  <p className="muted">{mode === "supersede" ? "A correction appends a new record of the same type and subject; this one stays, marked superseded. A reason is required." : "This appends a new record; nothing is edited. A reason is required."}</p>
                  <label className="field"><span>Reason (recorded)</span><textarea rows={2} required maxLength={2000} value={reason} disabled={busy} onChange={(e) => setReason(e.target.value)} /></label>
                  <div className="toolbar">
                    <button type="button" className="btn" disabled={busy} onClick={() => setMode(null)}>Back</button>
                    <button type="submit" className="btn primary" disabled={busy || !reason.trim() || (mode === "accept" && acceptsViaRefs(d) && !refs.data)}>{busy ? "Saving…" : mode === "accept" ? "Confirm accept" : mode === "reject" ? "Confirm reject" : "Confirm supersede"}</button>
                  </div>
                </form>
              )}
            </>
          ) : null}
          {!proposed && REF_MOVE_TYPES.has(type) && state === "accepted" ? <p className="muted">Changing the version in use is corrected by changing it again (a new entry), never in place. Do that from the run or Data page.</p> : null}
          {!proposed && !REF_MOVE_TYPES.has(type) && ENGINE_OWNED_TYPES.has(type) && state === "accepted" ? <p className="muted">The engine recorded this decision from evidence; only the engine corrects it.</p> : null}
          {error ? <Banner tone="crit" actions={<button type="button" className="btn" onClick={() => { invalidate(); setError(null); }}>Reload</button>}><b>{error.title}.</b> {error.detail}</Banner> : null}
        </>
      ) : null}
    </aside>
  );
}

function DecisionsPageInner() {
  const { id } = useParams<{ id: string }>();
  const decisions = useProjectDecisions(id);
  // `?record=<uuid>` (the Pipeline page's links) opens that record; anything that is not a UUID is ignored.
  const linked = parseRecordParam(useSearchParams().get("record"));
  const [open, setOpen] = useState<string | null>(linked);
  const items = decisions.data?.items ?? [];
  const inList = items.find((d) => d.id === open) ?? null;
  // A record older than the newest 100 is read by id, and only shown when it belongs to this project.
  const older = useDecisionDetail(open && decisions.data && !inList ? open : null);
  const selected = inList ?? (older.data && older.data.id === open && older.data.project_id === id ? older.data : null);
  const pending = items.filter((d) => d.effective_state === "proposed").length;
  const columns: Column<ListItem>[] = [
    { key: "type", header: "Decision", sortValue: (d) => d.decision_type, render: (d) => typeLabel(d.decision_type) },
    { key: "state", header: "State", sortValue: (d) => d.effective_state, render: (d) => <Pill tone={STATUS_TONE[d.effective_state] ?? "gray"}>{d.effective_state}</Pill> },
    { key: "actor", header: "Made by", render: (d) => <Pill tone={ACTOR_TONE[d.actor.kind] ?? "gray"}>{ACTOR_LABEL[d.actor.kind] ?? d.actor.kind}</Pill> },
    { key: "subject", header: "About", render: (d) => <>{d.subject.kind.replaceAll("_", " ")} <span className="mono">{d.subject.id.slice(0, 8)}</span></> },
    { key: "recorded", header: "Recorded", sortValue: (d) => d.recorded_at, render: (d) => formatWhen(d.recorded_at) },
    { key: "open", header: "Detail", render: (d) => <button type="button" className="btn" onClick={() => setOpen(d.id)}>{d.effective_state === "proposed" ? "Review" : "Open"}<span className="sr-only"> {typeLabel(d.decision_type)} {d.id.slice(0, 8)}</span></button> },
  ];
  return (
    <>
      <PageHead title="History" subtitle="A plain log of every change to this project: who or what made it (you, the rules or a connected tool), what it was about, and when. Corrections add a new entry; nothing is deleted." actions={<Link className="btn" href={`/projects/${id}/graph`}>Lineage</Link>} />
      <PageGuide
        purpose="See how the project got to where it is, and answer suggestions that wait for you."
        howTo={<>Each row is one <Term definition="A saved note of a choice (for example putting a model in use), with who made it and the evidence. Corrections add a new entry; nothing is edited or deleted.">History entry</Term>. Open one to see its evidence and reason. A suggestion waits for you: Accept or Reject it. An accepted entry can be corrected with a reason.</>}
        youGet="The newest 100 entries, newest first, with entries made by the rules, by connected tools and by people marked separately."
        attention={pending ? `${pending} suggestion${pending === 1 ? " is" : "s are"} waiting for your answer.` : undefined}
      />
      {decisions.isError ? <QueryNotice error={decisions.error} what="decision list" /> : null}
      {decisions.isPending ? <p role="status">Loading History…</p> : null}
      {decisions.data ? (
        <div className={selected ? "grid cols-2" : undefined}>
          <DataTable caption="History" columns={columns} rows={items} rowKey={(d) => d.id} highlightRow={(d) => d.id === open} emptyMessage="Nothing has happened in this project yet." />
          {selected ? <DecisionDrawer key={selected.id} projectId={id} summary={selected} onClose={() => setOpen(null)} /> : null}
        </div>
      ) : null}
    </>
  );
}

export default function DecisionsPage() {
  return <Suspense fallback={<p role="status">Loading…</p>}><DecisionsPageInner /></Suspense>;
}
