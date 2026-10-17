"use client";

import { useParams } from "next/navigation";
import { ImproveLoopView } from "@/app/components/studio-app/ImproveLoopView";

/** One improvement loop. Inbox cards of kind `improve_run` link here ("Open the loop", via `improveItemHref` → `improveNoticeHref`). */
export default function ImproveLoopPage() {
  const { id, loopId } = useParams<{ id: string; loopId: string }>();
  return <ImproveLoopView projectId={id} loopId={loopId} />;
}
