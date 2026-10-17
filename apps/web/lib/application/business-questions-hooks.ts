"use client";

/**
 * Questions to the business (D7b): reads and the three writes. Signed-in sessions only (the API refuses any bearer).
 * Every write sends an Idempotency-Key per action (a retry of the same request reuses it); closing also sends
 * `If-Match` with the question's version. Text in these reads is untrusted (see `business-questions.ts`).
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { ifMatch, v1Get, v1Post } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";
import type { SubjectLike } from "./business-questions";

const nullableString = z.string().nullable().optional();
const PersonSchema = z.object({ is_you: z.boolean().optional(), side: z.string() });
const SubjectSchema = z.object({ kind: z.string(), id: z.string() });
const QuestionSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  project_name: z.string(), // untrusted: shown with plainText
  subject: SubjectSchema.nullable().optional(),
  text: z.string(),
  status: z.string(), // an unknown value reads as "State not known", it must not break the list
  asked_by: PersonSchema,
  reply_count: z.number(),
  last_reply_at: nullableString,
  created_at: z.string(),
  updated_at: z.string(),
  closed_at: nullableString,
  closed_by: PersonSchema.nullable().optional(),
  version: z.number(),
  can_reply: z.boolean(),
  can_close: z.boolean(),
  you_reply_as: z.string().nullable().optional(), // "business" | "data_team" | null; anything else reads as null
});
const ReplySchema = z.object({ id: z.string(), text: z.string(), author: PersonSchema, created_at: z.string() });
const DetailSchema = QuestionSchema.extend({ replies: z.array(ReplySchema) });
const ListSchema = z.object({ items: z.array(QuestionSchema), has_more: z.boolean(), limit: z.number() });
export type BusinessQuestionDetail = z.infer<typeof DetailSchema>;

const LIST = "business-questions";
const DETAIL = "business-question";

/** The Business view: questions to this workspace's business users, all projects, open first. */
export function useWorkspaceQuestions(enabled: boolean) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", LIST, "workspace"),
    queryFn: ({ signal }) => v1Get("/v1/questions", ListSchema, { query: { limit: 100 }, signal }),
    enabled,
    retry: false,
  });
}

/** One project's questions (same order). */
export function useProjectQuestions(projectId: string | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", LIST, projectId),
    queryFn: ({ signal }) => v1Get("/v1/projects/{project_id}/questions", ListSchema, { params: { project_id: projectId! }, query: { limit: 100 }, signal }),
    enabled: isUuid(projectId),
    retry: false,
  });
}

/** One question with its replies (oldest first); read when its conversation is opened. */
export function useQuestion(questionId: string | null) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", DETAIL, questionId),
    queryFn: ({ signal }) => v1Get("/v1/questions/{question_id}", DetailSchema, { params: { question_id: questionId! }, signal }),
    enabled: isUuid(questionId),
    retry: false,
  });
}

export function askQuestion(input: { projectId: string; text: string; subject: SubjectLike | null; key: string }) {
  const subject = input.subject ? { kind: input.subject.kind as "model_version" | "batch_prediction" | "monitoring_window", id: input.subject.id } : null;
  return v1Post("/v1/projects/{project_id}/questions", DetailSchema, { text: input.text, subject }, {
    params: { project_id: input.projectId }, idempotencyKey: input.key,
  });
}

export function replyToQuestion(input: { questionId: string; text: string; key: string }) {
  return v1Post("/v1/questions/{question_id}/replies", DetailSchema, { text: input.text }, {
    params: { question_id: input.questionId }, idempotencyKey: input.key,
  });
}

export function closeQuestion(input: { questionId: string; version: number; key: string }) {
  return v1Post("/v1/questions/{question_id}/close", DetailSchema, undefined as never, {
    params: { question_id: input.questionId }, idempotencyKey: input.key, headers: ifMatch(input.version),
  });
}

/**
 * After a write: show the returned question at once (when there is one), then re-read the question, the lists, the
 * Inbox and the activity. Also used after a stale close (409 / 412) or a 2xx whose body could not be read.
 */
export function useQuestionWrites() {
  const client = useQueryClient();
  return (detail: BusinessQuestionDetail | null, questionId?: string) => {
    if (detail) client.setQueryData(workspaceQueryKey("v1", DETAIL, detail.id), detail);
    else if (questionId) void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", DETAIL, questionId) });
    for (const name of [LIST, "inbox", "inbox-counts", "activity"]) void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", name) });
  };
}
