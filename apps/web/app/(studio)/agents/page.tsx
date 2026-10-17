"use client";

import { useState } from "react";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { TabPanel, Tabs } from "@/components/studio/Tabs";
import { AgentsConnect } from "@/app/components/studio-app/AgentsConnect";
import { ServiceTokensPanel } from "@/app/components/studio-app/ServiceTokensPanel";

const TABS = [
  { id: "connect", label: "Use DCLab from code" },
  { id: "tokens", label: "Access tokens" },
];

export default function AgentsPage() {
  const [tab, setTab] = useState("connect");
  return (
    <>
      <PageHead eyebrow="Workspace" title="Connect" subtitle="Use DCLab from your own code, from a terminal, or from a coding assistant such as Claude Code or Cursor." />
      <PageGuide
        purpose="Connect Claude Code, Cursor, scripts or CI to this workspace, and manage the access tokens they use."
        howTo="Create an access token under Access tokens (it is shown once), then copy a snippet from Use DCLab from code and paste the token where it says dclab_st_…."
        youGet="Code and tools that can read your projects and, only if you allow it, run experiments and propose decisions. A person still accepts every decision, in the Inbox."
        attention="A token is shown once, when you create it. Revoke any token you no longer use."
      />
      <Tabs items={TABS} value={tab} onChange={setTab} idPrefix="agents" label="Connect sections" />
      <TabPanel idPrefix="agents" id="connect" active={tab === "connect"}><AgentsConnect /></TabPanel>
      <TabPanel idPrefix="agents" id="tokens" active={tab === "tokens"}><ServiceTokensPanel /></TabPanel>
    </>
  );
}
