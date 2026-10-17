"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Term } from "@/components/studio/Term";
import { LineageView, NewerFileBanner } from "@/app/components/studio-app/LineageView";
import { useProjectGraph } from "@/lib/application/studio-data-hooks";

const LINEAGE_TERMS = {
  lineage: "Which versions each item was built from: a test design splits the data, a run uses a goal, the data and a test design, a model is built by a run.",
  stale: "Built on an older version: it was made from a version that is no longer the one in use. Nothing is changed or retrained. Only moving the goal, the data, the test design or the model in use marks things this way; changing the features does not.",
  ref: "The versions the project uses now (goal, data, test design, features, model). Changing one is saved in History.",
  split: "Test design: the fixed assignment of rows to the final test set (scored once per run) and the cross-validation folds. Runs on the same design are comparable.",
};

export default function GraphPage() {
  const { id } = useParams<{ id: string }>();
  const graph = useProjectGraph(id);
  return (
    <>
      <PageHead
        title="Lineage"
        actions={<Link className="btn" href={`/projects/${id}/decisions`}>History</Link>}
        subtitle="How this project fits together: data, test design, runs and models. ★ marks the versions in use; orange items marked built on an older version were made from a version that is no longer in use. Select an item to see its details and what would become old."
      />
      <PageGuide
        purpose={<>See how the project&apos;s versions connect (<Term definition={LINEAGE_TERMS.lineage}>what was built from what</Term>) and which results are built on an older version.</>}
        howTo={<>Select an item, or switch to &ldquo;As a list&rdquo; for a keyboard-friendly table. Arrows point from what an item was built from. The <Term definition={LINEAGE_TERMS.ref}>versions in use</Term> and the <Term definition={LINEAGE_TERMS.split}>test design</Term> decide what is comparable.</>}
        youGet="What each item is, its status, what it was built from, and what a change to it would make old. The reference of each item is in its details."
        attention={<>An item <Term definition={LINEAGE_TERMS.stale}>built on an older version</Term> is not wrong, only made from an older version. Only moving the goal, the data, the test design or the model in use marks items this way. Nothing here changes the project.</>}
      />
      <NewerFileBanner projectId={id} graph={graph.data} />
      <LineageView projectId={id} />
    </>
  );
}
