"use client";

/**
 * Node inspectors (P4.3-A): one page-level body per node kind and a compact drawer body.
 * Every value comes from a /v1 field or the model-build read; the final test set is never shown
 * here (no `final_evaluation`, no final test set stage). Names, rationales and generated code are
 * untrusted text and render as plain text.
 */
import { GLOSSARY } from "@/components/studio/glossary";
import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";
import { ModelBuildInspector } from "@/app/components/model-build/ModelBuildInspector";
import { QueryNotice, STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { CodeBlock } from "@/components/studio/CodeBlock";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { KeyValue } from "@/components/studio/KeyValue";
import { Level } from "@/components/studio/Level";
import { PageGuide } from "@/components/studio/PageGuide";
import { Pill } from "@/components/studio/Pill";
import { safeInternalHref } from "@/components/studio/safe-href";
import { SectionTabs } from "@/components/studio/SectionTabs";
import { Term } from "@/components/studio/Term";
import { plainText, projectHref } from "@/lib/application/command-search";
import { useModelBuild } from "@/lib/application/hooks";
import { percent } from "@/lib/application/studio-data";
import { attentionCount, findingsState } from "@/lib/application/studio-findings";
import { useDatasetProfile, useDatasetVersion, useExperimentFindings, useProjectGraph, type StudioGraph, type StudioGraphNode } from "@/lib/application/studio-data-hooks";
import {
  candidateRows, featureReason, featureRows, foldRows, foldSizes, formatNumber, experimentUsing, inspectorPath, investigationView, metricNames,
  modelVersionOf, preprocessingSteps, proposalsFor, reviewView, safeFilename, splitFacts, transformSnippet, type CandidateRow, type FeatureRow, type FoldRow, type PrepStep,
} from "@/lib/application/studio-inspect";
import {
  useExperimentCode, useExperimentDetail, useModelCardRead, useModelVersionRead, useProjectProposals,
  type StudioExperimentCode, type StudioExperimentDetail,
} from "@/lib/application/studio-inspect-hooks";
import { kindLabel, shortId } from "@/lib/application/studio-graph";

const TERMS = {
  cv: "Cross-validation (CV): the training rows are split into folds; each fold is held out once while the model trains on the others. Models are compared on these CV scores only.",
  fold: "One of the parts the training rows are cut into for cross-validation. Preprocessing is fitted on the other folds, never on the one being scored.",
  holdout: GLOSSARY.finalTest.definition,
  digest: "A fingerprint of the content. Two items with the same fingerprint hold the same bytes.",
  importance: "How much the cross-validation score drops when a column's values are shuffled, measured on validation folds only.",
  leakage: GLOSSARY.leakage.definition,
  threshold: GLOSSARY.threshold.definition,
  changeSet: "A typed list of changes (for example a different feature recipe) applied on top of a parent experiment.",
  level: "Advice only: recorded, never applied. Ask first: shown for a person to accept or reject. Advice never changes the project by itself.",
};

const mono = (value: string | null | undefined) => (value ? <span className="mono">{value}</span> : "—");
const link = (href: string | null, text: ReactNode) => (href && safeInternalHref(href) ? <Link href={href}>{text}</Link> : <>{text}</>);

function Reason({ children }: { children: ReactNode }) {
  return <Card title="Reason" aside={<span className="muted">why this node exists</span>}>{children}</Card>;
}

/** Plain-text download: a Blob with a text media type and a sanitised name; nothing is executed. */
function downloadText(filename: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function CodeActions({ filename, source, label }: { filename: string; source: string; label: string }) {
  const [state, setState] = useState("");
  const name = safeFilename(filename, "experiment.py");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(source);
      setState(`Copied ${label} to the clipboard.`);
    } catch {
      setState("Copy was blocked by the browser; select the code and copy it by hand.");
    }
  };
  return (
    <div className="toolbar">
      <button type="button" className="btn" onClick={() => void copy()}>Copy<span className="sr-only"> {label}</span></button>
      <button type="button" className="btn" onClick={() => { downloadText(name, source); setState(`Downloaded ${name}.`); }}>Download<span className="sr-only"> {label}</span></button>
      <span className="muted" role="status">{state}</span>
    </div>
  );
}

// --- experiment ----------------------------------------------------------------------

function CodeTab({ code, error, pending }: { code?: StudioExperimentCode; error: unknown; pending: boolean }) {
  if (pending) return <p role="status">Loading the code…</p>;
  if (!code) return <Banner tone="warn">Reproducible code is not available for this run{error instanceof Error ? ` (${error.message})` : ""}. Runs from before the code generator, or runs that are still going, have none.</Banner>;
  return (
    <>
      <p className="muted">
        Generated by the service from the recorded spec, <span className="mono">{code.generator_version}</span>, spec digest <span className="mono">{code.spec_digest}</span>. {code.standalone_cv ? "Runs cross-validation only; it does not touch the final test set." : "It needs the inputs below."} Shown as text: DCLab never runs it for you.
      </p>
      {code.inputs?.length ? (
        <ul className="plain-list" aria-label="Inputs the script needs">
          {code.inputs.map((input) => <li key={input.name}><b>{input.name}</b> <span className="mono">{input.env_var}</span>: {plainText(input.description, 200)}</li>)}
        </ul>
      ) : null}
      <h3>Script <span className="mono muted">{code.script.filename}</span></h3>
      <CodeActions filename={code.script.filename} source={code.script.source} label="the script" />
      <CodeBlock code={code.script.source} label={`Generated script ${code.script.filename}`} language="python" />
      <p className="muted">Script digest <span className="mono">{code.script.content_digest}</span>. The notebook <span className="mono">{code.notebook.filename}</span> holds the same steps.</p>
      <CodeActions filename={code.notebook.filename} source={code.notebook.source} label="the notebook JSON" />
    </>
  );
}

