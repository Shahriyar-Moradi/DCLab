"use client";

/**
 * Improvement loop reads and commands (D4e = P5.5-A): `GET /v1/improve-runs/{id}` (with its ETag, the If-Match of cancel and
 * accept), `GET /v1/projects/{id}/improve-runs`, `POST /v1/improve-runs`, `.../cancel` and `.../accept`. The loop read is
 * cross-validation only. The one final-test number of an accepted try is read from its final run, for people, under its own
 * cache key so no other screen's run read gains it. Every write carries an Idempotency-Key; reads poll only while the loop moves.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { ifMatch, v1Get, v1GetTagged, v1Post } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";
import { isPolled, type BudgetBody, type GoalBody } from "./studio-improve-loop";

const nullableString = z.string().nullable().optional();
const nullableNumber = z.number().nullable().optional();
const anyRecord = z.record(z.string(), z.unknown());

const IterationSchema = z.object({
  seq: z.number(),
  kind: z.string(),
  status: z.string(),
  reason: nullableString,
  action: anyRecord,
  experiment_id: nullableString,
  incumbent_experiment_id: z.string(),
  planned_new_candidates: z.number(),
  comparison: anyRecord.nullable().optional(),
  agent: z.string(),
  training_seconds: nullableNumber,
  created_at: z.string(),
  finished_at: nullableString,
});

const LoopSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  split_plan_id: z.string(),
  start_experiment_id: z.string(),
  incumbent_experiment_id: z.string(),
  final_experiment_id: nullableString,
  status: z.string(),
  outcome: nullableString,
  stop_reason: nullableString,
  stop_detail: nullableString,
  goal: anyRecord,
  budget: anyRecord,
  planned_comparisons: z.number(),
  validation_folds: z.number(),
  looks_before: z.number(),
  sealed_set_label: z.string(),
  sealed_set_consulted: z.boolean(),
  final_attempts: z.number(),
  accepted_iteration_seq: nullableNumber,
  started_by: z.string(),
  ai: z.string(),
  training_seconds_spent: z.number(),
  llm_micros_spent: z.number(),
  cancel_requested_at: nullableString,
  waiting_user_expires_at: nullableString,
  created_at: z.string(),
  started_at: nullableString,
  finished_at: nullableString,
  iterations: z.array(IterationSchema),
  actions: z.object({ accept: z.boolean(), keep_current: z.boolean(), cancel: z.boolean() }),
});
export type StudioLoop = z.infer<typeof LoopSchema>;
const LoopPageSchema = z.object({ project_id: z.string(), items: z.array(LoopSchema) });

const POLL_MS = 5_000;

export const loopKey = (loopId: string | null | undefined) => workspaceQueryKey("v1", "improve-run", loopId);
export const loopsKey = (projectId: string | null | undefined) => workspaceQueryKey("v1", "improve-runs", projectId);

/** One loop with its ETag; polls every 5 s while it is queued, running or finalizing. */
export function useImproveLoop(loopId: string | null | undefined) {
  return useQuery({
    queryKey: loopKey(loopId),
    queryFn: ({ signal }) => v1GetTagged("/v1/improve-runs/{improve_run_id}", LoopSchema, { params: { improve_run_id: loopId! }, signal }),
    enabled: isUuid(loopId),
    retry: false,
    refetchInterval: (query) => (isPolled(query.state.data?.data.status) ? POLL_MS : false),
  });
}

/** The project's loops, newest first; polls while one of them moves. */
export function useImproveLoops(projectId: string | null | undefined) {
  return useQuery({
    queryKey: loopsKey(projectId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/improve-runs", LoopPageSchema, { params: { project_id: projectId! }, query: { limit: 20 }, signal }),
    enabled: isUuid(projectId),
    retry: false,
    refetchInterval: (query) => (query.state.data?.items.some((l) => isPolled(l.status)) ? POLL_MS : false),
  });
}

const FinalRunSchema = z.object({
  id: z.string(),
  status: z.string(),
  metrics: z.object({ holdout: z.record(z.string(), z.number()).optional() }).nullable().optional(),
});

/**
 * The accepted try's final run: its status while it trains, and (people only, once finished) its labelled final-test scores. The
 * phase is part of the key, so the settled read is a new read the moment the loop leaves `finalizing` (no reload needed).
 */
export function useFinalRun(experimentId: string | null | undefined, poll: boolean) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "improve-final-run", experimentId, poll ? "live" : "settled"),
    queryFn: ({ signal }) => v1Get("/v1/experiments/{experiment_id}", FinalRunSchema, { params: { experiment_id: experimentId! }, signal }),
    enabled: isUuid(experimentId),
    retry: false,
    refetchInterval: poll ? POLL_MS : false,
  });
}

export function startImproveLoop(input: { body: { start: { from_experiment_id: string }; goal: GoalBody; budget: BudgetBody }; key: string }) {
  // The budget sends only the fields the form sets; the server fills its own defaults for the rest (the generated type marks them required).
  return v1Post("/v1/improve-runs", LoopSchema, input.body as never, { idempotencyKey: input.key });
}

/** `If-Match` from the read's ETag; a missing or weak tag is answered like the API's 428 (reload), never as a network error. */
function strongIfMatch(etag: string): Record<string, string> {
  if (!etag || etag.startsWith("W/")) throw Object.assign(new Error("precondition required"), { status: 428, body: { error: { code: "precondition_required" } } });
  return ifMatch(etag);
}

export function acceptImproveLoop(input: { loopId: string; body: { iteration: number; keep_current: false; rationale: string } | { keep_current: true; rationale?: string }; key: string; etag: string }) {
  return v1Post("/v1/improve-runs/{improve_run_id}/accept", LoopSchema, input.body, {
    params: { improve_run_id: input.loopId }, idempotencyKey: input.key, headers: strongIfMatch(input.etag),
  });
}

export function cancelImproveLoop(input: { loopId: string; key: string; etag: string }) {
  return v1Post("/v1/improve-runs/{improve_run_id}/cancel", LoopSchema, undefined as never, {
    params: { improve_run_id: input.loopId }, idempotencyKey: input.key, headers: strongIfMatch(input.etag),
  });
}

/** After a write: re-read the loop, the project's loops and the run lists. */
export function useLoopInvalidation() {
  const client = useQueryClient();
  return (loopId: string | null, projectId: string | null) => {
    if (loopId) void client.invalidateQueries({ queryKey: loopKey(loopId) });
    if (projectId) void client.invalidateQueries({ queryKey: loopsKey(projectId) });
    void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", "experiments") });
  };
}
