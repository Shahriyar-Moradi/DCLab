"use client";

import { TrialResult } from "@/app/components/labs/TrialResult";
import { Button } from "@/app/components/ui/Button";
import { Card, Fact, FactGrid, Panel } from "@/app/components/ui/Card";
import { DataTable } from "@/app/components/ui/DataTable";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { StatusBadge } from "@/app/components/ui/StatusBadge";
import { useLabQuota, useLabRuns, useRunLabTrial, useSession } from "@/lib/application";
import { formatTimestamp, type ClientLabProblem, type ClientLabRun } from "@/lib/domain";
import { canWriteWorkspaceSession } from "@/lib/infrastructure/session";
import { useRef, useState, type KeyboardEvent } from "react";

export function ProblemWorkspace({ problems }: { problems: ClientLabProblem[] }) {
  const [selectedId, setSelectedId] = useState<string | null>(problems[0]?.use_case ?? null);
  const selected = problems.find((problem) => problem.use_case === selectedId) ?? null;

  if (problems.length === 0) {
    return (
      <Panel title="Problem trial" description="Bounded catalog trials for this business area.">
        <p className="text-body text-ink-muted">No bounded trial problems in this category.</p>
      </Panel>
    );
  }

  function onListKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const index = problems.findIndex((problem) => problem.use_case === selectedId);
    if (index < 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSelectedId(problems[Math.min(index + 1, problems.length - 1)].use_case);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSelectedId(problems[Math.max(index - 1, 0)].use_case);
    } else if (event.key === "Home") {
      event.preventDefault();
      setSelectedId(problems[0].use_case);
    } else if (event.key === "End") {
      event.preventDefault();
      setSelectedId(problems[problems.length - 1].use_case);
    }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-3">
      <Panel title="Use case" description="Fixed problems only — not open-ended configuration." className="lg:col-span-1">
        <div className="grid gap-1.5" role="listbox" aria-label="Lab problems" onKeyDown={onListKeyDown}>
          {problems.map((problem) => {
            const active = problem.use_case === selectedId;
            return (
              <button
                key={problem.use_case}
                type="button"
                role="option"
                aria-selected={active}
                tabIndex={active ? 0 : -1}
                onClick={() => setSelectedId(problem.use_case)}
                className={`rounded-md border px-3 py-2 text-left text-body transition-ui ${
                  active ? "border-navy bg-navy-soft text-ink" : "border-hairline bg-paper-raised text-ink hover:bg-navy-soft/40"
                }`}
              >
                <span className="break-words">{problem.question}</span>
              </button>
            );
          })}
        </div>
      </Panel>
      {selected ? <SelectedProblem problem={selected} /> : null}
    </div>
  );
}

function SelectedProblem({ problem }: { problem: ClientLabProblem }) {
  const quota = useLabQuota(problem.use_case);
  const previous = useLabRuns(problem.use_case);
  const runTrial = useRunLabTrial();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<ClientLabRun | null>(null);
  const { user, loaded } = useSession();
  const canWrite = loaded && user != null && canWriteWorkspaceSession(user.role);
  const remaining = quota.data?.runs_remaining;
  const exhausted = remaining !== undefined && remaining <= 0;
  const running = runTrial.isPending;
  const blocked = !canWrite || quota.isPending || quota.isError || exhausted || running;

  function runWithSample() {
    runTrial.mutate({ useCase: problem.use_case, file: null }, { onSuccess: setLastRun });
  }

  function runWithUpload() {
    const file = fileInputRef.current?.files?.[0];
    if (!file) return;
    runTrial.mutate({ useCase: problem.use_case, file }, { onSuccess: setLastRun });
  }

  const quotaValue = quota.isError
    ? "Could not load remaining trial runs"
    : quota.isPending
      ? "Checking remaining trial runs…"
      : exhausted
        ? "No trial runs left for this problem"
        : `${remaining} of ${problem.max_trial_runs} trial runs left`;

  return (
    <div className="grid min-w-0 gap-5 lg:col-span-2">
      <Panel title="Configuration" description={problem.sample_scenario}>
        <FactGrid className="xl:grid-cols-2">
          <Fact label="Quota" value={quotaValue} />
          <Fact label="Upload row cap" value={`up to ${problem.max_upload_rows.toLocaleString()} rows`} mono />
          <Fact label="Sample rows" value={problem.sample_row_count.toLocaleString()} mono />
          <Fact label="Required columns" value={problem.required_columns.join(", ")} mono />
        </FactGrid>
      </Panel>

      <Panel title="Run" description="Uses the same engine as admin simulations, bounded by quota.">
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={runWithSample} disabled={blocked}>
            {running ? "Running…" : "Run with sample data"}
          </Button>
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv"
            className="sr-only"
            disabled={!canWrite || exhausted || quota.isPending || quota.isError}
            aria-label="CSV file for this problem trial"
            onChange={(event) => setFileName(event.target.files?.[0]?.name ?? null)}
          />
          <Button
            variant="secondary"
            onClick={() => fileInputRef.current?.click()}
            disabled={!canWrite || exhausted || quota.isPending || quota.isError}
          >
            {fileName ?? "Choose CSV"}
          </Button>
          {fileName ? (
            <Button variant="secondary" onClick={runWithUpload} disabled={blocked}>
              Run with my file
            </Button>
          ) : null}
        </div>
        {runTrial.isError ? (
          <p className="mt-3 text-body text-oxblood" role="alert">
            {runTrial.error.message}
          </p>
        ) : null}
        {lastRun ? <TrialResult run={lastRun} /> : null}
      </Panel>

      <Panel title="Previous trial runs">
        {previous.isPending ? (
          <Skeleton className="h-32" />
        ) : previous.isError ? (
          <p className="text-body text-oxblood">Could not load previous trial runs.</p>
        ) : previous.data && previous.data.length > 0 ? (
          <DataTable
            columns={[
              {
                id: "when",
                header: "Run",
                mono: true,
                cell: (row) => formatTimestamp(row.created_at) || "—",
              },
              {
                id: "source",
                header: "Source",
                cell: (row) => (row.data_source === "sample" ? "Sample data" : "Uploaded file"),
              },
              {
                id: "rows",
                header: "Rows",
                mono: true,
                cell: (row) => row.row_count.toLocaleString(),
              },
              {
                id: "status",
                header: "Status",
                cell: (row) => <StatusBadge status={row.status} />,
              },
              {
                id: "open",
                header: "Result",
                cell: (row) => (
                  <button
                    type="button"
                    className="font-medium text-navy underline-offset-2 hover:underline"
                    onClick={() => setLastRun(row)}
                  >
                    Show
                  </button>
                ),
              },
            ]}
            rows={previous.data}
            rowKey={(row) => row.id}
          />
        ) : (
          <Card className="border-dashed px-6 py-8">
            <p className="text-body text-ink-muted">No trial runs for this problem yet.</p>
          </Card>
        )}
      </Panel>
    </div>
  );
}
