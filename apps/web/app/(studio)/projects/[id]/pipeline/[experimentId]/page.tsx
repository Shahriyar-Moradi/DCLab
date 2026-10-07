"use client";

import { useParams } from "next/navigation";
import { PipelineEvidence } from "@/app/components/studio-app/PipelineEvidence";

export default function PipelineRunPage() {
  const { id, experimentId } = useParams<{ id: string; experimentId: string }>();
  return <PipelineEvidence projectId={id} experimentId={experimentId} />;
}
