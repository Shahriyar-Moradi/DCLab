import { MarketingPage, PageHero } from "@/app/components/marketing/primitives";
import { DataInSection, GetStartedCTA } from "@/app/components/marketing/sections";

export default function ResourcesPage() {
  return (
    <MarketingPage>
      <PageHero
        eyebrow="Resources"
        title="How the product is used"
        subtitle="Upload paths and operating surfaces. This page does not publish customer case-study scores or a vendor logo wall."
      />
      <DataInSection />
      <GetStartedCTA />
    </MarketingPage>
  );
}
