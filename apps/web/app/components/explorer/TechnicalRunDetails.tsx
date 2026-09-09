"use client";

import { ModelBuildInspector } from "@/app/components/model-build/ModelBuildInspector";
import { useSession } from "@/lib/application";
import { isBusinessAdministrationRole, isPlatformRole } from "@/lib/infrastructure/session";

/** Technical details belong to engineering roles, including personal workspace owners.
 * The API independently enforces the requested workspace membership.
 */
export function TechnicalRunDetails(props: { workspaceId: string; pipelineRunId: string }) {
  const { user } = useSession();
  if (!user || !(isPlatformRole(user.role) || isBusinessAdministrationRole(user.role))) return null;
  return <ModelBuildInspector {...props} />;
}
