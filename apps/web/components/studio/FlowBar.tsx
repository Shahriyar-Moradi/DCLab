import Link from "next/link";
import { safeInternalHref } from "./safe-href";

export type FlowBarStep = { id: string; label: string; href: string | null; state: "done" | "current" | "next" | null };

/** The project's steps by name with their state. Steps without a known state are shown plain; steps without a page are not links. */
export function FlowBar({ steps, label = "Project steps" }: { steps: FlowBarStep[]; label?: string }) {
  return (
    <nav aria-label={label}>
      <ol className="steps flow">
        {steps.map((step) => {
          const href = safeInternalHref(step.href);
          return (
            <li key={step.id} data-state={step.state ?? undefined}>
              {href ? <Link href={href} aria-current={step.state === "current" ? "step" : undefined}>{step.label}</Link> : <span>{step.label}</span>}
              {step.state === "done" ? <span className="sr-only"> (done)</span> : null}
              {step.state === "next" ? <span className="sr-only"> (next)</span> : null}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
