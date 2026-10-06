"use client";

import { useState } from "react";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { TabPanel, Tabs } from "@/components/studio/Tabs";
import { AgentsConnect } from "@/app/components/studio-app/AgentsConnect";
import { ServiceTokensPanel } from "@/app/components/studio-app/ServiceTokensPanel";
import { PhaseEmpty } from "@/app/components/studio-app/StudioParts";

const TABS = [
  { id: "connect", label: "Connect: MCP · SDK · CLI · API" },
  { id: "tokens", label: "Service tokens" },
  { id: "tools", label: "Tool registry" },
  { id: "runs", label: "Agent runs" },
  { id: "catalog", label: "Agent catalog" },
];

export default function AgentsPage() {
  const [tab, setTab] = useState("connect");
  return (
    <>
      <PageHead eyebrow="Workspace" title="Agents & tools" subtitle="How outside agents and scripts connect to this workspace, and the tokens that let them in." />
      <PageGuide
        purpose="Connect Claude Code, Cursor, scripts or CI to this workspace, and manage the tokens they use."
        howTo="Copy a snippet from Connect, create a token under Service tokens (shown once), paste it where the snippet says dclab_st_…."
        youGet="Agents that can read your projects and, only if you allow it, propose changes. A person still accepts every decision."
        attention="A token shows once, when you create it. Revoke any token you no longer use."
      />
      <Tabs items={TABS} value={tab} onChange={setTab} idPrefix="agents" label="Agents and tools sections" />
      <TabPanel idPrefix="agents" id="connect" active={tab === "connect"}><AgentsConnect /></TabPanel>
      <TabPanel idPrefix="agents" id="tokens" active={tab === "tokens"}><ServiceTokensPanel /></TabPanel>
      <TabPanel idPrefix="agents" id="tools" active={tab === "tools"}>
        <PhaseEmpty title="The tool registry arrives with the agent platform screens." phase="A2-UI" />
      </TabPanel>
      <TabPanel idPrefix="agents" id="runs" active={tab === "runs"}>
        <PhaseEmpty title="Agent runs and their harness records arrive with the agent platform screens." phase="A2-UI" />
      </TabPanel>
      <TabPanel idPrefix="agents" id="catalog" active={tab === "catalog"}>
        <PhaseEmpty title="The agent catalog arrives with the agent governance screens." phase="P6.8-UI" />
      </TabPanel>
    </>
  );
}
