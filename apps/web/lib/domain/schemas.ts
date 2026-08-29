import { z } from "zod";

// Everything between the BEGIN/END markers below describes data that reaches an
// authenticated client_user's screen. It must stay free of engine internals (see
// apps/api/app/translation/banned_terms.py for the enforced list) — the
// banned-terms scanner (apps/api/app/translation/scanner.py) checks this exact
// block in CI. Admin/Lab-only schemas belong below the END marker instead.
// BEGIN CLIENT-FACING SCHEMAS

export const HealthSchema = z.object({
  status: z.string(),
  db: z.string(),
});
export type Health = z.infer<typeof HealthSchema>;

export const OpportunitySchema = z.object({
  id: z.uuid(),
  org_id: z.string(),
  external_id: z.string(),
  customer_id: z.string(),
  amount: z.coerce.number(),
  currency: z.string(),
  stage: z.string(),
  source: z.string(),
  owner_id: z.string(),
  created_at: z.string(),
  close_date: z.string().nullable(),
  last_contact_days_ago: z.number().nullable(),
  engagement_score: z.number().nullable(),
  sales_rep_available: z.boolean().nullable(),
  industry: z.string().nullable(),
  num_interactions: z.number().nullable(),
  converted: z.number().nullable(),
});
export type Opportunity = z.infer<typeof OpportunitySchema>;

export const OpportunityListSchema = z.object({
  items: z.array(OpportunitySchema),
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
});
export type OpportunityList = z.infer<typeof OpportunityListSchema>;

export const RowErrorSchema = z.object({
  row: z.number(),
  reason: z.string(),
});

export const UploadResultSchema = z.object({
  inserted: z.number(),
  rejected: z.number(),
  errors: z.array(RowErrorSchema),
});
export type UploadResult = z.infer<typeof UploadResultSchema>;

export const ConfidenceBandSchema = z.enum(["High", "Medium", "Low"]);
export type ConfidenceBandValue = z.infer<typeof ConfidenceBandSchema>;

export const DecisionSchema = z.object({
  id: z.uuid(),
  opportunity_id: z.uuid(),
  recommended_action: z.string(),
  expected_revenue: z.coerce.number(),
  confidence_band: ConfidenceBandSchema,
  reasoning: z.array(z.string()),
  policy_version: z.string(),
  status: z.string(),
  created_at: z.string(),
  external_id: z.string().nullable().optional(),
});
export type Decision = z.infer<typeof DecisionSchema>;

export const DecisionListSchema = z.object({
  items: z.array(DecisionSchema),
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
});
export type DecisionList = z.infer<typeof DecisionListSchema>;

export const DecisionGenerateSchema = z.object({
  opportunity_id: z.string(),
  expected_revenue: z.coerce.number(),
  recommended_action: z.string(),
  confidence_band: ConfidenceBandSchema,
  reasoning: z.array(z.string()),
  policy_version: z.string(),
});
export type DecisionGenerate = z.infer<typeof DecisionGenerateSchema>;

export type DecisionView = {
  id?: string;
  opportunityExternalId: string;
  recommendedAction: string;
  expectedRevenue: number;
  confidenceBand: ConfidenceBandValue;
  reasoning: string[];
  policyVersion: string;
  createdAt?: string;
  status?: string;
};

export function decisionToView(row: Decision): DecisionView {
  return {
    id: row.id,
    opportunityExternalId: row.external_id ?? row.opportunity_id,
    recommendedAction: row.recommended_action,
    expectedRevenue: row.expected_revenue,
    confidenceBand: row.confidence_band,
    reasoning: row.reasoning,
    policyVersion: row.policy_version,
    createdAt: row.created_at,
    status: row.status,
  };
}

export function generateToView(row: DecisionGenerate): DecisionView {
  return {
    opportunityExternalId: row.opportunity_id,
    recommendedAction: row.recommended_action,
    expectedRevenue: row.expected_revenue,
    confidenceBand: row.confidence_band,
    reasoning: row.reasoning,
    policyVersion: row.policy_version,
  };
}

export const InsightCategorySchema = z.enum([
  "Marketing",
  "Sales",
  "Revenue",
  "Churn & Retention",
  "Customer Value",
  "Custom",
]);
export type InsightCategoryValue = z.infer<typeof InsightCategorySchema>;

export const ClientInsightSchema = z.object({
  subject_id: z.string(),
  category: InsightCategorySchema,
  headline: z.string(),
  confidence_band: ConfidenceBandSchema,
  recommended_action: z.string(),
  expected_value: z.coerce.number(),
  currency: z.string(),
  reasoning: z.array(z.string()),
  generated_at: z.string(),
});
export type ClientInsight = z.infer<typeof ClientInsightSchema>;

