import { describe, expect, it } from "vitest";

import {
  back,
  commitHistory,
  fullViewport,
  nudge,
  place,
  scale,
  spanS,
  zoomStackPop,
  zoomStackPush,
  zoomTo,
  MIN_SPAN_S,
} from "@/lib/viewport";
import type { SpanRow } from "@/lib/trace";

function row(startS: number, endS: number): SpanRow {
  return {
    id: `${startS}-${endS}`, name: "s", kind: "query", startS, endS,
    status: "ok", isError: false, error: null, attrs: {}, parentId: null,
    depth: 0, ancestors: [],
  };
}

const V = { startS: 10, endS: 20 };

describe("place", () => {
  it("maps a fully contained span to its fraction of the WINDOW, not the run", () => {
    // The whole point: at window [10,20], a span [12,14] is 20%..40% wide,
    // regardless of how long the run is. pctOf(totalS) would give something else.
    expect(place(V, 12, 14)).toEqual({
      leftPct: 20, widthPct: 20, clippedStart: false, clippedEnd: false, visible: true,
    });
  });

  it("clips a span that starts before the window and says so", () => {
    const p = place(V, 5, 15);
    expect(p.leftPct).toBe(0);
    expect(p.widthPct).toBe(50);
    expect(p.clippedStart).toBe(true);
    expect(p.clippedEnd).toBe(false);
  });

  it("clips a span that outlives the window and says so", () => {
    const p = place(V, 15, 40);
    expect(p.leftPct).toBe(50);
    expect(p.widthPct).toBe(50);
    expect(p.clippedEnd).toBe(true);
  });

  it("marks the root case — spanning the whole window — clipped at BOTH ends", () => {
    // This is why filter-by-overlap could never zoom: the root always overlaps.
    // Drawn honestly it fills the width and shows two cut markers.
    const p = place(V, 0, 100);
    expect(p).toEqual({
      leftPct: 0, widthPct: 100, clippedStart: true, clippedEnd: true, visible: true,
    });
  });

  it("reports a span outside the window as not visible", () => {
    expect(place(V, 1, 2).visible).toBe(false);
    expect(place(V, 30, 31).visible).toBe(false);
  });

  it("gives a zero-duration span inside the window a hairline, not zero width", () => {
    // A degenerate span must still be clickable; trace.ts explicitly allows them.
    const p = place(V, 15, 15);
    expect(p.visible).toBe(true);
    expect(p.widthPct).toBeGreaterThan(0);
  });
});

describe("zoomTo", () => {
  it("orders a backwards drag", () => {
    expect(zoomTo(V, 18, 12)).toEqual({ startS: 12, endS: 18 });
  });

  it("refuses to collapse below MIN_SPAN_S", () => {
    const z = zoomTo(V, 15, 15);
    expect(spanS(z)).toBeGreaterThanOrEqual(MIN_SPAN_S);
  });
});

describe("pan and zoom stay inside bounds", () => {
  const bounds = { startS: 0, endS: 100 };

  it("panning right stops at the end of the run", () => {
    const v = nudge({ startS: 90, endS: 100 }, 1, 0.5, bounds);
    expect(v.endS).toBe(100);
    expect(spanS(v)).toBeCloseTo(10);
  });

  it("panning preserves the window width", () => {
    const v = nudge({ startS: 40, endS: 50 }, -1, 0.5, bounds);
    expect(spanS(v)).toBeCloseTo(10);
  });

  it("zooming out clamps to the run and cannot exceed it", () => {
    const v = scale({ startS: 40, endS: 50 }, 100, bounds);
    expect(v).toEqual(bounds);
  });

  it("zooming in narrows about the centre", () => {
    const v = scale({ startS: 40, endS: 50 }, 0.5, bounds);
    expect(spanS(v)).toBeCloseTo(5);
    expect((v.startS + v.endS) / 2).toBeCloseTo(45);
  });
});

describe("fullViewport", () => {
  it("spans min start to max end", () => {
    expect(fullViewport([row(3, 9), row(1, 4)], 0)).toEqual({ startS: 1, endS: 9 });
  });

  it("falls back to a non-degenerate window when every timestamp collapses", () => {
    // trace.ts degrades garbage timestamps to t0; a zero-width viewport would
    // divide by zero in toPct and blank the entire view.
    const v = fullViewport([row(5, 5), row(5, 5)], 5);
    expect(spanS(v)).toBeGreaterThanOrEqual(MIN_SPAN_S);
  });

  it("falls back to a non-degenerate window on no rows at all", () => {
    expect(spanS(fullViewport([], 0))).toBeGreaterThanOrEqual(MIN_SPAN_S);
  });
});

describe("zoom stack", () => {
  it("pops back to the previous window", () => {
    const a = { startS: 0, endS: 100 };
    const b = { startS: 10, endS: 20 };
    const { view, stack } = zoomStackPop(zoomStackPush([a], b));
    expect(view).toEqual(a);
    expect(stack).toHaveLength(1);
  });

  it("popping the last entry yields null rather than an empty window", () => {
    expect(zoomStackPop([{ startS: 0, endS: 1 }]).view).toBeNull();
  });

  it("does not push a duplicate of the current top", () => {
    const a = { startS: 0, endS: 100 };
    expect(zoomStackPush([a], { ...a })).toHaveLength(1);
  });
});

describe("zoom history", () => {
  // The two operations the strip actually performs on the stack, as opposed
  // to the two primitives above. They were a pair of nested `zoomStackPush`
  // calls inside a `useRef` closure, where "commit pushes nothing" was a
  // mutation the entire suite passed.
  const whole = { startS: 0, endS: 100 };
  const phase = { startS: 90, endS: 96 };
  const closer = { startS: 92, endS: 94 };

  it("records the window being left before the one being entered", () => {
    // Two entries, not one: `back` needs somewhere to go after the very first
    // zoom, and the window being left is the only candidate.
    expect(commitHistory([whole], whole, phase)).toEqual([whole, phase]);
  });

  it("does not grow when the same window is committed twice", () => {
    expect(commitHistory([whole, phase], phase, phase)).toEqual([whole, phase]);
  });

  it("records a window that was set from outside rather than leaving a hole", () => {
    // The reset button, a breadcrumb — anything that moves the view without
    // going through the strip. `from` is then a window the stack has never
    // seen, and dropping it would make `back` skip a level.
    expect(commitHistory([whole], closer, phase)).toEqual([whole, closer, phase]);
  });

  it("steps back to the window before the current one", () => {
    expect(back([whole, phase, closer], closer)).toEqual({
      stack: [whole, phase],
      view: phase,
    });
  });

  it("steps back from a window the stack has never seen to the top of the stack", () => {
    // Same self-healing property from the other side: `back` pressed right
    // after an outside change must land on the last window the strip
    // committed, not on the one before it.
    expect(back([whole, phase], closer)).toEqual({ stack: [whole, phase], view: phase });
  });

  it("has nowhere to go from the first window, and says so", () => {
    expect(back([whole], whole)).toEqual({ stack: [whole], view: null });
  });
});
