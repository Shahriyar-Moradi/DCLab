import type { ReactNode } from "react";

/** Development-only reviewer notes. Renders nothing in production builds. */
export function ReviewNotes({ title = "Review notes (development only)", items }: { title?: string; items: ReactNode[] }) {
  if (process.env.NODE_ENV === "production") return null;
  return (
    <details className="review">
      <summary>{title}</summary>
      <ul>
        {items.map((item, index) => (
          <li key={index}>{item}</li>
        ))}
      </ul>
    </details>
  );
}
