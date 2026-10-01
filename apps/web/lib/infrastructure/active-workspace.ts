import type { QueryClient } from "@tanstack/react-query";

export const WORKSPACE_HEADER = "X-Workspace-Id";
export const REQUEST_ID_HEADER = "X-Request-Id";
export const WORKSPACE_QUERY_ROOT = "ws";

let activeWorkspaceId: string | null = null;
let workspaceRequestController = new AbortController();

export function getActiveWorkspaceId(): string | null {
  return activeWorkspaceId;
}

export function setActiveWorkspaceId(workspaceId: string | null): void {
  if (activeWorkspaceId !== workspaceId) {
    workspaceRequestController.abort("active workspace changed");
    workspaceRequestController = new AbortController();
  }
  activeWorkspaceId = workspaceId;
}

export function getWorkspaceRequestSignal(): AbortSignal {
  return workspaceRequestController.signal;
}

export function abortWorkspaceRequests(): void {
  workspaceRequestController.abort("active workspace transition");
  workspaceRequestController = new AbortController();
}

export function workspaceQueryKey(...parts: unknown[]): unknown[] {
  return [WORKSPACE_QUERY_ROOT, activeWorkspaceId ?? "none", ...parts];
}

export function workspaceSelectorPresentation(
  workspaces: Array<{ id: string; name: string }>,
  activeId: string | null,
): { empty: boolean; label: string } {
  const empty = workspaces.length === 0;
  const active = workspaces.find((workspace) => workspace.id === activeId);
  return {
    empty,
    label: empty ? "No workspace" : (active?.name ?? "Select workspace"),
  };
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

export async function prepareWorkspaceTransition(client: QueryClient): Promise<void> {
  abortWorkspaceRequests();
  await client.cancelQueries({ queryKey: [WORKSPACE_QUERY_ROOT] });
  clearWorkspaceQueries(client);
  client.getMutationCache().clear();
}
