"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiGet, apiPost, apiPostForm, uploadFile, apiDownload } from "@/lib/infrastructure/api-client";
import { type SessionUser } from "@/lib/infrastructure/session";
import { useSession } from "./session-provider";
import {
  AdminClientUploadDetailSchema,
  AdminClientUploadSummarySchema,
  ClientLabProblemSchema,
  ClientLabQuotaSchema,
  ClientLabRunSchema,
  ClientLabUploadSchema,
  DecisionGenerateSchema,
  DecisionListSchema,
  DecisionSchema,
  HealthSchema,
  InsightListSchema,
  LabCandidateSchema,
  LabComparisonSchema,
  LabDatasetSchema,
  LabEnvironmentSchema,
  LabExperimentSchema,
  LabReportSchema,
  LabTaskSchema,
  LabUseCasePlanSchema,
  ClientTrialAuditDetailSchema,
  MonitoringOverviewSchema,
  OpportunityListSchema,
  OpportunitySchema,
  OrganizationDetailSchema,
  OrganizationSummarySchema,
  RegisteredModelSchema,
  UploadResultSchema,
  type AdminClientUploadDetail,
  type AdminClientUploadSummary,
  type ClientLabProblem,
  type ClientLabQuota,
  type ClientLabRun,
  type ClientLabUpload,
  type ClientTrialAuditDetail,
  type Decision,
  type DecisionGenerate,
  type DecisionList,
  type Health,
  type InsightList,
  type MonitoringOverview,
  type Opportunity,
  type OpportunityList,
  type OrganizationDetail,
  type OrganizationSummary,
  type RegisteredModel,
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

const LoginResponseSchema = z.object({
  access_token: z.string(),
  token_type: z.string(),
  user: z.object({
    id: z.string(),
    email: z.string(),
    role: z.enum([
      "dclab_admin",
      "dclab_developer",
      "business_admin",
      "business_developer",
      "client_user",
    ]),
    full_name: z.string(),
    workspace_id: z.string().nullable(),
  }),
});

export function useLogin() {
  const { signIn } = useSession();
  return useMutation({
    mutationFn: (credentials: { email: string; password: string }) =>
      apiPost("/auth/login", LoginResponseSchema, credentials),
    onSuccess: (data) => {
      signIn(data.access_token, data.user as SessionUser);
    },
  });
}

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
      apiGet("/app/opportunities", OpportunityListSchema, {
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
    queryFn: () => apiGet(`/app/opportunities/${id}`, OpportunitySchema),
    enabled: Boolean(id),
  });
}

export function useDecisions(params: DecisionQuery = {}): ReturnType<typeof useQuery<DecisionList>> {
  return useQuery({
    queryKey: ["decisions", params],
    queryFn: () =>
      apiGet("/app/decisions", DecisionListSchema, {
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
    queryFn: () => apiGet(`/app/decisions/${id}`, DecisionSchema),
    enabled: Boolean(id),
  });
}

export function useGenerateDecision(): ReturnType<
  typeof useMutation<DecisionGenerate, Error, string>
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (opportunity_id: string) =>
      apiPost("/app/decisions/generate", DecisionGenerateSchema, { opportunity_id }),
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
      const opportunities = await apiGet("/app/opportunities", OpportunityListSchema, { limit: 1, offset: 0 });
      const first = await apiGet("/app/decisions", DecisionListSchema, { limit: 100, offset: 0 });
      const items = [...first.items];
      let offset = 100;
      while (offset < first.total && offset < 500) {
        const page = await apiGet("/app/decisions", DecisionListSchema, { limit: 100, offset });
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

export function useInsights(): ReturnType<typeof useQuery<InsightList>> {
  return useQuery({
    queryKey: ["insights"],
    queryFn: () => apiGet("/app/insights", InsightListSchema),
  });
}

export function useLabProblems(): ReturnType<typeof useQuery<ClientLabProblem[]>> {
  return useQuery({
    queryKey: ["client-labs", "problems"],
    queryFn: () => apiGet("/app/labs/problems", z.array(ClientLabProblemSchema)),
  });
}

export function useLabQuota(useCase: string | undefined): ReturnType<typeof useQuery<ClientLabQuota>> {
  return useQuery({
    queryKey: ["client-labs", "quota", useCase],
    queryFn: () => apiGet(`/app/labs/problems/${useCase}/quota`, ClientLabQuotaSchema),
    enabled: Boolean(useCase),
  });
}

export function useLabRuns(useCase?: string): ReturnType<typeof useQuery<ClientLabRun[]>> {
  return useQuery({
    queryKey: ["client-labs", "runs", useCase ?? "all"],
    queryFn: () => apiGet("/app/labs/runs", z.array(ClientLabRunSchema), { use_case: useCase }),
  });
}

export function useLabRun(id: string | undefined): ReturnType<typeof useQuery<ClientLabRun>> {
  return useQuery({
    queryKey: ["client-labs", "runs", "detail", id],
    queryFn: () => apiGet(`/app/labs/runs/${id}`, ClientLabRunSchema),
    enabled: Boolean(id),
  });
}

export function useRunLabTrial(): ReturnType<
  typeof useMutation<ClientLabRun, Error, { useCase: string; file?: File | null }>
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ useCase, file }) => {
      const form = new FormData();
      form.append("use_case", useCase);
      if (file) form.append("file", file);
      return apiPostForm("/app/labs/runs", ClientLabRunSchema, form);
    },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["client-labs", "runs"] });
      void queryClient.invalidateQueries({ queryKey: ["client-labs", "quota", variables.useCase] });
    },
  });
}

