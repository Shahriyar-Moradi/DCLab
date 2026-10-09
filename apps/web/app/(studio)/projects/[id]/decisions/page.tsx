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
import { plainText, projectHref } from "@/lib/application/command-search";
import { useProjectDecisions, useProjectExperiments, useProjectRefs, useSession, type StudioDecisionItem } from "@/lib/application";
import { useProjectGraph } from "@/lib/application/studio-data-hooks";
import { graphNodeNames } from "@/lib/application/studio-graph";
import { correctionFor, evidenceHref, evidenceName, stateWords, subjectName, whatWords, whoWords } from "@/lib/application/studio-history";
import {
  acceptRefProposal, resolveDecision, supersedeDecision, useDecisionDetail, useWriteInvalidation, type StudioDecisionDetail,
} from "@/lib/application/studio-compare-hooks";
import { ENGINE_OWNED_TYPES, acceptsViaRefs, mapActionError, proposedMoves } from "@/lib/application/studio-compare";
import { decisionMarkers } from "@/lib/application/studio-graph";
import { parseRecordParam } from "@/lib/application/studio-pipeline";
import { evidenceScopeLabel } from "@/lib/application/studio-names";
import { plainWords } from "@/lib/application/studio-model";
import { metricInfo } from "@/lib/application/studio-goal";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";
import { ActionKeys, type PlainError } from "@/lib/application/studio-wizard";
import { CAPABILITIES, hasCapability } from "@/lib/infrastructure/capabilities";

const ACTOR_TONE: Record<string, PillTone> = { rule: "det", agent: "ai", human: "gray" };
const NO_WRITE = "Only people who can change this workspace can answer, correct or score files.";
const actorTone = (kind: string): PillTone => (Object.hasOwn(ACTOR_TONE, kind) ? ACTOR_TONE[kind] : "gray");
const stateTone = (state: string): PillTone => (Object.hasOwn(STATUS_TONE, state) ? STATUS_TONE[state] : "gray");

type ListItem = StudioDecisionItem;
type Mode = "accept" | "reject" | "supersede";

