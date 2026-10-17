"use client";

/**
 * D8b: Settings → Notifications and Integrations reads and writes (signed-in sessions only; the API refuses any bearer).
 * `GET/PUT /v1/me/notification-preferences` (your own, in the selected workspace) and the workspace's webhooks
 * (`GET/POST /v1/workspaces/{id}/webhooks`, `PATCH .../{webhook_id}`, `POST .../rotate-secret`, `POST .../test`,
 * `GET .../deliveries`; owners and administrators only). A create / rotate answer carries the signing secret: it is returned
 * to the caller and never put in the query cache (the list read never carries it).
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get, v1Patch, v1Post, v1Put } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";

const nullableString = z.string().nullable().optional();

const PreferencesSchema = z.object({
  workspace_id: z.string(),
  email_delivery: z.string(),
  email_verified: z.boolean(),
  items: z.array(z.object({ kind: z.string(), email_enabled: z.boolean(), default: z.boolean(), applies_to_you: z.boolean() })),
});
export type StudioPreferences = z.infer<typeof PreferencesSchema>;

const WebhookSchema = z.object({
  id: z.string(), workspace_id: z.string(), name: z.string(), url: z.string(), events: z.array(z.string()), enabled: z.boolean(),
  secret_version: z.number(), created_at: z.string(), updated_at: z.string(), secret_rotated_at: nullableString,
  consecutive_failures: z.number().optional(), paused_until: nullableString, last_delivery_at: nullableString,
  last_delivery_outcome: nullableString, last_error_code: nullableString,
});
export type StudioWebhook = z.infer<typeof WebhookSchema>;
const WebhookSecretSchema = WebhookSchema.extend({ secret: z.string().nullable() });
const DeliverySchema = z.object({
  id: z.string(), delivery_id: z.string(), event: z.string(), attempt: z.number(), outcome: z.string(), status_code: z.number().nullable().optional(),
  error_code: nullableString, duration_ms: z.number(), created_at: z.string(),
});
const TestSchema = z.object({ outbox_id: z.string(), status: z.string(), note: z.string() });

export function useNotificationPreferences(enabled: boolean) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "notification-preferences"),
    enabled,
    queryFn: ({ signal }) => v1Get("/v1/me/notification-preferences", PreferencesSchema, { signal }),
    retry: false,
    staleTime: 30_000,
  });
}

export async function saveNotificationPreferences(items: Array<{ kind: string; email_enabled: boolean }>, key: string): Promise<StudioPreferences> {
  type Kind = "decision_needed" | "run_finished" | "run_failed" | "scheduled_scoring_result" | "drift_alert" | "quota_warning" | "question_asked" | "question_answered";
  return v1Put("/v1/me/notification-preferences", PreferencesSchema, { items: items.map((i) => ({ kind: i.kind as Kind, email_enabled: i.email_enabled })) }, { idempotencyKey: key });
}

export function useWebhooks(workspaceId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "webhooks", workspaceId),
    enabled: enabled && isUuid(workspaceId),
    queryFn: ({ signal }) => v1Get("/v1/workspaces/{workspace_id}/webhooks", z.array(WebhookSchema), { params: { workspace_id: workspaceId! }, signal }),
    retry: false,
    staleTime: 15_000,
  });
}

export function useWebhookDeliveries(workspaceId: string | null, webhookId: string | null) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "webhook-deliveries", workspaceId, webhookId),
    enabled: isUuid(workspaceId) && isUuid(webhookId),
    queryFn: ({ signal }) => v1Get("/v1/workspaces/{workspace_id}/webhooks/{webhook_id}/deliveries", z.array(DeliverySchema), { params: { workspace_id: workspaceId!, webhook_id: webhookId! }, signal }),
    retry: false,
    staleTime: 5_000,
  });
}

type EventId = "decision_needed" | "run_finished" | "run_failed" | "scheduled_scoring_result" | "drift_alert" | "quota_warning";

/** Returns the answer with its one-time `secret`; the caller shows it once and drops it. */
export function createWebhook(workspaceId: string, body: { name: string; url: string; events: string[] }, key: string) {
  return v1Post("/v1/workspaces/{workspace_id}/webhooks", WebhookSecretSchema, { name: body.name, url: body.url, events: body.events as EventId[], enabled: true }, { params: { workspace_id: workspaceId }, idempotencyKey: key });
}

export function rotateWebhookSecret(workspaceId: string, webhookId: string, key: string) {
  return v1Post("/v1/workspaces/{workspace_id}/webhooks/{webhook_id}/rotate-secret", WebhookSecretSchema, undefined as never, { params: { workspace_id: workspaceId, webhook_id: webhookId }, idempotencyKey: key });
}

export function testWebhook(workspaceId: string, webhookId: string, key: string) {
  return v1Post("/v1/workspaces/{workspace_id}/webhooks/{webhook_id}/test", TestSchema, undefined as never, { params: { workspace_id: workspaceId, webhook_id: webhookId }, idempotencyKey: key });
}

export function setWebhookEnabled(workspaceId: string, webhookId: string, enabled: boolean, key: string) {
  return v1Patch("/v1/workspaces/{workspace_id}/webhooks/{webhook_id}", WebhookSchema, { enabled }, { params: { workspace_id: workspaceId, webhook_id: webhookId }, idempotencyKey: key });
}

/** After a write: the list and every delivery log are read again (never with a secret). */
export function useWebhookInvalidation() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ predicate: (q) => q.queryKey.includes("webhooks") || q.queryKey.includes("webhook-deliveries") });
  };
}
