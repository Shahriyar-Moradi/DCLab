"use client";

import { useSession } from "@/lib/application";
import { cn } from "@/lib/cn";
import { safeWorkspaceSwitchDestination } from "@/lib/infrastructure/capabilities";
import { workspaceSelectorPresentation } from "@/lib/infrastructure/active-workspace";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";

export function WorkspaceSelector({
  collapsed = false,
  inputId = "active-workspace",
}: {
  collapsed?: boolean;
  inputId?: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const { loaded, workspaces, activeWorkspaceId, workspaceSwitching, selectWorkspace } = useSession();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  if (!loaded) return null;

  async function onChange(next: string) {
    if (!next || next === activeWorkspaceId) return;
    setPending(true);
    setError(null);
    setAnnouncement("Switching workspace…");
    try {
      const nextUser = await selectWorkspace(next);
      const selected = nextUser.workspaces.find((workspace) => workspace.id === next);
      setAnnouncement(`Workspace changed to ${selected?.name ?? "the selected workspace"}.`);
      router.replace(safeWorkspaceSwitchDestination(nextUser, pathname));
      router.refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not switch workspace.");
      setAnnouncement("Workspace change failed.");
    } finally {
      setPending(false);
    }
  }

  const { empty, label } = workspaceSelectorPresentation(workspaces, activeWorkspaceId);

  return (
    <div className={cn("app-workspace-selector", collapsed && "is-collapsed")} role="group" aria-label="Active workspace">
      <label className={cn("app-workspace-selector-label", collapsed && "sr-only")} htmlFor={inputId}>
        Workspace
      </label>
      <select
        id={inputId}
        className="app-workspace-select"
        value={activeWorkspaceId ?? ""}
        disabled={pending || workspaceSwitching || empty}
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
      <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {announcement}
      </p>
    </div>
  );
}
