"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { PageHead } from "@/components/studio/PageHead";
import { SplitInspector } from "@/app/components/studio-app/Inspectors";

export default function SplitInspectorPage() {
  const { id, nodeId } = useParams<{ id: string; nodeId: string }>();
  return (
    <>
      <PageHead title="Goal & test design" subtitle="What is predicted, and how rows were assigned to the final test set (used once) and the cross-validation folds. Counts only." actions={<Link className="btn" href={`/projects/${id}/graph`}>Back to lineage</Link>} />
      <SplitInspector projectId={id} nodeId={nodeId} />
    </>
  );
}
