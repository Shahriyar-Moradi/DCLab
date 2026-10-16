"use client";

/**
 * Monitoring (P7.5-A): whether the inputs the model in use sees look different from training (per column, weekly), whether
 * its scores moved, and — once a person links a file of real outcomes — how it actually did. Drift means "the inputs look
 * different from training", never "the model is wrong"; a column is marked only when PSI passes the line AND a statistical
 * test says it is not chance. Date columns are shown but not judged. DCLab never retrains or switches the model on its own;
 * going back to the previous approved model is the Model screen's own confirm step (same component, same write). Every
 * value comes from the monitoring reads; names are untrusted plain text; no final test value is shown.
 */
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ConfirmApproval } from "@/app/components/studio-app/ApprovedForScoring";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { KeyValue } from "@/components/studio/KeyValue";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { Term } from "@/components/studio/Term";
import { isUuid, plainText, projectHref } from "@/lib/application/command-search";
import { utcWhen } from "@/lib/application/format-time";
import { useSession } from "@/lib/application/session-provider";
import { useProjectRefs } from "@/lib/application/studio-hooks";
import {
  REAL_NOT_TEST, TABLE_NOTE, THRESHOLDS_READ_ONLY, THRESHOLDS_UNAVAILABLE, WEEK_DEFINITION, canAct, dateNote, driftBars, featureList, featureSentence, goBackOffer,
  headline, idCaveat, isCounted, isDate, isLinkConflict, linkConfirmWords, linkedRows, mapLinkError, mapUploadError, outcomeView, psiDefinition, scopeText,
  scoreSentences, statusWords, thresholdLines, weekLabel, weekRows, weekSentence, type FeatureLike, type FileInfo, type InputScope, type LinkIntent, type Thresholds,
  type WeekRow,
} from "@/lib/application/studio-monitoring";
import { linkOutcomes, useDatasetColumns, useMonitoring, useMonitoringInvalidation, useMonitoringWeek, useOutcomeSets } from "@/lib/application/studio-monitoring-hooks";
import { modelLabel, releaseRows } from "@/lib/application/studio-releases";
import { useProjectReleases } from "@/lib/application/studio-releases-hooks";
import { ActionKeys, type PlainError } from "@/lib/application/studio-wizard";
import { uploadDataset, useProjectDatasets } from "@/lib/application/studio-wizard-hooks";
import { ApiError } from "@/lib/infrastructure/api-client";
import { CAPABILITIES, hasCapability } from "@/lib/infrastructure/capabilities";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

const STATUS_ID = "monitoring-status";
const TONE = { ok: "ok", warn: "warn", crit: "crit", gray: "gray" } as const;

function ReadFailed({ what, onRetry, error }: { what: string; onRetry: () => void; error: unknown }) {
  const missing = error instanceof ApiError && (error.status === 404 || error.status === 403);
  return (
    <Banner tone={missing ? "warn" : "crit"} actions={<button type="button" className="btn" onClick={onRetry}>Reload</button>}>
      {missing ? <>The {what} is not in the active workspace, or you cannot see it.</> : <><b>The {what} could not be loaded.</b> Nothing was changed. Check your connection and reload.</>}
    </Banner>
  );
}

