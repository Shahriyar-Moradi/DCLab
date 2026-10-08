"use client";

import { useMemo, useState, type ReactNode } from "react";

export type Column<Row> = {
  key: string;
  header: string;
  /** Right-align numeric columns. */
  numeric?: boolean;
  /** Sort value; presence makes the column sortable. */
  sortValue?: (row: Row) => string | number;
  render?: (row: Row) => ReactNode;
};

export type DataTableProps<Row> = {
  columns: Column<Row>[];
  rows: Row[];
  rowKey: (row: Row) => string;
  /** Accessible table name (also the caption). */
  caption: string;
  highlightRow?: (row: Row) => boolean;
  emptyMessage?: ReactNode;
};

export function DataTable<Row>({ columns, rows, rowKey, caption, highlightRow, emptyMessage }: DataTableProps<Row>) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const sorted = useMemo(() => {
    const column = columns.find((c) => c.key === sort?.key);
    if (!sort || !column?.sortValue) return rows;
    const value = column.sortValue;
    return [...rows].sort((a, b) => (value(a) < value(b) ? -sort.dir : value(a) > value(b) ? sort.dir : 0));
  }, [columns, rows, sort]);

  if (rows.length === 0) return <div className="empty">{emptyMessage ?? "Nothing to show yet."}</div>;

  return (
    <div className="tbl card">
      <table>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((column) => {
              const active = sort?.key === column.key;
              return (
                <th
                  key={column.key}
                  scope="col"
                  className={column.numeric ? "r" : undefined}
                  aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : column.sortValue ? "none" : undefined}
                >
                  {column.sortValue ? (
                    <button type="button" onClick={() => setSort({ key: column.key, dir: active && sort.dir === 1 ? -1 : 1 })}>
                      {column.header}
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr key={rowKey(row)} className={highlightRow?.(row) ? "win" : undefined}>
              {columns.map((column) => (
                <td key={column.key} className={column.numeric ? "r" : undefined}>
                  {column.render ? column.render(row) : String((row as Record<string, unknown>)[column.key] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
