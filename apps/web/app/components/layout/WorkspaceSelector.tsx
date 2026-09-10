"use client";

import { useSession } from "@/lib/application";
import { cn } from "@/lib/cn";
import { useState } from "react";

export function WorkspaceSelector({
  collapsed = false,
  inputId = "active-workspace",
}: {
  collapsed?: boolean;
  inputId?: string;
}) {
  const { loaded, workspaces, activeWorkspaceId, activeWorkspace, selectWorkspace } = useSession();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!loaded) return null;

  async function onChange(next: string) {
    if (!next || next === activeWorkspaceId) return;
    setPending(true);
    setError(null);
    try {
      await selectWorkspace(next);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not switch workspace.");
    } finally {
      setPending(false);
    }
  }

  const empty = workspaces.length === 0;
  const label = empty ? "No workspace" : (activeWorkspace?.name ?? "Select workspace");

  return (
    <div className={cn("app-workspace-selector", collapsed && "is-collapsed")} role="group" aria-label="Active workspace">
      <label className={cn("app-workspace-selector-label", collapsed && "sr-only")} htmlFor={inputId}>
        Workspace
      </label>
      <select
        id={inputId}
        className="app-workspace-select"
        value={activeWorkspaceId ?? ""}
        disabled={pending || empty}
        title={collapsed ? label : undefined}
        onChange={(event) => void onChange(event.target.value)}
      >
        {empty ? <option value="">No workspace</option> : null}
        {workspaces.map((workspace) => (
          <option key={workspace.id} value={workspace.id}>
            {workspace.name}
          </option>
        ))}
      </select>
      {error ? (
        <p className="app-workspace-selector-error" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}
