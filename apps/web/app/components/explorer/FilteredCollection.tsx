"use client";

import { CollectionSearch } from "@/app/components/ui/CollectionSearch";
import { filterByText } from "@/app/components/ui/localCollection";
import { useState, type ReactNode } from "react";

export function FilteredCollection<T>({
  rows,
  haystack,
  empty,
  children,
}: {
  rows: T[];
  haystack: (row: T) => Array<string | number | boolean | null | undefined>;
  empty: ReactNode;
  children: (rows: T[]) => ReactNode;
}) {
  const [query, setQuery] = useState("");
  if (rows.length === 0) return empty;
  const filtered = filterByText(rows, query, haystack);
  return (
    <div>
      <CollectionSearch value={query} onChange={setQuery} shown={filtered.length} total={rows.length} />
      {filtered.length === 0 ? (
        <p className="text-body text-ink-muted">Nothing on this list matches that filter.</p>
      ) : (
        children(filtered)
      )}
    </div>
  );
}
