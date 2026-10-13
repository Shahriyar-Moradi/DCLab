"use client";

/**
 * "Weekly scoring" reads and the one write (V7-B2b-UI). The save is `PUT /v1/projects/{id}/scoring-schedule` with an
 * Idempotency-Key per action (a retry of the same request reuses it); it is a signed-in person's act.
 */
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get, v1Put } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";
import { DatasetVersionSchema } from "./studio-data-hooks";

const nullableString = z.string().nullable().optional();
const RunSchema = z.object({
  id: z.string(),
  planned_for: z.string(),
  outcome: z.string(), // an unknown value reads as a fixed sentence, it must not break the whole card
  message: z.string(),
  reason_code: nullableString,
  batch_prediction_id: nullableString,
  model_version_id: nullableString,
  model_release_id: nullableString,
  input_dataset_id: nullableString,
  created_at: z.string(),
});
const ScheduleSchema = z.object({
  id: z.string(),
  weekday: z.number(),
  weekday_name: z.string(),
  hour_utc: z.number(),
  timezone: z.literal("UTC"),
  enabled: z.boolean(),
  next_planned_for: nullableString,
  last_planned_for: nullableString,
  created_at: z.string(),
  updated_at: z.string(),
  created_by_user_id: nullableString,
  updated_by_user_id: nullableString,
  owner_can_score: z.boolean(),
  warning: nullableString,
});
const StateSchema = z.object({
  project_id: z.string(),
  schedule: ScheduleSchema.nullable().optional(),
  status: z.string(),
  status_message: nullableString,
  about: z.string(),
  recent_runs: z.array(RunSchema).optional(),
});
export type StudioScheduleState = z.infer<typeof StateSchema>;

const KEY = "scoring-schedule";

/** The project's weekly scoring schedule (null `schedule` when never set) and its newest handled weeks. */
export function useScoringSchedule(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", KEY, projectId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/scoring-schedule", StateSchema, { params: { project_id: projectId! }, signal }),
    enabled: isUuid(projectId),
    retry: false,
  });
}

export function saveScoringSchedule(input: { projectId: string; weekday: number; hourUtc: number; enabled: boolean; rationale?: string; key: string }) {
  return v1Put(
    "/v1/projects/{project_id}/scoring-schedule",
    StateSchema,
    { weekday: input.weekday, hour_utc: input.hourUtc, enabled: input.enabled, ...(input.rationale ? { rationale: input.rationale } : {}) },
    { params: { project_id: input.projectId }, idempotencyKey: input.key },
  );
}

/** After a save: show the state the save returned at once (no flicker), then re-read it and the scorings it queued. */
export function useScheduleWrites(projectId: string) {
  const client = useQueryClient();
  return {
    saved: (state: StudioScheduleState) => client.setQueryData(workspaceQueryKey("v1", KEY, projectId), state),
    invalidate: () => { for (const name of [KEY, "prediction"]) void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", name) }); },
  };
}

/** Dataset names for the files weekly runs scored (same cache entries as `useDatasetVersion`); a failed read has no name. */
export function useDatasetNames(ids: readonly string[]): Map<string, string> {
  const reads = useQueries({
    queries: ids.map((datasetId) => ({
      queryKey: workspaceQueryKey("v1", "dataset", datasetId),
      queryFn: ({ signal }: { signal: AbortSignal }) => v1Get("/v1/datasets/{dataset_id}", DatasetVersionSchema, { params: { dataset_id: datasetId }, signal }),
      enabled: isUuid(datasetId),
      retry: false,
    })),
  });
  return new Map(ids.flatMap((id, i) => (reads[i]?.data?.name ? [[id, reads[i].data!.name] as const] : [])));
}
