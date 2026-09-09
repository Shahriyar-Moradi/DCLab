export function explorerRoot(businessMode: boolean): { href: string; label: string } {
  return businessMode
    ? { href: "/business", label: "Business administration" }
    : { href: "/admin/businesses", label: "Businesses" };
}

export function explorerBase(businessId: string, businessMode: boolean): string {
  return businessMode ? `/business/workspaces/${businessId}` : `/admin/businesses/${businessId}`;
}

export function domainHref(businessId: string, domainId: string, businessMode: boolean): string {
  return `${explorerBase(businessId, businessMode)}/domains/${domainId}`;
}

export function workflowHref(businessId: string, workflowId: string, businessMode: boolean): string {
  return `${explorerBase(businessId, businessMode)}/workflows/${workflowId}`;
}

export function workflowRunHref(businessId: string, runId: string, businessMode: boolean): string {
  return `${explorerBase(businessId, businessMode)}/workflow-runs/${runId}`;
}

export function modelHref(businessId: string, modelId: string, businessMode: boolean): string {
  return `${explorerBase(businessId, businessMode)}/models/${modelId}`;
}

export function monitorHref(pipelineId: string, businessId: string, businessMode: boolean): string {
  return businessMode
    ? `${explorerBase(businessId, businessMode)}/pipeline-runs/${pipelineId}/monitor`
    : `/admin/pipeline-runs/${pipelineId}/monitor`;
}
