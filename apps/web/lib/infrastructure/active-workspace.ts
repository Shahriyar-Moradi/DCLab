import { QueryClient } from "@tanstack/react-query";

export const WORKSPACE_HEADER = "X-Workspace-Id";
export const REQUEST_ID_HEADER = "X-Request-Id";
export const WORKSPACE_QUERY_ROOT = "ws";

let activeWorkspaceId: string | null = null;

export function getActiveWorkspaceId(): string | null {
  return activeWorkspaceId;
}

export function setActiveWorkspaceId(workspaceId: string | null): void {
  activeWorkspaceId = workspaceId;
}

export function workspaceQueryKey(...parts: unknown[]): unknown[] {
  return [WORKSPACE_QUERY_ROOT, activeWorkspaceId ?? "none", ...parts];
}

export function clearWorkspaceQueries(client: QueryClient): void {
  client.removeQueries({ queryKey: [WORKSPACE_QUERY_ROOT] });
  client.removeQueries({
    predicate: (query) => {
      const root = query.queryKey[0];
      return root !== "health" && root !== WORKSPACE_QUERY_ROOT;
    },
  });
}
