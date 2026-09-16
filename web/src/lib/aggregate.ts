/**
 * Group identical span shapes regardless of when they occurred, heaviest
 * first. This is the view that was missing: with 22 queries across 4 streams,
 * time order alone is noise, and "what is expensive" is unanswerable from it.
 *
 * Self time is total minus the time spent inside children — the same
 * distinction a profiler draws, and the one that tells you whether a span is
 * slow itself or merely contains something slow.
 *
 * Buckets are keyed on (kind, name), not name alone. `kind` is already
 * semantic identity elsewhere in this codebase — tracks.ts branches on
 * `row.kind === "lifecycle"`, and charts/palette.ts colours by kind via
 * KIND_SLOTS — so a bucket spanning two kinds cannot be coloured
 * consistently: it is two things wearing one row. The failure modes are not
 * symmetric either: splitting something that could have been one row costs a
 * reader one extra line, but merging two different operations presents a
 * single number that is not what it claims to be. "The same name in a
 * different suite might carry a different kind" does not save name-only
 * keying, because those spans already have different names
 * (`tpc-h.q21` vs. `tpc-ds.q21`).
 */

import type { SpanRow } from "@/lib/trace";

export interface Bucket {
  name: string;
  kind: string;
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

  // Nested by kind then name, rather than a joined string key, so that no
  // delimiter choice can let two distinct (kind, name) pairs collide.
  const buckets = new Map<string, Map<string, Bucket>>();
  for (const row of rows) {
    const name = row.name ?? "";
    const total = Math.max(row.endS - row.startS, 0);
    // Children can overlap each other, so their summed time can exceed the
    // parent's own span. Clamp rather than report a negative self time.
    const self = Math.max(total - (childTime.get(row.id) ?? 0), 0);
    let byName = buckets.get(row.kind);
    if (byName === undefined) {
      byName = new Map<string, Bucket>();
      buckets.set(row.kind, byName);
    }
    const bucket = byName.get(name);
    if (bucket === undefined) {
      byName.set(name, { name, kind: row.kind, totalS: total, selfS: self, count: 1 });
    } else {
      bucket.totalS += total;
      bucket.selfS += self;
      bucket.count += 1;
    }
  }

  return [...buckets.values()]
    .flatMap((byName) => [...byName.values()])
    .sort((a, b) => b.totalS - a.totalS);
}
