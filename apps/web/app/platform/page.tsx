import { MarketingShell, PageHero } from "@/app/components/marketing/primitives";
import { AgentsGrid, GetStartedCTA, PlatformPills } from "@/app/components/marketing/sections";

export default function PlatformPage() {
  return (
    <MarketingShell className="bg-midnight">
      <PageHero
        invert
        eyebrow="Our platform"
        title="Meet The Platform"
        subtitle="Everything your business needs in one intelligent operating system — the opportunity ledger you use every day, and the Experimentation Lab that keeps its models honest."
      />
      <PlatformPills />
      <div className="mx-auto max-w-3xl px-5 pt-20 text-center lg:px-8">
        <p className="text-eyebrow uppercase text-cyan">AI Agents</p>
        <h2 className="mt-4 text-3xl font-bold text-white lg:text-4xl">Autonomous Agents, Always Working</h2>
        <p className="mt-3 text-white/70">Six specialized AI agents that learn, predict, and recommend — 24/7.</p>
      </div>
      <AgentsGrid />
      <div className="mx-auto max-w-3xl px-5 py-20 text-center lg:px-8">
        <p className="text-eyebrow uppercase text-cyan">Platform architecture</p>
        <h2 className="mt-4 text-3xl font-bold text-white lg:text-4xl">How the Intelligence Flows</h2>
        <p className="mt-3 text-white/70">
          Upload opportunities, the decision engine scores them and returns an eight-field audited decision, then you
          compare models and datasets in the Experimentation Lab — the same engine, no dataset-specific code.
        </p>
      </div>
      <GetStartedCTA />
    </MarketingShell>
  );
}