function CandidatesTab({ rows, metrics }: { rows: CandidateRow[]; metrics: string[] }) {
  const columns: Column<CandidateRow>[] = [
    { key: "algo", header: "Candidate", sortValue: (c) => c.algorithm, render: (c) => <>{plainText(c.algorithm, 80)} {c.selected ? <Pill tone="ok">winner</Pill> : c.runnerUp ? <Pill tone="gray">runner-up</Pill> : null}</> },
    { key: "family", header: "Family", render: (c) => plainText(c.family, 60) },
    { key: "folds", header: "Folds", numeric: true, sortValue: (c) => c.folds, render: (c) => c.folds },
    ...metrics.slice(0, 4).map((name): Column<CandidateRow> => ({ key: `m-${name}`, header: `CV ${name}`, numeric: true, sortValue: (c) => c.cv[name] ?? -Infinity, render: (c) => formatNumber(c.cv[name]) })),
    { key: "fp", header: "Fingerprint", render: (c) => <span className="mono">{c.fingerprint.slice(0, 12)}</span> },
    { key: "hp", header: "Hyperparameters", render: (c) => (c.hyperparameters.length ? <span className="mono">{c.hyperparameters.map(([k, v]) => `${plainText(k, 40)}=${plainText(v, 40)}`).join(", ")}</span> : "—") },
  ];
  return (
    <>
      <p className="muted"><Term definition={TERMS.digest}>Fingerprint</Term> identifies the exact configuration. Candidates are compared on <Term definition={TERMS.cv}>cross-validation</Term> only. The final test set never takes part in the comparison and is not shown here.</p>
      <DataTable caption="Candidates and their CV scores" columns={columns} rows={rows} rowKey={(c) => c.id} emptyMessage="No candidate is recorded for this run yet." />
    </>
  );
}

function FoldsTab({ rows, metrics, detail, sizes }: { rows: FoldRow[]; metrics: string[]; detail?: StudioExperimentDetail; sizes: Array<{ fold: number; trainRows: number | null; validationRows: number | null }> }) {
  const columns: Column<FoldRow>[] = [
    { key: "cand", header: "Candidate", sortValue: (f) => f.candidate, render: (f) => plainText(f.candidate, 80) },
    { key: "fold", header: "Fold", numeric: true, sortValue: (f) => f.fold, render: (f) => f.fold },
    { key: "train", header: "Train rows", numeric: true, render: (f) => f.trainRows ?? "—" },
    { key: "val", header: "Validation rows", numeric: true, render: (f) => f.validationRows ?? "—" },
    ...metrics.slice(0, 4).map((name): Column<FoldRow> => ({ key: `m-${name}`, header: name, numeric: true, sortValue: (f) => f.metrics[name] ?? -Infinity, render: (f) => formatNumber(f.metrics[name]) })),
  ];
  const m = detail?.metrics;
  return (
    <>
      <p className="muted">A <Term definition={TERMS.fold}>fold</Term> is one held-out part of the training rows.</p>
      <KeyValue items={[
        { key: "metric", label: "Selection metric", value: mono(m?.selection_metric) },
        { key: "score", label: "Selected CV score", value: formatNumber(m?.selected_score) },
        { key: "thr", label: <Term definition={TERMS.threshold}>Decision threshold</Term>, value: m?.decision_threshold == null ? "none (not a thresholded task, or none chosen)" : <>{formatNumber(m.decision_threshold)} <span className="muted">chosen on cross-validation</span></> },
        { key: "folds", label: "Fold sizes", value: sizes.length ? sizes.map((s) => `fold ${s.fold}: ${s.trainRows ?? "—"} train / ${s.validationRows ?? "—"} validation`).join(" · ") : "—" },
      ]} />
      <DataTable caption="Per-fold CV metrics" columns={columns} rows={rows} rowKey={(f) => f.id} emptyMessage="No fold result is recorded for this run yet." />
    </>
  );
}

function DriversTab({ modelVersionId }: { modelVersionId: string | null }) {
  const card = useModelCardRead(modelVersionId);
  if (!modelVersionId) return <div className="empty">Feature importance is measured on the final model, so it appears once this run has produced a model version.</div>;
  if (card.isError) return <QueryNotice error={card.error} what="model card" />;
  if (!card.data) return <p role="status">Loading importance…</p>;
  const { drivers } = card.data;
  const features = drivers.features ?? [];
  const columns: Column<(typeof features)[number]>[] = [
    { key: "rank", header: "Rank", numeric: true, sortValue: (f) => f.rank, render: (f) => f.rank },
    { key: "col", header: "Column", render: (f) => <span className="mono">{plainText(f.column, 80)}</span> },
    { key: "imp", header: "Importance", numeric: true, sortValue: (f) => f.importance_mean ?? -Infinity, render: (f) => formatNumber(f.importance_mean) },
    { key: "se", header: "± s.e.", numeric: true, render: (f) => formatNumber(f.importance_se) },
    { key: "clear", header: "Clearly above noise", render: (f) => (f.distinguishable == null ? "—" : f.distinguishable ? "yes" : "no") },
  ];
  return (
    <>
      <p>{plainText(drivers.text, 400)}</p>
      <p className="muted">What importance means: <Term definition={TERMS.importance}>permutation importance</Term>. Method {drivers.method ?? "—"}, scoring {drivers.scoring ?? "—"}{drivers.folds ? `, ${drivers.folds} validation folds` : ""}. Status: {drivers.status.replaceAll("_", " ")}.</p>
      <DataTable caption="Feature importance" columns={columns} rows={features} rowKey={(f) => f.column} emptyMessage="No importance was computed for this model." />
    </>
  );
}

