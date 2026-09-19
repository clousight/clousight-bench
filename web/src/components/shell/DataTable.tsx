/**
 * The list shell: a filter bar above a table that knows how to be empty, how
 * to be loading, and how to be paged.
 *
 * Every list in the app goes through here. The alternative — each view
 * assembling its own `<Table>` — is what produced three list pages that put
 * the filter in three different places and disagreed on what an empty result
 * looks like.
 *
 * It renders rows; it does not fetch, filter or sort them. The caller has the
 * data and the predicates, which keeps "what is on screen" answerable by
 * reading one component instead of two.
 */

import type { ReactNode } from "react";

import { EmptyState } from "@/components/shell/Page";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * The filter strip.
 *
 * Fields flow left; the count sits hard right, so "how many of how many" lands
 * in the same place on every list. The count is a string the caller formats —
 * this component never computes it, because a count derived here could
 * disagree with the rows the caller actually passed.
 */
export function FilterBar({ children, count }: { children: ReactNode; count?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end gap-x-4 gap-y-3 pb-4">
      {children}
      {count !== undefined && (
        <span className="ml-auto self-center font-mono text-xs tabular-nums text-muted-foreground">
          {count}
        </span>
      )}
    </div>
  );
}

/** A labelled control inside the filter strip. */
export function FilterField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-muted-foreground">
      {label}
      {children}
    </label>
  );
}

export interface Column<T> {
  /** Stable key; also the sort key when `sortable`. */
  key: string;
  header: ReactNode;
  /** Rendered per row. Return a string for text, or any node. */
  cell: (row: T) => ReactNode;
  /** Right-align — for numbers. */
  numeric?: boolean;
  className?: string;
}

export interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  /** Whole-row link. Renders the first cell as an anchor. */
  href?: (row: T) => string;
  loading?: boolean;
  error?: string | null;
  empty?: { title: string; blurb?: string; action?: ReactNode };
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  href,
  loading,
  error,
  empty,
}: DataTableProps<T>) {
  const { t } = useI18n();

  if (error !== undefined && error !== null) {
    return (
      <p role="alert" className="border-t border-border py-6 text-sm text-status-critical">
        {error}
      </p>
    );
  }
  if (loading === true) {
    return <p className="border-t border-border py-6 text-sm text-muted-foreground">{t("common.loading")}</p>;
  }
  if (rows.length === 0) {
    return (
      <EmptyState
        title={empty?.title ?? t("common.empty")}
        blurb={empty?.blurb}
        action={empty?.action}
      />
    );
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          {columns.map((column) => (
            <TableHead key={column.key} className={cn(column.numeric === true && "text-right")}>
              {column.header}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((row) => {
          const link = href?.(row);
          return (
            <TableRow key={rowKey(row)}>
              {columns.map((column, index) => (
                <TableCell
                  key={column.key}
                  className={cn(column.numeric === true && "text-right", column.className)}
                >
                  {index === 0 && link !== undefined ? (
                    <a
                      href={link}
                      className="underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {column.cell(row)}
                    </a>
                  ) : (
                    column.cell(row)
                  )}
                </TableCell>
              ))}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
