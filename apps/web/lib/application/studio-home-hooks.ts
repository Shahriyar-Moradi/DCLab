"use client";

/** Home reads (P4.15-UI): the activity feed (cursor paged) and the governance read for the AI-health and spend cards. */
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get } from "@/lib/infrastructure/v1/client";

const nullableString = z.string().nullable().optional();

const ActivityItemSchema = z.object({
  id: z.string(),
  kind: z.string(),
  occurred_at: z.string(),
  project_id: nullableString,
  actor: z.object({ kind: z.string(), rule: nullableString, agent_key: nullableString, agent_run_id: nullableString, is_you: z.boolean().optional() }),
  subject: z.object({ kind: z.string(), id: nullableString, key: nullableString }),
  summary: z.string(),
  status: nullableString,
  decision_type: nullableString,
  link: z.object({ kind: z.string(), id: z.string() }),
});
export type StudioActivityItem = z.infer<typeof ActivityItemSchema>;
const ActivityPageSchema = z.object({ items: z.array(ActivityItemSchema), next_cursor: nullableString, limit: z.number() });

export const ACTIVITY_PAGE_SIZE = 10;

/** Newest first; "Load more" asks for the next cursor. A page is never re-fetched on its own (no storm). */
export function useActivity() {
  return useInfiniteQuery({
    queryKey: workspaceQueryKey("v1", "activity"),
    queryFn: ({ pageParam, signal }) => v1Get("/v1/activity", ActivityPageSchema, { query: { cursor: pageParam ?? null, limit: ACTIVITY_PAGE_SIZE }, signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    staleTime: 30_000,
  });
}

const BudgetPeriodSchema = z.object({
  currency: z.string(), hard_stop: z.boolean(), limit_micros: z.number(), period: z.string(), scope: z.string(),
  spent_micros: z.number(), reserved_micros: z.number(), calls: z.number(),
});
export type StudioBudgetPeriod = z.infer<typeof BudgetPeriodSchema>;
const GovernanceSchema = z.object({
  ai_enabled_setting: z.boolean(),
  policy_unavailable: nullableString,
  spend: z.object({ currency: z.string(), workspace: z.array(BudgetPeriodSchema) }).nullable().optional(),
  open_incidents: z.array(z.object({ id: z.string(), kind: z.string(), status: z.string(), opened_at: z.string() })),
  switches: z.object({ platform_ai_blocking: z.string().nullable(), workspace: z.array(z.object({ switch_key: z.string(), state: z.string(), held_by_incident: z.boolean() })) }),
});
export type StudioGovernance = z.infer<typeof GovernanceSchema>;

export function useGovernance() {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "governance"),
    queryFn: ({ signal }) => v1Get("/v1/governance", GovernanceSchema, { signal }),
    retry: false,
    staleTime: 60_000,
  });
}
