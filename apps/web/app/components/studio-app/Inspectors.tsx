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
import { QueryNotice, formatWhen, statusTone } from "@/app/components/studio-app/StudioParts";
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
import { attentionCount, findingsState } from "@/lib/application/studio-findings";
import { designKind, metricInfo, taskLabel, ruleStatusText } from "@/lib/application/studio-goal";
import { changeSentences, familyLabel, selectionScore, statusWords } from "@/lib/application/studio-runs";
import { useProjectExperiments } from "@/lib/application";
import { designLabels, dataVersionName, modelName, runOrdinals } from "@/lib/application/studio-names";
import { modelSentence, runRef, usedAsWords } from "@/lib/application/studio-model";
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
  digest: "A checksum: a short code worked out from the content. Two items with the same checksum hold the same bytes.",
  importance: "How much the cross-validation score drops when a column's values are shuffled, measured on validation folds only.",
  leakage: GLOSSARY.leakage.definition,
  threshold: GLOSSARY.threshold.definition,
  changeSet: "What was changed on top of the run this one is based on. The data and test design stay the same, so the two can be compared.",
  level: "Advice only: saved, never applied. Ask first: shown for a person to accept or reject. Advice never changes the project by itself.",
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
  if (!code) return <Banner tone="warn">Code to reproduce this run is not available{error instanceof Error ? ` (${error.message})` : ""}. Runs from before the code generator, or runs that are still going, have none.</Banner>;
  return (
    <>
      <p className="muted">
        Generated from the saved settings of this run by software version <span className="mono">{code.generator_version}</span>; recipe checksum <span className="mono">{code.spec_digest}</span>. {code.standalone_cv ? "Runs cross-validation only; it does not touch the final test set." : "It needs the inputs below."} Shown as text: DCLab never runs it for you.
      </p>
      {code.inputs?.length ? (
        <ul className="plain-list" aria-label="Inputs the script needs">
          {code.inputs.map((input) => <li key={input.name}><b>{input.name}</b> <span className="mono">{input.env_var}</span>: {plainText(input.description, 200)}</li>)}
        </ul>
      ) : null}
      <h3>Script <span className="mono muted">{code.script.filename}</span></h3>
      <CodeActions filename={code.script.filename} source={code.script.source} label="the script" />
      <CodeBlock code={code.script.source} label={`Generated script ${code.script.filename}`} language="python" />
      <p className="muted">Script checksum <span className="mono">{code.script.content_digest}</span>. The notebook <span className="mono">{code.notebook.filename}</span> holds the same steps.</p>
      <CodeActions filename={code.notebook.filename} source={code.notebook.source} label="the notebook JSON" />
    </>
  );
}

function CandidatesTab({ rows, metrics }: { rows: CandidateRow[]; metrics: string[] }) {
  const columns: Column<CandidateRow>[] = [
    { key: "algo", header: "Model tried", sortValue: (c) => c.algorithm, render: (c) => <>{plainText(c.algorithm, 80)} {c.selected ? <Pill tone="ok">best</Pill> : c.runnerUp ? <Pill tone="gray">second best</Pill> : null}</> },
    { key: "family", header: "Family", render: (c) => familyLabel(c.family) ?? "—" },
    { key: "folds", header: "Folds", numeric: true, sortValue: (c) => c.folds, render: (c) => c.folds },
    ...metrics.slice(0, 4).map((name): Column<CandidateRow> => ({ key: `m-${name}`, header: `${metricInfo(name)?.label ?? name} (cross-validation)`, numeric: true, sortValue: (c) => c.cv[name] ?? -Infinity, render: (c) => formatNumber(c.cv[name]) })),
    { key: "fp", header: "Settings checksum", render: (c) => <span className="mono">{c.fingerprint.slice(0, 12)}</span> },
    { key: "hp", header: "Settings", render: (c) => (c.hyperparameters.length ? <span className="mono">{c.hyperparameters.map(([k, v]) => `${plainText(k, 40)}=${plainText(v, 40)}`).join(", ")}</span> : "—") },
  ];
  return (
    <>
      <p className="muted">The <Term definition={TERMS.digest}>settings checksum</Term> identifies the exact settings. Models are compared on <Term definition={TERMS.cv}>cross-validation</Term> only. The final test set never takes part in the comparison and is not shown here.</p>
      <DataTable caption="Models tried and their cross-validation scores" columns={columns} rows={rows} rowKey={(c) => c.id} emptyMessage="No model is recorded for this run yet." />
    </>
  );
}

