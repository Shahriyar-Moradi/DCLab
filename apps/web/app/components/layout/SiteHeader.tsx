"use client";

import { BrandLogo } from "@/app/components/brand/BrandLogo";
import { cn } from "@/lib/cn";
import { Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

const MARKETING = [
  { href: "/company", label: "Company" },
  { href: "/solutions", label: "Solutions" },
  { href: "/platform", label: "Platform" },
  { href: "/industries", label: "Industries" },
  { href: "/resources", label: "Resources" },
  { href: "/dashboards", label: "Dashboards" },
  { href: "/pricing", label: "Pricing" },
];

const WORKSPACE = [
  { href: "/opportunities", label: "Opportunities" },
  { href: "/decisions", label: "Decisions" },
  { href: "/opportunities/upload", label: "Upload" },
  { href: "/lab", label: "Experimentation Lab" },
];

function isActive(pathname: string, href: string) {
  if (href === "/opportunities") {
    return pathname.startsWith("/opportunities") && !pathname.startsWith("/opportunities/upload");
  }
  if (href === "/lab") return pathname.startsWith("/lab");
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function SiteHeader({ inverted = false }: { inverted?: boolean }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const text = inverted ? "text-white/80 hover:text-white" : "text-ink-muted hover:text-ink";
  const active = inverted ? "text-white" : "text-ink";

  return (
    <header className={cn("relative z-40 border-b", inverted ? "border-white/10 bg-midnight/80 backdrop-blur" : "border-hairline bg-white/90 backdrop-blur")}>
      <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-5 py-3 lg:px-8">
        <BrandLogo inverted={inverted} />
        <nav className="hidden items-center gap-5 xl:flex" aria-label="Marketing">
          {MARKETING.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={cn("text-[0.82rem] font-medium", isActive(pathname, item.href) ? active : text)}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="hidden items-center gap-4 md:flex">
          <Link href="/dashboards" className={cn("text-[0.82rem] font-medium", text)}>
            Sign In
          </Link>
          <Link
            href="/opportunities/upload"
            className="bg-brand-gradient shadow-brand rounded-full px-4 py-2 text-[0.82rem] font-semibold text-white"
          >
            Book a Demo
          </Link>
        </div>
        <button type="button" className="xl:hidden" aria-label="Open menu" onClick={() => setOpen(true)}>
          <Menu size={22} className={inverted ? "text-white" : "text-ink"} />
        </button>
      </div>
      <div className={cn("border-t", inverted ? "border-white/10 bg-white/5" : "border-hairline bg-navy-soft/50")}>
        <nav className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-5 gap-y-2 px-5 py-2 lg:px-8" aria-label="Workspace">
          <span className={cn("text-[0.68rem] font-bold uppercase tracking-[0.1em]", inverted ? "text-cyan" : "text-brand")}>
            Workspace
          </span>
          {WORKSPACE.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "text-[0.82rem] font-medium",
                isActive(pathname, item.href) ? (inverted ? "text-white" : "text-ink") : text,
              )}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </div>
      {open ? (
        <div className="fixed inset-0 z-50 bg-ink/40 xl:hidden">
          <aside className="h-full w-72 bg-white p-5">
            <div className="flex items-center justify-between">
              <BrandLogo />
              <button type="button" aria-label="Close menu" onClick={() => setOpen(false)}>
                <X size={20} />
              </button>
            </div>
            <nav className="mt-8 flex flex-col gap-3">
              {[...MARKETING, ...WORKSPACE].map((item) => (
                <Link key={item.href} href={item.href} onClick={() => setOpen(false)} className="text-ink">
                  {item.label}
                </Link>
              ))}
              <Link href="/dashboards" onClick={() => setOpen(false)} className="text-ink-muted">
                Sign In
              </Link>
              <Link
                href="/opportunities/upload"
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
