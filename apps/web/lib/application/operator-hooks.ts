"use client";

/**
 * Operator console reads (P8.5-UI): `GET /v1/operator/overview`, `/workspaces` and `/failed-jobs` (cursor paged).
 * Platform administrators only (the API answers 403 to everyone else); signed-in session only; read-only (no write
 * exists). The reads are cross-tenant, so their cache keys are not tied to the active workspace.
 */
import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { v1Get } from "@/lib/infrastructure/v1/client";

const CheckSchema = z.object({
  name: z.string(), status: z.string(), required: z.boolean(), code: z.string().nullable().optional(),
});
const OverviewSchema = z.object({
  generated_at: z.string(),
  workspaces: z.object({ total: z.number(), active_last_7d: z.number() }),
  jobs_last_24h: z.array(z.object({ job_type: z.string(), status: z.string(), count: z.number() })),
  queue: z.object({ depth: z.number(), oldest_age_seconds: z.number().nullable() }),
  workers: z.object({ dispatcher: z.string(), count: z.number(), heartbeat_age_seconds: z.array(z.number()), visible: z.boolean() }),
  failed_jobs_last_24h: z.array(z.object({ code: z.string(), count: z.number() })),
  failed_jobs_last_24h_truncated: z.boolean(),
  platform_ai_caps: z.object({
    ai_enabled: z.boolean(), max_data_class: z.string(), sample_values_per_column: z.number(), user_text_to_jev: z.boolean(),
    auto_release: z.boolean(), model_allowlist: z.array(z.string()), budgets: z.record(z.string(), z.union([z.number(), z.boolean()])),
  }),
  readiness: z.object({ status: z.string(), checks: z.array(CheckSchema) }),
});
export type OperatorOverview = z.infer<typeof OverviewSchema>;

const WorkspacePageSchema = z.object({
  items: z.array(z.object({
    id: z.string(), name: z.string(), kind: z.string(), created_at: z.string(), member_count: z.number(), project_count: z.number(),
    jobs_last_30d: z.number(), failed_jobs_last_24h: z.number(), active_last_7d: z.boolean(),
  })),
  next_cursor: z.string().nullable(),
});

const FailedJobPageSchema = z.object({
  items: z.array(z.object({
    id: z.string(), workspace_id: z.string(), job_type: z.string(), handler_key: z.string(), code: z.string(), attempts: z.number(), failed_at: z.string(),
  })),
  next_cursor: z.string().nullable(),
});

export const OPERATOR_PAGE_SIZE = 50;
const KEY = "operator";

export function useOperatorOverview(enabled: boolean) {
  return useQuery({
    queryKey: [KEY, "overview"],
    queryFn: ({ signal }) => v1Get("/v1/operator/overview", OverviewSchema, { signal }),
    enabled,
    retry: false,
    staleTime: 30_000,
  });
}

/** Oldest first; "Show more" asks for the next cursor. */
export function useOperatorWorkspaces(enabled: boolean) {
  return useInfiniteQuery({
    queryKey: [KEY, "workspaces"],
    queryFn: ({ pageParam, signal }) => v1Get("/v1/operator/workspaces", WorkspacePageSchema, { query: { cursor: pageParam, limit: OPERATOR_PAGE_SIZE }, signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled,
    retry: false,
    staleTime: 30_000,
  });
}

/** Newest first; "Show more" asks for the next cursor. */
export function useOperatorFailedJobs(enabled: boolean) {
  return useInfiniteQuery({
    queryKey: [KEY, "failed-jobs"],
    queryFn: ({ pageParam, signal }) => v1Get("/v1/operator/failed-jobs", FailedJobPageSchema, { query: { cursor: pageParam, limit: OPERATOR_PAGE_SIZE }, signal }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled,
    retry: false,
    staleTime: 30_000,
  });
}

/** Drop every loaded page of one list and read it again from the first page (after the API refused a cursor). */
export function useOperatorListReset(list: "workspaces" | "failed-jobs") {
  const client = useQueryClient();
  return () => void client.resetQueries({ queryKey: [KEY, list] });
}
