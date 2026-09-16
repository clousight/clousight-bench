import { describe, expect, it } from "vitest";

import { clampSelection, fullSelection, sameRows, selectSpans } from "@/lib/selection";
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

describe("selectSpans over fullSelection", () => {
  /**
   * The arrival state: `selection` is null, so every pane renders through
   * `fullSelection`. That window is the min start and max end over the rows,
   * so by construction nothing lies outside it — and if `selectSpans` drops a
   * row anyway, the reader lands on an empty pane with no narrower window to
   * widen back out of. Stated as a property over several shapes rather than
   * one example, because the shape that broke it (every timestamp collapsed
   * onto t0, which `trace.ts` explicitly supports) is not the shape anybody
   * writes an example for.
   */
  const shapes: Record<string, SpanRow[]> = {
    "a normal trace": rows,
    "a single span": [row("only", 4, 9)],
    "a single degenerate span": [row("only", 4, 4)],
    "an all-degenerate trace": [row("a", 7, 7), row("b", 7, 7), row("c", 7, 7)],
    "degenerate spans on both edges of a real window": [
      row("a", 0, 0),
      row("b", 0, 5),
      row("c", 5, 5),
    ],
    "a trace of zero-length work at the origin": [row("a", 0, 0), row("b", 0, 0)],
    "an empty trace": [],
  };

  for (const [name, shape] of Object.entries(shapes)) {
    it(`is the identity over ${name}`, () => {
      const oneTrack: Track[] = [
        { id: "all", label: "all", kind: "work", spanIds: shape.map((r) => r.id) },
      ];
      const sel = fullSelection(shape, oneTrack);
      expect(selectSpans(shape, oneTrack, sel).map((r) => r.id)).toEqual(shape.map((r) => r.id));
    });
  }
});

describe("selectSpans and zero width", () => {
  it("keeps a degenerate span sitting exactly on a window edge", () => {
    // A point has no width to overlap *with*, so a strict comparison rejects
    // it against every window there is — including one drawn around it.
    const point = [row("p", 3, 3)];
    const oneTrack: Track[] = [{ id: "all", label: "all", kind: "work", spanIds: ["p"] }];
    const atStart = { startS: 3, endS: 9, trackIds: new Set(["all"]) };
    const atEnd = { startS: 0, endS: 3, trackIds: new Set(["all"]) };
    expect(selectSpans(point, oneTrack, atStart).map((r) => r.id)).toEqual(["p"]);
    expect(selectSpans(point, oneTrack, atEnd).map((r) => r.id)).toEqual(["p"]);
  });

  it("still drops a real span touching a real window's edge", () => {
    // The degenerate allowance must not leak into the case with real widths:
    // dragging a window up to b's start must not pull b in.
    const mixed = [row("point", 3, 3), row("b", 3, 6)];
    const oneTrack: Track[] = [{ id: "all", label: "all", kind: "work", spanIds: ["point", "b"] }];
    const sel = { startS: 0, endS: 3, trackIds: new Set(["all"]) };
    expect(selectSpans(mixed, oneTrack, sel).map((r) => r.id)).toEqual(["point"]);
  });

  it("a zero-width window still reports what was running across it", () => {
    // A drag clamped to a single instant is a playhead, not an empty pane.
    const sel = { startS: 4, endS: 4, trackIds: new Set(["t1", "t2"]) };
    expect(selectSpans(rows, tracks, sel).map((r) => r.id)).toEqual(["b"]);
  });
});

describe("sameRows", () => {
  it("sees two fresh results of the same filter as the same rows", () => {
    // The case that matters: two adjacent pointermoves during a drag. The
    // windows differ, the arrays are different objects, and the rows inside
    // are identical — so the caller can keep the first array and leave
    // Waterfall's ECharts instance alone.
    const wide = { startS: 0, endS: 10, trackIds: new Set(["t1", "t2"]) };
    const narrower = { startS: 0.1, endS: 9.9, trackIds: new Set(["t1", "t2"]) };
    const first = selectSpans(rows, tracks, wide);
    const second = selectSpans(rows, tracks, narrower);
    expect(second).not.toBe(first);
    expect(sameRows(first, second)).toBe(true);
  });

  it("sees a crossed edge as a change", () => {
    const before = { startS: 0, endS: 10, trackIds: new Set(["t1", "t2"]) };
    const after = { startS: 0, endS: 7, trackIds: new Set(["t1", "t2"]) };
    expect(sameRows(selectSpans(rows, tracks, before), selectSpans(rows, tracks, after))).toBe(false);
  });

  it("sees an unchecked track as a change even when the window did not move", () => {
    const both = { startS: 0, endS: 10, trackIds: new Set(["t1", "t2"]) };
    const one = { startS: 0, endS: 10, trackIds: new Set(["t1"]) };
    expect(sameRows(selectSpans(rows, tracks, both), selectSpans(rows, tracks, one))).toBe(false);
  });

  it("compares by element identity and order, not by length or content", () => {
    // Same rows, same length, different order: the waterfall draws in the
    // order it is handed, so this is a different list.
    expect(sameRows([rows[0], rows[1]], [rows[1], rows[0]])).toBe(false);
    // A copy of a row is a different object, and a consumer keying on
    // identity would not see the substitution if this returned true.
    expect(sameRows([rows[0]], [{ ...rows[0] }])).toBe(false);
  });
});
