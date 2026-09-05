"use client";

import { BrandLogo } from "@/app/components/brand/BrandLogo";
import {
  activeNavigationItem,
  defaultProductRoute,
  navigationForRole,
  type AppNavigationSection,
} from "@/app/components/layout/app-navigation";
import { cn } from "@/lib/cn";
import { useSession } from "@/lib/application";
import { displayName, roleLabel } from "@/lib/infrastructure/session";
import { LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

type AppSidebarProps = {
  mobile?: boolean;
  onNavigate?: () => void;
};

function SidebarNavigation({
  sections,
  pathname,
  onNavigate,
}: {
  sections: AppNavigationSection[];
  pathname: string;
  onNavigate?: () => void;
}) {
  return (
    <nav className="app-sidebar-nav" aria-label="Application navigation">
      {sections.map((section) => (
        <div key={section.id} className="app-nav-section">
          <p className="app-nav-section-label">{section.label}</p>
          <div className="space-y-1">
            {section.items.map((item) => {
              const Icon = item.icon;
              const active = item.isActive(pathname);
              return (
                <Link
                  key={item.id}
                  href={item.href}
                  onClick={onNavigate}
                  aria-current={active ? "page" : undefined}
                  className={cn("app-nav-item", active && "app-nav-item-active")}
                >
                  <Icon size={18} strokeWidth={1.8} aria-hidden />
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

export function AppSidebar({ mobile = false, onNavigate }: AppSidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loaded, signOut } = useSession();
  const sections = navigationForRole(user);
  const active = activeNavigationItem(pathname, user);

  function handleSignOut() {
    onNavigate?.();
    signOut();
    router.push("/login");
    router.refresh();
  }

  const accountName = user ? displayName(user) : "Loading account";
  const accountRole = user ? roleLabel(user.role) : "Checking session";

  return (
    <aside className={cn("app-sidebar", mobile && "app-sidebar-mobile")}>
      <div className="app-sidebar-brand">
        <BrandLogo product href={user ? defaultProductRoute(user.role) : "/app/dashboards"} />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
        {loaded && user ? (
          <SidebarNavigation sections={sections} pathname={pathname} onNavigate={onNavigate} />
        ) : (
          <div className="px-3 pt-7 text-sm text-ink-muted">Loading navigation…</div>
        )}
      </div>

      <div className="app-sidebar-account" role="group" aria-label="Account">
        <div className="flex min-w-0 items-center gap-3">
          <div className="app-account-avatar" aria-hidden>
            {accountName.slice(0, 1).toUpperCase()}
          </div>
          <div className="min-w-0">
            <p className="truncate text-[0.9375rem] font-semibold text-ink">{accountName}</p>
            <p className="truncate text-[0.8125rem] text-ink-muted">{accountRole}</p>
            {user ? <p className="truncate text-[0.8125rem] text-ink-muted/80">{user.email}</p> : null}
          </div>
        </div>
        {user ? (
          <button type="button" onClick={handleSignOut} className="app-sign-out">
            <LogOut size={16} aria-hidden />
            Sign out
          </button>
        ) : null}
      </div>

      {active ? <p className="sr-only">Current section: {active.label}</p> : null}
    </aside>
  );
}
