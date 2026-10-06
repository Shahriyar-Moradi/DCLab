"use client";

/** Developer Studio data hooks (P4.1-A). Every read is a typed /v1 call through the BFF. */
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { z } from "zod";
import { safeInternalHref } from "@/components/studio/safe-href";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get } from "@/lib/infrastructure/v1/client";
import { createCommandSearch, isUuid, projectHref, type SearchHit, type SearchSources } from "./command-search";

const nullableString = z.string().nullable().optional();

export const StudioProjectSchema = z.object({
  id: z.string(),
  workspace_id: z.string(),
  name: z.string(),
  slug: z.string(),
  description: z.string(),
  status: z.string(),
  updated_at: z.string(),
});
export type StudioProject = z.infer<typeof StudioProjectSchema>;

const RefSchema = z.object({
  ref_kind: z.string(),
  version: z.number(),
  moved_at: z.string(),
  target: z.object({ kind: z.string(), id: z.string(), key: z.string() }),
});
export const ProjectRefsSchema = z.object({
  project_id: z.string(),
  refs_initialized: z.boolean(),
  items: z.array(RefSchema),
  missing_kinds: z.array(z.string()).optional(),
});
export type ProjectRefs = z.infer<typeof ProjectRefsSchema>;

const ExperimentItemSchema = z.object({
  id: z.string(),
  status: z.string(),
  created_at: z.string(),
  intent: nullableString,
  project_id: nullableString,
  parent_experiment_id: nullableString,
  split_plan_id: nullableString,
  has_change_set: z.boolean().optional(),
  ended_at: nullableString,
});
export type StudioExperimentItem = z.infer<typeof ExperimentItemSchema>;
const ExperimentPageSchema = z.object({ items: z.array(ExperimentItemSchema), limit: z.number(), next_cursor: nullableString });

export const StudioExperimentSchema = z.object({
  id: z.string(),
  workspace_id: z.string(),
  project_id: nullableString,
  status: z.string(),
  intent: nullableString,
  created_at: z.string(),
  started_at: nullableString,
  ended_at: nullableString,
  failure_reason: nullableString,
  target_column: nullableString,
  task_type: nullableString,
  lineage: z.object({ parent_experiment_id: nullableString, split_plan_id: nullableString, problem_spec_id: nullableString, source_dataset_id: nullableString, execution_request_id: nullableString }),
});
export type StudioExperiment = z.infer<typeof StudioExperimentSchema>;

const DecisionItemSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  decision_type: z.string(),
  state: z.string(),
  effective_state: z.string(),
  recorded_at: z.string(),
  actor: z.object({ kind: z.string() }),
  subject: z.object({ kind: z.string(), id: z.string() }),
});
export type StudioDecisionItem = z.infer<typeof DecisionItemSchema>;
const DecisionPageSchema = z.object({ items: z.array(DecisionItemSchema), limit: z.number(), next_cursor: nullableString });

const ModelVersionSchema = z.object({ id: z.string(), version: z.string(), project_id: nullableString });

const LIVE = new Set(["queued", "running", "cancelling"]);

export function useStudioProjects() {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "projects"),
    queryFn: ({ signal }) => v1Get("/v1/projects", z.array(StudioProjectSchema), { signal }),
  });
}

export function useStudioProject(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "project", projectId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}", StudioProjectSchema, { params: { project_id: projectId! }, signal }),
    enabled: isUuid(projectId),
    retry: false,
  });
}

export function useProjectRefs(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "project-refs", projectId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/refs", ProjectRefsSchema, { params: { project_id: projectId! }, signal }),
    enabled: isUuid(projectId),
  });
}

export function useProjectExperiments(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "experiments", projectId),
    queryFn: ({ signal }) => v1Get("/v1/experiments", ExperimentPageSchema, { query: { project_id: projectId!, limit: 100 }, signal }),
    enabled: isUuid(projectId),
    refetchInterval: (query) => (query.state.data?.items.some((item) => LIVE.has(item.status)) ? 5000 : false),
  });
}

export function useStudioExperiment(experimentId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "experiment", experimentId),
    queryFn: ({ signal }) => v1Get("/v1/experiments/{experiment_id}", StudioExperimentSchema, { params: { experiment_id: experimentId! }, signal }),
    enabled: isUuid(experimentId),
    retry: false,
    refetchInterval: (query) => (LIVE.has(query.state.data?.status ?? "") ? 2000 : false),
  });
}

export function useProjectDecisions(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "decisions", projectId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/decisions", DecisionPageSchema, { params: { project_id: projectId! }, query: { limit: 100 }, signal }),
    enabled: isUuid(projectId),
  });
}

/**
 * `/lab/runs/{id}` → the project experiment page, only when the run's experiment is readable
 * in the active workspace and belongs to a project (404/403 keep the existing page).
 */
export function useExperimentProjectHref(pipelineRunId: string | null | undefined): string | null {
  const query = useStudioExperiment(pipelineRunId ?? undefined);
  const experiment = query.data;
  if (!experiment || experiment.id !== pipelineRunId) return null;
  return projectHref(experiment.project_id, "experiments", experiment.id);
}

const studioSources: SearchSources = {
  projects: (signal) => v1Get("/v1/projects", z.array(StudioProjectSchema), { signal }),
  experiments: async (projectId, signal) =>
    (await v1Get("/v1/experiments", ExperimentPageSchema, { query: { project_id: projectId ?? null, limit: 50 }, signal })).items,
  decisions: async (projectId, signal) =>
    (await v1Get("/v1/projects/{project_id}/decisions", DecisionPageSchema, { params: { project_id: projectId }, query: { limit: 50 }, signal })).items,
  refs: async (projectId, signal) =>
    (await v1Get("/v1/projects/{project_id}/refs", ProjectRefsSchema, { params: { project_id: projectId }, signal })).items,
  modelVersion: (id, signal) => v1Get("/v1/model-versions/{model_version_id}", ModelVersionSchema, { params: { model_version_id: id }, signal }),
};

/** ⌘K command bar data: debounced, stale requests aborted, hrefs already safe. */
export function useCommandSearch(projectId: string | undefined) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const searchRef = useRef<ReturnType<typeof createCommandSearch> | null>(null);
  if (!searchRef.current) {
    searchRef.current = createCommandSearch({
      sources: studioSources,
      onLoading: setLoading,
      onResults: (hits) => {
        setFailed(false);
        setResults(hits.filter((hit) => safeInternalHref(hit.href) !== null));
      },
      onError: () => setFailed(true),
    });
  }
  useEffect(() => () => searchRef.current?.cancel(), []);
  useEffect(() => {
    searchRef.current?.search(query, { projectId });
  }, [query, projectId]);
  return useMemo(() => ({ query, setQuery, results, loading, failed }), [query, results, loading, failed]);
}
