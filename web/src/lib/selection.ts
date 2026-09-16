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

export function selectSpans(rows: SpanRow[], tracks: Track[], selection: Selection): SpanRow[] {
  const allowed = new Set<string>();
  for (const track of tracks) {
    if (!selection.trackIds.has(track.id)) continue;
    for (const id of track.spanIds) allowed.add(id);
  }
  // Overlap, not containment: a span straddling an edge is part of what
  // happened in the window, and dropping it would under-count the time.
  return rows.filter(
    (row) => allowed.has(row.id) && row.startS < selection.endS && row.endS > selection.startS,
  );
}

export function clampSelection(selection: Selection, minS: number, maxS: number): Selection {
  const lo = Math.min(minS, maxS);
  const hi = Math.max(minS, maxS);
  const a = Math.min(Math.max(selection.startS, lo), hi);
  const b = Math.min(Math.max(selection.endS, lo), hi);
  return { startS: Math.min(a, b), endS: Math.max(a, b), trackIds: selection.trackIds };
}
