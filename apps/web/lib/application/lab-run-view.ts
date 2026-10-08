/**
 * P4.5-A: what the Labs run page shows to which role. Pure so it runs under `npm run test:components`.
 * Ids, stage names and the model-build inspector belong to development roles; everyone else sees
 * plain results and, while the run is processing, only "Analyzing your data".
 */
export const CLIENT_PROCESSING_TITLE = "Analyzing your data";

export type LabRunVisibility = {
  /** Run id, upload/dataset ids, progress and pipeline stage names, the model-build inspector. */
  technical: boolean;
  /** Live step list and milestone text while processing (clients get one fixed title instead). */
  liveSteps: boolean;
};

export function labRunVisibility(hasDevelopmentAccess: boolean): LabRunVisibility {
  return { technical: hasDevelopmentAccess, liveSteps: hasDevelopmentAccess };
}

export function processingTitle(visibility: LabRunVisibility, milestone: string | undefined): string | undefined {
  return visibility.liveSteps ? milestone : CLIENT_PROCESSING_TITLE;
}
