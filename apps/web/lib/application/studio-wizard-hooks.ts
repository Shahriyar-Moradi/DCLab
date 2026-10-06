"use client";

/** Wizard writes and reads (P4.1-B). Every write is a typed /v1 call carrying the action's Idempotency-Key. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get, v1Post, v1PostForm } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";
import { StudioProjectSchema } from "./studio-hooks";
import type { SpecBody } from "./studio-wizard";

const nullableString = z.string().nullable().optional();

export const UploadedDatasetSchema = z.object({
  id: z.string(),
  project_id: nullableString,
  name: z.string(),
  version: z.string(),
  source_type: z.string(),
  content_digest: nullableString,
  schema_digest: nullableString,
  size_bytes: z.number().nullable().optional(),
  row_count: z.number(),
  column_count: z.number(),
  columns: z.array(z.object({ name: z.string(), dtype: z.string(), missing_fraction: z.number() })).optional(),
  ingestion: z.object({ id: z.string(), status: z.string(), publication_state: z.string(), rows_read: z.number(), bytes_read: z.number() }),
});
export type UploadedDataset = z.infer<typeof UploadedDatasetSchema>;

const DatasetItemSchema = z.object({
  id: z.string(),
  project_id: nullableString,
  name: z.string(),
  version: z.string(),
  row_count: z.number(),
  column_count: z.number(),
  content_digest: nullableString,
  purpose: z.string(),
  created_at: z.string(),
});
export type StudioDatasetItem = z.infer<typeof DatasetItemSchema>;

const SpecSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  version: z.number(),
  status: z.string(),
  task_type: z.string(),
  target_column: nullableString,
  primary_metric: nullableString,
  content_digest: z.string(),
});
export type StudioProblemSpec = z.infer<typeof SpecSchema>;

const StartedExperimentSchema = z.object({ id: z.string(), project_id: nullableString, status: z.string() });

const WaitingSchema = z.object({
  id: z.string(),
  status: z.string(),
  result_summary: z.record(z.string(), z.unknown()).nullable().optional(),
});
export type StudioExecutionRequest = z.infer<typeof WaitingSchema>;

type Keyed = { key: string };

export function createProject(input: { name: string; description?: string } & Keyed) {
  return v1Post("/v1/projects", StudioProjectSchema, { name: input.name, description: input.description ?? "" }, { idempotencyKey: input.key });
}

export function uploadDataset(input: { projectId: string; file: File } & Keyed) {
  const form = new FormData();
  form.append("project_id", input.projectId);
  form.append("file", input.file, input.file.name);
  return v1PostForm("/v1/datasets", UploadedDatasetSchema, form, { idempotencyKey: input.key });
}

export function createProblemSpec(input: { projectId: string; body: SpecBody } & Keyed) {
  return v1Post("/v1/projects/{project_id}/problem-specs", SpecSchema, input.body, { params: { project_id: input.projectId }, idempotencyKey: input.key });
}

export function startExperiment(input: { projectId: string; datasetId: string; problemSpecId: string; intent?: string } & Keyed) {
  return v1Post(
    "/v1/experiments",
    StartedExperimentSchema,
    { project_id: input.projectId, dataset_id: input.datasetId, problem_spec_id: input.problemSpecId, intent: input.intent ?? null },
    { idempotencyKey: input.key },
  );
}

export function confirmTarget(input: { requestId: string; targetColumn: string } & Keyed) {
  return v1Post(
    "/v1/execution-requests/{request_id}/target-confirmation",
    WaitingSchema,
    { target_column: input.targetColumn },
    { params: { request_id: input.requestId }, idempotencyKey: input.key },
  );
}

export function confirmSplit(input: { requestId: string } & Keyed) {
  return v1Post(
    "/v1/execution-requests/{request_id}/split-confirmation",
    WaitingSchema,
    { answer: "keep_rule_split" },
    { params: { request_id: input.requestId }, idempotencyKey: input.key },
  );
}

/** Datasets of one project, newest first (the list endpoint is workspace-wide; filtered here). */
export function useProjectDatasets(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "datasets", projectId),
    queryFn: async ({ signal }) => {
      const rows = await v1Get("/v1/datasets", z.array(DatasetItemSchema), { query: { limit: 200 }, signal });
      return rows.filter((row) => row.project_id === projectId && row.purpose === "training");
    },
    enabled: isUuid(projectId),
  });
}

/** The waiting payload of a run parked in needs_input (target or split question). */
export function useExecutionRequest(requestId: string | null | undefined, enabled: boolean) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "execution-request", requestId),
    queryFn: ({ signal }) => v1Get("/v1/execution-requests/{request_id}", WaitingSchema, { params: { request_id: requestId! }, signal }),
    enabled: enabled && isUuid(requestId),
    retry: false,
  });
}

/** Refresh lists and the experiment after a write. */
export function useWizardInvalidation() {
  const client = useQueryClient();
  return (projectId?: string, experimentId?: string) => {
    void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", "projects") });
    void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", "experiments", projectId) });
    void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", "datasets", projectId) });
    if (experimentId) void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", "experiment", experimentId) });
  };
}
