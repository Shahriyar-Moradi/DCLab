"use client";

import { Badge } from "@/app/components/ui/Badge";
import { Fact, FactGrid } from "@/app/components/ui/Card";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { formatWhen } from "@/app/components/admin/format";
import type { ModelBuildStage, PipelineModelBuild } from "@/lib/domain";
import { cn } from "@/lib/cn";
import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  asList,
  asNumber,
  asNumberRecord,
  asRecord,
  asString,
  asStringRecord,
  formatMetric,
  percentLabel,
  stageByKey,
} from "./format";

const CHART_COLORS = ["var(--color-navy)", "var(--color-cyan)", "var(--color-green)", "var(--color-amber)"];

function nonempty(value: string | null | undefined): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

function StageFacts({
  stage,
  extras = [],
}: {
  stage: ModelBuildStage;
  extras?: Array<{ label: string; value: string; mono?: boolean }>;
}) {
  const facts = [
    nonempty(stage.decision_summary)
      ? { label: "Decision", value: stage.decision_summary as string }
      : null,
    nonempty(stage.reason) ? { label: "Reason", value: stage.reason as string } : null,
    stage.rows_in != null ? { label: "Rows in", value: String(stage.rows_in), mono: true } : null,
    stage.rows_out != null ? { label: "Rows out", value: String(stage.rows_out), mono: true } : null,
    ...extras,
  ].filter((item): item is { label: string; value: string; mono?: boolean } => item != null);
  if (facts.length === 0) return null;
  return (
    <FactGrid>
      {facts.map((fact) => (
        <Fact key={fact.label} label={fact.label} value={fact.value} mono={fact.mono} />
      ))}
    </FactGrid>
  );
}

