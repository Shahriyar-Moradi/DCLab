import { MarketingPage, PageHero } from "@/app/components/marketing/primitives";
import { GetStartedCTA, PricingPanel } from "@/app/components/marketing/sections";

export default function PricingPage() {
  return (
    <MarketingPage>
      <PageHero
        eyebrow="Pricing"
        title="Talk to the team about access"
        subtitle="This deployment does not publish self-serve plan prices. Sign in if you have an account, or book a demo."
      />
      <PricingPanel />
      <GetStartedCTA />
    </MarketingPage>
  );
}
