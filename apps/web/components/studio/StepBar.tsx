export type Step = { id: string; label: string; state: "done" | "current" | "todo" };

/** Where you are in a multi-step flow (for example upload, target, train). */
export function StepBar({ steps, label = "Progress" }: { steps: Step[]; label?: string }) {
  return (
    <nav aria-label={label}>
      <ol className="steps">
        {steps.map((step) => (
          <li key={step.id} data-state={step.state} aria-current={step.state === "current" ? "step" : undefined}>
            {step.label}
            {step.state === "done" ? <span className="sr-only"> (done)</span> : null}
          </li>
        ))}
      </ol>
    </nav>
  );
}
