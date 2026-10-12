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
