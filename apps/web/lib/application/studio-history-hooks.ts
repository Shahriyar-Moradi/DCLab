"use client";

/**
 * History reads (V7 UI-E): one page (the newest 100) of a project's activity, for the events History lists beside its
 * decision entries (uploads, final tests, drift alerts, weekly scorings). Read-only; the server builds the items from typed
 * fields, and the page shows its own fixed sentences, never the server's summary.
 */
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";

const nullableString = z.string().nullable().optional();
const ActivityEventSchema = z.object({
  id: z.string(),
  kind: z.string(),
  occurred_at: z.string(),
  status: nullableString,
  actor: z.object({ kind: z.string(), is_you: z.boolean().optional() }),
  subject: z.object({ kind: z.string(), id: nullableString }),
  link: z.object({ kind: z.string(), id: z.string() }),
});
const ActivityPageSchema = z.object({ items: z.array(ActivityEventSchema), next_cursor: nullableString, limit: z.number() });
export type StudioActivityEvent = z.infer<typeof ActivityEventSchema>;

export const HISTORY_ACTIVITY_LIMIT = 100;

/** The newest 100 activity items of one project (`next_cursor` set means older ones exist). */
export function useProjectActivity(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "activity", "project", projectId),
    queryFn: ({ signal }) => v1Get("/v1/activity", ActivityPageSchema, { query: { project_id: projectId!, limit: HISTORY_ACTIVITY_LIMIT }, signal }),
    enabled: isUuid(projectId),
    retry: false,
    staleTime: 30_000,
  });
}
