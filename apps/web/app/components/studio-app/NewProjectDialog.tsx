"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { Banner } from "@/components/studio/Banner";
import { KeyValue } from "@/components/studio/KeyValue";
import { Pill } from "@/components/studio/Pill";
import { StepBar } from "@/components/studio/StepBar";
import {
  ActionKeys, DIALOG_STEPS, TASK_OPTIONS, WizardInputError, buildSpecBody, constraintMetricOptions, createProblemSpec, createProject, dataStepProblem,
  dialogStepState, objectiveProblem, plainWizardError, primaryMetricOptions, singleFlight, startExperiment, testDesignRules, uploadDataset,
  useWizardInvalidation, type ConstraintDraft, type DialogStepId, type PlainError, type TaskType, type UploadedDataset, type WizardContext,
} from "@/lib/application";
import { plainText, projectHref } from "@/lib/application/command-search";
import { metricInfo } from "@/lib/application/studio-goal";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

/**
 * "New project" (D2b, founder decision 15): a three-step dialog. 1 a name and a file (creates the project and uploads
 * the file: `POST /v1/projects`, `POST /v1/datasets`); 2 what to predict and the success rule (nothing is sent);
 * 3 the test design the fixed rules will make, which the person confirms. Only that confirm writes the goal and starts
 * training (`POST /v1/projects/{id}/problem-specs`, `POST /v1/experiments`). If the run needs an answer (an unclear
 * target, or a connected tool's plan that changes the split) it waits on the run page, which answers through the
 * execution-request target and split confirmation routes. Every write carries one Idempotency-Key per action and runs
 * single-flight; a refused call shows a fixed sentence (never the server's words). The four-step page at /projects/new
 * stays reachable from here.
 */
const STEP_TITLE: Record<DialogStepId, string> = { file: "Name and file", goal: "What to predict", design: "Test design" };

