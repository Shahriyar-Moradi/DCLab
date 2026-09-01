"use client";

import { InsightCard } from "@/app/components/insights/InsightCard";
import { CATEGORY_META, CATEGORY_ORDER } from "@/app/components/insights/categoryMeta";
import { Button } from "@/app/components/ui/Button";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useLabProblems, useLabQuota, useLabUploads, useRunLabTrial, useUploadLabFile } from "@/lib/application";
import {
  type ClientLabProblem,
  type ClientLabRun,
  type InsightCategoryValue,
} from "@/lib/domain";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

const OPEN_FILE_ACCEPT = [
  ".csv",
  ".tsv",
  ".tab",
  ".json",
  ".jsonl",
  ".ndjson",
  ".parquet",
  ".pq",
  ".xlsx",
  ".xls",
  ".txt",
  ".log",
  "text/csv",
  "text/plain",
  "application/json",
].join(",");

const KIND_LABELS: Record<string, string> = {
  spreadsheet: "spreadsheet",
  json: "JSON",
  table_file: "table file",
  plain_text: "plain text",
};

function parseDelimitedHeader(text: string, delimiter: string): string[] {
  const fields: string[] = [];
  let field = "";
  let quoted = false;

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (character === '"') {
      if (quoted && text[index + 1] === '"') {
        field += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (character === delimiter && !quoted) {
      fields.push(field.trim());
      field = "";
    } else if ((character === "\n" || character === "\r") && !quoted) {
      break;
    } else {
      field += character;
    }
  }
  fields.push(field.trim());
  return Array.from(new Set(fields.map((value) => value.replace(/^\uFEFF/, "")).filter(Boolean)));
}

async function targetOptionsFor(file: File): Promise<string[]> {
  const suffix = file.name.split(".").pop()?.toLowerCase();
  if (!suffix || !["csv", "tsv", "tab"].includes(suffix)) return [];
  const text = await file.slice(0, 64 * 1024).text();
  return parseDelimitedHeader(text, suffix === "csv" ? "," : "\t");
}

export default function ClientLabsPage() {
  const problems = useLabProblems();

  if (problems.isPending) {
    return (
      <div>
        <LabsHero />
        <div className="mx-auto max-w-7xl px-5 py-12 lg:px-8">
          <div className="grid gap-4 md:grid-cols-2">
            <Skeleton className="h-48" />
            <Skeleton className="h-48" />
          </div>
        </div>
      </div>
    );
  }

  if (problems.isError) {
    return (
      <div>
        <LabsHero />
        <div className="mx-auto max-w-3xl px-5 py-12">
          <ErrorState
            body="Could not load the trial problems from the backend. Check that the API is running."
            onRetry={() => void problems.refetch()}
          />
        </div>
      </div>
    );
  }

  const byCategory = new Map<InsightCategoryValue, ClientLabProblem[]>();
  for (const problem of problems.data ?? []) {
    const list = byCategory.get(problem.category) ?? [];
    list.push(problem);
    byCategory.set(problem.category, list);
  }

  return (
    <div>
      <LabsHero />
      <div className="mx-auto max-w-7xl px-5 pb-16 lg:px-8">
        <div className="space-y-12">
          {CATEGORY_ORDER.map((category) => {
            const items = byCategory.get(category) ?? [];
            const meta = CATEGORY_META[category];
            const Icon = meta.icon;
            return (
              <section key={category}>
                <div className="flex items-center gap-3">
                  <span className="flex h-9 w-9 items-center justify-center rounded-full bg-navy-soft/60 text-navy">
                    <Icon size={18} />
                  </span>
                  <div>
                    <h2 className="font-display text-section text-ink">{category}</h2>
                    <p className="font-body text-body text-ink-muted">{meta.blurb}</p>
                  </div>
                </div>
                <OpenFileCard category={category} />
                {items.length > 0 ? (
                  <div className="mt-4 grid gap-4 md:grid-cols-2">
                    {items.map((problem) => (
                      <LabProblemCard key={problem.use_case} problem={problem} />
                    ))}
                  </div>
                ) : null}
              </section>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function LabsHero() {
  return (
    <section className="bg-midnight px-5 pb-10 pt-16 text-center lg:px-8 lg:pt-20">
      <p className="text-eyebrow uppercase text-cyan">Labs</p>
      <h1 className="mt-4 text-4xl font-bold text-white lg:text-5xl">Try DCLab Before You Commit.</h1>
      <p className="mx-auto mt-3 max-w-2xl text-white/65">
        At the top of each category, drop any usual data file — no particular field names required. Below that, pick a
        business problem and run it on sample data or a matching file. Trials stay bounded so they never turn into a
        bill.
      </p>
    </section>
  );
}

function runPath(runId: string): string {
  return `/lab/runs/${runId}`;
}

function OpenFileCard({ category }: { category: InsightCategoryValue }) {
  const uploads = useLabUploads(category);
  const saveFile = useUploadLabFile();
  const router = useRouter();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [targetColumn, setTargetColumn] = useState("");
  const [targetOptions, setTargetOptions] = useState<string[]>([]);

  async function onFileChange(file: File | undefined) {
    setFileName(file?.name ?? null);
    setTargetColumn("");
    setTargetOptions([]);
    if (!file) return;
    const options = await targetOptionsFor(file);
    if (fileInputRef.current?.files?.[0] === file) setTargetOptions(options);
  }

  function onPick() {
    const file = fileInputRef.current?.files?.[0];
    if (!file) return;
    saveFile.mutate(
      { category, file, targetColumn: targetColumn || undefined },
      {
        onSuccess: (row) => {
          router.push(runPath(row.run_id));
        },
      },
    );
  }

  const recent = uploads.data ?? [];

  return (
    <article className="mt-4 rounded bg-paper-raised p-6">
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Your file · any usual format</p>
      <h3 className="mt-2 font-display text-lg text-ink">No template required</h3>
      <p className="mt-2 font-body text-body text-ink-muted">
        Spreadsheet, JSON, table file, Excel, or a raw log — even with no field names. We save it as-is for {category}.
        Reading messy files into a usable table is coming next. To run a trial on a problem below, use that card&apos;s
        sample data or a matching CSV.
      </p>
      <p className="mt-3 font-mono text-data text-ink-muted">up to 500 rows · 2 MB</p>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <input
          ref={fileInputRef}
          type="file"
          accept={OPEN_FILE_ACCEPT}
          className="hidden"
          onChange={(event) => void onFileChange(event.target.files?.[0])}
        />
        <Button variant="secondary" onClick={() => fileInputRef.current?.click()} disabled={saveFile.isPending}>
          {fileName ?? "Choose a file"}
        </Button>
        {fileName ? (
          <Button onClick={onPick} disabled={saveFile.isPending}>
            {saveFile.isPending ? "Saving…" : "Save file"}
          </Button>
        ) : null}
      </div>

      {fileName ? (
        <label className="mt-4 block max-w-md font-body text-body text-ink">
          Outcome column to predict <span className="text-ink-muted">(optional)</span>
          {targetOptions.length > 0 ? (
            <select
              className="mt-2 block w-full rounded border border-hairline bg-paper-raised px-3 py-2"
              value={targetColumn}
              onChange={(event) => setTargetColumn(event.target.value)}
            >
              <option value="">Let DCLab choose</option>
              {targetOptions.map((column) => (
                <option key={column} value={column}>
                  {column}
                </option>
              ))}
            </select>
          ) : (
            <input
              className="mt-2 block w-full rounded border border-hairline bg-paper-raised px-3 py-2"
              type="text"
              value={targetColumn}
              onChange={(event) => setTargetColumn(event.target.value)}
              placeholder="Exact column name"
            />
          )}
          <span className="mt-2 block text-ink-muted">
            Choose this when your file has several possible labels, such as multiple Yes/No columns.
          </span>
        </label>
      ) : null}

      {saveFile.isError ? <p className="mt-3 font-body text-body text-oxblood">{saveFile.error.message}</p> : null}
      {uploads.isError ? (
        <p className="mt-3 font-body text-body text-oxblood">Could not load saved files for this category.</p>
      ) : null}

      {recent.length > 0 ? (
        <ul className="mt-4 space-y-1">
          {recent.slice(0, 3).map((row) => (
            <li key={row.id}>
              <Link
                className="font-mono text-data text-navy underline-offset-2 hover:underline"
                href={runPath(row.run_id)}
              >
                {row.filename}
                {" · "}
                {KIND_LABELS[row.kind] ?? row.kind}
                {row.record_count > 0 ? ` · ${row.record_count.toLocaleString()} rows` : null}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  );
}

function LabProblemCard({ problem }: { problem: ClientLabProblem }) {
  const quota = useLabQuota(problem.use_case);
  const runTrial = useRunLabTrial();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<ClientLabRun | null>(null);

  const remaining = quota.data?.runs_remaining ?? problem.max_trial_runs;
  const exhausted = remaining <= 0;

  function runWithSample() {
    runTrial.mutate({ useCase: problem.use_case, file: null }, { onSuccess: setLastRun });
  }

  function runWithUpload() {
    const file = fileInputRef.current?.files?.[0];
    if (!file) return;
    runTrial.mutate({ useCase: problem.use_case, file }, { onSuccess: setLastRun });
  }

  return (
    <article className="rounded bg-paper-raised p-6">
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">
        Sample data: {problem.sample_scenario}
      </p>
      <h3 className="mt-2 font-display text-lg text-ink">{problem.question}</h3>
      <p className="mt-3 font-mono text-data text-ink-muted">
        {exhausted ? "No trial runs left for this problem" : `${remaining} of ${problem.max_trial_runs} trial runs left`}
        {" · "}up to {problem.max_upload_rows.toLocaleString()} rows per upload
      </p>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <Button onClick={runWithSample} disabled={exhausted || runTrial.isPending}>
          {runTrial.isPending ? "Running…" : "Run with sample data"}
        </Button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv"
          className="hidden"
          onChange={(event) => setFileName(event.target.files?.[0]?.name ?? null)}
        />
        <Button variant="secondary" onClick={() => fileInputRef.current?.click()} disabled={exhausted}>
          {fileName ?? "Choose CSV"}
        </Button>
        {fileName ? (
          <Button variant="secondary" onClick={runWithUpload} disabled={exhausted || runTrial.isPending}>
            Run with my file
          </Button>
        ) : null}
      </div>

      {runTrial.isError ? <p className="mt-3 font-body text-body text-oxblood">{runTrial.error.message}</p> : null}

      {lastRun ? <LabRunResult run={lastRun} /> : null}
    </article>
  );
}

function LabRunResult({ run }: { run: ClientLabRun }) {
  if (run.status === "failed") {
    return (
      <div className="mt-5 rounded bg-navy-soft/40 p-4">
        <p className="font-body text-body text-ink">{run.failure_reason ?? "This run could not be completed."}</p>
      </div>
    );
  }
  return (
    <div className="mt-5 space-y-3">
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">
        Results from {run.data_source === "sample" ? "sample data" : "your file"} · {run.row_count.toLocaleString()}{" "}
        rows
      </p>
      <div className="grid gap-3">
        {run.insights.map((insight) => (
          <InsightCard key={insight.subject_id} insight={insight} />
        ))}
      </div>
    </div>
  );
}
