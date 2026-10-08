/**
 * Text-only slot for assistant/agent output. React escapes the string, so markup is shown
 * literally; line breaks are kept. Rule for the whole kit: ReactNode slots must never be fed
 * untrusted HTML. Any future Markdown rendering must forbid raw HTML and route every link
 * through `safeInternalHref` (external links: separate allowlist plus rel="noopener noreferrer").
 */
export function AgentText({ text }: { text: string }) {
  return <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{text}</p>;
}
