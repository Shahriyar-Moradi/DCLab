"use client";

import { useState } from "react";
import {
  AgentText, Banner, Card, Chart, Chat, CodeBlock, CommandBar, Crumbs, DataTable, EventList, KeyValue, Level, PageGuide, PageHead,
  Pill, ReviewNotes, SectionTabs, Shell, Stat, StepBar, TabPanel, Tabs, Term, resolveSidebar,
  type ChatMessage, type Column, type CommandResult, type StudioCapabilities,
} from "@/components/studio";

/* Invented demo data. It lives only in this page, never in components/studio. */
type Row = { id: string; name: string; score: number; note: string; best?: boolean };
const ROWS: Row[] = [
  { id: "r1", name: "Demo candidate A", score: 0.81, note: "baseline rule", best: true },
  { id: "r2", name: "Demo candidate B", score: 0.78, note: "extra feature set" },
  { id: "r3", name: "Demo candidate C", score: 0.74, note: "shallower model" },
];
const COLUMNS: Column<Row>[] = [
  { key: "name", header: "Candidate", sortValue: (r) => r.name },
  { key: "score", header: "Demo score", numeric: true, sortValue: (r) => r.score, render: (r) => r.score.toFixed(2) },
  { key: "note", header: "Note", render: (r) => <span className="muted">{r.note}</span> },
  { key: "tag", header: "Source", render: (r) => <Pill tone={r.best ? "det" : "ai"}>{r.best ? "rule" : "advisory"}</Pill> },
];
const SERIES = [
  { key: "a", label: "Demo series A", tone: "accent" as const },
  { key: "b", label: "Demo series B", tone: "ai" as const },
];
const POINTS = ["w1", "w2", "w3", "w4", "w5"].map((w, i) => ({ w, a: 0.6 + i * 0.05, b: 0.55 + i * 0.03 }));
const MESSAGES: ChatMessage[] = [
  { id: "m1", role: "user", from: "You", body: "Demo question: which demo candidate is strongest?" },
  { id: "m2", role: "agent", from: "Demo agent", meta: "advisory", body: <AgentText text={"Demo answer: candidate A leads.\nThis <b>text</b> is invented and shown as plain text."} /> },
];
const RESULTS: CommandResult[] = [
  { id: "c1", title: "Demo project one", kind: "Project", detail: "invented entry" },
  { id: "c2", title: "Demo experiment two", kind: "Experiment", detail: "invented entry" },
];
const ALL_ON: StudioCapabilities = { home: true, inbox: true, graph: true, data: true, experiments: true, models: true };

