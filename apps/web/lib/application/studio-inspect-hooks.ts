"use client";

/** Node inspector reads (P4.3-A). Every read is a typed /v1 call; nothing is written. */
import { useQueries, useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";

const nullableString = z.string().nullable().optional();
const nullableNumber = z.number().nullable().optional();
const anyRecord = z.record(z.string(), z.unknown());

export const ExperimentDetailSchema = z.object({
  id: z.string(),
  workspace_id: z.string(),
  project_id: nullableString,
  status: z.string(),
  intent: nullableString,
  created_at: z.string(),
  started_at: nullableString,
  ended_at: nullableString,
  target_column: nullableString,
  task_type: nullableString,
  change_set: anyRecord.nullable().optional(),
  diff_vs_parent: anyRecord.nullable().optional(),
  metrics: z.object({
    candidate_id: nullableString,
    constraint_status: nullableString,
    cv: anyRecord.optional(),
    decision_threshold: nullableNumber,
    family: nullableString,
    selected_score: nullableNumber,
    selection_metric: nullableString,
    /** The winner against the dummy baseline on the selection metric (cross-validation only). */
    baseline_comparison: anyRecord.nullable().optional(),
  }).nullable().optional(),
  untrusted_fields: z.array(z.string()).optional(),
});
export type StudioExperimentDetail = z.infer<typeof ExperimentDetailSchema>;

const CodeDocumentSchema = z.object({ filename: z.string(), media_type: z.string(), content_digest: z.string(), source: z.string() });
export const ExperimentCodeSchema = z.object({
  experiment_id: z.string(),
  generator_version: z.string(),
  spec_digest: z.string(),
  standalone_cv: z.boolean(),
  is_branch: z.boolean().optional(),
  helper_requirements: z.array(z.string()).optional(),
  inputs: z.array(z.object({ name: z.string(), placeholder: z.string(), env_var: z.string(), description: z.string() })).optional(),
  script: CodeDocumentSchema,
  notebook: CodeDocumentSchema,
});
export type StudioExperimentCode = z.infer<typeof ExperimentCodeSchema>;

export const ModelVersionSchema = z.object({
  id: z.string(),
  version: z.string(),
  project_id: nullableString,
  created_at: z.string(),
  content_digest: z.string(),
  algorithm: nullableString,
  family: nullableString,
  candidate_key: nullableString,
  is_champion: z.boolean(),
  ref_kinds: z.array(z.string()).optional(),
  lineage: z.object({
    candidate_id: z.string(),
    experiment_id: z.string(),
    feature_recipe_id: nullableString,
    prepared_dataset_id: nullableString,
    problem_spec_id: nullableString,
    source_dataset_id: nullableString,
    split_plan_id: nullableString,
  }),
  artifacts: z.array(z.object({ id: z.string(), role: z.string(), artifact_type: z.string(), content_digest: z.string(), size_bytes: z.number() })).optional(),
  metrics: z.object({ selection_metric: nullableString, selected_score: nullableNumber, cv: anyRecord.optional(), decision_threshold: nullableNumber, constraint_status: nullableString }).nullable().optional(),
});
export type StudioModelVersion = z.infer<typeof ModelVersionSchema>;

/** The card read WITHOUT its `final_evaluation`: this page never shows final test set values. */
export const ModelCardSchema = z.object({
  model_version_id: z.string(),
  baseline: z.object({ available: z.boolean(), text: z.string(), beats_baseline: z.boolean().nullable().optional() }),
  drivers: z.object({
    status: z.string(),
    text: z.string(),
    method: nullableString,
    scoring: nullableString,
    folds: nullableNumber,
    features: z.array(z.object({ rank: z.number(), column: z.string(), importance_mean: nullableNumber, importance_se: nullableNumber, distinguishable: z.boolean().nullable().optional() })).optional(),
  }),
  split: z.object({
    split_plan_id: nullableString, evaluation_split_strategy: nullableString, evaluation_fraction: nullableNumber, evaluation_rows: nullableNumber,
    train_rows: nullableNumber, group_column: nullableString, time_column: nullableString, stratified: z.boolean().nullable().optional(),
    validation_folds: nullableNumber, validation_strategy: nullableString,
  }),
  metric_in_words: z.object({ text: z.string(), basis: z.string(), caveat: nullableString }),
  untrusted_fields: z.array(z.string()).optional(),
});
export type StudioModelCard = z.infer<typeof ModelCardSchema>;

const ProposalSchema = z.object({
  id: z.string(),
  proposal_type: z.string(),
  status: z.string(),
  level_at_proposal: z.number(),
  proposed_by: z.string(),
  created_at: z.string(),
  subject: z.object({ kind: z.string(), id: nullableString }),
  payload: anyRecord,
  rule_answer: anyRecord.nullable().optional(),
  proposed_rationale: nullableString,
  validator_verdict: z.string(),
});
const ProposalPageSchema = z.object({ items: z.array(ProposalSchema), limit: z.number(), next_cursor: nullableString });

export function useExperimentDetail(experimentId: string | null | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "experiment-detail", experimentId),
    queryFn: ({ signal }) => v1Get("/v1/experiments/{experiment_id}", ExperimentDetailSchema, { params: { experiment_id: experimentId! }, signal }),
    enabled: isUuid(experimentId),
    retry: false,
  });
}

