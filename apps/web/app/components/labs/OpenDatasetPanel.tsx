"use client";

import { KIND_LABELS, LAB_RUN_STATUS_LABEL, OPEN_FILE_ACCEPT, runPath, targetOptionsFor } from "@/app/components/labs/status";
import { Button } from "@/app/components/ui/Button";
import { CollectionSearch } from "@/app/components/ui/CollectionSearch";
import { DataTable } from "@/app/components/ui/DataTable";
import { Panel } from "@/app/components/ui/Card";
import { Select } from "@/app/components/ui/Select";
import { StatusBadge } from "@/app/components/ui/StatusBadge";
import { UploadZone } from "@/app/components/ui/UploadZone";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { filterByText } from "@/app/components/ui/localCollection";
import { useLabUploads, useSession, useUploadLabFile } from "@/lib/application";
import { formatTimestamp, type InsightCategoryValue, type LabRunStatus } from "@/lib/domain";
import { canWriteWorkspaceSession, isPlatformRole } from "@/lib/infrastructure/session";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

function isLabRunStatus(value: string): value is LabRunStatus {
  return value === "queued" || value === "processing" || value === "completed" || value === "failed";
}

export function OpenDatasetPanel({ category }: { category: InsightCategoryValue }) {
  const uploads = useLabUploads(category);
  const saveFile = useUploadLabFile();
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [targetColumn, setTargetColumn] = useState("");
  const [targetOptions, setTargetOptions] = useState<string[]>([]);
  const [listQuery, setListQuery] = useState("");
  const { user, loaded } = useSession();
  const isPlatformMember = user ? isPlatformRole(user.role) : false;
  const canWrite = loaded && user != null && canWriteWorkspaceSession(user.role);
  const recent = uploads.data ?? [];
  const visibleRecent = filterByText(recent, listQuery, (row) => [row.filename, row.kind, row.status]);
  const uploading = saveFile.isPending;
  const selected = Boolean(file);

  async function onFileChange(next: File | undefined) {
    setFile(next ?? null);
    setTargetColumn("");
    setTargetOptions([]);
    if (!next) return;
    setTargetOptions(await targetOptionsFor(next));
  }

  function onSave() {
    if (!file) return;
    saveFile.mutate(
      { category, file, targetColumn: targetColumn || undefined },
      {
        onSuccess: (row) => {
          router.push(isPlatformMember ? `/admin/models/client-uploads/${row.id}` : runPath(row.run_id));
        },
      },
    );
  }

  return (
    <Panel
      title="Dataset input"
      description="No template required. Spreadsheet, JSON, table file, Excel, or a raw log — saved as-is for this category."
    >
      <p className="font-mono text-data text-ink-muted">No DCLab row, column, or file-size cap</p>
      <p className="mt-2 text-body text-ink-muted">
        Reading messy files into a usable table is coming next. Bounded problem trials below still need matching columns
        or sample data.
      </p>

      <div className="mt-5">
        <UploadZone
          accept={OPEN_FILE_ACCEPT}
          disabled={!canWrite || uploading}
          label={file ? file.name : "Drop a file here, or click to choose"}
          hint={selected ? "Optional: name the outcome column, then save." : undefined}
          error={saveFile.isError ? saveFile.error.message : undefined}
          onFiles={(files) => void onFileChange(files[0])}
        />
      </div>

      {selected ? (
        <div className="mt-4 max-w-md">
          <p className="mb-1.5 font-sans text-body font-medium text-ink">
            Outcome column to predict <span className="text-ink-muted">(optional)</span>
          </p>
          {targetOptions.length > 0 ? (
            <Select aria-label="Outcome column to predict" value={targetColumn} onChange={(event) => setTargetColumn(event.target.value)} disabled={!canWrite || uploading}>
              <option value="">Let DCLab choose</option>
              {targetOptions.map((column) => (
                <option key={column} value={column}>
                  {column}
                </option>
              ))}
            </Select>
          ) : (
            <input
              className="h-10 w-full rounded-md border border-hairline bg-paper-raised px-3 text-body text-ink shadow-xs"
              type="text"
              value={targetColumn}
              disabled={!canWrite || uploading}
              onChange={(event) => setTargetColumn(event.target.value)}
              placeholder="Exact column name"
              aria-label="Outcome column to predict"
            />
          )}
          <p className="mt-1.5 text-helper text-ink-muted">
            Choose this when your file has several possible labels, such as multiple Yes/No columns.
          </p>
        </div>
      ) : null}

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <Button onClick={onSave} disabled={!canWrite || !selected || uploading}>
          {uploading ? "Saving…" : "Save file"}
        </Button>
      </div>
      {!canWrite ? (
        <p className="mt-3 text-body text-ink-muted">
          Read-only access. Uploading and running analyses require an administrator.
        </p>
      ) : null}
      {uploads.isError ? (
        <p className="mt-3 text-body text-oxblood">Could not load saved files for this category.</p>
      ) : null}

      {uploads.isPending ? (
        <Skeleton className="mt-6 h-32" />
      ) : uploads.isError ? null : recent.length > 0 ? (
        <div className="mt-6">
          <h3 className="font-sans text-section text-ink">Previous dataset runs</h3>
          <div className="mt-3">
            <CollectionSearch value={listQuery} onChange={setListQuery} shown={visibleRecent.length} total={recent.length} />
            <DataTable
              columns={[
                {
                  id: "file",
                  header: "File",
                  cell: (row) => (
                    <Link
                      className="break-all font-medium text-navy underline-offset-2 hover:underline"
                      href={isPlatformMember ? `/admin/models/client-uploads/${row.id}` : runPath(row.run_id)}
                    >
                      {row.filename}
                    </Link>
                  ),
                },
                {
                  id: "kind",
                  header: "Kind",
                  cell: (row) => KIND_LABELS[row.kind] ?? row.kind,
                },
                {
                  id: "rows",
                  header: "Rows",
                  mono: true,
                  cell: (row) => (row.record_count > 0 ? row.record_count.toLocaleString() : "—"),
                },
                {
                  id: "status",
                  header: "Status",
                  cell: (row) => (
                    <StatusBadge
                      status={isLabRunStatus(row.status) ? LAB_RUN_STATUS_LABEL[row.status] : row.status}
                    />
                  ),
                },
                {
                  id: "created",
                  header: "Saved",
                  mono: true,
                  cell: (row) => formatTimestamp(row.created_at) || "—",
                },
              ]}
              rows={visibleRecent}
              rowKey={(row) => row.id}
              emptyTitle="No matching files"
              emptyBody="Nothing on this list matches that filter."
            />
          </div>
        </div>
      ) : (
        <p className="mt-6 text-body text-ink-muted">No saved files in this category yet.</p>
      )}
    </Panel>
  );
}
