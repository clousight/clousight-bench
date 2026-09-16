import { describe, expect, it } from "vitest";

import { assignTracks, STAGE_TRACK_ID } from "@/lib/tracks";
import type { SpanRow } from "@/lib/trace";

function row(over: Partial<SpanRow> & Pick<SpanRow, "id">): SpanRow {
  return {
    name: over.id,
    kind: "span",
    startS: 0,
    endS: 1,
    status: "ok",
    isError: false,
    error: null,
    attrs: {},
    parentId: null,
    depth: 0,
    ancestors: [],
    ...over,
  } as SpanRow;
}

describe("assignTracks", () => {
  it("puts lifecycle spans on one reserved track", () => {
    const tracks = assignTracks([
      row({ id: "a", kind: "lifecycle", name: "csbench.stage.EXECUTE" }),
      row({ id: "b", kind: "lifecycle", name: "csbench.stage.SEAL" }),
    ]);
    expect(tracks).toHaveLength(1);
    expect(tracks[0].id).toBe(STAGE_TRACK_ID);
    expect(tracks[0].spanIds).toEqual(["a", "b"]);
  });

  it("splits concurrent siblings into separate tracks", () => {
    // Two spans with the same parent whose intervals overlap cannot share a lane.
    const tracks = assignTracks([
      row({ id: "s1", parentId: "p", startS: 0, endS: 10 }),
      row({ id: "s2", parentId: "p", startS: 1, endS: 9 }),
    ]);
    const work = tracks.filter((track) => track.id !== STAGE_TRACK_ID);
    expect(work).toHaveLength(2);
    expect(work.map((track) => track.spanIds)).toEqual([["s1"], ["s2"]]);
  });

  it("keeps non-overlapping siblings on one track", () => {
    const tracks = assignTracks([
      row({ id: "s1", parentId: "p", startS: 0, endS: 2 }),
      row({ id: "s2", parentId: "p", startS: 3, endS: 5 }),
    ]);
    const work = tracks.filter((track) => track.id !== STAGE_TRACK_ID);
    expect(work).toHaveLength(1);
    expect(work[0].spanIds).toEqual(["s1", "s2"]);
  });

  it("keeps different parents on separate lanes even when their intervals do not overlap", () => {
    // Two siblings of "p" would share one lane per the interval-packing rule;
    // two spans from unrelated parents must not, even though nothing here
    // overlaps in time. A lane is scoped to one logical actor.
    const tracks = assignTracks([
      row({ id: "s1", parentId: "p1", startS: 0, endS: 2 }),
      row({ id: "s2", parentId: "p2", startS: 3, endS: 5 }),
    ]);
    const work = tracks.filter((track) => track.id !== STAGE_TRACK_ID);
    expect(work).toHaveLength(2);
    expect(work.map((track) => track.spanIds)).toEqual([["s1"], ["s2"]]);
  });

  it("prefers an explicit stream attribute over interval packing", () => {
    // A suite that knows its own concurrency says so; we believe it. The
    // attribute key is `csbench.stream_id` because that is what the emitter
    // writes (`_tpc_official/trace.py`); with the key this test used to
    // invent, every row here falls through to parentId packing and comes back
    // as "lane 1"/"lane 2" holding [a, c] and [b] — a and b overlap, c does
    // not — so the two groupings are distinguishable by both label and
    // membership, not just by count.
    const tracks = assignTracks([
      row({ id: "a", parentId: "p", startS: 0, endS: 9, attrs: { "csbench.stream_id": 2 } }),
      row({ id: "b", parentId: "p", startS: 0, endS: 9, attrs: { "csbench.stream_id": 1 } }),
      row({ id: "c", parentId: "p", startS: 9, endS: 12, attrs: { "csbench.stream_id": 1 } }),
    ]);
    const work = tracks.filter((track) => track.id !== STAGE_TRACK_ID);
    expect(work.map((track) => track.label)).toEqual(["stream 1", "stream 2"]);
    expect(work.map((track) => track.spanIds)).toEqual([["b", "c"], ["a"]]);
  });

  it("packs a stream's container apart from the queries it contains", () => {
    // A TPC-H throughput stream is one `tpc-h.streamN` span covering 22
    // queries. Dropped into one lane unpacked — which is what the declared
    // path used to do — the container draws on top of every query in it at a
    // single lane's height, so this must come back as two lanes.
    //
    // All three rows share a parentId deliberately: with distinct parents the
    // fallback path would split them the same way for an unrelated reason, and
    // this test would pass under a broken stream key. The labels are asserted
    // for the same reason — they are what says which path produced the split.
    const tracks = assignTracks([
      row({ id: "container", parentId: "block", startS: 0, endS: 9, attrs: { "csbench.stream_id": 1 } }),
      row({ id: "q1", parentId: "block", startS: 0, endS: 4, attrs: { "csbench.stream_id": 1 } }),
      row({ id: "q2", parentId: "block", startS: 4, endS: 9, attrs: { "csbench.stream_id": 1 } }),
    ]);
    const work = tracks.filter((track) => track.id !== STAGE_TRACK_ID);
    expect(work.map((track) => [track.label, track.spanIds])).toEqual([
      ["stream 1.1", ["container"]],
      ["stream 1.2", ["q1", "q2"]],
    ]);
  });

  it("keeps the stream's identity in every lane it needs, not an anonymous number", () => {
    // Two streams that each need two lanes. The point of the whole declared
    // path is that "did the streams stagger or collide?" is answerable, which
    // it is not if stream 2's second lane is called "lane 4".
    const overlapping = (stream: number, id: string, startS: number, endS: number) =>
      row({ id, startS, endS, attrs: { "csbench.stream_id": stream } });
    const tracks = assignTracks([
      overlapping(1, "a1", 0, 9),
      overlapping(1, "a2", 1, 8),
      overlapping(2, "b1", 0, 9),
      overlapping(2, "b2", 1, 8),
    ]);
    const work = tracks.filter((track) => track.id !== STAGE_TRACK_ID);
    expect(work.map((track) => track.label)).toEqual([
      "stream 1.1",
      "stream 1.2",
      "stream 2.1",
      "stream 2.2",
    ]);
    expect(work.map((track) => track.id)).toEqual([
      "stream:1:0",
      "stream:1:1",
      "stream:2:0",
      "stream:2:1",
    ]);
  });

  it("returns no tracks for no spans", () => {
    expect(assignTracks([])).toEqual([]);
  });
});
