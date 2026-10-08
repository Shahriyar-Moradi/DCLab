"use client";

/**
 * Lower sections of the Pipeline evidence page (P4.17-UI): decision points, agent runs with Replay,
 * artifacts with downloads, cost and provenance. Untrusted text is plain text; links are same-origin and UUID-checked.
 */
import Link from "next/link";
import { useRef, useState } from "react";
import { CopyButton } from "@/app/components/studio-app/CopyButton";
import { QueryNotice, formatWhen } from "@/app/components/studio-app/StudioParts";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { KeyValue } from "@/components/studio/KeyValue";
import { Level } from "@/components/studio/Level";
import { Pill } from "@/components/studio/Pill";
import { Term } from "@/components/studio/Term";
import { plainText } from "@/lib/application/command-search";
import { safeFilename } from "@/lib/application/studio-inspect";
import { downloadArtifactBlob, replayAgentRun, type RunAgentRun, type RunArtifact } from "@/lib/application/studio-pipeline-hooks";
import {
  canDownloadArtifact, canReplay, costLabel, decisionHref, replayErrorText, replayView, sizeLabel, answerSummary,
  type PointRow, type ProvenanceRow, type ReplayView,
} from "@/lib/application/studio-pipeline";
import { ActionKeys } from "@/lib/application/studio-wizard";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

export const PIPELINE_TERMS = {
  digest: "A fingerprint of the exact content (a hash). If one byte changes, the digest changes, so equal digests mean identical files.",
  stage: "One step the engine runs in a fixed order, such as profiling the data or locking the final test set. Each stage records what it did.",
  deterministic: "Decided by code that gives the same answer every time for the same inputs. No AI model is involved.",
  decisionPoint: "A place where an AI answer could be compared with the rule's answer. The level says how much the AI may do: Shadow, the rules decided and the AI's answer is only recorded; Ask first, a person accepts it; Automatic, you can undo, it is applied and one click undoes it; Automatic, by policy.",
  replay: "Runs the agent again from its stored record with a stand-in model and stubbed tools, and checks it produces the same tool calls, output digest and proposals.",
};

export function DigestLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="digest-row">
      <span className="muted">{label}</span>
      <span className="mono" data-testid="digest">{plainText(value, 200)}</span>
      <CopyButton text={value} label={`Copy ${label} digest`} />
    </div>
  );
}

export function AnswerTable({ row }: { row: PointRow }) {
  if (!row.answers.length) return null;
  return (
    <details>
      <summary>Rule answer beside AI answer ({row.answersTotal ?? row.answers.length} column{(row.answersTotal ?? row.answers.length) === 1 ? "" : "s"})</summary>
      <table className="graph-answers">
        <caption className="sr-only">Rule answer beside AI answer for {row.key}</caption>
        <thead><tr><th scope="col">Column</th><th scope="col">Rule</th><th scope="col">AI</th><th scope="col">Used</th></tr></thead>
        <tbody>{row.answers.map((a, i) => <tr key={`${a.column}-${i}`}><td className="mono">{plainText(a.column, 60)}</td><td>{plainText(a.rule, 40)}</td><td>{plainText(a.ai, 40)}</td><td>{plainText(a.used, 40)}</td></tr>)}</tbody>
      </table>
    </details>
  );
}