export function useLabUploads(category: string): ReturnType<typeof useQuery<ClientLabUpload[]>> {
  return useQuery({
    queryKey: ["client-labs", "uploads", category],
    queryFn: () => apiGet("/app/labs/uploads", z.array(ClientLabUploadSchema), { category }),
  });
}

export function useLabUpload(id: string | undefined): ReturnType<typeof useQuery<ClientLabUpload>> {
  return useQuery({
    queryKey: ["client-labs", "uploads", "detail", id],
    queryFn: () => apiGet(`/app/labs/uploads/${id}`, ClientLabUploadSchema),
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "queued" || status === "processing") return 1000;
      return query.state.data?.progress === "looking" ? 1000 : false;
    },
  });
}

export async function downloadLabPredictions(runId: string): Promise<void> {
  await saveDownloadedCsv(`/app/labs/uploads/${runId}/predictions.csv`);
}

export async function downloadAdminRunPredictions(runId: string): Promise<void> {
  await saveDownloadedCsv(`/admin/client-uploads/${runId}/predictions.csv`);
}

async function saveDownloadedCsv(path: string): Promise<void> {
  const { blob, filename } = await apiDownload(path);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function useUploadLabFile(): ReturnType<
  typeof useMutation<ClientLabUpload, Error, { category: string; file: File; targetColumn?: string }>
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ category, file, targetColumn }) => {
      const form = new FormData();
      form.append("category", category);
      form.append("file", file);
      if (targetColumn?.trim()) form.append("target_column", targetColumn.trim());
      return apiPostForm("/app/labs/uploads", ClientLabUploadSchema, form);
    },
    onSuccess: (_data, variables) => {
      void queryClient.invalidateQueries({ queryKey: ["client-labs", "uploads", variables.category] });
    },
  });
}

export function useUploadOpportunities(): ReturnType<
  typeof useMutation<UploadResult, Error, { file: File; onProgress?: (percent: number) => void }>
> {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, onProgress }) => uploadFile("/app/opportunities/upload", UploadResultSchema, file, onProgress),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["opportunities"] });
      void queryClient.invalidateQueries({ queryKey: ["overview-snapshot"] });
    },
  });
}

export function useLabEnvironments() {
  return useQuery({
    queryKey: ["lab", "environments"],
    queryFn: () => apiGet("/admin/environments", z.array(LabEnvironmentSchema)),
  });
}

export function useLabDatasets() {
  return useQuery({
    queryKey: ["lab", "datasets"],
    queryFn: () => apiGet("/admin/datasets", z.array(LabDatasetSchema)),
  });
}

export function useUploadLabDataset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, onProgress }: { file: File; onProgress?: (percent: number) => void }) => {
      const name = file.name.replace(/\.[^.]+$/, "") || "dataset";
      return uploadFile("/admin/datasets/upload", LabDatasetSchema, file, onProgress, { name });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["lab", "datasets"] });
    },
  });
}

export function useCreateLabWorkbook() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiPost("/admin/datasets/sample-workbook", LabDatasetSchema, {}),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["lab", "datasets"] });
    },
  });
}

export function useLabUseCasePlan(datasetId: string | undefined) {
  return useQuery({
    queryKey: ["lab", "use-cases", datasetId],
    queryFn: () => apiGet(`/admin/datasets/${datasetId}/use-cases`, LabUseCasePlanSchema),
    enabled: Boolean(datasetId),
  });
}

