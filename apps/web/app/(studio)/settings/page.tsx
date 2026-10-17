"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { NotificationsPanel } from "@/app/components/studio-app/NotificationsPanel";
import { UsagePanel } from "@/app/components/studio-app/UsagePanel";
import { WebhooksPanel } from "@/app/components/studio-app/WebhooksPanel";
import { TabPanel, Tabs } from "@/components/studio/Tabs";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { useSession } from "@/lib/application";
import { ADDING_PEOPLE_TEXT, MORE_SETTINGS_TEXT, memberRow, membersCountText, myAccess, roleRow } from "@/lib/application/studio-settings";
import { useWorkspaceMembers } from "@/lib/application/studio-settings-hooks";
import { ApiError } from "@/lib/infrastructure/api-client";
import { readErrorSentence } from "@/lib/application/api-error-text";
import { usePreviewRole } from "@/app/components/studio-app/ViewAs";
import { PREVIEW_ROLE_LABEL, PREVIEW_ROLE_VALUE } from "@/lib/application/view-as";

const DEEP_TABS = ["usage", "notifications", "integrations"];

export default function SettingsPage() {
  const session = useSession();
  const access = myAccess(session.user?.capabilities);
  const members = useWorkspaceMembers(session.activeWorkspaceId, session.loaded && access.canSeeMembers);
  const refused = members.error instanceof ApiError && members.error.status === 403;
  const hidden = session.loaded && (!access.canSeeMembers || refused);
  const [tab, setTab] = useState("people");
  // Deep link: /settings?tab=usage|notifications|integrations (read after mount so the server and first client render agree).
  useEffect(() => {
    const asked = new URLSearchParams(window.location.search).get("tab") ?? "";
    if (DEEP_TABS.includes(asked)) setTab(asked);
  }, []);
  const chooseTab = (id: string) => {
    setTab(id);
    window.history.replaceState(null, "", DEEP_TABS.includes(id) ? `/settings?tab=${id}` : "/settings");
  };
  const roles = (members.data?.roles ?? []).map(roleRow);
  // "View as" (admins only): what the previewed role may do, from the same server role table (no extra request).
  const preview = usePreviewRole();
  const previewValue = preview ? PREVIEW_ROLE_VALUE[preview] : null;
  const previewRow = previewValue ? roles.find((row) => row.role === previewValue) : undefined;

  return (
    <>
      <PageHead eyebrow="Admin" title="Settings" subtitle="Who is in this workspace, what each role can do, how much of your plan is used, your email choices and the workspace's webhooks." />
      <PageGuide
        purpose="See the people in your workspace and what each role may do, how much of your plan is used, choose which emails you get, and connect your own systems with webhooks."
        howTo="Read the list of people, then the table of roles; open Usage & cost for limits and spend, Notifications for your email choices, and Integrations for webhooks (owners and administrators). To give an access token to your own code or tools, go to Connect."
        youGet="Names, emails, roles and join dates of the people in this workspace and what each role can and cannot do (read only); your own email choices; the workspace's webhooks with their delivery log."
        attention={hidden ? "Your role cannot see the list of people. Ask an admin." : undefined}
      />

      <Tabs
        idPrefix="settings"
        label="Settings sections"
        value={tab}
        onChange={chooseTab}
        items={[{ id: "people", label: "People and roles" }, { id: "usage", label: "Usage & cost" }, { id: "notifications", label: "Notifications" }, { id: "integrations", label: "Integrations" }]}
      />

      <TabPanel idPrefix="settings" id="notifications" active={tab === "notifications"}>{tab === "notifications" ? <NotificationsPanel ready={session.loaded} /> : null}</TabPanel>
      <TabPanel idPrefix="settings" id="integrations" active={tab === "integrations"}>
        {tab === "integrations" ? <WebhooksPanel key={session.activeWorkspaceId ?? "none"} workspaceId={session.activeWorkspaceId} canManage={access.canSeeMembers} ready={session.loaded} /> : null}
      </TabPanel>

      <TabPanel idPrefix="settings" id="usage" active={tab === "usage"}>{tab === "usage" ? <UsagePanel /> : null}</TabPanel>
      <TabPanel idPrefix="settings" id="people" active={tab === "people"}>
      {!session.loaded ? <p role="status">Loading your access…</p> : null}

      {hidden ? (
        <Card title="Members and roles">
          <Banner tone="info">Only admins can see the people in a workspace. Your role cannot.</Banner>
        </Card>
      ) : null}

      {preview && previewRow ? (
        <Card title={`What the ${PREVIEW_ROLE_LABEL[preview]} role can do (preview)`}>
          <p className="muted">From the server&apos;s role table below. The rest of this screen still shows your own access.</p>
          <div className="cols-2 grid">
            <div><h3>Can</h3>{previewRow.can.length ? <ul className="plain-list">{previewRow.can.map((t) => <li key={t}>{t}</li>)}</ul> : <p>Nothing</p>}</div>
            <div><h3>Cannot</h3>{previewRow.cannot.length ? <ul className="plain-list">{previewRow.cannot.map((t) => <li key={t}>{t}</li>)}</ul> : <p>Nothing</p>}</div>
          </div>
        </Card>
      ) : null}

      <Card title="Your access">
        {session.loaded ? <ul className="plain-list" aria-label="Your access">{access.sentences.map((text) => <li key={text}>{text}</li>)}</ul> : null}
      </Card>

      {session.loaded && !hidden ? (
        <Card title="Members" aside={members.data ? membersCountText(members.data.members.length, members.data.more) : undefined}>
          {members.isPending ? <p role="status">Loading the people in this workspace…</p> : null}
          {members.isError ? <Banner tone="crit">{readErrorSentence(members.error, "members")}</Banner> : null}
          {members.data && members.data.members.length === 0 ? <div className="empty">No active members found.</div> : null}
          {members.data && members.data.members.length > 0 ? (
            <div className="tbl" tabIndex={0} role="region" aria-label="People in this workspace (scrolls sideways on small screens)">
              <table>
                <caption className="sr-only">People in this workspace</caption>
                <thead><tr><th scope="col">Name</th><th scope="col">Email</th><th scope="col">Role</th><th scope="col">Joined</th></tr></thead>
                <tbody>
                  {members.data.members.map(memberRow).map((row) => (
                    <tr key={row.key}>
                      <th scope="row">{row.name} {row.you ? <Pill tone="det">you</Pill> : null} {row.legacy ? <Pill tone="gray">legacy access</Pill> : null}</th>
                      <td>{row.email}</td>
                      <td>{row.role}</td>
                      <td>{row.joined}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
          <p className="small muted">{ADDING_PEOPLE_TEXT}</p>
        </Card>
      ) : null}

      {roles.length > 0 ? (
        <Card title="What each role can do">
          <div className="tbl" tabIndex={0} role="region" aria-label="What each role can do (scrolls sideways on small screens)">
            <table>
              <caption className="sr-only">What each role can and cannot do</caption>
              <thead><tr><th scope="col">Role</th><th scope="col">Can</th><th scope="col">Cannot</th></tr></thead>
              <tbody>
                {roles.map((row) => (
                  <tr key={row.role}>
                    <th scope="row">{row.label}</th>
                    <td>{row.can.length ? <ul className="plain-list">{row.can.map((t) => <li key={t}>{t}</li>)}</ul> : "Nothing"}</td>
                    <td>{row.cannot.length ? <ul className="plain-list">{row.cannot.map((t) => <li key={t}>{t}</li>)}</ul> : "Nothing"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small muted">The server decides every request; this table only describes it.</p>
        </Card>
      ) : null}

      <Card title="Access tokens">
        <p>Access tokens for your own code and connected tools are managed under <Link href="/agents">Connect</Link>.</p>
      </Card>
      <Card title="AI settings">
        <p>Whether AI is on, what the assistant beta may do, its model, spend and problem reports are under <Link href="/governance">AI settings</Link>.</p>
      </Card>
      <p className="small muted">{MORE_SETTINGS_TEXT}</p>
      </TabPanel>
    </>
  );
}
