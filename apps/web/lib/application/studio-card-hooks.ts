"use client";

/**
 * Model card page read (P4.11-UI): `GET /v1/model-versions/{id}/card`, parsed in full including the single
 * labelled `final_evaluation` (the human view; the API withholds it from agents). A separate cache key from
 * the trimmed read used by the model Version tab, so that tab never holds final-evaluation values.
 */
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";

const nullableString = z.string().nullable().optional();
const nullableNumber = z.number().nullable().optional();
const numbers = z.record(z.string(), z.number());

export const FullModelCardSchema = z.object({
  model_version_id: z.string(),
  version: z.string(),
  experiment_id: z.string(),
  project_id: nullableString,
  family: nullableString,
  algorithm: nullableString,
  created_at: z.string(),
  content_digest: z.string(),
  target: z.object({ column: nullableString, task_type: nullableString, positive_label: nullableString, positive_label_note: nullableString, class_labels: z.array(z.string()).optional(), prediction_unit: nullableString }),
  objective: z.object({
    primary_metric: nullableString, primary_metric_reason: nullableString, business_objective: nullableString,
    decision_threshold: nullableNumber, decision_threshold_source: nullableString,
    constraints: z.array(z.object({ metric: z.string(), op: z.string(), value: z.number(), cv_value: nullableNumber, cv_satisfied: z.boolean().nullable().optional() })).optional(),
  }),
  metric_in_words: z.object({ text: z.string(), basis: z.string(), caveat: nullableString, numbers: numbers.optional() }),
  cv: z.object({ metric: nullableString, mean: nullableNumber, std: nullableNumber, folds: nullableNumber, strategy: nullableString, at_locked_threshold: numbers.optional(), threshold_note: nullableString }),
  baseline: z.object({
    available: z.boolean(), text: z.string(), metric: nullableString, baseline_score: nullableNumber, winner_score: nullableNumber,
    margin: nullableNumber, beats_baseline: z.boolean().nullable().optional(), clear_margin: z.boolean().nullable().optional(),
  }),
  drivers: z.object({
    status: z.string(), text: z.string(), method: nullableString, folds: nullableNumber, columns_tested: nullableNumber, clear_drivers: z.array(z.string()).optional(),
    features: z.array(z.object({ rank: z.number(), column: z.string(), importance_mean: nullableNumber, importance_std: nullableNumber, importance_se: nullableNumber, distinguishable: z.boolean().nullable().optional() })).optional(),
  }),
  risks: z.object({ investigated: z.boolean(), text: z.string(), items: z.array(z.object({ check: z.string(), status: z.string(), severity: z.string(), message: z.string() })).optional() }),
  data: z.object({ name: nullableString, row_count: nullableNumber, column_count: nullableNumber, modeled_feature_count: nullableNumber, content_digest: nullableString }),
  split: z.object({
    evaluation_split_strategy: nullableString, evaluation_fraction: nullableNumber, validation_strategy: nullableString, validation_folds: nullableNumber,
    train_rows: nullableNumber, evaluation_rows: nullableNumber, stratified: z.boolean().nullable().optional(), group_column: nullableString, time_column: nullableString,
  }),
  llm: z.object({ used: z.boolean(), purposes: z.array(z.string()).optional() }),
  final_evaluation: z.object({ status: z.string(), label: nullableString, metric: nullableString, value: nullableNumber, metrics: numbers.optional(), decision_threshold: nullableNumber, note: nullableString }),
  markdown: z.string().optional(),
  untrusted_fields: z.array(z.string()).optional(),
});
export type StudioFullModelCard = z.infer<typeof FullModelCardSchema>;

export function useFullModelCard(modelVersionId: string | null | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "model-card-full", modelVersionId),
    queryFn: ({ signal }) => v1Get("/v1/model-versions/{model_version_id}/card", FullModelCardSchema, { params: { model_version_id: modelVersionId! }, signal }),
    enabled: isUuid(modelVersionId),
    retry: false,
  });
}
