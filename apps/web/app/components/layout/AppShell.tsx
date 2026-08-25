"use client";

import { LayoutDashboard, ListChecks, Menu, Upload, Wallet, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { cn } from "@/lib/cn";
import { HealthPill } from "./HealthPill";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/opportunities", label: "Opportunities", icon: Wallet },
  { href: "/decisions", label: "Decisions", icon: ListChecks },
  { href: "/opportunities/upload", label: "Upload", icon: Upload },
];

function NavLinks({ collapsed, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Primary">
      <ul className="flex flex-col gap-1">
        {NAV.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : item.href === "/opportunities"
                ? pathname.startsWith("/opportunities") && !pathname.startsWith("/opportunities/upload")
                : pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                onClick={onNavigate}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-3 rounded px-3 py-2 font-body text-body text-ink",
                  active && "bg-navy-soft",
                  collapsed && "justify-center px-2",
                )}
              >
                <Icon size={18} strokeWidth={1.5} aria-hidden />
                {collapsed ? <span className="sr-only">{item.label}</span> : item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="min-h-screen bg-paper text-ink">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:bg-navy focus:px-3 focus:py-2 focus:text-paper-raised">
        Skip to content
      </a>
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 border-r border-hairline bg-paper-raised px-4 py-8 lg:block">
        <p className="px-3 font-display text-section text-ink">Decision.ai</p>
        <p className="mt-1 px-3 font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Revenue ops</p>
        <div className="mt-8">
          <NavLinks />
        </div>
      </aside>
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-16 border-r border-hairline bg-paper-raised px-2 py-8 md:block lg:hidden">
        <NavLinks collapsed />
      </aside>
      {open ? (
        <div className="fixed inset-0 z-40 md:hidden">
          <button type="button" className="absolute inset-0 bg-ink/30" aria-label="Close menu" onClick={() => setOpen(false)} />
          <aside className="relative h-full w-60 bg-paper-raised px-4 py-8">
            <div className="flex items-center justify-between px-3">
              <p className="font-display text-section text-ink">Decision.ai</p>
              <button type="button" onClick={() => setOpen(false)} aria-label="Close navigation">
                <X size={18} strokeWidth={1.5} />
              </button>
            </div>
            <div className="mt-8">
              <NavLinks onNavigate={() => setOpen(false)} />
            </div>
          </aside>
        </div>
      ) : null}
      <div className="md:pl-16 lg:pl-60">
        <header className="flex items-center justify-between border-b border-hairline bg-paper px-6 py-4 lg:px-12">
          <button type="button" className="md:hidden" aria-label="Open navigation" onClick={() => setOpen(true)}>
            <Menu size={20} strokeWidth={1.5} />
          </button>
          <div className="ml-auto">
            <HealthPill />
          </div>
        </header>
        <main id="main" className="px-6 py-8 lg:px-12 lg:py-12">
          {children}
        </main>
      </div>
    </div>
  );
}
