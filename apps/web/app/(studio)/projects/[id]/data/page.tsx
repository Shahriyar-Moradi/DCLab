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
import { Stat } from "@/components/studio/Stat";
import { safeInternalHref } from "@/components/studio/safe-href";
import { Term } from "@/components/studio/Term";
import { FindingsPanel } from "@/app/components/studio-app/FindingsPanel";
import { QueryNotice, formatWhen } from "@/app/components/studio-app/StudioParts";
import { ActionKeys, mapWizardError, useProjectDatasets, useProjectRefs, type PlainError, type StudioDatasetItem } from "@/lib/application";
import { plainText, projectHref } from "@/lib/application/command-search";
import { columnUse, compareRoles, dataCheckRows, fileSummary, leakageExcluded, percent, pickDatasetId, preparedIds, roleLabel, typeLabel, usedBy, usedByText, type CheckRow } from "@/lib/application/studio-data";
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
  inUse: "The data version new runs use. Changing it is saved in History.",
  dataClass: "The most that AI help may receive from this file, if you switch it on: nothing, column names and types, summary counts, or sample values.",
};
const AI_CLASS_WORDS: Record<string, string> = {
  none: "Nothing from this file",
  metadata: "Column names and types only",
  aggregates: "Column names, types and summary counts",
  sample_values: "Column names, types, summary counts and sample values",
};
const USED_PILL: Record<string, { tone: PillTone; text: string }> = {
  yes: { tone: "ok", text: "yes" }, no: { tone: "gray", text: "no" }, target: { tone: "det", text: "target" }, unknown: { tone: "gray", text: "not clear yet" },
};
const ROLE_TONE: Record<string, PillTone> = { target: "ok", identifier: "gray", ignored_free_text: "gray" };

function RolePill({ role }: { role: string | null | undefined }) {
  return role ? <Pill tone={ROLE_TONE[role] ?? "det"}>{roleLabel(role)}</Pill> : <span className="muted">—</span>;
}

const columnTable = (hasRun: boolean): Column<StudioProfileColumn>[] => [
  { key: "name", header: "Column", sortValue: (c) => c.ordinal_position, render: (c) => <span className="mono">{plainText(c.name, 80)}</span> },
  { key: "type", header: "Type", sortValue: (c) => typeLabel(c.physical_dtype, c.role_used ?? c.rule_role), render: (c) => typeLabel(c.physical_dtype, c.role_used ?? c.rule_role) },
  { key: "missing", header: "Missing", numeric: true, sortValue: (c) => c.missing_fraction ?? -1, render: (c) => percent(c.missing_fraction) },
  { key: "used", header: "Used?", sortValue: (c) => columnUse(c, hasRun).used, render: (c) => { const pill = USED_PILL[columnUse(c, hasRun).used]; return <Pill tone={pill.tone}>{pill.text}</Pill>; } },
  { key: "why", header: "Why", render: (c) => columnUse(c, hasRun).reason ?? "—" },
  { key: "importance", header: "Importance", numeric: true, sortValue: (c) => c.importance ?? -1, render: (c) => (c.importance == null ? "—" : c.importance.toFixed(3)) },
];

const DETAIL_TABLE: Column<StudioProfileColumn>[] = [
  { key: "name", header: "Column", render: (c) => <span className="mono">{plainText(c.name, 80)}</span> },
  { key: "used", header: "Role used", render: (c) => (
    <>
      <RolePill role={c.role_used} />
      {compareRoles(c.rule_role, c.role_used) === "differs" ? <> <Pill tone="warn">differs · {c.leakage_excluded ? "left out for leakage" : `set by ${roleLabel(c.role_source)}`}</Pill></> : null}
    </>
  ) },
  { key: "rule", header: "Rule role", render: (c) => <RolePill role={c.rule_role} /> },
  { key: "missingN", header: "Missing rows", numeric: true, render: (c) => c.missing_count ?? "—" },
  { key: "unique", header: "Distinct values", numeric: true, render: (c) => c.unique_count ?? "—" },
  { key: "transform", header: "Transform", render: (c) => (c.leakage_excluded ? "left out" : c.transforms.length ? c.transforms.map((t) => plainText(t, 40)).join(" · ") : "—") },
];

