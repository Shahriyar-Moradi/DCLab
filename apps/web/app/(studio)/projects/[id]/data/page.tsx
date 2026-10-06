"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { KeyValue } from "@/components/studio/KeyValue";
import { Level } from "@/components/studio/Level";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill, type PillTone } from "@/components/studio/Pill";
import { SectionTabs } from "@/components/studio/SectionTabs";
import { safeInternalHref } from "@/components/studio/safe-href";
import { Term } from "@/components/studio/Term";
import { PhaseEmpty, QueryNotice, STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { ActionKeys, mapWizardError, useProjectDatasets, useProjectRefs, type PlainError, type StudioDatasetItem } from "@/lib/application";
import { projectHref } from "@/lib/application/command-search";
import { compareRoles, percent, pickDatasetId, preparedIds, roleLabel, usedBy, usedByText } from "@/lib/application/studio-data";
import {
  makeDatasetCurrent, useDatasetProfile, useDatasetVersion, useExperimentFindings, useProjectGraph, useRefInvalidation,
  type StudioDatasetProfile, type StudioProfileColumn,
} from "@/lib/application/studio-data-hooks";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

const TERMS = {
  training: "The rows the split plan keeps for training and cross-validation. The final holdout rows are set aside and never counted here.",
  rule: "The role a fixed, deterministic rule gives the column on the training rows. It works with AI off.",
  used: "The role the run actually used. It differs from the rule when a branch change, a validated decision or a promoted decision point changed it.",
  importance: "How much the cross-validation score drops when the column's values are shuffled, on validation folds only.",
  policy: "The upload policy of ADR 0005: a structurally valid upload is published for deterministic training inside this workspace only.",
  dataClass: "The most an AI call may receive from this dataset: none, metadata (names and types), aggregates (counts) or sample values.",
  ref: "The project's dataset ref names the version new runs and the graph treat as current. Moving it is a recorded decision.",
};
const ROLE_TONE: Record<string, PillTone> = { target: "ok", identifier: "gray", ignored_free_text: "gray" };

function RolePill({ role }: { role: string | null | undefined }) {
  return role ? <Pill tone={ROLE_TONE[role] ?? "det"}>{roleLabel(role)}</Pill> : <span className="muted">—</span>;
}

const COLUMN_TABLE: Column<StudioProfileColumn>[] = [
  { key: "name", header: "Column", sortValue: (c) => c.ordinal_position, render: (c) => <span className="mono">{c.name}</span> },
  { key: "type", header: "Type", sortValue: (c) => c.physical_dtype, render: (c) => c.physical_dtype },
  {
    key: "used", header: "Role used", sortValue: (c) => c.role_used ?? "",
    render: (c) => (
      <>
        <RolePill role={c.role_used} />
        {compareRoles(c.rule_role, c.role_used) === "differs" ? <> <Pill tone="warn">differs · {c.leakage_excluded ? "leakage plan" : `set by ${roleLabel(c.role_source)}`}</Pill></> : null}
      </>
    ),
  },
  { key: "rule", header: "Rule role", sortValue: (c) => c.rule_role ?? "", render: (c) => <RolePill role={c.rule_role} /> },
  { key: "missing", header: "Missing", numeric: true, sortValue: (c) => c.missing_fraction ?? -1, render: (c) => (c.missing_count == null ? "—" : `${percent(c.missing_fraction)} (${c.missing_count})`) },
  { key: "unique", header: "Unique", numeric: true, sortValue: (c) => c.unique_count ?? -1, render: (c) => c.unique_count ?? "—" },
  { key: "transform", header: "Transform", render: (c) => (c.leakage_excluded ? "excluded" : c.transforms.length ? c.transforms.join(" · ") : "—") },
  { key: "importance", header: "Importance", numeric: true, sortValue: (c) => c.importance ?? -1, render: (c) => (c.importance == null ? "—" : c.importance.toFixed(3)) },
];

const LEAKAGE_TABLE: Column<StudioProfileColumn>[] = [
  { key: "name", header: "Column", render: (c) => <span className="mono">{c.name}</span> },
  { key: "risk", header: "Risk", render: (c) => c.leakage_risk ?? "—" },
  { key: "reason", header: "Reason (recorded by the rule)", render: (c) => c.leakage_reason ?? "—" },
  { key: "used", header: "Role used", render: (c) => <RolePill role={c.role_used} /> },
];

function ScopeNote({ profile }: { profile: StudioDatasetProfile }) {
  if (profile.statistics_status === "computed" && profile.split_plan) {
    return (
      <p className="muted">
        Statistics over the {profile.split_plan.training_row_count} <Term definition={TERMS.training}>training rows</Term> of split
        plan v{profile.split_plan.version} <span className="mono">{profile.split_plan.id.slice(0, 8)}</span>
        {profile.split_plan.source === "project_ref" ? " (the project's split ref)" : " (this version's newest plan)"}. Holdout rows are never counted.
      </p>
    );
  }
  if (profile.statistics_status === "unavailable") {
    return <Banner tone="warn">The split plan&apos;s row map could not be verified, so statistics are withheld. Names and types come from the upload.</Banner>;
  }
  return <Banner>No split plan yet. Statistics appear after the first run fixes the final holdout; until then only names and types from the upload are shown.</Banner>;
}

function RunNote({ projectId, profile }: { projectId: string; profile: StudioDatasetProfile }) {
  if (!profile.experiment) return <p className="muted">No completed run on this split plan yet, so role used, transforms and importance are empty.</p>;
  const href = projectHref(projectId, "experiments", profile.experiment.id);
  const label = <span className="mono">{profile.experiment.id.slice(0, 8)}</span>;
  return (
    <p className="muted">
      Role used, transforms and <Term definition={TERMS.importance}>importance</Term> come from run {href ? <Link href={href}>{label}</Link> : label}
      {profile.experiment.selection === "champion" ? ", the champion's run on this plan." : ", the newest completed run on this plan."}
    </p>
  );
}

function VersionsTab({ projectId, datasets, selected, onSelect }: { projectId: string; datasets: StudioDatasetItem[]; selected: string | null; onSelect: (id: string) => void }) {
  const refs = useProjectRefs(projectId);
  const graph = useProjectGraph(projectId);
  const invalidate = useRefInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const [pending, setPending] = useState<string | null>(null);
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const ref = refs.data?.items.find((item) => item.ref_kind === "dataset");
  const edges = graph.data?.edges ?? [];
  const prepared = preparedIds(edges);
  const confirm = async () => {
    if (!pending || busy || !rationale.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await makeDatasetCurrent({ projectId, datasetId: pending, version: ref?.version ?? null, rationale: rationale.trim(), key: keys.keyFor("ref", `${pending}:${ref?.version ?? "new"}:${rationale.trim()}`) });
      keys.done("ref");
      invalidate(projectId);
      setPending(null);
      setRationale("");
    } catch (caught) {
      setError(mapWizardError(caught));
    } finally {
      setBusy(false);
    }
  };
  const columns: Column<StudioDatasetItem>[] = [
    {
      key: "version", header: "Version", sortValue: (d) => d.created_at,
      render: (d) => (
        <>
          <span className="mono">{d.name}</span> · {d.version} {ref?.target.id === d.id ? <Pill tone="ok">current</Pill> : null} {prepared.has(d.id) ? <Pill tone="gray">prepared by a run</Pill> : null} {d.id === selected ? <Pill tone="det">shown</Pill> : null}
        </>
      ),
    },
    { key: "shape", header: "Rows × columns", numeric: true, sortValue: (d) => d.row_count, render: (d) => `${d.row_count} × ${d.column_count}` },
    { key: "digest", header: "Content digest", render: (d) => <span className="mono" title={d.content_digest ?? undefined}>{d.content_digest ? `${d.content_digest.slice(0, 12)}…` : "—"}</span> },
    { key: "used", header: "Used by", render: (d) => (graph.data ? usedByText(usedBy(edges, d.id)) : "—") },
    { key: "created", header: "Uploaded", sortValue: (d) => d.created_at, render: (d) => formatWhen(d.created_at) },
    {
      key: "actions", header: "Actions",
      render: (d) => (
        <span className="toolbar">
          {d.id !== selected ? <button type="button" className="btn" onClick={() => onSelect(d.id)}>Show profile<span className="sr-only"> of {d.name} {d.version}</span></button> : null}
          {ref?.target.id !== d.id && refs.data && !prepared.has(d.id) ? <button type="button" className="btn" onClick={() => { setPending(d.id); setError(null); }}>Make current<span className="sr-only"> {d.name} {d.version}</span></button> : null}
        </span>
      ),
    },
  ];
  const target = datasets.find((d) => d.id === pending);
  return (
    <>
      <p className="muted">
        The <Term definition={TERMS.ref}>dataset ref</Term> {ref ? <>points at <span className="mono">{ref.target.id.slice(0, 8)}</span> (version {ref.version}).</> : "is not set yet; the first finished run sets it."}
        {graph.data?.truncated ? " The graph is truncated, so “Used by” may be incomplete." : ""}
      </p>
      {refs.isError ? <QueryNotice error={refs.error} what="refs" /> : null}
      {graph.isError ? <QueryNotice error={graph.error} what="project graph" /> : null}
      <DataTable caption="Dataset versions" columns={columns} rows={datasets} rowKey={(d) => d.id} highlightRow={(d) => d.id === selected} />
      {target ? (
        <form className="form card" onSubmit={(event) => { event.preventDefault(); void confirm(); }}>
          <h3>Make {target.name} · {target.version} current</h3>
          <p className="muted">This records an accepted decision that moves the dataset ref. Runs, split plans and models built on the old version show as stale in the graph; nothing is retrained.</p>
          {error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null}
          <label className="field"><span>Why (recorded with the decision)</span>
            <textarea value={rationale} maxLength={2000} rows={2} required onChange={(e) => setRationale(e.target.value)} disabled={busy} />
          </label>
          <div className="toolbar">
            <button type="button" className="btn" disabled={busy} onClick={() => setPending(null)}>Cancel</button>
            <button type="submit" className="btn primary" disabled={busy || !rationale.trim()}>{busy ? "Moving…" : "Move the dataset ref"}</button>
          </div>
        </form>
      ) : null}
    </>
  );
}

function LeakageTab({ projectId, profile }: { projectId: string; profile: StudioDatasetProfile }) {
  const findings = useExperimentFindings(profile.experiment?.id);
  if (!profile.experiment) return <div className="empty">The leakage audit runs inside each experiment. No completed run on this version&apos;s split plan yet.</div>;
  const leakage = findings.data?.checks?.find((check) => check.check === "target_leakage");
  const excluded = profile.columns.filter((c) => c.leakage_excluded);
  return (
    <>
      <RunNote projectId={projectId} profile={profile} />
      {findings.isError ? <QueryNotice error={findings.error} what="trust checks" /> : null}
      {leakage ? (
        <Banner tone={leakage.status === "pass" ? "info" : "warn"}>
          <Pill tone={leakage.status === "pass" ? "ok" : (STATUS_TONE[leakage.status] ?? "warn")}>{leakage.status.replaceAll("_", " ")}</Pill> {leakage.message}
        </Banner>
      ) : findings.data ? <p className="muted">This run has no recorded target-leakage check.</p> : null}
      <DataTable caption="Columns excluded by the leakage plan" columns={LEAKAGE_TABLE} rows={excluded} rowKey={(c) => c.name}
        emptyMessage="The run's train-only leakage plan excluded no column." />
      <p className="muted">The audit runs on training rows only. Re-including an excluded column is a new root run; the holdout stays the same rows.</p>
    </>
  );
}

function PolicyTab({ datasetId }: { datasetId: string }) {
  const version = useDatasetVersion(datasetId);
  if (version.isError) return <QueryNotice error={version.error} what="dataset" />;
  if (!version.data) return <p role="status">Loading policy…</p>;
  const p = version.data.policy;
  return (
    <>
      <KeyValue items={[
        { key: "upload", label: <Term definition={TERMS.policy}>Upload policy</Term>, value: p.upload_policy ? <><span className="mono">{p.upload_policy}</span> (ADR 0005)</> : "Not published under an upload policy" },
        { key: "pub", label: "Publication state", value: p.publication_state ?? "—" },
        { key: "ai", label: <Term definition={TERMS.dataClass}>Data class for AI</Term>, value: <><Pill tone={p.ai_data_class === "none" ? "gray" : "ai"}>{p.ai_data_class}</Pill> exposure label <span className="mono">{p.llm_exposure_policy}</span>{p.workspace_ai_max_class ? <> · workspace AI policy allows up to <span className="mono">{p.workspace_ai_max_class}</span></> : " · no workspace AI policy in effect"}</> },
        { key: "sens", label: "Sensitivity", value: p.sensitivity_class ?? "unresolved" },
        { key: "ret", label: "Retention", value: p.retention_class ?? "unresolved" },
        { key: "res", label: "Residency", value: p.residency_class ?? "unresolved" },
        { key: "rev", label: "Policy revision", value: `${p.policy_revision ?? "none"} · ${p.policy_complete ? "every column labelled" : "labels incomplete"}` },
      ]} />
      <h3>Who can read</h3>
      <p>Members of this workspace with read access, and this workspace&apos;s service tokens with the <span className="mono">read</span> scope. Tokens and agents get the same profile: names, types and training-row counts, never rows and never holdout values.</p>
      <PhaseEmpty title="A per-person access list" phase="the Settings page (members)" />
    </>
  );
}

export default function DataPage() {
  const { id } = useParams<{ id: string }>();
  const datasets = useProjectDatasets(id);
  const refs = useProjectRefs(id);
  const graph = useProjectGraph(id);
  const [chosen, setChosen] = useState<string | null>(null);
  const rows = datasets.data ?? [];
  const current = refs.data?.items.find((item) => item.ref_kind === "dataset")?.target.id;
  const prepared = preparedIds(graph.data?.edges ?? []);
  const uploads = rows.filter((d) => !prepared.has(d.id)).map((d) => d.id);
  const selected = chosen && rows.some((d) => d.id === chosen) ? chosen : pickDatasetId(uploads, null, current);
  const profile = useDatasetProfile(selected);
  const shown = rows.find((d) => d.id === selected);
  const experiments = projectHref(id, "experiments");
  const newRun = experiments ? safeInternalHref(`${experiments}/new`) : null;
  return (
    <>
      <PageHead title="Data" subtitle="Dataset versions, column roles, the leakage audit and the access policy. Statistics come from training rows only; the rule's role is shown beside the role the run used." />
      <PageGuide
        purpose="Check what the model was built from before you trust it."
        howTo={<>Pick a version under Versions. Columns &amp; roles compares the <Term definition={TERMS.rule}>rule role</Term> with the <Term definition={TERMS.used}>role used</Term>.</>}
        youGet="Per-column types, missing and unique counts on training rows, transforms, importance, leakage exclusions and the policy that governs the data."
        attention="Statistics never include the final holdout. Before the first run there is no split, so only names and types are shown."
      />
      {datasets.isError ? <QueryNotice error={datasets.error} what="dataset list" /> : null}
      {datasets.isPending ? <p role="status">Loading datasets…</p> : null}
      {datasets.data && rows.length === 0 ? (
        <div className="empty">No training data in this project yet. {newRun ? <Link href={newRun}>Start a run with a file</Link> : null}</div>
      ) : null}
      {selected && shown ? (
        <SectionTabs
          title={`${shown.name} · ${shown.version}`}
          idPrefix="data"
          aside={<span className="mono muted">{shown.id.slice(0, 8)}</span>}
          sections={[
            { id: "versions", label: "Versions", count: rows.length, content: <VersionsTab projectId={id} datasets={rows} selected={selected} onSelect={setChosen} /> },
            {
              id: "columns", label: "Columns & roles", count: profile.data?.columns.length,
              content: profile.isError ? <QueryNotice error={profile.error} what="column profile" /> : !profile.data ? <p role="status">Loading profile…</p> : (
                <>
                  <ScopeNote profile={profile.data} />
                  <RunNote projectId={id} profile={profile.data} />
                  <DataTable caption="Columns and roles" columns={COLUMN_TABLE} rows={profile.data.columns} rowKey={(c) => c.name} />
                  <PhaseEmpty title="AI (Jev) role answers appear here when a decision point is promoted." phase="the P6.8 release decision; every point is L0 (shadow) today">
                    <Level level={0} />
                  </PhaseEmpty>
                </>
              ),
            },
            { id: "leakage", label: "Leakage audit", content: profile.data ? <LeakageTab projectId={id} profile={profile.data} /> : <p role="status">Loading profile…</p> },
            { id: "findings", label: "Findings & questions", content: <PhaseEmpty title="Data findings and open questions arrive with the findings panel." phase="P4.10-UI" /> },
            { id: "policy", label: "Policy & access", content: <PolicyTab datasetId={selected} /> },
          ]}
        />
      ) : null}
    </>
  );
}
