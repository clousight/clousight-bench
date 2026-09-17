/**
 * Lane assignment. The waterfall draws one row per span in start order, which
 * flattens concurrency — four throughput streams running at once read as
 * twelve sequential rows, and "did they stagger or collide?" cannot be asked.
 *
 * A track is a lane, and a lane groups spans that belong to the same logical
 * actor. Lifecycle spans get one reserved lane because they are the frame,
 * not a series. Work spans are grouped by an explicit stream attribute when
 * the suite declares one; otherwise they are grouped by `parentId` (siblings
 * under the same parent are the same actor; unrelated subtrees never share a
 * lane just because their timestamps happen not to collide).
 *
 * Both groupings then go through the same greedy packing: spans that do not
 * overlap in time can share a lane, spans that do cannot. A declared stream
 * is a group like any other in that respect — TPC-H's 23 spans per stream are
 * one `tpc-h.streamN` container plus the 22 queries it contains, which
 * overlap it by definition, so dropped into a single-height lane they would
 * draw on top of each other.
 */

import { packIntervals } from "@/lib/rowmodel";
import type { SpanRow } from "@/lib/trace";

/**
 * The reserved lane for the run's own stages, and the prefix of its lane ids.
 *
 * Reserved does not mean unpacked. `csbench.run` is itself a lifecycle span
 * and is the parent of all eleven stages, so it overlaps every one of them;
 * emitted as a single lane, twelve bars drew on top of each other at one
 * lane's height. It goes through `packGroup` like every other group, and the
 * lanes that come out are `lifecycle:0`, `lifecycle:1`, … — `TrackList`
 * recognises them by `kind`, not by an id equal to this constant.
 */
export const STAGE_TRACK_ID = "lifecycle";

/**
 * Attribute a suite sets when it knows its own concurrency.
 *
 * This must be the key the emitter actually writes. It read `csbench.stream`
 * for the whole life of this feature while `_tpc_official/trace.py` wrote
 * `csbench.stream_id`, so `streamOf` returned null for every span on the only
 * workload in the repo that declares streams, all 69 throughput queries fell
 * through to `parentId` packing, and the browser showed ten anonymous lanes.
 * Nothing caught it because the unit tests asserted against the invented key.
 * `test_track_stream_attribute_matches_the_emitter` in
 * tests/test_viewer_frontend.py now holds this constant against the Python
 * that produces it; keep the literal on one line so it stays greppable.
 */
const STREAM_ATTR = "csbench.stream_id";

/** Groups spans whose `parentId` is null — they are siblings of each other,
 * not of every other orphaned span, but null is not a comparable key on its
 * own, so they share this one synthetic group. */
const ROOT_GROUP = " root";

export interface Track {
  id: string;
  /**
   * The lane's English identity — NOT a display string.
   *
   * Nothing renders this verbatim. `TrackList.trackLabel` translates the noun
   * and re-composes it with whatever identifies the lane (`stream 2.2` ->
   * "数据流 2.2"), so for the lifecycle lanes the word `lifecycle` never
   * reaches a reader at all and only the number after it survives. It is kept
   * as English text rather than split into (noun, suffix) fields because
   * `tracks.ts` is the layer that names groups and `TrackList` is the layer
   * that speaks a language; a structured field here would put half a sentence
   * in the wrong module.
   */
  label: string;
  /**
   * Which grouping rule produced the lane: `lifecycle`, `stream` or `work`.
   *
   * Not the same vocabulary as `SpanRow.kind`, despite the name. A span's
   * kind is what it *is* (`query`, `llm_call`, …); a track's is how its spans
   * were gathered. They coincide for `lifecycle` and nowhere else — a
   * `stream` lane holds `query` spans, a `work` lane holds anything. Read
   * `track.kind` and `row.kind` as two different questions that happen to
   * share a spelling.
   */
  kind: string;
  spanIds: string[];
}

function streamOf(row: SpanRow): string | null {
  const raw = row.attrs[STREAM_ATTR];
  if (typeof raw === "number" && Number.isFinite(raw)) return String(raw);
  if (typeof raw === "string" && raw !== "") return raw;
  return null;
}

/** Greedy interval packing within one group: first lane whose last span ends
 * at or before this one starts. `groupRows` must already be sorted by
 * startS, so a single pass suffices.
 *
 * The packing itself now lives in `rowmodel.ts::packIntervals` (row model's
 * `packChildren` needed the same algorithm over full `SpanRow`s rather than
 * ids); this stays as the id-shaped adapter every call site here expects. */
function packGroup(groupRows: SpanRow[]): string[][] {
  return packIntervals(groupRows).map((lane) => lane.map((row) => row.id));
}

