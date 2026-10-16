"use client";

/** Settings read (V7-B6): GET /v1/workspaces/{workspace_id}/members (people only; owners and administrators). */
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";

const MembersSchema = z.object({
  members: z.array(z.object({ full_name: z.string(), email: z.string(), role: z.string(), joined_at: z.string(), is_you: z.boolean(), legacy_access: z.boolean().optional() })),
  more: z.boolean(),
  roles: z.array(z.object({ role: z.string(), can_read: z.boolean(), can_run_ml: z.boolean(), can_manage_members: z.boolean(), can_approve_ai_policy: z.boolean() })),
});
export type StudioMembers = z.infer<typeof MembersSchema>;

/** `enabled` is false for people who may not see the list (the API would answer 403 anyway). */
export function useWorkspaceMembers(workspaceId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "members", workspaceId),
    enabled: enabled && isUuid(workspaceId),
    queryFn: ({ signal }) => v1Get("/v1/workspaces/{workspace_id}/members", MembersSchema, { params: { workspace_id: workspaceId! }, signal }),
    staleTime: 30_000,
    retry: false,
  });
}

const UsageSchema = z.object({
  plan: z.string(),
  quotas: z.array(z.object({
    key: z.string(), limit: z.number().nullable(), over: z.boolean(), percent: z.number().nullable(), period: z.string(),
    resets_at: z.string().nullable(), used: z.number(), warning: z.boolean(), enforced: z.boolean(), unit: z.string(),
  })),
  ai_spend: z.object({
    currency: z.string(), hard_stop: z.boolean(), limit_micros: z.number(), over: z.boolean(), percent: z.number().nullable(),
    period_start: z.string(), reserved_micros: z.number(), spent_micros: z.number(), warning: z.boolean(),
  }).nullable(),
  daily: z.array(z.object({ day: z.string(), compute_seconds: z.number() })),
  disclaimer: z.string(),
  notes: z.array(z.string()),
  resets_at: z.string(),
  quotas_enforced: z.boolean(),
  metering_enabled: z.boolean(),
  measured_at: z.string().nullable(),
});
export type StudioUsage = z.infer<typeof UsageSchema>;

/** GET /v1/workspaces/{workspace_id}/usage (owners, administrators and ML roles; others get 403 `usage_not_permitted`). */
export function useWorkspaceUsage(workspaceId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "usage", workspaceId),
    enabled: enabled && isUuid(workspaceId),
    queryFn: ({ signal }) => v1Get("/v1/workspaces/{workspace_id}/usage", UsageSchema, { params: { workspace_id: workspaceId! }, signal }),
    staleTime: 30_000,
    retry: false,
  });
}
