"use client";

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

// Marketing pages manage their own full-bleed sections and inner max-width
// containers. Authenticated product routes use AppShell instead of this main.
const FULL_BLEED_ROUTES = ["/company", "/solutions", "/platform", "/industries", "/resources", "/pricing"];

function isFullBleed(pathname: string) {
  if (pathname === "/") return true;
  return FULL_BLEED_ROUTES.some((route) => pathname === route || pathname.startsWith(`${route}/`));
}

export function SiteMain({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return (
    <main id="main" className={isFullBleed(pathname) ? undefined : "site-page"}>
      {children}
    </main>
  );
}