export const InsightCategoryGroupSchema = z.object({
  category: InsightCategorySchema,
  insights: z.array(ClientInsightSchema),
});
export type InsightCategoryGroup = z.infer<typeof InsightCategoryGroupSchema>;

export const InsightListSchema = z.object({
  categories: z.array(InsightCategoryGroupSchema),
});
export type InsightList = z.infer<typeof InsightListSchema>;

export const ClientLabProblemSchema = z.object({
  use_case: z.string(),
  category: InsightCategorySchema,
  question: z.string(),
  sample_scenario: z.string(),
  sample_row_count: z.number(),
  max_upload_rows: z.number(),
  max_trial_runs: z.number(),
  required_columns: z.array(z.string()),
});
export type ClientLabProblem = z.infer<typeof ClientLabProblemSchema>;

export const ClientLabRunSchema = z.object({
  id: z.uuid(),
  use_case: z.string(),
  category: InsightCategorySchema,
  data_source: z.enum(["sample", "uploaded"]),
  row_count: z.number(),
  status: z.enum(["completed", "failed"]),
  failure_reason: z.string().nullable(),
  insights: z.array(ClientInsightSchema),
  created_at: z.string(),
});
export type ClientLabRun = z.infer<typeof ClientLabRunSchema>;

export const ClientLabQuotaSchema = z.object({
  use_case: z.string(),
  max_trial_runs: z.number(),
  runs_used: z.number(),
  runs_remaining: z.number(),
});
export type ClientLabQuota = z.infer<typeof ClientLabQuotaSchema>;

export const LabRunStepStateSchema = z.enum(["done", "current", "pending"]);
export const LabRunStepSchema = z.object({
  id: z.string(),
  label: z.string(),
  state: LabRunStepStateSchema,
});
export type LabRunStep = z.infer<typeof LabRunStepSchema>;

export const LabRunStatusSchema = z.enum(["queued", "processing", "completed", "failed"]);
export type LabRunStatus = z.infer<typeof LabRunStatusSchema>;

export const LabRunPredictionSchema = z.object({
  prediction: z.string(),
  probability: z.number().nullable(),
});
export type LabRunPrediction = z.infer<typeof LabRunPredictionSchema>;

export const LabRunOutcomeSchema = z.object({
  dataset_name: z.string(),
  record_count: z.number(),
  feature_count: z.number(),
  target_label: z.string(),
  task_kind: z.string(),
  method_label: z.string(),
  performance_percent: z.number(),
  performance_summary: z.string(),
  prediction_count: z.number(),
  title: z.string(),
  summary: z.string(),
  records_line: z.string(),
  target_line: z.string(),
  predictions: z.array(LabRunPredictionSchema),
  download_available: z.boolean(),
});
export type LabRunOutcome = z.infer<typeof LabRunOutcomeSchema>;

export const ClientLabUploadSchema = z.object({
  id: z.uuid(),
  run_id: z.uuid(),
  dataset_id: z.uuid().nullable(),
  status: LabRunStatusSchema,
  stage: z.string(),
  headline: z.string(),
  steps: z.array(LabRunStepSchema),
  category: InsightCategorySchema,
  filename: z.string(),
  kind: z.string(),
  record_count: z.number(),
  fields_noticed: z.array(z.string()),
  has_named_fields: z.boolean(),
  structured: z.boolean(),
  progress: z.enum(["looking", "ready", "saved"]),
  message: z.string(),
  insights: z.array(ClientInsightSchema),
  outcome: LabRunOutcomeSchema.nullable(),
  created_at: z.string(),
});
export type ClientLabUpload = z.infer<typeof ClientLabUploadSchema>;

// END CLIENT-FACING SCHEMAS

