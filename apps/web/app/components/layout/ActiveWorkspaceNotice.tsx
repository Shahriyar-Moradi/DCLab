"use client";

import { useSession } from "@/lib/application";

export function ActiveWorkspaceNotice({ action }: { action: string }) {
  const { activeWorkspace, activeWorkspaceId } = useSession();
  if (!activeWorkspaceId) {
    return (
      <p className="text-body text-oxblood" role="status" data-testid="active-workspace-notice">
        No workspace selected. Choose a workspace before {action}.
      </p>
    );
  }
  return (
    <p className="font-mono text-data text-ink-muted" data-testid="active-workspace-notice">
      {action} in {activeWorkspace?.name ?? "workspace"} ({activeWorkspaceId})
    </p>
  );
}
