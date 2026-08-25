import { MarketingShell, PageHero } from "@/app/components/marketing/primitives";
import { AgentsGrid, GetStartedCTA, PlatformPills } from "@/app/components/marketing/sections";

export default function PlatformPage() {
  return (
    <MarketingShell className="pb-0">
      <PageHero
        eyebrow="Our platform"
        title="Meet The Platform"
        subtitle="Everything your business needs in one intelligent operating system."
      />
      <section className="bg-midnight-glow mt-12 text-white">
        <div className="mx-auto max-w-3xl px-5 pt-12">
          <div className="flex h-12 items-center rounded-full border border-white/10 bg-white/5 px-5 text-sm text-white/40">
            Search the operating system…
          </div>
        </div>
        <PlatformPills />
        <div className="mx-auto max-w-3xl px-5 pt-16 text-center lg:px-8">
          <p className="text-eyebrow uppercase text-cyan">AI Agents</p>
          <h2 className="mt-4 text-3xl font-bold text-white lg:text-4xl">Autonomous Agents, Always Working</h2>
          <p className="mt-3 text-white/70">Six specialized AI agents that learn, predict, and recommend — 24/7.</p>
        </div>
        <div className="pb-16">
          <AgentsGrid />
        </div>
      </section>
      <div className="mx-auto max-w-3xl px-5 py-20 text-center lg:px-8">
        <p className="text-eyebrow uppercase text-brand">Platform architecture</p>
        <h2 className="mt-4 text-3xl font-bold text-ink lg:text-4xl">How the Intelligence Flows</h2>
        <p className="mt-3 text-ink-muted">
          From business goals to AI-powered recommendations — the full architecture of continuous intelligence.
        </p>
      </div>
      <GetStartedCTA />
    </MarketingShell>
  );
}
