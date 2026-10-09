"use client";

/**
 * Goal & test design (V7-A2): what is predicted, how models are ranked, and how rows are tested (training folds and
 * a final test set used once). Every value is a field the API returns (the newest run on this test design, its model
 * card and the decision records); a value that is not returned is left out. Names, columns and rationales are
 * untrusted text and render as plain text. No final test set value is read: only the strategy, the share and row counts.
 */
import Link from "next/link";
import { useMemo } from "react";
import { QueryNotice, formatWhen } from "@/app/components/studio-app/StudioParts";
import { useBackingRun } from "@/app/components/studio-app/Inspectors";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { GlossaryTerm } from "@/components/studio/GlossaryTerm";
import { KeyValue } from "@/components/studio/KeyValue";
import { PageGuide } from "@/components/studio/PageGuide";
import { safeInternalHref } from "@/components/studio/safe-href";
import { plainText } from "@/lib/application/command-search";
import { useProjectDecisions } from "@/lib/application";
import { decidedRows, designGraphic, goalFacts, problemSpecOf, testDesign, whyText, type DecidedRow, type DesignGraphic } from "@/lib/application/studio-goal";
import { foldSizes, inspectorPath, modelVersionOf } from "@/lib/application/studio-inspect";
import { useModelCardRead } from "@/lib/application/studio-inspect-hooks";
import { percent } from "@/lib/application/studio-data";

const mono = (value: string | null | undefined) => (value ? <span className="mono">{plainText(value, 80)}</span> : null);
const count = (n: number | null) => (n === null ? null : n.toLocaleString("en-GB"));

/** The schematic: one bar per fold (training in green, the held-out fold in blue) above the final test set. */
function TestDesignGraphic({ graphic, summary }: { graphic: DesignGraphic; summary: string }) {
  const row = 22;
  const gap = 8;
  const height = graphic.folds.length * (row + gap) + 28;
  return (
    <figure style={{ margin: 0 }}>
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none" role="img" aria-label={summary} style={{ width: "100%", height: Math.min(height * 1.6, 360) }}>
        {graphic.folds.map((bar, i) => {
          const y = i * (row + gap);
          return (
            <g key={bar.fold}>
              {bar.train.map((r) => <rect key={r.x} x={r.x} y={y} width={r.w} height={row} fill="var(--ok)" opacity={0.75} />)}
              <rect x={bar.validation.x} y={y} width={bar.validation.w} height={row} fill="var(--accent)" />
              <rect x={graphic.trainWidth} y={y} width={100 - graphic.trainWidth} height={row} fill="var(--line-strong)" />
            </g>
          );
        })}
        <rect x={graphic.trainWidth} y={0} width={100 - graphic.trainWidth} height={graphic.folds.length * (row + gap) - gap} fill="none" stroke="var(--ink)" strokeWidth={0.5} strokeDasharray="2 1" />
      </svg>
      <figcaption className="muted">
        <span aria-hidden="true" style={{ color: "var(--ok-text)" }}>■</span> learns from · <span aria-hidden="true" style={{ color: "var(--accent-text)" }}>■</span> checked on · <span aria-hidden="true">□</span> final test set (used once per run, at the end).
        {" "}Schematic: one bar per fold, not to scale.{graphic.capped ? " Only the first 10 folds are drawn." : ""}
      </figcaption>
    </figure>
  );
}

