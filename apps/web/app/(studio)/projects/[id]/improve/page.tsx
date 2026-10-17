"use client";

import { useParams } from "next/navigation";
import { ImproveHome } from "@/app/components/studio-app/ImproveLoop";

export default function ImprovePage() {
  const { id } = useParams<{ id: string }>();
  return <ImproveHome projectId={id} />;
}
