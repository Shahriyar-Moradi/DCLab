"use client";

/**
 * Model screen reads, v7 parity (UI-D): the project's model versions (cross-validation numbers and standing only, never a
 * final test value) and one model's column list (names and types only). Both are reads; nothing here writes.
 */
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";

const nullableString = z.string().nullable().optional();
const nullableNumber = z.number().nullable().optional();
const numbers = z.record(z.string(), z.number());

const ModelVersionItemSchema = z.object({
  id: z.string(),
  version: z.string(),
  experiment_id: z.string(),
  created_at: z.string(),
  /** A string, not an enum: a standing added later shows as "Standing not known" instead of failing the read. */
  status: z.string(),
  approved_for_scoring: z.boolean(),
  waiting_for_approval: z.boolean(),
  last_approved_at: nullableString,
  cv: numbers.optional(),
  cv_at_locked_threshold: numbers.optional(),
  decision_threshold: nullableNumber,
  selection_metric: nullableString,
});
const ModelVersionPageSchema = z.object({
  items: z.array(ModelVersionItemSchema),
  next_cursor: nullableString,
  model_in_use_id: nullableString,
});
export type StudioModelVersionItem = z.infer<typeof ModelVersionItemSchema>;

export const MODEL_VERSIONS_PAGE = 50;

/** The project's model versions, newest first, a page at a time (cursor). */
export function useProjectModelVersions(projectId: string | undefined) {
  return useInfiniteQuery({
    queryKey: workspaceQueryKey("v1", "project-model-versions", projectId),
    queryFn: ({ pageParam, signal }) =>
      v1Get("/v1/projects/{project_id}/model-versions", ModelVersionPageSchema, {
        params: { project_id: projectId! }, query: { limit: MODEL_VERSIONS_PAGE, cursor: pageParam }, signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: isUuid(projectId),
    retry: false,
  });
}

const FeatureContractSchema = z.object({
  model_version_id: z.string(),
  status: z.enum(["available", "names_only"]),
  column_count: z.number(),
  columns: z.array(z.object({
    name: z.string(),
    role: z.enum(["numeric", "categorical"]).nullable().optional(),
    training_dtype: nullableString,
    required: z.boolean(),
    is_date: z.boolean(),
  })),
  target_column: nullableString,
  unknown_columns: nullableString,
  unseen_categories: nullableString,
});
export type StudioFeatureContract = z.infer<typeof FeatureContractSchema>;

/** The columns a file must have to be scored by this model (names and types; never values). */
export function useFeatureContract(modelVersionId: string | null | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "feature-contract", modelVersionId),
    queryFn: ({ signal }) => v1Get("/v1/model-versions/{model_version_id}/feature-contract", FeatureContractSchema, { params: { model_version_id: modelVersionId! }, signal }),
    enabled: isUuid(modelVersionId),
    retry: false,
    staleTime: 60_000,
  });
}
