"use client";

/**
 * Score new data (P4.9-UI): upload a scoring file, run the model on it, read the column check in plain
 * words, follow progress and download the predictions. Names, file names and API messages are untrusted
 * plain text. The API has no list read for scorings, so history is the ones started in this browser session.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { formatWhen } from "@/app/components/studio-app/StudioParts";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { KeyValue } from "@/components/studio/KeyValue";
import { PageGuide } from "@/components/studio/PageGuide";
import { Pill } from "@/components/studio/Pill";
import { StepBar, type Step } from "@/components/studio/StepBar";
import { Term } from "@/components/studio/Term";
import { plainText } from "@/lib/application/command-search";
import { safeFilename } from "@/lib/application/studio-inspect";
import {
  contractSentences, contractView, failureError, isTerminal, mapScoringError, parseSessionScorings, type SessionScoring,
} from "@/lib/application/studio-scoring";
import { createPrediction, downloadPredictions, uploadScoringFile, usePrediction, type StudioPrediction } from "@/lib/application/studio-scoring-hooks";
import { ActionKeys, singleFlight, type PlainError } from "@/lib/application/studio-wizard";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

const TERMS = {
  contract: "New files must have the same columns: the list of columns the model was trained on. A file can be scored only if it has every one of them, with the same names.",
  threshold: "Set when the model was built: picked on cross-validation predictions when the project declared a cost, a constraint or a threshold-based metric, otherwise the default 0.5. Rows at or above it are labelled positive. It is applied as stored and cannot be changed here.", // see also GLOSSARY.threshold
  operating: "The operating point is the threshold the model runs at. It decides how many rows are labelled positive, trading missed positives against false alarms.",
};

const STORE = "dclab.scorings.";

function readSession(modelVersionId: string): SessionScoring[] {
  try {
    return parseSessionScorings(window.sessionStorage.getItem(STORE + modelVersionId));
  } catch {
    return [];
  }
}
function writeSession(modelVersionId: string, rows: SessionScoring[]) {
  try {
    window.sessionStorage.setItem(STORE + modelVersionId, JSON.stringify(rows));
  } catch {
    /* storage can be blocked; the list then lives in memory only */
  }
}

function ErrorBanner({ error }: { error: PlainError | null }) {
  return error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null;
}

const STATUS_TONE = { queued: "gray", running: "ai", completed: "ok", failed: "crit" } as const;

function stepsFor(prediction: StudioPrediction | undefined, busy: boolean): Step[] {
  const status = prediction?.status;
  const checked = Boolean(prediction?.contract_check);
  const state = (done: boolean, current: boolean): Step["state"] => (done ? "done" : current ? "current" : "todo");
  return [
    { id: "upload", label: "Upload file", state: state(Boolean(prediction), !prediction && busy) },
    { id: "check", label: "Check columns", state: state(checked || status === "completed", Boolean(prediction) && !checked && status !== "failed") },
    { id: "score", label: "Score rows", state: state(status === "completed" || status === "failed", status === "running" || (checked && status === "queued")) },
    { id: "download", label: "Download", state: state(false, status === "completed") },
  ];
}

