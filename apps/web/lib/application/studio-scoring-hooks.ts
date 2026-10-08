"use client";

/** Score new data (P4.9-UI): typed /v1 upload (purpose scoring), create, poll and download. Every write carries the action's Idempotency-Key. */
import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { v1Download, v1Get, v1Post, v1PostForm } from "@/lib/infrastructure/v1/client";
import { isUuid } from "./command-search";
import { isTerminal, pollDelay, safeDownloadId } from "./studio-scoring";
import { UploadedDatasetSchema } from "./studio-wizard-hooks";

const nullableString = z.string().nullable().optional();
const nullableNumber = z.number().nullable().optional();

export const PredictionSchema = z.object({
  id: z.string(),
  model_version_id: z.string(),
  input_dataset_id: z.string(),
  status: z.enum(["queued", "running", "completed", "failed"]),
  output_format: z.enum(["csv", "parquet"]),
  rows_in: nullableNumber,
  rows_out: nullableNumber,
  decision_threshold: nullableNumber,
  contract_check: z.record(z.string(), z.unknown()).nullable(),
  error_code: nullableString,
  error_message: nullableString,
  created_at: z.string(),
  completed_at: nullableString,
  output: z.object({ artifact_id: z.string(), content_digest: z.string(), download_path: z.string(), size_bytes: z.number(), mime_type: nullableString }).nullable(),
});
export type StudioPrediction = z.infer<typeof PredictionSchema>;

/** A scoring file is uploaded with purpose "scoring": no target, never a training source. */
export function uploadScoringFile(input: { projectId: string; file: File; key: string }) {
  const form = new FormData();
  form.append("project_id", input.projectId);
  form.append("purpose", "scoring");
  form.append("file", input.file, input.file.name);
  return v1PostForm("/v1/datasets", UploadedDatasetSchema, form, { idempotencyKey: input.key });
}

export function createPrediction(input: { modelVersionId: string; datasetId: string; key: string }) {
  return v1Post(
    "/v1/model-versions/{model_version_id}/predictions",
    PredictionSchema,
    { dataset_id: input.datasetId, output_format: "csv" },
    { params: { model_version_id: input.modelVersionId }, idempotencyKey: input.key },
  );
}

/** One scoring, polled with a growing delay until it completes or fails; unmounting aborts the read. */
export function usePrediction(predictionId: string | null | undefined) {
  return useQuery({
    queryKey: workspaceQueryKey("v1", "prediction", predictionId),
    queryFn: ({ signal }) => v1Get("/v1/predictions/{prediction_id}", PredictionSchema, { params: { prediction_id: predictionId! }, signal }),
    enabled: isUuid(predictionId),
    retry: 1,
    refetchInterval: (query) => (query.state.error || isTerminal(query.state.data?.status) ? false : pollDelay(query.state.dataUpdateCount)),
  });
}

/** Authorised download through the BFF with the session credentials; the saved file is offered as a Blob (no token or signed URL in any link). */
export async function downloadPredictions(prediction: StudioPrediction): Promise<{ blob: Blob; filename: string }> {
  const id = safeDownloadId(prediction.output?.download_path, prediction.id);
  if (!id) throw new Error("The download link from the API is not a DCLab path, so it was not opened.");
  return v1Download("/v1/predictions/{prediction_id}/download", { params: { prediction_id: id }, accept: "text/csv", fallbackFilename: `predictions-${id}.csv` });
}