export function useTrainLabUseCase(datasetId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) =>
      apiPost(`/admin/datasets/${datasetId}/use-cases/${slug}/train`, LabExperimentSchema, { max_models: 5 }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["lab", "use-cases", datasetId] });
      void queryClient.invalidateQueries({ queryKey: ["lab", "experiments"] });
      void queryClient.invalidateQueries({ queryKey: ["lab", "tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["admin", "models"] });
    },
  });
}

export function useLabTasks() {
  return useQuery({
    queryKey: ["lab", "tasks"],
    queryFn: () => apiGet("/admin/tasks", z.array(LabTaskSchema)),
  });
}

export function useLabExperiments() {
  return useQuery({
    queryKey: ["lab", "experiments"],
    queryFn: () => apiGet("/admin/experiments", z.array(LabExperimentSchema)),
  });
}

export function useLabExperiment(id: string | undefined) {
  return useQuery({
    queryKey: ["lab", "experiments", id],
    queryFn: () => apiGet(`/admin/experiments/${id}`, LabExperimentSchema),
    enabled: Boolean(id),
  });
}

export function useLabReport(id: string | undefined) {
  return useQuery({
    queryKey: ["lab", "report", id],
    queryFn: () => apiGet(`/admin/experiments/${id}/report`, LabReportSchema),
    enabled: Boolean(id),
  });
}

export function useLabCandidates(id: string | undefined) {
  return useQuery({
    queryKey: ["lab", "candidates", id],
    queryFn: () => apiGet(`/admin/experiments/${id}/candidates`, z.array(LabCandidateSchema)),
    enabled: Boolean(id),
  });
}

export function useLabComparison(id: string | undefined) {
  return useQuery({
    queryKey: ["lab", "comparison", id],
    queryFn: () => apiGet(`/admin/experiments/${id}/comparison`, LabComparisonSchema),
    enabled: Boolean(id),
  });
}

export function useAdminOrganizations(): ReturnType<typeof useQuery<OrganizationSummary[]>> {
  return useQuery({
    queryKey: ["admin", "organizations"],
    queryFn: () => apiGet("/admin/organizations", z.array(OrganizationSummarySchema)),
  });
}

export function useAdminOrganization(id: string | undefined): ReturnType<typeof useQuery<OrganizationDetail>> {
  return useQuery({
    queryKey: ["admin", "organizations", id],
    queryFn: () => apiGet(`/admin/organizations/${id}`, OrganizationDetailSchema),
    enabled: Boolean(id),
  });
}

export function useAdminModelRegistry(): ReturnType<typeof useQuery<RegisteredModel[]>> {
  return useQuery({
    queryKey: ["admin", "models"],
    queryFn: () => apiGet("/admin/models", z.array(RegisteredModelSchema)),
  });
}

export function useAdminMonitoring(): ReturnType<typeof useQuery<MonitoringOverview>> {
  return useQuery({
    queryKey: ["admin", "monitoring"],
    queryFn: () => apiGet("/admin/monitoring", MonitoringOverviewSchema),
  });
}

export function useAdminClientUploads(): ReturnType<typeof useQuery<AdminClientUploadSummary[]>> {
  return useQuery({
    queryKey: ["admin", "client-uploads"],
    queryFn: () => apiGet("/admin/client-uploads", z.array(AdminClientUploadSummarySchema)),
  });
}

export function useAdminClientUpload(
  id: string | undefined,
): ReturnType<typeof useQuery<AdminClientUploadDetail>> {
  return useQuery({
    queryKey: ["admin", "client-uploads", id],
    queryFn: () => apiGet(`/admin/client-uploads/${id}`, AdminClientUploadDetailSchema),
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const status = query.state.data?.pipeline_status;
      if (!status) return false;
      if (
        [
          "queued",
          "running",
          "ingesting",
          "analyzing",
          "cleaning",
          "feature_engineering",
          "preprocessing",
          "splitting",
          "cross_validation",
          "training",
          "evaluating",
          "predicting",
        ].includes(status)
      ) {
        return 1000;
      }
      return false;
    },
  });
}

export function useAdminClientTrialAudit(
  auditId: string | undefined,
): ReturnType<typeof useQuery<ClientTrialAuditDetail>> {
  return useQuery({
    queryKey: ["admin", "models", "client-trials", auditId],
    queryFn: () => apiGet(`/admin/models/client-trials/${auditId}`, ClientTrialAuditDetailSchema),
    enabled: Boolean(auditId),
  });
}
