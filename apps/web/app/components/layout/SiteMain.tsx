"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

// Marketing pages manage their own full-bleed sections and inner max-width
// containers. Workspace pages (opportunities, decisions, lab, upload) render a
// bare page body and rely on the shell for consistent padding.
const FULL_BLEED_ROUTES = ["/company", "/solutions", "/platform", "/industries", "/resources", "/pricing", "/dashboards"];

function isFullBleed(pathname: string) {
  if (pathname === "/") return true;
  return FULL_BLEED_ROUTES.some((route) => pathname === route || pathname.startsWith(`${route}/`));
}

export function SiteMain({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return (
    <main id="main" className={isFullBleed(pathname) ? undefined : "mx-auto max-w-7xl px-5 py-10 lg:px-8 lg:py-14"}>
      {children}
    </main>
  );
}
