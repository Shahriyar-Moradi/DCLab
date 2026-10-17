"use client";

/**
 * Assistant (Beta) and AI settings reads and writes (D6b). Reads: /v1/assistant/threads[/{id}], /v1/proposals[/{id}].
 * Writes reuse the existing routes, each with the caller's Idempotency-Key: thread create, message (streamed as
 * Server-Sent Events), governance switches, incident resolve, policy propose / accept. The API decides every request.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get, v1Post, v1PostStream } from "@/lib/infrastructure/v1/client";
import { SseParser, type AssistantEvent, type PageContext } from "./studio-assistant";

const nullableString = z.string().nullable().optional();
const ThreadSchema = z.object({ id: z.string(), project_id: z.string(), title: z.string().nullable(), status: z.string(), created_at: z.string(), last_activity_at: z.string() });
export type AssistantThread = z.infer<typeof ThreadSchema>;
const ThreadDetailSchema = ThreadSchema.extend({
  limits: z.record(z.string(), z.number()),
  usage: z.record(z.string(), z.number()),
  ai_available: z.boolean(),
  quick_actions: z.array(z.string()),
  cost: z.object({ conversation_micros: z.number(), by_turn_micros: z.record(z.string(), z.number()), price_is_upper_bound: z.boolean() }),
  provider_notice: z.string(),
  events: z.array(z.object({ type: z.string(), turn_id: z.string(), seq: z.number(), data: z.record(z.string(), z.unknown()) })),
});
export type AssistantThreadDetail = z.infer<typeof ThreadDetailSchema>;

export function useAssistantThreads(projectId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "assistant-threads", projectId),
    queryFn: ({ signal }) => v1Get("/v1/assistant/threads", z.array(ThreadSchema), { query: { project_id: projectId, limit: 50 }, signal }),
    enabled: enabled && !!projectId,
    retry: false,
    staleTime: 30_000,
  });
}

export function useAssistantThread(threadId: string | null) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "assistant-thread", threadId),
    queryFn: ({ signal }) => v1Get("/v1/assistant/threads/{thread_id}", ThreadDetailSchema, { params: { thread_id: threadId! }, signal }),
    enabled: !!threadId,
    retry: false,
  });
}

export function createThread(input: { projectId: string; key: string }) {
  return v1Post("/v1/assistant/threads", ThreadSchema, { project_id: input.projectId }, { idempotencyKey: input.key });
}

/**
 * Send one message and read the turn as it streams. Resolves when the stream ends (`turn_done` or the connection closes);
 * a refusal before the stream starts rejects with the API error. Resending the same key replays the stored turn.
 */
export async function streamMessage(input: {
  threadId: string; text: string; pageContext: PageContext | null; key: string; signal?: AbortSignal; onEvent: (event: AssistantEvent) => void;
}): Promise<{ finished: boolean; malformed: number }> {
  const body = { text: input.text, ...(input.pageContext ? { page_context: input.pageContext } : {}) };
  const response = await v1PostStream("/v1/assistant/threads/{thread_id}/messages", body, { params: { thread_id: input.threadId }, idempotencyKey: input.key, signal: input.signal });
  const parser = new SseParser();
  let finished = false;
  const emit = (events: AssistantEvent[]) => {
    for (const event of events) {
      if (event.type === "turn_done") finished = true;
      input.onEvent(event);
    }
  };
  const reader = response.body?.getReader();
  if (!reader) return { finished, malformed: 0 };
  const decoder = new TextDecoder();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    emit(parser.feed(decoder.decode(value, { stream: true })));
  }
  emit(parser.feed(decoder.decode()));
  emit(parser.end());
  return { finished, malformed: parser.malformed };
}

const ProposalSchema = z.object({
  id: z.string(), project_id: z.string(), proposal_type: z.string(), proposed_by: z.string(), status: z.string(), open: z.boolean(),
  level_at_proposal: z.number(), subject: z.object({ kind: z.string(), id: nullableString }),
  tool_name: nullableString, tool_arguments: z.record(z.string(), z.unknown()).nullable().optional(), proposed_rationale: nullableString,
  estimated_cost_micros: z.number().nullable().optional(), estimated_duration_s: z.number().nullable().optional(),
  expires_at: nullableString, created_at: z.string(),
});
export type StudioProposal = z.infer<typeof ProposalSchema>;

export function useProposal(proposalId: string | null) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "proposal", proposalId),
    queryFn: ({ signal }) => v1Get("/v1/proposals/{proposal_id}", ProposalSchema, { params: { proposal_id: proposalId! }, signal }),
    enabled: !!proposalId,
    retry: false,
  });
}

/** Open proposals of a project (item 21: the per-screen "AI suggestions" list). Read only. */
export function useOpenProposals(projectId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "proposals", projectId, "proposed"),
    queryFn: ({ signal }) => v1Get("/v1/proposals", z.object({ items: z.array(ProposalSchema), next_cursor: nullableString, limit: z.number() }), { query: { project_id: projectId, status: "proposed", limit: 50 }, signal }),
    enabled: enabled && !!projectId,
    retry: false,
    staleTime: 30_000,
  });
}

/* ---------- AI settings commands ---------- */

const SwitchSchema = z.object({ id: z.string(), switch_key: z.string(), state: z.string() });
const IncidentSchema = z.object({ id: z.string(), status: z.string() });
const FieldChange = z.object({ path: z.string(), before: z.unknown().optional(), after: z.unknown().optional() });
const PolicyChangeSchema = z.object({
  id: z.string(), version: z.number(), state: z.string(), open: z.boolean(), policy_digest: z.string(), proposed_by: nullableString,
  diff_vs_head: z.array(FieldChange).nullable().optional(), diff_vs_effective: z.array(FieldChange).nullable().optional(),
});
export type StudioPolicyChange = z.infer<typeof PolicyChangeSchema>;

export function changeAiSwitch(input: { state: "on" | "off"; reason: string; key: string }) {
  return v1Post("/v1/governance/switches", SwitchSchema, { switch_key: "all_ai", state: input.state, reason: input.reason }, { idempotencyKey: input.key });
}

export function resolveReport(input: { incidentId: string; resolution: string; key: string }) {
  return v1Post("/v1/governance/incidents/{incident_id}/resolve", IncidentSchema, { resolution: input.resolution }, { params: { incident_id: input.incidentId }, idempotencyKey: input.key });
}

export function proposePolicy(input: { policy: Record<string, unknown>; rationale: string; key: string }) {
  return v1Post("/v1/governance/policy", PolicyChangeSchema, { policy: input.policy, rationale: input.rationale }, { idempotencyKey: input.key });
}

export function acceptPolicy(input: { proposalId: string; digest: string; key: string }) {
  return v1Post("/v1/governance/policy/{proposal_id}/accept", PolicyChangeSchema, { policy_digest: input.digest, acknowledge_consent_change: false }, { params: { proposal_id: input.proposalId }, idempotencyKey: input.key });
}

/** After a settings write: the governance read (Home, Inbox, AI settings and the panel's gate share it). */
export function useGovernanceInvalidation() {
  const client = useQueryClient();
  return () => void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", "governance") });
}
