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

export type FlowTileStep = {
  id: string; number: number; label: string; value: string; note: string;
  state: "done" | "in_progress" | "alert" | "todo" | "unknown"; stateWords: string; current: boolean; href: string | null;
};

/**
 * The project's steps as tiles (V7 UI-B): number and name, a value and a short note per step, the state from the flow read
 * and the current step marked. Text only; a step without a page is not a link. Scrolls sideways on narrow screens.
 */
export function FlowTiles({ steps, label = "Project steps" }: { steps: FlowTileStep[]; label?: string }) {
  return (
    <nav aria-label={label}>
      <ol className="steps flow v7-flow-tiles">
        {steps.map((step) => {
          const href = safeInternalHref(step.href);
          const inner = (
            <>
              <span className="v7-flow-k">{step.number} {step.label}</span>{" "}
              <b>{step.value}</b>{" "}
              <small>{step.note}</small>
              {/* The state in words too (never colour alone), plus where you are. */}
              {step.stateWords || step.current ? <>{" "}<span className="v7-flow-state">{[step.stateWords, step.current ? "you are here" : null].filter(Boolean).join(" · ")}</span></> : null}
            </>
          );
          return (
            <li key={step.id} data-state={step.state} data-current={step.current ? "true" : undefined}>
              {href ? <Link href={href} aria-current={step.current ? "step" : undefined}>{inner}</Link> : <span className="v7-flow-tile" aria-current={step.current ? "step" : undefined}>{inner}</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
