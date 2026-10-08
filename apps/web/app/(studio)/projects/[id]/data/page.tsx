"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useRef, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { KeyValue } from "@/components/studio/KeyValue";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill, type PillTone } from "@/components/studio/Pill";
import { SectionTabs } from "@/components/studio/SectionTabs";
import { safeInternalHref } from "@/components/studio/safe-href";
import { Term } from "@/components/studio/Term";
import { FindingsPanel } from "@/app/components/studio-app/FindingsPanel";
import { PhaseEmpty, QueryNotice, STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { ActionKeys, mapWizardError, useProjectDatasets, useProjectRefs, type PlainError, type StudioDatasetItem } from "@/lib/application";
import { projectHref } from "@/lib/application/command-search";
import { compareRoles, percent, pickDatasetId, preparedIds, roleLabel, usedBy, usedByText } from "@/lib/application/studio-data";
import { dataVersionName } from "@/lib/application/studio-names";
import {
  makeDatasetCurrent, useDatasetProfile, useDatasetVersion, useExperimentFindings, useProjectGraph, useRefInvalidation,
  type StudioDatasetProfile, type StudioProfileColumn,
} from "@/lib/application/studio-data-hooks";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

const TERMS = {
  training: "The rows the test design keeps for training and cross-validation. The final test set (used once) is set aside and never counted here.",
  rule: "The role a fixed, deterministic rule gives the column on the training rows. It works with AI off.",
  used: "What the run actually did with the column. It differs from the rules' answer when a change you made or an accepted suggestion changed it.",
  importance: "How much the cross-validation score drops when the column's values are shuffled, on validation folds only.",
  policy: "The upload policy of ADR 0005: a structurally valid upload is published for deterministic training inside this workspace only.",
  dataClass: "The most an AI call may receive from this dataset: none, metadata (names and types), aggregates (counts) or sample values.",
  ref: "The data version new runs use. Changing it is saved in History.",
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
        {profile.split_plan.source === "project_ref" ? " (the test design in use)" : " (this version's newest plan)"}. Final test set rows are never counted.
      </p>
    );
  }
  if (profile.statistics_status === "unavailable") {
    return <Banner tone="warn">The test design&apos;s row map could not be verified, so statistics are withheld. Names and types come from the upload.</Banner>;
  }
  return <Banner>No test design yet. Statistics appear after the first run fixes the final test set; until then only names and types from the upload are shown.</Banner>;
}

function RunNote({ projectId, profile }: { projectId: string; profile: StudioDatasetProfile }) {
  if (!profile.experiment) return <p className="muted">No completed run on this test design yet, so what the run did, transforms and importance are empty.</p>;
  const href = projectHref(projectId, "experiments", profile.experiment.id);
  const label = <span className="mono">{profile.experiment.id.slice(0, 8)}</span>;
  return (
    <p className="muted">
      Role used, transforms and <Term definition={TERMS.importance}>importance</Term> come from run {href ? <Link href={href}>{label}</Link> : label}
      {profile.experiment.selection === "champion" ? ", the run of the model in use on this test design." : ", the newest completed run on this test design."}
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
          {dataVersionName(d.name, d.created_at)} <span className="muted">(version {d.version})</span> {ref?.target.id === d.id ? <Pill tone="ok">in use</Pill> : null} {prepared.has(d.id) ? <Pill tone="gray">prepared by a run</Pill> : null} {d.id === selected ? <Pill tone="det">shown</Pill> : null}
        </>
      ),
    },
    { key: "shape", header: "Rows × columns", numeric: true, sortValue: (d) => d.row_count, render: (d) => `${d.row_count} × ${d.column_count}` },
    { key: "used", header: "Used by", render: (d) => (graph.data ? usedByText(usedBy(edges, d.id)) : "—") },
    { key: "created", header: "Uploaded", sortValue: (d) => d.created_at, render: (d) => formatWhen(d.created_at) },
    {
      key: "actions", header: "Actions",
      render: (d) => (
        <span className="toolbar">
          {d.id !== selected ? <button type="button" className="btn" onClick={() => onSelect(d.id)}>Show profile<span className="sr-only"> of {dataVersionName(d.name, d.created_at)}</span></button> : null}
          {ref?.target.id !== d.id && refs.data && !prepared.has(d.id) ? <button type="button" className="btn" onClick={() => { setPending(d.id); setError(null); }}>Use this data<span className="sr-only">: {dataVersionName(d.name, d.created_at)}</span></button> : null}
        </span>
      ),
    },
  ];
  const target = datasets.find((d) => d.id === pending);
  return (
    <>
      <p className="muted">
        The <Term definition={TERMS.ref}>data in use</Term> {ref ? <>is {dataVersionName(datasets.find((d) => d.id === ref.target.id)?.name, datasets.find((d) => d.id === ref.target.id)?.created_at)}.</> : "is not set yet; the first finished run sets it."}
        {graph.data?.truncated ? " The graph is truncated, so “Used by” may be incomplete." : ""}
      </p>
      {refs.isError ? <QueryNotice error={refs.error} what="versions in use" /> : null}
      {graph.isError ? <QueryNotice error={graph.error} what="project graph" /> : null}
      <DataTable caption="Data versions" columns={columns} rows={datasets} rowKey={(d) => d.id} highlightRow={(d) => d.id === selected} />
      {target ? (
        <form className="form card" onSubmit={(event) => { event.preventDefault(); void confirm(); }}>
          <h3>Use {dataVersionName(target.name, target.created_at)} as the data in use</h3>
          <p className="muted">This is saved in History. Runs, test designs and models built on the old version show as built on an older version in the lineage; nothing is retrained.</p>
          {error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null}
          <label className="field"><span>Why (saved with the change)</span>
            <textarea value={rationale} maxLength={2000} rows={2} required onChange={(e) => setRationale(e.target.value)} disabled={busy} />
          </label>
          <div className="toolbar">
            <button type="button" className="btn" disabled={busy} onClick={() => setPending(null)}>Cancel</button>
            <button type="submit" className="btn primary" disabled={busy || !rationale.trim()}>{busy ? "Saving…" : "Use this data"}</button>
          </div>
        </form>
      ) : null}
    </>
  );
}

