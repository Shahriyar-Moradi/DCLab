"use client";

import Link from "next/link";
import { useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { TabPanel, Tabs } from "@/components/studio/Tabs";
import { Term } from "@/components/studio/Term";
import { InboxCard } from "@/app/components/studio-app/InboxCard";
import { QueryNotice } from "@/app/components/studio-app/StudioParts";
import { useInbox, useInboxCounts } from "@/lib/application/studio-inbox-hooks";
import { INBOX_TABS, type InboxTabId } from "@/lib/application/studio-inbox";

function TabBody({ tab, onDone }: { tab: InboxTabId; onDone: (text: string) => void }) {
  const inbox = useInbox(tab);
  const items = inbox.data?.pages.flatMap((page) => page.items) ?? [];
  const canDecide = inbox.data?.pages[0]?.viewer.can_decide ?? false;
  const meta = INBOX_TABS.find((t) => t.id === tab)!;
  return (
    <>
      {inbox.isError ? <QueryNotice error={inbox.error} what="inbox" /> : null}
      {inbox.isPending ? <p role="status">Loading the inbox…</p> : null}
      {inbox.data && !canDecide && items.some((item) => item.actions.length > 0) ? (
        <Banner tone="info">You can read everything here but not decide: your role cannot write ML work in this workspace. Buttons are disabled and say why.</Banner>
      ) : null}
      {inbox.data && items.length === 0 ? <div className="empty" role="note"><p>{meta.empty}</p></div> : null}
      <div style={{ display: "grid", gap: 16 }}>
        {items.map((item) => <InboxCard key={item.id} item={item} canDecide={canDecide} onDone={onDone} />)}
      </div>
      {inbox.hasNextPage ? (
        <p><button type="button" className="btn" disabled={inbox.isFetchingNextPage} onClick={() => void inbox.fetchNextPage()}>{inbox.isFetchingNextPage ? "Loading…" : "Load more"}</button></p>
      ) : null}
    </>
  );
}

export default function InboxPage() {
  const [tab, setTab] = useState<InboxTabId>("needs_decision");
  const [message, setMessage] = useState<string | null>(null);
  const counts = useInboxCounts();
  const waiting = counts.data?.needs_decision ?? 0;
  return (
    <>
      <PageHead eyebrow="Workspace" title="Inbox" subtitle="Decisions and questions that wait for a person, across every project." actions={<Link className="btn" href="/home">Home</Link>} />
      <PageGuide
        purpose="One place for everything that needs your answer: suggested decisions, suggestions from the assistant, and runs that ask a question."
        howTo={<>Read the evidence, compare the <Term definition="The deterministic rule's answer. It is always computed, with or without AI.">rule answer</Term> with the AI answer, then Approve or Reject with a reason. The decision is recorded and the item moves to Done.</>}
        youGet="Three lists: needs a decision, applied automatically, and done. Nothing is changed by opening this page."
        attention={waiting ? `${waiting} item${waiting === 1 ? " is" : "s are"} waiting for a decision.` : undefined}
      />
      <div role="status" aria-live="polite">{message ? <Banner tone="info">{message}</Banner> : null}</div>
      <Tabs
        idPrefix="inbox" label="Inbox lists" value={tab} onChange={(id) => { setTab(id as InboxTabId); setMessage(null); }}
        items={INBOX_TABS.map((t) => ({ id: t.id, label: t.label, count: counts.data?.[t.id] }))}
      />
      {INBOX_TABS.map((t) => (
        <TabPanel key={t.id} idPrefix="inbox" id={t.id} active={t.id === tab}>
          {t.id === tab ? <TabBody tab={t.id} onDone={setMessage} /> : null}
        </TabPanel>
      ))}
    </>
  );
}
