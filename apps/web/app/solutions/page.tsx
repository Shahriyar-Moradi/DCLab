import { MarketingShell, PageHero } from "@/app/components/marketing/primitives";
import { GetStartedCTA, MLGrid } from "@/app/components/marketing/sections";

export default function SolutionsPage() {
  return (
    <MarketingShell>
      <PageHero
        eyebrow="Machine Learning"
        title="From Analytics to Prediction"
        subtitle="Instead of dashboards that tell you what happened, we build prediction engines that tell you what will happen next."
      />
      <MLGrid />
      <GetStartedCTA />
    </MarketingShell>
  );
}