function FindingsAndReview({ projectId, experimentId }: { projectId: string; experimentId: string }) {
  const findings = useExperimentFindings(experimentId);
  const proposals = useProjectProposals(projectId, "ExperimentReviewProposal");
  const reviews = proposalsFor(proposals.data?.items ?? [], "ExperimentReviewProposal", "experiment", experimentId);
  const findingsTotal = findings.data?.checks?.length ?? 0;
  return (
    <>
      <h3>Findings</h3>
      {findings.isError ? <QueryNotice error={findings.error} what="findings" /> : null}
      <p className="muted" data-testid="findings-summary">
        {findingsState(findings.data) === "pending" ? "Loading findings…"
          : findingsState(findings.data) === "not_computed" ? "No trust checks are recorded for this run yet (not the same as passing)."
            : attentionCount(findings.data) ? `${attentionCount(findings.data)} of ${findingsTotal} trust checks need attention.` : `All ${findingsTotal} trust checks passed.`}
        {" "}Severity, numbers and what to do are in the Findings card above.
      </p>
      <h3><Term definition={TERMS.level}>Critic review</Term> <span className="muted">(advisory)</span></h3>
      {proposals.isError ? <QueryNotice error={proposals.error} what="review proposals" /> : null}
      {proposals.isPending ? <p role="status">Loading the review…</p> : null}
      {proposals.data && reviews.length === 0 ? <p className="muted">No Critic review for this run. AI is off, or the Critic has not been released; everything on this page works without it.</p> : null}
      {reviews.map((p) => {
        const v = reviewView(p);
        return (
          <article key={p.id} className="card flat" aria-label={`Critic review ${shortId(p.id)}`}>
            <p className="toolbar">
              <Pill tone="ai"><span aria-hidden="true">◆ </span>{v.verdict ? v.verdict.replaceAll("_", " ") : "no verdict"}</Pill>
              {v.level !== null ? <Level level={v.level} /> : null}
              <span className="muted">{v.status} · by {v.by} · {formatWhen(p.created_at)}{v.confidence !== null ? ` · confidence ${formatNumber(v.confidence)}` : ""}</span>
            </p>
            {v.summary ? <p>{plainText(v.summary, 2000)}</p> : null}
            <table className="graph-answers">
              <caption className="sr-only">Rule answer beside the Critic answer</caption>
              <thead><tr><th scope="col">Rule (deterministic)</th><th scope="col">Critic (AI)</th></tr></thead>
              <tbody><tr><td>{v.ruleAnswer ? <span className="mono">{plainText(v.ruleAnswer, 300)}</span> : "No rule answer recorded"}</td><td>{v.verdict ? v.verdict.replaceAll("_", " ") : "—"}</td></tr></tbody>
            </table>
            {v.metrics.length ? <p>Cites CV values: {v.metrics.map((m) => `${plainText(m.metric, 40)} ${formatNumber(m.value)}`).join(", ")}.</p> : null}
            {v.findings.length ? <ul className="plain-list">{v.findings.map((f) => <li key={f.check}><span className="mono">{plainText(f.check, 60)}</span> {f.status.replaceAll("_", " ")}: {plainText(f.note, 500)}</li>)}</ul> : null}
          </article>
        );
      })}
    </>
  );
}

