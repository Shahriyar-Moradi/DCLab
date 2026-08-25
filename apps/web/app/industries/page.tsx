import { FeatureCard, MarketingShell, PageHero } from "@/app/components/marketing/primitives";
import { GetStartedCTA, INDUSTRIES } from "@/app/components/marketing/sections";

export default function IndustriesPage() {
  return (
    <MarketingShell>
      <PageHero
        eyebrow="Industries"
        title="Decision intelligence for operators"
        subtitle="A horizontal decision layer on top of the stack you already run — not a new CRM, and not a replacement for your team."
      />
      <div className="mx-auto mt-12 grid max-w-7xl gap-4 px-5 sm:grid-cols-2 lg:px-8">
        {INDUSTRIES.map((item) => (
          <FeatureCard key={item.title} {...item} />
        ))}
      </div>
      <GetStartedCTA />
    </MarketingShell>
  );
}