function DecisionDrawer({ projectId, summary, names, myUserId, canWrite, initialMode, onClose }: {
  projectId: string; summary: ListItem; names: ReadonlyMap<string, string>; myUserId: string | null; canWrite: boolean; initialMode: Mode | null; onClose: () => void;
}) {
  const detail = useDecisionDetail(summary.id);
  const refs = useProjectRefs(projectId);
  const invalidate = useWriteInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const [mode, setMode] = useState<Mode | null>(initialMode);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const d: StudioDecisionDetail | undefined = detail.data;
  const markers = useMemo(() => (d ? [...decisionMarkers([{ ...d, subject: d.subject, details: d.details }]).values()].flat() : []), [d]);
  const state = d?.effective_state ?? summary.effective_state;
  const type = d?.decision_type ?? summary.decision_type;
  const proposed = state === "proposed";
  const correction = correctionFor(type, state);
  const canSupersede = correction.kind === "correct" && !ENGINE_OWNED_TYPES.has(type);
  const note = correction.kind === "note" ? correction : null;
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
      setDone(mode === "accept" ? "Accepted: a new entry was added." : mode === "reject" ? "Rejected: a new entry was added." : "Corrected: a new entry replaces this one.");
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
        {whatWords(type)}
        <button type="button" className="btn sp" onClick={onClose}>Close<span className="sr-only"> this entry</span></button>
      </h2>
      {detail.isError ? <QueryNotice error={detail.error} what="History entry" /> : null}
      {!d && detail.isPending ? <p role="status">Loading the entry…</p> : null}
      {done ? <Banner tone="info">{done}</Banner> : null}
      {d ? (
        <>
          <KeyValue items={[
            { key: "state", label: "Status", value: <Pill tone={stateTone(state)}>{stateWords(state)}</Pill> },
            { key: "actor", label: "Who", value: whoWords(d.actor, myUserId) },
            { key: "subject", label: "About", value: subjectName(d.subject, names) },
            { key: "when", label: "When", value: formatWhen(d.recorded_at) },
            { key: "why", label: "Reason", value: <span>{plainText(d.actor.kind === "rule" ? plainWords(d.rationale) : d.rationale, 1000) || "—"} <span className="muted">({d.rationale_untrusted ? "written by a connected tool or the assistant; treat it as text" : "recorded with the entry"})</span></span> },
            { key: "chain", label: "Corrections", value: <>{d.supersedes_id ? "This entry corrects an earlier one. " : null}{d.superseded_by_id ? "A later entry corrects this one." : null}{!d.supersedes_id && !d.superseded_by_id ? "None" : null}</> },
            { key: "ref", label: "Reference", value: <span className="mono">{d.id}</span> },
          ]} />
          <h3>Evidence</h3>
          {d.evidence_refs?.length ? (
            <ul className="plain-list">{d.evidence_refs.map((e) => {
              const href = evidenceHref(projectId, e);
              const label = evidenceName(e, names);
              return <li key={`${e.kind}-${e.id}-${e.scope ?? ""}`}>{href ? <Link href={href}>{label}</Link> : label}{e.scope ? `: ${evidenceScopeLabel(e.scope)}` : ""}{e.metric ? `, ${metricInfo(e.metric)?.label ?? plainText(e.metric.replaceAll("_", " "), 40)}` : ""}</li>;
            })}</ul>
          ) : <p className="muted">No evidence is recorded on this entry.</p>}
          {markers.length ? <h3>The rules&apos; answer beside the assistant&apos;s answer</h3> : null}
          {markers.map((m) => (
            <table key={m.id} className="graph-answers">
              <caption className="sr-only">The rules&apos; answer beside the assistant&apos;s answer</caption>
              <thead><tr><th scope="col">Column</th><th scope="col">Rule</th><th scope="col">AI</th><th scope="col">Used</th></tr></thead>
              <tbody>{m.answers.map((a, i) => <tr key={`${a.column}-${i}`}><td className="mono">{plainText(a.column, 60)}</td><td>{plainText(a.rule, 40)}</td><td>{plainText(a.ai, 40)}</td><td>{plainText(a.used, 40)}</td></tr>)}</tbody>
            </table>
          ))}
          {d.details_truncated ? <p className="muted">The entry was too large to show in full.</p> : null}
          {(proposed || canSupersede) && !canWrite ? <p className="muted">{NO_WRITE}</p> : null}
          {(proposed || canSupersede) && canWrite ? (
            <>
              <h3>{proposed ? "Answer this suggestion" : "Correct this entry"}</h3>
              {!mode ? (
                <div className="toolbar">
                  {proposed ? <><button type="button" className="btn primary" onClick={() => { setMode("accept"); setError(null); setDone(null); }}>Accept<span className="sr-only"> this suggestion</span></button>
                    <button type="button" className="btn" onClick={() => { setMode("reject"); setError(null); setDone(null); }}>Reject<span className="sr-only"> this suggestion</span></button></> : null}
                  {canSupersede ? <button type="button" className="btn" onClick={() => { setMode("supersede"); setError(null); setDone(null); }}>Correct</button> : null}
                </div>
              ) : (
                <form className="form" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
                  <p className="muted">{mode === "supersede" ? "A correction adds a new entry about the same thing; this one stays, marked corrected. A reason is required." : "This adds a new entry; nothing is edited. A reason is required."}</p>
                  <label className="field"><span>Reason (recorded)</span><textarea rows={2} required maxLength={2000} value={reason} disabled={busy} onChange={(e) => setReason(e.target.value)} /></label>
                  <div className="toolbar">
                    <button type="button" className="btn" disabled={busy} onClick={() => setMode(null)}>Back</button>
                    <button type="submit" className="btn primary" disabled={busy || !reason.trim() || (mode === "accept" && acceptsViaRefs(d) && !refs.data)}>{busy ? "Saving…" : mode === "accept" ? "Confirm accept" : mode === "reject" ? "Confirm reject" : "Confirm correction"}</button>
                  </div>
                </form>
              )}
            </>
          ) : null}
          {note ? <p className="muted">{note.text}</p> : null}
          {error ? <Banner tone="crit" actions={<button type="button" className="btn" onClick={() => { invalidate(); setError(null); }}>Reload</button>}><b>{error.title}.</b> {error.detail}</Banner> : null}
        </>
      ) : null}
    </aside>
  );
}

