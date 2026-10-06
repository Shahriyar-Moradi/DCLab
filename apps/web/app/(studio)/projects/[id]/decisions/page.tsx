"use client";

import { useParams } from "next/navigation";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill, type PillTone } from "@/components/studio/Pill";
import { Term } from "@/components/studio/Term";
import { QueryNotice, STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { useProjectDecisions, type StudioDecisionItem } from "@/lib/application";

const ACTOR_TONE: Record<string, PillTone> = { rule: "det", agent: "ai", human: "gray" };
const ACTOR_LABEL: Record<string, string> = { rule: "rule", agent: "agent", human: "person" };

const COLUMNS: Column<StudioDecisionItem>[] = [
  { key: "type", header: "Decision", sortValue: (d) => d.decision_type, render: (d) => d.decision_type.replaceAll("_", " ") },
  { key: "state", header: "State", sortValue: (d) => d.effective_state, render: (d) => <Pill tone={STATUS_TONE[d.effective_state] ?? "gray"}>{d.effective_state}</Pill> },
  { key: "actor", header: "Made by", render: (d) => <Pill tone={ACTOR_TONE[d.actor.kind] ?? "gray"}>{ACTOR_LABEL[d.actor.kind] ?? d.actor.kind}</Pill> },
  { key: "subject", header: "About", render: (d) => <>{d.subject.kind.replaceAll("_", " ")} <span className="mono">{d.subject.id.slice(0, 8)}</span></> },
  { key: "recorded", header: "Recorded", sortValue: (d) => d.recorded_at, render: (d) => formatWhen(d.recorded_at) },
];

export default function DecisionsPage() {
  const { id } = useParams<{ id: string }>();
  const decisions = useProjectDecisions(id);
  return (
    <>
      <PageHead title="Decisions" subtitle="Every change to this project is a recorded decision: who or what made it, about which node, and when. Records are append-only." />
      <PageGuide
        purpose="Audit how the project reached its current state."
        howTo={<>Each row is one <Term definition="An append-only record of a choice (for example moving the champion ref), with its actor and evidence. Corrections supersede; nothing is edited.">decision record</Term>. Rule decisions and agent decisions are marked separately.</>}
        youGet="The newest 100 decisions. Accept, reject and supersede arrive with P4.4-A."
      />
      {decisions.isError ? <QueryNotice error={decisions.error} what="decision list" /> : null}
      {decisions.isPending ? <p role="status">Loading decisions…</p> : null}
      {decisions.data ? (
        <DataTable caption="Decisions" columns={COLUMNS} rows={decisions.data.items} rowKey={(d) => d.id} emptyMessage="No decisions recorded for this project yet." />
      ) : null}
    </>
  );
}
