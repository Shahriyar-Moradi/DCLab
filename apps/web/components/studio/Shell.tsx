import Link from "next/link";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { ResolvedSidebarGroup } from "./sidebar-config";
import { safeInternalHref } from "./safe-href";
import "./studio.css";

export type ShellProps = {
  nav: ResolvedSidebarGroup[];
  /** Pathname or href of the current page (matches a nav item's href). */
  currentHref?: string;
  workspace?: { name: string; detail?: string };
  user?: { name: string; detail?: string };
  /** Top bar: breadcrumbs, command bar and right-side slot. */
  crumbs?: ReactNode;
  commandBar?: ReactNode;
  topbarRight?: ReactNode;
  /** Orientation slots, rendered above the page content. */
  steps?: ReactNode;
  guide?: ReactNode;
  /** Force a theme; omit to follow the system. */
  theme?: "light" | "dark";
  /** Assistant panel slot (A3-UI). Rendered only when given; Studio passes nothing until the capability is on. */
  assistant?: ReactNode;
  /** Preview mode (design kit): no viewport-height layout and no duplicate landmarks. */
  embedded?: boolean;
  mainId?: string;
  children: ReactNode;
};

/** Sidebar (264 px) plus sticky glass top bar. Landmarks: nav, header, main (div in embedded mode). */
export function Shell({ nav, currentHref, workspace, user, crumbs, commandBar, topbarRight, steps, guide, assistant, theme, embedded, mainId = "main", children }: ShellProps) {
  const Aside = embedded ? "div" : "aside";
  const Side = embedded ? "div" : "nav";
  const Top = embedded ? "div" : "header";
  const Main = embedded ? "div" : "main";
  return (
    <div className={cn("studio shell", embedded && "embedded")} data-theme={theme}>
      <Aside className="nav">
        <Link className="brand" href="/">
          <span className="logo" aria-hidden="true">DC</span>
          <b>DCLab</b>
        </Link>
        {workspace ? (
          <div className="ws">
            <b>{workspace.name}</b>
            {workspace.detail ? <small>{workspace.detail}</small> : null}
          </div>
        ) : null}
        <Side aria-label={embedded ? undefined : "Studio"} style={{ display: "grid", gap: 22 }}>
          {nav.map((group) => (
            <div className="nav-group" key={group.id}>
              <div className="nav-label">{group.label}</div>
              {group.items.map((item) => {
                const href = item.available ? safeInternalHref(item.href) : null;
                return href ? (
                  <a key={item.id} href={href} aria-current={href === currentHref ? "page" : undefined}>
                    <span className="ic" aria-hidden="true">{item.icon}</span>
                    <span>{item.label}</span>
                  </a>
                ) : (
                  <span key={item.id} className="nav-off" aria-disabled="true">
                    <span className="ic" aria-hidden="true">{item.icon}</span>
                    <span>{item.label}</span>
                    {item.available ? null : (
                      <span className="cnt" style={{ whiteSpace: "nowrap" }}>
                        <span className="sr-only">Planned in </span>
                        {item.phase}
                      </span>
                    )}
                  </span>
                );
              })}
            </div>
          ))}
        </Side>
        {user ? (
          <div className="user">
            <b>{user.name}</b>
            {user.detail ? <small>{user.detail}</small> : null}
          </div>
        ) : null}
      </Aside>
      <div className="main">
        <Top className="topbar">
          {crumbs}
          {commandBar}
          {topbarRight ? <div className="right">{topbarRight}</div> : null}
        </Top>
        <Main id={mainId} className="content" tabIndex={-1}>
          {steps}
          {guide}
          {children}
        </Main>
      </div>
      {assistant ? <aside className="assistant" aria-label="Assistant">{assistant}</aside> : null}
    </div>
  );
}
