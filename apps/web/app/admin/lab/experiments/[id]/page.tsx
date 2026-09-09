"use client";

import { formatNumericMetrics, recordOf, stringifyUnknown } from "@/app/components/admin/format";
import { Fact, FactGrid, Panel } from "@/app/components/ui/Card";
import { DataTable } from "@/app/components/ui/DataTable";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { MetricCard } from "@/app/components/ui/MetricCard";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { SectionHeader } from "@/app/components/ui/SectionHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { StatusBadge, statusTone } from "@/app/components/ui/StatusBadge";
import { useLabCandidates, useLabComparison, useLabExperiment, useLabReport } from "@/lib/application";
import Link from "next/link";
import { useParams } from "next/navigation";

type CombinationRow = { groups: string[]; best_score: number };

export default function LabExperimentDetailPage() {
  const params = useParams<{ id: string }>();
  const experiment = useLabExperiment(params.id);
  const report = useLabReport(params.id);
  const candidates = useLabCandidates(params.id);
  const comparison = useLabComparison(params.id);
  if (experiment.isPending) return <Skeleton className="h-96" />;
  if (experiment.isError || !experiment.data) {
    return <ErrorState body="Experiment not found." onRetry={() => void experiment.refetch()} />;
  }
  const result = recordOf(experiment.data.result);
  const funnel = recordOf(result.funnel);
  const testMetrics = recordOf(result.test_metrics);
  const bestSingle = recordOf(result.best_single);
  const featureGroupScores = recordOf(result.feature_group_scores);
  const leakage = recordOf(result.leakage);
  const combinations = Array.isArray(result.combination_table)
    ? (result.combination_table as CombinationRow[]).filter(
        (row) => Array.isArray(row?.groups) && typeof row.best_score === "number",
      )
    : [];
  const fusion = typeof result.fusion === "string" ? result.fusion : "";
  const bestFamily = typeof bestSingle.model_family === "string" ? bestSingle.model_family : "";
  const leakageRisk = typeof leakage.risk === "string" ? leakage.risk : "";
  const comparisonRow = recordOf(comparison.data);
  const comparisonKnown = ["fusion", "test_metrics", "best_single", "baselines", "weights"] as const;
  const comparisonExtras = Object.entries(comparisonRow).filter(
    ([key, value]) => !comparisonKnown.includes(key as (typeof comparisonKnown)[number]) && value !== undefined,
  );

  return (
    <div>
      <PageHeader
        breadcrumbs={[
          { label: "Labs", href: "/admin/lab" },
          { label: "Experiments", href: "/admin/lab/experiments" },
          { label: experiment.data.task_name ?? experiment.data.use_case ?? experiment.data.id },
        ]}
        title={experiment.data.task_name ?? experiment.data.use_case ?? experiment.data.id}
        identifier={experiment.data.id}
        status={{ label: experiment.data.status, tone: statusTone(experiment.data.status) }}
        description={
          experiment.data.dataset_name
            ? `${experiment.data.dataset_name}${experiment.data.use_case ? ` · ${experiment.data.use_case}` : ""}`
            : undefined
        }
      />
      <Panel className="mt-8">
        <FactGrid>
          <Fact label="Status" value={experiment.data.status} />
          {experiment.data.dataset_name ? (
            <Fact
              label="Dataset"
              value={experiment.data.dataset_name}
            />
          ) : null}
          {experiment.data.task_slug ? <Fact label="Task" value={experiment.data.task_slug} mono /> : null}
          <Fact label="Seed" value={String(experiment.data.seed)} mono />
          {experiment.data.git_commit ? <Fact label="Git commit" value={experiment.data.git_commit} mono /> : null}
          {experiment.data.artifact_dir ? <Fact label="Artifacts" value={experiment.data.artifact_dir} mono /> : null}
        </FactGrid>
        {experiment.data.dataset_id ? (
          <p className="mt-4">
            <Link className="text-navy hover:underline" href={`/admin/lab/datasets/${experiment.data.dataset_id}`}>
              Open dataset
            </Link>
          </p>
        ) : null}
      </Panel>

      {fusion || bestFamily || leakageRisk ? (
        <div className="mt-8 grid gap-4 md:grid-cols-3">
          {fusion ? <MetricCard label="Fusion" value={fusion} /> : null}
          {bestFamily ? <MetricCard label="Best family" value={bestFamily} /> : null}
          {leakageRisk ? <MetricCard label="Leakage" value={leakageRisk} /> : null}
        </div>
      ) : null}

      {Object.keys(funnel).length > 0 ? (
        <div className="mt-10">
          <SectionHeader title="Funnel" />
          <div className="mt-4">
            <KeyValueTable rows={funnel} />
          </div>
        </div>
      ) : null}

      <div className="mt-10">
        <SectionHeader title="Candidates" />
        <div className="mt-4">
          <DataTable
            columns={[
              { id: "family", header: "Family", cell: (row) => row.model_family ?? "—" },
              {
                id: "groups",
                header: "Feature groups",
                mono: true,
                cell: (row) => (row.feature_groups ?? []).join("+") || "—",
              },
              {
                id: "status",
                header: "Status",
                cell: (row) => (row.status ? <StatusBadge status={row.status} /> : "—"),
              },
              {
                id: "score",
                header: "Score",
                mono: true,
                cell: (row) => (typeof row.score === "number" ? row.score.toFixed(4) : "—"),
              },
              { id: "id", header: "Candidate", mono: true, cell: (row) => row.candidate_id ?? "—" },
            ]}
            rows={candidates.data ?? []}
            rowKey={(row) =>
              row.candidate_id ?? `${row.model_family ?? "candidate"}-${(row.feature_groups ?? []).join("+")}`
            }
            emptyTitle="No candidates"
            emptyBody="Candidate rows appear after this experiment records model searches."
          />
        </div>
      </div>

      {Object.keys(comparisonRow).length > 0 ? (
        <div className="mt-10">
          <SectionHeader title="Comparison" />
          <Panel className="mt-4">
            <FactGrid>
              {typeof comparisonRow.fusion === "string" ? (
                <Fact label="Fusion" value={comparisonRow.fusion} mono />
              ) : null}
              {formatNumericMetrics(recordOf(comparisonRow.test_metrics)) ? (
                <Fact label="Test metrics" value={formatNumericMetrics(recordOf(comparisonRow.test_metrics))} mono />
              ) : null}
              {stringifyUnknown(comparisonRow.best_single) ? (
                <Fact label="Best single" value={stringifyUnknown(comparisonRow.best_single)} mono />
              ) : null}
              {stringifyUnknown(comparisonRow.baselines) ? (
                <Fact label="Baselines" value={stringifyUnknown(comparisonRow.baselines)} mono />
              ) : null}
              {stringifyUnknown(comparisonRow.weights) ? (
                <Fact label="Weights" value={stringifyUnknown(comparisonRow.weights)} mono />
              ) : null}
            </FactGrid>
            {comparisonExtras.length > 0 ? (
              <pre className="mt-4 max-h-[40vh] overflow-auto font-mono text-data text-ink">
                {JSON.stringify(Object.fromEntries(comparisonExtras), null, 2)}
              </pre>
            ) : null}
          </Panel>
        </div>
      ) : null}

      {Object.keys(testMetrics).length > 0 ? (
        <div className="mt-10">
          <SectionHeader title="Test metrics" />
          <div className="mt-4">
            <KeyValueTable rows={testMetrics} />
          </div>
        </div>
      ) : null}

      {Object.keys(featureGroupScores).length > 0 ? (
        <div className="mt-10">
          <SectionHeader title="Feature groups" />
          <div className="mt-4">
            <DataTable
              columns={[
                { id: "name", header: "Group", cell: ([name]) => name },
                {
                  id: "score",
                  header: "Score",
                  mono: true,
                  cell: ([, score]) => (typeof score === "number" ? score.toFixed(4) : stringifyUnknown(score) || "—"),
                },
              ]}
              rows={Object.entries(featureGroupScores)}
              rowKey={([name]) => name}
            />
          </div>
        </div>
      ) : null}

      {combinations.length > 0 ? (
        <div className="mt-10">
          <SectionHeader title="Combinations" />
          <div className="mt-4">
            <DataTable
              columns={[
                { id: "groups", header: "Groups", mono: true, cell: (row) => row.groups.join(" + ") },
                { id: "score", header: "Best score", mono: true, cell: (row) => row.best_score.toFixed(4) },
              ]}
              rows={combinations}
              rowKey={(row) => row.groups.join("+")}
            />
          </div>
        </div>
      ) : null}

      {fusion ? (
        <div className="mt-10">
          <SectionHeader title="Ensemble" />
          <p className="mt-3 text-body text-ink">
            Fusion <span className="font-mono text-data">{fusion}</span>
            {bestFamily ? ` · best single ${bestFamily}` : ""}
          </p>
        </div>
      ) : null}

      <div className="mt-10">
        <SectionHeader title="Report" />
        <pre className="mt-3 overflow-auto whitespace-pre-wrap rounded-xl border border-hairline bg-paper-raised p-4 text-body text-ink">
          {report.data?.markdown ?? "No report yet."}
        </pre>
      </div>
    </div>
  );
}

function KeyValueTable({ rows }: { rows: Record<string, unknown> }) {
  return (
    <DataTable
      columns={[
        { id: "key", header: "Field", cell: ([key]) => key },
        { id: "value", header: "Value", mono: true, cell: ([, value]) => stringifyUnknown(value) || "—" },
      ]}
      rows={Object.entries(rows)}
      rowKey={([key]) => key}
    />
  );
}
