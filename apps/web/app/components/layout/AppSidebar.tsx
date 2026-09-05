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
import { ChevronLeft, ChevronRight, LogOut } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

type AppSidebarProps = {
  mobile?: boolean;
  collapsed?: boolean;
  onToggleCollapse?: () => void;
  onNavigate?: () => void;
};

function SidebarNavigation({
  sections,
  pathname,
  collapsed,
  onNavigate,
}: {
  sections: AppNavigationSection[];
  pathname: string;
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  return (
    <nav className="app-sidebar-nav" aria-label="Application navigation">
      {sections.map((section) => (
        <div key={section.id} className="app-nav-section">
          <p className={cn("app-nav-section-label", collapsed && "sr-only")}>{section.label}</p>
          <div className="space-y-1">
            {section.items.map((item) => {
              const Icon = item.icon;
              const active = item.isActive(pathname);
              return (
                <Link
                  key={item.id}
                  href={item.href}
                  title={collapsed ? item.label : undefined}
                  onClick={onNavigate}
                  aria-current={active ? "page" : undefined}
                  className={cn("app-nav-item", active && "app-nav-item-active")}
                >
                  <Icon size={18} strokeWidth={1.8} aria-hidden />
                  <span className={cn(collapsed && "sr-only")}>{item.label}</span>
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

export function AppSidebar({
  mobile = false,
  collapsed = false,
  onToggleCollapse,
  onNavigate,
}: AppSidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, loaded, signOut } = useSession();
  const sections = navigationForRole(user);
  const active = activeNavigationItem(pathname, user);
  const iconOnly = collapsed && !mobile;

  function handleSignOut() {
    onNavigate?.();
    signOut();
    router.push("/login");
    router.refresh();
  }

  const accountName = user ? displayName(user) : "Loading account";
  const accountRole = user ? roleLabel(user.role) : "Checking session";

  return (
    <aside className={cn("app-sidebar", mobile && "app-sidebar-mobile", iconOnly && "is-collapsed")}>
      <div className="app-sidebar-brand">
        <BrandLogo
          product
          compact={iconOnly}
          href={user ? defaultProductRoute(user.role) : "/app/dashboards"}
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-4">
        {loaded && user ? (
          <SidebarNavigation
            sections={sections}
            pathname={pathname}
            collapsed={iconOnly}
            onNavigate={onNavigate}
          />
        ) : (
          <div className="px-3 pt-7 text-sm text-ink-muted">{iconOnly ? "…" : "Loading navigation…"}</div>
        )}
      </div>

      <div className="app-sidebar-account" role="group" aria-label="Account">
        <Link
          href="/app/settings"
          onClick={onNavigate}
          title={iconOnly ? accountName : undefined}
          className="flex min-w-0 items-center gap-3"
        >
          <div className="app-account-avatar" aria-hidden>
            {accountName.slice(0, 1).toUpperCase()}
          </div>
          <div className={cn("min-w-0", iconOnly && "sr-only")}>
            <p className="truncate text-[0.8125rem] font-semibold leading-tight text-ink">{accountName}</p>
            <p className="truncate text-[0.6875rem] leading-tight text-ink-muted">{accountRole}</p>
          </div>
        </Link>
        {user ? (
          <button type="button" onClick={handleSignOut} className="app-sign-out" aria-label="Sign out">
            <LogOut size={16} aria-hidden />
            <span className={cn(iconOnly && "sr-only")}>Sign out</span>
          </button>
        ) : null}
      </div>

      {onToggleCollapse ? (
        <button
          type="button"
          className="app-sidebar-collapse"
          aria-pressed={collapsed}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          onClick={onToggleCollapse}
        >
          {iconOnly ? <ChevronRight size={16} aria-hidden /> : <ChevronLeft size={16} aria-hidden />}
          <span className={cn(iconOnly && "sr-only")}>{collapsed ? "Expand" : "Collapse"}</span>
        </button>
      ) : null}

      {active ? <p className="sr-only">Current section: {active.label}</p> : null}
    </aside>
  );
}