const LEFT_OUT_TABLE: Column<StudioProfileColumn>[] = [
  { key: "name", header: "Column", render: (c) => <span className="mono">{plainText(c.name, 80)}</span> },
  { key: "risk", header: "Risk", render: (c) => (c.leakage_risk ? plainText(c.leakage_risk, 40) : "—") },
  { key: "reason", header: "Why (recorded by the rules)", render: (c) => (c.leakage_reason ? plainText(c.leakage_reason, 240) : "—") },
];

function ScopeNote({ profile }: { profile: StudioDatasetProfile }) {
  if (profile.statistics_status === "computed" && profile.split_plan) {
    return (
      <p className="muted">
        Counts are over the {profile.split_plan.training_row_count.toLocaleString("en-GB")} <Term definition={TERMS.training}>training rows</Term> of{" "}
        {profile.split_plan.source === "project_ref" ? "the test design in use" : "this version's newest test design"}. Final test set rows are never counted.
      </p>
    );
  }
  if (profile.statistics_status === "unavailable") {
    return <Banner tone="warn">The test design&apos;s row map could not be verified, so counts are withheld. Names and types come from the upload.</Banner>;
  }
  return <Banner>No test design yet. Counts appear after the first run fixes the final test set; until then only names and types from the upload are shown.</Banner>;
}

