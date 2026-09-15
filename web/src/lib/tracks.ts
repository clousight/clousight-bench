/**
 * Lane assignment. The waterfall draws one row per span in start order, which
 * flattens concurrency — four throughput streams running at once read as
 * twelve sequential rows, and "did they stagger or collide?" cannot be asked.
 *
 * A track is a lane. Lifecycle spans get one reserved lane because they are the
 * frame, not a series. Work spans are grouped by an explicit stream attribute
 * when the suite declares one, and otherwise packed greedily: siblings that do
 * not overlap in time can share a lane, siblings that do cannot.
 */

import type { SpanRow } from "@/lib/trace";

/** The reserved lane for the run's own stages. */
export const STAGE_TRACK_ID = "lifecycle";

/** Attribute a suite sets when it knows its own concurrency. */
const STREAM_ATTR = "csbench.stream";

export interface Track {
  id: string;
  label: string;
  kind: string;
  spanIds: string[];
}

function streamOf(row: SpanRow): string | null {
  const raw = row.attrs[STREAM_ATTR];
  if (typeof raw === "number" && Number.isFinite(raw)) return String(raw);
  if (typeof raw === "string" && raw !== "") return raw;
  return null;
}

export function assignTracks(rows: SpanRow[]): Track[] {
  if (rows.length === 0) return [];

  const stage: string[] = [];
  const declared = new Map<string, string[]>();
  const packable: SpanRow[] = [];

  for (const row of rows) {
    if (row.kind === "lifecycle") {
      stage.push(row.id);
      continue;
    }
    const stream = streamOf(row);
    if (stream !== null) {
      const bucket = declared.get(stream);
      if (bucket === undefined) declared.set(stream, [row.id]);
      else bucket.push(row.id);
      continue;
    }
    packable.push(row);
  }

  const tracks: Track[] = [];
  if (stage.length > 0) {
    tracks.push({ id: STAGE_TRACK_ID, label: STAGE_TRACK_ID, kind: "lifecycle", spanIds: stage });
  }

  // Declared streams sort by their label so lane order is stable across runs.
  for (const stream of [...declared.keys()].sort((a, b) => a.localeCompare(b, "en"))) {
    tracks.push({
      id: `stream:${stream}`,
      label: `stream ${stream}`,
      kind: "stream",
      spanIds: declared.get(stream) ?? [],
    });
  }

  // Greedy interval packing: first lane whose last span ends at or before this
  // one starts. `rows` is already sorted by startS, so a single pass suffices.
  const laneEnds: number[] = [];
  const laneSpans: string[][] = [];
  for (const row of packable) {
    let lane = laneEnds.findIndex((end) => end <= row.startS);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(row.endS);
      laneSpans.push([row.id]);
    } else {
      laneEnds[lane] = row.endS;
      laneSpans[lane].push(row.id);
    }
  }
  for (const [index, spanIds] of laneSpans.entries()) {
    tracks.push({ id: `lane:${index}`, label: `lane ${index + 1}`, kind: "work", spanIds });
  }

  return tracks;
}
