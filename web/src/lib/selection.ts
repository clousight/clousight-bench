/**
 * The selection: a start time, an end time, and a set of tracks. Every pane
 * reads it and none owns it.
 *
 * A playhead answers "what was happening at t". A range answers "where did the
 * time go in these seconds", which is the question an operator actually has.
 */

import type { SpanRow } from "@/lib/trace";
import type { Track } from "@/lib/tracks";

export interface Selection {
  startS: number;
  endS: number;
  trackIds: ReadonlySet<string>;
}

export function fullSelection(rows: SpanRow[], tracks: Track[]): Selection {
  let startS = Number.POSITIVE_INFINITY;
  let endS = Number.NEGATIVE_INFINITY;
  for (const row of rows) {
    if (row.startS < startS) startS = row.startS;
    if (row.endS > endS) endS = row.endS;
  }
  if (!Number.isFinite(startS) || !Number.isFinite(endS)) {
    startS = 0;
    endS = 0;
  }
  return { startS, endS, trackIds: new Set(tracks.map((track) => track.id)) };
}

/**
 * Does a row fall in the window at all?
 *
 * Overlap, not containment: a span straddling an edge is part of what happened
 * in the window, and dropping it would under-count the time. Where both widths
 * are real the comparison is strict, because a span ending exactly at the
 * window's start did not happen in the window, and loosening that wholesale
 * would pull a neighbour in on every drag.
 *
 * Zero width is the exception, and it is not a corner case: `trace.ts`
 * explicitly supports a trace whose timestamps all collapse to `t0`, and a
 * degenerate span has no width to overlap *with* — strict comparison rejects
 * it against every window, including the full one that was derived from it. So
 * when either side is a point rather than an interval, compare inclusively.
 * That is what makes `selectSpans(rows, tracks, fullSelection(rows, tracks))`
 * the identity for every input, which is the arrival state: if it dropped a
 * row, the reader would land on an empty pane with no narrower window to widen
 * back out of.
 */
function overlaps(row: SpanRow, selection: Selection): boolean {
  if (row.endS <= row.startS || selection.endS <= selection.startS) {
    return row.startS <= selection.endS && row.endS >= selection.startS;
  }
  return row.startS < selection.endS && row.endS > selection.startS;
}

export function selectSpans(rows: SpanRow[], tracks: Track[], selection: Selection): SpanRow[] {
  const allowed = new Set<string>();
  for (const track of tracks) {
    if (!selection.trackIds.has(track.id)) continue;
    for (const id of track.spanIds) allowed.add(id);
  }
  return rows.filter((row) => allowed.has(row.id) && overlaps(row, selection));
}

/**
 * Do two filtered row lists hold the same rows, in the same order?
 *
 * `selectSpans` allocates a fresh array every call, and a drag calls it on
 * every `pointermove` — but the rows it returns only change when a span
 * enters or leaves the window, a handful of times per drag rather than a few
 * hundred. Consumers key expensive work on the array's *identity*
 * (`Waterfall` holds it in an effect dependency list and disposes its ECharts
 * instance when it changes), so the caller collapses an equal result back to
 * the previous array using this.
 *
 * Element identity, not deep equality: rows come from one memoised
 * `buildRows` call, so two lists holding the same spans hold the same
 * objects. Order is part of the answer because the waterfall draws rows in
 * the order it is given.
 */
export function sameRows(a: readonly SpanRow[], b: readonly SpanRow[]): boolean {
  if (a === b) return true;
  if (a.length !== b.length) return false;
  for (let index = 0; index < a.length; index += 1) {
    if (a[index] !== b[index]) return false;
  }
  return true;
}

export function clampSelection(selection: Selection, minS: number, maxS: number): Selection {
  const lo = Math.min(minS, maxS);
  const hi = Math.max(minS, maxS);
  const a = Math.min(Math.max(selection.startS, lo), hi);
  const b = Math.min(Math.max(selection.endS, lo), hi);
  return { startS: Math.min(a, b), endS: Math.max(a, b), trackIds: selection.trackIds };
}
