/**
 * Group identical span shapes regardless of when they occurred, heaviest
 * first. This is the view that was missing: with 22 queries across 4 streams,
 * time order alone is noise, and "what is expensive" is unanswerable from it.
 *
 * Every number here is clipped to the selection window and then ranked by
 * self time — see `clipped()` and the sort at the bottom for why each of
 * those is load-bearing rather than a detail.
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

/**
 * The part of an interval that happened inside the window.
 *
 * `selectSpans` filters by *overlap*, so a span straddling an edge arrives
 * whole: `tpc-h.load` runs 5.41 -> 11.84 s and reported all 6.43 s of itself
 * inside a 1.56 s window, which made the self column sum to several times the
 * wall clock captioned directly above it. Clipping is what makes the header's
 * "selected 1.56s of 13.0s" and every number under it the same claim.
 */
function clipped(startS: number, endS: number, window: { startS: number; endS: number }): number {
  return Math.max(Math.min(endS, window.endS) - Math.max(startS, window.startS), 0);
}

export function aggregate(rows: SpanRow[], window: { startS: number; endS: number }): Bucket[] {
  // Child time is clipped too, not only the parent's own span. Subtracting a
  // child's full duration from a clipped parent can only under-report self
  // time, and for a window narrower than the child it pins every parent to
  // the zero clamp — the pane would read 0.00s everywhere it mattered most.
  const childTime = new Map<string, number>();
  for (const row of rows) {
    if (row.parentId === null) continue;
    const span = clipped(row.startS, row.endS, window);
    childTime.set(row.parentId, (childTime.get(row.parentId) ?? 0) + span);
  }

  // Nested by kind then name, rather than a joined string key, so that no
  // delimiter choice can let two distinct (kind, name) pairs collide.
  const buckets = new Map<string, Map<string, Bucket>>();
  for (const row of rows) {
    const total = clipped(row.startS, row.endS, window);
    // Nothing of this span happened inside the window — it only touches an
    // edge, or the window is a point. A row of zeroes would assert the
    // opposite of what was measured, so it is not a bucket at all. The cost
    // is that an instantaneous span cannot be counted here; this pane answers
    // "where did the time go", and a span with no duration has no answer.
    if (total <= 0) continue;
    const name = row.name ?? "";
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

  // Ranked by SELF time. Ranked by total, the browser put `tpc-h.official`
  // (total 7.48s, self 0.00s — a pure container that holds children and does
  // nothing itself) above `tpc-h.load` (6.43s, all of it its own), which is
  // the wrong answer to the only question this pane exists to answer. Ties
  // fall back to total and then to name so a fixed input always sorts the
  // same way: zero-self containers are common, and without a tiebreak they
  // would order by whatever the Maps happened to iterate.
  return [...buckets.values()]
    .flatMap((byName) => [...byName.values()])
    .sort((a, b) => b.selfS - a.selfS || b.totalS - a.totalS || a.name.localeCompare(b.name, "en"));
}
