"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";
import { PageHead } from "@/components/studio/PageHead";
import { safeInternalHref } from "@/components/studio/safe-href";
import { GoalOverview } from "@/app/components/studio-app/GoalTestDesign";
import { QueryNotice } from "@/app/components/studio-app/StudioParts";
import { useProjectRefs } from "@/lib/application";
import { useProjectGraph } from "@/lib/application/studio-data-hooks";
import { newestSplitPlan } from "@/lib/application/studio-goal";

/**
 * Goal & test design for the project (V7 UI-C): always reachable. Once a test design exists it opens it (the one in use,
 * else the newest); before that it shows the goal from the problem spec and says honestly that no test design exists yet.
 */
export default function ProjectGoalPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const refs = useProjectRefs(id);
  const graph = useProjectGraph(id);
  const known = !refs.isPending && !graph.isPending;
  const plan = known ? newestSplitPlan(refs.data?.items.find((r) => r.ref_kind === "split_plan")?.target.id, graph.data?.nodes) : null;
  // "No test design yet" only when both reads answered and neither names one.
  const designKnown = !!refs.data && !!graph.data;
  const href = plan ? safeInternalHref(`/projects/${id}/splits/${plan}`) : null;
  useEffect(() => {
    if (href) router.replace(href);
  }, [href, router]);
  return (
    <>
      <PageHead title="Goal & test design" subtitle="What we want to predict, what “good” means, and how we test it fairly." />
      {!known ? <p role="status">Loading the goal and test design…</p> : href ? <p role="status">Opening the test design…</p> : (
        <>
          {refs.isError ? <QueryNotice error={refs.error} what="versions in use" /> : null}
          {graph.isError ? <QueryNotice error={graph.error} what="project" /> : null}
          <GoalOverview projectId={id} designKnown={designKnown} />
        </>
      )}
    </>
  );
}
