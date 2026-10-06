/**
 * The one gate for any href/redirect that did not come from a literal in our own source
 * (search results, slugs, agent text, project bases). Returns the href when it is a
 * same-origin path, otherwise null: callers then render plain text or a disabled item.
 *
 * Accepted: a single leading "/" followed by non-control, non-space, non-backslash characters.
 * Rejected: any scheme (`javascript:`, `data:`, `https:`), protocol-relative `//host`, `/\host`,
 * backslashes, whitespace/control characters (raw or percent-encoded), empty, over-long values.
 */
// Controls, space, DEL, C1 controls, backslash, U+2028/2029.
const UNSAFE_CHARS = new RegExp("[\\u0000-\\u0020\\u007f-\\u009f\\\\\\u2028\\u2029]");

export function safeInternalHref(value: unknown): string | null {
  if (typeof value !== "string" || value.length === 0 || value.length > 2048) return null;
  if (value[0] !== "/" || value[1] === "/" || value[1] === "\\") return null;

  if (UNSAFE_CHARS.test(value)) return null;
  if (/%(?:[01][0-9a-f]|7f|5c|2f%2f)/i.test(value)) return null;
  return value;
}
