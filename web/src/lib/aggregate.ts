/**
 * Group identical span shapes regardless of when they occurred, heaviest
 * first. This is the view that was missing: with 22 queries across 4 streams,
 * time order alone is noise, and "what is expensive" is unanswerable from it.
 *
 * Self time is total minus the time spent inside children — the same
 * distinction a profiler draws, and the one that tells you whether a span is
 * slow itself or merely contains something slow.
 */

import type { SpanRow } from "@/lib/trace";

export interface Bucket {
  name: string;
  totalS: number;
  selfS: number;
  count: number;
}

export function aggregate(rows: SpanRow[]): Bucket[] {
  const childTime = new Map<string, number>();
  for (const row of rows) {
    if (row.parentId === null) continue;
    const span = Math.max(row.endS - row.startS, 0);
    childTime.set(row.parentId, (childTime.get(row.parentId) ?? 0) + span);
  }

  const buckets = new Map<string, Bucket>();
  for (const row of rows) {
    const name = row.name ?? "";
    const total = Math.max(row.endS - row.startS, 0);
    // Children can overlap each other, so their summed time can exceed the
    // parent's own span. Clamp rather than report a negative self time.
    const self = Math.max(total - (childTime.get(row.id) ?? 0), 0);
    const bucket = buckets.get(name);
    if (bucket === undefined) {
      buckets.set(name, { name, totalS: total, selfS: self, count: 1 });
    } else {
      bucket.totalS += total;
      bucket.selfS += self;
      bucket.count += 1;
    }
  }

  return [...buckets.values()].sort((a, b) => b.totalS - a.totalS);
}
