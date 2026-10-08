"use client";

/**
 * Pipeline evidence (P4.17-UI): the engine's typed stages for one run, each with the deterministic result the API
 * returns, its digests, the checks that ran, links to its decision records, and the AI notes recorded for it
 * (rule answer beside AI answer). Works with AI off: the AI parts then say "none recorded".
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AgentRuns, Artifacts, DecisionPoints, DigestLine, PIPELINE_TERMS, Provenance } from "@/app/components/studio-app/PipelineParts";
import { QueryNotice, STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { KeyValue } from "@/components/studio/KeyValue";
import { Level } from "@/components/studio/Level";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill, type PillTone } from "@/components/studio/Pill";
import { Stat } from "@/components/studio/Stat";
import { Term } from "@/components/studio/Term";
import { downloadModelBuildReproduction, useProjectExperiments, useStudioExperiment } from "@/lib/application";
import { plainText, projectHref } from "@/lib/application/command-search";
import { DECISION_TYPE_LABEL } from "@/lib/application/studio-compare";
import { useExperimentFindings } from "@/lib/application/studio-data-hooks";
import { useProjectProposals } from "@/lib/application/studio-inspect-hooks";
import { proposalsFor, reviewView } from "@/lib/application/studio-inspect";
import {
  checkStatusLabel, checkTotals, checksByStage, costLabel, decisionHref, decisionPointRows, durationLabel, orderStages, pipelineHref, pointsForStage,
  provenanceRows, runDurationMs, runsForExperiment, stageCounts, stageDigests, stageRecords, stageResult, stageTone, switcherRuns,
  type CheckRow, type PointRow, type RecordLike,
} from "@/lib/application/studio-pipeline";
import { useRunAgentRuns, useRunArtifacts, useRunBuild, useRunEvents, useRunRecords, type RunBuild } from "@/lib/application/studio-pipeline-hooks";

const CHECK_TONE: Record<string, PillTone> = { pass: "ok", warn: "warn", fail: "crit", other: "gray" };
const TONE: Record<string, PillTone> = { ok: "ok", crit: "crit", ai: "ai", gray: "gray" };
type Review = ReturnType<typeof reviewView> & { id: string; created: string };

function Checks({ rows }: { rows: CheckRow[] }) {
  if (!rows.length) return null;
  return (
    <ul className="plain-list tight" aria-label="Checks that ran">
      {rows.map((c) => (
        <li key={c.id} data-check-status={c.status}>
          <Pill tone={CHECK_TONE[c.status]}>{checkStatusLabel(c.status)}</Pill> <b>{plainText(c.label, 80)}</b>
          <span className="muted"> {plainText(c.text, 400)}</span>
        </li>
      ))}
    </ul>
  );
}

function PointNote({ projectId, row }: { projectId: string; row: PointRow }) {
  const href = row.recordId ? decisionHref(projectId, row.recordId) : null;
  return (
    <div className="stage-note ai" data-testid="ai-note">
      <p className="toolbar">
        <Pill tone="ai">{row.crossCheck ? "AI cross-check" : row.when === "after" ? "AI-after" : "AI-before"}</Pill>
        <span className="mono">{plainText(row.key, 60)}</span>
        {row.level !== null ? <Level level={row.level} /> : null}
        {href ? <Link href={href} data-testid="decision-link">Decision record <span className="mono">{row.recordId!.slice(0, 8)}</span></Link> : null}
      </p>
      {row.aiOff
        ? <p>AI was off for this point: the rule&apos;s answer was used. No AI answer exists.{row.reason ? <span className="muted"> ({plainText(row.reason.replaceAll("_", " "), 120)})</span> : null}</p>
        : !row.recordId
          ? <p>No record was written for this point, so the rule&apos;s answer was used.{row.reason ? <span className="muted"> ({plainText(row.reason.replaceAll("_", " "), 120)})</span> : null}</p>
          : <p>Rule answer and AI answer are recorded side by side{row.agreement ? <>; agreement: <b>{plainText(row.agreement.replaceAll("_", " "), 40)}</b></> : null}. The value the stage used is in the table below.</p>}
    </div>
  );
}

function CriticNote({ reviews }: { reviews: Review[] }) {
  if (!reviews.length) return <div className="stage-note"><p className="muted">Critic review: none recorded (AI off, or the Critic has not reviewed this run).</p></div>;
  return (
    <>
      {reviews.map((v) => (
        <div key={v.id} className="stage-note ai" data-testid="critic-note">
          <p className="toolbar">
            <Pill tone="ai">AI-after</Pill> <b>Critic review</b>
            {v.level !== null ? <Level level={v.level} /> : null}
            <span className="muted">{v.status} · {formatWhen(v.created)}</span>
          </p>
          <table className="graph-answers">
            <caption className="sr-only">Rule answer beside the Critic answer</caption>
            <thead><tr><th scope="col">Rule (deterministic)</th><th scope="col">Critic (AI, advisory)</th></tr></thead>
            <tbody><tr><td>{v.ruleAnswer ? <span className="mono">{plainText(v.ruleAnswer, 300)}</span> : "No rule answer recorded"}</td><td>{v.verdict ? plainText(v.verdict.replaceAll("_", " "), 60) : "no verdict"}</td></tr></tbody>
          </table>
        </div>
      ))}
    </>
  );
}

function StageCard({ projectId, stage, checks, records, points, reviews }: {
  projectId: string; stage: RunBuild["stages"][number]; checks: CheckRow[]; records: RecordLike[]; points: PointRow[]; reviews: Review[] | null;
}) {
  const result = stageResult(stage);
  const digests = stageDigests(stage);
  const evidence = new Map<string, number>();
  for (const ref of stage.evidence_references ?? []) evidence.set(ref.entity_type, (evidence.get(ref.entity_type) ?? 0) + 1);
  const rows = stage.rows_in != null || stage.rows_out != null ? `${stage.rows_in ?? "—"} rows in, ${stage.rows_out ?? "—"} out` : null;
  return (
    <li data-testid="stage-card" data-stage={stage.key}>
      <Card as="article" title={<><span className="mono muted">{stage.sequence}</span> {plainText(stage.title, 80)}</>} aside={<><Pill tone={TONE[stageTone(stage.status)]}>{plainText(stage.status.replaceAll("_", " "), 24)}</Pill> {durationLabel(stage.duration_ms)}</>}>
        {stage.decision_summary ? <p>{plainText(stage.decision_summary, 600)}</p> : <p className="muted">This stage recorded no summary.</p>}
        {stage.reason ? <p className="muted">Why: {plainText(stage.reason, 400)}</p> : null}
        {rows ? <p className="muted">{rows}</p> : null}
        <div className="stage-note rule"><p><Pill tone="det">deterministic</Pill> The result below is computed by code, not by an AI.</p></div>
        {result.locked ? (
          <p className="muted">The final test set is scored once, after the winner is locked. Its values are not shown on this page; see the model card for the labelled final evaluation.</p>
        ) : result.rows.length ? (
          <KeyValue items={result.rows.map((r) => ({ key: r.key, label: plainText(r.label, 60), value: <span className="mono">{plainText(r.value, 300)}</span> }))} />
        ) : <p className="muted">No configuration values were recorded for this stage.</p>}
        {result.more ? <p className="muted small">and {result.more} more recorded field{result.more === 1 ? "" : "s"}.</p> : null}
        {digests.length ? <div aria-label="Artifact digests">{digests.map((d, i) => <DigestLine key={`${d.label}-${i}`} label={d.label} value={d.value} />)}</div> : null}
        {evidence.size ? <p className="muted small">Evidence rows: {[...evidence].map(([type, n]) => `${n} ${type.replaceAll("_", " ")}`).join(", ")}.</p> : null}
        <Checks rows={checks} />
        {records.map((r) => {
          const href = decisionHref(projectId, r.id);
          return href ? <p key={r.id}><Link href={href} data-testid="decision-link">Decision record: {DECISION_TYPE_LABEL[r.decision_type] ?? r.decision_type.replaceAll("_", " ")} <span className="mono">{r.id.slice(0, 8)}</span></Link></p> : null;
        })}
        {points.map((p) => <PointNote key={p.key} projectId={projectId} row={p} />)}
        {reviews ? <CriticNote reviews={reviews} /> : null}
      </Card>
    </li>
  );
}

export function PipelineEvidence({ projectId, experimentId }: { projectId: string; experimentId: string }) {
  const router = useRouter();
  const experiment = useStudioExperiment(experimentId);
  const build = useRunBuild(experimentId);
  const live = build.data ? !["completed", "failed", "skipped", "cancelled", "canceled"].includes(build.data.pipeline_run_status.toLowerCase()) : false;
  const events = useRunEvents(experimentId, live);
  const winnerId = build.data?.stages.find((s) => s.key === "winner_lock")?.configuration?.selected_candidate_id;
  const records = useRunRecords(projectId, [
    { kind: "experiment", id: experimentId },
    { kind: "split_plan", id: experiment.data?.lineage.split_plan_id },
    { kind: "candidate", id: typeof winnerId === "string" ? winnerId : null },
  ]);
  const artifacts = useRunArtifacts(experimentId);
  
  const agentRuns = useRunAgentRuns(projectId);
  const findings = useExperimentFindings(experimentId);
  const proposals = useProjectProposals(projectId, "ExperimentReviewProposal");
  const siblings = useProjectExperiments(projectId);

  if (experiment.isPending || build.isPending) return <p role="status">Loading the pipeline…</p>;
  if (experiment.isError) return <QueryNotice error={experiment.error} what="experiment" />;
  if (build.isError) return <QueryNotice error={build.error} what="pipeline evidence" />;
  const run = experiment.data;
  if (run.project_id !== projectId) {
    const other = projectHref(run.project_id, "pipeline");
    return <Banner tone="warn">This run belongs to another project.{other ? <> <Link href={other}>Open the pipeline list of its project</Link></> : null}</Banner>;
  }
  const b = build.data;
  const stages = orderStages(b.stages);
  const counts = stageCounts(stages);
  const recordItems: RecordLike[] = records.items;
  const points = decisionPointRows(events.data?.items ?? [], recordItems);
  const { byStage, loose } = checksByStage(stages, findings.data);
  const allChecks = [...byStage.values()].flat().concat(loose);
  const totals = checkTotals(allChecks);
  const runs = runsForExperiment(agentRuns.data?.items ?? [], experimentId);
  const cost = costLabel(runs);
  const reviews: Review[] = proposalsFor(proposals.data?.items ?? [], "ExperimentReviewProposal", "experiment", experimentId).map((p) => ({ ...reviewView(p), id: p.id, created: p.created_at }));
  const duration = runDurationMs(run.started_at, run.ended_at);
  const switcher = switcherRuns(siblings.data?.items ?? [], experimentId);
  const reviewStage = pointsForStage(points, "deterministic_verification").length || stages.some((s) => s.key === "deterministic_verification");
  const provenance = provenanceRows({ build: b, stages, lineage: run.lineage, eventCount: events.data?.items.length ?? 0, formatWhen });
  const reproduction: Array<{ kind: "script" | "notebook"; label: string; run: () => Promise<void> }> = [
    { kind: "script", label: "Download reproduction script", run: () => downloadModelBuildReproduction(b.workspace_id, b.pipeline_run_id, "script") },
    { kind: "notebook", label: "Download reproduction notebook", run: () => downloadModelBuildReproduction(b.workspace_id, b.pipeline_run_id, "notebook") },
  ];
  return (
    <>
      <PageHead
        title="Run evidence"
        eyebrow={<>Run <span className="mono">{experimentId.slice(0, 8)}</span></>}
        badges={<Pill tone={STATUS_TONE[run.status] ?? "gray"}>{run.status.replaceAll("_", " ")}</Pill>}
        subtitle={run.intent ? plainText(run.intent, 300) : "Every step of this run with its result, fingerprints, checks and History entries."}
        actions={(
          <>
            <label className="sr-only" htmlFor="pipeline-run-switch">Switch run</label>
            <select id="pipeline-run-switch" className="input" value={experimentId} onChange={(event) => { const href = pipelineHref(projectId, event.target.value); if (href) router.push(href); }}>
              {switcher.map((r) => <option key={r.id} value={r.id}>{plainText(r.intent || `Run ${r.id.slice(0, 8)}`, 50)} · {r.status.replaceAll("_", " ")}</option>)}
            </select>
            <Link className="btn" href={`/projects/${projectId}/experiments/${experimentId}`}>Open experiment</Link>
          </>
        )}
      />
      <PageGuide
        purpose={<>The evidence behind one run: what each <Term definition={PIPELINE_TERMS.stage}>stage</Term> did, in order, and how to verify it.</>}
        howTo={<>Read down the stages. Each shows its <Term definition={PIPELINE_TERMS.deterministic}>deterministic</Term> result, its <Term definition={PIPELINE_TERMS.digest}>digests</Term>, the checks that ran and the decision records it produced. AI notes appear only where a record exists.</>}
        youGet="Stage results, artifact digests, checks, decision points (AI answer beside rule answer), provenance, and a replay of the agent runs."
        attention={totals.fail || totals.warn ? `${totals.fail} failed and ${totals.warn} warning check${totals.warn === 1 ? "" : "s"} on this run.` : counts.failed ? `${counts.failed} stage${counts.failed === 1 ? "" : "s"} failed.` : undefined}
      />
      {live ? <Banner tone="info">This run is still in progress. Stages update every few seconds.</Banner> : null}
      {b.compatibility_fallback_used ? <Banner tone="warn">Some stages were read from the older run record, so a stage may show fewer details.</Banner> : null}
      <div className="grid cols-4" aria-label="Run summary">
        <Stat value={`${counts.completed} / ${counts.total}`} label="stages completed" hint={b.pipeline_run_status.replaceAll("_", " ")} />
        <Stat value={durationLabel(duration)} label="run time" hint={duration === null ? "not finished" : undefined} />
        <Stat value={cost.cost} label="AI cost" hint={cost.calls} />
        <Stat value={totals.total ? `${totals.pass} / ${totals.total}` : "none yet"} label="checks passed" hint={totals.total ? `${totals.warn} warning · ${totals.fail} failed · ${totals.other} not evaluated` : "checks are stored when a run finishes"} />
      </div>
      {counts.total === 0 ? <div className="empty" role="note">The engine has not recorded any stage for this run yet.</div> : null}
      <ol className="stage-list" aria-label="Stages in run order">
        {stages.map((stage) => (
          <StageCard
            key={`${stage.sequence}-${stage.key}`} projectId={projectId} stage={stage}
            checks={byStage.get(stage.key) ?? []} records={stageRecords(stage.key, recordItems)} points={pointsForStage(points, stage.key)}
            reviews={stage.key === "deterministic_verification" && reviewStage ? reviews : null}
          />
        ))}
      </ol>
      {loose.length ? <Card title="Other checks on this run"><Checks rows={loose} /></Card> : null}
      {events.isError ? <QueryNotice error={events.error} what="pipeline events" /> : null}
      {records.isError ? <QueryNotice error={records.error} what="decision records" /> : null}
      <DecisionPoints projectId={projectId} rows={points} pending={events.isPending || records.isPending} />
      <AgentRuns runs={runs} pending={agentRuns.isPending} error={agentRuns.isError ? agentRuns.error : null} />
      <Artifacts workspaceId={b.workspace_id} artifacts={artifacts.data ?? []} pending={artifacts.isPending} error={artifacts.isError ? artifacts.error : null} reproduction={reproduction} />
      <Provenance rows={provenance} aiCost={cost} />
    </>
  );
}