function FoldsTab({ rows, metrics, detail, sizes }: { rows: FoldRow[]; metrics: string[]; detail?: StudioExperimentDetail; sizes: Array<{ fold: number; trainRows: number | null; validationRows: number | null }> }) {
  const columns: Column<FoldRow>[] = [
    { key: "cand", header: "Model tried", sortValue: (f) => f.candidate, render: (f) => plainText(f.candidate, 80) },
    { key: "fold", header: "Fold", numeric: true, sortValue: (f) => f.fold, render: (f) => f.fold },
    { key: "train", header: "Rows learned from", numeric: true, render: (f) => f.trainRows ?? "—" },
    { key: "val", header: "Rows checked on", numeric: true, render: (f) => f.validationRows ?? "—" },
    ...metrics.slice(0, 4).map((name): Column<FoldRow> => ({ key: `m-${name}`, header: name, numeric: true, sortValue: (f) => f.metrics[name] ?? -Infinity, render: (f) => formatNumber(f.metrics[name]) })),
  ];
  const m = detail?.metrics;
  return (
    <>
      <p className="muted">A <Term definition={TERMS.fold}>fold</Term> is one part of the training rows that is held out and scored once.</p>
      <KeyValue items={[
        { key: "metric", label: "Models are ranked on", value: m?.selection_metric ? (metricInfo(m.selection_metric)?.label ?? mono(m.selection_metric)) : "—" },
        { key: "score", label: "Cross-validation score of the best model", value: formatNumber(selectionScore(m?.selection_metric, m?.cv, m?.selected_score)) },
        { key: "thr", label: <Term definition={TERMS.threshold}>Decision threshold</Term>, value: m?.decision_threshold == null ? "none (not a thresholded task, or none chosen)" : <>{formatNumber(m.decision_threshold)} <span className="muted">chosen on cross-validation</span></> },
        { key: "folds", label: "Fold sizes", value: sizes.length ? sizes.map((s) => `fold ${s.fold}: ${s.trainRows ?? "—"} learned from / ${s.validationRows ?? "—"} checked on`).join(" · ") : "—" },
      ]} />
      <DataTable caption="Cross-validation scores per fold" columns={columns} rows={rows} rowKey={(f) => f.id} emptyMessage="No fold result is recorded for this run yet." />
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
      <h3>Trust checks</h3>
      {findings.isError ? <QueryNotice error={findings.error} what="trust checks" /> : null}
      <p className="muted" data-testid="findings-summary">
        {findingsState(findings.data) === "pending" ? "Loading the trust checks…"
          : findingsState(findings.data) === "not_computed" ? "No trust checks are saved for this run yet (not the same as passed)."
            : attentionCount(findings.data) ? `${attentionCount(findings.data)} of ${findingsTotal} trust checks need a look.` : `All ${findingsTotal} trust checks passed.`}
        {" "}How serious each is, the numbers and what to do are in the Trust checks card above.
      </p>
      {/* The AI reviewer is advice only and exists only when AI is on: with AI off (no review saved) nothing is shown. */}
      {proposals.isError ? <QueryNotice error={proposals.error} what="AI reviewer notes" /> : null}
      {reviews.length ? <h3><Term definition={TERMS.level}>AI reviewer</Term> <span className="muted">(advice only)</span></h3> : null}
      {reviews.map((p) => {
        const v = reviewView(p);
        return (
          <article key={p.id} className="card flat" aria-label={`AI reviewer note ${shortId(p.id)}`}>
            <p className="toolbar">
              <Pill tone="ai"><span aria-hidden="true">◆ </span>{v.verdict ? v.verdict.replaceAll("_", " ") : "no verdict"}</Pill>
              {v.level !== null ? <Level level={v.level} /> : null}
              <span className="muted">{v.status} · by {v.by} · {formatWhen(p.created_at)}{v.confidence !== null ? ` · confidence ${formatNumber(v.confidence)}` : ""}</span>
            </p>
            {v.summary ? <p>{plainText(v.summary, 2000)}</p> : null}
            <table className="graph-answers">
              <caption className="sr-only">Rule answer beside the AI reviewer&apos;s answer</caption>
              <thead><tr><th scope="col">Rule&apos;s answer</th><th scope="col">AI reviewer (advice only)</th></tr></thead>
              <tbody><tr><td>{v.ruleAnswer ? <span className="mono">{plainText(v.ruleAnswer, 300)}</span> : "No rule answer saved"}</td><td>{v.verdict ? v.verdict.replaceAll("_", " ") : "—"}</td></tr></tbody>
            </table>
            {v.metrics.length ? <p>Cites cross-validation values: {v.metrics.map((m) => `${plainText(metricInfo(m.metric)?.label ?? m.metric, 40)} ${formatNumber(m.value)}`).join(", ")}.</p> : null}
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
                  {lineage.parent_experiment_id ? <>Based on run {link(href("experiment", lineage.parent_experiment_id), mono(shortId(lineage.parent_experiment_id)))}, </> : "Started from scratch, "}
                  using data version {link(href("dataset_version", lineage.source_dataset_id), mono(shortId(lineage.source_dataset_id ?? "")))} and test design {link(href("split_plan", lineage.split_plan_id), mono(shortId(lineage.split_plan_id ?? "")))}.
                </p>
              </Reason>
              {detail.isError ? <QueryNotice error={detail.error} what="experiment detail" /> : null}
              <h3>Settings</h3>
              <KeyValue items={[
                { key: "task", label: "Kind of answer", value: taskLabel(d?.task_type) ?? "—" },
                { key: "target", label: "Column to predict", value: mono(d?.target_column) },
                { key: "sel", label: "Models are ranked on", value: d?.metrics?.selection_metric ? (metricInfo(d.metrics.selection_metric)?.label ?? mono(d.metrics.selection_metric)) : "—" },
                { key: "fam", label: "Best model", value: familyLabel(d?.metrics?.family) ?? "—" },
                { key: "constraint", label: "Business rule", value: ruleStatusText(d?.metrics?.constraint_status) },
                { key: "model", label: "Model", value: modelVersionId ? link(href("model_version", modelVersionId), mono(shortId(modelVersionId))) : "None yet" },
              ]} />
              <h3><Term definition={TERMS.changeSet}>What changed</Term></h3>
              {changeSet.length ? (
                <>
                  <ul className="plain-list">{changeSentences(d?.change_set).map((line, i) => <li key={i}>{line}</li>)}</ul>
                  <details><summary>Exact change (technical)</summary><KeyValue items={changeSet.map(([k, v]) => ({ key: k, label: <span className="mono">{plainText(k, 60)}</span>, value: <span className="mono">{plainText(typeof v === "string" ? v : JSON.stringify(v), 300)}</span> }))} /></details>
                </>
              ) : <p className="muted">Nothing was changed: this run did not start from another run.</p>}
              {d?.diff_vs_parent ? <details><summary>Difference from the run it is based on (technical)</summary><span className="mono">{plainText(JSON.stringify(d.diff_vs_parent), 400)}</span></details> : null}
              <h3><Term definition={TERMS.cv}>Cross-validation scores</Term></h3>
              {cvEntries.length ? <KeyValue items={cvEntries.map(([k, v]) => ({ key: k, label: plainText(metricInfo(k)?.label ?? k, 60), value: formatNumber(v) }))} /> : <p className="muted">No cross-validation scores are saved yet.</p>}
              <p className="muted">The <Term definition={TERMS.holdout}>final test set (used once per run)</Term> is scored once in this run, for the chosen model and is not shown on this page.</p>
              <FindingsAndReview projectId={projectId} experimentId={experimentId} />
            </>
          ),
        },
        { id: "candidates", label: "Models tried", count: candidates.length, content: build.isError ? <QueryNotice error={build.error} what="model build" /> : build.isPending ? <p role="status">Loading the models tried…</p> : <CandidatesTab rows={candidates} metrics={cvMetrics} /> },
        { id: "folds", label: "Per fold", content: build.isError ? <QueryNotice error={build.error} what="model build" /> : build.isPending ? <p role="status">Loading folds…</p> : <FoldsTab rows={folds} metrics={foldMetrics} detail={d} sizes={foldSizes(build.data)} /> },
        { id: "importance", label: "Feature importance", content: <DriversTab modelVersionId={modelVersionId} /> },
        { id: "code", label: "Code", content: <CodeTab code={code.data} error={code.error} pending={code.isPending} /> },
        { id: "evidence", label: "Build record", content: (
          <>
            <p className="muted">The steps of this run in plain words are shown at the top of the page. This is the full technical record, for specialists.</p>
            <div className="legacy-surface"><ModelBuildInspector workspaceId={workspaceId} pipelineRunId={experimentId} /></div>
          </>
        ) },
      ]}
    />
  );
}

