"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { PageHead } from "@/components/studio/PageHead";
import { FeatureInspector } from "@/app/components/studio-app/Inspectors";

export default function FeatureInspectorPage() {
  const { id, nodeId } = useParams<{ id: string; nodeId: string }>();
  return (
    <>
      <PageHead title="Features" subtitle="Each feature with its reason, formula, importance and code." actions={<Link className="btn" href={`/projects/${id}/graph`}>Back to lineage</Link>} />
      <FeatureInspector projectId={id} nodeId={nodeId} />
    </>
  );
}
