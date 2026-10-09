"use client";

/**
 * Pipeline evidence (P4.17-UI): the engine's typed stages for one run, each with the deterministic result the API
 * returns, its digests, the checks that ran, links to its decision records, and the AI notes recorded for it
 * (rule answer beside AI answer). Works with AI off: the AI parts then say "none recorded".
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AgentRuns, Artifacts, DecisionPoints, DigestLine, PIPELINE_TERMS, Provenance } from "@/app/components/studio-app/PipelineParts";
import { QueryNotice, formatWhen, statusTone } from "@/app/components/studio-app/StudioParts";
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
import { runName, runOrdinals } from "@/lib/application/studio-names";
import { pointLabel, stageStateWords, stageWords, statusWords } from "@/lib/application/studio-runs";
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
    <ul className="plain-list tight" aria-label="Trust checks of this step">
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
        <Pill tone="ai">{row.crossCheck ? "AI second opinion" : row.when === "after" ? "AI note after the step" : "AI suggestion before the step"}</Pill>
        <b>{pointLabel(row.key)}</b>
        {row.level !== null ? <Level level={row.level} /> : null}
        {href ? <Link href={href} data-testid="decision-link">History entry <span className="mono">{row.recordId!.slice(0, 8)}</span></Link> : null}
      </p>
      {!row.recordId
          ? <p>No History entry was written for this choice, so the rule&apos;s answer was used.{row.reason ? <span className="muted"> ({plainText(row.reason.replaceAll("_", " "), 120)})</span> : null}</p>
          : <p>The rule&apos;s answer and the AI&apos;s answer are saved side by side{row.agreement ? <>; they <b>{plainText(row.agreement.replaceAll("_", " "), 40)}</b></> : null}. The value the step used is in the table below.</p>}
    </div>
  );
}

function CriticNote({ reviews }: { reviews: Review[] }) {
  if (!reviews.length) return null;
  return (
    <>
      {reviews.map((v) => (
        <div key={v.id} className="stage-note ai" data-testid="critic-note">
          <p className="toolbar">
            <Pill tone="ai">AI note after the step</Pill> <b>AI reviewer</b>
            {v.level !== null ? <Level level={v.level} /> : null}
            <span className="muted">{v.status} · {formatWhen(v.created)}</span>
          </p>
          <table className="graph-answers">
            <caption className="sr-only">Rule answer beside the AI reviewer&apos;s answer</caption>
            <thead><tr><th scope="col">Rule&apos;s answer</th><th scope="col">AI reviewer (advice only)</th></tr></thead>
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
      <Card as="article" title={<><span className="mono muted">{stage.sequence}</span> {stageWords(stage.key, stage.title)}</>} aside={<><Pill tone={TONE[stageTone(stage.status)]}>{stageStateWords(stage.status)}</Pill> {durationLabel(stage.duration_ms)}</>}>
        {stage.decision_summary ? <p>{plainText(stage.decision_summary, 600)}</p> : <p className="muted">This step recorded no summary.</p>}
        {stage.reason ? <p className="muted">Why: {plainText(stage.reason, 400)}</p> : null}
        {rows ? <p className="muted">{rows}</p> : null}
        <div className="stage-note rule"><p><Pill tone="det">fixed rules</Pill> The result below is computed by code, not by an AI.</p></div>
        {result.locked ? (
          <p className="muted">The final test set is scored once per run, after the best model is chosen. Its values are not shown on this page; the model page shows the labelled final test.</p>
        ) : result.rows.length ? (
          <KeyValue items={result.rows.map((r) => ({ key: r.key, label: plainText(r.label, 60), value: <span className="mono">{plainText(r.value, 300)}</span> }))} />
        ) : <p className="muted">No values were recorded for this step.</p>}
        {result.more ? <p className="muted small">and {result.more} more recorded field{result.more === 1 ? "" : "s"}.</p> : null}
        {digests.length ? <div aria-label="Checksums">{digests.map((d, i) => <DigestLine key={`${d.label}-${i}`} label={d.label} value={d.value} />)}</div> : null}
        {evidence.size ? <p className="muted small">Records behind it: {[...evidence].map(([type, n]) => `${n} ${type.replaceAll("_", " ")}`).join(", ")}.</p> : null}
        <Checks rows={checks} />
        {records.map((r) => {
          const href = decisionHref(projectId, r.id);
          return href ? <p key={r.id}><Link href={href} data-testid="decision-link">History entry: {Object.hasOwn(DECISION_TYPE_LABEL, r.decision_type) ? DECISION_TYPE_LABEL[r.decision_type] : plainText(r.decision_type.replaceAll("_", " "), 60)} <span className="mono">{r.id.slice(0, 8)}</span></Link></p> : null;
        })}
        {points.filter((p) => !p.aiOff).map((p) => <PointNote key={p.key} projectId={projectId} row={p} />)}
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
  const siblingRuns = siblings.data?.items ?? [];
  const ordinals = runOrdinals(siblingRuns);
  const partial = !!siblings.data?.next_cursor;
  const nameOf = (r: { id: string; intent?: string | null }) => runName(partial ? undefined : ordinals.get(r.id), r.intent ? plainText(r.intent, 50) : null, partial || !ordinals.has(r.id) ? r.id.slice(0, 8) : undefined);
  const switcher = switcherRuns(siblingRuns, experimentId);
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
        eyebrow={<>{nameOf({ id: experimentId, intent: null })} · <span className="mono">{experimentId.slice(0, 8)}</span></>}
        badges={<Pill tone={statusTone(run.status)}>{statusWords(run.status)}</Pill>}
        subtitle={run.intent ? plainText(run.intent, 300) : "Every step of this run with its result, checks and History entries."}
        actions={(
          <>
            <label className="sr-only" htmlFor="pipeline-run-switch">Switch run</label>
            <select id="pipeline-run-switch" className="input" value={experimentId} onChange={(event) => { const href = pipelineHref(projectId, event.target.value); if (href) router.push(href); }}>
              {switcher.map((r) => <option key={r.id} value={r.id}>{nameOf(r)} · {statusWords(r.status)}</option>)}
            </select>
            <Link className="btn" href={`/projects/${projectId}/experiments/${experimentId}`}>Open the run</Link>
          </>
        )}
      />
      <PageGuide
        purpose={<>The evidence behind one run: what each <Term definition={PIPELINE_TERMS.stage}>step</Term> did, in order, and how to check it.</>}
        howTo={<>Read down the steps. Each shows its result (<Term definition={PIPELINE_TERMS.deterministic}>computed by fixed rules</Term>), its <Term definition={PIPELINE_TERMS.digest}>checksums</Term>, the trust checks that ran and the History entries it produced. AI notes appear only where one was saved.</>}
        youGet="Step results, checksums, trust checks, the choices made (with the AI&apos;s answer beside the rule&apos;s when there is one), how to reproduce the run, and a replay of any AI runs."
        attention={totals.fail || totals.warn ? `${totals.fail} failed and ${totals.warn} to review among the trust checks of this run.` : counts.failed ? `${counts.failed} step${counts.failed === 1 ? "" : "s"} failed.` : undefined}
      />
      {live ? <Banner tone="info">This run is still in progress. Steps update every few seconds.</Banner> : null}
      {b.compatibility_fallback_used ? <Banner tone="warn">Some steps were read from the older run record, so a step may show fewer details.</Banner> : null}
      <div className="grid cols-4" aria-label="Run summary">
        <Stat value={`${counts.completed} / ${counts.total}`} label="steps done" hint={b.pipeline_run_status.replaceAll("_", " ")} />
        <Stat value={durationLabel(duration)} label="run time" hint={duration === null ? "not finished" : undefined} />
        {runs.length ? <Stat value={cost.cost} label="AI cost" hint={cost.calls} /> : null}
        <Stat value={totals.total ? `${totals.pass} / ${totals.total}` : "none yet"} label="checks passed" hint={totals.total ? `${totals.warn} to review · ${totals.fail} failed · ${totals.other} not checked` : "checks are saved when a run finishes"} />
      </div>
      {counts.total === 0 ? <div className="empty" role="note">No step has been recorded for this run yet.</div> : null}
      <ol className="stage-list" aria-label="Steps in run order">
        {stages.map((stage) => (
          <StageCard
            key={`${stage.sequence}-${stage.key}`} projectId={projectId} stage={stage}
            checks={byStage.get(stage.key) ?? []} records={stageRecords(stage.key, recordItems)} points={pointsForStage(points, stage.key)}
            reviews={stage.key === "deterministic_verification" && reviewStage ? reviews : null}
          />
        ))}
      </ol>
      {loose.length ? <Card title="Other trust checks of this run"><Checks rows={loose} /></Card> : null}
      {events.isError ? <QueryNotice error={events.error} what="pipeline events" /> : null}
      {records.isError ? <QueryNotice error={records.error} what="decision records" /> : null}
      <DecisionPoints projectId={projectId} rows={points} pending={events.isPending || records.isPending} />
      {runs.length || agentRuns.isError ? <AgentRuns runs={runs} pending={agentRuns.isPending} error={agentRuns.isError ? agentRuns.error : null} /> : null}
      <Artifacts workspaceId={b.workspace_id} artifacts={artifacts.data ?? []} pending={artifacts.isPending} error={artifacts.isError ? artifacts.error : null} reproduction={reproduction} />
      <Provenance rows={provenance} aiCost={runs.length ? cost : null} />
    </>
  );
}