// --- nodes backed by an experiment's build ----------------------------------------------

/** Split plans and feature recipes have no read of their own: they are read through the newest run that uses them. */
export function useBackingRun(projectId: string, kind: "split_plan" | "feature_recipe", nodeId: string) {
  const graph = useProjectGraph(projectId);
  const node = graph.data?.nodes.find((n) => n.kind === kind && n.id === nodeId) ?? null;
  const experimentId = graph.data ? experimentUsing(graph.data.nodes, graph.data.edges, kind, nodeId) : null;
  const experiment = useExperimentDetail(experimentId);
  const build = useModelBuild(experiment.data?.workspace_id, experimentId ?? undefined);
  return { graph, node, experimentId, experiment, build };
}

function NotInGraph({ what }: { what: string }) {
  return <Banner tone="warn">This {what} is not among the newest runs of this project, or you cannot see it. The lineage page loads the newest runs first; older items open from its &ldquo;Show older runs&rdquo; button.</Banner>;
}

function NodeFacts({ node }: { node: StudioGraphNode }) {
  return (
    <KeyValue items={[
      { key: "id", label: "Id", value: mono(node.id) },
      { key: "digest", label: <Term definition={TERMS.digest}>Checksum</Term>, value: mono(node.digest) },
      { key: "created", label: "Created", value: formatWhen(node.created_at) },
      { key: "status", label: "Status", value: node.status ? node.status.replaceAll("_", " ") : "—" },
      { key: "stale", label: "Built on an older version", value: node.stale ? "Yes: made from a version that is no longer in use" : "No" },
    ]} />
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
  return <DataTable caption="Features of this run" columns={columns} rows={rows} rowKey={(f) => f.id} emptyMessage="No feature is recorded for this run." />;
}

function PrepTable({ steps }: { steps: PrepStep[] }) {
  const columns: Column<PrepStep>[] = [
    { key: "seq", header: "Step", numeric: true, sortValue: (s) => s.sequence, render: (s) => s.sequence },
    { key: "scope", header: "Columns", render: (s) => s.scope },
    { key: "type", header: "Kind", render: (s) => s.type },
    { key: "class", header: "Treatment (software name)", render: (s) => <span className="mono">{plainText(s.transformer, 80)}</span> },
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
  if (graph.isPending) return <p role="status">Loading the features…</p>;
  if (!node) return <NotInGraph what="set of features" />;
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
        <p>{reason ? plainText(reason, 500) : "These features record how raw columns become model inputs."}</p>
        <p className="muted">Read from {experimentId ? <>run {link(inspectorPath(projectId, "experiment", experimentId), mono(shortId(experimentId)))}, the loaded run that produced these features.</> : "no loaded run: the run that produced these features is outside the graph window."}</p>
      </Reason>
      <Card title="Features"><NodeFacts node={node} /></Card>
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
  if (version.isError) return <QueryNotice error={version.error} what="data version" />;
  if (!version.data) return <p role="status">Loading the data version…</p>;
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
        howTo="Read the shape and checksum, the profile summary, then the AI investigation if one exists. The Data page has the full column table."
        youGet="Rows, columns, checksum, role counts, columns left out for leakage and the rule answer beside any AI answer."
        attention="Statistics use training rows only; the final test set is never counted."
      />
      <Reason>
        <p>{node?.intent ? plainText(node.intent, 400) : "An uploaded or prepared table that experiments are trained on."} {node?.derived ? "It was prepared by a run from an upload." : "It is an upload."}</p>
        <p className="muted">{node?.stale ? "Built on an older version: this version is no longer in use." : "Not built on an older version."} Open <Link href={dataHref}>the Data page</Link> for every version and the policy.</p>
      </Reason>
      <Card title="Data version">
        <KeyValue items={[
          { key: "name", label: "Name", value: `${plainText(v.name, 120)} · ${v.version}` },
          { key: "shape", label: "Rows × columns", value: `${v.row_count} × ${v.column_count}` },
          { key: "id", label: "Id", value: mono(v.id) },
          { key: "digest", label: <Term definition={TERMS.digest}>Checksum</Term>, value: mono(v.content_digest) },
          { key: "created", label: "Uploaded", value: formatWhen(v.created_at) },
          { key: "ai", label: "Data class for AI", value: <Pill tone={v.policy.ai_data_class === "none" ? "gray" : "ai"}>{v.policy.ai_data_class}</Pill> },
        ]} />
      </Card>
      <Card title="Profile summary">
        {profile.isError ? <QueryNotice error={profile.error} what="profile" /> : profile.isPending ? <p role="status">Loading the profile…</p> : (
          <KeyValue items={[
            { key: "scope", label: "Statistics", value: profile.data?.statistics_status === "computed" ? `Computed over ${profile.data.split_plan?.training_row_count ?? "—"} training rows` : "Not available yet: names and types only" },
            { key: "roles", label: "Roles used", value: roles.size ? [...roles].map(([r, n]) => `${r.replaceAll("_", " ")} ${n}`).join(" · ") : "—" },
            { key: "leak", label: <Term definition={TERMS.leakage}>Columns left out for leakage</Term>, value: excluded },
            { key: "imp", label: "Importance method", value: profile.data?.importance_method ?? "—" },
          ]} />
        )}
      </Card>
      {proposals.isError || investigations.length ? (
      <Card title="AI investigation" aside={<span className="muted">advice only</span>}>
        {proposals.isError ? <QueryNotice error={proposals.error} what="AI investigation notes" /> : null}
        {investigations.map((p) => {
          const inv = investigationView(p);
          const ruleTarget = cols.find((c) => c.rule_role === "target")?.name;
          return (
            <article key={p.id} aria-label={`Investigation ${shortId(p.id)}`}>
              <p className="toolbar"><Pill tone="ai"><span aria-hidden="true">◆ </span>AI data investigator</Pill>{inv.level !== null ? <Level level={inv.level} /> : null}<span className="muted">{inv.status} · {formatWhen(p.created_at)}</span></p>
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
      ) : null}
    </>
  );
}

export function ModelInspector({ projectId, modelVersionId }: { projectId: string; modelVersionId: string }) {
  const model = useModelVersionRead(modelVersionId);
  const card = useModelCardRead(modelVersionId);
  const runs = useProjectExperiments(projectId);
  const sourceId = model.data?.lineage.source_dataset_id;
  const dataset = useDatasetVersion(sourceId);
  if (model.isError) return <QueryNotice error={model.error} what="model" />;
  if (!model.data) return <p role="status">Loading the model…</p>;
  const m = model.data;
  if (m.project_id && m.project_id !== projectId) return <Banner tone="warn">This model belongs to another project.</Banner>;
  const lineage = m.lineage;
  const to = (kind: string, id: string | null | undefined) => (id ? inspectorPath(projectId, kind, id) : null);
  const cv = Object.entries(m.metrics?.cv ?? {}).filter((e): e is [string, number] => typeof e[1] === "number");
  const items = runs.data?.items ?? [];
  const partial = !!runs.data?.next_cursor;
  const run = runRef(runOrdinals(items), partial || !runs.data, lineage.experiment_id);
  const design = lineage.split_plan_id && runs.data && !partial
    ? designLabels([...items].sort((a, b) => a.created_at.localeCompare(b.created_at)).map((r) => r.split_plan_id)).get(lineage.split_plan_id) ?? null
    : null;
  const dataName = dataset.data ? dataVersionName(plainText(dataset.data.name, 120), dataset.data.created_at) : null;
  const score = selectionScore(m.metrics?.selection_metric, m.metrics?.cv, m.metrics?.selected_score);
  const scoreName = m.metrics?.selection_metric ? (metricInfo(m.metrics.selection_metric)?.label ?? m.metrics.selection_metric) : null;
  const summary = modelSentence({
    family: m.family, algorithm: m.algorithm, run, data: dataName, designKind: designKind(card.data?.split.validation_strategy), folds: card.data?.split.validation_folds ?? null,
    trained: m.created_at ? formatWhen(m.created_at) : null, inUse: m.is_champion,
  });
  return (
    <>
      <PageGuide
        purpose="See what this model is, what it was built from and whether it is the model in use."
        howTo="Read the summary, then follow the links to the run, test design, features and data it came from."
        youGet="The kind of model, the run that built it, its cross-validation score, what it was built from and whether it is in use."
        attention={<>Only cross-validation numbers are shown here. The single labelled <Term definition={TERMS.holdout}>final test (used once per run)</Term> is on the Model card tab.</>}
      />
      <Card title={plainText(modelName(plainText(m.version, 40)), 60)} aside={m.is_champion ? <Pill tone="ok">★ in use</Pill> : <Pill tone="gray">not in use</Pill>}>
        <p className="mc-words">{summary}</p>
        <KeyValue items={[
          { key: "ver", label: "Model", value: `${modelName(plainText(m.version, 40))} · ${m.algorithm ? plainText(familyLabel(m.family || m.algorithm) ?? m.algorithm, 60) : "kind not recorded"}` },
          { key: "refs", label: "Used as", value: usedAsWords(m.ref_kinds, m.is_champion) },
          { key: "created", label: "Trained", value: formatWhen(m.created_at) },
          { key: "metric", label: "Score used to choose it", value: scoreName && score !== null ? <>{scoreName} = {formatNumber(score)} <span className="muted">(cross-validation)</span></> : "Not recorded" },
        ]} />
        <details className="ids">
          <summary>Technical details</summary>
          <p>Model id <span className="mono">{m.id}</span> · <Term definition={TERMS.digest}>checksum</Term> <span className="mono">{plainText(m.content_digest, 80)}</span>{m.algorithm ? <> · algorithm <span className="mono">{plainText(m.algorithm, 60)}</span></> : null}</p>
        </details>
      </Card>
      <Card title="Built from">
        <KeyValue items={[
          { key: "exp", label: "Run", value: <>{link(to("experiment", lineage.experiment_id), run ?? "Run")} <span className="muted">(id <span className="mono">{shortId(lineage.experiment_id)}</span>)</span></> },
          { key: "split", label: "Test design", value: lineage.split_plan_id ? link(to("split_plan", lineage.split_plan_id), design ?? `Test design ${shortId(lineage.split_plan_id)}`) : "—" },
          { key: "feat", label: "Features", value: lineage.feature_recipe_id ? link(to("feature_recipe", lineage.feature_recipe_id), "Features used by the run") : "—" },
          { key: "data", label: "Data file", value: lineage.source_dataset_id ? (dataset.isError ? link(to("dataset_version", lineage.source_dataset_id), "Open the data version") : link(to("dataset_version", lineage.source_dataset_id), dataName ?? "Loading the data file…")) : "—" },
        ]} />
        {cv.length ? <p className="muted">Cross-validation scores of this model: {cv.map(([k, v]) => `${plainText(metricInfo(k)?.label ?? k, 40)} ${formatNumber(v)}`).join(" · ")}.</p> : null}
      </Card>
      <Card title="Summary from the model card">
        {card.isError ? <QueryNotice error={card.error} what="model card" /> : card.isPending ? <p role="status">Loading the card…</p> : (
          <>
            <p>{plainText(card.data.metric_in_words.text, 500)}</p>
            {card.data.metric_in_words.caveat ? <p className="muted">{plainText(card.data.metric_in_words.caveat, 600)}</p> : null}
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
  const runList = useProjectExperiments(kind === "model_version" ? projectId : undefined);
  const href = inspectorPath(projectId, kind, node.id);
  const experimentsHref = projectHref(projectId, "experiments");
  let reason: ReactNode = null;
  if (kind === "experiment") reason = experiment.data?.intent ? plainText(experiment.data.intent, 300) : "A run: it trained several models and chose the best on cross-validation.";
  else if (kind === "split_plan") reason = splitFacts(build.data).reason ?? "Fixes the final test set and folds before any modelling.";
  else if (kind === "feature_recipe") reason = featureReason(build.data) ?? "How raw columns become model inputs.";
  else if (kind === "dataset_version") reason = dataset.data ? `${dataset.data.row_count} rows × ${dataset.data.column_count} columns, ${node.derived ? "prepared by a run" : "uploaded"}.` : "A data version.";
  else if (kind === "model_version") reason = model.data ? `${familyLabel(model.data.family || model.data.algorithm) ?? "A model"} (${modelName(plainText(model.data.version, 40))}), built by ${runRef(runOrdinals(runList.data?.items ?? []), !!runList.data?.next_cursor || !runList.data, model.data.lineage.experiment_id)}${model.data.is_champion ? "; the model in use" : ""}.` : "A model.";
  const m = experiment.data?.metrics;
  return (
    <section aria-label={`${kindLabel(kind)} inspector`}>
      <h3>Reason</h3>
      <p>{reason ?? <span className="muted">No inspector exists for this kind yet.</span>}</p>
      {experimentKind && experiment.data ? (
        <p>
          <Pill tone={statusTone(experiment.data.status)}>{statusWords(experiment.data.status)}</Pill>{" "}
          {m?.selection_metric && selectionScore(m.selection_metric, m.cv, m.selected_score) !== null ? <>Cross-validation {plainText(metricInfo(m.selection_metric)?.label ?? m.selection_metric, 40)} {formatNumber(selectionScore(m.selection_metric, m.cv, m.selected_score))}</> : null}
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
