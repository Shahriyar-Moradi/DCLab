import type { ReactNode } from "react";

export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <main id="main" className="auth-shell">
      {children}
    </main>
  );
}
