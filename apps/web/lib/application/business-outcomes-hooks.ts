"use client";

/**
 * Business view reads (P7.6-A): `GET /v1/outcomes/projects` (projects with a model in use: id and name only) and
 * `GET /v1/projects/{id}/outcomes`. The list download is the existing authorized prediction download.
 */
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Download, v1Get } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";

const nullableString = z.string().nullable().optional();

const ProjectListSchema = z.object({ items: z.array(z.object({ id: z.string(), name: z.string() })).optional() });

const OutcomesSchema = z.object({
  project_name: z.string(),
  state: z.enum(["ready", "no_model", "no_result"]),
  message: nullableString,
  model_in_use: z
    .object({ display_name: z.string(), saved_at: z.string(), approved_at: nullableString, approval_text: z.string() })
    .nullable()
    .optional(),
  headline: z
    .object({
      status: z.enum(["reported", "withheld", "missing"]),
      sentences: z.array(z.string()).optional(),
      counts_text: nullableString,
      basis: z.string(),
    })
    .nullable()
    .optional(),
  how_built: z.object({ sentences: z.array(z.string()).optional() }).nullable().optional(),
  weekly_list: z
    .object({
      id: z.string(),
      completed_at: z.string(),
      rows: z.number(),
      has_reasons: z.boolean(),
      text: z.string(),
      download_path: z.string(),
    })
    .nullable()
    .optional(),
  weekly_list_note: nullableString,
  limits: z.array(z.string()).optional(),
  boundary: z.string(),
});
export type BusinessOutcomes = z.infer<typeof OutcomesSchema>;

/** The workspace's projects with a model in use (id and name only). */
export function useOutcomeProjects(enabled: boolean) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "outcome-projects"),
    queryFn: ({ signal }) => v1Get("/v1/outcomes/projects", ProjectListSchema, { signal }),
    enabled,
    retry: false,
  });
}

export function useProjectOutcomes(projectId: string | null) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "outcomes", projectId),
    queryFn: ({ signal }) =>
      v1Get("/v1/projects/{project_id}/outcomes", OutcomesSchema, { params: { project_id: projectId! }, signal }),
    enabled: isUuid(projectId),
    retry: false,
  });
}

/** The list file through `GET /v1/predictions/{id}/download` (the caller validated the id against the API's path). */
export function downloadList(predictionId: string): Promise<{ blob: Blob; filename: string }> {
  return v1Download("/v1/predictions/{prediction_id}/download", {
    params: { prediction_id: predictionId },
    accept: "text/csv",
    fallbackFilename: `list-${predictionId}.csv`,
  });
}
