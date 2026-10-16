"use client";

/** Goal & test design reads (V7 UI-C): the project's problem specs and one split plan. Every value comes from a /v1 field. */
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";

const Creator = z.object({ kind: z.string(), is_you: z.boolean().optional() });
const ProblemSpecSchema = z.object({
  id: z.string(),
  version: z.number(),
  status: z.string(),
  is_current: z.boolean(),
  task_type: z.string(),
  target_column: z.string().nullable(),
  primary_metric: z.string().nullable(),
  prediction_unit: z.string().nullable(),
  prediction_horizon: z.string().nullable(),
  business_objective: z.string(),
  constraints: z.record(z.string(), z.unknown()),
  created_at: z.string(),
  creator: Creator,
});
export type StudioProblemSpecRead = z.infer<typeof ProblemSpecSchema>;

const SplitPlanSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  dataset_id: z.string(),
  version: z.number(),
  task_type: z.string(),
  target_column: z.string(),
  evaluation_strategy: z.string(),
  validation_strategy: z.string(),
  validation_folds: z.number(),
  stratified: z.boolean(),
  time_column: z.string().nullable(),
  group_column: z.string().nullable(),
  row_count: z.number(),
  train_row_count: z.number(),
  evaluation_row_count: z.number(),
  has_evaluation_set: z.boolean(),
  folds_status: z.string(),
  folds: z.array(z.object({ fold_number: z.number(), train_row_count: z.number(), validation_row_count: z.number() })),
  warmup_row_count: z.number().nullable(),
  creator: Creator,
  created_at: z.string(),
  locked_at: z.string(),
});
export type StudioSplitPlan = z.infer<typeof SplitPlanSchema>;

/** Problem spec versions of the project, newest first (the API's default page of 50). */
export function useProblemSpecs(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "problem-specs", projectId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/problem-specs", z.array(ProblemSpecSchema), { params: { project_id: projectId! }, signal }),
    enabled: isUuid(projectId),
    retry: false,
  });
}

/** One problem spec version (used when the run's spec is older than the listed page). */
export function useProblemSpec(projectId: string | undefined, specId: string | null | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "problem-spec", projectId, specId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/problem-specs/{spec_id}", ProblemSpecSchema, { params: { project_id: projectId!, spec_id: specId! }, signal }),
    enabled: isUuid(projectId) && isUuid(specId),
    retry: false,
  });
}

export function useSplitPlan(splitPlanId: string | null | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "split-plan", splitPlanId),
    queryFn: ({ signal }) => v1Get("/v1/split-plans/{split_plan_id}", SplitPlanSchema, { params: { split_plan_id: splitPlanId! }, signal }),
    enabled: isUuid(splitPlanId),
    retry: false,
  });
}