export function GoalTestDesign({ projectId, nodeId }: { projectId: string; nodeId: string }) {
  const { graph, node, experimentId, experiment, build } = useBackingRun(projectId, "split_plan", nodeId);
  const modelVersionId = graph.data && experimentId ? modelVersionOf(graph.data.edges, experimentId) : null;
  const card = useModelCardRead(modelVersionId);
  const decisions = useProjectDecisions(projectId);
  const goal = useMemo(() => goalFacts(build.data, experiment.data), [build.data, experiment.data]);
  const design = useMemo(() => testDesign(build.data, card.data?.split), [build.data, card.data]);
  const graphic = designGraphic(design.kind, design.folds, design.testShare);
  const why = whyText(design, (v) => plainText(v, 60));
  const decided = decidedRows(decisions.data?.items ?? [], nodeId, graph.data && experimentId ? problemSpecOf(graph.data.edges, experimentId) : null);
  const loadingRun = Boolean(experimentId) && (experiment.isPending || build.isPending);
  const sizes = foldSizes(build.data);
  if (graph.isError) return <QueryNotice error={graph.error} what="project" />;
  if (graph.isPending) return <p role="status">Loading the goal and test design…</p>;
  if (!node) return <Banner tone="warn">This test design is not among the newest items loaded for this project, or you cannot see it. Open it from the History page.</Banner>;
  const runHref = experimentId ? inspectorPath(projectId, "experiment", experimentId) : null;
  const decidedColumns: Column<DecidedRow>[] = [
    { key: "part", header: "Part", render: (r) => r.part },
    { key: "by", header: "Recorded by", render: (r) => r.by },
    { key: "when", header: "When", render: (r) => formatWhen(r.when) },
    { key: "state", header: "Status", render: (r) => r.state.replaceAll("_", " ") },
  ];
  const graphicSummary = design.kind && design.folds
    ? `${design.folds} cross-validation folds on the training rows, then a final test set used once per run at the end.` : "";
  return (
    <>
      <PageGuide
        purpose={<>See what is predicted, what &ldquo;good&rdquo; means and how the data is split so the score is fair: <GlossaryTerm term="cv" /> on the training rows and a <GlossaryTerm term="finalTest" />.</>}
        howTo="Check the target and the ranking metric, then the test design. Runs on the same test design are comparable."
        youGet="The target, the kind of answer, the ranking metric, the test design as a picture with row counts, why it was chosen, and who recorded each decision."
        attention="The test design is fixed before any model is fitted. Final test set row values are never shown here."
      />
      {node.stale ? <Banner tone="warn">This test design was made for a data version that is no longer in use, so it is built on an older version.</Banner> : null}
      <Card title="What we predict" aside={<span className="muted">{runHref ? <>from <Link href={safeInternalHref(runHref) ?? "#"}>the newest run on this test design</Link></> : "no run on this test design is loaded"}</span>}>
        {loadingRun ? <p role="status">Loading the run&apos;s plan…</p> : null}
        {experiment.isError ? <QueryNotice error={experiment.error} what="the run" /> : null}
        {build.isError ? <QueryNotice error={build.error} what="the run's plan" /> : null}
        <KeyValue items={[
          ...(goal.target ? [{ key: "target", label: "What we predict", value: mono(goal.target) }] : []),
          ...(goal.task ? [{ key: "task", label: "Kind of answer", value: goal.task }] : []),
          ...(goal.metric ? [{ key: "metric", label: "How we rank models", value: <>{goal.metric.glossary ? <GlossaryTerm term={goal.metric.glossary}>{goal.metric.label}</GlossaryTerm> : goal.metric.label}{goal.metric.about ? <span className="muted"> · {goal.metric.about}</span> : null}</> }] : []),
          ...(goal.businessRule ? [{ key: "rule", label: "Business rule check", value: goal.businessRule }] : []),
        ]} />
        {!goal.target && !goal.metric && !loadingRun && !experiment.isError && !build.isError ? <p className="muted">The goal is read from a finished run on this test design; none is loaded yet.</p> : null}
        {goal.ruleNotRequested ? <p className="muted">No business rule was set for this goal.</p> : null}
      </Card>
      <Card title="How we test" aside={<span className="muted">the same design is used for every run on it</span>}>
        {card.isPending && modelVersionId ? <p role="status">Loading row counts…</p> : null}
        {graphic ? <TestDesignGraphic graphic={graphic} summary={graphicSummary} /> : <p className="muted">The fold layout appears once a finished run on this test design records its validation plan.</p>}
        <KeyValue items={[
          ...(count(design.trainRows) ? [{ key: "train", label: "Training rows", value: `${count(design.trainRows)}${design.folds ? ` · ${design.folds} cross-validation folds` : ""}` }] : []),
          ...(count(design.testRows) ? [{ key: "test", label: <GlossaryTerm term="finalTest" />, value: `${count(design.testRows)} rows${design.testShare ? ` (${percent(design.testShare)})` : ""}. Each run scores it once, for its chosen model only.` }] : []),
          ...(design.timeColumn ? [{ key: "time", label: "Ordered by", value: mono(design.timeColumn) }] : []),
          ...(design.groupColumn ? [{ key: "group", label: "Kept together by", value: mono(design.groupColumn) }] : []),
        ]} />
        {why ? <p><b>Why this design?</b> {why}</p> : null}
      </Card>
      <Card title="Who decided" aside={<span className="muted">from the History</span>}>
        {decisions.isError ? <QueryNotice error={decisions.error} what="decisions" /> : null}
        {decided.length ? <DataTable caption="Who decided" columns={decidedColumns} rows={decided} rowKey={(r) => r.key} /> : <p className="muted">{decisions.isPending ? "Loading…" : "No decision about this goal or test design is among the newest History entries."}</p>}
      </Card>
      <details>
        <summary>Details</summary>
        <KeyValue items={[
          { key: "id", label: "Test design id", value: <span className="mono">{node.id}</span> },
          { key: "created", label: "Created", value: formatWhen(node.created_at) },
          { key: "locked", label: "Locked", value: design.locked ? `Yes${design.lockedAt ? `, ${formatWhen(design.lockedAt)}` : ""}` : "Not recorded as locked" },
          ...(design.stratified !== null ? [{ key: "strat", label: "Keeps the class mix", value: design.stratified ? "yes" : "no" }] : []),
        ]} />
        {sizes.length ? <p className="muted">Rows per fold of the first candidate (training / validation): {sizes.map((s) => `fold ${s.fold}: ${s.trainRows ?? "—"} / ${s.validationRows ?? "—"}`).join(" · ")}.</p> : null}
      </details>
    </>
  );
}
