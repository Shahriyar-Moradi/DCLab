"use client";

import { useParams } from "next/navigation";
import { MonitoringScreen } from "@/app/components/studio-app/MonitoringScreen";

export default function MonitoringPage() {
  const { id } = useParams<{ id: string }>();
  return <MonitoringScreen projectId={id} />;
}