export function ExperimentInspector({ projectId, experimentId, workspaceId, lineage, graph }: {
  projectId: string; experimentId: string; workspaceId: string;
  lineage: { parent_experiment_id?: string | null; split_plan_id?: string | null; problem_spec_id?: string | null; source_dataset_id?: string | null };
  graph?: StudioGraph;
}) {
  const detail = useExperimentDetail(experimentId);
  const build = useModelBuild(workspaceId, experimentId);
  const code = useExperimentCode(experimentId);
  const modelVersionId = graph ? modelVersionOf(graph.edges, experimentId) : null;
  const candidates = useMemo(() => candidateRows(build.data), [build.data]);
  const folds = useMemo(() => foldRows(build.data), [build.data]);
  const d = detail.data;
  const preferred = d?.metrics?.selection_metric;
  const cvMetrics = metricNames(candidates, preferred);
  const foldMetrics = metricNames(folds, preferred);
  const cvEntries = Object.entries(d?.metrics?.cv ?? {}).filter((e): e is [string, number] => typeof e[1] === "number");
  const changeSet = d?.change_set ? Object.entries(d.change_set) : [];
  const href = (section: string, id: string | null | undefined) => (id ? inspectorPath(projectId, section, id) : null);
  return (
    <SectionTabs
      title="Inspector" idPrefix="exp-inspect" label="Experiment inspector"
      aside={<span className="mono muted">{shortId(experimentId)}</span>}
      sections={[
        {
          id: "overview", label: "Overview",
          content: (
            <>
              <Reason>
                <p>{d?.intent ? plainText(d.intent, 500) : "No intent was written for this run."} <span className="muted">(written by a person or agent)</span></p>
                <p className="muted">
                  Built from {lineage.parent_experiment_id ? <>branch of run {link(href("experiment", lineage.parent_experiment_id), shortId(lineage.parent_experiment_id))}, </> : "no parent (a root run), "}
                  dataset {link(href("dataset_version", lineage.source_dataset_id), mono(shortId(lineage.source_dataset_id ?? "")))}, split plan {link(href("split_plan", lineage.split_plan_id), mono(shortId(lineage.split_plan_id ?? "")))}.
                </p>
              </Reason>
              {detail.isError ? <QueryNotice error={detail.error} what="experiment detail" /> : null}
              <h3>Config</h3>
              <KeyValue items={[
                { key: "task", label: "Task", value: d?.task_type ?? "—" },
                { key: "target", label: "Target column", value: mono(d?.target_column) },
                { key: "sel", label: "Selection metric", value: mono(d?.metrics?.selection_metric) },
                { key: "fam", label: "Winning family", value: d?.metrics?.family ? plainText(d.metrics.family, 60) : "—" },
                { key: "constraint", label: "Constraint status", value: d?.metrics?.constraint_status ?? "—" },
                { key: "model", label: "Model version", value: modelVersionId ? link(href("model_version", modelVersionId), mono(shortId(modelVersionId))) : "None yet" },
              ]} />
              <h3><Term definition={TERMS.changeSet}>Change set</Term></h3>
              {changeSet.length ? (
                <KeyValue items={changeSet.map(([k, v]) => ({ key: k, label: <span className="mono">{plainText(k, 60)}</span>, value: <span className="mono">{plainText(typeof v === "string" ? v : JSON.stringify(v), 300)}</span> }))} />
              ) : <p className="muted">No change set: this is not a branch.</p>}
              {d?.diff_vs_parent ? <p className="muted">Difference against the parent is recorded: <span className="mono">{plainText(JSON.stringify(d.diff_vs_parent), 400)}</span></p> : null}
              <h3><Term definition={TERMS.cv}>Cross-validation metrics</Term></h3>
              {cvEntries.length ? <KeyValue items={cvEntries.map(([k, v]) => ({ key: k, label: <span className="mono">{plainText(k, 60)}</span>, value: formatNumber(v) }))} /> : <p className="muted">No CV metrics are recorded yet.</p>}
              <p className="muted">The <Term definition={TERMS.holdout}>final test set (used once)</Term> is scored one time after the winner is locked and is not shown on this page.</p>
              <FindingsAndReview projectId={projectId} experimentId={experimentId} />
            </>
          ),
        },
        { id: "candidates", label: "Candidates", count: candidates.length, content: build.isError ? <QueryNotice error={build.error} what="model build" /> : build.isPending ? <p role="status">Loading candidates…</p> : <CandidatesTab rows={candidates} metrics={cvMetrics} /> },
        { id: "folds", label: "Per-fold and threshold", content: build.isError ? <QueryNotice error={build.error} what="model build" /> : build.isPending ? <p role="status">Loading folds…</p> : <FoldsTab rows={folds} metrics={foldMetrics} detail={d} sizes={foldSizes(build.data)} /> },
        { id: "importance", label: "Feature importance", content: <DriversTab modelVersionId={modelVersionId} /> },
        { id: "code", label: "Code", content: <CodeTab code={code.data} error={code.error} pending={code.isPending} /> },
        { id: "evidence", label: "Evidence", content: <div className="legacy-surface"><ModelBuildInspector workspaceId={workspaceId} pipelineRunId={experimentId} /></div> },
      ]}
    />
  );
}

// --- nodes backed by an experiment's build ----------------------------------------------

/** Split plans and feature recipes have no read of their own: they are read through the newest run that uses them. */
function useBackingRun(projectId: string, kind: "split_plan" | "feature_recipe", nodeId: string) {
  const graph = useProjectGraph(projectId);
  const node = graph.data?.nodes.find((n) => n.kind === kind && n.id === nodeId) ?? null;
  const experimentId = graph.data ? experimentUsing(graph.data.nodes, graph.data.edges, kind, nodeId) : null;
  const experiment = useExperimentDetail(experimentId);
  const build = useModelBuild(experiment.data?.workspace_id, experimentId ?? undefined);
  return { graph, node, experimentId, experiment, build };
}

function NotInGraph({ what }: { what: string }) {
  return <Banner tone="warn">This {what} is not part of this project&apos;s graph window, or you cannot see it. The graph loads the newest experiments only; older nodes open from the Graph page&apos;s older window.</Banner>;
}

function NodeFacts({ node }: { node: StudioGraphNode }) {
  return (
    <KeyValue items={[
      { key: "id", label: "Id", value: mono(node.id) },
      { key: "digest", label: <Term definition={TERMS.digest}>Digest</Term>, value: mono(node.digest) },
      { key: "created", label: "Created", value: formatWhen(node.created_at) },
      { key: "status", label: "Status", value: node.status ? node.status.replaceAll("_", " ") : "—" },
      { key: "stale", label: "Built on an older version", value: node.stale ? "Yes: made from a version that is no longer in use" : "No" },
    ]} />
  );
}

