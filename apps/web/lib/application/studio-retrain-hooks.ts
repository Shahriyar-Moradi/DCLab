"use client";

/**
 * Retrain and threshold reads and writes (D5c, ADR 0012). Reads: the retrain status (the Monitoring card), the retrain policy
 * and the monitoring thresholds. Writes, each with an Idempotency-Key per action (a retry of the same request reuses it):
 * start a retrain (a person with ML-write), set the policy and set the thresholds (workspace owners and administrators, with
 * a reason and `If-Match`). Switch / Keep is NOT here: it is the existing approval of the switch proposal
 * (`acceptRefProposal` / `resolveDecision` in studio-compare-hooks). The API decides; the UI's gating is a convenience.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { ifMatch, v1Get, v1Post, v1Put } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";
import type { PolicyValue, ThresholdValues } from "./studio-retrain";

const nullableString = z.string().nullable().optional();
const nullableNumber = z.number().nullable().optional();

const SideSchema = z.object({
  metric: nullableString, metric_value: nullableNumber, metrics: z.record(z.string(), z.number().nullable()).optional(),
  decision_threshold: nullableNumber, n_rows: nullableNumber,
});
const ComparisonSchema = z.object({
  status: z.string(), reason_code: nullableString, time_column: nullableString, incumbent_training_end: nullableString,
  final_test_start: nullableString, final_test_rows: nullableNumber, challenger: SideSchema.nullable().optional(),
  incumbent: SideSchema.nullable().optional(), proposal_refusal: nullableString, computed_at: z.string(),
});
const RequestSchema = z.object({
  id: z.string(), origin: z.string(), created_at: z.string(), incumbent_model_version_id: z.string(), dataset_id: z.string(),
  experiment_id: nullableString, experiment_status: nullableString, window_start: nullableString,
  carried: z.array(z.string()), not_carried: z.array(z.string()),
});
const StatusSchema = z.object({
  project_id: z.string(), policy: z.string(), state: z.string(), model_in_use_id: nullableString,
  latest_window_start: nullableString, latest_window_status: nullableString, newer_dataset_id: nullableString,
  request: RequestSchema.nullable().optional(), comparison: ComparisonSchema.nullable().optional(), comparison_withheld: z.boolean().optional(),
  real_outcomes: z.object({ window_start: z.string(), precision: nullableNumber, recall: nullableNumber, n_labelled: nullableNumber }).nullable().optional(),
  proposal_id: nullableString, proposal_state: nullableString,
});
export type StudioRetrainStatus = z.infer<typeof StatusSchema>;
const PolicySchema = z.object({
  project_id: z.string(), policy: z.string(), version: z.number(), etag: z.string(), updated_at: nullableString,
  updated_by_user_id: nullableString, owner_can_start: z.boolean().nullable().optional(), auto_paused_until: nullableString,
});
export type StudioRetrainPolicy = z.infer<typeof PolicySchema>;
const ValuesSchema = z.object({ psi_watch: z.number(), psi_alert: z.number(), precision_floor: z.number().nullable(), recall_floor: z.number().nullable() });
const ThresholdsSchema = ValuesSchema.extend({
  project_id: z.string(), version: z.number(), etag: z.string(), defaults: ValuesSchema,
  bounds: z.object({ psi_watch_min: z.number(), psi_watch_max: z.number(), psi_alert_max: z.number(), floor_min: z.number(), floor_max: z.number() }),
  changed_at: nullableString, changed_by_user_id: nullableString, can_edit: z.boolean(),
});
export type StudioThresholds = z.infer<typeof ThresholdsSchema>;

/** "What the system does on its own": the policy, the state, the newest retrain, its comparison and the switch proposal. */
export function useRetrainStatus(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "retrain-status", projectId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/retrain-status", StatusSchema, { params: { project_id: projectId! }, signal }),
    enabled: isUuid(projectId),
    retry: false,
  });
}

export function useRetrainPolicy(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "retrain-policy", projectId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/retrain-policy", PolicySchema, { params: { project_id: projectId! }, signal }),
    enabled: isUuid(projectId),
    retry: false,
  });
}

/** The thresholds in force, the defaults, the server's bounds and whether you may change them (`can_edit`). */
export function useMonitoringThresholds(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "monitoring-thresholds", projectId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/monitoring-thresholds", ThresholdsSchema, { params: { project_id: projectId! }, signal }),
    enabled: isUuid(projectId),
    retry: false,
  });
}

export function startRetrain(input: { projectId: string; datasetId: string; key: string }) {
  return v1Post("/v1/projects/{project_id}/retrain", StatusSchema, { dataset_id: input.datasetId }, { params: { project_id: input.projectId }, idempotencyKey: input.key });
}

export function setRetrainPolicy(input: { projectId: string; policy: PolicyValue; rationale: string; version: number; key: string }) {
  return v1Put(
    "/v1/projects/{project_id}/retrain-policy", PolicySchema, { policy: input.policy, rationale: input.rationale },
    { params: { project_id: input.projectId }, idempotencyKey: input.key, headers: ifMatch(input.version) },
  );
}

export function setThresholds(input: { projectId: string; values: ThresholdValues; rationale: string; version: number; key: string }) {
  return v1Put(
    "/v1/projects/{project_id}/monitoring-thresholds", ThresholdsSchema, { ...input.values, rationale: input.rationale },
    { params: { project_id: input.projectId }, idempotencyKey: input.key, headers: ifMatch(input.version) },
  );
}

/** After any retrain or threshold write (or Switch / Keep): re-read everything the Monitoring screen and the Inbox show. */
export function useRetrainInvalidation() {
  const client = useQueryClient();
  return () => {
    for (const name of ["retrain-status", "retrain-policy", "monitoring-thresholds", "monitoring", "inbox", "inbox-counts", "activity", "decisions", "project-refs", "experiments", "graph", "releases", "project-model-versions", "model-version"]) {
      void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", name) });
    }
  };
}
