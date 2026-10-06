"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { KeyValue } from "@/components/studio/KeyValue";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { StepBar, type Step } from "@/components/studio/StepBar";
import { Term } from "@/components/studio/Term";
import {
  ActionKeys, TASK_OPTIONS, WizardInputError, WIZARD_STEPS, buildSpecBody, constraintMetricOptions, createProblemSpec, createProject, dataStepProblem,
  mapWizardError, objectiveProblem, primaryMetricOptions, singleFlight, startExperiment, uploadDataset, useProjectDatasets,
  useProjectExperiments, useStudioExperiment, useWizardInvalidation, type ConstraintDraft, type PlainError, type TaskType,
  type UploadedDataset, type WizardStepId,
} from "@/lib/application";
import { projectHref } from "@/lib/application/command-search";
import { useDatasetProfile } from "@/lib/application/studio-data-hooks";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

type ProfileRow = NonNullable<UploadedDataset["columns"]>[number];
const PROFILE_COLUMNS: Column<ProfileRow>[] = [
  { key: "name", header: "Column", sortValue: (c) => c.name.toLowerCase(), render: (c) => <span className="mono">{c.name}</span> },
  { key: "dtype", header: "Type", sortValue: (c) => c.dtype, render: (c) => c.dtype },
  { key: "missing", header: "Missing", numeric: true, sortValue: (c) => c.missing_fraction, render: (c) => `${(c.missing_fraction * 100).toFixed(1)}%` },
];

const TERMS = {
  target: "The column you want to predict. Every other column is used to predict it.",
  task: "What kind of answer the target holds: a category (classification) or a number (regression).",
  metric: "The score DCLab uses to pick the best model. Higher or lower is better depending on the metric.",
  holdout: "Rows set aside before training and used once at the end to check the chosen model. Never used to choose or tune it, and its values are not shown here.",
  leakage: "A column that gives away the answer because it is only known after the outcome. DCLab checks for it after the run.",
};

function ErrorBanner({ error }: { error: PlainError | null }) {
  if (!error) return null;
  return <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner>;
}

