import { BrandLogo } from "@/app/components/brand/BrandLogo";
import { HealthPill } from "@/app/components/layout/HealthPill";
import Link from "next/link";

export function SiteFooter() {
  return (
    <footer className="bg-midnight text-white">
      <div className="mx-auto grid max-w-7xl gap-10 px-5 py-14 lg:grid-cols-4 lg:px-8">
        <div className="lg:col-span-1">
          <BrandLogo inverted />
          <p className="mt-4 max-w-xs text-sm leading-6 text-white/65">
            The AI Decision Intelligence Company. We build autonomous AI systems that continuously improve marketing,
            sales, pricing, and customer success through predictive intelligence.
          </p>
          <div className="mt-6">
            <HealthPill inverted />
          </div>
        </div>
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.12em] text-cyan">Platform</p>
          <ul className="mt-3 space-y-2 text-sm text-white/70">
            <li><Link href="/platform">AI Agents</Link></li>
            <li><Link href="/solutions">Machine Learning</Link></li>
            <li><Link href="/dashboards">Dashboard</Link></li>
            <li><Link href="/resources">Integrations</Link></li>
          </ul>
        </div>
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.12em] text-cyan">Solutions</p>
          <ul className="mt-3 space-y-2 text-sm text-white/70">
            <li><Link href="/solutions">Marketing Intelligence</Link></li>
            <li><Link href="/solutions">Sales Intelligence</Link></li>
            <li><Link href="/solutions">Pricing Intelligence</Link></li>
            <li><Link href="/solutions">Customer Intelligence</Link></li>
          </ul>
        </div>
        <div>
          <p className="text-xs font-bold uppercase tracking-[0.12em] text-cyan">Company</p>
          <ul className="mt-3 space-y-2 text-sm text-white/70">
            <li><Link href="/resources">Case Studies</Link></li>
            <li><Link href="/company">Services</Link></li>
            <li><Link href="/pricing">Pricing</Link></li>
            <li><Link href="/opportunities/upload">Book a Demo</Link></li>
          </ul>
        </div>
      </div>
    </footer>
  );
}
