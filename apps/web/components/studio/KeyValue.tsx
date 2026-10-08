import type { ReactNode } from "react";

export type KeyValueItem = { key: string; label: ReactNode; value: ReactNode };

export function KeyValue({ items }: { items: KeyValueItem[] }) {
  return (
    <dl className="kv">
      {items.map((item) => (
        <div key={item.key} style={{ display: "contents" }}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
