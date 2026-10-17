"use client";

/**
 * Improve (V7-B4): the comparison verdict of a try against the run it is based on
 * (`GET /v1/experiments/{id}/verdict`). Cross-validation numbers and counts only; the read carries no final test set value.
 * The schema ignores fields it does not know, so the API can add fields without breaking this page.
 */
import { useQueries } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";

const nullableString = z.string().nullable().optional();
const nullableNumber = z.number().nullable().optional();

const VerdictSchema = z.object({
  experiment_id: z.string(),
  against_experiment_id: nullableString,
  against_is_parent: z.boolean().nullable().optional(),
  split_plan_id: nullableString,
  verdict: z.string(),
  reason: nullableString,
  message: z.string().optional(),
  metric: nullableString,
  direction: nullableString,
  folds: nullableNumber,
  mean_difference: nullableNumber,
  margin: nullableNumber,
  minimum_effect: nullableNumber,
  comparisons_on_plan: nullableNumber,
  comparisons: nullableNumber,
  /** k, the new models this try added. */
  new_candidates: nullableNumber,
  fold_scores: z.array(z.object({ fold_number: z.number(), experiment: z.number(), against: z.number(), difference: z.number() })).nullable().optional(),
  test_set_looks: z.object({ split_plan_id: nullableString, looks_on_plan: nullableNumber, experiment_look: nullableNumber, against_look: nullableNumber }).nullable().optional(),
});
export type StudioVerdict = z.infer<typeof VerdictSchema>;

/** One verdict read per finished try (against its parent, the default), cached for a minute. */
export function useVerdicts(experimentIds: string[]) {
  const ids = experimentIds.filter((id) => isUuid(id));
  const results = useQueries({
    queries: ids.map((experimentId) => ({
      queryKey: workspaceQueryKey("v1", "verdict", experimentId),
      queryFn: ({ signal }: { signal: AbortSignal }) => v1Get("/v1/experiments/{experiment_id}/verdict", VerdictSchema, { params: { experiment_id: experimentId }, signal }),
      staleTime: 60_000, retry: false, refetchOnWindowFocus: false,
    })),
  });
  const byId = new Map<string, (typeof results)[number]>();
  ids.forEach((experimentId, index) => byId.set(experimentId, results[index]));
  return byId;
}
