import { BrandLogo } from "@/app/components/brand/BrandLogo";
import { HealthPill } from "@/app/components/layout/HealthPill";
import Link from "next/link";

export function SiteFooter() {
  return (
    <footer className="border-t border-hairline bg-midnight text-white">
      <div className="mx-auto grid max-w-7xl gap-10 px-5 py-14 lg:grid-cols-4 lg:px-8">
        <div className="lg:col-span-1">
          <BrandLogo />
          <p className="mt-4 max-w-xs text-sm leading-6 text-white/65">
            The AI decision layer for revenue teams. We score opportunities, recommend actions, and run
            reproducible experiments — a decision layer, not a CRM.
          </p>
          <div className="mt-6">
            <HealthPill />
          </div>
        </div>
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.12em] text-cyan">Platform</p>
          <ul className="mt-3 space-y-2 text-sm text-white/70">
            <li><Link href="/platform">How it works</Link></li>
            <li><Link href="/dashboards">Dashboard</Link></li>
            <li><Link href="/lab">Experimentation Lab</Link></li>
            <li><Link href="/resources">Integrations &amp; benchmarks</Link></li>
          </ul>
        </div>
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.12em] text-cyan">Workspace</p>
          <ul className="mt-3 space-y-2 text-sm text-white/70">
            <li><Link href="/opportunities">Opportunities</Link></li>
            <li><Link href="/decisions">Decisions</Link></li>
            <li><Link href="/opportunities/upload">Upload opportunities</Link></li>
            <li><Link href="/lab">Experimentation Lab</Link></li>
          </ul>
        </div>
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.12em] text-cyan">Company</p>
          <ul className="mt-3 space-y-2 text-sm text-white/70">
            <li><Link href="/resources">Case study</Link></li>
            <li><Link href="/company">Services</Link></li>
            <li><Link href="/pricing">Pricing</Link></li>
            <li><Link href="mailto:hello@decision.ai?subject=Book%20a%20demo">Book a demo</Link></li>
          </ul>
        </div>
      </div>
      <div className="border-t border-white/10 px-5 py-4 text-center text-xs text-white/40 lg:px-8">
        Decision.ai — internal decision layer. No roles or auth in this build; every workspace page is open.
      </div>
    </footer>
  );
}