function Kit({ theme, embedded, mainId }: { theme: "light" | "dark"; embedded: boolean; mainId: string }) {
  const [tab, setTab] = useState("one");
  const [draft, setDraft] = useState("");
  const nav = resolveSidebar("developer", { capabilities: ALL_ON, showUnavailable: true, projectBase: "/projects/demo" });
  return (
    <Shell
      theme={theme}
      embedded={embedded}
      mainId={mainId}
      nav={nav}
      currentHref="/projects/demo/graph"
      workspace={{ name: "Demo workspace", detail: "invented, kit only" }}
      user={{ name: "demo@example.invalid", detail: "Developer" }}
      crumbs={<Crumbs label={`Breadcrumb ${theme}`} items={[{ label: "Projects", href: "#" }, { label: "Demo project", href: "#" }, { label: `Kit (${theme})` }]} />}
      commandBar={<CommandBar results={RESULTS} globalShortcut={!embedded} />}
      topbarRight={<Pill tone="ok">AI advisory on</Pill>}
      steps={<StepBar label={`Demo steps ${theme}`} steps={[{ id: "a", label: "Upload", state: "done" }, { id: "b", label: "Choose target", state: "current" }, { id: "c", label: "Train", state: "todo" }]} />}
      guide={
        <PageGuide
          label={`About this screen (${theme})`}
          purpose="Shows every Studio building block in one place."
          howTo="Scroll, switch tabs, press Cmd/Ctrl+K, hover a dotted term."
          youGet="A reference for later screens, with invented data only."
          attention="Nothing here is real. Do not copy the demo values."
        />
      }
    >
      <PageHead
        eyebrow="Development only"
        title="Studio kit"
        subtitle={<>A <Term definition="Fold: one slice of the training data held out in turn while the rest trains.">fold</Term> is explained on hover or focus. Detail stays visible; guidance sits beside it.</>}
        badges={<><Pill tone="ok">ok</Pill><Pill tone="warn">warn</Pill><Pill tone="crit">crit</Pill><Pill tone="ai">ai</Pill><Pill tone="det">det</Pill><Pill tone="gray">gray</Pill><Level level={0} /><Level level={1} /><Level level={2} /><Level level={3} /></>}
        actions={<button type="button" className="btn primary">Primary action</button>}
      />
      <Banner tone="ai" actions={<button type="button" className="btn">Review</button>}>Advisory note from a model. A rule produced the numbers below.</Banner>
      <Banner tone="warn">Warning banner with a demo message.</Banner>
      <div className="grid cols-4">
        <Stat value="0.81" label="Demo score" hint="invented" />
        <Stat value="3" label="Demo candidates" />
        <Stat value="12 s" label="Demo duration" />
        <Stat value="L2" label="Demo trust level" />
      </div>
      <Tabs label={`Page tabs ${theme}`} idPrefix={`kit-${theme}`} value={tab} onChange={setTab} items={[{ id: "one", label: "First" }, { id: "two", label: "Second", count: 2 }, { id: "three", label: "Third" }]} />
      {(["one", "two", "three"] as const).map((id) => (
        <TabPanel key={id} idPrefix={`kit-${theme}`} id={id} active={tab === id}>
          <Card title={`Panel: ${id}`}>
            <p>Arrow keys, Home and End move between page tabs.</p>
          </Card>
        </TabPanel>
      ))}
      <SectionTabs
        title="Candidates"
        label={`Candidates (${theme})`}
        idPrefix={`sect-${theme}`}
        sections={[
          { id: "table", label: "Table", count: 3, content: <DataTable caption={`Demo candidates ${theme}`} columns={COLUMNS} rows={ROWS} rowKey={(r) => r.id} highlightRow={(r) => r.best === true} /> },
          { id: "kv", label: "Details", content: <KeyValue items={[{ key: "id", label: "Id", value: <code>demo_0001</code> }, { key: "lvl", label: "Level", value: <Level level={2} /> }]} /> },
          { id: "code", label: "Code", content: <CodeBlock label={`Demo code ${theme}`} language="python" code={"# invented snippet\nscore = demo_value / total\nprint(round(score, 2))"} /> },
        ]}
      />
      <div className="grid cols-2">
        <Card title="Line chart"><Chart kind="line" data={POINTS} xKey="w" series={SERIES} limit={{ value: 0.7, label: "demo limit", tone: "warn" }} summary="Demo line chart: two invented series rising over five weeks." /></Card>
        <Card title="Bar chart"><Chart kind="bar" data={POINTS} xKey="w" series={SERIES} summary="Demo bar chart: two invented series over five weeks." /></Card>
      </div>
      <div className="grid cols-2">
        <Card title="Events"><EventList label={`Demo events ${theme}`} items={[{ id: "e1", kind: "det", when: "10:02", body: "Rule check passed." }, { id: "e2", kind: "ai", when: "10:04", body: "Model suggested a change." }]} /></Card>
        <Card title="Chat"><Chat label={`Demo chat ${theme}`} messages={MESSAGES} composer={{ value: draft, onChange: setDraft, onSubmit: () => setDraft(""), placeholder: "Type a demo message" }} /></Card>
      </div>
      <DataTable caption="Empty table" columns={COLUMNS} rows={[]} rowKey={(r) => r.id} emptyMessage="Empty state: nothing here yet. This feature arrives in a later phase." />
      <ReviewNotes items={["Reviewer note one (development builds only).", "Reviewer note two."]} />
    </Shell>
  );
}

export function StudioKit({ themes }: { themes: Array<"light" | "dark"> }) {
  return (
    <>
      {themes.map((theme, index) =>
        index > 0 ? (
          <section key={theme} aria-label={`${theme} theme preview`} style={{ padding: 16 }}>
            <Kit theme={theme} embedded mainId={`main-${theme}`} />
          </section>
        ) : (
          <Kit key={theme} theme={theme} embedded={false} mainId="main" />
        ),
      )}
    </>
  );
}