export function SplitInspector({ projectId, nodeId }: { projectId: string; nodeId: string }) {
  const { graph, node, experimentId, build } = useBackingRun(projectId, "split_plan", nodeId);
  const facts = useMemo(() => splitFacts(build.data), [build.data]);
  const sizes = foldSizes(build.data);
  const modelVersionId = graph.data && experimentId ? modelVersionOf(graph.data.edges, experimentId) : null;
  const card = useModelCardRead(modelVersionId);
  const split = card.data?.split;
  if (graph.isError) return <QueryNotice error={graph.error} what="project graph" />;
  if (graph.isPending) return <p role="status">Loading the split plan…</p>;
  if (!node) return <NotInGraph what="split plan" />;
  return (
    <>
      <PageGuide
        purpose={<>See exactly how rows were assigned to the <Term definition={TERMS.holdout}>final test set (used once)</Term> and to <Term definition={TERMS.fold}>folds</Term>.</>}
        howTo="Check the strategy and the group or time column, then the row counts. Runs on the same split plan are comparable."
        youGet="Strategy, fractions, group and time columns, partition row counts and the digest. Counts only."
        attention="The plan is locked before any model is fitted. Final test set row values are never shown."
      />
      <Reason>
        <p>{facts.reason ? plainText(facts.reason, 500) : "A split plan fixes which rows are held out before modelling, so every experiment on it is scored on the same rows."}</p>
        <p className="muted">Read from {experimentId ? <>run {link(inspectorPath(projectId, "experiment", experimentId), mono(shortId(experimentId)))}, the newest loaded run on this plan.</> : "no loaded run: no run on this plan is in the graph window."}</p>
      </Reason>
      <Card title="Plan">
        <NodeFacts node={node} />
        {build.isPending && experimentId ? <p role="status">Loading the plan…</p> : null}
        {build.isError ? <QueryNotice error={build.error} what="model build" /> : null}
        <KeyValue items={[
          { key: "strategy", label: "Strategy", value: facts.strategy ?? split?.evaluation_split_strategy ?? "—" },
          { key: "frac", label: "Final test set fraction", value: facts.testSize !== null ? percent(facts.testSize <= 1 ? facts.testSize : facts.testSize / 100) : percent(split?.evaluation_fraction) },
          { key: "group", label: "Group column", value: mono(facts.groupColumn ?? split?.group_column) },
          { key: "time", label: "Time column", value: mono(facts.timeColumn ?? split?.time_column) },
          { key: "strat", label: "Stratified", value: split?.stratified == null ? "—" : split.stratified ? "yes" : "no" },
          { key: "locked", label: "Locked", value: facts.locked ? `Yes${facts.lockedAt ? `, ${formatWhen(facts.lockedAt)}` : ""}` : "Not recorded as locked" },
        ]} />
      </Card>
      <Card title="Rows per partition" aside={<span className="muted">counts only</span>}>
        <KeyValue items={[
          { key: "train", label: "Training rows", value: split?.train_rows ?? "—" },
          { key: "eval", label: "Final test set rows", value: split?.evaluation_rows ?? "—" },
          { key: "cv", label: "Validation", value: split?.validation_strategy ? `${split.validation_strategy}${split.validation_folds ? `, ${split.validation_folds} folds` : ""}` : "—" },
        ]} />
        {sizes.length ? <p className="muted">Fold sizes of the first candidate: {sizes.map((s) => `fold ${s.fold} ${s.trainRows ?? "—"}/${s.validationRows ?? "—"} (train/validation)`).join(" · ")}.</p> : null}
        {!split && experimentId ? <p className="muted">Row counts come from the model card, which exists once the run produced a model version.</p> : null}
      </Card>
    </>
  );
}

function FeatureTable({ rows, importance, onPick, picked }: { rows: FeatureRow[]; importance: Map<string, number | null | undefined>; onPick: (id: string) => void; picked: string | null }) {
  const columns: Column<FeatureRow>[] = [
    { key: "name", header: "Feature", sortValue: (f) => f.name, render: (f) => <button type="button" className="linkish mono" aria-pressed={picked === f.id} onClick={() => onPick(f.id)}>{plainText(f.name, 80)}<span className="sr-only"> (show code)</span></button> },
    { key: "kind", header: "Type", sortValue: (f) => f.kind, render: (f) => f.kind },
    { key: "origin", header: "Origin", sortValue: (f) => f.origin, render: (f) => f.origin },
    { key: "dec", header: "Decision", render: (f) => <Pill tone={f.decision === "accepted" ? "ok" : "gray"}>{f.decision}</Pill> },
    { key: "src", header: "Built from", render: (f) => (f.sources.length ? <span className="mono">{f.sources.map((s) => plainText(s, 40)).join(", ")}</span> : "—") },
    { key: "formula", header: "Formula", render: (f) => (f.formula ? plainText(f.formula, 200) : "—") },
    { key: "imp", header: "Importance", numeric: true, sortValue: (f) => importance.get(f.name) ?? -Infinity, render: (f) => formatNumber(importance.get(f.name)) },
  ];
  return <DataTable caption="Features of this recipe" columns={columns} rows={rows} rowKey={(f) => f.id} emptyMessage="No feature is recorded for this recipe's run." />;
}

function PrepTable({ steps }: { steps: PrepStep[] }) {
  const columns: Column<PrepStep>[] = [
    { key: "seq", header: "Step", numeric: true, sortValue: (s) => s.sequence, render: (s) => s.sequence },
    { key: "scope", header: "Columns", render: (s) => s.scope },
    { key: "type", header: "Kind", render: (s) => s.type },
    { key: "class", header: "Transformer", render: (s) => <span className="mono">{plainText(s.transformer, 80)}</span> },
    { key: "fit", header: "Fitted on", render: (s) => s.fit.replaceAll("_", " ") },
  ];
  return <DataTable caption="Preprocessing steps" columns={columns} rows={steps} rowKey={(s) => `${s.sequence}-${s.scope}-${s.transformer}`} emptyMessage="No preprocessing step is recorded for this run." />;
}