export function DecisionPoints({ projectId, rows, pending }: { projectId: string; rows: PointRow[]; pending: boolean }) {
  const columns: Column<PointRow>[] = [
    { key: "point", header: "Decision point", sortValue: (r) => r.key, render: (r) => <span className="mono">{plainText(r.key, 60)}</span> },
    { key: "ai", header: "AI answer", render: (r) => (r.aiOff ? <Pill tone="gray">AI off</Pill> : <>{plainText(answerSummary(r, "ai"), 80)}<AnswerTable row={r} /></>) },
    { key: "rule", header: "Rule answer", render: (r) => plainText(answerSummary(r, "rule"), 80) },
    { key: "used", header: "Value used", render: (r) => plainText(answerSummary(r, "used"), 80) },
    { key: "level", header: "Level", render: (r) => (r.level === null ? "—" : <Level level={r.level} />) },
    { key: "who", header: "Decided by", render: (r) => <><Pill tone={r.actor === "agent" ? "ai" : "det"}>{r.actor === "agent" ? "agent" : r.actor === "human" ? "person" : "rule"}</Pill>{r.agreement ? <span className="muted"> {plainText(r.agreement.replaceAll("_", " "), 40)}</span> : null}</> },
    {
      key: "record", header: "Record",
      render: (r) => {
        const href = r.recordId ? decisionHref(projectId, r.recordId) : null;
        return href ? <Link href={href} data-testid="decision-link">Open record <span className="mono">{r.recordId!.slice(0, 8)}</span></Link> : <span className="muted">none written{r.aiOff ? " (AI off)" : ""}</span>;
      },
    },
  ];
  return (
    <Card title={<Term definition={PIPELINE_TERMS.decisionPoint}>Decision points in this run</Term>} aside={`${rows.length} point${rows.length === 1 ? "" : "s"}`}>
      <p className="muted">The AI never decides alone here: each row shows its answer next to the rule&apos;s answer and which value the stage used. With AI off, the rule answer is used and no AI answer exists.</p>
      {pending ? <p role="status">Loading decision points…</p> : null}
      <DataTable caption="Decision points in this run" columns={columns} rows={rows} rowKey={(r) => r.key} emptyMessage="No decision points were recorded for this run." />
    </Card>
  );
}

function ReplayResultView({ view }: { view: ReplayView }) {
  return (
    <div role="status" data-testid="replay-result">
      <Banner tone={view.tone === "crit" ? "crit" : view.tone === "warn" ? "warn" : "info"}><b>{view.title}.</b> {view.detail}</Banner>
      {view.reasons.length ? <ul className="plain-list tight">{view.reasons.map((reason, i) => <li key={i}>{plainText(reason, 300)}</li>)}</ul> : null}
      {view.tools.length ? <p className="muted small">Tool calls replayed: {view.tools.map((t) => `${plainText(t.tool, 60)} (${plainText(t.digest, 20)}…)`).join(", ")}</p> : null}
    </div>
  );
}

export function AgentRuns({ runs, pending, error }: { runs: RunAgentRun[]; pending: boolean; error: unknown }) {
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(new Set<string>());
  const [busy, setBusy] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, { view?: ReplayView; error?: { title: string; detail: string } }>>({});
  const cost = costLabel(runs);

  const replay = async (runId: string) => {
    if (flight.current.has(runId)) return;
    flight.current.add(runId);
    setBusy(runId);
    try {
      const body = await replayAgentRun({ runId, key: keys.keyFor(`replay:${runId}`, runId) });
      keys.done(`replay:${runId}`);
      setResults((all) => ({ ...all, [runId]: { view: replayView(body) } }));
    } catch (caught) {
      setResults((all) => ({ ...all, [runId]: { error: replayErrorText(caught) } }));
    } finally {
      flight.current.delete(runId);
      setBusy(null);
    }
  };

  return (
    <Card title="AI agent runs on this run" aside={`${cost.calls} · ${cost.cost}`}>
      <p className="muted">
        Agents only advise. Each run below is stored with its tool calls and output digest, so you can <Term definition={PIPELINE_TERMS.replay}>replay</Term> it
        and check that it reproduces. Replay changes nothing in the project.
      </p>
      {error ? <QueryNotice error={error} what="agent runs" /> : null}
      {pending ? <p role="status">Loading agent runs…</p> : null}
      {!pending && !error && runs.length === 0 ? <div className="empty" role="note">No AI runs are recorded for this run (AI was off, or no agent reviewed it). Everything on this page works without them.</div> : null}
      {runs.map((run) => {
        const result = results[run.id];
        return (
          <article key={run.id} className="card flat" aria-label={`Agent run ${run.agent_key} ${run.id.slice(0, 8)}`} data-testid="agent-run">
            <p className="toolbar">
              <Pill tone="ai"><span aria-hidden="true">◆ </span>{plainText(run.agent_key.replaceAll("_", " "), 60)}</Pill>
              <span className="muted">{plainText(run.status, 24)} · {formatWhen(run.created_at)} · cost {(run.cost_micros / 1_000_000).toFixed(4)} {plainText(run.currency, 6)} · run <span className="mono">{run.id.slice(0, 8)}</span></span>
              {canReplay(run) ? (
                <button type="button" className="btn sm" disabled={busy !== null} aria-busy={busy === run.id} onClick={() => void replay(run.id)}>
                  {busy === run.id ? "Replaying…" : "Replay AI"}<span className="sr-only"> run {run.id.slice(0, 8)}</span>
                </button>
              ) : <span className="muted">Replay is available once the run has finished.</span>}
            </p>
            {result?.view ? <ReplayResultView view={result.view} /> : null}
            {result?.error ? <Banner tone="warn"><b>{result.error.title}.</b> {result.error.detail}</Banner> : null}
          </article>
        );
      })}
    </Card>
  );
}

