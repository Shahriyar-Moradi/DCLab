"use client";

/** Pipeline evidence reads and the two actions of the page (P4.17-UI): replay an agent run, download an artifact. */
import { useQueries, useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { apiDownload } from "@/lib/infrastructure/api-client";
import { v1Get, v1Post } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";

const nullableString = z.string().nullable().optional();
const nullableNumber = z.number().nullable().optional();
const anyRecord = z.record(z.string(), z.unknown());

const StageSchema = z.object({
  key: z.string(), sequence: z.number(), title: z.string(), status: z.string(),
  started_at: nullableString, completed_at: nullableString,
  duration_ms: nullableNumber, rows_in: nullableNumber, rows_out: nullableNumber,
  decision_summary: nullableString, reason: nullableString,
  configuration: anyRecord.optional(),
  evidence_references: z.array(z.object({ entity_type: z.string(), id: nullableString, source: z.string().optional() })).optional(),
  generated_code: z.object({ digest: z.string(), spec_digest: z.string(), generator_version: z.string() }).nullable().optional(),
});
export const RunBuildSchema = z.object({
  pipeline_run_id: z.string(),
  workspace_id: z.string(),
  pipeline_run_status: z.string(),
  scientific_evidence_locked_at: nullableString,
  compatibility_fallback_used: z.boolean().optional(),
  generator_version: nullableString,
  reproduction_spec_digest: nullableString,
  stages: z.array(StageSchema),
});
export type RunBuild = z.infer<typeof RunBuildSchema>;

const EventPageSchema = z.object({
  items: z.array(z.object({ id: z.string(), event_type: z.string(), stage: z.string(), status: z.string(), sequence: z.number(), payload: anyRecord })),
  next_cursor: nullableString,
});
export type RunEvents = z.infer<typeof EventPageSchema>;

const ArtifactSchema = z.object({ id: z.string(), artifact_type: z.string(), content_digest: z.string(), mime_type: nullableString, size_bytes: z.number() });
export type RunArtifact = z.infer<typeof ArtifactSchema>;

const RecordSchema = z.object({
  id: z.string(), decision_type: z.string(), effective_state: z.string(), recorded_at: z.string(),
  actor: z.object({ kind: z.string(), rule: nullableString, agent_run_id: nullableString, service_token_id: nullableString }),
  subject: z.object({ kind: z.string(), id: z.string() }),
  details: anyRecord.optional(), details_truncated: z.boolean().optional(),
});
const RecordPageSchema = z.object({ items: z.array(RecordSchema), next_cursor: nullableString });
export type RunRecord = z.infer<typeof RecordSchema>;

const AgentRunSchema = z.object({
  id: z.string(), agent_key: z.string(), agent_version: z.string(), kind: z.string(), status: z.string(),
  cost_micros: z.number(), currency: z.string(), created_at: z.string(), decision_point_key: nullableString,
  subject: z.object({ kind: z.string(), id: nullableString }),
  usage: anyRecord.optional(),
});
const AgentRunPageSchema = z.object({ items: z.array(AgentRunSchema), next_cursor: nullableString });
export type RunAgentRun = z.infer<typeof AgentRunSchema>;

const LIVE_RUN = new Set(["queued", "running", "cancelling", "pending"]);

export function useRunBuild(experimentId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "run-build", experimentId),
    queryFn: ({ signal }) => v1Get("/v1/model-builds/{pipeline_run_id}", RunBuildSchema, { params: { pipeline_run_id: experimentId! }, signal }),
    enabled: isUuid(experimentId),
    retry: false,
    refetchInterval: (query) => (query.state.data && !["completed", "failed", "skipped", "cancelled", "canceled"].includes(query.state.data.pipeline_run_status.toLowerCase()) ? 2000 : false),
  });
}

export function useRunEvents(experimentId: string | undefined, live: boolean) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "run-events", experimentId),
    queryFn: ({ signal }) => v1Get("/v1/model-builds/{pipeline_run_id}/events", EventPageSchema, { params: { pipeline_run_id: experimentId! }, query: { limit: 200 }, signal }),
    enabled: isUuid(experimentId),
    retry: false,
    refetchInterval: live ? 3000 : false,
  });
}

export function useRunArtifacts(experimentId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "run-artifacts", experimentId),
    queryFn: ({ signal }) => v1Get("/v1/model-builds/{pipeline_run_id}/artifacts", z.array(ArtifactSchema), { params: { pipeline_run_id: experimentId! }, signal }),
    enabled: isUuid(experimentId),
    retry: false,
  });
}

export type RecordSubject = { kind: "experiment" | "split_plan" | "candidate"; id: string | null | undefined };

/**
 * Records about the run: its own (decision points) plus those about the split plan and the winning candidate it
 * used (the engine writes `split_plan_created` and `winner_locked` on those nodes, not on the experiment).
 */
export function useRunRecords(projectId: string | undefined, subjects: RecordSubject[]) {
  const queries = useQueries({
    queries: subjects.map((subject) => ({
      queryKey: workspaceQueryKey("v1", "run-records", projectId, subject.kind, subject.id),
      queryFn: ({ signal }: { signal: AbortSignal }) => v1Get("/v1/projects/{project_id}/decisions", RecordPageSchema, { params: { project_id: projectId! }, query: { subject_kind: subject.kind, subject_id: subject.id!, limit: 100 }, signal }),
      enabled: isUuid(projectId) && isUuid(subject.id),
      retry: false,
    })),
  });
  const seen = new Set<string>();
  const items = queries.flatMap((q) => q.data?.items ?? []).filter((r) => (seen.has(r.id) ? false : (seen.add(r.id), true)));
  const failed = queries.find((q) => q.isError);
  return { items, isPending: queries.some((q) => q.isPending && q.fetchStatus !== "idle"), isError: Boolean(failed), error: failed?.error };
}

export function useRunAgentRuns(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "run-agent-runs", projectId),
    queryFn: ({ signal }) => v1Get("/v1/agent-runs", AgentRunPageSchema, { query: { project_id: projectId!, limit: 100 }, signal }),
    enabled: isUuid(projectId),
    retry: false,
    refetchInterval: (query) => (query.state.data?.items.some((r) => LIVE_RUN.has(r.status.toLowerCase())) ? 4000 : false),
  });
}

const ReplaySchema = z.object({
  run_id: z.string(),
  equal: z.boolean(),
  same_failure: z.boolean().optional(),
  not_comparable: z.boolean().optional(),
  mismatches: z.array(z.string()),
  incident_id: nullableString,
  output_digest: nullableString,
  tool_sequence: z.array(z.object({ tool: z.string(), argument_digest: z.string() })),
});

/** Human-only POST (CSRF and Idempotency-Key): the caller passes the action's key so a retried click replays. */
export function replayAgentRun(input: { runId: string; key: string }) {
  return v1Post("/v1/agent-runs/{run_id}/replay", ReplaySchema, undefined as never, { params: { run_id: input.runId }, idempotencyKey: input.key });
}

/** Authorised download through the BFF with the session credentials; offered to the browser as a Blob (no token or signed URL in any link). */
export function downloadArtifactBlob(workspaceId: string, artifactId: string): Promise<{ blob: Blob; filename: string }> {
  if (!isUuid(workspaceId) || !isUuid(artifactId)) return Promise.reject(new Error("The artifact id is not valid, so nothing was downloaded."));
  return apiDownload(`/workspaces/${workspaceId}/artifacts/${artifactId}/download`, { fallbackFilename: `artifact-${artifactId.slice(0, 8)}` });
}
