import { MarketingPage, MarketingSection, PageHero } from "@/app/components/marketing/primitives";
import { GetStartedCTA, SurfaceGrid } from "@/app/components/marketing/sections";

export default function PlatformPage() {
  return (
    <MarketingPage>
      <PageHero
        eyebrow="Our platform"
        title="Meet The Platform"
        subtitle="Everything your business needs in one intelligent operating system — the opportunity ledger you use every day, and the Experimentation Lab that keeps its models honest."
      />
      <MarketingSection>
        <h2 className="text-center text-title text-ink">Operating surfaces</h2>
        <p className="mx-auto mt-3 max-w-2xl text-center text-body text-ink-muted">
          These links open the product that already ships. Authenticated routes ask you to sign in.
        </p>
        <SurfaceGrid />
        <div className="mx-auto mt-16 max-w-3xl text-center">
          <p className="text-eyebrow uppercase tracking-[0.18em] text-brand">Platform architecture</p>
          <h2 className="mt-4 text-title text-ink">How the intelligence flows</h2>
          <p className="mt-3 text-body text-ink-muted">
            Upload opportunities, the decision engine scores them and returns an audited decision, then you compare
            models and datasets in the Experimentation Lab — the same engine, no dataset-specific code.
          </p>
        </div>
      </MarketingSection>
      <GetStartedCTA />
    </MarketingPage>
  );
}

