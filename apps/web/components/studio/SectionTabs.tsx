"use client";

import { useState, type ReactNode } from "react";
import { TabPanel, Tabs } from "./Tabs";

export type Section = { id: string; label: string; count?: number; content: ReactNode };

/** A card whose body is split into tabbed sections (prototype `.sect`). */
export function SectionTabs({
  title,
  aside,
  sections,
  idPrefix,
  initial,
  label,
}: {
  title: string;
  aside?: ReactNode;
  sections: Section[];
  idPrefix: string;
  initial?: string;
  /** Accessible name when several cards share a title; defaults to the title. */
  label?: string;
}) {
  const [active, setActive] = useState(initial ?? sections[0]?.id ?? "");
  return (
    <section className="card sect" aria-label={label ?? title}>
      <div className="sect-head">
        <h2>{title}</h2>
        {aside}
      </div>
      <Tabs
        variant="section"
        label={`${title} sections`}
        idPrefix={idPrefix}
        value={active}
        onChange={setActive}
        items={sections.map(({ id, label, count }) => ({ id, label, count }))}
      />
      {sections.map((section) => (
        <TabPanel key={section.id} idPrefix={idPrefix} id={section.id} active={section.id === active}>
          <div className="sect-panel">{section.content}</div>
        </TabPanel>
      ))}
    </section>
  );
}
