"use client";

/**
 * Operating points (P5.2-UI): `GET /v1/experiments/{id}/operating-points` and the human-only
 * `POST /v1/experiments/{id}/operating-point`. Every figure is an out-of-fold training-fold figure; the read
 * carries no final-evaluation value. Scoring keeps the locked threshold: a choice is a recorded decision.
 */
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get, v1Post } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";

const nullableString = z.string().nullable().optional();
const nullableNumber = z.number().nullable().optional();
const interval = z.object({ low: z.number(), high: z.number() }).nullable().optional();

const PointSchema = z.object({
  threshold: z.number(), tp: z.number(), fp: z.number(), fn: z.number(), tn: z.number(),
  precision: z.number(), recall: z.number(), specificity: z.number(), f1: z.number(), accuracy: z.number(),
  balanced_accuracy: z.number(), flagged_share: z.number(), expected_cost: nullableNumber,
});
const DetailSchema = PointSchema.extend({
  precision_interval: interval, recall_interval: interval,
  fold_spread: z.object({
    folds: z.number(), recall_folds: z.number(), precision_folds: z.number(), min_fold_positives: z.number(), min_fold_flagged: z.number(),
    min_denominator: z.number(), includes_folds_outside_curve: z.boolean(),
    recall_min: nullableNumber, recall_max: nullableNumber, precision_min: nullableNumber, precision_max: nullableNumber,
  }).nullable().optional(),
  what_this_means: z.string().optional(),
});
const ConstraintSchema = z.object({ metric: z.string(), op: z.string(), value: z.number() });
const ObjectiveSchema = z.object({
  goal: z.string(), constraints: z.array(ConstraintSchema).optional(), cost_false_positive: nullableNumber, cost_false_negative: nullableNumber,
});
const ChosenSchema = z.object({
  decision_id: z.string(), threshold: z.number(), method: z.enum(["threshold", "objective"]), objective: ObjectiveSchema.nullable().optional(),
  rationale: z.string(), chosen_by_user_id: nullableString, recorded_at: z.string(), supersedes_id: nullableString,
  point: DetailSchema.nullable().optional(), curve_changed: z.boolean().nullable().optional(), applies_to_scoring: z.boolean().optional(),
});
const ScoringSchema = z.object({ threshold: nullableNumber, uses: z.string().optional(), note: z.string().optional() });

const OperatingPointsSchema = z.object({
  experiment_id: z.string(),
  status: z.enum(["available", "not_applicable", "not_available", "not_evaluated"]),
  reason: nullableString,
  message: z.string(),
  task_type: nullableString,
  version: nullableString,
  oof_folds: nullableString,
  rows: nullableNumber, positives: nullableNumber, negatives: nullableNumber,
  min_class_rows: z.number(),
  cost_matrix: z.object({ false_positive: z.number(), false_negative: z.number() }).nullable().optional(),
  points: z.array(PointSchema).optional(),
  pareto: z.array(DetailSchema).optional(),
  locked: z.object({
    threshold: nullableNumber, source: nullableString, constraint_status: nullableString, reproduced_from_curve: z.boolean().nullable().optional(),
    point: DetailSchema.nullable().optional(), note: z.string().optional(),
  }).nullable().optional(),
  chosen: ChosenSchema.nullable().optional(),
  scoring: ScoringSchema.nullable().optional(),
  tie_break: z.string(),
  optimism_note: z.string().optional(),
  final_evaluation_note: z.string().optional(),
});
export type StudioOperatingPoints = z.infer<typeof OperatingPointsSchema>;
export type OperatingPoint = z.infer<typeof PointSchema>;
export type OperatingDetail = z.infer<typeof DetailSchema>;
export type OperatingChosen = z.infer<typeof ChosenSchema>;

const ChoiceSchema = z.object({
  experiment_id: z.string(),
  chosen: ChosenSchema,
  decision: z.object({ id: z.string() }).passthrough(),
  scoring: ScoringSchema,
});

export function operatingKey(experimentId: string | null | undefined) {
  return workspaceQueryKey("v1", "operating-points", experimentId);
}

export function useOperatingPoints(experimentId: string | null | undefined, enabled = true) {
  return useQuery({
    queryKey: operatingKey(experimentId),
    queryFn: ({ signal }) => v1Get("/v1/experiments/{experiment_id}/operating-points", OperatingPointsSchema, { params: { experiment_id: experimentId! }, signal }),
    enabled: enabled && isUuid(experimentId),
    retry: false,
  });
}

export type ChoiceBody = {
  threshold?: number;
  objective?: { goal: string; constraints: Array<{ metric: string; op: ">=" | "<="; value: number }> };
  reason: string;
};

export function chooseOperatingPoint(input: { experimentId: string; body: ChoiceBody; key: string }) {
  return v1Post("/v1/experiments/{experiment_id}/operating-point", ChoiceSchema, input.body as never, {
    params: { experiment_id: input.experimentId }, idempotencyKey: input.key,
  });
}