export function assignTracks(rows: SpanRow[]): Track[] {
  if (rows.length === 0) return [];

  // Sort defensively on a copy: the function's contract does not require the
  // caller to pre-sort, so an unsorted input must not silently degrade the
  // packing below instead of just working.
  const ordered = [...rows].sort((a, b) => a.startS - b.startS);

  const stage: SpanRow[] = [];
  const declared = new Map<string, SpanRow[]>();
  const packable: SpanRow[] = [];

  for (const row of ordered) {
    if (row.kind === "lifecycle") {
      stage.push(row);
      continue;
    }
    const stream = streamOf(row);
    if (stream !== null) {
      const bucket = declared.get(stream);
      if (bucket === undefined) declared.set(stream, [row]);
      else bucket.push(row);
      continue;
    }
    packable.push(row);
  }

  const tracks: Track[] = [];
  // The reserved group, packed like every other one. A run root that contains
  // eleven stages overlaps all of them, so this is typically two lanes: the
  // root on its own and the sequential stages beneath it. The naming follows
  // the streams' — one lane keeps the plain word, several are numbered after
  // it — so `TrackList.trackLabel` can translate the word and keep whatever
  // identifies the lane.
  const stageLanes = packGroup(stage);
  for (const [laneIndex, spanIds] of stageLanes.entries()) {
    tracks.push({
      id: `${STAGE_TRACK_ID}:${laneIndex}`,
      label: stageLanes.length === 1 ? STAGE_TRACK_ID : `${STAGE_TRACK_ID} ${laneIndex + 1}`,
      kind: "lifecycle",
      spanIds,
    });
  }

  // Declared streams sort by their label so lane order is stable across runs.
  // Each one is packed like any other group: a stream's container span covers
  // every query it holds, so an unpacked stream lane would stack a bar on top
  // of 22 others at one lane's height. A stream that needs more than one lane
  // numbers them after the stream (`stream 2.1`, `stream 2.2`) rather than
  // falling back to an anonymous running number — the identity is the point,
  // and TrackList's `trackLabel` carries whatever follows "stream " through to
  // the reader untouched. A stream that packs into a single lane keeps the
  // plain `stream 2`, so the common case is not dressed up as a split.
  const declaredStreams = [...declared.entries()].sort(([a], [b]) => a.localeCompare(b, "en"));
  for (const [stream, streamRows] of declaredStreams) {
    // Iterated as entries rather than keys-then-get: the previous form ended
    // `declared.get(stream) ?? []`, an empty-array fallback for a key that
    // came out of `declared.keys()` one line above and therefore always
    // resolves. Nothing could ever take that branch, and nothing could test it.
    const laneSpans = packGroup(streamRows);
    for (const [laneIndex, spanIds] of laneSpans.entries()) {
      tracks.push({
        id: `stream:${stream}:${laneIndex}`,
        label: laneSpans.length === 1 ? `stream ${stream}` : `stream ${stream}.${laneIndex + 1}`,
        kind: "stream",
        spanIds,
      });
    }
  }

  // Partition the remaining spans by parentId: siblings under the same
  // parent are the same logical actor and compete for lanes together;
  // spans from unrelated subtrees never share a lane just because their
  // intervals happen not to overlap. `parentId === null` spans are not
  // siblings of each other in any real sense, but they still need somewhere
  // to live, so they are kept together under one synthetic root group
  // rather than each getting its own lane.
  const groups = new Map<string, SpanRow[]>();
  for (const row of packable) {
    const key = row.parentId ?? ROOT_GROUP;
    const bucket = groups.get(key);
    if (bucket === undefined) groups.set(key, [row]);
    else bucket.push(row);
  }

  // Group order must be deterministic — a lane changing index between two
  // renders of the same trace would be a real defect (jumpy UI, unstable
  // snapshots). Order by the earliest startS in the group (rows within a
  // group are already time-sorted from `ordered`, so `[0]` is the earliest),
  // and break ties by parentId so a fixed input always produces the same
  // order even if two groups start at the exact same instant.
  const orderedGroups = [...groups.entries()].sort(([keyA, rowsA], [keyB, rowsB]) => {
    const startDiff = rowsA[0].startS - rowsB[0].startS;
    if (startDiff !== 0) return startDiff;
    return keyA.localeCompare(keyB, "en");
  });

  // Lane ids carry the group so they stay unique once there is more than one
  // group (`lane:0` in one group must not collide with `lane:0` in
  // another); the human-facing label stays a plain running number so the UI
  // never surfaces a raw span/parent id to the user.
  let laneNumber = 1;
  for (const [groupIndex, [, groupRows]] of orderedGroups.entries()) {
    const laneSpans = packGroup(groupRows);
    for (const [laneIndex, spanIds] of laneSpans.entries()) {
      tracks.push({
        id: `lane:${groupIndex}:${laneIndex}`,
        label: `lane ${laneNumber}`,
        kind: "work",
        spanIds,
      });
      laneNumber += 1;
    }
  }

  return tracks;
}