export function FeatureInspector({ projectId, nodeId }: { projectId: string; nodeId: string }) {
  const { graph, node, experimentId, build } = useBackingRun(projectId, "feature_recipe", nodeId);
  const rows = useMemo(() => featureRows(build.data), [build.data]);
  const steps = useMemo(() => preprocessingSteps(build.data), [build.data]);
  const modelVersionId = graph.data && experimentId ? modelVersionOf(graph.data.edges, experimentId) : null;
  const card = useModelCardRead(modelVersionId);
  const importance = new Map((card.data?.drivers.features ?? []).map((f) => [f.column, f.importance_mean]));
  const [open, setOpen] = useState<string | null>(null);
  if (graph.isError) return <QueryNotice error={graph.error} what="project graph" />;
  if (graph.isPending) return <p role="status">Loading the feature recipe…</p>;
  if (!node) return <NotInGraph what="feature recipe" />;
  const shown = rows.find((f) => f.id === open) ?? null;
  const reason = featureReason(build.data);
  return (
    <>
      <PageGuide
        purpose="See which features the model uses, how each was built and how much the model relies on them."
        howTo={<>Each row names the source columns and the formula. Pick a feature to see its code. Transforms are fitted inside each <Term definition={TERMS.fold}>fold</Term>.</>}
        youGet={<>Reason, formula, <Term definition={TERMS.importance}>importance</Term> and a code snippet per feature, from the run that produced this recipe.</>}
        attention={<>A column excluded for <Term definition={TERMS.leakage}>leakage</Term> shows as rejected.</>}
      />
      <Reason>
        <p>{reason ? plainText(reason, 500) : "A feature recipe records how raw columns become model inputs."}</p>
        <p className="muted">Read from {experimentId ? <>run {link(inspectorPath(projectId, "experiment", experimentId), mono(shortId(experimentId)))}, the loaded run that produced this recipe.</> : "no loaded run: the run that produced this recipe is outside the graph window."}</p>
      </Reason>
      <Card title="Recipe"><NodeFacts node={node} /></Card>
      {build.isError ? <QueryNotice error={build.error} what="model build" /> : null}
      {build.isPending && experimentId ? <p role="status">Loading the features…</p> : null}
      <FeatureTable rows={rows} importance={importance} onPick={(id) => setOpen(open === id ? null : id)} picked={open} />
      {!modelVersionId ? <p className="muted">Importance is measured on the final model, so it appears once the run produced a model version.</p> : card.data ? <p className="muted">Importance: {plainText(card.data.drivers.text, 300)}</p> : null}
      {shown ? (
        <>
          <h3>Code for <span className="mono">{plainText(shown.name, 80)}</span></h3>
          <CodeBlock code={transformSnippet(shown, steps)} label={`Transform steps of ${shown.name}`} language="python" />
        </>
      ) : <p className="muted">Pick a feature name to see its transform code.</p>}
      <h3>Preprocessing steps</h3>
      <PrepTable steps={steps} />
    </>
  );
}

// --- dataset, model --------------------------------------------------------------------

