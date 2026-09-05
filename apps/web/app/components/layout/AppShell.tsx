"use client";

import { BrandLogo } from "@/app/components/brand/BrandLogo";
import { AppMobileDrawer } from "@/app/components/layout/AppMobileDrawer";
import { AppSidebar } from "@/app/components/layout/AppSidebar";
import { activeNavigationItem, defaultProductRoute } from "@/app/components/layout/app-navigation";
import { useSession } from "@/lib/application";
import { displayName } from "@/lib/infrastructure/session";
import { Menu } from "lucide-react";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { user } = useSession();
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);
  const active = activeNavigationItem(pathname, user);
  const home = user ? defaultProductRoute(user.role) : "/app/dashboards";
  const accountName = user ? displayName(user) : "Account";

  return (
    <div className="app-shell font-sans">
      <div className="app-sidebar-rail hidden h-full lg:block">
        <AppSidebar />
      </div>
      <div className="app-shell-workspace">
        <header className="app-topbar">
          <button
            type="button"
            className="app-topbar-menu"
            aria-label="Open application navigation"
            aria-expanded={mobileNavigationOpen}
            onClick={() => setMobileNavigationOpen(true)}
          >
            <Menu size={20} aria-hidden />
          </button>
          <BrandLogo product compact className="app-topbar-logo" href={home} />
          <p className="app-topbar-context">{active?.label ?? "Workspace"}</p>
          <button
            type="button"
            className="app-topbar-account"
            aria-label="Open account"
            aria-expanded={mobileNavigationOpen}
            onClick={() => setMobileNavigationOpen(true)}
          >
            <span className="app-account-avatar" aria-hidden>
              {accountName.slice(0, 1).toUpperCase()}
            </span>
          </button>
        </header>

        <main id="main" className="min-w-0">
          <div className="app-page">{children}</div>
        </main>
      </div>
      <AppMobileDrawer open={mobileNavigationOpen} onClose={() => setMobileNavigationOpen(false)} />
    </div>
  );
}
