"use client";

import { AppShell } from "@/app/components/layout/AppShell";
import { AuthShell } from "@/app/components/layout/AuthShell";
import { MarketingShell } from "@/app/components/layout/MarketingShell";
import { isAuthRoute, isProductRoute } from "@/app/components/layout/app-navigation";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

export function RouteShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  if (isProductRoute(pathname)) {
    return <AppShell>{children}</AppShell>;
  }

  if (isAuthRoute(pathname)) {
    return <AuthShell>{children}</AuthShell>;
  }

  return <MarketingShell>{children}</MarketingShell>;
}
