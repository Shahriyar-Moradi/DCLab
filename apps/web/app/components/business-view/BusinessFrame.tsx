"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { WorkspaceSelector } from "@/app/components/layout/WorkspaceSelector";
import { useSignOutToLogin } from "@/app/components/layout/use-sign-out";
import { businessNavigationForUser } from "@/app/components/layout/app-navigation";
import { Crumbs } from "@/components/studio/Crumbs";
import { Shell } from "@/components/studio/Shell";
import { useSession } from "@/lib/application";
import { CAPABILITIES, hasCapability } from "@/lib/infrastructure/capabilities";
import { displayName } from "@/lib/infrastructure/session";
import { accountRoleLabel } from "@/lib/application/role-labels";
import { useViewAs, ViewAsBanner } from "@/app/components/studio-app/ViewAs";

/** Business view frame (P7.6-A): one sidebar item, no developer routes; the existing Labs page stays one click away. */
export function BusinessFrame({ children }: { children: ReactNode }) {
  const { user, loaded, activeWorkspace, activeWorkspaceId, workspaceSwitching } = useSession();
  const nav = businessNavigationForUser(user);
  // An admin who opened this view from a "View as" preview keeps the preview note here, with a way to end it.
  const viewAs = useViewAs(user);
  const signOutToLogin = useSignOutToLogin();

  let body: ReactNode = children;
  if (!loaded) body = <p role="status">Loading your workspace…</p>;
  else if (!user) body = <p>Your session ended. <Link href="/login">Sign in again</Link>.</p>;
  else if (workspaceSwitching) body = <p role="status">Switching workspace…</p>;

  return (
    <Shell
      nav={nav}
      currentHref="/outcomes"
      workspace={activeWorkspace ? { name: activeWorkspace.name, detail: accountRoleLabel(user, activeWorkspace) } : undefined}
      user={user ? { name: displayName(user), detail: user.email } : undefined}
      onSignOut={user ? signOutToLogin : undefined}
      crumbs={<Crumbs items={[{ label: activeWorkspace?.name ?? "Workspace" }, { label: "Business view" }]} />}
      topbarRight={
        user ? (
          <>
            {hasCapability(user, CAPABILITIES.applicationAccess) ? <a className="btn" href="/app/labs">Labs</a> : null}
            <WorkspaceSelector inputId="business-workspace" />
          </>
        ) : null
      }
    >
      <div key={activeWorkspaceId ?? "no-workspace"} style={{ display: "contents" }}>
        {viewAs.role ? <ViewAsBanner role={viewAs.role} onStop={() => viewAs.choose(null)} /> : null}
        {body}
      </div>
    </Shell>
  );
}
