"use client";

/** The project flow read (V7 UI-B): `GET /v1/projects/{id}/flow`, typed facts for the eight steps (signed-in people only). */
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";

const str = z.string().nullable().optional();
const num = z.number().nullable().optional();
const state = { state: z.string() };

const ProjectFlowSchema = z.object({
  project_id: z.string(),
  stage: z.string(),
  waiting_decisions: z.number(),
  data: z.object({ ...state, file_name: str, row_count: num, newer_file: z.boolean().optional(), in_use: z.boolean().optional(), training_files: z.number().optional(), uploaded_at: str }),
  goal: z.object({ ...state, goal: z.object({ target_column: str, objective: str, primary_metric: str, task_type: z.string().optional() }).nullable().optional(), split_plan_id: str, validation_folds: num }),
  experiments: z.object({ ...state, run_count: z.number().optional(), running_count: z.number().optional(), best_experiment_id: str, best_run_number: num, best_cv_score: num, selection_metric: str }),
  improve: z.object({ ...state, branch_run_count: z.number().optional() }),
  model: z.object({ ...state, model_version_id: str, version: str, model_family: str, algorithm: str, decision_threshold: num, run_number: num }),
  predictions: z.object({ ...state, completed_scorings: z.number().optional(), last_rows_scored: num, last_scored_at: str, schedule_enabled: z.boolean().nullable().optional(), next_planned_for: str }),
  monitoring: z.object({ ...state, status: str, window_start: str, max_psi: num }),
  history: z.object({ ...state, decision_count: z.number().optional(), last_decision_at: str }),
});
export type StudioProjectFlow = z.infer<typeof ProjectFlowSchema>;

/**
 * Keyed under "projects" so every write that refreshes the project list refreshes the steps too. While a run or a scoring
 * is in progress it is re-read every 15 s (one cheap read); it stops once nothing is in progress, and a failed read stops it.
 */
export function useProjectFlow(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "projects", "flow", projectId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/flow", ProjectFlowSchema, { params: { project_id: projectId! }, signal }),
    enabled: isUuid(projectId),
    retry: false,
    staleTime: 15_000,
    refetchInterval: (query) => {
      if (query.state.status === "error") return false;
      const data = query.state.data;
      return data && (data.experiments.state === "in_progress" || data.predictions.state === "in_progress") ? 15_000 : false;
    },
  });
}
