import type { ReactNode } from "react";
import { EmptyState } from "./EmptyState";
import { Table, Td, Th } from "./Table";

export type DataTableColumn<T> = {
  id: string;
  header: string;
  mono?: boolean;
  sortable?: boolean;
  className?: string;
  cell: (row: T) => ReactNode;
};

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  emptyTitle = "Nothing to show",
  emptyBody = "There are no rows in this table.",
  sortId,
  onSort,
}: {
  columns: DataTableColumn<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  emptyTitle?: string;
  emptyBody?: string;
  sortId?: string;
  onSort?: (id: string) => void;
}) {
  if (rows.length === 0) {
    return <EmptyState title={emptyTitle} body={emptyBody} />;
  }
  return (
    <Table>
      <thead>
        <tr>
          {columns.map((column) => (
            <Th
              key={column.id}
              sortable={Boolean(column.sortable && onSort)}
              onSort={column.sortable && onSort ? () => onSort(column.id) : undefined}
              aria-sort={sortId === column.id ? "other" : undefined}
              className={column.className}
            >
              {column.header}
            </Th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={rowKey(row)} className="transition-ui hover:bg-navy-soft/40">
            {columns.map((column) => (
              <Td key={column.id} mono={column.mono} className={column.className}>
                {column.cell(row)}
              </Td>
            ))}
          </tr>
        ))}
      </tbody>
    </Table>
  );
}
