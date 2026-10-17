"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
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
import { TargetOverTime, UploadChecks, UploadLog } from "@/app/components/studio-app/DataGoalD3";
import { FindingsPanel } from "@/app/components/studio-app/FindingsPanel";
import { QueryNotice, formatWhen } from "@/app/components/studio-app/StudioParts";
import { ActionKeys, mapWizardError, uploadDataset, useProjectDatasets, useProjectRefs, useWizardInvalidation, type PlainError, type StudioDatasetItem } from "@/lib/application";
import { plainText, projectHref } from "@/lib/application/command-search";
import {
  assistantVsRules, columnUse, columnWhy, compareRoles, dataCheckRows, fileSummary, filterColumns, isLeakageLeftOut, leakageExcluded, percent, pickDatasetId,
  preparedIds, recordedReason, relativeTime, roleLabel, typeLabel, uploadErrorText, uploaderText, usedBy, versionStatus, type AiVsRules, type CheckRow, type UploadError,
} from "@/lib/application/studio-data";
import { dataVersionName } from "@/lib/application/studio-names";
import {
  makeDatasetCurrent, useDatasetProfile, useDatasetVersion, useDecisionPoints, useExperimentFindings, useProjectGraph, useRefInvalidation,
  type StudioDatasetProfile, type StudioProfileColumn,
} from "@/lib/application/studio-data-hooks";
import { useProblemSpecs } from "@/lib/application/studio-goal-hooks";
import { useAiOff } from "@/lib/application/studio-home-hooks";
import { useSession } from "@/lib/application/session-provider";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { CAPABILITIES, hasCapability } from "@/lib/infrastructure/capabilities";
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

const clean = (v: string, max = 120) => plainText(v, max);