export function NewProjectDialog({ label = "New project", primary = true }: { label?: string; primary?: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const router = useRouter();
  const invalidate = useWizardInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const [step, setStep] = useState<DialogStepId>("file");
  const [projectId, setProjectId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [uploaded, setUploaded] = useState<UploadedDataset | null>(null);
  const [task, setTask] = useState<TaskType>("classification");
  const [target, setTarget] = useState("");
  const [metric, setMetric] = useState("");
  const [rule, setRule] = useState<ConstraintDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const context = useRef<WizardContext>("project");
  const stepHeading = useRef<HTMLHeadingElement>(null);
  const firstStep = useRef(true);
  // Moving between steps puts the keyboard focus on the new step's heading.
  useEffect(() => {
    if (firstStep.current) {
      firstStep.current = false;
      return;
    }
    stepHeading.current?.focus();
  }, [step]);
  const reset = () => {
    for (const action of ["project", "upload", "spec", "run"]) keys.done(action);
    setStep("file"); setProjectId(null); setName(""); setFile(null); setUploaded(null);
    setTask("classification"); setTarget(""); setMetric(""); setRule(null); setError(null);
  };
  const objective = { primaryMetric: metric, constraint: rule, businessObjective: "" };
  const columns = uploaded?.columns?.map((c) => c.name) ?? [];
  const design = testDesignRules(task);

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
          setError(plainWizardError(caught, context.current));
        } finally {
          busyRef.current = false;
          setBusy(false);
        }
      };
    }
    return flight();
  };

  const submitFile = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      if (uploaded) return setStep("goal");
      const problem = dataStepProblem({ projectId, projectName: name, file, datasetId: null });
      if (problem || !file) throw new WizardInputError(problem ?? "Choose a data file to upload.");
      let pid = projectId;
      if (!pid) {
        context.current = "project";
        pid = (await createProject({ name: name.trim(), key: keys.keyFor("project", name.trim()) })).id;
        setProjectId(pid);
      }
      context.current = "upload";
      const upload = await uploadDataset({ projectId: pid, file, key: keys.keyFor("upload", `${pid}:${file.name}:${file.size}:${file.lastModified}`) });
      setUploaded(upload);
      invalidate(pid);
      setStep("goal");
    });
  };

  const submitGoal = (event: FormEvent) => {
    event.preventDefault();
    const problem = objectiveProblem(objective);
    if (problem) return setError({ title: "Check the success rule", detail: problem, fixable: true });
    setError(null);
    setStep("design");
  };

  const confirm = () => run(async () => {
    if (!projectId || !uploaded) throw new WizardInputError("Upload the file first.");
    const body = buildSpecBody(task, target, objective);
    context.current = "goal";
    const spec = await createProblemSpec({ projectId, body, key: keys.keyFor("spec", `${projectId}:${uploaded.id}:${JSON.stringify(body)}`) });
    context.current = "run";
    const experiment = await startExperiment({ projectId, datasetId: uploaded.id, problemSpecId: spec.id, key: keys.keyFor("run", `${spec.id}:${uploaded.id}`) });
    invalidate(projectId, experiment.id);
    const href = projectHref(projectId, "experiments", experiment.id);
    // Started: forget every answer and key, so opening the dialog again can never start a second run.
    reset();
    dialog.current?.close();
    if (href) router.push(href);
  });

  const back = (to: DialogStepId) => <button type="button" className="btn" disabled={busy} onClick={() => { setError(null); setStep(to); }}>Back</button>;
  const actions = (children: ReactNode) => <div className="toolbar np-actions">{children}</div>;

  return (
    <>
      <button type="button" className={primary ? "btn primary" : "btn"} aria-haspopup="dialog" onClick={() => dialog.current?.showModal()}>{label}</button>
      <dialog ref={dialog} className="studio-cmd-dialog np-dialog" aria-labelledby={titleId} data-testid="new-project-dialog">
        <div className="np-body">
          <h2 id={titleId}>New project</h2>
          <StepBar steps={DIALOG_STEPS.map((s) => ({ id: s.id, label: s.label, state: dialogStepState(s.id, step) }))} label="New project steps" />
          <h3 ref={stepHeading} tabIndex={-1} className="np-step-title">{STEP_TITLE[step]}</h3>
          {error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null}

          {step === "file" ? (
            <form className="form" onSubmit={submitFile}>
              <p className="muted">A project is one prediction goal. Name it and choose a CSV, TSV, JSON, Parquet or XLSX file with a header row. Uploading saves the project and a copy of the file; nothing is trained yet.</p>
              <label className="field"><span>Project name</span>
                <input value={name} maxLength={120} onChange={(e) => setName(e.target.value)} required disabled={busy || Boolean(projectId)} />
              </label>
              <label className="field"><span>Data file</span>
                <input type="file" accept=".csv,.tsv,.json,.jsonl,.parquet,.xlsx" disabled={busy || Boolean(uploaded)} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              </label>
              {uploaded ? <p role="status"><Pill tone="ok">Uploaded</Pill> {plainText(uploaded.name, 120)}: {uploaded.row_count} rows, {uploaded.column_count} columns.</p> : null}
              {actions(<>
                <button type="button" className="btn" onClick={() => dialog.current?.close()}>Close</button>
                <button type="submit" className="btn primary" disabled={busy}>{busy ? "Uploading…" : uploaded ? "Continue" : "Upload and continue"}</button>
              </>)}
              <p className="muted small">Prefer one step per page? <Link href="/projects/new">Use the four-step page</Link>.</p>
            </form>
          ) : null}

          {step === "goal" ? (
            <form className="form" onSubmit={submitGoal}>
              <p className="muted">Say which column to predict and, if you have one, the rule a good model must meet. Nothing is sent until you confirm the test design.</p>
              <label className="field"><span>Kind of answer</span>
                <select value={task} onChange={(e) => { setTask(e.target.value as TaskType); setMetric(""); setRule(null); }}>
                  {TASK_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label className="field"><span>Column to predict</span>
                <select value={columns.includes(target) ? target : ""} onChange={(e) => setTarget(e.target.value)}>
                  <option value="">Let DCLab choose (it asks you on the run page if unclear)</option>
                  {columns.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
              <label className="field"><span>Score that picks the best model</span>
                <select value={metric} onChange={(e) => setMetric(e.target.value)}>
                  <option value="">DCLab default for the kind of answer</option>
                  {primaryMetricOptions(task).map((m) => <option key={m} value={m}>{metricInfo(m)?.label ?? m}</option>)}
                </select>
              </label>
              <label className="field"><span>Success rule (optional)</span>
                <select value={rule?.metric ?? ""} onChange={(e) => setRule(e.target.value ? { metric: e.target.value, op: rule?.op ?? ">=", value: rule?.value ?? "" } : null)}>
                  <option value="">No success rule</option>
                  {constraintMetricOptions(task).map((m) => <option key={m} value={m}>{metricInfo(m)?.label ?? m}</option>)}
                </select>
              </label>
              {rule ? (
                <div className="row">
                  <label className="field"><span>Must be</span>
                    <select value={rule.op} onChange={(e) => setRule({ ...rule, op: e.target.value as ">=" | "<=" })}><option value=">=">at least</option><option value="<=">at most</option></select>
                  </label>
                  <label className="field"><span>Value</span><input inputMode="decimal" value={rule.value} onChange={(e) => setRule({ ...rule, value: e.target.value })} /></label>
                </div>
              ) : null}
              {actions(<>{back("file")}<button type="submit" className="btn primary">Continue</button></>)}
            </form>
          ) : null}

          {step === "design" ? (
            <div className="form">
              <p className="muted">The test design decides which rows are kept away from training to test the model. Fixed rules make it when the run starts; they never look at scores. Confirming lets the rules choose it and starts training.</p>
              <div className="np-design" aria-label="Test design suggested by the rules" role="group">
                <p><Pill tone="det">rule</Pill> <b>{design.suggestion}</b></p>
                <p>Unless the rules find one of these in your table:</p>
                <ol>{design.exceptions.map((text) => <li key={text}>{text}</li>)}</ol>
                <p className="muted">{design.after}</p>
              </div>
              <KeyValue items={[
                { key: "file", label: "Data file", value: uploaded ? plainText(uploaded.name, 120) : "—" },
                { key: "task", label: "Kind of answer", value: TASK_OPTIONS.find((o) => o.value === task)?.label ?? task },
                { key: "target", label: "Column to predict", value: target ? <span className="mono">{plainText(target, 120)}</span> : "DCLab chooses; you confirm if unclear" },
                { key: "metric", label: "Score that picks the best model", value: metric ? (metricInfo(metric)?.label ?? metric) : "default for the kind of answer" },
                { key: "rule", label: "Success rule", value: rule ? `${metricInfo(rule.metric)?.label ?? rule.metric} ${rule.op === ">=" ? "at least" : "at most"} ${rule.value}` : "none" },
              ]} />
              <p className="muted small">Training starts only when you press the button. The run page shows each step as it happens: the final test set is set aside before any model is trained, and Goal &amp; test design shows the design the rules chose.</p>
              {actions(<>{back("goal")}<button type="button" className="btn primary" disabled={busy} onClick={() => void confirm()}>{busy ? "Starting…" : "Confirm and start training"}</button></>)}
            </div>
          ) : null}
        </div>
      </dialog>
    </>
  );
}
