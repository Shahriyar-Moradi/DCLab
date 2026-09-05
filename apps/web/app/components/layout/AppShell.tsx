"use client";

import { BrandLogo } from "@/app/components/brand/BrandLogo";
import { AppMobileDrawer } from "@/app/components/layout/AppMobileDrawer";
import { AppSidebar } from "@/app/components/layout/AppSidebar";
import { CommandPalette, useCommandPaletteShortcut } from "@/app/components/layout/CommandPalette";
import { activeNavigationItem, defaultProductRoute } from "@/app/components/layout/app-navigation";
import { useSidebarCollapsed } from "@/app/components/layout/useSidebarCollapsed";
import { useSession } from "@/lib/application";
import { displayName } from "@/lib/infrastructure/session";
import { Menu, Search } from "lucide-react";
import { usePathname } from "next/navigation";
import { useCallback, useState, type ReactNode } from "react";

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { user } = useSession();
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const { collapsed, toggle } = useSidebarCollapsed();
  const active = activeNavigationItem(pathname, user);
  const home = user ? defaultProductRoute(user.role) : "/app/dashboards";
  const accountName = user ? displayName(user) : "Account";
  const togglePalette = useCallback(() => setPaletteOpen((open) => !open), []);
  useCommandPaletteShortcut(togglePalette);

  return (
    <div className="app-shell font-sans">
      <div className={`app-sidebar-rail hidden h-full lg:block${collapsed ? " is-collapsed" : ""}`}>
        <AppSidebar collapsed={collapsed} onToggleCollapse={toggle} />
      </div>
      <div className="app-shell-workspace">
        <header className="app-topbar">
          <button
            type="button"
            className="app-topbar-menu"
            aria-label="Open application navigation"
            aria-expanded={mobileNavigationOpen}
            aria-controls="app-mobile-navigation"
            onClick={() => setMobileNavigationOpen(true)}
          >
            <Menu size={20} aria-hidden />
          </button>
          <BrandLogo product compact className="app-topbar-logo" href={home} />
          <p className="app-topbar-context">{active?.label ?? "Workspace"}</p>
          <button
            type="button"
            className="app-command-trigger"
            aria-label="Search destinations"
            aria-keyshortcuts="Meta+K Control+K"
            aria-expanded={paletteOpen}
            aria-haspopup="dialog"
            onClick={() => setPaletteOpen(true)}
          >
            <Search size={16} aria-hidden />
            <span className="app-command-trigger-label">Search</span>
            <kbd className="app-command-trigger-keys">⌘K</kbd>
          </button>
          <button
            type="button"
            className="app-topbar-account"
            aria-label="Open account"
            aria-expanded={mobileNavigationOpen}
            aria-controls="app-mobile-navigation"
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
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