export function DatasetInspector({ projectId, datasetId }: { projectId: string; datasetId: string }) {
  const version = useDatasetVersion(datasetId);
  const profile = useDatasetProfile(datasetId);
  const graph = useProjectGraph(projectId);
  const proposals = useProjectProposals(projectId, "DatasetInvestigationProposal");
  const node = graph.data?.nodes.find((n) => n.kind === "dataset_version" && n.id === datasetId);
  const investigations = proposalsFor(proposals.data?.items ?? [], "DatasetInvestigationProposal", "dataset_version", datasetId);
  if (version.isError) return <QueryNotice error={version.error} what="dataset version" />;
  if (!version.data) return <p role="status">Loading the dataset version…</p>;
  const v = version.data;
  const cols = profile.data?.columns ?? [];
  const excluded = cols.filter((c) => c.leakage_excluded).length;
  const roles = new Map<string, number>();
  for (const c of cols) roles.set(c.role_used ?? "unknown", (roles.get(c.role_used ?? "unknown") ?? 0) + 1);
  const dataHref = `/projects/${projectId}/data`;
  return (
    <>
      <PageGuide
        purpose="Check what this version of the data is and what it was used for."
        howTo="Read the shape and digest, the profile summary, then the AI investigation if one exists. The Data page has the full column table."
        youGet="Rows, columns, digest, role counts, leakage exclusions and the rule answer beside any AI answer."
        attention="Statistics use training rows only; the final test set is never counted."
      />
      <Reason>
        <p>{node?.intent ? plainText(node.intent, 400) : "An uploaded or prepared table that experiments are trained on."} {node?.derived ? "It was prepared by a run from an upload." : "It is an upload."}</p>
        <p className="muted">{node?.stale ? "Built on an older version: this version is no longer in use." : "Not built on an older version."} Open <Link href={dataHref}>the Data page</Link> for every version and the policy.</p>
      </Reason>
      <Card title="Dataset version">
        <KeyValue items={[
          { key: "name", label: "Name", value: `${plainText(v.name, 120)} · ${v.version}` },
          { key: "shape", label: "Rows × columns", value: `${v.row_count} × ${v.column_count}` },
          { key: "id", label: "Id", value: mono(v.id) },
          { key: "digest", label: <Term definition={TERMS.digest}>Content digest</Term>, value: mono(v.content_digest) },
          { key: "created", label: "Uploaded", value: formatWhen(v.created_at) },
          { key: "ai", label: "Data class for AI", value: <Pill tone={v.policy.ai_data_class === "none" ? "gray" : "ai"}>{v.policy.ai_data_class}</Pill> },
        ]} />
      </Card>
      <Card title="Profile summary">
        {profile.isError ? <QueryNotice error={profile.error} what="profile" /> : profile.isPending ? <p role="status">Loading the profile…</p> : (
          <KeyValue items={[
            { key: "scope", label: "Statistics", value: profile.data?.statistics_status === "computed" ? `Computed over ${profile.data.split_plan?.training_row_count ?? "—"} training rows` : "Not available yet: names and types only" },
            { key: "roles", label: "Roles used", value: roles.size ? [...roles].map(([r, n]) => `${r.replaceAll("_", " ")} ${n}`).join(" · ") : "—" },
            { key: "leak", label: <Term definition={TERMS.leakage}>Leakage exclusions</Term>, value: excluded },
            { key: "imp", label: "Importance method", value: profile.data?.importance_method ?? "—" },
          ]} />
        )}
      </Card>
      <Card title="AI investigation" aside={<span className="muted">advisory</span>}>
        {proposals.isError ? <QueryNotice error={proposals.error} what="investigation proposals" /> : null}
        {proposals.data && investigations.length === 0 ? <p className="muted">No AI investigation of this version. AI is off or the Dataset Investigator is not released; the rule&apos;s answers are on the Data page.</p> : null}
        {investigations.map((p) => {
          const inv = investigationView(p);
          const ruleTarget = cols.find((c) => c.rule_role === "target")?.name;
          return (
            <article key={p.id} aria-label={`Investigation ${shortId(p.id)}`}>
              <p className="toolbar"><Pill tone="ai"><span aria-hidden="true">◆ </span>Dataset Investigator</Pill>{inv.level !== null ? <Level level={inv.level} /> : null}<span className="muted">{inv.status} · {formatWhen(p.created_at)}</span></p>
              <table className="graph-answers">
                <caption className="sr-only">Rule answer beside AI answer</caption>
                <thead><tr><th scope="col">Question</th><th scope="col">Rule</th><th scope="col">AI</th></tr></thead>
                <tbody>
                  <tr><td>Target column</td><td className="mono">{ruleTarget ?? "—"}</td><td className="mono">{inv.targets.length ? inv.targets.map((t) => `${t.rank}. ${plainText(t.column, 40)}`).join(", ") : "—"}</td></tr>
                  {inv.missing.map((m) => <tr key={`m-${m.column}`}><td>Missing values in <span className="mono">{plainText(m.column, 40)}</span></td><td>{inv.ruleAnswer ? "see record" : "—"}</td><td>{m.action.replaceAll("_", " ")}: {plainText(m.reason, 200)}</td></tr>)}
                  {inv.leakage.map((l) => <tr key={`l-${l.column}`}><td>Possible leakage in <span className="mono">{plainText(l.column, 40)}</span></td><td>{cols.find((c) => c.name === l.column)?.leakage_excluded ? "excluded" : "kept"}</td><td>{plainText(l.explanation, 300)}</td></tr>)}
                </tbody>
              </table>
              {inv.questions.length ? <ul className="plain-list">{inv.questions.map((q) => <li key={q}>{plainText(q, 300)}</li>)}</ul> : null}
              {inv.ruleAnswer ? <p className="muted">Recorded rule answer: <span className="mono">{plainText(inv.ruleAnswer, 300)}</span></p> : null}
            </article>
          );
        })}
      </Card>
    </>
  );
}

