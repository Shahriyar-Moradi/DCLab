"use client";

/** Data page reads and the "make current" ref move (P4.1-C). Every value comes from a /v1 field. */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { ifMatch, v1Get, v1Post } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";

const nullableString = z.string().nullable().optional();
const nullableNumber = z.number().nullable().optional();

const PolicySchema = z.object({
  upload_policy: nullableString,
  publication_state: nullableString,
  policy_revision: nullableNumber,
  policy_complete: z.boolean(),
  sensitivity_class: nullableString,
  llm_exposure_policy: z.string(),
  retention_class: nullableString,
  residency_class: nullableString,
  ai_data_class: z.string(),
  workspace_ai_max_class: nullableString,
});
export const DatasetVersionSchema = z.object({
  id: z.string(),
  name: z.string(),
  version: z.string(),
  row_count: z.number(),
  column_count: z.number(),
  content_digest: nullableString,
  created_at: z.string(),
  policy: PolicySchema,
});
export type StudioDatasetVersion = z.infer<typeof DatasetVersionSchema>;

const ProfileColumnSchema = z.object({
  name: z.string(),
  ordinal_position: z.number(),
  physical_dtype: z.string(),
  rule_role: nullableString,
  role_used: nullableString,
  role_source: nullableString,
  role_reason: nullableString,
  missing_count: nullableNumber,
  missing_fraction: nullableNumber,
  unique_count: nullableNumber,
  unique_fraction: nullableNumber,
  transforms: z.array(z.string()),
  importance: nullableNumber,
  leakage_excluded: z.boolean(),
  leakage_risk: nullableString,
  leakage_reason: nullableString,
});
export type StudioProfileColumn = z.infer<typeof ProfileColumnSchema>;
export const DatasetProfileSchema = z.object({
  dataset_id: z.string(),
  scope: z.string(),
  statistics_status: z.string(),
  split_plan: z.object({ id: z.string(), version: z.number(), source: z.string(), target_column: z.string(), training_row_count: z.number() }).nullable(),
  experiment: z.object({ id: z.string(), selection: z.string(), created_at: z.string() }).nullable(),
  importance_method: nullableString,
  columns: z.array(ProfileColumnSchema),
});
export type StudioDatasetProfile = z.infer<typeof DatasetProfileSchema>;

const NodeRef = z.object({ kind: z.string(), id: z.string() });
const GraphSchema = z.object({
  edges: z.array(z.object({ from: NodeRef, to: NodeRef, relation: z.string(), attribute: z.boolean() })),
  truncated: z.boolean(),
});
export type StudioGraph = z.infer<typeof GraphSchema>;

const FindingsSchema = z.object({
  experiment_id: z.string(),
  investigated: z.boolean(),
  checks: z.array(z.object({ check: z.string(), status: z.string(), severity: z.string(), message: z.string() })).optional(),
});
export type StudioFindings = z.infer<typeof FindingsSchema>;

const RefMovedSchema = z.object({ refs: z.array(z.object({ ref_kind: z.string(), version: z.number() })) });

export function useDatasetVersion(datasetId: string | null | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "dataset", datasetId),
    queryFn: ({ signal }) => v1Get("/v1/datasets/{dataset_id}", DatasetVersionSchema, { params: { dataset_id: datasetId! }, signal }),
    enabled: isUuid(datasetId),
    retry: false,
  });
}

export function useDatasetProfile(datasetId: string | null | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "dataset-profile", datasetId),
    queryFn: ({ signal }) => v1Get("/v1/datasets/{dataset_id}/profile", DatasetProfileSchema, { params: { dataset_id: datasetId! }, signal }),
    enabled: isUuid(datasetId),
    retry: false,
  });
}

export function useProjectGraph(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "graph", projectId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/graph", GraphSchema, { params: { project_id: projectId! }, signal }),
    enabled: isUuid(projectId),
  });
}

export function useExperimentFindings(experimentId: string | null | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "findings", experimentId),
    queryFn: ({ signal }) => v1Get("/v1/experiments/{experiment_id}/findings", FindingsSchema, { params: { experiment_id: experimentId! }, signal }),
    enabled: isUuid(experimentId),
    retry: false,
  });
}

/**
 * Point the project's `dataset` ref at a version: one accepted `ref_moved` decision record.
 * `version` is the ref's current version (If-Match); null creates the missing ref (If-None-Match: *).
 */
export function makeDatasetCurrent(input: { projectId: string; datasetId: string; version: number | null; rationale: string; key: string }) {
  return v1Post(
    "/v1/projects/{project_id}/refs/{ref_kind}",
    RefMovedSchema,
    { target_id: input.datasetId, rationale: input.rationale, evidence_refs: [{ kind: "dataset_version", id: input.datasetId }] },
    {
      params: { project_id: input.projectId, ref_kind: "dataset" },
      idempotencyKey: input.key,
      headers: input.version === null ? { "If-None-Match": "*" } : ifMatch(input.version),
    },
  );
}

export function useRefInvalidation() {
  const client = useQueryClient();
  return (projectId: string) => {
    void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", "project-refs", projectId) });
    void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", "graph", projectId) });
    void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", "decisions", projectId) });
  };
}
