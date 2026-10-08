import type { ReactNode } from "react";

export type PageGuideProps = {
  purpose: ReactNode;
  howTo: ReactNode;
  youGet: ReactNode;
  attention?: ReactNode;
  label?: string;
};

/** Per-screen orientation: what it is for, how to use it, what you get, what needs attention. */
export function PageGuide({ purpose, howTo, youGet, attention, label = "About this screen" }: PageGuideProps) {
  const sections: Array<[string, ReactNode]> = [
    ["What it is for", purpose],
    ["How to use it", howTo],
    ["What you get", youGet],
  ];
  if (attention) sections.push(["Needs attention", attention]);
  return (
    <section className="guide" aria-label={label}>
      {sections.map(([title, body]) => (
        <div key={title}>
          <h2>{title}</h2>
          <p>{body}</p>
        </div>
      ))}
    </section>
  );
}
