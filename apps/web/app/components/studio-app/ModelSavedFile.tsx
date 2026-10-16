"use client";

/**
 * Model → "Saved file & checks" (UI-D): the saved file (format, size, what it holds) and the checks DCLab stored when it built
 * it, from the model version read; the columns new data must have, from the column list read (names and types only).
 * Column names are the customer's own data: plain text only. Nothing here writes.
 */
import { useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { KeyValue } from "@/components/studio/KeyValue";
import { PageGuide } from "@/components/studio/PageGuide";
import { Pill } from "@/components/studio/Pill";
import { QueryNotice } from "@/app/components/studio-app/StudioParts";
import { useModelVersionRead } from "@/lib/application/studio-inspect-hooks";
import { useFeatureContract } from "@/lib/application/studio-model-detail-hooks";
import { RECHECKED, columnsView, contractError, fileChecksView, savedFileView, type ColumnsView } from "@/lib/application/studio-model-detail";
import { utcWhen } from "@/lib/application/studio-schedule";

const COLUMNS_SHOWN = 30;
type ColumnRow = ColumnsView["rows"][number];

function ColumnsCard({ modelVersionId }: { modelVersionId: string }) {
  const read = useFeatureContract(modelVersionId);
  const [all, setAll] = useState(false);
  if (read.isError) {
    return <Card title="Columns new data must have"><div className="empty" role="note"><p>{contractError(read.error)}</p></div></Card>;
  }
  if (!read.data) return <Card title="Columns new data must have"><p role="status">Loading the columns…</p></Card>;
  const view = columnsView(read.data);
  const rows = all ? view.rows : view.rows.slice(0, COLUMNS_SHOWN);
  const columns: Column<ColumnRow>[] = [
    { key: "name", header: "Column", render: (r) => <span className="mono">{r.name}</span> },
    { key: "kind", header: "Type", render: (r) => r.kind },
    { key: "required", header: "Needed?", render: (r) => r.required },
  ];
  return (
    <Card title="Columns new data must have" aside={<Pill tone="gray">{view.count} in total</Pill>}>
      <DataTable caption="Columns new data must have" columns={columns} rows={rows} rowKey={(r) => r.key} emptyMessage="No columns are recorded for this model." />
      {view.rows.length > COLUMNS_SHOWN ? (
        <p><button type="button" className="btn" aria-expanded={all} onClick={() => setAll((v) => !v)}>{all ? `Show the first ${COLUMNS_SHOWN} only` : `Show all ${view.rows.length} columns`}</button></p>
      ) : null}
      <ul className="plain-list tight v7-model-notes">{view.notes.map((n) => <li key={n}>{n}</li>)}</ul>
    </Card>
  );
}

export function ModelSavedFile({ projectId, modelVersionId }: { projectId: string; modelVersionId: string }) {
  const model = useModelVersionRead(modelVersionId);
  if (model.isError) return <QueryNotice error={model.error} what="model" />;
  if (!model.data) return <p role="status">Loading the model…</p>;
  if (model.data.project_id && model.data.project_id !== projectId) return <Banner tone="warn">This model belongs to another project.</Banner>;
  const file = savedFileView(model.data);
  const checks = fileChecksView(model.data.package_verification, utcWhen);
  return (
    <>
      <PageGuide
        purpose="See how this model is saved, what DCLab checked on the saved file, and which columns a new file must have to be scored."
        howTo="Read the saved file card, then the checks. Before you score a file, compare its columns with the table below."
        youGet="The file's format, size and contents, the checks with pass or fail, and the column names and types the model needs."
        attention="A file that lacks a needed column is not scored. Names must match exactly."
      />
      <div className="grid cols-2 v7-model-file">
        <Card title="Saved file" aside={file.state === "saved" ? <Pill tone={file.tone}>{file.format}</Pill> : <Pill tone="gray">none</Pill>}>
          {file.state === "saved" ? (
            <KeyValue items={[
              { key: "format", label: "Format", value: <><b>{file.format}.</b> {file.formatDetail}</> },
              { key: "size", label: "Size", value: file.size ?? "not recorded" },
              { key: "includes", label: "Includes", value: file.includes.length ? file.includes.join(", ") : "not recorded" },
            ]} />
          ) : (
            <div className="empty" role="note"><p>{file.text}</p></div>
          )}
        </Card>
        <Card title="Checks on the file" aside={checks.state === "verified" ? <Pill tone={checks.allPassed ? "ok" : "crit"}>{checks.allPassed ? "all passed" : "a check failed"}</Pill> : <Pill tone="gray">{checks.state === "not_recorded" ? "not recorded" : "does not apply"}</Pill>}>
          {checks.state === "verified" ? (
            <>
              <ul className="plain-list v7-model-checks" aria-label="Checks on the saved file">
                {checks.items.map((c) => <li key={c.key}><Pill tone={c.passed ? "ok" : "crit"}>{c.passed ? "pass" : "fail"}</Pill> {c.text}</li>)}
              </ul>
              {checks.when ? <p className="muted">Checked when the model was built, {checks.when}.</p> : null}
            </>
          ) : (
            <div className="empty" role="note"><p>{checks.text}</p></div>
          )}
          {checks.state !== "none" ? <p className="muted">{RECHECKED}</p> : null}
        </Card>
      </div>
      <ColumnsCard modelVersionId={modelVersionId} />
    </>
  );
}