export const LabEnvironmentSchema = z.object({
  id: z.uuid(),
  org_id: z.string(),
  name: z.string(),
});
export const LabDatasetSchema = z.object({
  id: z.uuid(),
  name: z.string(),
  source_type: z.string(),
  location: z.string(),
  version: z.string(),
  row_count: z.number(),
  column_count: z.number(),
  schema_json: z.unknown().nullable().optional(),
});
export const LabTaskSchema = z.object({
  id: z.uuid(),
  slug: z.string(),
  name: z.string(),
  description: z.string(),
  task_type: z.string(),
  spec: z.unknown(),
});
export const LabExperimentSchema = z.object({
  id: z.uuid(),
  status: z.string(),
  seed: z.number(),
  git_commit: z.string().nullable().optional(),
  artifact_dir: z.string().nullable().optional(),
  result: z.unknown().nullable().optional(),
  config: z.unknown(),
  task_id: z.uuid(),
  dataset_id: z.uuid(),
  task_slug: z.string().nullable().optional(),
  task_name: z.string().nullable().optional(),
  dataset_name: z.string().nullable().optional(),
  use_case: z.string().nullable().optional(),
});
export const LabUseCasePlanItemSchema = z.object({
  slug: z.string(),
  name: z.string(),
  description: z.string(),
  task_type: z.string(),
  trainable: z.boolean(),
  target_column: z.string().nullable().optional(),
  skip_reason: z.string().nullable().optional(),
  feature_groups: z.record(z.string(), z.array(z.string())),
  model_families: z.array(z.string()),
  latest_experiment_id: z.string().nullable().optional(),
  latest_status: z.string().nullable().optional(),
});
export const LabUseCasePlanSchema = z.object({
  dataset_id: z.string(),
  dataset_name: z.string(),
  row_count: z.number(),
  columns: z.array(z.string()),
  entity_column: z.string().nullable().optional(),
  time_column: z.string().nullable().optional(),
  use_cases: z.array(LabUseCasePlanItemSchema),
  trainable_count: z.number(),
});
export type LabUseCasePlan = z.infer<typeof LabUseCasePlanSchema>;
export const LabReportSchema = z.object({
  markdown: z.string().nullable().optional(),
  result: z.unknown().nullable().optional(),
});
export const LabCandidateSchema = z
  .object({
    candidate_id: z.string().optional(),
    model_family: z.string().optional(),
    status: z.string().optional(),
    score: z.number().nullable().optional(),
    feature_groups: z.array(z.string()).optional(),
  })
  .passthrough();
export const LabComparisonSchema = z
  .object({
    fusion: z.string().nullable().optional(),
    test_metrics: z.unknown().optional(),
    best_single: z.unknown().optional(),
    baselines: z.unknown().optional(),
    weights: z.unknown().optional(),
  })
  .passthrough();

