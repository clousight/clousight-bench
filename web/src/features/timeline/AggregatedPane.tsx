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

export function AggregatedPane({ rows }: { rows: SpanRow[] }) {
  const { t } = useI18n();
  const buckets = useMemo(() => aggregate(rows), [rows]);

  if (buckets.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("timeline.empty_selection")}</p>;
  }

  const heaviest = buckets[0].totalS;
  const grand = buckets.reduce((sum: number, bucket: Bucket) => sum + bucket.totalS, 0);

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
              width: `${barWidthPct(bucket.totalS, heaviest)}%`,
              ...laneSpanStyle(bucket.kind, false, true),
            }}
          />
          <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
            {fmtDur(bucket.totalS)}
            <span className="mx-1.5">·</span>
            {sharePct(bucket.totalS, grand)}% {t("timeline.share")}
            <span className="mx-1.5">·</span>×{bucket.count}
          </span>
        </div>
      ))}
    </div>
  );
}
