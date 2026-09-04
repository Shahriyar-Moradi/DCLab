"use client";

import { Badge } from "@/app/components/ui/Badge";
import { Button } from "@/app/components/ui/Button";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { useBusinessDeepAudit, usePipelineMonitor, useSession } from "@/lib/application";
import Link from "next/link";
import { useParams } from "next/navigation";
import type { ReactNode } from "react";

type TechnicalRow = Record<string, unknown>;

const STAGES = [
  ["ingestion", "File ingestion"], ["profiling_eda", "EDA / profiling"], ["target_task", "Target / task"],
  ["structural_cleaning", "Structural cleaning"], ["holdout_plan", "Holdout plan"],
  ["holdout_lock", "Holdout lock"],
  ["problem_profile", "Problem profile"], ["validation_plan", "Validation plan"], ["metric_plan", "Metric plan"],
  ["leakage_audit", "Leakage audit"], ["model_development_plan", "Model development plan"],
  ["missing_value_decisions", "Missing-value decisions"], ["column_roles", "Column roles"],
  ["feature_engineering", "Feature engineering"], ["preprocessing_configuration", "Preprocessing"],
  ["model_selection", "Candidate comparison / selection"], ["winner_lock", "Winner lock"],
  ["final_fit", "Final fit"], ["final_test", "Final holdout"], ["predictions", "Predictions"],
  ["artifact_persistence", "Artifact persistence"], ["deterministic_verification", "Deterministic verification"],
  ["openai_audit", "OpenAI Auditor"], ["report", "Reports"], ["terminal", "Terminal state"],
] as const;

