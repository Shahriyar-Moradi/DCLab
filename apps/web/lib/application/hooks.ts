"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiGet, apiPost, uploadFile } from "@/lib/infrastructure/api-client";
import {
  DecisionGenerateSchema,
  DecisionListSchema,
  DecisionSchema,
  HealthSchema,
  LabCandidateSchema,
  LabComparisonSchema,
  LabDatasetSchema,
  LabEnvironmentSchema,
  LabExperimentSchema,
  LabReportSchema,
  LabTaskSchema,
  OpportunityListSchema,
  OpportunitySchema,
  UploadResultSchema,
  type Decision,
  type DecisionGenerate,
  type DecisionList,
  type Health,
  type Opportunity,
  type OpportunityList,
  type UploadResult,
} from "@/lib/domain/schemas";

export type OpportunityQuery = {
  limit?: number;
  offset?: number;
  stage?: string;
  sort?: "created_at" | "amount";
  order?: "asc" | "desc";
};

export type DecisionQuery = {
  limit?: number;
  offset?: number;
  status?: string;
  action?: string;
  opportunity_id?: string;
};

export function useHealth(): ReturnType<typeof useQuery<Health>> {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => apiGet("/health", HealthSchema),
    refetchInterval: 15000,
    retry: 0,
  });
}

export function useOpportunities(params: OpportunityQuery = {}): ReturnType<typeof useQuery<OpportunityList>> {
  return useQuery({
    queryKey: ["opportunities", params],
    queryFn: () =>
      apiGet("/opportunities", OpportunityListSchema, {
        limit: params.limit ?? 20,
        offset: params.offset ?? 0,
        stage: params.stage,
        sort: params.sort ?? "created_at",
        order: params.order ?? "desc",
      }),
  });
}

export function useOpportunity(id: string | undefined): ReturnType<typeof useQuery<Opportunity>> {
  return useQuery({
    queryKey: ["opportunities", id],
    queryFn: () => apiGet(`/opportunities/${id}`, OpportunitySchema),
    enabled: Boolean(id),
  });
}

export function useDecisions(params: DecisionQuery = {}): ReturnType<typeof useQuery<DecisionList>> {
  return useQuery({
    queryKey: ["decisions", params],
    queryFn: () =>
      apiGet("/decisions", DecisionListSchema, {
        limit: params.limit ?? 20,
        offset: params.offset ?? 0,
        status: params.status,
        action: params.action,
        opportunity_id: params.opportunity_id,
      }),
  });
}

export function useDecision(id: string | undefined): ReturnType<typeof useQuery<Decision>> {
  return useQuery({
    queryKey: ["decisions", id],
    queryFn: () => apiGet(`/decisions/${id}`, DecisionSchema),
    enabled: Boolean(id),
  });
}

export function useGenerateDecision(): ReturnType<
  typeof useMutation<DecisionGenerate, Error, string>
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (opportunity_id: string) =>
      apiPost("/decisions/generate", DecisionGenerateSchema, { opportunity_id }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["decisions"] });
      void queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      void queryClient.invalidateQueries({ queryKey: ["overview-snapshot"] });
    },
  });
}

export function useOverviewSnapshot(): ReturnType<
  typeof useQuery<{ opportunityTotal: number; decisions: DecisionList["items"]; decisionTotal: number; truncated: boolean }>
> {
  return useQuery({
    queryKey: ["overview-snapshot"],
    queryFn: async () => {
      const opportunities = await apiGet("/opportunities", OpportunityListSchema, { limit: 1, offset: 0 });
      const first = await apiGet("/decisions", DecisionListSchema, { limit: 100, offset: 0 });
      const items = [...first.items];
      let offset = 100;
      while (offset < first.total && offset < 500) {
        const page = await apiGet("/decisions", DecisionListSchema, { limit: 100, offset });
        items.push(...page.items);
        offset += 100;
      }
      return {
        opportunityTotal: opportunities.total,
        decisions: items,
        decisionTotal: first.total,
        truncated: first.total > 500,
      };
    },
  });
}

export function useUploadOpportunities(): ReturnType<
  typeof useMutation<UploadResult, Error, { file: File; onProgress?: (percent: number) => void }>
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, onProgress }) => uploadFile("/opportunities/upload", UploadResultSchema, file, onProgress),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      void queryClient.invalidateQueries({ queryKey: ["overview-snapshot"] });
    },
  });
}

export function useLabEnvironments() {
  return useQuery({
    queryKey: ["lab", "environments"],
    queryFn: () => apiGet("/lab/environments", z.array(LabEnvironmentSchema)),
  });
}

export function useLabDatasets() {
  return useQuery({
    queryKey: ["lab", "datasets"],
    queryFn: () => apiGet("/lab/datasets", z.array(LabDatasetSchema)),
  });
}

export function useLabTasks() {
  return useQuery({
    queryKey: ["lab", "tasks"],
    queryFn: () => apiGet("/lab/tasks", z.array(LabTaskSchema)),
  });
}

export function useLabExperiments() {
  return useQuery({
    queryKey: ["lab", "experiments"],
    queryFn: () => apiGet("/lab/experiments", z.array(LabExperimentSchema)),
  });
}

export function useLabExperiment(id: string | undefined) {
  return useQuery({
    queryKey: ["lab", "experiments", id],
    queryFn: () => apiGet(`/lab/experiments/${id}`, LabExperimentSchema),
    enabled: Boolean(id),
  });
}

export function useLabReport(id: string | undefined) {
  return useQuery({
    queryKey: ["lab", "report", id],
    queryFn: () => apiGet(`/lab/experiments/${id}/report`, LabReportSchema),
    enabled: Boolean(id),
  });
}

export function useLabCandidates(id: string | undefined) {
  return useQuery({
    queryKey: ["lab", "candidates", id],
    queryFn: () => apiGet(`/lab/experiments/${id}/candidates`, z.array(LabCandidateSchema)),
    enabled: Boolean(id),
  });
}

export function useLabComparison(id: string | undefined) {
  return useQuery({
    queryKey: ["lab", "comparison", id],
    queryFn: () => apiGet(`/lab/experiments/${id}/comparison`, LabComparisonSchema),
    enabled: Boolean(id),
  });
}