function FeatureList({
  title,
  rows,
}: {
  title: string;
  rows: Array<Record<string, unknown>>;
}) {
  if (rows.length === 0) return null;
  return (
    <div>
      <p className="product-eyebrow">{title}</p>
      <ul className="mt-2 space-y-2">
        {rows.map((row) => (
          <li key={asString(row.id, asString(row.name))} className="rounded-lg border border-hairline bg-paper-raised px-3 py-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-data text-ink">{asString(row.name)}</span>
              <Badge tone={asString(row.decision) === "accepted" ? "green" : "oxblood"} emphasis="soft">
                {asString(row.decision)}
              </Badge>
              <span className="text-helper text-ink-muted">{asString(row.feature_type)}</span>
            </div>
            {asList(row.sources).length > 0 ? (
              <p className="mt-1 text-helper text-ink-muted">
                Source: {asList(row.sources).map((source) => asString(asRecord(source).column_name)).join(", ")}
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function HoldoutPanel({ build, stage }: { build: PipelineModelBuild; stage: ModelBuildStage }) {
  const plan = asRecord(stageByKey(build, "final_holdout_plan")?.configuration);
  const lock = asRecord(stageByKey(build, "holdout_lock")?.configuration);
  const config = { ...plan, ...lock, ...asRecord(stage.configuration) };
  const locked = Boolean(config.locked) || Boolean(config.locked_at);
  return (
    <div className="space-y-5">
      <StageFacts
        stage={stage}
        extras={[
          { label: "Strategy", value: asString(config.strategy) },
          { label: "Test percentage", value: percentLabel(config.test_size) },
          { label: "Group column", value: asString(config.group_column, "None") },
          { label: "Time column", value: asString(config.time_column, "None") },
          { label: "Locked", value: locked ? "Locked" : "Not locked" },
          nonempty(asString(config.locked_at, ""))
            ? { label: "Locked at", value: formatWhen(asString(config.locked_at, "")), mono: true }
            : { label: "Locked at", value: "—" },
        ]}
      />
    </div>
  );
}

function FeatureEngineeringPanel({ stage }: { stage: ModelBuildStage }) {
  const config = asRecord(stage.configuration);
  const features = asList(config.features).map(asRecord);
  const original = features.filter((row) => asString(row.origin) === "original");
  const generated = features.filter((row) => asString(row.origin) === "generated");
  const transforms = features.flatMap((feature) =>
    asList(feature.transformations).map((item) => {
      const transform = asRecord(item);
      return {
        feature: asString(feature.name),
        sequence: transform.sequence,
        transformation_type: transform.transformation_type,
        transformer_class: transform.transformer_class,
      };
    }),
  );
  const lineage = features.flatMap((feature) =>
    asList(feature.sources).map((item) => {
      const source = asRecord(item);
      return {
        feature: asString(feature.name),
        column: asString(source.column_name),
        relationship: asString(source.relationship),
        decision: asString(feature.decision),
      };
    }),
  );
  return (
    <div className="space-y-5">
      <StageFacts
        stage={stage}
        extras={[
          { label: "Original features", value: asString(config.original_feature_count, String(original.length)), mono: true },
          { label: "Generated features", value: asString(config.generated_feature_count, String(generated.length)), mono: true },
          { label: "Accepted", value: asString(config.accepted_count), mono: true },
          { label: "Rejected", value: asString(config.rejected_count), mono: true },
        ]}
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <FeatureList title="Original features" rows={original} />
        <FeatureList title="Generated features" rows={generated} />
      </div>
      {transforms.length > 0 ? (
        <Table>
          <thead>
            <tr>
              <Th>Feature</Th>
              <Th>Transformation</Th>
              <Th>Class</Th>
            </tr>
          </thead>
          <tbody>
            {transforms.map((row, index) => (
              <tr key={`${asString(row.feature)}-${asString(row.sequence)}-${index}`}>
                <Td mono>{asString(row.feature)}</Td>
                <Td>{asString(row.transformation_type)}</Td>
                <Td mono>{asString(row.transformer_class, "—")}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : null}
      {lineage.length > 0 ? (
        <Table>
          <thead>
            <tr>
              <Th>Feature</Th>
              <Th>Source column</Th>
              <Th>Relationship</Th>
              <Th>Decision</Th>
            </tr>
          </thead>
          <tbody>
            {lineage.map((row, index) => (
              <tr key={`${row.feature}-${row.column}-${index}`}>
                <Td mono>{row.feature}</Td>
                <Td mono>{row.column}</Td>
                <Td>{row.relationship}</Td>
                <Td>{row.decision}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : null}
    </div>
  );
}

function pipelineSteps(steps: Array<Record<string, unknown>>, scope: string) {
  return steps.filter((row) => asString(row.column_scope).toLowerCase().includes(scope));
}

function typedSteps(steps: Array<Record<string, unknown>>, type: string) {
  return steps.filter((row) => asString(row.transformer_type).toLowerCase().includes(type));
}

function StepList({ title, rows }: { title: string; rows: Array<Record<string, unknown>> }) {
  if (rows.length === 0) return null;
  return (
    <div>
      <p className="product-eyebrow">{title}</p>
      <ol className="mt-2 space-y-2">
        {rows.map((row) => (
          <li key={`${asString(row.sequence)}-${asString(row.transformer_class)}`} className="rounded-lg border border-hairline px-3 py-2">
            <p className="text-body text-ink">{asString(row.transformer_class)}</p>
            <p className="mt-1 font-mono text-data text-ink-muted">
              {asString(row.transformer_type)} · fit {asString(row.fit_scope)}
            </p>
          </li>
        ))}
      </ol>
    </div>
  );
}

function PreprocessingPanel({ stage }: { stage: ModelBuildStage }) {
  const steps = asList(asRecord(stage.configuration).steps).map(asRecord);
  const scopes = Array.from(
    new Set(steps.map((row) => asString(row.fit_scope)).filter((value) => value !== "—")),
  );
  return (
    <div className="space-y-5">
      <StageFacts
        stage={stage}
        extras={[{ label: "Fit scope", value: scopes.join(", ") || "—" }]}
      />
      <div className="grid gap-4 lg:grid-cols-2">
        <StepList title="Numerical pipeline" rows={pipelineSteps(steps, "numeric")} />
        <StepList title="Categorical pipeline" rows={pipelineSteps(steps, "categ")} />
        <StepList title="Imputation" rows={typedSteps(steps, "impute")} />
        <StepList title="Scaling" rows={typedSteps(steps, "scale")} />
        <StepList title="Encoding" rows={typedSteps(steps, "encode")} />
      </div>
    </div>
  );
}

function CandidateTrainingPanel({
  build,
  stage,
}: {
  build: PipelineModelBuild;
  stage: ModelBuildStage;
}) {
  const candidates = asList(asRecord(stageByKey(build, "candidate_generation")?.configuration).candidates).map(asRecord);
  const folds = asList(asRecord(stageByKey(build, "cv_training")?.configuration).folds).map(asRecord);
  const [openId, setOpenId] = useState<string | null>(candidates[0] ? asString(candidates[0].id, "") : null);
  return (
    <div className="space-y-5">
      <StageFacts stage={stage} extras={[{ label: "Candidates", value: String(candidates.length), mono: true }]} />
      <div className="grid gap-3 md:grid-cols-2">
        {candidates.map((row) => {
          const id = asString(row.id, "");
          const selected = openId === id;
          const relatedFolds = folds.filter((fold) => asString(fold.candidate_id) === id);
          return (
            <button
              key={id}
              type="button"
              onClick={() => setOpenId(id)}
              className={cn(
                "rounded-xl border border-hairline bg-paper-raised p-4 text-left shadow-xs transition-ui",
                selected && "border-navy outline outline-2 outline-navy",
              )}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <p className="font-sans text-card text-ink">{asString(row.algorithm, asString(row.model_family))}</p>
                <Badge tone="neutral" emphasis="soft">
                  {asString(row.status)}
                </Badge>
              </div>
              <p className="mt-2 font-mono text-data text-ink-muted">
                {asString(row.implementation_library, "library")} · {asString(row.implementation_class, "class")}
              </p>
              <p className="mt-1 break-all font-mono text-data text-ink">{asString(row.fingerprint)}</p>
              <p className="mt-2 text-helper text-ink-muted">
                {relatedFolds.length} fold{relatedFolds.length === 1 ? "" : "s"}
              </p>
              {selected ? (
                <dl className="mt-3 grid gap-2 border-t border-hairline pt-3">
                  {Object.entries(asStringRecord(row.hyperparameters)).map(([name, value]) => (
                    <div key={name} className="flex justify-between gap-3">
                      <dt className="text-helper text-ink-muted">{name}</dt>
                      <dd className="break-all font-mono text-data text-ink">{value}</dd>
                    </div>
                  ))}
                </dl>
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function CvPanel({ build, stage }: { build: PipelineModelBuild; stage: ModelBuildStage }) {
  const generation = asList(asRecord(stageByKey(build, "candidate_generation")?.configuration).candidates).map(asRecord);
  const folds = asList(asRecord(stage.configuration).folds).map(asRecord);
  const names = new Map(generation.map((row) => [asString(row.id), asString(row.algorithm, asString(row.candidate_key))]));
  const metricNames = Array.from(
    new Set(folds.flatMap((fold) => Object.keys(asNumberRecord(fold.metrics)))),
  );
  const primary = metricNames[0];
  const chart = useMemo(() => {
    if (!primary) return [];
    const byFold = new Map<number, Record<string, number | string>>();
    for (const fold of folds) {
      const foldNumber = asNumber(fold.fold_number);
      if (foldNumber == null) continue;
      const label = `Fold ${foldNumber}`;
      const current = byFold.get(foldNumber) ?? { fold: label };
      const candidate = names.get(asString(fold.candidate_id)) ?? asString(fold.candidate_id).slice(0, 8);
      const metrics = asNumberRecord(fold.metrics);
      if (metrics[primary] != null) current[candidate] = metrics[primary];
      byFold.set(foldNumber, current);
    }
    return Array.from(byFold.entries())
      .sort(([left], [right]) => left - right)
      .map(([, row]) => row);
  }, [folds, names, primary]);
  const series = Array.from(new Set(chart.flatMap((row) => Object.keys(row).filter((key) => key !== "fold"))));
  return (
    <div className="space-y-5">
      <StageFacts stage={stage} extras={primary ? [{ label: "Chart metric", value: primary, mono: true }] : []} />
      {chart.length > 0 && series.length > 0 ? (
        <div className="h-72 min-w-0 rounded-xl border border-hairline bg-paper-raised p-3">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chart} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--color-hairline)" vertical={false} />
              <XAxis dataKey="fold" tick={{ fill: "var(--color-ink-muted)", fontSize: 12 }} />
              <YAxis tick={{ fill: "var(--color-ink-muted)", fontSize: 12 }} />
              <Tooltip />
              <Legend />
              {series.map((name, index) => (
                <Bar key={name} dataKey={name} fill={CHART_COLORS[index % CHART_COLORS.length]} radius={[4, 4, 0, 0]} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      ) : null}
      <Table>
        <thead>
          <tr>
            <Th>Candidate</Th>
            <Th>Fold</Th>
            <Th>Train rows</Th>
            <Th>Validation rows</Th>
            <Th>Metrics</Th>
          </tr>
        </thead>
        <tbody>
          {folds.map((fold) => {
            const metrics = asNumberRecord(fold.metrics);
            return (
              <tr key={asString(fold.id)}>
                <Td>{names.get(asString(fold.candidate_id)) ?? asString(fold.candidate_id)}</Td>
                <Td mono>{asString(fold.fold_number)}</Td>
                <Td mono>{asString(fold.train_row_count)}</Td>
                <Td mono>{asString(fold.validation_row_count)}</Td>
                <Td mono>
                  {Object.entries(metrics)
                    .map(([name, value]) => `${name}=${formatMetric(value)}`)
                    .join(" · ") || "—"}
                </Td>
              </tr>
            );
          })}
        </tbody>
      </Table>
    </div>
  );
}

function WinnerPanel({ stage }: { stage: ModelBuildStage }) {
  const config = asRecord(stage.configuration);
  const selectedScore = asNumber(config.selected_score);
  const runnerUpScore = asNumber(config.runner_up_score);
  const delta = asNumber(config.score_delta);
  return (
    <div className="space-y-5">
      <StageFacts
        stage={stage}
        extras={[
          { label: "Selection metric", value: asString(config.selection_metric), mono: true },
          { label: "Policy", value: asString(config.selection_policy) },
        ]}
      />
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-xl border border-green/30 bg-paper-raised p-4">
          <p className="product-eyebrow">Winner</p>
          <p className="mt-2 font-sans text-section text-ink">{asString(config.selected_algorithm, "Selected candidate")}</p>
          <p className="mt-1 text-helper text-ink-muted">{asString(config.selected_model_family)}</p>
          <p className="mt-3 font-mono text-kpi text-ink">
            {selectedScore == null ? "—" : formatMetric(selectedScore)}
          </p>
        </div>
        <div className="rounded-xl border border-hairline bg-paper-raised p-4">
          <p className="product-eyebrow">Runner-up</p>
          <p className="mt-2 font-sans text-section text-ink">{asString(config.runner_up_algorithm, "None")}</p>
          <p className="mt-1 text-helper text-ink-muted">{asString(config.runner_up_model_family)}</p>
          <p className="mt-3 font-mono text-kpi text-ink">
            {runnerUpScore == null ? "—" : formatMetric(runnerUpScore)}
          </p>
        </div>
      </div>
      {delta != null ? (
        <p className="text-body text-ink">
          Winner beat runner-up by {formatMetric(delta)} {asString(config.selection_metric)} on CV evidence only.
        </p>
      ) : (
        <p className="text-body text-ink-muted">No runner-up score was recorded for this lock.</p>
      )}
    </div>
  );
}

function FinalHoldoutPanel({ build, stage }: { build: PipelineModelBuild; stage: ModelBuildStage }) {
  const winner = stageByKey(build, "winner_lock");
  const evaluations = asList(asRecord(stage.configuration).evaluations).map(asRecord);
  return (
    <div className="space-y-5">
      <div className="model-build-lock-banner">
        <p className="product-eyebrow">Scientific order</p>
        <p className="mt-2 font-sans text-title text-ink">Winner locked before final holdout</p>
        <p className="mt-2 max-w-2xl text-body text-ink">
          Candidate comparison used CV only. The locked winner is the only model scored on the final holdout.
        </p>
        {winner?.completed_at ? (
          <p className="mt-2 font-mono text-data text-ink-muted">Winner locked {formatWhen(winner.completed_at)}</p>
        ) : null}
      </div>
      <StageFacts stage={stage} />
      {evaluations.length > 0 ? (
        <Table>
          <thead>
            <tr>
              <Th>Evaluation</Th>
              <Th>Status</Th>
              <Th>Metrics</Th>
            </tr>
          </thead>
          <tbody>
            {evaluations.map((row) => {
              const metrics = asNumberRecord(row.metrics);
              return (
                <tr key={asString(row.id)}>
                  <Td mono>{asString(row.id)}</Td>
                  <Td>{asString(row.status)}</Td>
                  <Td mono>
                    {Object.entries(metrics)
                      .map(([name, value]) => `${name}=${formatMetric(value)}`)
                      .join(" · ") || "—"}
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      ) : null}
    </div>
  );
}

function GenericPanel({ stage }: { stage: ModelBuildStage }) {
  const skip = new Set([
    "features",
    "candidates",
    "folds",
    "fold_metrics",
    "steps",
    "artifacts",
    "code_snapshots",
    "runtime_environments",
    "attempts",
    "evaluations",
    "candidate_scores",
  ]);
  const extras = Object.entries(asRecord(stage.configuration))
    .filter(([key, value]) => !skip.has(key) && value !== null && typeof value !== "object")
    .map(([key, value]) => ({
      label: key.replaceAll("_", " "),
      value: asString(value),
      mono: typeof value === "number" || key.includes("id") || key.includes("digest"),
    }));
  return <StageFacts stage={stage} extras={extras} />;
}

export function ModelBuildStagePanel({
  build,
  stage,
}: {
  build: PipelineModelBuild;
  stage: ModelBuildStage;
}) {
  if (stage.key === "final_holdout_plan" || stage.key === "holdout_lock") {
    return <HoldoutPanel build={build} stage={stage} />;
  }
  if (stage.key === "feature_engineering") return <FeatureEngineeringPanel stage={stage} />;
  if (stage.key === "preprocessing") return <PreprocessingPanel stage={stage} />;
  if (stage.key === "candidate_generation") return <CandidateTrainingPanel build={build} stage={stage} />;
  if (stage.key === "cv_training") return <CvPanel build={build} stage={stage} />;
  if (stage.key === "winner_lock" || stage.key === "candidate_comparison") {
    return <WinnerPanel stage={stageByKey(build, "winner_lock") ?? stage} />;
  }
  if (stage.key === "final_holdout") return <FinalHoldoutPanel build={build} stage={stage} />;
  return <GenericPanel stage={stage} />;
}