function object(value: unknown): TechnicalRow { return typeof value === "object" && value !== null && !Array.isArray(value) ? value as TechnicalRow : {}; }
function text(value: unknown, fallback = "—"): string { return value === null || value === undefined || value === "" ? fallback : String(value); }
function list(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
function tone(status: string): "green" | "amber" | "oxblood" { return ["completed", "pass", "verified", "success"].includes(status.toLowerCase()) ? "green" : ["failed", "fail", "error"].includes(status.toLowerCase()) ? "oxblood" : "amber"; }

export default function PipelineMonitorPage() {
  const { pipelineId, businessId } = useParams<{ pipelineId: string; businessId?: string }>();
  const query = usePipelineMonitor(pipelineId, businessId);
  const deepAudit = useBusinessDeepAudit();
  const { user } = useSession();
  if (query.isPending) return <Skeleton className="h-[38rem]" />;
  if (query.isError || !query.data) return <ErrorState body="Pipeline Monitor could not be loaded." onRetry={() => void query.refetch()} />;
  const monitor = query.data;
  const capabilities = monitor.capabilities;
  const hierarchy = monitor.hierarchy;
  const business = object(hierarchy.business); const domain = object(hierarchy.domain); const workflow = object(hierarchy.workflow); const workflowRun = object(hierarchy.workflow_run); const model = object(hierarchy.model);
  const events = monitor.events as TechnicalRow[];
  const invocations = monitor.llm_invocations as TechnicalRow[];
  const candidates = monitor.candidates as TechnicalRow[];
  const checks = list(monitor.deterministic_verification.checks).map(object);
  const semantic = invocations.filter((row) => text(row.purpose).startsWith("semantic_"));
  const audit = invocations.filter((row) => text(row.purpose).startsWith("pipeline_audit_"));
  const folds = events.filter((row) => row.event_type === "cv_fold_completed");
  const root = businessId ? "/business" : "/admin/businesses";
  const businessBase = businessId ? `/business/workspaces/${businessId}` : `/admin/businesses/${text(business.id)}`;
  const sourceUpload = object(hierarchy.source_upload);
  const canDeepAudit = Boolean(businessId && user?.role === "business_admin" && capabilities.deep_audit && capabilities.openai_pipeline_audit && sourceUpload.id);

  return <div className="pb-16">
    <p className="text-eyebrow uppercase tracking-[0.08em] text-ink-muted">
      <Link href={root}>{businessId ? "Business administration" : "Businesses"}</Link> → <Link href={businessBase}>{text(business.name)}</Link> → {text(domain.name)} → {text(workflow.name)} → <Link href={`${businessBase}/workflow-runs/${text(workflowRun.id)}`}>Workflow Run</Link> → Pipeline Run → {model.name ? "Model → " : ""}Pipeline Monitor
    </p>
    <div className="mt-3 flex flex-wrap items-center gap-3"><h1 className="font-display text-title">Pipeline Monitor</h1><Badge tone={tone(monitor.summary.status)}>{monitor.summary.status}</Badge></div>
    <p className="mt-2 font-mono text-data text-ink-muted">{monitor.summary.pipeline_name} · {monitor.summary.id}</p>
    <div className="mt-8 grid gap-4 md:grid-cols-4"><Metric label="Workflow run" value={text(workflowRun.id)} /><Metric label="Dataset" value={monitor.summary.dataset_name} /><Metric label="Candidates" value={String(monitor.summary.candidate_count)} /><Metric label="Persisted events" value={String(monitor.summary.event_count)} /></div>

    <Panel title="Pipeline stage coverage" subtitle="Persisted stage events make fast and completed runs fully replayable."><div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{STAGES.map(([stage, label]) => { const rows = events.filter((event) => event.stage === stage); const last = rows.at(-1); return <div key={stage} className="rounded border border-hairline p-4"><div className="flex items-center justify-between gap-2"><h3 className="font-semibold">{label}</h3><Badge tone={tone(text(last?.status, "not recorded"))}>{text(last?.status, "not recorded")}</Badge></div><p className="mt-2 font-mono text-data text-ink-muted">{rows.length} event{rows.length === 1 ? "" : "s"}</p></div>; })}</div></Panel>

    <Panel title="Preprocessing configuration" subtitle="The persisted run uses leakage-safe sklearn pipelines.">
      <div className="grid gap-4 md:grid-cols-2"><Flow title="Numerical" steps={list(monitor.preprocessing.numerical).map(String)} /><Flow title="Categorical" steps={list(monitor.preprocessing.categorical).map(String)} footer={`drop=${text(object(monitor.preprocessing.one_hot).drop)} · handle_unknown=${text(object(monitor.preprocessing.one_hot).handle_unknown)}`} /></div>
      <ul className="mt-4 space-y-2 rounded bg-navy-soft/50 p-5">{list(monitor.preprocessing.fit_guarantees).map((item) => <li key={String(item)} className="text-body">✓ {String(item)}</li>)}</ul>
    </Panel>

    <ScientificPlan plan={object(monitor.scientific_plan)} />

    {capabilities.cv_fold_details ? <Panel title="Fold-by-fold cross-validation" subtitle="Candidate comparison and winner selection use CV evidence only; the final holdout is excluded.">
      <Table><thead><tr><Th>Candidate</Th><Th>Fold</Th><Th>Train rows</Th><Th>Validation rows</Th><Th>Metrics</Th></tr></thead><tbody>{folds.map((row) => { const payload = object(row.payload); return <tr key={`${text(payload.candidate_id)}-${text(payload.fold_number)}-${text(row.sequence)}`}><Td mono>{text(payload.candidate_id)}</Td><Td mono>{text(payload.fold_number)}</Td><Td mono>{text(payload.train_row_count)}</Td><Td mono>{text(payload.validation_row_count)}</Td><Td mono>{JSON.stringify(payload.metrics ?? {})}</Td></tr>; })}</tbody></Table>
      {folds.length === 0 ? <Empty>No completed folds were recorded.</Empty> : null}
    </Panel> : <CapabilityUnavailable name="cv_fold_details" />}

    <Panel title="Candidate comparison" subtitle="CV-only ranking. Rejected candidates are never evaluated on the final test set.">
      <Table><thead><tr><Th>Candidate</Th><Th>Family</Th><Th>Status</Th><Th>CV score</Th><Th>Winner</Th><Th>Final test</Th></tr></thead><tbody>{candidates.map((row) => { const payload = object(row.payload); const selected = row.selected === true; return <tr key={text(row.id)}><Td mono>{text(row.candidate_key)}</Td><Td>{text(payload.model_family)}</Td><Td>{text(row.status)}</Td><Td mono>{text(payload.score ?? object(payload.cv_score).mean)}</Td><Td>{selected ? "LOCKED WINNER" : "Rejected"}</Td><Td>{selected ? "Evaluated once" : "NOT EVALUATED"}</Td></tr>; })}</tbody></Table>
    </Panel>

    <Panel title="Deterministic verification" subtitle="DETERMINISTIC VERIFICATION = AUTHORITATIVE">
      <div className="mb-4"><Badge tone={tone(text(monitor.deterministic_verification.overall_status))}>{text(monitor.deterministic_verification.overall_status, "Not available")}</Badge></div>
      <Table><thead><tr><Th>Check</Th><Th>Stage</Th><Th>Status</Th><Th>Summary / evidence</Th></tr></thead><tbody>{checks.map((check, index) => <tr key={`${text(check.check_id)}-${index}`}><Td mono>{text(check.check_id)}</Td><Td>{text(check.stage)}</Td><Td><Badge tone={tone(text(check.status))}>{text(check.status)}</Badge></Td><Td>{text(check.summary ?? check.message ?? check.evidence)}</Td></tr>)}</tbody></Table>
      {checks.length === 0 ? <Empty>No deterministic check ledger is available for this run.</Empty> : null}
    </Panel>

    {capabilities.semantic_llm_audit ? <Panel title="Semantic LLM participation" subtitle="Every decision explicitly records whether an LLM was used, why, and what the validator accepted."><LlmTable rows={semantic} /></Panel> : <CapabilityUnavailable name="semantic_llm_audit" />}
    {capabilities.openai_pipeline_audit ? <Panel title="OpenAI Auditor" subtitle="OPENAI AUDIT = ADVISORY · kept separate from semantic decisions and authoritative deterministic checks."><LlmTable rows={audit} />{businessId ? <div className="mt-4"><Button disabled={!canDeepAudit || deepAudit.isPending} onClick={() => { if (!sourceUpload.id || !businessId) return; deepAudit.mutate({ businessId, runId: text(sourceUpload.id) }, { onSuccess: () => void query.refetch() }); }}>{deepAudit.isPending ? "Running deep audit…" : "Run deep audit"}</Button>{!canDeepAudit ? <p className="mt-2 text-body text-ink-muted">Deep audit requires Business Admin plus the deep_audit capability.</p> : null}{deepAudit.isError ? <p className="mt-2 text-body text-oxblood">Deep audit could not be completed.</p> : null}</div> : null}</Panel> : <CapabilityUnavailable name="openai_pipeline_audit" />}

    <Panel title="Predictions" subtitle="Persisted holdout prediction evidence is summarized without exposing raw customer rows."><div className="grid gap-4 md:grid-cols-3"><Metric label="Prediction count" value={text(monitor.predictions.count)} /><Metric label="Raw rows included" value={text(monitor.predictions.raw_rows_included)} /><Metric label="Distribution" value={JSON.stringify(monitor.predictions.distribution ?? {})} /></div></Panel>

    {capabilities.raw_pipeline_debug ? <Panel title="Timeline / replay" subtitle="Append-only sequence order; no artificial delay is introduced."><ol className="space-y-3 border-l border-hairline pl-5">{events.map((event) => <li key={text(event.id)} className="relative rounded bg-paper-raised p-4 before:absolute before:-left-[1.55rem] before:top-5 before:h-2 before:w-2 before:rounded-full before:bg-brand"><div className="flex flex-wrap justify-between gap-2"><span className="font-mono text-data">#{text(event.sequence)} · {text(event.stage)} · {text(event.event_type)}</span><span className="font-mono text-data text-ink-muted">{new Date(text(event.timestamp)).toLocaleString()}</span></div><pre className="mt-3 overflow-x-auto whitespace-pre-wrap text-data text-ink-muted">{JSON.stringify(event.payload ?? {}, null, 2)}</pre></li>)}</ol></Panel> : <CapabilityUnavailable name="raw_pipeline_debug" />}

    {capabilities.decision_ledger ? <Panel title="Reports"><SafeJson value={monitor.reports} /></Panel> : <CapabilityUnavailable name="decision_ledger" />}
    {capabilities.raw_pipeline_debug ? <Panel title="Sanitized raw technical evidence" subtitle="Bounded evidence only. Datasets, secrets, API keys, and row provenance are excluded."><SafeJson value={monitor.sanitized_evidence} /></Panel> : null}
  </div>;
}

function LlmTable({ rows }: { rows: TechnicalRow[] }) { return <><Table><thead><tr><Th>LLM used</Th><Th>Purpose</Th><Th>Reason</Th><Th>Provider / model</Th><Th>Prompt</Th><Th>Validator</Th><Th>Final accepted decision</Th></tr></thead><tbody>{rows.map((row) => <tr key={text(row.id)}><Td><Badge tone={row.llm_used === true ? "green" : "amber"}>{row.llm_used === true ? "YES" : "NO"}</Badge></Td><Td mono>{text(row.purpose)}</Td><Td>{text(row.reason)}</Td><Td>{row.llm_used === true ? `${text(row.provider)} / ${text(row.model)}` : "Not used"}</Td><Td mono>{text(row.prompt_version)}</Td><Td>{text(row.validator_verdict)}</Td><Td mono>{JSON.stringify(row.final_decision ?? {})}</Td></tr>)}</tbody></Table>{rows.length === 0 ? <Empty>No invocation was recorded.</Empty> : null}</>; }
function ScientificPlan({ plan }: { plan: TechnicalRow }) {
  const profile = object(plan.problem_profile);
  const validation = object(plan.validation);
  const metric = object(plan.metric);
  const holdout = object(plan.holdout);
  const leakage = object(plan.leakage);
  const findings = list(leakage.findings).map(object);
  const allowed = list(plan.allowed_features).map(String);
  const excluded = list(plan.excluded_features).map(object);
  const overlap = validation.group_overlap_count;
  const overlapLabel = overlap === null || overlap === undefined ? "n/a" : `${text(overlap)}${validation.group_overlap_ok === true ? " ✓" : ""}`;
  return <>
    <Panel title="Problem Profile" subtitle="Train-only scientific profile used to choose validation, metrics, and leakage policy.">
      <div className="grid gap-4 md:grid-cols-4">
        <Metric label="Task" value={text(profile.task_type)} />
        <Metric label="Train rows" value={text(profile.row_count)} />
        <Metric label="Features" value={text(profile.feature_count)} />
        <Metric label="Imbalance ratio" value={text(profile.imbalance_ratio)} />
      </div>
      <p className="mt-4 font-mono text-data text-ink-muted">Identifiers: {text(list(profile.identifier_columns).join(", ") || "none")} · Datetime: {text(list(profile.datetime_columns).join(", ") || "none")}</p>
    </Panel>
    <Panel title="Validation Strategy" subtitle="The splitter, grouping, and fold counts actually used for candidate comparison.">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Metric label="Validation Strategy" value={text(validation.strategy)} />
        <Metric label="Group" value={text(validation.group_column, "none")} />
        <Metric label="Time column" value={text(validation.time_column, "none")} />
        <Metric label="Requested folds" value={text(validation.requested_folds)} />
        <Metric label="Actual folds" value={text(validation.actual_folds)} />
        <Metric label="Group overlap" value={overlapLabel} />
      </div>
      {validation.reason ? <p className="mt-4 text-body text-ink-muted">{text(validation.reason)}</p> : null}
    </Panel>
    <Panel title="Final Holdout" subtitle="The locked test partition matches the same grouping or chronology used for CV.">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Metric label="Holdout Strategy" value={text(holdout.strategy)} />
        <Metric label="Train rows" value={text(holdout.n_train)} />
        <Metric label="Test rows" value={text(holdout.n_test)} />
        <Metric label="Group" value={text(holdout.group_column, "none")} />
        <Metric label="Time column" value={text(holdout.time_column, "none")} />
        <Metric label="Group overlap" value={text(holdout.group_overlap_count, "n/a")} />
        <Metric label="train_time_max" value={text(holdout.train_time_max, "n/a")} />
        <Metric label="test_time_min" value={text(holdout.test_time_min, "n/a")} />
      </div>
      {holdout.reason ? <p className="mt-4 text-body text-ink-muted">{text(holdout.reason)}</p> : null}
    </Panel>
    <Panel title="Metric Strategy" subtitle="Primary selection metric is locked from the ProblemProfile before candidates train.">
      <div className="grid gap-4 md:grid-cols-2">
        <Metric label="Primary metric" value={text(metric.primary_metric)} />
        <Metric label="Secondary metrics" value={text(list(metric.secondary_metrics).join(", ") || "none")} />
      </div>
      {metric.reason ? <p className="mt-4 text-body text-ink-muted">{text(metric.reason)}</p> : null}
    </Panel>
    <Panel title="Leakage Audit" subtitle="Prediction-time availability and risk. HIGH/CRITICAL features are excluded from estimators.">
      <p className="mb-4 font-mono text-data text-ink-muted">Overall risk: {text(leakage.overall_risk)} · Partition: {text(leakage.partition, "train")}</p>
      <Table><thead><tr><Th>Feature</Th><Th>Risk</Th><Th>Prediction-time status</Th><Th>Action</Th></tr></thead><tbody>{findings.map((row, index) => <tr key={`${text(row.column)}-${index}`}><Td mono>{text(row.column)}</Td><Td>{text(row.risk)}</Td><Td mono>{text(row.availability_status)}</Td><Td>{text(row.action)}</Td></tr>)}</tbody></Table>
      {findings.length === 0 ? <Empty>No leakage findings were recorded.</Empty> : null}
    </Panel>
    <Panel title="Allowed Features" subtitle="Features eligible for candidate estimators after the leakage audit.">
      <p className="font-mono text-data">{allowed.join(", ") || "—"}</p>
    </Panel>
    <Panel title="Excluded Features" subtitle="Identifiers and HIGH/CRITICAL leakage features never enter candidate feature sets.">
      <Table><thead><tr><Th>Feature</Th><Th>Risk</Th><Th>Action</Th><Th>Reason</Th></tr></thead><tbody>{excluded.map((row, index) => <tr key={`${text(row.column)}-${index}`}><Td mono>{text(row.column)}</Td><Td>{text(row.risk)}</Td><Td>{text(row.action)}</Td><Td>{text(row.reason)}</Td></tr>)}</tbody></Table>
      {excluded.length === 0 ? <Empty>No features were excluded by the leakage audit.</Empty> : null}
    </Panel>
  </>;
}
function Panel({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) { return <section className="mt-12"><h2 className="font-display text-section">{title}</h2>{subtitle ? <p className="mb-4 mt-1 text-body text-ink-muted">{subtitle}</p> : <div className="mb-4" />}{children}</section>; }
function Metric({ label, value }: { label: string; value: string }) { return <div className="rounded bg-paper-raised p-5"><p className="text-eyebrow uppercase text-ink-muted">{label}</p><p className="mt-2 break-all font-mono text-data">{value}</p></div>; }
function Flow({ title, steps, footer }: { title: string; steps: string[]; footer?: string }) { return <div className="rounded bg-paper-raised p-6"><p className="text-eyebrow uppercase text-ink-muted">{title}</p><div className="mt-4 flex flex-wrap items-center gap-3">{steps.map((step, index) => <span key={step} className="contents"><span className="rounded bg-navy px-3 py-2 font-mono text-data text-white">{step}</span>{index < steps.length - 1 ? <span>→</span> : null}</span>)}</div>{footer ? <p className="mt-4 font-mono text-data text-ink-muted">{footer}</p> : null}</div>; }
function SafeJson({ value }: { value: unknown }) { return <pre className="max-h-[38rem] overflow-auto rounded bg-navy p-5 text-data text-white">{JSON.stringify(value, null, 2)}</pre>; }
function Empty({ children }: { children: ReactNode }) { return <p className="mt-4 rounded bg-paper-raised p-4 text-body text-ink-muted">{children}</p>; }
function CapabilityUnavailable({ name }: { name: string }) { return <section className="mt-12 rounded border border-hairline bg-paper-raised p-5"><p className="font-mono text-data text-ink-muted">{name} is not enabled for this workspace.</p></section>; }
