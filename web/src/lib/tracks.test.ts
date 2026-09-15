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
    // A suite that knows its own concurrency says so; we believe it.
    const tracks = assignTracks([
      row({ id: "a", parentId: "p", startS: 0, endS: 9, attrs: { "csbench.stream": 2 } }),
      row({ id: "b", parentId: "p", startS: 0, endS: 9, attrs: { "csbench.stream": 1 } }),
      row({ id: "c", parentId: "p", startS: 5, endS: 9, attrs: { "csbench.stream": 1 } }),
    ]);
    const work = tracks.filter((track) => track.id !== STAGE_TRACK_ID);
    expect(work.map((track) => track.label)).toEqual(["stream 1", "stream 2"]);
    expect(work[0].spanIds).toEqual(["b", "c"]);
  });

  it("returns no tracks for no spans", () => {
    expect(assignTracks([])).toEqual([]);
  });
});
