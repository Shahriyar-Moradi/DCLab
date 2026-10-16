"use client";

import Link from "next/link";
import { Banner } from "@/components/studio/Banner";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { safeInternalHref } from "@/components/studio/safe-href";
import { useSession } from "@/lib/application";
import { REAL_PAGE_WORDS, REAL_SUMMARY, SCREEN_STATUS, STATUS_WORDS, rowHref, tagLabel, tagTone, type ScreenGroup, type StatusKind } from "@/lib/application/screen-status";
import { CAPABILITIES, hasCapability } from "@/lib/infrastructure/capabilities";

const GROUPS: ScreenGroup[] = ["Workspace", "Project", "Admin", "Other views"];
const KINDS: StatusKind[] = ["working", "partly", "off", "planned"];

/** What's real today (V7 UI-A): every screen with its status and what is missing, from the one registry in screen-status.ts. */
export default function RealTodayPage() {
  const { user } = useSession();
  const mayOpenBusinessView = hasCapability(user, CAPABILITIES.applicationAccess);
  return (
    <>
      <PageHead eyebrow="Help" title="What's real today" subtitle={REAL_PAGE_WORDS.subtitle} />
      <PageGuide purpose={REAL_PAGE_WORDS.purpose} howTo={REAL_PAGE_WORDS.howTo} youGet={REAL_PAGE_WORDS.youGet} />
      <Banner>
        <b>{REAL_SUMMARY}</b>
      </Banner>
      <section aria-label="What the status words mean">
        <ul className="plain-list status-legend">
          {KINDS.map((kind) => (
            <li key={kind}>
              <Pill tone={STATUS_WORDS[kind].tone}>{kind === "planned" ? "Planned · Phase N" : STATUS_WORDS[kind].label}</Pill> <span className="muted">{STATUS_WORDS[kind].meaning}</span>
            </li>
          ))}
        </ul>
      </section>
      <div className="tbl card">
        <table className="real-table">
          <caption className="sr-only">Every screen, its status and what is missing</caption>
          <thead>
            <tr><th scope="col">Screen</th><th scope="col">Status</th><th scope="col">What is missing</th></tr>
          </thead>
            {GROUPS.map((group) => (
              <tbody key={group}>
                <tr className="group-row"><th scope="rowgroup" colSpan={3}>{group === "Project" ? `${group} (open a project to reach these)` : group}</th></tr>
                {SCREEN_STATUS.filter((entry) => entry.group === group).map((entry) => {
                  const href = safeInternalHref(rowHref(entry, mayOpenBusinessView));
                  return (
                    <tr key={entry.id} id={`screen-${entry.id}`}>
                      <th scope="row">{href ? <Link href={href}>{entry.name}</Link> : entry.name}</th>
                      <td><span className="toolbar">{entry.tags.map((tag) => <Pill key={tagLabel(tag)} tone={tagTone(tag)}>{tagLabel(tag)}</Pill>)}</span></td>
                      <td>{entry.missing}</td>
                    </tr>
                  );
                })}
              </tbody>
            ))}
        </table>
      </div>
      <p className="small muted">{REAL_PAGE_WORDS.footnote}</p>
    </>
  );
}
