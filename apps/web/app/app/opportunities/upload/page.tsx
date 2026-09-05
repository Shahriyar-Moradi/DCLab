"use client";

import { Button } from "@/app/components/ui/Button";
import { DataTable } from "@/app/components/ui/DataTable";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { Panel } from "@/app/components/ui/Card";
import { UploadZone } from "@/app/components/ui/UploadZone";
import { useUploadOpportunities } from "@/lib/application";
import Link from "next/link";
import { useState } from "react";

export default function UploadPage() {
  const upload = useUploadOpportunities();
  const [progress, setProgress] = useState(0);

  function send(file: File) {
    setProgress(0);
    upload.mutate({ file, onProgress: setProgress });
  }

  return (
    <div>
      <PageHeader
        eyebrow="Workspace"
        title="Upload opportunities"
        description="CSV with at least external_id, customer_id, amount, currency, stage, source, owner_id."
        breadcrumbs={[
          { label: "Opportunities", href: "/app/opportunities" },
          { label: "Upload" },
        ]}
      />

      <UploadZone
        accept=".csv,text/csv"
        disabled={upload.isPending}
        label="Drop a CSV here, or click to choose a file"
        hint="One row per opportunity. Invalid rows are rejected without blocking the rest of the file."
        error={upload.isError ? upload.error.message : undefined}
        onFiles={(files) => {
          const file = files[0];
          if (file) send(file);
        }}
      />

      {upload.isPending ? (
        <div className="mt-4" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress} aria-label="Upload progress">
          <p className="font-mono text-data text-ink">Uploading {progress}%</p>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-navy-soft">
            <div className="h-full rounded-full bg-navy" style={{ width: `${Math.min(100, Math.max(0, progress))}%` }} />
          </div>
        </div>
      ) : null}

      {upload.data ? (
        <Panel className="mt-6" title="Result" description="Inserted rows are available on the opportunities list.">
          <p className="font-mono text-title text-ink">{upload.data.inserted} inserted</p>
          <p className="mt-1 font-mono text-data text-ink-muted">{upload.data.rejected} rejected</p>
          {upload.data.errors.length ? (
            <div className="mt-4">
              <DataTable
                columns={[
                  { id: "row", header: "Row", mono: true, cell: (item) => String(item.row) },
                  { id: "reason", header: "Reason", cell: (item) => item.reason },
                ]}
                rows={upload.data.errors}
                rowKey={(item) => `${item.row}-${item.reason}`}
              />
            </div>
          ) : null}
          <Link className="mt-6 inline-block font-medium text-navy underline-offset-2 hover:underline" href="/app/opportunities">
            Open opportunities
          </Link>
        </Panel>
      ) : null}

      <Button className="mt-6" variant="secondary" disabled={upload.isPending} onClick={() => upload.reset()}>
        Clear result
      </Button>
    </div>
  );
}
