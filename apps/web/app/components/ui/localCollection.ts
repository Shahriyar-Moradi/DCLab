export type LocalSortDir = "asc" | "desc";

export function filterByText<T>(
  rows: T[],
  query: string,
  haystack: (row: T) => Array<string | number | boolean | null | undefined>,
): T[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return rows;
  return rows.filter((row) =>
    haystack(row)
      .map((part) => (part == null ? "" : String(part)))
      .join(" ")
      .toLowerCase()
      .includes(needle),
  );
}

export function nextLocalSort(
  currentId: string,
  currentDir: LocalSortDir,
  columnId: string,
): { id: string; dir: LocalSortDir } {
  if (currentId === columnId) {
    return { id: columnId, dir: currentDir === "asc" ? "desc" : "asc" };
  }
  return { id: columnId, dir: "desc" };
}

export function sortByValue<T>(
  rows: T[],
  id: string,
  dir: LocalSortDir,
  valueOf: (row: T, id: string) => string | number | null | undefined,
): T[] {
  const copy = [...rows];
  const mul = dir === "asc" ? 1 : -1;
  copy.sort((left, right) => {
    const a = valueOf(left, id);
    const b = valueOf(right, id);
    if (a == null && b == null) return 0;
    if (a == null) return 1;
    if (b == null) return -1;
    if (typeof a === "number" && typeof b === "number") return (a - b) * mul;
    return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" }) * mul;
  });
  return copy;
}

export function sortMarker(activeId: string, columnId: string, dir: LocalSortDir) {
  if (activeId !== columnId) return "";
  return dir === "desc" ? " ↓" : " ↑";
}