function DriftChart({ features, thresholds }: { features: readonly FeatureLike[]; thresholds: Thresholds }) {
  const chart = driftBars(features, thresholds);
  if (!chart.bars.length) return <p className="muted">{chart.summary}</p>;
  const at = (v: number) => Math.min(100, (v / chart.max) * 100);
  const color = (status: string) => (status === "alert" ? "var(--crit)" : status === "watch" ? "var(--warn)" : "var(--muted)");
  return (
    <figure role="img" aria-label={chart.summary} data-testid="drift-chart" style={{ margin: "8px 0 16px" }}>
      <div aria-hidden="true" style={{ position: "relative", height: chart.stackLabels ? 36 : 22, marginLeft: "min(45%, 240px)", fontSize: 12, color: "var(--muted)" }}>
        <span style={{ position: "absolute", top: 0, left: `${at(chart.watch)}%`, transform: "translateX(-50%)", whiteSpace: "nowrap" }}>watch {chart.watch.toFixed(2)}</span>
        <span style={{ position: "absolute", top: chart.stackLabels ? 16 : 0, left: `${at(chart.alert)}%`, transform: "translateX(-10%)", whiteSpace: "nowrap" }}>alert {chart.alert.toFixed(2)}</span>
      </div>
      {chart.bars.map((bar, i) => (
        <div key={i} aria-hidden="true" style={{ display: "grid", gridTemplateColumns: "min(45%, 240px) 1fr", alignItems: "center", gap: 8, minHeight: 28 }}>
          <span style={{ overflowWrap: "anywhere", fontSize: 14 }}>{bar.name} <span className="mono muted">{bar.psi.toFixed(2)} · {bar.word}{bar.offScale ? " · off scale" : ""}</span></span>
          <svg width="100%" height="16" viewBox="0 0 100 16" preserveAspectRatio="none" focusable="false">
            <rect x="0" y="3" height="10" rx="1" width={bar.width} style={{ fill: color(bar.status) }} />
            {bar.offScale ? <polygon points="96,1 100,8 96,15" style={{ fill: "var(--ink)" }} /> : null}
            <line x1={at(chart.watch)} x2={at(chart.watch)} y1="0" y2="16" vectorEffect="non-scaling-stroke" strokeDasharray="3 2" style={{ stroke: "var(--warn)", strokeWidth: 1.5 }} />
            <line x1={at(chart.alert)} x2={at(chart.alert)} y1="0" y2="16" vectorEffect="non-scaling-stroke" strokeDasharray="3 2" style={{ stroke: "var(--crit)", strokeWidth: 1.5 }} />
          </svg>
        </div>
      ))}
      <figcaption className="muted" style={{ fontSize: 13, marginTop: 6 }}>
        Bars: input change (PSI) of the judged columns, largest first; each row names its status (Alert, Watch, Within chance, Little change). Red and amber bars are marked columns; grey bars are not. Dashed lines: watch and alert, set by DCLab. A bar longer than the axis ends in an arrow and says “off scale”. Date columns are not in the chart.
      </figcaption>
    </figure>
  );
}