// Step 6 — Admin-only surfaces (Organizations, Model Registry, Monitoring).
// Unrestricted, no translation layer; not part of the client-facing block above.
export const OrganizationSummarySchema = z.object({
  id: z.uuid(),
  slug: z.string(),
  name: z.string(),
  created_at: z.string(),
  user_count: z.number(),
  opportunity_count: z.number(),
  decision_count: z.number(),
  trial_run_count: z.number(),
});
export const OrganizationUserSchema = z.object({
  id: z.uuid(),
  email: z.string(),
  full_name: z.string(),
  role: z.string(),
  is_active: z.boolean(),
  created_at: z.string(),
});
export const OrganizationDetailSchema = OrganizationSummarySchema.extend({
  users: z.array(OrganizationUserSchema),
});
export const RegisteredModelSchema = z.object({
  id: z.uuid(),
  source: z.string(),
  name: z.string(),
  status: z.string(),
  model_family: z.string().nullable().optional(),
  fusion: z.string().nullable().optional(),
  metrics: z.record(z.string(), z.unknown()),
  candidate_count: z.number().nullable().optional(),
  created_at: z.string(),
  client_lab_run_id: z.uuid().nullable().optional(),
});
export const ClientTrialAuditDetailSchema = z.object({
  id: z.uuid(),
  client_lab_run_id: z.uuid(),
  use_case: z.string(),
  payload: z.record(z.string(), z.unknown()),
  created_at: z.string(),
});
export const AdminClientUploadSummarySchema = z.object({
  id: z.uuid(),
  workspace_id: z.uuid(),
  category: z.string(),
  original_filename: z.string(),
  kind: z.string(),
  record_count: z.number(),
  has_named_fields: z.boolean(),
  pipeline_status: z.string(),
  experiment_id: z.uuid().nullable(),
  created_at: z.string(),
});
export const AdminLabDecisionRecordSchema = z.object({
  id: z.uuid(),
  column: z.string(),
  source: z.string(),
  rule_decision: z.string(),
  final_decision: z.string(),
  fill_value: z.unknown().nullable(),
  validator_verdict: z.string(),
  prompt_version: z.string(),
  evidence_snapshot: z.record(z.string(), z.unknown()),
  raw_llm_output: z.record(z.string(), z.unknown()).nullable(),
  created_at: z.string(),
});
export const AdminCleaningStepSchema = z.object({
  column: z.string(),
  problem: z.string(),
  action: z.string(),
  result: z.string(),
});
export const AdminDataAnalysisSchema = z.object({
  rows: z.number().nullable(),
  columns: z.number().nullable(),
  numerical_columns: z.array(z.string()),
  categorical_columns: z.array(z.string()),
  missing_values: z.number().nullable(),
  duplicates: z.number().nullable(),
  constant_columns: z.array(z.string()),
  high_cardinality_columns: z.array(z.string()),
});
export const AdminFeatureEngineeringSchema = z.object({
  original_features: z.array(z.string()),
  generated_features: z.array(z.string()),
  removed_features: z.array(z.string()),
  transformations: z.array(z.record(z.string(), z.unknown())),
});
export const AdminValidationSchema = z.object({
  train_rows: z.number().nullable(),
  test_rows: z.number().nullable(),
  cv_strategy: z.string().nullable(),
  n_folds: z.number().nullable(),
  random_state: z.number().nullable(),
});
export const AdminModelComparisonRowSchema = z.object({
  name: z.string(),
  model_family: z.string(),
  cv_auc: z.number().nullable(),
  test_auc: z.number().nullable(),
  cv_metrics: z.record(z.string(), z.unknown()),
  test_metrics: z.record(z.string(), z.unknown()).nullable(),
  selected: z.boolean(),
  status: z.string(),
});
export const AdminFinalModelSchema = z.object({
  selected_model: z.string().nullable(),
  model_family: z.string().nullable(),
  cv_metrics: z.record(z.string(), z.unknown()),
  test_metrics: z.record(z.string(), z.unknown()),
});
export const AdminPredictionsSchema = z.object({
  count: z.number(),
  distribution: z.record(z.string(), z.number()),
  download_available: z.boolean(),
});
export const AdminProcessingSummarySchema = z.object({
  cleaning_completed: z.boolean(),
  feature_engineering_completed: z.boolean(),
  preprocessing_completed: z.boolean(),
  train_test_split: z.string().nullable(),
  cross_validation: z.string().nullable(),
  training_completed: z.boolean(),
  evaluation_completed: z.boolean(),
  predictions_completed: z.boolean(),
});
export const AdminMlRunSchema = z.object({
  run_id: z.uuid(),
  dataset: z.string(),
  dataset_id: z.uuid().nullable(),
  status: z.string(),
  target: z.string().nullable().optional(),
  task_type: z.string().nullable().optional(),
  started_at: z.string().nullable(),
  completed_at: z.string().nullable(),
  duration_seconds: z.number().nullable(),
  analysis: AdminDataAnalysisSchema,
  processing_summary: AdminProcessingSummarySchema.optional(),
  cleaning: z.array(AdminCleaningStepSchema),
  feature_engineering: AdminFeatureEngineeringSchema,
  validation: AdminValidationSchema,
  model_comparison: z.array(AdminModelComparisonRowSchema),
  final_model: AdminFinalModelSchema.nullable(),
  predictions: AdminPredictionsSchema,
});
export const AdminClientUploadDetailSchema = AdminClientUploadSummarySchema.extend({
  stored_path: z.string(),
  fields_noticed: z.array(z.string()),
  pipeline_log: z.record(z.string(), z.unknown()).nullable(),
  decision_records: z.array(AdminLabDecisionRecordSchema).default([]),
  ml_run: AdminMlRunSchema.nullable().optional(),
});
export type AdminClientUploadSummary = z.infer<typeof AdminClientUploadSummarySchema>;
export type AdminLabDecisionRecord = z.infer<typeof AdminLabDecisionRecordSchema>;
export type AdminClientUploadDetail = z.infer<typeof AdminClientUploadDetailSchema>;
export type AdminMlRun = z.infer<typeof AdminMlRunSchema>;

export const MetricDeltaSchema = z.object({
  previous: z.number(),
  current: z.number(),
  delta: z.number(),
});
export const RetrainEventSchema = z.object({
  id: z.uuid(),
  source: z.string(),
  name: z.string(),
  status: z.string(),
  metrics: z.record(z.string(), z.unknown()),
  metric_deltas: z.record(z.string(), MetricDeltaSchema),
  created_at: z.string(),
  client_lab_run_id: z.uuid().nullable().optional(),
});
export const DatasetHealthSchema = z.object({
  id: z.uuid(),
  name: z.string(),
  row_count: z.number(),
  column_count: z.number(),
  last_profiled_at: z.string().nullable().optional(),
  status: z.string(),
});
export const MonitoringOverviewSchema = z.object({
  retrain_events: z.array(RetrainEventSchema),
  dataset_health: z.array(DatasetHealthSchema),
  drift_detection_note: z.string(),
});

export type OrganizationSummary = z.infer<typeof OrganizationSummarySchema>;
export type OrganizationDetail = z.infer<typeof OrganizationDetailSchema>;
export type RegisteredModel = z.infer<typeof RegisteredModelSchema>;
export type ClientTrialAuditDetail = z.infer<typeof ClientTrialAuditDetailSchema>;
export type RetrainEvent = z.infer<typeof RetrainEventSchema>;
export type MonitoringOverview = z.infer<typeof MonitoringOverviewSchema>;
