"use client";

import { useParams } from "next/navigation";
import { ImproveScreen } from "@/app/components/studio-app/ImproveScreen";

export default function ImprovePage() {
  const { id } = useParams<{ id: string }>();
  return <ImproveScreen projectId={id} />;
}
