"use client";

/** Home reads (P4.15-UI): the activity feed (cursor paged) and the governance read for the AI-health and spend cards. */
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get } from "@/lib/infrastructure/v1/client";
import { aiIsOff } from "./studio-home";

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
  /** `run_finished` with locked evidence: the winner's family and CV score (never a final test set value). */
  run_result: z.object({
    model_family: nullableString, algorithm: nullableString, metric: z.string(), cv_score: z.number(), metric_scope: z.string().optional(),
  }).nullable().optional(),
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
const IncidentSchema = z.object({
  id: z.string(), kind: z.string(), status: z.string(), opened_at: z.string(),
  // D6b (AI settings page): problem reports with their plain-text resolution.
  subject_kind: z.string().optional(), subject_key: z.string().optional(), action: z.string().optional(),
  evidence: z.record(z.string(), z.unknown()).optional(), resolved_at: nullableString, resolved_by: nullableString, resolution: nullableString,
});
const LevelSchema = z.object({
  key: z.string(), cap: z.number(), workspace_level: z.number(), platform_level: z.number(), effective_level: z.number(),
  open_incidents: z.record(z.string(), z.number()).optional(),
  tested: z.object({ agreement_rate: z.number(), n: z.number(), run_id: z.string(), recorded_at: z.string() }).nullable().optional(),
});
const FieldChangeSchema = z.object({ path: z.string(), before: z.unknown().optional(), after: z.unknown().optional() });
const GovernanceSchema = z.object({
  ai_enabled_setting: z.boolean(),
  policy_unavailable: nullableString,
  spend: z.object({ currency: z.string(), workspace: z.array(BudgetPeriodSchema), per_run_limits_micros: z.record(z.string(), z.number()).optional() }).nullable().optional(),
  open_incidents: z.array(IncidentSchema),
  switches: z.object({
    platform_ai_blocking: z.string().nullable(),
    workspace: z.array(z.object({ switch_key: z.string(), state: z.string(), held_by_incident: z.boolean(), reason: z.string().optional(), changed_by: z.string().optional(), changed_at: z.string().optional() })),
  }),
  // D6b (assistant beta + AI settings); optional so a stubbed or older read still parses.
  viewer: z.object({ can_approve: z.boolean(), can_propose: z.boolean(), can_switch_off: z.boolean() }).optional(),
  policy: z.object({ digest: z.string(), workspace_version: z.number().nullable().optional(), document: z.record(z.string(), z.unknown()) }).nullable().optional(),
  policy_head: z.record(z.string(), z.unknown()).nullable().optional(),
  sole_approver: z.boolean().optional(),
  data_classes: z.object({ max_class: z.string(), sample_values_per_column: z.number() }).nullable().optional(),
  levels: z.array(LevelSchema).optional(),
  policy_changes: z.array(z.object({
    id: z.string(), version: z.number(), state: z.string(), change_kind: z.string(), open: z.boolean(), rationale: z.string(), policy_digest: z.string(),
    proposed_by: nullableString, created_at: z.string(), diff_vs_effective: z.array(FieldChangeSchema).nullable().optional(), consent_change: z.boolean().optional(),
    diff_vs_head: z.array(FieldChangeSchema).nullable().optional(),
  })).optional(),
  recent_changes: z.array(z.object({ kind: z.string(), id: z.string(), at: z.string(), subject: z.string(), state: z.string(), detail: z.string(), rationale: z.string(), actor: nullableString })).optional(),
  assistant_beta: z.boolean().optional(),
  model_in_use: z.object({ model: z.string(), provider: nullableString, price_is_upper_bound: z.boolean() }).nullable().optional(),
  month_spend: z.object({ period_start: z.string(), spent_micros: z.number(), calls: z.number(), price_is_upper_bound: z.boolean() }).nullable().optional(),
  incidents: z.array(IncidentSchema).optional(),
  provider_notice: z.string().optional(),
});
export type StudioGovernance = z.infer<typeof GovernanceSchema>;

export function useGovernance(enabled = true) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "governance"),
    queryFn: ({ signal }) => v1Get("/v1/governance", GovernanceSchema, { signal }),
    enabled,
    retry: false,
    staleTime: 60_000,
  });
}

/** True when AI is known to be switched off, false when known on, null while unknown (loading, or this role cannot read the settings). */
export function useAiOff(): boolean | null {
  const governance = useGovernance();
  return governance.data ? aiIsOff(governance.data) : null;
}
