"use client";

/**
 * Compare, branch, cancel, champion ref move and decision transitions (P4.4-A).
 * Every write carries an Idempotency-Key from the caller (one per user action); ref moves carry
 * If-Match (or If-None-Match: * for a missing ref). Role gating in the UI is a convenience: the API decides.
 */
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { ifMatch, v1Get, v1Post } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";
import type { BranchBody } from "./studio-compare";

const nullableString = z.string().nullable().optional();
const nullableNumber = z.number().nullable().optional();
const anyRecord = z.record(z.string(), z.unknown());

/** The comparison read WITHOUT its `holdout` sections: the compare page shows cross-validation only. */
const CompareSchema = z.object({
  split_plan_id: z.string(),
  common: z.object({ cv: z.array(z.string()) }),
  experiments: z.array(z.object({
    experiment_id: z.string(),
    parent_experiment_id: nullableString,
    family: nullableString,
    candidate_id: nullableString,
    selection_metric: nullableString,
    selected_score: nullableNumber,
    constraint_status: nullableString,
    decision_threshold: nullableNumber,
    cv: z.record(z.string(), z.number()).optional(),
  })),
});
export type StudioComparison = z.infer<typeof CompareSchema>;

export function useCompare(ids: string[] | null) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "compare", ...(ids ?? [])),
    queryFn: ({ signal }) => v1Get("/v1/experiments/compare", CompareSchema, { query: { ids: ids!.join(",") }, signal }),
    enabled: !!ids,
    retry: false,
  });
}

const DecisionDetailSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  decision_type: z.string(),
  state: z.string(),
  effective_state: z.string(),
  recorded_at: z.string(),
  rationale: z.string(),
  rationale_untrusted: z.boolean().optional(),
  rationale_truncated: z.boolean().optional(),
  actor: z.object({ kind: z.string(), rule: nullableString, user_id: nullableString, agent_run_id: nullableString, service_token_id: nullableString }),
  subject: z.object({ kind: z.string(), id: z.string() }),
  evidence_refs: z.array(z.object({ kind: z.string(), id: z.string(), scope: nullableString, metric: nullableString })).optional(),
  facts: anyRecord.optional(),
  details: anyRecord.optional(),
  details_truncated: z.boolean(),
  supersedes_id: nullableString,
  superseded_by_id: nullableString,
});
export type StudioDecisionDetail = z.infer<typeof DecisionDetailSchema>;

export function useDecisionDetail(decisionId: string | null | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "decision", decisionId),
    queryFn: ({ signal }) => v1Get("/v1/decisions/{decision_id}", DecisionDetailSchema, { params: { decision_id: decisionId! }, signal }),
    enabled: isUuid(decisionId),
    retry: false,
  });
}

const CreatedSchema = z.object({ id: z.string() });
const ExperimentSchema = z.object({ id: z.string(), status: z.string() });
const RefsMovedSchema = z.object({ decision: z.object({ id: z.string() }), refs: z.array(z.object({ ref_kind: z.string(), version: z.number() })) });

export function createBranch(input: { parentId: string; body: BranchBody; key: string }) {
  return v1Post("/v1/experiments/{experiment_id}/branches", ExperimentSchema, input.body, { params: { experiment_id: input.parentId }, idempotencyKey: input.key });
}

export function cancelExperiment(input: { experimentId: string; key: string }) {
  return v1Post("/v1/experiments/{experiment_id}/cancel", ExperimentSchema, undefined as never, { params: { experiment_id: input.experimentId }, idempotencyKey: input.key });
}

export type RefVersion = { ref_kind: string; version: number } | undefined;

/**
 * Promote a model version: one accepted `champion_promoted` record. The champion cites its own final
 * evaluation by candidate id (identity only; the API checks it and refuses anything else), moves the feature
 * recipe with it, and `If-Match` is the champion ref's version (If-None-Match: * when there is none yet).
 */
export function makeChampion(input: {
  projectId: string; modelVersionId: string; candidateId: string; featureRecipeId: string | null;
  champion: RefVersion; recipe: RefVersion; rationale: string; key: string; proposalId?: string;
}) {
  return v1Post(
    "/v1/projects/{project_id}/refs/{ref_kind}",
    RefsMovedSchema,
    {
      target_id: input.modelVersionId,
      rationale: input.rationale,
      evidence_refs: [{ kind: "candidate", id: input.candidateId, scope: "final_holdout" }],
      companion_moves: input.featureRecipeId ? [{ ref_kind: "feature_recipe", target_id: input.featureRecipeId, expected_version: input.recipe?.version ?? null }] : [],
      ...(input.proposalId ? { proposal_id: input.proposalId } : {}),
    },
    {
      params: { project_id: input.projectId, ref_kind: "champion_model" },
      idempotencyKey: input.key,
      headers: input.champion ? ifMatch(input.champion.version) : { "If-None-Match": "*" },
    },
  );
}

/**
 * Accept a proposed ref move (an agent's `ref_moved` / `champion_promoted` proposal): the refs endpoint with the
 * proposal id, the proposal's own moves and the refs' current versions. A champion's final-evaluation evidence is
 * attached and re-checked by the service; the record cites the proposal.
 */
export function acceptRefProposal(input: {
  projectId: string; proposalId: string; moves: Array<{ refKind: string; targetId: string }>;
  refs: Array<{ ref_kind: string; version: number }>; rationale: string; key: string;
}) {
  const primary = input.moves.find((m) => m.refKind === "champion_model") ?? input.moves[0];
  const version = (kind: string) => input.refs.find((r) => r.ref_kind === kind)?.version ?? null;
  const current = version(primary.refKind);
  return v1Post(
    "/v1/projects/{project_id}/refs/{ref_kind}",
    RefsMovedSchema,
    {
      target_id: primary.targetId,
      rationale: input.rationale,
      evidence_refs: [{ kind: "decision_record", id: input.proposalId }],
      companion_moves: input.moves.filter((m) => m !== primary).map((m) => ({ ref_kind: m.refKind as "feature_recipe", target_id: m.targetId, expected_version: version(m.refKind) })),
      proposal_id: input.proposalId,
    },
    {
      params: { project_id: input.projectId, ref_kind: primary.refKind as "champion_model" },
      idempotencyKey: input.key,
      headers: current === null ? { "If-None-Match": "*" } : ifMatch(current),
    },
  );
}

export function resolveDecision(input: { action: "accept" | "reject"; decisionId: string; rationale: string; key: string }) {
  const body = { rationale: input.rationale };
  const options = { idempotencyKey: input.key };
  return input.action === "accept"
    ? v1Post("/v1/decisions/{decision_id}/accept", CreatedSchema, body, { params: { decision_id: input.decisionId }, ...options })
    : v1Post("/v1/decisions/{decision_id}/reject", CreatedSchema, body, { params: { decision_id: input.decisionId }, ...options });
}

/** Supersede = correct: a new record of the same type and subject; the original stays, marked superseded. */
export function supersedeDecision(input: { decisionId: string; rationale: string; key: string }) {
  return v1Post("/v1/decisions/{decision_id}/supersede", CreatedSchema, { rationale: input.rationale }, { params: { decision_id: input.decisionId }, idempotencyKey: input.key });
}

/** After any write: refs, graph, decisions and experiment reads are re-fetched. */
export function useWriteInvalidation() {
  const client = useQueryClient();
  return () => {
    for (const name of ["project-refs", "graph", "decisions", "experiments", "experiment", "experiment-detail", "model-version", "compare", "decision"]) {
      void client.invalidateQueries({ queryKey: workspaceQueryKey("v1", name) });
    }
  };
}