function WeekDetail({ projectId, week, thresholds }: { projectId: string; week: string; thresholds: Thresholds }) {
  const read = useMonitoringWeek(projectId, week);
  if (read.isPending) return <Card title="Week"><p role="status">Loading the week…</p></Card>;
  if (read.isError || !read.data) return <Card title="Week"><ReadFailed what="week" error={read.error} onRetry={() => void read.refetch()} /></Card>;
  const w = read.data;
  const t = w.thresholds ?? thresholds;
  const inputs = (w.input_drift ?? {}) as InputScope & { available?: unknown; files_unavailable?: unknown };
  const features = featureList(w.input_drift);
  const judged = features.filter(isCounted);
  const dates = features.filter(isDate);
  const compared = typeof inputs.features_compared === "number" ? inputs.features_compared : null;
  const caveat = idCaveat(judged);
  const words = statusWords(w.status);
  const outcome = outcomeView(w.outcomes, t);
  const scope = scopeText(inputs);
  const notShown = typeof inputs.features_not_shown === "number" && inputs.features_not_shown > 0 ? inputs.features_not_shown : 0;
  const unavailable = typeof inputs.files_unavailable === "number" && inputs.files_unavailable > 0 ? inputs.files_unavailable : 0;
  const label = weekLabel(w.window_start);
  return (
    <Card title={<>Week: {label.replace(/^week of /, "")}</>} aside={<Pill tone={TONE[words.tone]}>{words.label}</Pill>}>
      <p data-testid="week-sentence">{weekSentence(w, t, compared)}</p>
      <p className="muted">{w.n_rows.toLocaleString("en-US")} rows scored in {w.n_batch_predictions} {w.n_batch_predictions === 1 ? "scoring" : "scorings"} this week (checked {utcWhen(w.computed_at)}).{unavailable ? ` ${unavailable} scored ${unavailable === 1 ? "file is" : "files are"} no longer available and ${unavailable === 1 ? "was" : "were"} left out.` : ""}</p>
      {scope ? <p data-testid="week-scope">{scope}</p> : null}
      <h3>Columns that moved most</h3>
      {inputs.available === true ? (
        <>
          <DriftChart features={judged} thresholds={t} />
          {caveat ? <Banner tone="info"><span data-testid="id-caveat">{caveat}</span></Banner> : null}
          <ul className="approvals" aria-label="Judged columns in plain words, largest change first">
            {judged.map((f, i) => <li key={i}>{featureSentence(f, t)}</li>)}
          </ul>
          {notShown ? <p className="muted">{notShown} more {notShown === 1 ? "column was" : "columns were"} compared and moved less.</p> : null}
          {dates.length ? (
            <>
              <h3>Date columns</h3>
              <ul className="approvals" aria-label="Date columns, shown but not judged" data-testid="date-columns">
                {dates.map((f, i) => <li key={i}>{featureSentence(f, t)}</li>)}
              </ul>
              <p className="muted" data-testid="date-note">{dateNote(dates)}</p>
            </>
          ) : null}
        </>
      ) : <p className="muted">The inputs could not be compared with training for this week (see above).</p>}
      <h3>Scores</h3>
      {scoreSentences(w.prediction_drift, t).map((line) => <p key={line}>{line}</p>)}
      <h3 id={`real-${week}`}>Real-world results</h3>
      <section aria-labelledby={`real-${week}`} data-testid="real-results">
        {outcome.kind === "measured" ? (
          <>
            <p><Pill tone="ok">Real outcomes</Pill> {outcome.lead}</p>
            <KeyValue items={outcome.metrics.map((m) => ({ key: m.label, label: m.label, value: <span className="mono">{m.value}</span> }))} />
          </>
        ) : <p>{outcome.lead}</p>}
        {outcome.notes.map((note) => <p key={note} className="muted">{note}</p>)}
        <p className="muted">{REAL_NOT_TEST}</p>
      </section>
      <details>
        <summary>Watch and alert lines (read-only)</summary>
        <ul>{thresholdLines(t).map((line) => <li key={line}>{line}</li>)}</ul>
        <p className="muted">{THRESHOLDS_READ_ONLY}</p>
      </details>
    </Card>
  );
}

