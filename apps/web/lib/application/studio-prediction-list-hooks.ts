"use client";

/**
 * "This week's list" read (UI-D): `GET /v1/predictions/{id}/rows`, a page of 50 rows at a time (cursor), with the same access
 * as the download. Row cells are the customer's own data; the page shows them as plain text only.
 */
import { useInfiniteQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Get } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";
import { ROWS_PAGE } from "./studio-prediction-list";

const nullableString = z.string().nullable().optional();
const nullableNumber = z.number().nullable().optional();

const RowsPageSchema = z.object({
  prediction_id: z.string(),
  task_type: z.enum(["binary", "multiclass", "regression"]),
  order: z.enum(["score", "row"]),
  entity_column: nullableString,
  decision_threshold: nullableNumber,
  rows_total: z.number(),
  rows_reachable: z.number(),
  limit: z.number(),
  next_cursor: nullableString,
  items: z.array(z.object({
    row_number: z.number(),
    entity: nullableString,
    probability: nullableNumber,
    flagged: z.boolean().nullable().optional(),
    label: nullableString,
    prediction: nullableNumber,
    reasons: z.array(z.string()).optional(),
  })),
  reasons: z.object({ available: z.boolean(), method: z.enum(["tree_shap", "linear_contributions", "occlusion"]).nullable().optional(), rows_explained: z.number() }).nullable().optional(),
});
export type StudioRowsPage = z.infer<typeof RowsPageSchema>;

export const predictionRowsKey = (predictionId: string | null | undefined, order: "score" | "row") => workspaceQueryKey("v1", "prediction-rows", predictionId, order);

export function usePredictionRows(predictionId: string | null | undefined, order: "score" | "row") {
  return useInfiniteQuery({
    queryKey: predictionRowsKey(predictionId, order),
    queryFn: ({ pageParam, signal }) =>
      v1Get("/v1/predictions/{prediction_id}/rows", RowsPageSchema, {
        params: { prediction_id: predictionId! }, query: { order, limit: ROWS_PAGE, cursor: pageParam }, signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: isUuid(predictionId),
    retry: false,
    staleTime: 60_000,
  });
}