function DecisionsPageInner() {
  const { id } = useParams<{ id: string }>();
  const decisions = useProjectDecisions(id);
  const graph = useProjectGraph(id);
  const runs = useProjectExperiments(id);
  const { user } = useSession();
  const canWrite = hasCapability(user, CAPABILITIES.workspaceExecuteMl);
  // `?record=<uuid>` (the Run evidence page's links) opens that entry; anything that is not a UUID is ignored.
  const linked = parseRecordParam(useSearchParams().get("record"));
  const [open, setOpen] = useState<{ id: string; mode: Mode | null } | null>(linked ? { id: linked, mode: null } : null);
  const items = decisions.data?.items ?? [];
  const names = useMemo(() => graphNodeNames(graph.data?.nodes ?? [], runs.data?.items ?? [], !!runs.data?.next_cursor || !runs.data, graph.data?.edges), [graph.data, runs.data]);
  const inList = items.find((d) => d.id === open?.id) ?? null;
  // An entry older than the newest 100 is read by id, and only shown when it belongs to this project.
  const older = useDecisionDetail(open && decisions.data && !inList ? open.id : null);
  const selected = inList ?? (older.data && older.data.id === open?.id && older.data.project_id === id ? older.data : null);
  const pending = items.filter((d) => d.effective_state === "proposed").length;
  const lineage = projectHref(id, "graph");
  const columns: Column<ListItem>[] = [
    { key: "recorded", header: "When", sortValue: (d) => d.recorded_at, render: (d) => formatWhen(d.recorded_at) },
    { key: "actor", header: "Who", render: (d) => <Pill tone={actorTone(d.actor.kind)}>{whoWords(d.actor, user?.id)}</Pill> },
    {
      key: "what", header: "What",
      render: (d) => (
        <>
          {whatWords(d.decision_type)}
          <span className="muted block">About {subjectName(d.subject, names)}</span>
          {d.effective_state !== "accepted" ? <Pill tone={stateTone(d.effective_state)}>{stateWords(d.effective_state)}</Pill> : null}
        </>
      ),
    },
    { key: "evidence", header: "Evidence", render: (d) => <button type="button" className="btn" onClick={() => setOpen({ id: d.id, mode: null })}>Open<span className="sr-only"> the evidence for: {whatWords(d.decision_type)}, {formatWhen(d.recorded_at)}</span></button> },
    {
      key: "undo", header: "Correct",
      render: (d) => {
        const c = correctionFor(d.decision_type, d.effective_state);
        if ((c.kind === "answer" || c.kind === "correct") && !canWrite) return <span className="muted">Needs permission to change this workspace</span>;
        if (c.kind === "answer" || c.kind === "correct") {
          return <button type="button" className={c.kind === "answer" ? "btn primary" : "btn"} onClick={() => setOpen({ id: d.id, mode: c.kind === "correct" ? "supersede" : null })}>{c.label}<span className="sr-only">: {whatWords(d.decision_type)}, {formatWhen(d.recorded_at)}</span></button>;
        }
        return c.kind === "note" ? <span className="muted">{c.text}</span> : <span className="muted">—</span>;
      },
    },
  ];
  return (
    <>
      <PageHead title="History" subtitle="A plain log of every change to this project: who made it (you, the rules, a connected tool or the assistant), what it was, and when. Corrections add a new entry; nothing is deleted." actions={lineage ? <Link className="btn" href={lineage}>Lineage</Link> : null} />
      <PageGuide
        purpose="See how the project got to where it is, and answer suggestions that wait for you."
        howTo={<>Each row is one <Term definition="A saved note of a choice (for example putting a model in use), with who made it and the evidence. Corrections add a new entry; nothing is edited or deleted.">History entry</Term>. Open its evidence to see the reason and what it was based on. A suggestion waits for your answer: Accept or Reject it. An entry a person recorded can be corrected with a reason; for anything else the row says where it is changed.</>}
        youGet="The newest 100 entries, newest first, with those made by the rules, by connected tools, by the assistant and by people marked separately."
        attention={pending ? `${pending} suggestion${pending === 1 ? " is" : "s are"} waiting for your answer.` : undefined}
      />
      {!canWrite && decisions.data ? <Banner tone="info">{NO_WRITE} You can still read every entry.</Banner> : null}
      {decisions.isError ? <QueryNotice error={decisions.error} what="History" /> : null}
      {decisions.isPending ? <p role="status">Loading History…</p> : null}
      {decisions.data ? (
        <div className={selected ? "grid cols-2" : undefined}>
          <DataTable caption="History" columns={columns} rows={items} rowKey={(d) => d.id} highlightRow={(d) => d.id === open?.id} emptyMessage="Nothing has happened in this project yet." />
          {selected ? <DecisionDrawer key={`${selected.id}:${open?.mode ?? ""}`} projectId={id} summary={selected} names={names} myUserId={user?.id ?? null} canWrite={canWrite} initialMode={open?.mode ?? null} onClose={() => setOpen(null)} /> : null}
        </div>
      ) : null}
    </>
  );
}

export default function DecisionsPage() {
  return <Suspense fallback={<p role="status">Loading…</p>}><DecisionsPageInner /></Suspense>;
}