function LeakageTab({ projectId, profile }: { projectId: string; profile: StudioDatasetProfile }) {
  const findings = useExperimentFindings(profile.experiment?.id);
  if (!profile.experiment) return <div className="empty">The leakage check runs inside each run. No completed run on this version&apos;s test design yet.</div>;
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
      <p className="muted">The audit runs on training rows only. Re-including an excluded column is a new root run; the final test set stays the same rows.</p>
    </>
  );
}

function DataFindingsTab({ projectId, profile }: { projectId: string; profile: StudioDatasetProfile }) {
  const experiment = profile.experiment;
  if (!experiment) return <div className="empty">The trust checks run inside each run. No completed run on this version&apos;s test design yet, so there is nothing to show.</div>;
  const href = projectHref(projectId, "experiments", experiment.id);
  const label = <span className="mono">{experiment.id.slice(0, 8)}</span>;
  return (
    <>
      <p className="muted">
        From experiment {href ? <Link href={href}>{label}</Link> : label}
        {experiment.selection === "champion" ? ", the run of the model in use on this test design." : ", the newest completed run on this test design."}
      </p>
      <FindingsPanel projectId={projectId} experimentId={experiment.id} />
      <PhaseEmpty title="Open data questions arrive with the questions inbox." phase="P4.16-UI" />
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
      <p>Members of this workspace with read access, and this workspace&apos;s service tokens with the <span className="mono">read</span> scope. Tokens and agents get the same profile: names, types and training-row counts, never rows and never final test set values.</p>
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
      <PageHead title="Data" subtitle="Your data versions, what each column is used for, the leakage check and who can see the data. Statistics come from training rows only; the rules' answer is shown beside what the run used." />
      <PageGuide
        purpose="Check what the model was built from before you trust it."
        howTo={<>Pick a version under Versions. Columns &amp; roles compares the <Term definition={TERMS.rule}>rule role</Term> with the <Term definition={TERMS.used}>role used</Term>.</>}
        youGet="Per-column types, missing and unique counts on training rows, transforms, importance, leakage exclusions and the policy that governs the data."
        attention="Statistics never include the final test set. Before the first run there is no split, so only names and types are shown."
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
                </>
              ),
            },
            { id: "leakage", label: "Leakage audit", content: profile.data ? <LeakageTab projectId={id} profile={profile.data} /> : <p role="status">Loading profile…</p> },
            { id: "findings", label: "Findings & questions", content: profile.data ? <DataFindingsTab projectId={id} profile={profile.data} /> : <p role="status">Loading profile…</p> },
            { id: "policy", label: "Policy & access", content: <PolicyTab datasetId={selected} /> },
          ]}
        />
      ) : null}
    </>
  );
}
