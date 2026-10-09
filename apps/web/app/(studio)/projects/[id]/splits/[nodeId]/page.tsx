"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { projectHref } from "@/lib/application/command-search";
import { PageHead } from "@/components/studio/PageHead";
import { GoalTestDesign } from "@/app/components/studio-app/GoalTestDesign";

export default function SplitInspectorPage() {
  const { id, nodeId } = useParams<{ id: string; nodeId: string }>();
  return (
    <>
      <PageHead title="Goal & test design" subtitle="What we want to predict, what “good” means, and how we test it fairly." actions={projectHref(id, "graph") ? <Link className="btn" href={projectHref(id, "graph")!}>Back to lineage</Link> : null} />
      <GoalTestDesign projectId={id} nodeId={nodeId} />
    </>
  );
}