function RunNote({ projectId, profile }: { projectId: string; profile: StudioDatasetProfile }) {
  if (!profile.experiment) return <p className="muted">No finished run on this test design yet, so what the run did, transforms and importance are empty.</p>;
  const href = projectHref(projectId, "experiments", profile.experiment.id);
  return (
    <p className="muted">
      What the run did, transforms and <Term definition={TERMS.importance}>importance</Term> come from {href ? <Link href={href}>this run</Link> : "this run"}
      {profile.experiment.selection === "champion" ? ", the run of the model in use." : ", the newest finished run on this test design."}
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
          {dataVersionName(plainText(d.name, 80), d.created_at)} <span className="muted">(version {plainText(d.version, 20)})</span> {ref?.target.id === d.id ? <Pill tone="ok">in use</Pill> : null} {prepared.has(d.id) ? <Pill tone="gray">made by a run</Pill> : null} {d.id === selected ? <Pill tone="det">shown</Pill> : null}
        </>
      ),
    },
    { key: "shape", header: "Rows × columns", numeric: true, sortValue: (d) => d.row_count, render: (d) => `${d.row_count} × ${d.column_count}` },
    { key: "used", header: "Used by", render: (d) => (graph.data ? usedByText(usedBy(edges, d.id)) : "—") },
    { key: "created", header: "Uploaded", sortValue: (d) => d.created_at, render: (d) => formatWhen(d.created_at) },
    { key: "details", header: "Details", render: (d) => <details><summary>Show</summary><span className="muted">id </span><span className="mono">{d.id}</span></details> },
    {
      key: "actions", header: "Actions",
      render: (d) => (
        <span className="toolbar">
          {d.id !== selected ? <button type="button" className="btn" onClick={() => onSelect(d.id)}>Show columns<span className="sr-only"> of {dataVersionName(plainText(d.name, 80), d.created_at)}</span></button> : null}
          {ref?.target.id !== d.id && refs.data && !prepared.has(d.id) ? <button type="button" className="btn" onClick={() => { setPending(d.id); setError(null); }}>Use this data<span className="sr-only">: {dataVersionName(plainText(d.name, 80), d.created_at)}</span></button> : null}
        </span>
      ),
    },
  ];
  const target = datasets.find((d) => d.id === pending);
  return (
    <>
      <p className="muted">
        The <Term definition={TERMS.inUse}>data in use</Term> {ref ? <>is {dataVersionName(plainText(datasets.find((d) => d.id === ref.target.id)?.name, 80), datasets.find((d) => d.id === ref.target.id)?.created_at)}.</> : "is not set yet; the first finished run sets it."}
        {graph.data?.truncated ? " Only the newest runs are loaded, so “Used by” may be incomplete." : ""}
      </p>
      {refs.isError ? <QueryNotice error={refs.error} what="versions in use" /> : null}
      {graph.isError ? <QueryNotice error={graph.error} what="what uses each version" /> : null}
      <DataTable caption="Data versions" columns={columns} rows={datasets} rowKey={(d) => d.id} highlightRow={(d) => d.id === selected} />
      {target ? (
        <form className="form card" onSubmit={(event) => { event.preventDefault(); void confirm(); }}>
          <h3>Use {dataVersionName(plainText(target.name, 80), target.created_at)} (version {plainText(target.version, 20)}) as the data in use</h3>
          <p className="muted">This is saved in History. Runs, test designs and models built on the old version show as built on an older version; nothing is retrained and old files are never changed or deleted.</p>
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

function ChecksTab({ projectId, profile }: { projectId: string; profile: StudioDatasetProfile }) {
  const findings = useExperimentFindings(profile.experiment?.id);
  const rows = dataCheckRows(profile, findings.data?.checks ?? null, (v, max = 120) => plainText(v, max), { findingsLoading: findings.isPending && Boolean(profile.experiment), findingsError: findings.isError });
  const excluded = leakageExcluded(profile.columns);
  const TONE: Record<string, PillTone> = { ok: "ok", info: "det", attention: "warn", unknown: "gray" };
  const WORD: Record<string, string> = { ok: "ok", info: "for your information", attention: "look at this", unknown: "not checked yet" };
  const checkColumns: Column<CheckRow>[] = [
    { key: "check", header: "Check", render: (r) => r.label },
    { key: "result", header: "Result", render: (r) => <Pill tone={TONE[r.result]}>{WORD[r.result]}</Pill> },
    { key: "found", header: "What we found", render: (r) => r.text },
  ];
  return (
    <>
      <p className="muted">Checks on the training rows. They run inside every run and work with AI off.</p>
      {findings.isError ? <QueryNotice error={findings.error} what="trust checks" /> : null}
      <DataTable caption="Data checks" columns={checkColumns} rows={rows} rowKey={(r) => r.key} />
      {excluded.length ? (
        <>
          <h3>Columns left out by the leakage plan</h3>
          <DataTable caption="Columns left out by the leakage plan" columns={LEFT_OUT_TABLE} rows={excluded} rowKey={(c) => c.name} />
          <p className="muted">Putting a left-out column back is a new run; the final test set stays the same rows.</p>
        </>
      ) : null}
      {profile.experiment ? (
        <>
          <h3>All trust checks of the run</h3>
          <RunNote projectId={projectId} profile={profile} />
          <FindingsPanel projectId={projectId} experimentId={profile.experiment.id} />
        </>
      ) : null}
    </>
  );
}

function AccessTab({ datasetId }: { datasetId: string }) {
  const version = useDatasetVersion(datasetId);
  if (version.isError) return <QueryNotice error={version.error} what="dataset" />;
  if (!version.data) return <p role="status">Loading access…</p>;
  const p = version.data.policy;
  const items = [
    { key: "grant", label: "Who it is shared with", value: p.upload_policy ? "Published for training, predictions and download by members of this workspace; not shared with other workspaces." : "Not published (cannot be used for training)." },
    { key: "ai", label: <Term definition={TERMS.dataClass}>What AI help may see</Term>, value: Object.hasOwn(AI_CLASS_WORDS, p.ai_data_class) ? AI_CLASS_WORDS[p.ai_data_class] : plainText(p.ai_data_class, 40) },
    { key: "labels", label: "Column labels", value: p.policy_complete ? "Every column is labelled" : "Some columns are not labelled yet" },
  ];
  return (
    <>
      <KeyValue items={items} />
      <h3>Who can read</h3>
      <p>Members of this workspace who can read data. On this page, connected tools (access token) with read access get the same summary: column names, types and training-row counts, never final test set values. They can also download prediction files of this project.</p>
      <details>
        <summary>Technical details</summary>
        <KeyValue items={[
          { key: "up", label: "Upload policy", value: p.upload_policy ? <span className="mono">{plainText(p.upload_policy, 60)}</span> : "Not published under an upload policy" },
          { key: "pub", label: "Publication state", value: plainText(p.publication_state, 60) || "—" },
          { key: "sens", label: "Sensitivity label", value: p.sensitivity_class ? plainText(p.sensitivity_class, 60) : "not set" },
          { key: "ret", label: "Retention label (not yet enforced)", value: p.retention_class ? plainText(p.retention_class, 60) : "not set" },
          { key: "res", label: "Storage label (not yet enforced)", value: p.residency_class ? plainText(p.residency_class, 60) : "not set" },
          { key: "exp", label: "Exposure label", value: <span className="mono">{plainText(p.llm_exposure_policy, 60)}</span> },
          { key: "ws", label: "Workspace AI limit", value: p.workspace_ai_max_class ? <span className="mono">{plainText(p.workspace_ai_max_class, 40)}</span> : "none set" },
        ]} />
      </details>
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
  const summary = shown ? fileSummary(shown, profile.data, (v) => plainText(v, 60)) : [];
  return (
    <>
      <PageHead title="Data" subtitle="The files in this project, what is in them and which columns the model uses." />
      <PageGuide
        purpose="Check what the model was built from before you trust it."
        howTo={<>Pick a file under Versions. Columns shows which columns are used and why; the technical details compare the <Term definition={TERMS.rule}>rule role</Term> with the <Term definition={TERMS.used}>role used</Term>.</>}
        youGet="Rows and columns, missing values, what each column is used for, the data checks and who can see the data."
        attention="Counts never include the final test set (used once). Before the first run no test design exists, so only names and types are shown."
      />
      {datasets.isError ? <QueryNotice error={datasets.error} what="dataset list" /> : null}
      {datasets.isPending ? <p role="status">Loading data…</p> : null}
      {datasets.data && rows.length === 0 ? (
        <div className="empty">No training data in this project yet. {newRun ? <Link href={newRun}>Start a run with a file</Link> : null}</div>
      ) : null}
      {selected && shown ? (
        <>
          <section className="grid cols-4" aria-label="File summary">
            {summary.map((item) => <Stat key={item.key} value={item.value} label={item.label} />)}
          </section>
          <SectionTabs
            title={dataVersionName(plainText(shown.name, 80), shown.created_at)}
            idPrefix="data"
            sections={[
              {
                id: "columns", label: "Columns", count: profile.data?.columns.length,
                content: profile.isError ? <QueryNotice error={profile.error} what="column profile" /> : !profile.data ? <p role="status">Loading columns…</p> : (
                  <>
                    <ScopeNote profile={profile.data} />
                    <RunNote projectId={id} profile={profile.data} />
                    <DataTable caption="Columns" columns={columnTable(Boolean(profile.data.experiment))} rows={profile.data.columns} rowKey={(c) => c.name} />
                    <details>
                      <summary>Technical details per column</summary>
                      <DataTable caption="Column details" columns={DETAIL_TABLE} rows={profile.data.columns} rowKey={(c) => c.name} />
                    </details>
                  </>
                ),
              },
              { id: "versions", label: "Versions", count: rows.length, content: <VersionsTab projectId={id} datasets={rows} selected={selected} onSelect={setChosen} /> },
              { id: "checks", label: "Data checks", content: profile.data ? <ChecksTab projectId={id} profile={profile.data} /> : <p role="status">Loading data checks…</p> },
              { id: "access", label: "Access", content: <AccessTab datasetId={selected} /> },
            ]}
          />
        </>
      ) : null}
    </>
  );
}