function LinkConfirm({ projectId, intent, thresholds, status, onClose, onDone }: {
  projectId: string; intent: LinkIntent; thresholds: Thresholds; status: string; onClose: () => void; onDone: (text: string) => void;
}) {
  const invalidate = useMonitoringInvalidation();
  // The same request resent keeps its key; a changed file or column is a new action with a new key.
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const words = linkConfirmWords(intent, thresholds, status);
  useEffect(() => heading.current?.focus(), []);
  const send = async () => {
    if (flight.current) return;
    flight.current = true;
    setBusy(true);
    setError(null);
    try {
      await linkOutcomes({ projectId, datasetId: intent.datasetId, keyColumn: intent.key, outcomeColumn: intent.outcome, key: keys.keyFor("link", `${intent.datasetId}:${intent.key}:${intent.outcome}`) });
      keys.done("link");
      invalidate();
      onDone(`Linked ${plainText(intent.file, 80)} as real outcomes. Real-world results appear here once enough rows are labelled; reload in a few minutes. The link was saved in History.`);
    } catch (caught) {
      if (isLinkConflict(caught)) keys.done("link"); // the same request can never succeed: the next try is new
      else invalidate(); // it may have been saved: re-read the list of linked files
      setError(mapLinkError(caught));
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };
  return (
    <form className="form card" aria-label={words.title} onSubmit={(event) => { event.preventDefault(); void send(); }}>
      <h3 ref={heading} tabIndex={-1}>{words.title}</h3>
      <ul>{words.lines.map((line) => <li key={line}>{line}</li>)}</ul>
      {error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null}
      <div className="toolbar">
        <button type="button" className="btn" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" className="btn primary" disabled={busy}>{busy ? "Linking…" : words.button}</button>
      </div>
    </form>
  );
}

type FileOption = { id: string; name: string; label: string; rows: number | null; uploadedAt: string | null };

function LinkOutcomes({ projectId, canWrite, enabled, status, thresholds }: { projectId: string; canWrite: boolean; enabled: boolean; status: string; thresholds: Thresholds }) {
  const { user } = useSession();
  const sets = useOutcomeSets(projectId);
  const datasets = useProjectDatasets(projectId);
  const invalidate = useMonitoringInvalidation();
  const uploadKeys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const [datasetId, setDatasetId] = useState("");
  const [uploaded, setUploaded] = useState<Record<string, { name: string; rows: number; columns: string[] }>>({});
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<PlainError | null>(null);
  const [key, setKey] = useState("");
  const [outcome, setOutcome] = useState("");
  const [intent, setIntent] = useState<LinkIntent | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [focusTo, setFocusTo] = useState<string | null>(null);
  const mine = Object.hasOwn(uploaded, datasetId) ? uploaded[datasetId] : undefined;
  // An upload that returned no column list falls back to the file's profile read (an empty list is not "known").
  const ownColumns = mine && mine.columns.length ? mine.columns : null;
  const profile = useDatasetColumns(isUuid(datasetId) && !ownColumns ? datasetId : null);
  const columns = ownColumns ?? profile.data ?? [];
  const listed = (datasets.data ?? []).filter((d) => isUuid(d.id));
  const options: FileOption[] = [
    ...Object.entries(uploaded).filter(([id]) => !listed.some((d) => d.id === id))
      .map(([id, u]) => ({ id, name: u.name, label: `${plainText(u.name, 80)} (just uploaded)`, rows: u.rows, uploadedAt: null })),
    ...listed.map((d) => ({ id: d.id, name: d.name, label: `${plainText(d.name, 80)} · ${d.row_count.toLocaleString("en-US")} rows · uploaded ${utcWhen(d.created_at)}`, rows: d.row_count, uploadedAt: d.created_at })),
  ];
  const chosen = options.find((o) => o.id === datasetId) ?? null;
  const files = new Map<string, FileInfo>(options.map((o) => [o.id, { name: o.name, rows: o.rows, uploadedAt: o.uploadedAt }]));
  const rows = linkedRows(sets.data?.items ?? [], user?.id ?? null, files);
  const editable = canAct({ canWrite, confirming: !!intent });
  const ready = !!chosen && !!key && !!outcome && key !== outcome && columns.includes(key) && columns.includes(outcome);

  useEffect(() => {
    if (!focusTo) return;
    document.getElementById(focusTo)?.focus();
    setFocusTo(null);
  }, [focusTo]);

  const pick = (id: string) => { setDatasetId(id); setKey(""); setOutcome(""); setDone(null); };
  const pickKey = (value: string) => { setKey(value); if (value && value === outcome) setOutcome(""); };
  const upload = async () => {
    if (!file || uploading) return;
    setUploading(true);
    setUploadError(null);
    try {
      const result = await uploadDataset({ projectId, file, key: uploadKeys.keyFor("upload", `${projectId}:${file.name}:${file.size}:${file.lastModified}`) });
      uploadKeys.done("upload");
      setUploaded((prev) => ({ ...prev, [result.id]: { name: result.name, rows: result.row_count, columns: (result.columns ?? []).map((c) => c.name) } }));
      pick(result.id);
      setFile(null);
      invalidate();
    } catch (caught) {
      setUploadError(mapUploadError(caught));
      invalidate();
    } finally {
      setUploading(false);
    }
  };
  return (
    <Card title="Link real outcomes">
      <p>When the real results of scored rows are known (for example, who really cancelled), link a file that holds them. DCLab matches each outcome to a scored row by an ID column and then shows how the model in use really did.</p>
      <div role="status" aria-live="polite" className={done ? "banner" : undefined}>
        {done ? <>{done}{projectHref(projectId, "decisions") ? <> <Link href={projectHref(projectId, "decisions") as string}>Open History</Link>.</> : null}</> : null}
      </div>
      {!canWrite ? <p className="muted">Only people who can run ML work in this workspace can link an outcomes file.</p> : null}
      {canWrite && !enabled ? <Banner tone="warn">Monitoring is switched off on this server, so linking a file is refused until it is turned on.</Banner> : null}
      {canWrite && enabled ? (
        <>
          {intent ? (
            <LinkConfirm
              key={`${intent.datasetId}:${intent.key}:${intent.outcome}`} projectId={projectId} intent={intent} thresholds={thresholds} status={status}
              onClose={() => { setFocusTo("link-open"); setIntent(null); }}
              onDone={(text) => { setDone(text); setIntent(null); setKey(""); setOutcome(""); setDatasetId(""); setFocusTo("linked-files"); }}
            />
          ) : null}
          <div className="form" role="group" aria-label="Outcomes file">
            <div className="field">
              <span id="upload-label">Upload a new outcomes file (optional)</span>
              <input type="file" aria-labelledby="upload-label" accept=".csv,.tsv,.json,.parquet,.xlsx" disabled={!editable || uploading} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              <p className="muted">It is kept with this project&apos;s files like any other upload, and is used for training only if someone picks it for a run.</p>
              <p><button type="button" className="btn" disabled={!editable || !file || uploading} onClick={() => void upload()}>{uploading ? "Uploading…" : "Upload file"}</button></p>
              {uploadError ? <Banner tone="crit"><b>{uploadError.title}.</b> {uploadError.detail}</Banner> : null}
            </div>
            <label className="field"><span>File with the real outcomes</span>
              <select value={datasetId} disabled={!editable} onChange={(e) => pick(e.target.value)}>
                <option value="">Choose a file of this project…</option>
                {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
              </select>
            </label>
            {datasets.isError ? <Banner tone="warn">The project&apos;s files could not be loaded. Reload the page, or upload the file above.</Banner> : null}
            {isUuid(datasetId) && !ownColumns && profile.isPending ? <p role="status">Loading the file&apos;s columns…</p> : null}
            {isUuid(datasetId) && !ownColumns && profile.isError ? <Banner tone="warn">The file&apos;s columns could not be loaded. Reload, or pick another file.</Banner> : null}
            {columns.length ? (
              <>
                <label className="field"><span>Key column (the same IDs as the scored rows&apos; ID column)</span>
                  <select value={key} disabled={!editable} onChange={(e) => pickKey(e.target.value)}>
                    <option value="">Choose a column…</option>
                    {columns.map((c) => <option key={c} value={c}>{plainText(c, 60)}</option>)}
                  </select>
                </label>
                <label className="field"><span>Outcome column (what really happened)</span>
                  <select value={outcome} disabled={!editable} onChange={(e) => setOutcome(e.target.value)}>
                    <option value="">Choose a column…</option>
                    {columns.filter((c) => c !== key).map((c) => <option key={c} value={c}>{plainText(c, 60)}</option>)}
                  </select>
                </label>
              </>
            ) : null}
            {editable ? (
              <div className="toolbar">
                <button id="link-open" type="button" className="btn primary" disabled={!ready} onClick={() => {
                  if (!chosen) return;
                  setIntent({ datasetId: chosen.id, file: chosen.name, key, outcome, rows: chosen.rows, uploadedAt: chosen.uploadedAt });
                  setDone(null);
                }}>Link this file</button>
              </div>
            ) : null}
          </div>
        </>
      ) : null}
      <h3 id="linked-files" tabIndex={-1}>Linked files</h3>
      {sets.isPending ? <p role="status">Loading the linked files…</p> : sets.isError ? <ReadFailed what="list of linked files" error={sets.error} onRetry={() => void sets.refetch()} /> : rows.length ? (
        <ul className="approvals" aria-label="Linked outcomes files, newest first">
          {rows.map((r) => (
            <li key={r.id}><p><b>{r.file}</b><br /><span>Key column: {r.key} · outcome column: {r.outcome}</span><br /><span className="muted">{r.line}</span></p></li>
          ))}
        </ul>
      ) : <p className="muted">No outcomes file is linked yet.</p>}
    </Card>
  );
}

export function MonitoringScreen({ projectId }: { projectId: string }) {
  const { user } = useSession();
  const canWrite = hasCapability(user, CAPABILITIES.workspaceExecuteMl);
  const validId = isUuid(projectId);
  const read = useMonitoring(projectId);
  const refs = useProjectRefs(projectId);
  const releases = useProjectReleases(projectId);
  const invalidate = useMonitoringInvalidation();
  const [picked, setPicked] = useState<string | null>(null);
  const [target, setTarget] = useState<{ modelVersionId: string; model: string } | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [focusTo, setFocusTo] = useState<string | null>(null);
  const o = read.data;
  const model = o?.model_version ? modelLabel(o.model_version) : "The model in use";
  const head = headline({ pending: validId && read.isPending, failed: !validId || (read.isError && !o), overview: o, model });
  const thresholds = o?.thresholds ?? null;
  const weeks = o && thresholds ? weekRows(o.windows, thresholds) : [];
  const week = picked && weeks.some((w) => w.key === picked) ? picked : weeks[0]?.key ?? null;
  const championId = refs.data?.items.find((r) => r.ref_kind === "champion_model")?.target.id ?? null;
  const approvals = releaseRows(releases.data?.items ?? [], { myUserId: user?.id ?? null, inUseModelId: isUuid(championId) ? championId : null, when: utcWhen });
  const offer = o && releases.data && refs.data
    ? goBackOffer(o.status, approvals, { weekModelId: o.latest?.model_version_id ?? null, inUseId: isUuid(championId) ? championId : null })
    : { kind: "hidden" as const };
  const approved = projectHref(projectId, "models");
  // A confirm step whose model is no longer the previous approved model (someone changed the model in use) is closed.
  const stale = !!target && (offer.kind !== "offer" || offer.modelVersionId !== target.modelVersionId);

  useEffect(() => { if (stale) setTarget(null); }, [stale]);
  useEffect(() => {
    if (!focusTo) return;
    document.getElementById(focusTo)?.focus();
    setFocusTo(null);
  }, [focusTo]);

  const columns: Column<WeekRow>[] = [
    { key: "week", header: "Week (UTC)", render: (r) => r.week.replace(/^week of /, "").replace(/ \(UTC\)$/, "") },
    { key: "label", header: "Status", render: (r) => <Pill tone={TONE[r.tone]}>{r.label}</Pill> },
    { key: "rows", header: "Rows scored", numeric: true },
    { key: "inputs", header: "Largest input change (PSI)", numeric: true },
    { key: "scores", header: "Score change (PSI)", numeric: true },
    { key: "outcomes", header: "Real-world results" },
    {
      key: "open", header: "Details",
      // Every row keeps a focusable button (the shown week is pressed), so a table that scrolls sideways stays reachable by keyboard.
      render: (r) => {
        const text = r.key === week ? "Shown below" : "Show";
        return <button type="button" className="btn" aria-pressed={r.key === week} aria-label={`${text}: ${r.week}`} onClick={() => { setPicked(r.key); setFocusTo("week-detail"); }}>{text}</button>;
      },
    },
  ];
  return (
    <>
      <PageHead title="Monitoring" subtitle="Whether the data the model in use scores still looks like the data it learned from, and how it really did once outcomes are known." />
      <PageGuide
        purpose={<>Each <Term definition={WEEK_DEFINITION}>week</Term>, DCLab compares the rows the model in use scored with its training data, column by column, and compares its scores with the scores it gave during training. Once you link real outcomes, it also shows how the model really did on those rows.</>}
        howTo="Read the status first, then open a week to see which columns moved. When real results are known, link the file that holds them below."
        youGet={<>A status per week (the compared columns look like training, watch, alert, or not enough data), the columns that moved most with their <Term definition={psiDefinition(thresholds)}>change score (PSI)</Term>, how the scores moved, and real-world results when outcomes are linked.</>}
        attention="A change in the inputs means the data looks different from training, not that the model is wrong, and the page never names a cause. A column is marked only when its change passes the line and a statistical test says it is not chance. Date columns are shown but not judged; ID-like columns can look different when new customers appear. DCLab never retrains or switches the model on its own: a person decides. The watch and alert lines are set by DCLab and cannot be changed here."
      />
      <Card title="Model in use" aside={head.kind !== "loading" && head.kind !== "error" ? <Pill tone={TONE[head.tone]}>{head.label}</Pill> : undefined}>
        {!validId ? <Banner tone="warn">This project link is not valid. Open the project from the list of projects.</Banner>
          : head.kind === "error" ? <ReadFailed what="monitoring status" error={read.error} onRetry={() => void read.refetch()} />
            : head.kind === "loading" ? <p role="status">Loading monitoring…</p>
              : <p id={STATUS_ID} tabIndex={-1} data-testid="monitoring-status"><b>{head.sentence}</b></p>}
        {read.isError && o ? <Banner tone="warn" actions={<button type="button" className="btn" onClick={() => void read.refetch()}>Reload</button>}>Monitoring could not be refreshed, so what is shown may be out of date.</Banner> : null}
        {validId && !thresholds && head.kind !== "loading" ? <p className="muted">{THRESHOLDS_UNAVAILABLE}</p> : null}
        <div role="status" aria-live="polite" className={done ? "banner" : undefined}>
          {done ? <>{done}{projectHref(projectId, "decisions") ? <> <Link href={projectHref(projectId, "decisions") as string}>Open History</Link>.</> : null}</> : null}
        </div>
        {offer.kind === "none" ? <p data-testid="go-back-none">{offer.text}</p> : null}
        {offer.kind === "offer" ? (
          canWrite ? (
            <>
              <p data-testid="go-back-offer">{offer.text}</p>
              {canAct({ canWrite, confirming: !!target }) ? (
                <p><button id="go-back-open" type="button" className="btn" onClick={() => { setTarget({ modelVersionId: offer.modelVersionId, model: offer.model }); setDone(null); }}>Go back to the previous model: {offer.model}</button></p>
              ) : null}
              {target ? (
                <ConfirmApproval
                  key={target.modelVersionId} projectId={projectId} current={model === "The model in use" ? null : model} where="the Monitoring screen"
                  target={{ mode: "go_back", modelVersionId: target.modelVersionId, model: target.model, returnId: "go-back-open" }}
                  onClose={() => { setFocusTo("go-back-open"); setTarget(null); }}
                  onDone={(text) => { setDone(text); setTarget(null); invalidate(); setFocusTo(STATUS_ID); }}
                />
              ) : null}
            </>
          ) : <p data-testid="go-back-offer">{offer.viewerText}</p>
        ) : null}
        {approved && head.kind !== "loading" && head.kind !== "error" ? (
          <p className="muted">Which model is in use, and every earlier approval, is on <Link href={`${approved}?tab=approved`}>Model → Approved for scoring</Link>.</p>
        ) : null}
      </Card>
      {o && weeks.length ? (
        <>
          <h2>Weekly checks</h2>
          <p className="muted">The last {weeks.length} {weeks.length === 1 ? "week" : "weeks"} of the model in use, newest first. Times are UTC. {TABLE_NOTE}</p>
          <DataTable caption="Weekly checks of the model in use, newest first" columns={columns} rows={weeks} rowKey={(r) => r.key} highlightRow={(r) => r.key === week} />
          <div id="week-detail" tabIndex={-1}>
            {week && thresholds ? <WeekDetail projectId={projectId} week={week} thresholds={thresholds} /> : null}
          </div>
        </>
      ) : null}
      {o && thresholds ? <LinkOutcomes projectId={projectId} canWrite={canWrite} enabled={o.enabled} status={o.status} thresholds={thresholds} /> : null}
    </>
  );
}
