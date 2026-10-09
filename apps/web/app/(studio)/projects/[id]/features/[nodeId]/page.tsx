"use client";

import Link from "next/link";
import { projectHref } from "@/lib/application/command-search";
import { useParams } from "next/navigation";
import { PageHead } from "@/components/studio/PageHead";
import { FeatureInspector } from "@/app/components/studio-app/Inspectors";

export default function FeatureInspectorPage() {
  const { id, nodeId } = useParams<{ id: string; nodeId: string }>();
  return (
    <>
      <PageHead title="Features" subtitle="Each feature with its reason, formula, importance and code." actions={projectHref(id, "graph") ? <Link className="btn" href={projectHref(id, "graph")!}>Back to lineage</Link> : null} />
      <FeatureInspector projectId={id} nodeId={nodeId} />
    </>
  );
}