export function Artifacts({ workspaceId, artifacts, pending, error, reproduction }: {
  workspaceId: string; artifacts: RunArtifact[]; pending: boolean; error: unknown;
  reproduction: Array<{ kind: "script" | "notebook"; label: string; run: () => Promise<void> }>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const saveBlob = (blob: Blob, filename: string) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const run = async (id: string, work: () => Promise<void>) => {
    if (busy) return;
    setBusy(id);
    setFailure(null);
    try { await work(); } catch (caught) { setFailure(caught instanceof Error ? caught.message : "The download failed."); } finally { setBusy(null); }
  };
  const columns: Column<RunArtifact>[] = [
    { key: "type", header: "Artifact", sortValue: (a) => a.artifact_type, render: (a) => plainText(a.artifact_type.replaceAll("_", " "), 40) },
    { key: "digest", header: "Digest", render: (a) => <DigestLine label={plainText(a.artifact_type, 30)} value={a.content_digest} /> },
    { key: "size", header: "Size", numeric: true, render: (a) => sizeLabel(a.size_bytes) },
    {
      key: "get", header: "Download",
      render: (a) => (canDownloadArtifact(a)
        ? <button type="button" className="btn sm" disabled={busy !== null} onClick={() => void run(a.id, async () => { const { blob, filename } = await downloadArtifactBlob(workspaceId, a.id); saveBlob(blob, safeFilename(filename, `${a.artifact_type}-${a.id.slice(0, 8)}`)); })}>Download<span className="sr-only"> {a.artifact_type.replaceAll("_", " ")}</span></button>
        : <span className="muted">digest only</span>),
    },
  ];
  return (
    <Card title="Artifacts and downloads" aside={`${artifacts.length} stored`}>
      <p className="muted">Every stored output of this run, named by its digest. There is no single bundle download: download the pieces you need. Model files, prediction files and run reports are listed by digest only; the model page and the run report open them with their labels.</p>
      <div className="toolbar">
        {reproduction.map((item) => <button key={item.kind} type="button" className="btn" disabled={busy !== null} onClick={() => void run(item.kind, item.run)}>{item.label}</button>)}
      </div>
      {error ? <QueryNotice error={error} what="artifacts" /> : null}
      {pending ? <p role="status">Loading artifacts…</p> : null}
      {failure ? <Banner tone="crit">Could not download. {plainText(failure, 200)}</Banner> : null}
      {!pending && !error ? <DataTable caption="Artifacts of this run" columns={columns} rows={artifacts} rowKey={(a) => a.id} emptyMessage="No artifacts are stored for this run yet." /> : null}
    </Card>
  );
}

export function Provenance({ rows, aiCost }: { rows: ProvenanceRow[]; aiCost: { cost: string; calls: string } }) {
  return (
    <Card title="Cost and provenance">
      <p className="muted">Only fields the API returns for this run. A field that was not recorded is not shown.</p>
      <KeyValue items={[
        ...rows.map((r) => ({ key: r.key, label: r.label, value: r.mono ? <span className="mono">{plainText(r.value, 200)}</span> : plainText(r.value, 200) })),
        { key: "ai-cost", label: "AI cost", value: `${aiCost.cost} (${aiCost.calls})` },
      ]} />
    </Card>
  );
}
