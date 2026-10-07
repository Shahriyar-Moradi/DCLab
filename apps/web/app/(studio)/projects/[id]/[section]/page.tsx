import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHead } from "@/components/studio/PageHead";
import { PhaseEmpty } from "@/app/components/studio-app/StudioParts";
import { projectHref } from "@/lib/application/command-search";

/** Project pages whose screen ships in a later prompt: an explicit empty state naming it (STUDIO_DESIGN 1.2). */
const SECTIONS: Record<string, { title: string; subtitle: string; empty: string; phase: string }> = {
  lab: { title: "Lab", subtitle: "Describe a goal in plain words and review the agent's plan.", empty: "Chat with the lead agent arrives with the assistant.", phase: "A3-UI" },
  pipeline: { title: "Pipeline", subtitle: "Stage-by-stage evidence for one run.", empty: "The pipeline evidence page arrives later. Each experiment page already shows its stages live.", phase: "P4.17-UI" },
  improve: { title: "Improve", subtitle: "A budgeted loop that proposes and runs branches.", empty: "Improve arrives with improve runs.", phase: "P5.5-A" },
  monitoring: { title: "Monitoring", subtitle: "Drift, labels and review of released versions.", empty: "Monitoring arrives with releases.", phase: "P7.5-A" },
};

export default async function ProjectSectionPage({ params }: { params: Promise<{ id: string; section: string }> }) {
  const { id, section } = await params;
  const config = Object.hasOwn(SECTIONS, section) ? SECTIONS[section] : undefined;
  const experiments = projectHref(id, "experiments");
  if (!config || !experiments) notFound();
  return (
    <>
      <PageHead title={config.title} subtitle={config.subtitle} />
      <PhaseEmpty title={config.empty} phase={config.phase}>
        <Link href={experiments}>Open experiments</Link>
      </PhaseEmpty>
    </>
  );
}