const columnTable = (hasRun: boolean, ai: Map<string, AiVsRules> | null): Column<StudioProfileColumn>[] => [
  { key: "name", header: "Column", sortValue: (c) => c.ordinal_position, render: (c) => <span className="mono">{plainText(c.name, 80)}</span> },
  { key: "type", header: "Type", sortValue: (c) => typeLabel(c.physical_dtype, c.role_used ?? c.rule_role), render: (c) => typeLabel(c.physical_dtype, c.role_used ?? c.rule_role) },
  { key: "missing", header: "Missing", numeric: true, sortValue: (c) => c.missing_fraction ?? -1, render: (c) => percent(c.missing_fraction) },
  {
    key: "used", header: "Used?", sortValue: (c) => columnUse(c, hasRun).used,
    render: (c) => {
      const use = columnUse(c, hasRun).used;
      const leak = use === "no" && hasRun && isLeakageLeftOut(c);
      return <Pill tone={leak ? "crit" : USED_PILL[use].tone}>{USED_PILL[use].text}{leak ? <span className="sr-only"> (may give away the answer)</span> : null}</Pill>;
    },
  },
  {
    key: "why", header: "Why",
    render: (c) => {
      const why = columnWhy(c, hasRun, clean);
      return <>{why.text ?? "—"}{why.recorded ? <span className="v7-data-recorded muted">Recorded reason: {why.recorded}</span> : null}</>;
    },
  },
  // Rule answer beside the assistant's, only when the run has assistant answers (never while AI is off).
  ...(ai ? [{
    key: "ai", header: "Assistant vs rules",
    render: (c: StudioProfileColumn) => {
      const row = ai.get(c.name);
      return row ? <><Pill tone={row.tone}>{row.pill}</Pill> <span className="v7-data-recorded muted">{row.text}</span></> : <span className="muted">—</span>;
    },
  }] : []),
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
  { key: "recorded", header: "Recorded reason", render: (c) => recordedReason(c, Boolean(c.role_used), clean) ?? "—" },
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

function VersionsTab({ projectId, datasets, selected, onSelect, canWrite }: { projectId: string; datasets: StudioDatasetItem[]; selected: string | null; onSelect: (id: string) => void; canWrite: boolean }) {
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
  const inUse = datasets.find((d) => d.id === ref?.target.id) ?? null;
  const columns: Column<StudioDatasetItem>[] = [
    {
      key: "file", header: "File", sortValue: (d) => d.created_at,
      render: (d) => (
        <>
          <b>{dataVersionName(plainText(d.name, 80), d.created_at)}</b> {d.id === selected ? <Pill tone="det">shown</Pill> : null}
          <br /><small className="muted">{plainText(d.name, 80)} · version {plainText(d.version, 20)}</small>
        </>
      ),
    },
    { key: "uploaded", header: "Uploaded", sortValue: (d) => d.created_at, render: (d) => <time dateTime={d.created_at} title={formatWhen(d.created_at)}>{relativeTime(d.created_at)}</time> },
    { key: "by", header: "By", render: (d) => (prepared.has(d.id) ? "A run" : uploaderText(d.uploader)) },
    { key: "rows", header: "Rows", numeric: true, sortValue: (d) => d.row_count, render: (d) => d.row_count.toLocaleString("en-GB") },
    {
      key: "status", header: "Status",
      render: (d) => {
        const status = versionStatus(d, { inUse, prepared: prepared.has(d.id), used: graph.data ? usedBy(edges, d.id) : null });
        return (
          <>
            {status.inUse ? <Pill tone="ok">in use</Pill> : null}{status.newer ? <Pill tone="det">newer</Pill> : null}{prepared.has(d.id) ? <Pill tone="gray">made by a run</Pill> : null} {status.text}
          </>
        );
      },
    },
    {
      key: "actions", header: "Actions",
      render: (d) => (
        <span className="toolbar">
          {d.id !== selected ? <button type="button" className="btn" onClick={() => onSelect(d.id)}>Show columns<span className="sr-only"> of {dataVersionName(plainText(d.name, 80), d.created_at)}</span></button> : null}
          {ref?.target.id !== d.id && refs.data && !prepared.has(d.id) && canWrite ? <button type="button" className="btn" onClick={() => { setPending(d.id); setError(null); }}>Use this data<span className="sr-only">: {dataVersionName(plainText(d.name, 80), d.created_at)}</span></button> : null}
          <details><summary>Details<span className="sr-only"> of {dataVersionName(plainText(d.name, 80), d.created_at)}</span></summary><span className="muted">id </span><span className="mono">{d.id}</span></details>
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
      <p className="muted">Old files are never changed or deleted. A newer file is a new version: once it is in use, test designs, runs and models made from an older one are marked <b>built on old data</b>.</p>
      <UploadLog projectId={projectId} />
      {target ? (
        <form className="form card" onSubmit={(event) => { event.preventDefault(); void confirm(); }}>
          <h3>Use {dataVersionName(plainText(target.name, 80), target.created_at)} (version {plainText(target.version, 20)}) as the data in use</h3>
          <p className="muted">This is saved in History. Runs, test designs and models made from the old version are marked built on old data; nothing is retrained and old files are never changed or deleted.</p>
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
      <UploadChecks checks={profile.upload_checks} />
      <section aria-labelledby="d3-checked-run" data-testid="checked-in-run">
      <h3 id="d3-checked-run">Checked inside each run</h3>
      <p className="muted">Leakage and the run&apos;s 15 trust checks are checked inside each run once the goal is known. Most use the training rows; four compare the training rows with the final test set&apos;s columns, never its answers. They work with AI off.{profile.experiment ? " The results below come from one run; all its checks are further down." : " No finished run yet, so there are no results from a run."}</p>
      {findings.isError ? <QueryNotice error={findings.error} what="trust checks" /> : null}
      <DataTable caption="Data checks" columns={checkColumns} rows={rows} rowKey={(r) => r.key} />
      </section>
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

/**
 * "Upload a newer file": one file, a new version of this project's data; single-flight. The Idempotency-Key belongs to the
 * page (`keys`), keyed by name + size + last-modified, so cancelling and reopening with the same file keeps the same key.
 */
function UploadNewer({ projectId, keys, title, onClose, onDone }: { projectId: string; keys: ActionKeys; title: string; onClose: () => void; onDone: (done: { id: string; text: string }) => void }) {
  const inflight = useRef(false);
  const invalidate = useWizardInvalidation();
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<UploadError | null>(null);
  const submit = async () => {
    if (!file || inflight.current) return;
    inflight.current = true;
    setBusy(true);
    setError(null);
    try {
      const created = await uploadDataset({ projectId, file, key: keys.keyFor("upload", `${file.name}:${file.size}:${file.lastModified}`) });
      keys.done("upload");
      invalidate(projectId);
      onDone({ id: created.id, text: `Uploaded ${plainText(created.name, 80)} as a new version: ${created.row_count.toLocaleString("en-GB")} rows, ${created.column_count} columns. Nothing was trained and the data in use did not change.` });
    } catch (caught) {
      setError(uploadErrorText(caught));
    } finally {
      inflight.current = false;
      setBusy(false);
      // Accepted uploads and rejected ones that reached the project add a row to the upload log; an upload refused before
      // that (for example a declared size over the limit) does not.
      void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", "upload-attempts", projectId) });
    }
  };
  return (
    <form className="form card" aria-labelledby="v7-data-upload-title" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <h2 id="v7-data-upload-title" tabIndex={-1}>{title}</h2>
      <p className="muted">The file is added as a new version of this project&apos;s data. Old files are never changed or deleted. Nothing is trained and the data in use stays the same: to use the new file, start a run with it or choose “Use this data” under Versions.</p>
      {error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null}
      <label className="field"><span>File (CSV, TSV, JSON, Parquet or XLSX with a header row)</span>
        <input type="file" accept=".csv,.tsv,.json,.jsonl,.parquet,.xlsx" disabled={busy} onChange={(e) => { setFile(e.target.files?.[0] ?? null); setError(null); }} />
      </label>
      <div className="toolbar">
        <button type="button" className="btn" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" className="btn primary" disabled={busy || !file}>{busy ? "Uploading…" : "Upload"}</button>
      </div>
    </form>
  );
}

function ColumnsTab({ projectId, profile }: { projectId: string; profile: StudioDatasetProfile }) {
  const [query, setQuery] = useState("");
  const decisions = useDecisionPoints(profile.experiment ? projectId : undefined);
  // Shown only while AI is known to be on (off or unknown: hidden) and the run has assistant answers.
  const aiOff = useAiOff();
  const ai = assistantVsRules(decisions.data?.items ?? [], profile.experiment?.id, aiOff, clean);
  const shown = filterColumns(profile.columns, query);
  return (
    <>
      <ScopeNote profile={profile} />
      <RunNote projectId={projectId} profile={profile} />
      <div className="toolbar">
        <label className="field v7-data-filter"><span>Filter columns</span>
          <input type="search" value={query} maxLength={200} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <span className="muted" role="status">Showing {shown.length} of {profile.columns.length} columns</span>
      </div>
      <DataTable caption="Columns" columns={columnTable(Boolean(profile.experiment), ai)} rows={shown} rowKey={(c) => c.name} emptyMessage="No column name contains that text." />
      <details>
        <summary>Technical details per column</summary>
        <DataTable caption="Column details" columns={DETAIL_TABLE} rows={profile.columns} rowKey={(c) => c.name} />
      </details>
    </>
  );
}

export default function DataPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useSession();
  const canWrite = hasCapability(user, CAPABILITIES.workspaceExecuteMl);
  const datasets = useProjectDatasets(id);
  const refs = useProjectRefs(id);
  const graph = useProjectGraph(id);
  const specs = useProblemSpecs(id);
  const [chosen, setChosen] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [focusTo, setFocusTo] = useState<string | null>(null);
  const uploadKeys = useRef(new ActionKeys(newIdempotencyKey)).current;
  useEffect(() => {
    if (!focusTo) return;
    document.getElementById(focusTo)?.focus();
    setFocusTo(null);
  }, [focusTo]);
  const rows = datasets.data ?? [];
  const current = refs.data?.items.find((item) => item.ref_kind === "dataset")?.target.id;
  const prepared = preparedIds(graph.data?.edges ?? []);
  const uploads = rows.filter((d) => !prepared.has(d.id)).map((d) => d.id);
  const selected = chosen && rows.some((d) => d.id === chosen) ? chosen : pickDatasetId(uploads, null, current);
  const profile = useDatasetProfile(selected);
  const shown = rows.find((d) => d.id === selected);
  const experiments = projectHref(id, "experiments");
  const newRun = experiments ? safeInternalHref(`${experiments}/new`) : null;
  const spec = specs.data?.find((s) => s.is_current) ?? specs.data?.[0];
  const summary = shown ? fileSummary(shown, profile.data, (v) => plainText(v, 60), spec?.prediction_unit ?? null) : [];
  const uploadLabel = datasets.data && rows.length === 0 ? "Upload a file" : "Upload a newer file";
  const openUpload = () => { setUploading(true); setDone(null); setFocusTo("v7-data-upload-title"); };
  return (
    <>
      <PageHead
        title="Data"
        subtitle="The files in this project, what is in them and which columns the model uses."
        actions={canWrite ? <button id="v7-data-upload-open" type="button" className="btn" disabled={uploading} onClick={openUpload}>{uploadLabel}</button> : null}
      />
      <PageGuide
        purpose="Check what the model was built from before you trust it."
        howTo={<>Pick a file under Versions. Columns shows which columns are used and why; the technical details compare the <Term definition={TERMS.rule}>rule role</Term> with the <Term definition={TERMS.used}>role used</Term>.</>}
        youGet="Rows and columns, missing values, what each column is used for, the data checks and who can see the data."
        attention="Counts never include the final test set (used once). Before the first run no test design exists, so only names and types are shown."
      />
      <div role="status" aria-live="polite" className={done ? "banner" : undefined}>{done}</div>
      {uploading ? (
        <UploadNewer
          projectId={id}
          keys={uploadKeys}
          title={uploadLabel}
          onClose={() => { setUploading(false); setFocusTo("v7-data-upload-open"); }}
          onDone={(result) => { setUploading(false); setDone(result.text); setChosen(result.id); }}
        />
      ) : null}
      {!canWrite ? <p className="muted">Only people who can change ML work in this workspace can upload a file or change the data in use.</p> : null}
      {datasets.isError ? <QueryNotice error={datasets.error} what="dataset list" /> : null}
      {datasets.isPending ? <p role="status">Loading data…</p> : null}
      {datasets.data && rows.length === 0 ? (
        <div className="empty">No training data in this project yet. {newRun ? <Link href={newRun}>Start a run with a file</Link> : null}</div>
      ) : null}
      {selected && shown ? (
        <>
          <section className="grid cols-4" aria-label="File summary">
            {summary.map((item) => <Stat key={item.key} value={item.value} label={item.label} hint={item.hint} />)}
          </section>
          {profile.data?.statistics_status === "computed" ? <p className="muted">“Training period” values count the training rows only; the final test set is never counted.</p> : null}
          <SectionTabs
            title={dataVersionName(plainText(shown.name, 80), shown.created_at)}
            idPrefix="data"
            sections={[
              {
                id: "columns", label: "Columns", count: profile.data?.columns.length,
                content: profile.isError ? <QueryNotice error={profile.error} what="column profile" /> : !profile.data ? <p role="status">Loading columns…</p> : <ColumnsTab projectId={id} profile={profile.data} />,
              },
              { id: "versions", label: "Versions", count: rows.length, content: <VersionsTab projectId={id} datasets={rows} selected={selected} onSelect={setChosen} canWrite={canWrite} /> },
              { id: "balance", label: "Target over time", content: <TargetOverTime datasetId={selected} /> },
              {
                id: "checks", label: "Data checks",
                content: profile.isError ? <QueryNotice error={profile.error} what="data checks" /> : profile.data ? <ChecksTab projectId={id} profile={profile.data} /> : <p role="status">Loading data checks…</p>,
              },
              { id: "access", label: "Access", content: <AccessTab datasetId={selected} /> },
            ]}
          />
        </>
      ) : null}
    </>
  );
}
