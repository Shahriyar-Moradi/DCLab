"use client";

import { BrandLogo } from "@/app/components/brand/BrandLogo";
import { useSession } from "@/lib/application";
import { displayName, roleLabel } from "@/lib/infrastructure/session";
import { cn } from "@/lib/cn";
import { Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";

const MARKETING = [
  { href: "/company", label: "Company" },
  { href: "/solutions", label: "Solutions" },
  { href: "/platform", label: "Platform" },
  { href: "/industries", label: "Industries" },
  { href: "/resources", label: "Resources" },
  { href: "/pricing", label: "Pricing" },
];

// Business-facing workspace. Everything here is client-safe.
const WORKSPACE = [
  { href: "/app/dashboards", label: "Dashboard" },
  { href: "/app/insights", label: "Insights" },
  { href: "/app/opportunities", label: "Opportunities" },
  { href: "/app/decisions", label: "Decisions" },
  { href: "/app/opportunities/upload", label: "Upload" },
  { href: "/app/labs", label: "Labs" },
];

// DCLab staff only. Never rendered for a client user, and the middleware plus
// the API both reject the routes independently of what the nav shows.
const ADMIN = [
  { href: "/admin/organizations", label: "Organizations" },
  { href: "/admin/lab", label: "Labs & Experiments" },
  { href: "/admin/models", label: "Registry" },
  { href: "/admin/monitoring", label: "Monitoring" },
];

const BOOK_A_DEMO_HREF = "mailto:hello@decision.ai?subject=Book%20a%20demo";

function isActive(pathname: string, href: string) {
  if (href === "/app/labs") {
    return pathname.startsWith("/app/labs") || pathname.startsWith("/lab/runs");
  }
  if (href === "/app/opportunities") {
    return pathname.startsWith("/app/opportunities") && !pathname.startsWith("/app/opportunities/upload");
  }
  if (href === "/admin/lab") return pathname.startsWith("/admin/lab");
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SiteHeader() {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const { user, loaded, signOut } = useSession();
  const isAdmin = user?.role === "dclab_admin";
  const workspaceNav = isAdmin ? [...WORKSPACE, ...ADMIN] : WORKSPACE;

  function handleSignOut() {
    signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <header className="relative z-40 border-b border-hairline bg-paper/85 backdrop-blur">
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-5 py-3 lg:px-8">
        <BrandLogo />
        <nav className="hidden items-center gap-5 xl:flex" aria-label="Marketing">
          {MARKETING.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "text-[0.82rem] font-medium",
                isActive(pathname, item.href) ? "text-ink" : "text-ink-muted hover:text-ink",
              )}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="hidden items-center gap-4 md:flex">
          {user ? (
            <>
              <div className="text-right">
                <p className="text-[0.82rem] font-medium text-ink">Signed in as {displayName(user)}</p>
                <p className="text-[0.68rem] text-ink-muted">
                  {user.email} · {roleLabel(user.role)}
                </p>
              </div>
              <button
                type="button"
                onClick={handleSignOut}
                className="text-[0.82rem] font-medium text-ink-muted hover:text-ink"
              >
                Sign out
              </button>
            </>
          ) : (
            <Link href="/login" className="text-[0.82rem] font-medium text-ink-muted hover:text-ink">
              Sign In
            </Link>
          )}
          <Link
            href={BOOK_A_DEMO_HREF}
            className="bg-brand-gradient shadow-brand rounded-full px-4 py-2 text-[0.82rem] font-semibold text-white"
          >
            Book a Demo
          </Link>
        </div>
        <button type="button" className="xl:hidden" aria-label="Open menu" onClick={() => setOpen(true)}>
          <Menu size={22} className="text-ink" />
        </button>
      </div>
      {loaded && user ? (
      <div className="border-t border-hairline bg-navy-soft/40">
        <nav className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-5 gap-y-2 px-5 py-2 lg:px-8" aria-label="Workspace">
          <span className="text-[0.68rem] font-bold uppercase tracking-[0.1em] text-brand">
            {isAdmin ? "Admin" : "Business Client"}
          </span>
          {workspaceNav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "text-[0.82rem] font-medium",
                isActive(pathname, item.href) ? "text-ink" : "text-ink-muted hover:text-ink",
              )}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
      ) : null}
      {open ? (
        <div className="fixed inset-0 z-50 bg-black/60 xl:hidden">
          <aside className="h-full w-72 bg-paper-raised p-5">
            <div className="flex items-center justify-between">
              <BrandLogo />
              <button type="button" aria-label="Close menu" onClick={() => setOpen(false)}>
                <X size={20} className="text-ink" />
              </button>
            </div>
            <nav className="mt-8 flex flex-col gap-3">
              {[...MARKETING, ...(user ? workspaceNav : [])].map((item) => (
                <Link key={item.href} href={item.href} onClick={() => setOpen(false)} className="text-ink">
                  {item.label}
                </Link>
              ))}
              {user ? (
                <>
                  <p className="mt-6 font-body text-body text-ink">Signed in as {displayName(user)}</p>
                  <p className="font-body text-body text-ink-muted">
                    {user.email} · {roleLabel(user.role)}
                  </p>
                  <button
                    type="button"
                    onClick={() => {
                      setOpen(false);
                      handleSignOut();
                    }}
                    className="text-left text-ink-muted"
                  >
                    Sign out
                  </button>
                </>
              ) : (
                <Link href="/login" onClick={() => setOpen(false)} className="text-ink-muted">
                  Sign In
                </Link>
              )}
              <Link
                href={BOOK_A_DEMO_HREF}
                onClick={() => setOpen(false)}
                className="bg-brand-gradient mt-2 rounded-full px-4 py-2 text-center text-white"
              >
                Book a Demo
              </Link>
            </nav>
          </aside>
        </div>
      ) : null}
    </header>
  );
}
