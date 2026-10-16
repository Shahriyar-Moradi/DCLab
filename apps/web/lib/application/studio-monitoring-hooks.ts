"use client";

/**
 * Monitoring reads and the one write (P7.5-A). Reads: the overview of the model in use, one week, the linked outcomes
 * files. Write: `POST /v1/projects/{id}/monitoring/outcomes` with an Idempotency-Key per action (a retry of the same
 * request reuses it); it is a signed-in person's act. Going back to an earlier model is NOT here: it is the existing
 * put-in-use write used by the Model screen (`makeChampion`), through the same confirm component.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get, v1Post } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";
import { DatasetProfileSchema } from "./studio-data-hooks";

const nullableString = z.string().nullable().optional();
const nullableNumber = z.number().nullable().optional();
const ThresholdsSchema = z.object({
  psi_watch: z.number(),
  psi_alert: z.number(),
  significance_level: nullableNumber,
  flagged_share_relative_watch: z.number(),
  flagged_share_absolute_watch: nullableNumber,
  min_rows: z.number(),
  min_labels: z.number(),
  min_per_class: z.number(),
});
const FindingSchema = z.object({ code: z.string(), severity: z.string(), title: z.string(), body: z.string() });
const WeekSummarySchema = z.object({
  window_start: z.string(),
  window_end: z.string(),
  revision: z.number(),
  model_version_id: z.string(),
  model_release_id: nullableString,
  status: z.string(), // an unknown value reads as a fixed sentence, it must not break the page
  summary_code: z.string(),
  summary: z.string(),
  n_rows: z.number(),
  n_batch_predictions: z.number(),
  max_psi: nullableNumber,
  prediction_psi: nullableNumber,
  outcomes_status: z.string(),
  n_labelled: nullableNumber,
  coverage: nullableNumber,
  outcome_metric: nullableString,
  outcome_metric_value: nullableNumber,
  outcome_precision: nullableNumber,
  outcome_recall: nullableNumber,
  computed_at: z.string(),
});
const PsiPointSchema = z.object({ window_start: z.string(), psi: z.number().nullable(), status: nullableString });
const FeatureSeriesSchema = z.object({ name: z.string(), kind: nullableString, points: z.array(PsiPointSchema) });
const OverviewSchema = z.object({
  project_id: z.string(),
  enabled: z.boolean(),
  model_version_id: nullableString,
  model_version: nullableString,
  model_release_id: nullableString,
  status: z.string(),
  latest: WeekSummarySchema.nullable().optional(),
  windows: z.array(WeekSummarySchema),
  // A malformed series must never blank the screen: a broken entry is dropped, a broken list reads as none.
  feature_psi_series: z.array(z.unknown()).transform((items) => items.flatMap((item) => { const r = FeatureSeriesSchema.safeParse(item); return r.success ? [r.data] : []; })).optional().catch([]),
  finding: FindingSchema.nullable().optional(),
  thresholds: ThresholdsSchema,
  notes: z.array(z.string()),
});
export type StudioMonitoring = z.infer<typeof OverviewSchema>;
const WeekSchema = WeekSummarySchema.extend({
  input_drift: z.record(z.string(), z.unknown()),
  feature_sentences: z.array(z.string()),
  prediction_drift: z.record(z.string(), z.unknown()),
  outcomes: z.record(z.string(), z.unknown()),
  outcome_sentences: z.array(z.string()).optional(),
  finding: FindingSchema.nullable().optional(),
  thresholds: ThresholdsSchema,
  notes: z.array(z.string()),
});
export type StudioMonitoringWeek = z.infer<typeof WeekSchema>;
const OutcomeSetSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  dataset_id: z.string(),
  dataset_name: nullableString,
  key_column: z.string(),
  outcome_column: z.string(),
  linked_at: z.string(),
  linked_by_user_id: nullableString,
});
const OutcomeSetListSchema = z.object({ project_id: z.string(), items: z.array(OutcomeSetSchema), notes: z.array(z.string()) });

const KEY = "monitoring";

/** The model in use: the latest week's status and the last 12 weeks, newest first, with the read-only thresholds. */
export function useMonitoring(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", KEY, projectId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/monitoring", OverviewSchema, { params: { project_id: projectId! }, signal }),
    enabled: isUuid(projectId),
    retry: false,
  });
}

/** One week of the model in use (`week` is its Monday, YYYY-MM-DD UTC). */
export function useMonitoringWeek(projectId: string | undefined, week: string | null) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", KEY, projectId, "week", week),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/monitoring/windows/{window_start}", WeekSchema, { params: { project_id: projectId!, window_start: week! }, signal }),
    enabled: isUuid(projectId) && !!week && /^\d{4}-\d{2}-\d{2}$/.test(week),
    retry: false,
  });
}

/** Linked outcomes files, newest first. */
export function useOutcomeSets(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", KEY, projectId, "outcomes"),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/monitoring/outcomes", OutcomeSetListSchema, { params: { project_id: projectId! }, signal }),
    enabled: isUuid(projectId),
    retry: false,
  });
}

/** The real column names of one uploaded file (its profile read). */
export function useDatasetColumns(datasetId: string | null) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "dataset-profile", datasetId),
    queryFn: ({ signal }) => v1Get("/v1/datasets/{dataset_id}/profile", DatasetProfileSchema, { params: { dataset_id: datasetId! }, signal }),
    enabled: isUuid(datasetId),
    retry: false,
    select: (profile) => [...profile.columns].sort((a, b) => a.ordinal_position - b.ordinal_position).map((c) => c.name),
  });
}

export function linkOutcomes(input: { projectId: string; datasetId: string; keyColumn: string; outcomeColumn: string; key: string }) {
  return v1Post(
    "/v1/projects/{project_id}/monitoring/outcomes",
    OutcomeSetSchema,
    { dataset_id: input.datasetId, key_column: input.keyColumn, outcome_column: input.outcomeColumn },
    { params: { project_id: input.projectId }, idempotencyKey: input.key },
  );
}

/** After a link (or going back to an earlier model): re-read monitoring and the project's files. */
export function useMonitoringInvalidation() {
  const client = useQueryClient();
  return () => { for (const name of [KEY, "datasets"]) void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", name) }); };
}
