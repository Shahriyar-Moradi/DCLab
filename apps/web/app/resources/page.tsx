import { MarketingShell, PageHero } from "@/app/components/marketing/primitives";
import { CaseStudySection, GetStartedCTA, IntegrationsSection } from "@/app/components/marketing/sections";

export default function ResourcesPage() {
  return (
    <MarketingShell>
      <PageHero
        eyebrow="Resources"
        title="Proof, Not Promises"
        subtitle="Integrations, case studies, and the connectors that feed the decision layer."
      />
      <IntegrationsSection />
      <CaseStudySection />
      <GetStartedCTA />
    </MarketingShell>
  );
}
