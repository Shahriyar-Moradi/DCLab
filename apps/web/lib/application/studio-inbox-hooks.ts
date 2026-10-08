"use client";

/**
 * Inbox reads and writes (P4.16-UI). Reads: GET /v1/inbox (cursor paged, per tab) and /v1/inbox/counts (sidebar badge).
 * Writes reuse existing routes: decisions via studio-compare-hooks, proposals via /v1/proposals/{id}/accept|reject|revert.
 * Every write carries the caller's Idempotency-Key; role gating in the UI is a convenience, the API decides.
 */
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get, v1Post } from "@/lib/infrastructure/v1/client";
import { INBOX_BADGE_REFETCH_MS, type InboxTabId } from "./studio-inbox";

const nullableString = z.string().nullable().optional();
const answer = z.record(z.string(), z.unknown()).nullable().optional();

const InboxItemSchema = z.object({
  id: z.string(),
  kind: z.string(),
  tab: z.string(),
  occurred_at: z.string(),
  project_id: nullableString,
  summary: z.string(),
  status: z.string(),
  source: z.object({ kind: z.string(), id: z.string() }),
  subject: z.object({ kind: z.string(), id: nullableString, key: nullableString }),
  proposed_by: nullableString,
  decision_type: nullableString,
  proposal_type: nullableString,
  decision_point_key: nullableString,
  level: z.number().nullable().optional(),
  resolution_record_id: nullableString,
  expires_at: nullableString,
  rule_answer: answer,
  ai_answer: answer,
  answers_truncated: z.boolean().optional(),
  evidence_refs: z.array(z.object({ kind: z.string(), id: z.string() })).optional(),
  actions: z.array(z.object({
    name: z.string(), operation: z.string(), path_params: z.record(z.string(), z.string()).optional(),
    body: z.record(z.string(), z.string()).optional(), allowed: z.boolean(),
  })).optional(),
  can_act: z.boolean(),
});
type RawInboxItem = z.infer<typeof InboxItemSchema>;
/** The API omits empty lists; the UI always gets arrays and maps. */
export type StudioInboxItem = Omit<RawInboxItem, "actions" | "evidence_refs"> & {
  evidence_refs: Array<{ kind: string; id: string }>;
  actions: Array<{ name: string; operation: string; path_params: Record<string, string>; body: Record<string, string>; allowed: boolean }>;
};
function normalize(item: RawInboxItem): StudioInboxItem {
  return {
    ...item,
    evidence_refs: item.evidence_refs ?? [],
    actions: (item.actions ?? []).map((a) => ({ ...a, path_params: a.path_params ?? {}, body: a.body ?? {} })),
  };
}
const InboxPageSchema = z.object({
  tab: z.string(),
  items: z.array(InboxItemSchema),
  next_cursor: nullableString,
  limit: z.number(),
  viewer: z.object({ is_agent: z.boolean(), can_decide: z.boolean(), can_approve_ai_policy: z.boolean() }),
});

export const INBOX_PAGE_SIZE = 20;

export function useInbox(tab: InboxTabId, pageSize: number = INBOX_PAGE_SIZE) {
  return useInfiniteQuery({
    queryKey: workspaceQueryKey("v1", "inbox", tab, pageSize),
    queryFn: ({ pageParam, signal }) => v1Get("/v1/inbox", InboxPageSchema, { query: { tab, cursor: pageParam ?? null, limit: pageSize }, signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 15_000,
    select: (data) => ({ ...data, pages: data.pages.map((page) => ({ ...page, items: page.items.map(normalize) })) }),
  });
}

const CountsSchema = z.object({ needs_decision: z.number(), applied_automatically: z.number(), done: z.number() });

/** One cheap read for the sidebar badge and the tab counts. With `poll` it refetches once a minute (the shell only). */
export function useInboxCounts(enabled = true, poll = false) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "inbox-counts"),
    queryFn: ({ signal }) => v1Get("/v1/inbox/counts", CountsSchema, { signal }),
    enabled,
    retry: false,
    staleTime: 30_000,
    // Only the shell polls (one timer); Home and the Inbox read the shared cache. An error stops the polling.
    refetchInterval: poll ? (query) => (query.state.status === "error" ? false : INBOX_BADGE_REFETCH_MS) : false,
    refetchIntervalInBackground: false,
  });
}

const ProposalSchema = z.object({ id: z.string(), status: z.string() });

/** Accept, reject or revert an agent / assistant proposal (the rationale is optional and recorded as plain text). */
export function decideProposal(input: { action: "accept" | "reject" | "revert"; proposalId: string; rationale: string; key: string; refVersions?: Record<string, number | null> }) {
  const body = {
    ...(input.rationale.trim() ? { rationale: input.rationale.trim() } : {}),
    // Accepting a proposal that moves refs needs the version seen of each ref (428 otherwise); extra kinds are ignored.
    ...(input.action === "accept" && input.refVersions ? { ref_versions: input.refVersions } : {}),
  };
  const options = { params: { proposal_id: input.proposalId }, idempotencyKey: input.key };
  if (input.action === "accept") return v1Post("/v1/proposals/{proposal_id}/accept", ProposalSchema, body, options);
  if (input.action === "reject") return v1Post("/v1/proposals/{proposal_id}/reject", ProposalSchema, body, options);
  return v1Post("/v1/proposals/{proposal_id}/revert", ProposalSchema, body, options);
}

/** After any inbox write: the lists, the counts and everything the decision touched are re-read. */
export function useInboxInvalidation() {
  const client = useQueryClient();
  return () => {
    for (const name of ["inbox", "inbox-counts", "activity", "project-refs", "graph", "decisions", "experiments", "experiment", "model-version", "decision", "projects"]) {
      void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", name) });
    }
  };
}
