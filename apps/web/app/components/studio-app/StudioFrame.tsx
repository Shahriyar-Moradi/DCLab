"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { WorkspaceSelector } from "@/app/components/layout/WorkspaceSelector";
import { STUDIO_BACKEND_FEATURES, studioNavigationForUser } from "@/app/components/layout/app-navigation";
import { CommandBar } from "@/components/studio/CommandBar";
import { Crumbs, type Crumb } from "@/components/studio/Crumbs";
import { Shell } from "@/components/studio/Shell";
import { useCommandSearch, useSession, useStudioProject } from "@/lib/application";
import { isUuid, MIN_QUERY_LENGTH } from "@/lib/application/command-search";
import { displayName } from "@/lib/infrastructure/session";

function StudioCommandBar({ projectId }: { projectId?: string }) {
  const search = useCommandSearch(projectId);
  const short = search.query.trim().length < MIN_QUERY_LENGTH;
  const emptyMessage = short
    ? `Type at least ${MIN_QUERY_LENGTH} characters.`
    : search.loading
      ? "Searching…"
      : search.failed
        ? "Search is unavailable right now. Try again."
        : projectId
          ? "No matching project, experiment, model or decision."
          : "No matching project or experiment. Open a project to search its models and decisions.";
  return (
    <CommandBar
      globalShortcut
      query={search.query}
      onQueryChange={search.setQuery}
      results={search.loading ? [] : search.results}
      emptyMessage={emptyMessage}
    />
  );
}

/** Developer Studio frame: sidebar (role + backend gated), breadcrumbs, ⌘K, workspace switcher. */
export function StudioFrame({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const params = useParams<{ id?: string }>();
  const projectId = pathname.startsWith("/projects/") && isUuid(params.id) ? params.id : undefined;
  const { user, loaded, activeWorkspace, activeWorkspaceId, workspaceSwitching } = useSession();
  const project = useStudioProject(projectId);
  const nav = studioNavigationForUser(user, projectId);
  const items = nav.flatMap((group) => group.items);
  const current = items
    .filter((item) => pathname === item.href || pathname.startsWith(`${item.href}/`))
    .sort((a, b) => b.href.length - a.href.length)[0];

  const crumbs: Crumb[] = [{ label: activeWorkspace?.name ?? "Workspace", href: "/home" }];
  if (projectId) {
    crumbs.push({ label: "Projects", href: "/projects" });
    crumbs.push({ label: project.data?.name ?? "Project", href: `/projects/${projectId}/experiments` });
  }
  if (current && (projectId || current.href !== crumbs.at(-1)?.href)) crumbs.push({ label: current.label });

  let body: ReactNode = children;
  if (!loaded) body = <p role="status">Loading your workspace…</p>;
  else if (!user) body = <p>Your session ended. <Link href="/login">Sign in again</Link>.</p>;
  else if (workspaceSwitching) body = <p role="status">Switching workspace…</p>;

  return (
    <Shell
      nav={nav}
      currentHref={current?.href}
      workspace={activeWorkspace ? { name: activeWorkspace.name, detail: activeWorkspace.kind } : undefined}
      user={user ? { name: displayName(user), detail: user.email } : undefined}
      crumbs={<Crumbs items={crumbs} />}
      // Keyed by workspace: a switch drops results and aborts requests made for the old one.
      commandBar={user ? <StudioCommandBar key={activeWorkspaceId ?? "none"} projectId={projectId} /> : null}
      topbarRight={user ? <WorkspaceSelector inputId="studio-workspace" /> : null}
      // Assistant panel (A3-UI) slot: the capability stays false until the assistant ships.
      assistant={STUDIO_BACKEND_FEATURES.assistant ? <p>Assistant</p> : undefined}
    >
      <div key={activeWorkspaceId ?? "no-workspace"} style={{ display: "contents" }}>
        {body}
      </div>
    </Shell>
  );
}