/** Run reads for a list (family, score, baseline), one per completed run, capped and cached 60 s; shares the cache of `useExperimentDetail`. */
export const RUN_LIST_CAP = 25;
export function useRunDetails(experimentIds: string[], cap = RUN_LIST_CAP) {
  const ids = experimentIds.filter((id) => isUuid(id)).slice(0, cap);
  const results = useQueries({
    queries: ids.map((experimentId) => ({
      queryKey: workspaceQueryKey("v1", "experiment-detail", experimentId),
      queryFn: ({ signal }: { signal: AbortSignal }) => v1Get("/v1/experiments/{experiment_id}", ExperimentDetailSchema, { params: { experiment_id: experimentId }, signal }),
      staleTime: 60_000, retry: false, refetchOnWindowFocus: false,
    })),
  });
  const byId = new Map<string, (typeof results)[number]>();
  ids.forEach((experimentId, index) => byId.set(experimentId, results[index]));
  return byId;
}

export function useExperimentCode(experimentId: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "experiment-code", experimentId),
    queryFn: ({ signal }) => v1Get("/v1/experiments/{experiment_id}/code", ExperimentCodeSchema, { params: { experiment_id: experimentId! }, signal }),
    enabled: enabled && isUuid(experimentId),
    retry: false,
  });
}

export function useModelVersionRead(modelVersionId: string | null | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "model-version", modelVersionId),
    queryFn: ({ signal }) => v1Get("/v1/model-versions/{model_version_id}", ModelVersionSchema, { params: { model_version_id: modelVersionId! }, signal }),
    enabled: isUuid(modelVersionId),
    retry: false,
  });
}

export function useModelCardRead(modelVersionId: string | null | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "model-card", modelVersionId),
    queryFn: ({ signal }) => v1Get("/v1/model-versions/{model_version_id}/card", ModelCardSchema, { params: { model_version_id: modelVersionId! }, signal }),
    enabled: isUuid(modelVersionId),
    retry: false,
  });
}

/** P6.4 proposals of one type in a project (advice only; AI off or not released → none). */
export function useProjectProposals(projectId: string | undefined, proposalType: "ExperimentReviewProposal" | "DatasetInvestigationProposal") {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "proposals", projectId, proposalType),
    queryFn: ({ signal }) => v1Get("/v1/proposals", ProposalPageSchema, { query: { project_id: projectId!, proposal_type: proposalType, limit: 100 }, signal }),
    enabled: isUuid(projectId),
    retry: false,
  });
}
