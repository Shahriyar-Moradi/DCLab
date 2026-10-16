"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { createContext, useContext, type ReactNode } from "react";
import { screenForPath, screenStatus, tagLabel, tagTone } from "../../lib/application/screen-status.ts";
import { Pill } from "./Pill";

/** On inside the Studio frame only: the tag links to `/real`, a Studio page other frames cannot open. */
const ScreenStatusScope = createContext(false);

export function ScreenStatusProvider({ children }: { children: ReactNode }) {
  return <ScreenStatusScope.Provider value>{children}</ScreenStatusScope.Provider>;
}

/** Status tags of the current screen (V7 UI-A), under the page title; each opens "What's real today". */
export function ScreenStatusTag() {
  const enabled = useContext(ScreenStatusScope);
  const pathname = usePathname();
  const id = enabled && pathname ? screenForPath(pathname) : null;
  const entry = id ? screenStatus(id) : null;
  if (!entry) return null;
  return (
    <p className="toolbar status-row">
      {entry.tags.map((tag) => {
        const label = tagLabel(tag);
        return (
          <Link key={label} className="status-tag" href="/real" aria-label={`Status of this screen: ${label}. Open what's real today`}>
            <Pill tone={tagTone(tag)}>{label}</Pill>
          </Link>
        );
      })}
    </p>
  );
}
