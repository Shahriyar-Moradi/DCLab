"use client";

import { SiteFooter } from "@/app/components/layout/SiteFooter";
import { SiteHeader } from "@/app/components/layout/SiteHeader";
import { SiteMain } from "@/app/components/layout/SiteMain";
import type { ReactNode } from "react";

export function MarketingShell({ children }: { children: ReactNode }) {
  return (
    <>
      <SiteHeader />
      <SiteMain>{children}</SiteMain>
      <SiteFooter />
    </>
  );
}
