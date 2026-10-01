"use client";

import { ModelBuildInspector } from "@/app/components/model-build/ModelBuildInspector";
import { useSession } from "@/lib/application";
import { CAPABILITIES, hasCapability } from "@/lib/infrastructure/capabilities";

/** Technical details belong to engineering roles, including personal workspace owners.
 * The API independently enforces the requested workspace membership.
 */
export function TechnicalRunDetails(props: { workspaceId: string; pipelineRunId: string }) {
  const { user } = useSession();
  if (!hasCapability(user, CAPABILITIES.developmentAccess)) return null;
  return <ModelBuildInspector {...props} />;
}