/** New project (no `projectId`) or New run in an existing project (dataset and answers prefilled). */
export function NewRunWizard({ projectId }: { projectId?: string }) {
  const router = useRouter();
  const invalidate = useWizardInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const [step, setStep] = useState<WizardStepId>("data");
  const [createdProject, setCreatedProject] = useState<string | null>(null);
  const [projectName, setProjectName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [uploaded, setUploaded] = useState<UploadedDataset | null>(null);
  const [datasetId, setDatasetId] = useState<string | null>(null);
  const [task, setTask] = useState<TaskType>("classification");
  const [target, setTarget] = useState("");
  const [primaryMetric, setPrimaryMetric] = useState("");
  const [constraint, setConstraint] = useState<ConstraintDraft | null>(null);
  const [businessObjective, setBusinessObjective] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const project = projectId ?? createdProject;

  // New run: prefill from the project's latest experiment (dataset, task and target it used).
  const datasets = useProjectDatasets(projectId);
  const experiments = useProjectExperiments(projectId);
  const latest = useStudioExperiment(experiments.data?.items[0]?.id);
  const prefilled = useRef(false);
  useEffect(() => {
    if (!projectId || prefilled.current || !datasets.data) return;
    if (experiments.isPending || (experiments.data?.items.length && latest.isPending)) return;
    prefilled.current = true;
    const used = latest.data?.lineage.source_dataset_id;
    setDatasetId(datasets.data.find((d) => d.id === used)?.id ?? datasets.data[0]?.id ?? null);
    if (latest.data?.target_column) setTarget(latest.data.target_column);
    const known = TASK_OPTIONS.find((o) => o.value === latest.data?.task_type);
    if (known) setTask(known.value);
  }, [projectId, datasets.data, experiments.isPending, experiments.data, latest.isPending, latest.data]);

  const columns = uploaded?.columns ?? [];
  // New run on an existing dataset: the target choices are the profile's column names (P4.1-C).
  const profile = useDatasetProfile(uploaded ? null : datasetId);
  const columnNames = uploaded?.columns?.map((c) => c.name) ?? profile.data?.columns.map((c) => c.name) ?? [];
  const objective = { primaryMetric, constraint, businessObjective };
  const chosen = datasets.data?.find((d) => d.id === datasetId) ?? null;
  const stepState = (id: WizardStepId): Step["state"] => {
    const order = WIZARD_STEPS.findIndex((s) => s.id === id);
    const at = WIZARD_STEPS.findIndex((s) => s.id === step);
    return order < at ? "done" : order === at ? "current" : "todo";
  };
  const steps: Step[] = WIZARD_STEPS.map((s) => ({ id: s.id, label: s.label, state: stepState(s.id) }));

  // One action in flight at a time: a second click or Enter while busy joins the running call.
  const workRef = useRef<() => Promise<void>>(async () => {});
  const busyRef = useRef(false);
  const flight = useRef(singleFlight(() => workRef.current())).current;
  const run = (work: () => Promise<void>) => {
    if (!busyRef.current) {
      busyRef.current = true;
      workRef.current = async () => {
        setBusy(true);
        setError(null);
        try {
          await work();
        } catch (caught) {
          setError(mapWizardError(caught));
        } finally {
          busyRef.current = false;
          setBusy(false);
        }
      };
    }
    return flight();
  };

  const submitData = () => run(async () => {
    const problem = dataStepProblem({ projectId: project ?? null, projectName, file, datasetId });
    if (problem) throw new WizardInputError(problem);
    if (!datasetId && file) {
      let pid = project;
      if (!pid) {
        const created = await createProject({ name: projectName.trim(), key: keys.keyFor("project", projectName.trim()) });
        pid = created.id;
        setCreatedProject(pid);
      }
      const upload = await uploadDataset({ projectId: pid, file, key: keys.keyFor("upload", `${pid}:${file.name}:${file.size}:${file.lastModified}`) });
      setUploaded(upload);
      setDatasetId(upload.id);
      invalidate(pid);
    }
    setStep("target");
  });

  const train = () => run(async () => {
    const problem = objectiveProblem(objective);
    if (problem) throw new WizardInputError(problem);
    if (!project || !datasetId) throw new Error("Choose the data first.");
    const body = buildSpecBody(task, target, objective);
    const spec = await createProblemSpec({ projectId: project, body, key: keys.keyFor("spec", `${project}:${datasetId}:${JSON.stringify(body)}`) });
    const experiment = await startExperiment({
      projectId: project, datasetId, problemSpecId: spec.id, key: keys.keyFor("run", `${spec.id}:${datasetId}`),
    });
    keys.done("spec");
    keys.done("run");
    invalidate(project, experiment.id);
    const href = projectHref(project, "experiments", experiment.id);
    if (href) router.push(href);
  });

  const nav = (back: WizardStepId | null, next: ReactNode) => (
    <div className="toolbar">
      {back ? <button type="button" className="btn" disabled={busy} onClick={() => { setError(null); setStep(back); }}>Back</button> : null}
      {next}
    </div>
  );

  return (
    <>
      <PageHead
        eyebrow={projectId ? "New run" : "New project"}
        title={projectId ? "Start a new run" : "Create a project from your data"}
        subtitle="Upload a table, confirm what to predict, and train. Every answer here is your choice or a fixed rule; nothing needs AI."
        actions={<Link className="btn" href={projectId ? `/projects/${projectId}/experiments` : "/projects"}>Cancel</Link>}
      />
      <StepBar steps={steps} label="Wizard progress" />
      <ErrorBanner error={error} />

      {step === "data" ? (
        <>
          <PageGuide
            purpose={projectId ? "Pick the data this run trains on." : "Create the project and bring your data in."}
            howTo={projectId ? "Use a dataset already in this project, or upload a new file." : "Name the project and choose a CSV, TSV, JSON, Parquet or XLSX file with a header row."}
            youGet="A stored, versioned dataset with its row count, columns, types and missing values. Nothing is trained yet."
            attention={<>The file is checked for structure only. A <Term definition={TERMS.holdout}>final holdout</Term> is set aside later, when the split is planned.</>}
          />
          <Card title="Data">
            <form className="form" onSubmit={(event) => { event.preventDefault(); void submitData(); }}>
              {!project ? (
                <label className="field"><span>Project name</span>
                  <input value={projectName} maxLength={120} onChange={(e) => setProjectName(e.target.value)} required disabled={busy} />
                </label>
              ) : null}
              {projectId && datasets.data?.length ? (
                <label className="field"><span>Dataset in this project</span>
                  <select value={datasetId ?? ""} onChange={(e) => setDatasetId(e.target.value || null)} disabled={busy}>
                    {datasets.data.map((d) => <option key={d.id} value={d.id}>{d.name} · {d.version} · {d.row_count} rows</option>)}
                    <option value="">Upload a new file instead</option>
                  </select>
                </label>
              ) : null}
              {!datasetId ? (
                <label className="field"><span>Data file</span>
                  <input type="file" accept=".csv,.tsv,.json,.jsonl,.parquet,.xlsx" disabled={busy || Boolean(uploaded)}
                    onChange={(e) => { setFile(e.target.files?.[0] ?? null); setUploaded(null); }} />
                </label>
              ) : null}
              {chosen ? (
                <KeyValue items={[
                  { key: "id", label: "Dataset id", value: <span className="mono">{chosen.id}</span> },
                  { key: "rows", label: "Rows", value: chosen.row_count }, { key: "cols", label: "Columns", value: chosen.column_count },
                  { key: "digest", label: "Content digest", value: <span className="mono">{chosen.content_digest ?? "—"}</span> },
                ]} />
              ) : null}
              {nav(null, <button type="submit" className="btn primary" disabled={busy}>{busy ? "Uploading…" : datasetId ? "Continue" : "Upload and continue"}</button>)}
            </form>
          </Card>
        </>
      ) : null}

      {step === "target" ? (
        <>
          <PageGuide
            purpose={<>Say what to predict and what kind of answer it is.</>}
            howTo={<>Choose the <Term definition={TERMS.target}>target</Term> column and the <Term definition={TERMS.task}>task</Term>. Or let DCLab choose the target: if it is unsure it stops and asks you on the experiment page, showing its rule suggestion.</>}
            youGet="A fixed target and task for this run, recorded in the project's objective."
            attention={<>Do not pick a column that is only known after the outcome; that is <Term definition={TERMS.leakage}>leakage</Term> and DCLab flags it after the run.</>}
          />
          {uploaded ? (
            <Card title="Profile of the uploaded file">
              <KeyValue items={[
                { key: "name", label: "File", value: uploaded.name }, { key: "id", label: "Dataset id", value: <span className="mono">{uploaded.id}</span> },
                { key: "rows", label: "Rows", value: uploaded.row_count }, { key: "cols", label: "Columns", value: uploaded.column_count },
                { key: "ing", label: "Ingestion", value: `${uploaded.ingestion.status} · ${uploaded.ingestion.publication_state}` },
                { key: "digest", label: "Content digest", value: <span className="mono">{uploaded.content_digest ?? "—"}</span> },
              ]} />
              <DataTable caption="Columns of the uploaded file" columns={PROFILE_COLUMNS} rows={columns} rowKey={(c) => c.name} />
              <p className="muted">Missing values are counted over the whole upload, before any split exists.</p>
            </Card>
          ) : null}
          <Card title="Target and task">
            <form className="form" onSubmit={(event) => { event.preventDefault(); setStep("objective"); }}>
              <label className="field"><span>Task</span>
                <select value={task} onChange={(e) => setTask(e.target.value as TaskType)}>
                  {TASK_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              {columnNames.length ? (
                <label className="field"><span>Target column</span>
                  <select value={columnNames.includes(target) ? target : ""} onChange={(e) => setTarget(e.target.value)}>
                    <option value="">Let DCLab choose (I confirm if unclear)</option>
                    {columnNames.map((name) => <option key={name} value={name}>{name}</option>)}
                  </select>
                </label>
              ) : (
                <label className="field"><span>Target column (leave empty to let DCLab choose)</span>
                  <input value={target} onChange={(e) => setTarget(e.target.value)} maxLength={256} />
                </label>
              )}
              {nav("data", <button type="submit" className="btn primary">Continue</button>)}
            </form>
          </Card>
        </>
      ) : null}

      {step === "objective" ? (
        <>
          <PageGuide
            purpose="Optionally say what good looks like."
            howTo={<>Pick the <Term definition={TERMS.metric}>metric</Term> to optimise, and at most one limit it must respect (for example recall at least 0.8). Leave both empty to use DCLab&apos;s default for the task.</>}
            youGet="A recorded objective. The decision threshold is chosen from out-of-fold predictions only."
            attention="The metric and limit are checked against the task when you train; an invalid pair is refused with a reason."
          />
          <Card title="Objective and constraint">
            <form className="form" onSubmit={(event) => { event.preventDefault(); const p = objectiveProblem(objective); if (p) setError({ title: "Check the constraint", detail: p, fixable: true }); else { setError(null); setStep("train"); } }}>
              <label className="field"><span>What do you want to achieve? (optional)</span>
                <textarea value={businessObjective} maxLength={500} rows={3} onChange={(e) => setBusinessObjective(e.target.value)} />
              </label>
              <label className="field"><span>Primary metric</span>
                <select value={primaryMetric} onChange={(e) => setPrimaryMetric(e.target.value)}>
                  <option value="">DCLab default for the task</option>
                  {primaryMetricOptions(task).map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
              <label className="field"><span>Constraint metric (optional)</span>
                <select value={constraint?.metric ?? ""} onChange={(e) => setConstraint(e.target.value ? { metric: e.target.value, op: constraint?.op ?? ">=", value: constraint?.value ?? "" } : null)}>
                  <option value="">No constraint</option>
                  {constraintMetricOptions(task).map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              </label>
              {constraint ? (
                <div className="row">
                  <label className="field"><span>Must be</span>
                    <select value={constraint.op} onChange={(e) => setConstraint({ ...constraint, op: e.target.value as ">=" | "<=" })}>
                      <option value=">=">at least</option><option value="<=">at most</option>
                    </select>
                  </label>
                  <label className="field"><span>Value</span>
                    <input inputMode="decimal" value={constraint.value} onChange={(e) => setConstraint({ ...constraint, value: e.target.value })} />
                  </label>
                </div>
              ) : null}
              {nav("target", <button type="submit" className="btn primary">Continue</button>)}
            </form>
          </Card>
        </>
      ) : null}

      {step === "train" ? (
        <>
          <PageGuide
            purpose="Review and start the run."
            howTo="Check the summary, then press Train. Pressing it twice is safe: a retry reuses the same request."
            youGet="An experiment you can follow live. Training runs in the background worker."
            attention={<>DCLab sets aside a <Term definition={TERMS.holdout}>final holdout</Term> and never shows or tunes on it. If the target or split needs your answer, the experiment page asks.</>}
          />
          <Card title="Summary">
            <KeyValue items={[
              { key: "project", label: "Project", value: <span className="mono">{project}</span> },
              { key: "dataset", label: "Dataset", value: <span className="mono">{datasetId}</span> },
              { key: "task", label: "Task", value: task }, { key: "target", label: "Target column", value: target ? <span className="mono">{target}</span> : "DCLab chooses; you confirm if unclear" },
              { key: "metric", label: "Primary metric", value: primaryMetric || "default for the task" },
              { key: "constraint", label: "Constraint", value: constraint ? `${constraint.metric} ${constraint.op} ${constraint.value}` : "none" },
            ]} />
            {nav("objective", <button type="button" className="btn primary" disabled={busy} onClick={() => void train()}>{busy ? "Starting…" : "Train"}</button>)}
          </Card>
        </>
      ) : null}
    </>
  );
}
