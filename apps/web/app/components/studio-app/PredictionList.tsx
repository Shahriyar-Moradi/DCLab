"use client";

/**
 * Predictions → "This week's list" (UI-D): the rows of one finished scoring, 50 at a time, highest score first, with the top
 * reasons the file holds for each row (what the model used, not causes). Keys, classes and reasons are the customer's own data:
 * plain text only, never links. Refusals are fixed sentences by API code. Nothing here writes.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type ReactNode } from "react";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { Pill } from "@/components/studio/Pill";
import { Term } from "@/components/studio/Term";
import { LIST_SOURCE, downloadError, listColumns, listError, listHeading, listNotes, listRows } from "@/lib/application/studio-prediction-list";
import { predictionRowsKey, usePredictionRows } from "@/lib/application/studio-prediction-list-hooks";
import { downloadPredictions, type StudioPrediction } from "@/lib/application/studio-scoring-hooks";
import { safeFilename } from "@/lib/application/studio-inspect";

const REASONS_TERM = "Each reason names an input of this row that moved the model's score up or down compared with a typical row. It says what the model used, not what caused anything in the world.";

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function PredictionList({ prediction, title, before }: { prediction: StudioPrediction; title: string; before?: ReactNode }) {
  const [order, setOrder] = useState<"score" | "row">("score");
  const read = usePredictionRows(prediction.id, order);
  const client = useQueryClient();
  // The page cursor is no longer valid: drop the loaded pages and read the first page again.
  const restart = () => void client.resetQueries({ queryKey: predictionRowsKey(prediction.id, order) });
  const more = useRef(false);
  const downloading = useRef(false);
  const [downloadFailed, setDownloadFailed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function download() {
    if (downloading.current) return;
    downloading.current = true;
    setBusy(true);
    setDownloadFailed(null);
    try {
      const file = await downloadPredictions(prediction);
      saveBlob(file.blob, safeFilename(file.filename, `predictions-${prediction.id.slice(0, 8)}.csv`));
    } catch (error) {
      setDownloadFailed(downloadError(error));
    } finally {
      downloading.current = false;
      setBusy(false);
    }
  }

  async function next() {
    if (more.current || read.isFetchingNextPage) return;
    more.current = true;
    try {
      await read.fetchNextPage();
    } finally {
      more.current = false;
    }
  }

  const pages = read.data?.pages ?? [];
  const first = pages[0];
  const items = pages.flatMap((p) => p.items);
  const rows = listRows(items);
  const columns = first ? listColumns(first, items) : null;
  const firstError = read.isError && !first ? listError(read.error) : null;
  const moreError = read.isFetchNextPageError ? listError(read.error) : null;

  return (
    <Card title={listHeading(prediction.completed_at, Date.now())}>
      {before}
      <p className="muted v7-pred-source">{title} {LIST_SOURCE}</p>
      <div className="toolbar v7-pred-toolbar">
        <label className="field"><span>Order</span>
          <select value={order} onChange={(event) => setOrder(event.target.value === "row" ? "row" : "score")}>
            <option value="score">Highest score first</option>
            <option value="row">File order</option>
          </select>
        </label>
        <button type="button" className="btn" disabled={busy || !prediction.output} onClick={() => void download()}>{busy ? "Downloading…" : "Download the full file (CSV)"}</button>
      </div>
      {downloadFailed ? <Banner tone="crit">{downloadFailed}</Banner> : null}
      {firstError ? (
        <div className="empty" role="alert">
          <p>{firstError.text}</p>
          {firstError.retry ? <p><button type="button" className="btn" onClick={() => void read.refetch()}>Try again</button></p> : null}
          {firstError.restart ? <p><button type="button" className="btn" onClick={restart}>Start again from the top</button></p> : null}
        </div>
      ) : read.isPending ? (
        <p role="status">Loading the list…</p>
      ) : first && columns ? (
        rows.length === 0 ? (
          <div className="empty" role="note"><p>This scoring&apos;s file has no rows.</p></div>
        ) : (
          <>
            <div className="tbl card v7-pred-list">
              <table>
                <caption className="sr-only">Scored rows of this list</caption>
                <thead>
                  <tr>
                    <th scope="col" className="r">Row</th>
                    {columns.entity ? <th scope="col">{columns.entity}</th> : null}
                    {columns.score ? <th scope="col" className="r">Score</th> : null}
                    {columns.flagged ? <th scope="col">Flagged?</th> : null}
                    {columns.label ? <th scope="col">Predicted class</th> : null}
                    {columns.prediction ? <th scope="col" className="r">Predicted value</th> : null}
                    {columns.reasons ? <th scope="col"><Term definition={REASONS_TERM}>Top reasons</Term> (what the model used, not causes)</th> : null}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.key}>
                      <td className="r">{r.row}</td>
                      {columns.entity ? <td className="mono">{r.entity}</td> : null}
                      {columns.score ? <td className="r">{r.score}</td> : null}
                      {columns.flagged ? <td>{r.flagged === "yes" ? <Pill tone="warn">yes</Pill> : r.flagged}</td> : null}
                      {columns.label ? <td>{r.answer}</td> : null}
                      {columns.prediction ? <td className="r">{r.answer}</td> : null}
                      {columns.reasons ? <td className="v7-pred-reasons">{r.reasons.length ? <ol>{r.reasons.map((x, i) => <li key={i}>{x}</li>)}</ol> : <span className="muted">none in the file</span>}</td> : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {read.hasNextPage ? (
              <p><button type="button" className="btn" disabled={read.isFetchingNextPage} onClick={() => void next()}>{read.isFetchingNextPage ? "Loading more rows…" : "Show the next 50 rows"}</button></p>
            ) : null}
            {moreError ? <Banner tone="warn">{moreError.text}{moreError.restart ? <> <button type="button" className="btn" onClick={restart}>Start again from the top</button></> : null}</Banner> : null}
            <ul className="plain-list tight v7-pred-notes" aria-label="About this list">{listNotes(first, rows.length).map((n) => <li key={n}>{n}</li>)}</ul>
          </>
        )
      ) : null}
    </Card>
  );
}
