import { FeatureCard, MarketingPage, MarketingWrap, PageHero } from "@/app/components/marketing/primitives";
import { GetStartedCTA, INDUSTRIES } from "@/app/components/marketing/sections";

export default function IndustriesPage() {
  return (
    <MarketingPage>
      <PageHero
        eyebrow="Industries"
        title="Decision intelligence for operators"
        subtitle="A horizontal decision layer on top of the stack you already run — not a new CRM, and not a replacement for your team."
      />
      <MarketingWrap className="grid gap-4 pb-8 sm:grid-cols-2">
        {INDUSTRIES.map((item) => (
          <FeatureCard key={item.title} {...item} />
        ))}
      </MarketingWrap>
      <GetStartedCTA />
    </MarketingPage>
  );
}
