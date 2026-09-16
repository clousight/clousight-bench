/**
 * Per-span self and total time. Self time is what tells you whether a span is
 * slow itself or merely contains something slow.
 */
import { useMemo, useState } from "react";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { aggregate, type Bucket } from "@/lib/aggregate";
import { fmtDur } from "@/lib/format";
import type { SpanRow } from "@/lib/trace";

export type SortKey = "totalS" | "selfS" | "count";

/** Sort buckets by one numeric field, heaviest first, without mutating the
 * input array — `aggregate()` already returns a fresh array sorted by
 * `selfS`, but re-sorting by `totalS`/`count` in place would corrupt that
 * return value for any other reader still holding it (memoized or not). */
export function sortBuckets(buckets: Bucket[], key: SortKey): Bucket[] {
  return [...buckets].sort((a, b) => b[key] - a[key]);
}

/** The first row's id that matches a bucket, or null if the selection that
 * produced these rows has since emptied. Matches on the (kind, name) pair,
 * never on name alone: a bucket is one kind by construction (see
 * `lib/aggregate.ts`), so resolving by name alone could hand back a span from
 * a different bucket entirely. */
export function firstIdFor(rows: SpanRow[], bucket: Bucket): string | null {
  const match = rows.find((row) => (row.name ?? "") === bucket.name && row.kind === bucket.kind);
  return match?.id ?? null;
}

export function TablePane({
  rows,
  window,
  onSelect,
}: {
  rows: SpanRow[];
  window: { startS: number; endS: number };
  onSelect: (id: string) => void;
}) {
  const { t } = useI18n();
  // Self, not total: a pure container's total is its children's time, so
  // opening on it puts the spans that spent nothing at the top. The column
  // headers stay user-controllable; only where the table opens changed.
  const [sort, setSort] = useState<SortKey>("selfS");
  const buckets = useMemo(() => sortBuckets(aggregate(rows, window), sort), [rows, window, sort]);

  if (buckets.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("timeline.empty_selection")}</p>;
  }

  // The sort control is a button inside the header cell, not a click handler on
  // the `<th>` itself: a bare onClick on a table header is unreachable by
  // keyboard and announces nothing to a screen reader.
  const sortButton = (key: SortKey, label: string) => (
    <button
      type="button"
      onClick={() => setSort(key)}
      aria-pressed={sort === key}
      className={
        sort === key
          ? "border-b-2 border-foreground text-foreground"
          : "border-b-2 border-transparent transition-colors hover:text-foreground"
      }
    >
      {label}
    </button>
  );

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("timeline.span")}</TableHead>
          <TableHead aria-sort={sort === "totalS" ? "descending" : "none"}>
            {sortButton("totalS", t("timeline.total_time"))}
          </TableHead>
          <TableHead aria-sort={sort === "selfS" ? "descending" : "none"}>
            {sortButton("selfS", t("timeline.self"))}
          </TableHead>
          <TableHead aria-sort={sort === "count" ? "descending" : "none"}>
            {sortButton("count", t("timeline.count"))}
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {buckets.map((bucket) => (
          // No onClick, no tabIndex on the row itself: a bare click handler on
          // a `<tr>` is exactly the anti-pattern the header's own sort button
          // was built to avoid — unreachable by keyboard, silent to a screen
          // reader. The name cell's content is a real `<button>` instead, the
          // same shape `RunsView.tsx` uses (an interactive element inside the
          // cell, not a handler on the row), so the row-to-target path is
          // reachable and announced like any other control in this feature.
          <TableRow key={`${bucket.kind}:${bucket.name}`}>
            <TableCell className="font-mono text-xs">
              <button
                type="button"
                onClick={() => {
                  const id = firstIdFor(rows, bucket);
                  if (id !== null) onSelect(id);
                }}
                // `text-left`: a bare <button> centres its content in the
                // user-agent stylesheet, and this cell reads left-to-right
                // like every other cell in the table. `min-w-0` on the name
                // span is what lets `truncate` actually clip inside a flex
                // row — without it a flex item won't shrink below its
                // content's natural width, so a long name would stretch the
                // column instead of ellipsizing. `shrink-0` keeps the kind
                // label always legible rather than fighting the name for
                // space.
                className="flex w-full items-center gap-2 text-left"
              >
                <span className="min-w-0 flex-1 truncate">
                  {bucket.name === "" ? t("common.unnamed") : bucket.name}
                </span>
                <span className="shrink-0 text-muted-foreground">{bucket.kind}</span>
              </button>
            </TableCell>
            <TableCell className="font-mono text-xs tabular-nums">{fmtDur(bucket.totalS)}</TableCell>
            <TableCell className="font-mono text-xs tabular-nums">{fmtDur(bucket.selfS)}</TableCell>
            <TableCell className="font-mono text-xs tabular-nums">{bucket.count}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
