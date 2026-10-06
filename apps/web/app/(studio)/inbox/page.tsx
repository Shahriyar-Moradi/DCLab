import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { PhaseEmpty } from "@/app/components/studio-app/StudioParts";

export default function InboxPage() {
  return (
    <>
      <PageHead eyebrow="Workspace" title="Inbox" subtitle="Decisions and questions that wait for a person." />
      <PageGuide
        purpose="One place for everything that needs your answer: proposed decisions, agent proposals and open questions."
        howTo="Until the inbox ships, open a project and use its Decisions page to see proposed decisions."
        youGet="A list split into needs a decision, applied automatically and done."
      />
      <PhaseEmpty title="The inbox arrives with the workspace inbox read model." phase="P4.16" />
    </>
  );
}