function ScoringCard({ item, current, modelVersionId }: { item: SessionScoring; current: boolean; modelVersionId: string }) {
  const read = usePrediction(item.id);
  const [error, setError] = useState<PlainError | null>(null);
  const [saving, setSaving] = useState(false);
  const p = read.data && read.data.model_version_id === modelVersionId ? read.data : undefined;
  const contract = contractView(p?.contract_check);
  const sentences = contract ? contractSentences(contract) : null;

  const download = async () => {
    if (!p || saving) return;
    setSaving(true);
    setError(null);
    try {
      const { blob, filename } = await downloadPredictions(p);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = safeFilename(filename, `predictions-${p.id}.csv`);
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (caught) {
      setError(caught instanceof Error && !("status" in caught) ? { title: "The download was not opened", detail: caught.message, fixable: false } : mapScoringError(caught));
    } finally {
      setSaving(false);
    }
  };

  const failure = p?.status === "failed" ? failureError(p.error_code, p.error_message) : null;
  return (
    <Card
      title={`Scoring of ${plainText(item.fileName, 80)}`}
      aside={p ? <Pill tone={STATUS_TONE[p.status]}>{p.status}</Pill> : <span className="muted">loading</span>}
    >
      {current ? <StepBar label="Scoring progress" steps={stepsFor(p, false)} /> : null}
      {read.isError ? <Banner tone="crit">Could not read this scoring. {read.error instanceof Error ? read.error.message : ""}</Banner> : null}
      {p && !isTerminal(p.status) ? <p role="status">{p.status === "queued" ? "Waiting for a worker to pick this up…" : "Scoring the rows…"}</p> : null}
      {sentences ? (
        <section aria-label="Column check">
          <h3><Term definition={TERMS.contract}>Column check</Term></h3>
          <Banner tone={sentences.tone === "ok" ? "info" : sentences.tone}>
            {sentences.lines.map((line) => <span key={`${line}`} style={{ display: "block" }}>{line}</span>)}
          </Banner>
        </section>
      ) : null}
      {failure && !(contract && contract.missingCount > 0) ? <ErrorBanner error={failure} /> : null}
      {p?.status === "completed" ? (
        <>
          <KeyValue items={[
            { key: "rows", label: "Rows scored", value: `${p.rows_out ?? "—"} of ${p.rows_in ?? "—"}` },
            { key: "thr", label: <Term definition={TERMS.threshold}>Threshold applied</Term>, value: p.decision_threshold != null ? p.decision_threshold : "Not used (this model predicts a number or several classes)" },
            { key: "fmt", label: "File", value: `${p.output_format.toUpperCase()}${p.output ? `, ${p.output.size_bytes} bytes` : ""}` },
            { key: "done", label: "Completed", value: formatWhen(p.completed_at) },
            { key: "id", label: "Scoring id", value: <span className="mono">{p.id}</span> },
          ]} />
          <p className="toolbar"><button type="button" className="btn primary" onClick={() => void download()} disabled={saving}>{saving ? "Preparing…" : "Download predictions"}</button></p>
          <p className="muted">One row per input row, in the same order, with the row number and the model&apos;s prediction columns{p.decision_threshold != null ? " (the probability and the label at the stored threshold)" : ""}.</p>
        </>
      ) : null}
      <ErrorBanner error={error} />
    </Card>
  );
}

export function ScoreNewData({ projectId, modelVersionId }: { projectId: string; modelVersionId: string }) {
  const client = useQueryClient();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const [file, setFile] = useState<File | null>(null);
  const [rows, setRows] = useState<SessionScoring[]>([]);
  const [error, setError] = useState<PlainError | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => setRows(readSession(modelVersionId)), [modelVersionId]);

  const work = useRef<() => Promise<void>>(async () => {});
  const flight = useRef(singleFlight(() => work.current())).current;

  const score = () => {
    if (!busyRef.current) {
      busyRef.current = true;
      work.current = async () => {
        setBusy(true);
        setError(null);
        try {
          if (!file) throw new Error("Choose a file to score first.");
          const upload = await uploadScoringFile({ projectId, file, key: keys.keyFor("upload", `${projectId}:${file.name}:${file.size}:${file.lastModified}`) });
          const created = await createPrediction({ modelVersionId, datasetId: upload.id, key: keys.keyFor("score", `${modelVersionId}:${upload.id}`) });
          keys.done("upload");
          keys.done("score");
          client.setQueryData(workspaceQueryKey("v1", "prediction", created.id), created);
          const next = [{ id: created.id, fileName: file.name }, ...readSession(modelVersionId).filter((r) => r.id !== created.id)].slice(0, 20);
          writeSession(modelVersionId, next);
          setRows(next);
          setFile(null);
          if (fileInput.current) fileInput.current.value = "";
        } catch (caught) {
          setError(caught instanceof Error && !("status" in caught) ? { title: "Check your answers", detail: caught.message, fixable: true } : mapScoringError(caught));
        } finally {
          busyRef.current = false;
          setBusy(false);
        }
      };
    }
    return flight();
  };

  const idle = !rows.length;
  return (
    <>
      <PageGuide
        purpose="Run this model on new rows and get a prediction for each one."
        howTo={<>Upload a file with the same feature columns the model was trained on (the <Term definition={TERMS.contract}>feature contract</Term>). The column you predict is not needed and is ignored if present.</>}
        youGet={<>A predictions file with one row per input row. For a yes/no model the labels use the stored <Term definition={TERMS.threshold}>threshold</Term> at the model&apos;s <Term definition={TERMS.operating}>operating point</Term>.</>}
        attention="Use rows the model has not seen. Only an exact copy of the training file (the same upload or identical bytes) is refused. A re-saved, re-ordered or edited copy is not detected, so check yourself that the rows are new."
      />
      <Card title="Score a file" aside={<span className="muted">CSV, TSV, JSON, Parquet or XLSX</span>}>
        <form onSubmit={(event) => { event.preventDefault(); void score(); }}>
          <label className="field"><span>Scoring file</span>
            <input ref={fileInput} type="file" accept=".csv,.tsv,.json,.jsonl,.parquet,.xlsx" disabled={busy} onChange={(e) => { setFile(e.target.files?.[0] ?? null); setError(null); }} />
          </label>
          <p className="toolbar"><button type="submit" className="btn primary" disabled={busy || !file}>{busy ? "Uploading and starting…" : "Score this file"}</button></p>
        </form>
        <ErrorBanner error={error} />
        {busy ? <p role="status">Uploading the file, then asking the model to score it…</p> : null}
        {idle && !busy ? <StepBar label="Scoring steps" steps={stepsFor(undefined, false)} /> : null}
      </Card>
      {rows.map((item, index) => <ScoringCard key={item.id} item={item} current={index === 0} modelVersionId={modelVersionId} />)}
      <Card title="Past scorings" aside={<span className="muted">this browser session</span>}>
        <p className="muted">History needs a list read: the API can read one scoring by id but cannot yet list a model&apos;s scorings, so only the files scored in this session are kept above.</p>
      </Card>
    </>
  );
}
