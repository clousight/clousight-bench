import { describe, expect, it } from "vitest";

import { clampSelection, fullSelection, selectSpans } from "@/lib/selection";
import type { Track } from "@/lib/tracks";
import type { SpanRow } from "@/lib/trace";

function row(id: string, startS: number, endS: number): SpanRow {
  return {
    id,
    name: id,
    kind: "span",
    startS,
    endS,
    status: "ok",
    isError: false,
    error: null,
    attrs: {},
    parentId: null,
    depth: 0,
    ancestors: [],
  };
}

const rows = [row("a", 0, 2), row("b", 3, 6), row("c", 8, 10)];
const tracks: Track[] = [
  { id: "t1", label: "t1", kind: "work", spanIds: ["a", "c"] },
  { id: "t2", label: "t2", kind: "work", spanIds: ["b"] },
];

describe("fullSelection", () => {
  it("spans the whole trace and every track", () => {
    const sel = fullSelection(rows, tracks);
    expect(sel.startS).toBe(0);
    expect(sel.endS).toBe(10);
    expect([...sel.trackIds].sort()).toEqual(["t1", "t2"]);
  });

  it("collapses to a zero-length window when there are no rows, but keeps the tracks", () => {
    // With no rows, startS/endS never turn finite, so the function must fall
    // back to 0,0 rather than leaking the +/-Infinity seeds. The fallback
    // only touches the time fields — trackIds still comes from the tracks
    // argument, so a caller with real tracks but no spans gets its track set
    // back, not an empty one.
    const sel = fullSelection([], tracks);
    expect(sel.startS).toBe(0);
    expect(sel.endS).toBe(0);
    expect([...sel.trackIds].sort()).toEqual(["t1", "t2"]);
  });
});

describe("selectSpans", () => {
  it("keeps a span that overlaps the window at all", () => {
    // Overlap, not containment: a span straddling the edge is part of what
    // happened in the window, and dropping it would under-count the time.
    const sel = { startS: 1, endS: 4, trackIds: new Set(["t1", "t2"]) };
    expect(selectSpans(rows, tracks, sel).map((r) => r.id)).toEqual(["a", "b"]);
  });

  it("excludes spans on unchecked tracks", () => {
    const sel = { startS: 0, endS: 10, trackIds: new Set(["t2"]) };
    expect(selectSpans(rows, tracks, sel).map((r) => r.id)).toEqual(["b"]);
  });

  it("treats a touching edge as outside", () => {
    // b is [3,6]; a window ending exactly at 3 has zero overlap with it.
    const sel = { startS: 0, endS: 3, trackIds: new Set(["t1", "t2"]) };
    expect(selectSpans(rows, tracks, sel).map((r) => r.id)).toEqual(["a"]);
  });

  it("returns nothing when no track is selected", () => {
    const sel = { startS: 0, endS: 10, trackIds: new Set<string>() };
    expect(selectSpans(rows, tracks, sel)).toEqual([]);
  });
});

describe("clampSelection", () => {
  it("pulls both edges inside the bounds and keeps order", () => {
    const sel = clampSelection({ startS: -5, endS: 99, trackIds: new Set(["t1"]) }, 0, 10);
    expect(sel.startS).toBe(0);
    expect(sel.endS).toBe(10);
  });

  it("never lets the window invert", () => {
    const sel = clampSelection({ startS: 8, endS: 2, trackIds: new Set(["t1"]) }, 0, 10);
    expect(sel.startS).toBeLessThanOrEqual(sel.endS);
  });
});
