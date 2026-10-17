"use client";

/**
 * "Approved for scoring" reads (V7-B2-UI). Approving and going back use the existing put-in-use write
 * (`makeChampion` in studio-compare-hooks: Idempotency-Key + If-Match); there is no other approval path.
 */
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";

/** The approval summary on a model version read and a prediction read (no person ids). */
export const ReleaseSummarySchema = z.object({
  id: z.string(),
  model_version_id: z.string(),
  approved_at: z.string(),
  is_current: z.boolean(),
});

const ReleaseSchema = z.object({
  id: z.string(),
  model_version_id: z.string(),
  model_version: z.string(),
  approved_at: z.string(),
  approved_by_user_id: z.string().nullable().optional(),
  rationale: z.string().nullable().optional(),
  saved_format: z.string(),
  is_current: z.boolean(),
  previous_release_id: z.string().nullable().optional(),
  decision_record_id: z.string(),
});
const ReleaseListSchema = z.object({
  project_id: z.string(),
  current_release_id: z.string().nullable().optional(),
  items: z.array(ReleaseSchema).optional(),
});
export type StudioReleaseList = z.infer<typeof ReleaseListSchema>;

/** The project's approvals, newest first. Re-read after any put-in-use write (`useWriteInvalidation`). */
export function useProjectReleases(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "releases", projectId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/releases", ReleaseListSchema, { params: { project_id: projectId! }, signal }),
    enabled: isUuid(projectId),
    retry: false,
  });
}
