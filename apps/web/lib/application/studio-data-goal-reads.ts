"use client";

/**
 * D3b reads (D3a routes): target rate by month, the project's upload log (cursor pages) and "who decided" the goal.
 * Schemas mirror the regenerated `schema.d.ts`; every value shown comes from these fields.
 */
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";

const nullableString = z.string().nullable().optional();
const nullableNumber = z.number().nullable().optional();

const TargetByPeriodSchema = z.object({
  dataset_id: z.string(),
  project_id: nullableString,
  status: z.string(),
  split_plan_id: nullableString,
  target_column: nullableString,
  time_column: nullableString,
  training_rate: nullableNumber,
  buckets: z.array(z.object({ month: z.string(), part: z.string(), rows: z.number(), rate: z.number() })),
  truncated: z.boolean(),
  final_test: z.object({
    layout: z.string(),
    start_month: nullableString,
    end_month: nullableString,
    row_count: z.number(),
    sealed: z.boolean(),
    sealed_reason: nullableString,
    rate: nullableNumber,
    disclosed_after_final_test: z.boolean(),
  }).nullable().optional(),
});
export type StudioTargetByPeriod = z.infer<typeof TargetByPeriodSchema>;

const UploadAttemptSchema = z.object({
  id: z.string(),
  created_at: z.string(),
  file_name: z.string(),
  size_bytes: nullableNumber,
  outcome: z.string(),
  reason_code: nullableString,
  reason: nullableString,
  uploaded_by: z.object({ kind: z.string(), is_you: z.boolean().optional() }),
  dataset_id: nullableString,
  source: z.string(),
});
const UploadAttemptPageSchema = z.object({
  project_id: z.string(),
  retention_days: z.number(),
  items: z.array(UploadAttemptSchema),
  next_cursor: nullableString,
});
export type StudioUploadAttempt = z.infer<typeof UploadAttemptSchema>;

const GoalDecisionsSchema = z.object({
  project_id: z.string(),
  items: z.array(z.object({
    what: z.string(),
    suggested_by: nullableString,
    confirmed_by: nullableString,
    when: nullableString,
    why: nullableString,
    why_is_default: z.boolean(),
    value: nullableString,
    decision_id: nullableString,
  })),
});

export function useTargetByPeriod(datasetId: string | null | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "target-by-period", datasetId),
    queryFn: ({ signal }) => v1Get("/v1/datasets/{dataset_id}/target-by-period", TargetByPeriodSchema, { params: { dataset_id: datasetId! }, signal }),
    enabled: isUuid(datasetId),
    retry: false,
  });
}

export const UPLOAD_LOG_PAGE = 20;
export function useUploadAttempts(projectId: string | null | undefined) {
  return useInfiniteQuery({
    queryKey: workspaceQueryKey("v1", "upload-attempts", projectId),
    queryFn: ({ pageParam, signal }) =>
      v1Get("/v1/projects/{project_id}/upload-attempts", UploadAttemptPageSchema, {
        params: { project_id: projectId! }, query: { limit: UPLOAD_LOG_PAGE, ...(pageParam ? { cursor: pageParam } : {}) }, signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: isUuid(projectId),
    retry: false,
  });
}

export function useGoalDecisions(projectId: string | null | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "goal-decisions", projectId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/goal/decisions", GoalDecisionsSchema, { params: { project_id: projectId! }, signal }),
    enabled: isUuid(projectId),
    retry: false,
  });
}
