"use client";

import { Button } from "@/app/components/ui/Button";
import { useUploadOpportunities } from "@/lib/application";
import Link from "next/link";
import { useState } from "react";

export default function UploadPage() {
  const upload = useUploadOpportunities();
  const [progress, setProgress] = useState(0);
  const [drag, setDrag] = useState(false);

  function send(file: File) {
    setProgress(0);
    upload.mutate({ file, onProgress: setProgress });
  }

  return (
    <div className="max-w-xl">
      <h1 className="font-display text-title text-ink">Upload opportunities</h1>
      <p className="mt-2 font-body text-body text-ink-muted">
        CSV with at least external_id, customer_id, amount, currency, stage, source, owner_id.
      </p>
      <label
        className={`mt-8 block cursor-pointer rounded border border-hairline bg-paper-raised px-8 py-16 text-center ${drag ? "bg-navy-soft" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDrag(false);
          const file = event.dataTransfer.files[0];
          if (file) send(file);
        }}
      >
        <input
          type="file"
          accept=".csv,text/csv"
          className="sr-only"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) send(file);
          }}
        />
        <span className="font-body text-body text-ink">Drop a CSV here, or click to choose a file</span>
      </label>
      {upload.isPending ? (
        <p className="mt-4 font-mono text-data text-ink">Uploading {progress}%</p>
      ) : null}
      {upload.isError ? <p className="mt-4 font-body text-body text-oxblood">{upload.error.message}</p> : null}
      {upload.data ? (
        <div className="mt-8 rounded bg-paper-raised p-6">
          <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Result</p>
          <p className="mt-2 font-mono text-title text-ink">{upload.data.inserted} inserted</p>
          <p className="mt-1 font-mono text-data text-ink-muted">{upload.data.rejected} rejected</p>
          {upload.data.errors.length ? (
            <ul className="mt-4">
              {upload.data.errors.map((item) => (
                <li key={`${item.row}-${item.reason}`} className="border-t border-hairline py-2 font-mono text-data text-ink">
                  row {item.row}: {item.reason}
                </li>
              ))}
            </ul>
          ) : null}
          <Link className="mt-6 inline-block font-body text-body text-navy underline-offset-2 hover:underline" href="/opportunities">
            Open opportunities
          </Link>
        </div>
      ) : null}
      <Button
        className="mt-6"
        variant="secondary"
        disabled={upload.isPending}
        onClick={() => upload.reset()}
      >
        Clear result
      </Button>
    </div>
  );
}
