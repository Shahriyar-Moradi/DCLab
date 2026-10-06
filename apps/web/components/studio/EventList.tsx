import type { ReactNode } from "react";

export type EventItem = { id: string; kind: "det" | "ai"; when: string; body: ReactNode };

/** Timeline of events: `det` = deterministic rule/service, `ai` = advisory model. */
export function EventList({ items, label }: { items: EventItem[]; label: string }) {
  return (
    <ol className="events" aria-label={label}>
      {items.map((item) => (
        <li key={item.id} className={`ev ${item.kind}`}>
          <div className="when">
            {item.when} · {item.kind === "ai" ? "advisory (AI)" : "deterministic rule"}
          </div>
          <div>{item.body}</div>
        </li>
      ))}
    </ol>
  );
}
