/**
 * Identical span shapes merged, heaviest first. With 22 queries across 4
 * streams this is the only view that answers "what is expensive" — time order
 * at that cardinality is noise.
 */
import { useMemo } from "react";

import { laneSpanStyle } from "@/charts/palette";
import { useI18n } from "@/i18n";
import { aggregate, type Bucket } from "@/lib/aggregate";
import { fmtDur } from "@/lib/format";
import type { SpanRow } from "@/lib/trace";

/** Bar width as a percentage of the widest bucket, capped at 60% of the row
 * so the row's trailing label never has to compete with the bar for space.
 * Degenerates to 0 rather than dividing by zero when there is no heaviest
 * bucket to measure against (an empty aggregate). */
export function barWidthPct(totalS: number, heaviestS: number): number {
  if (heaviestS <= 0) return 0;
  return (totalS / heaviestS) * 60;
}

/** A bucket's share of the grand total, rounded to a whole percent.
 * Degenerates to 0 rather than dividing by zero when the grand total is 0
 * (every bucket has zero duration). */
export function sharePct(totalS: number, grandS: number): number {
  if (grandS <= 0) return 0;
  return Math.round((totalS / grandS) * 100);
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
          {/* Colour by kind from the validated palette rather than one flat hue —
              the bucket key guarantees a bucket is exactly one kind, so the bar
              can carry that identity honestly. `laneSpanStyle` is the same
              function the strip and the lanes paint with, so all three panes
              agree on what a kind looks like by construction rather than by
              three copies of one rule staying in step. A bucket has no error
              state of its own, so pass `false`. */}
          <span
            aria-hidden
            className="h-3 shrink-0 rounded-[1px]"
            style={{
              width: `${barWidthPct(bucket.selfS, heaviest)}%`,
              ...laneSpanStyle(bucket.kind, false, true),
            }}
          />
          {/* Self first, because that is what the bar and the ranking are.
              Total stays beside it: the gap between the two is what tells a
              reader whether a span is slow itself or merely contains
              something slow, and a bar with no total next to it would make a
              pure container look like it had vanished. */}
          <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
            {fmtDur(bucket.selfS)} {t("timeline.self")}
            <span className="mx-1.5">·</span>
            {fmtDur(bucket.totalS)} {t("timeline.total_time")}
            <span className="mx-1.5">·</span>
            {sharePct(bucket.selfS, grand)}% {t("timeline.share")}
            <span className="mx-1.5">·</span>×{bucket.count}
          </span>
        </div>
      ))}
    </div>
  );
}
