/**
 * Self and total time per BUCKET — one row per (kind, name) pair, not one row
 * per span. `aggregate()` merges identical span shapes, so the `count` column
 * is how many spans a row stands for; a trace with 900 `query` spans across 22
 * statements is 22 rows here, which is the whole reason this pane exists
 * beside the waterfall.
 *
 * Self time is what tells you whether a span is slow itself or merely contains
 * something slow.
 */
import { useMemo, useState } from "react";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { aggregate, type Bucket } from "@/lib/aggregate";
import { fmtSpanDur } from "@/lib/format";
import type { SpanRow } from "@/lib/trace";
import { cn } from "@/lib/utils";

export type SortKey = "totalS" | "selfS" | "count";

/** Sort buckets by one numeric field, heaviest first, without mutating the
 * input array — `aggregate()` already returns a fresh array sorted by
 * `selfS`, but re-sorting by `totalS`/`count` in place would corrupt that
 * return value for any other reader still holding it (memoized or not). */
export function sortBuckets(buckets: Bucket[], key: SortKey): Bucket[] {
  return [...buckets].sort((a, b) => b[key] - a[key]);
}

/**
 * The first row's id that matches a bucket, or null if none does.
 *
 * Matches on the (kind, name) pair, never on name alone: a bucket is one kind
 * by construction (see `lib/aggregate.ts`), so resolving by name alone could
 * hand back a span from a different bucket entirely.
 *
 * The null is deliberately kept even though this pane's only call site cannot
 * produce it — the buckets it passes were aggregated from the very `rows` it
 * passes, in the same render, so a match always exists. It is kept because the
 * alternative is not "delete a dead branch", it is a non-null assertion that
 * turns a lookup miss into a runtime TypeError, and this is a general
 * bucket -> row lookup that the next caller may not satisfy so neatly. The
 * test below pins the returned value, which is what a caller depends on; it
 * does not claim to exercise a path this component can reach.
 */
export function firstIdFor(rows: SpanRow[], bucket: Bucket): string | null {
  const match = rows.find((row) => (row.name ?? "") === bucket.name && row.kind === bucket.kind);
  return match?.id ?? null;
}

export function TablePane({
  rows,
  allRows,
  window,
  onSelect,
}: {
  /** The spans the track filter left visible — what this table lists. */
  rows: SpanRow[];
  /** Every span in the trace, so a parent's self time stays the same number
   * whether or not the lane holding its children is checked. */
  allRows: SpanRow[];
  window: { startS: number; endS: number };
  onSelect: (id: string) => void;
}) {
  const { t } = useI18n();
  // Self, not total: a pure container's total is its children's time, so
  // opening on it puts the spans that spent nothing at the top. The column
  // headers stay user-controllable; only where the table opens changed.
  const [sort, setSort] = useState<SortKey>("selfS");
  const buckets = useMemo(
    () => sortBuckets(aggregate(rows, window, allRows), sort),
    [rows, window, allRows, sort],
  );

  if (buckets.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("timeline.empty_selection")}</p>;
  }

  // The sort control is a button inside the header cell, not a click handler on
  // the `<th>` itself: a bare onClick on a table header is unreachable by
  // keyboard and announces nothing to a screen reader.
  //
  // `min-h-6` and the horizontal padding are the target, not decoration: the
  // button used to be exactly as tall as its 11px text, about 10px of
  // pointer-reachable height, against WCAG 2.2's 24x24 minimum. The border
  // that marks the active column now sits at the bottom of a 24px box rather
  // than under the glyphs, which is also why it reads as a column marker
  // instead of an underline. `focus-visible:ring-ring` is the app's ring
  // token — this whole feature was falling through to the UA default outline
  // while every other control in the app used it.
  const sortButton = (key: SortKey, label: string) => (
    <button
      type="button"
      onClick={() => setSort(key)}
      aria-pressed={sort === key}
      className={cn(
        "inline-flex min-h-6 items-center px-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        sort === key
          ? "border-b-2 border-foreground text-foreground"
          : "border-b-2 border-transparent transition-colors hover:text-foreground",
      )}
    >
      {label}
    </button>
  );

  return (
    // `table-fixed`, and a width on the name column, are what make the
    // `truncate` below able to fire at all. Auto table layout sizes a column
    // BY its content, so the cell was never width-constrained and the class
    // did nothing: a 176-character span name measured 1340 px, pushed the
    // table to 1495 px inside a 1400 px viewport, and the parent's
    // `overflow-x: auto` turned that into a horizontal scrollbar that shoved
    // total/self/count off screen. Under fixed layout the columns are settled
    // from this header row before any cell content is looked at, so the table
    // is exactly as wide as its container at any name length and the name is
    // the thing that gives way. Same reason `AggregatedPane` pins its label
    // column to `w-40`; a fraction rather than a fixed 160 px because this
    // table is full-width and has three more columns to feed.
    <Table className="table-fixed">
      <TableHeader>
        <TableRow>
          <TableHead className="w-1/2">{t("timeline.span")}</TableHead>
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
                // like every other cell in the table. `w-full` is now a
                // definite width, because the fixed layout above settled the
                // column — under auto layout it resolved against a column
                // being sized by this very content. `min-w-0` on the name span
                // is what lets `truncate` clip inside a flex row: without it a
                // flex item won't shrink below its content's natural width.
                // `shrink-0` keeps the kind label always legible rather than
                // fighting the name for space.
                className="flex w-full items-center gap-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {/* Clipped, so the full name has to stay recoverable. */}
                <span
                  className="min-w-0 flex-1 truncate"
                  title={bucket.name === "" ? t("common.unnamed") : bucket.name}
                >
                  {bucket.name === "" ? t("common.unnamed") : bucket.name}
                </span>
                <span className="shrink-0 text-muted-foreground">{bucket.kind}</span>
              </button>
            </TableCell>
            <TableCell className="font-mono text-xs tabular-nums">{fmtSpanDur(bucket.totalS)}</TableCell>
            <TableCell className="font-mono text-xs tabular-nums">{fmtSpanDur(bucket.selfS)}</TableCell>
            <TableCell className="font-mono text-xs tabular-nums">{bucket.count}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
