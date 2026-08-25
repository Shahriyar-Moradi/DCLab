import { MarketingShell, PageHero } from "@/app/components/marketing/primitives";
import { GetStartedCTA, ServicesGrid, WhyUsSection } from "@/app/components/marketing/sections";

export default function CompanyPage() {
  return (
    <MarketingShell>
      <PageHero
        eyebrow="Services"
        title="Consulting Before You Adopt"
        subtitle="Some companies want expert guidance before adopting the platform. We offer both — strategic consulting and the full intelligent operating system."
      />
      <ServicesGrid />
      <WhyUsSection />
      <GetStartedCTA />
    </MarketingShell>
  );
}
