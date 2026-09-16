/**
 * Identical span shapes merged, heaviest first. With 22 queries across 4
 * streams this is the only view that answers "what is expensive" — time order
 * at that cardinality is noise.
 */
import { useMemo } from "react";

import { laneSpanStyle } from "@/charts/palette";
import { useI18n } from "@/i18n";
import { aggregate, type Bucket } from "@/lib/aggregate";
import { fmtSpanDur } from "@/lib/format";
import type { SpanRow } from "@/lib/trace";

/**
 * Bar width as a percentage of the bar's own fixed-width TRACK, so the
 * heaviest bucket fills it and every other bar is proportional to that.
 *
 * The cap is the track, not arithmetic here. The previous version scaled to
 * 60% of the whole row and its docstring said "capped at 60%", which was not
 * what the code did — nothing was ever clipped, the heaviest bucket simply
 * always landed on 60 because it is its own denominator, and the `<= 60`
 * assertion that was supposed to check the cap passed either way. It also
 * made the bar and the row's trailing label compete for the same space: at
 * 60% the heaviest row's trailer had nowhere to go and wrapped onto a second
 * line, 33px against 17px for every other row — on the one row a reader looks
 * at first. A bar that draws inside a `shrink-0` track cannot take space from
 * anything, so the geometry stops being a negotiation.
 *
 * Degenerates to 0 rather than dividing by zero when there is no heaviest
 * bucket to measure against. That is reachable, not defensive: `aggregate`
 * ranks by self time and a window can hold nothing but pure containers, whose
 * self time is legitimately 0.
 */
export function barWidthPct(totalS: number, heaviestS: number): number {
  if (heaviestS <= 0) return 0;
  return (totalS / heaviestS) * 100;
}

/**
 * A bucket's share of the grand total, as a percentage string.
 *
 * Rounded, not floored: two thirds of the work is 67%, and a column that
 * reads 66% invites the reader to check the arithmetic against a number that
 * was never claimed.
 *
 * But rounding alone erases the long tail. On a wide window a trace has
 * hundreds of buckets, most of them well under half a percent, and rounding
 * turns every one of them into "0%" — a row that measured something saying it
 * measured nothing. Anything above zero and below one percent reads "<1%"
 * instead: still not a number to add up, but no longer a denial. An exact zero
 * keeps "0%", because a pure container really did no work of its own.
 *
 * Returns a formatted string rather than a number so the sub-1% case cannot
 * be lost at the call site by a caller appending "%" to it.
 */
export function fmtShare(totalS: number, grandS: number): string {
  // Degenerates rather than dividing by zero when every bucket in the window
  // has zero duration.
  if (grandS <= 0) return "0%";
  const share = (totalS / grandS) * 100;
  if (share === 0) return "0%";
  if (share < 1) return "<1%";
  return `${Math.round(share)}%`;
}

/**
 * The denominator every share is taken against: the total work done in the
 * window, which is the sum of SELF times.
 *
 * Summing `totalS` instead counts every parent again inside each of its
 * children — the browser showed `csbench.run` at "13.0s · 32%", and
 * 13.0/0.323 is 40.29 s, the sum of all 107 span durations in a 13 s run. A
 * share against that is a share of nothing physical. Self time does not
 * double-count, so these shares partition real work and sum to 100%.
 *
 * With genuine concurrency the sum legitimately exceeds the window's wall
 * clock — three streams busy for one second each spend three seconds of work
 * in one second of clock — which is why the label says work, not elapsed.
 */
export function grandSelfS(buckets: Bucket[]): number {
  return buckets.reduce((sum: number, bucket: Bucket) => sum + bucket.selfS, 0);
}

export function AggregatedPane({
  rows,
  allRows,
  window,
}: {
  /** The spans the track filter left visible — what this pane lists. */
  rows: SpanRow[];
  /** Every span in the trace, so a parent's self time stays the same number
   * whether or not the lane holding its children is checked. */
  allRows: SpanRow[];
  window: { startS: number; endS: number };
}) {
  const { t } = useI18n();
  const buckets = useMemo(() => aggregate(rows, window, allRows), [rows, window, allRows]);

  if (buckets.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("timeline.empty_selection")}</p>;
  }

  // `aggregate` ranks by self time, so the first bucket is the heaviest by the
  // same measure the bars draw.
  const heaviest = buckets[0].selfS;
  const grand = grandSelfS(buckets);

  return (
    <div className="flex flex-col gap-1.5">
      {buckets.map((bucket) => (
        // Key on the pair, never on the name: two kinds can share a name and
        // are deliberately separate buckets, so a name-only key collides.
        <div key={`${bucket.kind}:${bucket.name}`} className="flex items-center gap-3">
          <span className="w-40 shrink-0 truncate font-mono text-[11px] text-muted-foreground">
            {bucket.name === "" ? t("common.unnamed") : bucket.name}
          </span>
          {/* The bar draws inside a fixed-width track, not as a percentage of
              the row. As a percentage of the row it took space the trailing
              label needed, and the heaviest bucket — always the widest bar —
              wrapped its own row onto a second line. `shrink-0` on the track
              and `whitespace-nowrap` on the trailer make the row's height
              independent of what the bar happens to measure.

              Colour by kind from the validated palette rather than one flat hue —
              the bucket key guarantees a bucket is exactly one kind, so the bar
              can carry that identity honestly. `laneSpanStyle` is the same
              function the strip and the lanes paint with, so all three panes
              agree on what a kind looks like by construction rather than by
              three copies of one rule staying in step. A bucket has no error
              state of its own, so pass `false`. */}
          <span aria-hidden className="h-3 w-24 shrink-0">
            <span
              className="block h-full rounded-[1px]"
              style={{
                width: `${barWidthPct(bucket.selfS, heaviest)}%`,
                ...laneSpanStyle(bucket.kind, false, true),
              }}
            />
          </span>
          {/* Self first, because that is what the bar and the ranking are.
              Total stays beside it: the gap between the two is what tells a
              reader whether a span is slow itself or merely contains
              something slow, and a bar with no total next to it would make a
              pure container look like it had vanished. */}
          <span className="whitespace-nowrap font-mono text-[11px] tabular-nums text-muted-foreground">
            {fmtSpanDur(bucket.selfS)} {t("timeline.self")}
            <span className="mx-1.5">·</span>
            {fmtSpanDur(bucket.totalS)} {t("timeline.total_time")}
            <span className="mx-1.5">·</span>
            {fmtShare(bucket.selfS, grand)} {t("timeline.share")}
            <span className="mx-1.5">·</span>×{bucket.count}
          </span>
        </div>
      ))}
    </div>
  );
}
