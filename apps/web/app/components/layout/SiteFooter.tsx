import { BrandLogo } from "@/app/components/brand/BrandLogo";
import { HealthPill } from "@/app/components/layout/HealthPill";
import { BOOK_A_DEMO_HREF } from "@/app/components/marketing/links";
import Link from "next/link";

export function SiteFooter() {
  return (
    <footer className="border-t border-white/10 bg-midnight text-white">
      <div className="marketing-wrap grid gap-10 py-14 lg:grid-cols-4">
        <div className="lg:col-span-1">
          <BrandLogo invert />
          <p className="mt-4 max-w-xs text-sm leading-6 text-white/65">
            The AI decision layer for revenue teams. We score opportunities, recommend actions, and run
            reproducible experiments — a decision layer, not a CRM.
          </p>
          <div className="mt-6">
            <HealthPill invert />
          </div>
        </div>
        <div>
          <p className="text-eyebrow uppercase tracking-[0.18em] text-cyan">Platform</p>
          <ul className="mt-3 space-y-2 text-sm text-white/70">
            <li><Link href="/platform" className="hover:text-white">How it works</Link></li>
            <li><Link href="/app/dashboards" className="hover:text-white">Dashboard</Link></li>
            <li><Link href="/app/labs" className="hover:text-white">Labs</Link></li>
            <li><Link href="/resources" className="hover:text-white">Integrations &amp; benchmarks</Link></li>
          </ul>
        </div>
        <div>
          <p className="text-eyebrow uppercase tracking-[0.18em] text-cyan">Workspace</p>
          <ul className="mt-3 space-y-2 text-sm text-white/70">
            <li><Link href="/app/opportunities" className="hover:text-white">Opportunities</Link></li>
            <li><Link href="/app/decisions" className="hover:text-white">Decisions</Link></li>
            <li><Link href="/app/opportunities/upload" className="hover:text-white">Upload opportunities</Link></li>
            <li><Link href="/app/labs" className="hover:text-white">Labs</Link></li>
          </ul>
        </div>
        <div>
          <p className="text-eyebrow uppercase tracking-[0.18em] text-cyan">Company</p>
          <ul className="mt-3 space-y-2 text-sm text-white/70">
            <li><Link href="/resources" className="hover:text-white">Resources</Link></li>
            <li><Link href="/company" className="hover:text-white">Services</Link></li>
            <li><Link href="/pricing" className="hover:text-white">Pricing</Link></li>
            <li><Link href={BOOK_A_DEMO_HREF} className="hover:text-white">Book a demo</Link></li>
          </ul>
        </div>
      </div>
      <div className="border-t border-white/10 py-4 text-center text-xs text-white/40">
        Decision.ai — internal decision layer for secure, role-aware workspaces.
      </div>
    </footer>
  );
}
