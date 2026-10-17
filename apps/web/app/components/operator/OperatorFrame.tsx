"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, type ReactNode } from "react";
import { operatorNavigationForUser } from "@/app/components/layout/app-navigation";
import { useSignOutToLogin } from "@/app/components/layout/use-sign-out";
import { Crumbs } from "@/components/studio/Crumbs";
import { Shell } from "@/components/studio/Shell";
import { useSession } from "@/lib/application";
import { OPERATOR_TABS, tabFromQuery } from "@/lib/application/operator";
import { displayName, roleLabel } from "@/lib/infrastructure/session";

function Frame({ children }: { children: ReactNode }) {
  const { user, loaded } = useSession();
  const signOutToLogin = useSignOutToLogin();
  const tab = tabFromQuery(useSearchParams().get("tab"));
  const nav = operatorNavigationForUser(user);
  const current = nav.flatMap((group) => group.items).find((item) => item.href === (tab === "overview" ? "/operator" : `/operator?tab=${tab}`));
  const label = OPERATOR_TABS.find((t) => t.id === tab)?.label ?? "Overview";

  let body: ReactNode = children;
  if (!loaded) body = <p role="status">Loading your account…</p>;
  else if (!user) body = <p>Your session ended. <Link href="/login">Sign in again</Link>.</p>;

  return (
    <Shell
      nav={nav}
      currentHref={current?.href}
      user={user ? { name: displayName(user), detail: `${roleLabel(user.role)} · ${user.email}` } : undefined}
      onSignOut={user ? signOutToLogin : undefined}
      crumbs={<Crumbs items={[{ label: "Operator console", href: "/operator" }, { label }]} />}
      topbarRight={user ? <a className="btn" href="/admin/monitoring">Platform admin pages</a> : null}
    >
      {body}
    </Shell>
  );
}

/** Operator console frame (P8.5-UI): platform administrators only (the middleware returns 403 to everyone else; the API authorizes). */
export function OperatorFrame({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<p role="status">Loading the operator console…</p>}>
      <Frame>{children}</Frame>
    </Suspense>
  );
}
