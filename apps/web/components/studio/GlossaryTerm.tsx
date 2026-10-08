import type { ReactNode } from "react";
import { GLOSSARY, type GlossaryKey } from "./glossary.ts";
import { Term } from "./Term";

/** A glossary term with its tooltip. Pass children to show different wording over the same definition. */
export function GlossaryTerm({ term, children }: { term: GlossaryKey; children?: ReactNode }) {
  const entry = GLOSSARY[term];
  return <Term definition={entry.definition}>{children ?? entry.label}</Term>;
}
