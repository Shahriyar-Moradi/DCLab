import { z } from "zod";

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

export const DecisionSchema = z.object({
  id: z.uuid(),
  opportunity_id: z.uuid(),
  prediction_id: z.uuid(),
  recommended_action: z.string(),
  expected_revenue: z.coerce.number(),
  confidence: z.number(),
  reasoning: z.array(z.string()),
  policy_version: z.string(),
  status: z.string(),
  created_at: z.string(),
  conversion_probability: z.number().nullable().optional(),
  model_version: z.string().nullable().optional(),
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
  conversion_probability: z.number(),
  expected_revenue: z.coerce.number(),
  recommended_action: z.string(),
  confidence: z.number(),
  reasoning: z.array(z.string()),
  model_version: z.string(),
  policy_version: z.string(),
});
export type DecisionGenerate = z.infer<typeof DecisionGenerateSchema>;

export type DecisionView = {
  id?: string;
  opportunityExternalId: string;
  recommendedAction: string;
  expectedRevenue: number;
  confidence: number;
  reasoning: string[];
  modelVersion: string;
  policyVersion: string;
  createdAt?: string;
  conversionProbability?: number | null;
  status?: string;
};

export function decisionToView(row: Decision): DecisionView {
  return {
    id: row.id,
    opportunityExternalId: row.external_id ?? row.opportunity_id,
    recommendedAction: row.recommended_action,
    expectedRevenue: row.expected_revenue,
    confidence: row.confidence,
    reasoning: row.reasoning,
    modelVersion: row.model_version ?? "unknown",
    policyVersion: row.policy_version,
    createdAt: row.created_at,
    conversionProbability: row.conversion_probability,
    status: row.status,
  };
}

export function generateToView(row: DecisionGenerate): DecisionView {
  return {
    opportunityExternalId: row.opportunity_id,
    recommendedAction: row.recommended_action,
    expectedRevenue: row.expected_revenue,
    confidence: row.confidence,
    reasoning: row.reasoning,
    modelVersion: row.model_version,
    policyVersion: row.policy_version,
    conversionProbability: row.conversion_probability,
  };
}
