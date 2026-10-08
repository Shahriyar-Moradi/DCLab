"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { WorkspaceSelector } from "@/app/components/layout/WorkspaceSelector";
import { STUDIO_BACKEND_FEATURES, studioNavigationForUser } from "@/app/components/layout/app-navigation";
import { CommandBar } from "@/components/studio/CommandBar";
import { Crumbs, type Crumb } from "@/components/studio/Crumbs";
import { Shell } from "@/components/studio/Shell";
import { useCommandSearch, useProjectRefs, useSession, useStudioProject } from "@/lib/application";
import { useInboxCounts } from "@/lib/application/studio-inbox-hooks";
import { badgeText } from "@/lib/application/studio-inbox";
import { offSidebarCrumb } from "@/lib/application/studio-names";
import { isUuid, MIN_QUERY_LENGTH, plainText } from "@/lib/application/command-search";
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
  const refs = useProjectRefs(projectId);
  const targetOf = (kind: string) => refs.data?.items.find((ref) => ref.ref_kind === kind)?.target.id ?? null;
  // The Goal and Predictions pages need an id (the test design, the model in use); they are listed once the project has one.
  const nav = studioNavigationForUser(user, projectId, { split_plan: targetOf("split_plan"), champion_model: targetOf("champion_model") }).map((group) =>
    group.id === "project" && project.data?.name ? { ...group, label: `Project · ${plainText(project.data.name, 60)}` } : group,
  );
  // One cheap counts read (refetched once a minute); the sidebar badge never needs the list.
  const counts = useInboxCounts(!!user && nav.some((group) => group.items.some((item) => item.id === "inbox")), true);
  const items = nav.flatMap((group) => group.items);
  const projectItems = new Set(nav.filter((group) => group.id === "project").flatMap((group) => group.items.map((item) => item.id)));
  const current = items
    .map((item) => ({ item, path: item.href.split("?")[0] }))
    // Inside a project only the project's own items can be "current"; the workspace Projects item would match every project page.
    .filter(({ item, path }) => item.href.includes("?") ? false : projectId && !projectItems.has(item.id) ? pathname === path : pathname === path || pathname.startsWith(`${path}/`))
    .sort((a, b) => b.path.length - a.path.length)[0]?.item;

  const crumbs: Crumb[] = [{ label: activeWorkspace?.name ?? "Workspace", href: "/home" }];
  if (projectId) {
    crumbs.push({ label: "Projects", href: "/projects" });
    crumbs.push({ label: project.data?.name ? plainText(project.data.name, 60) : "Project", href: `/projects/${projectId}/experiments` });
  }
  // Pages reached from other pages rather than from the sidebar still get a name in the breadcrumb.
  const extra = projectId ? offSidebarCrumb(pathname.split("/")[3]) : undefined;
  if (current && (projectId || current.href !== crumbs.at(-1)?.href)) crumbs.push({ label: current.label });
  else if (extra) crumbs.push({ label: extra });

  let body: ReactNode = children;
  if (!loaded) body = <p role="status">Loading your workspace…</p>;
  else if (!user) body = <p>Your session ended. <Link href="/login">Sign in again</Link>.</p>;
  else if (workspaceSwitching) body = <p role="status">Switching workspace…</p>;

  return (
    <Shell
      nav={nav}
      currentHref={current?.href}
      badges={{ inbox: badgeText(counts.data?.needs_decision) }}
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
