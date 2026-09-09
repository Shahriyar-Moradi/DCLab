import { BrandLogo } from "@/app/components/brand/BrandLogo";
import type { ReactNode } from "react";

export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="auth-shell">
      <header className="absolute inset-x-0 top-0 z-20">
        <div className="marketing-wrap flex h-16 items-center">
          <BrandLogo compact />
        </div>
      </header>
      <main id="main">{children}</main>
    </div>
  );
}