export function ModelInspector({ projectId, modelVersionId }: { projectId: string; modelVersionId: string }) {
  const model = useModelVersionRead(modelVersionId);
  const card = useModelCardRead(modelVersionId);
  if (model.isError) return <QueryNotice error={model.error} what="model version" />;
  if (!model.data) return <p role="status">Loading the model version…</p>;
  const m = model.data;
  if (m.project_id && m.project_id !== projectId) return <Banner tone="warn">This model version belongs to another project.</Banner>;
  const lineage = m.lineage;
  const to = (kind: string, id: string | null | undefined) => (id ? inspectorPath(projectId, kind, id) : null);
  const cv = Object.entries(m.metrics?.cv ?? {}).filter((e): e is [string, number] => typeof e[1] === "number");
  return (
    <>
      <PageGuide
        purpose="See what this model version is, what it was built from and whether it is the project's champion."
        howTo="Follow the links to the run, split plan, features and data it came from."
        youGet="Version, digest, algorithm, CV score, lineage and the champion ref state."
        attention={<>Only cross-validation numbers are shown here. The single labelled <Term definition={TERMS.holdout}>final test set (used once)</Term> evaluation is on the Card tab.</>}
      />
      <Reason>
        <p>Produced by run {link(to("experiment", lineage.experiment_id), mono(shortId(lineage.experiment_id)))} from the winning candidate <span className="mono">{shortId(lineage.candidate_id)}</span>. {card.data ? plainText(card.data.metric_in_words.text, 400) : ""}</p>
      </Reason>
      <Card title="Model version" aside={m.is_champion ? <Pill tone="ok">★ champion</Pill> : <Pill tone="gray">not the champion</Pill>}>
        <KeyValue items={[
          { key: "ver", label: "Version", value: m.version },
          { key: "id", label: "Id", value: mono(m.id) },
          { key: "digest", label: <Term definition={TERMS.digest}>Content digest</Term>, value: mono(m.content_digest) },
          { key: "algo", label: "Algorithm", value: m.algorithm ? `${plainText(m.algorithm, 60)}${m.family ? ` (${plainText(m.family, 60)})` : ""}` : "—" },
          { key: "created", label: "Created", value: formatWhen(m.created_at) },
          { key: "refs", label: "Ref state", value: m.ref_kinds?.length ? m.ref_kinds.map((r) => r.replaceAll("_", " ")).join(", ") : "No ref points at this version" },
          { key: "metric", label: "Selection metric", value: <>{mono(m.metrics?.selection_metric)} {m.metrics?.selected_score != null ? <>= {formatNumber(m.metrics.selected_score)} <span className="muted">(CV)</span></> : null}</> },
        ]} />
      </Card>
      <Card title="Built from">
        <KeyValue items={[
          { key: "exp", label: "Experiment", value: link(to("experiment", lineage.experiment_id), mono(shortId(lineage.experiment_id))) },
          { key: "split", label: "Split plan", value: lineage.split_plan_id ? link(to("split_plan", lineage.split_plan_id), mono(shortId(lineage.split_plan_id))) : "—" },
          { key: "feat", label: "Feature recipe", value: lineage.feature_recipe_id ? link(to("feature_recipe", lineage.feature_recipe_id), mono(shortId(lineage.feature_recipe_id))) : "—" },
          { key: "data", label: "Source dataset", value: lineage.source_dataset_id ? link(to("dataset_version", lineage.source_dataset_id), mono(shortId(lineage.source_dataset_id))) : "—" },
        ]} />
        {cv.length ? <p className="muted">CV metrics of the winner: {cv.map(([k, v]) => `${plainText(k, 40)} ${formatNumber(v)}`).join(" · ")}.</p> : null}
      </Card>
      <Card title="Summary from the card">
        {card.isError ? <QueryNotice error={card.error} what="model card" /> : card.isPending ? <p role="status">Loading the card…</p> : (
          <>
            <p>{plainText(card.data.metric_in_words.text, 500)}</p>
            <p className="muted">{plainText(card.data.baseline.text, 300)}</p>
          </>
        )}
      </Card>
    </>
  );
}

// --- graph drawer slot --------------------------------------------------------------

/** Compact inspector for the Graph drawer: one reason line and facts per kind, plus the full inspector link. */
export function NodeInspectorBody({ projectId, node, graph }: { projectId: string; node: StudioGraphNode; graph: StudioGraph }) {
  const kind = node.kind;
  const experimentKind = kind === "experiment";
  const expId = experimentKind ? node.id : kind === "split_plan" ? experimentUsing(graph.nodes, graph.edges, "split_plan", node.id) : kind === "feature_recipe" ? experimentUsing(graph.nodes, graph.edges, "feature_recipe", node.id) : null;
  const experiment = useExperimentDetail(expId);
  const build = useModelBuild(experiment.data?.workspace_id, expId ?? undefined);
  const dataset = useDatasetVersion(kind === "dataset_version" ? node.id : null);
  const model = useModelVersionRead(kind === "model_version" ? node.id : null);
  const href = inspectorPath(projectId, kind, node.id);
  const experimentsHref = projectHref(projectId, "experiments");
  let reason: ReactNode = null;
  if (kind === "experiment") reason = experiment.data?.intent ? plainText(experiment.data.intent, 300) : "A run: it trained candidates on a split plan and locked a winner on CV.";
  else if (kind === "split_plan") reason = splitFacts(build.data).reason ?? "Fixes the final test set and folds before any modelling.";
  else if (kind === "feature_recipe") reason = featureReason(build.data) ?? "How raw columns become model inputs.";
  else if (kind === "dataset_version") reason = dataset.data ? `${dataset.data.row_count} rows × ${dataset.data.column_count} columns, ${node.derived ? "prepared by a run" : "uploaded"}.` : "A dataset version.";
  else if (kind === "model_version") reason = model.data ? `${model.data.algorithm ?? "A model"}, produced by run ${shortId(model.data.lineage.experiment_id)}${model.data.is_champion ? "; the project's champion" : ""}.` : "A model version.";
  const m = experiment.data?.metrics;
  return (
    <section aria-label={`${kindLabel(kind)} inspector`}>
      <h3>Reason</h3>
      <p>{reason ?? <span className="muted">No inspector exists for this kind yet.</span>}</p>
      {experimentKind && experiment.data ? (
        <p>
          <Pill tone={STATUS_TONE[experiment.data.status] ?? "gray"}>{experiment.data.status.replaceAll("_", " ")}</Pill>{" "}
          {m?.selection_metric && m.selected_score != null ? <>CV {plainText(m.selection_metric, 40)} {formatNumber(m.selected_score)}</> : null}
        </p>
      ) : null}
      {kind === "feature_recipe" && build.data ? <p>{featureRows(build.data).length} features recorded in the run that used this recipe.</p> : null}
      {kind === "split_plan" && build.data ? <p>Strategy {splitFacts(build.data).strategy ?? "—"}; {splitFacts(build.data).locked ? "locked" : "lock not recorded"}.</p> : null}
      {experiment.isError && expId ? <p className="muted">The run behind this node could not be read.</p> : null}
      {href ? (
        <p className="toolbar">
          <Link className="btn" href={href}>Open full inspector</Link>
          {experimentKind && experiment.data?.status === "completed" && experimentsHref ? <Link className="btn" href={`${experimentsHref}?select=${node.id}`}>Compare with another run</Link> : null}
        </p>
      ) : null}
    </section>
  );
}
