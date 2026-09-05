"use client";

import { SearchInput } from "@/app/components/ui/SearchInput";

export function CollectionSearch({
  value,
  onChange,
  shown,
  total,
  placeholder = "Filter this list",
}: {
  value: string;
  onChange: (value: string) => void;
  shown: number;
  total: number;
  placeholder?: string;
}) {
  if (total === 0) return null;
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <SearchInput className="max-w-sm" value={value} onChange={onChange} placeholder={placeholder} />
      {value.trim() ? (
        <p className="font-mono text-data text-ink-muted">
          {shown} of {total}
        </p>
      ) : null}
    </div>
  );
}
