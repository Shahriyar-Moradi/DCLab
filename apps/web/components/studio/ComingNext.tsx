import { EXAMPLE_MARK, type ComingNextEntry } from "../../lib/application/coming-next.ts";
import { Pill } from "./Pill";

/**
 * A "Coming next" card (D2b): what an unbuilt piece will do, plus an example picture marked "Example, not your data".
 * The picture is one image for assistive technology (its rows are hidden from it), holds nothing that can be focused
 * or clicked, and never comes from an API.
 */
export function ComingNext({ entry }: { entry: ComingNextEntry }) {
  return (
    <section className="card coming-next" aria-labelledby={`coming-next-${entry.id}`} data-coming-next={entry.id}>
      <h2 id={`coming-next-${entry.id}`}>
        {entry.title}
        <span className="sp">{entry.status ? <><Pill tone="gray">{entry.status}</Pill> </> : null}<Pill tone="gray">Coming next</Pill></span>
      </h2>
      <p>{entry.text}</p>
      <div className="coming-next-picture" role="img" aria-label={`${EXAMPLE_MARK}. ${entry.pictureLabel}`}>
        <span className="coming-next-mark" aria-hidden="true">{EXAMPLE_MARK}</span>
        <dl aria-hidden="true">
          {entry.rows.map(([label, value]) => (
            <div key={label}><dt>{label}</dt><dd>{value}</dd></div>
          ))}
        </dl>
      </div>
      <p className="muted small">{entry.today}</p>
    </section>
  );
}
