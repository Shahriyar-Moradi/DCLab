import { MarketingShell, PageHero } from "@/app/components/marketing/primitives";
import { GetStartedCTA, PricingGrid } from "@/app/components/marketing/sections";

export default function PricingPage() {
  return (
    <MarketingShell>
      <PageHero
        eyebrow="Pricing"
        title="Enterprise-Grade, Scaled to You"
        subtitle="Start small, scale infinitely. Pricing that grows with your intelligence."
      />
      <PricingGrid />
      <GetStartedCTA />
    </MarketingShell>
  );
}
