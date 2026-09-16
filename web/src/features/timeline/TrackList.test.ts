import { describe, expect, it } from "vitest";

import { KIND_SLOTS } from "@/charts/palette";
import { laneSpanStyle, pctOf } from "@/features/timeline/TrackList";

describe("pctOf", () => {
  it("maps a second to its percentage across [t0, t0+totalS]", () => {
    expect(pctOf(25, 10, 40)).toBeCloseTo(37.5, 6);
  });

  it("degrades to 0 instead of NaN when totalS is 0 (empty/zero-length trace)", () => {
    expect(pctOf(25, 10, 0)).toBe(0);
  });

  it("does not clamp: a point past the trace's end still returns a percentage over 100", () => {
    // Unlike secondsAtX (pixel input, must clamp to a valid position), pctOf
    // takes a trace-domain second and is used to place both spans and the
    // selection window, neither of which is guaranteed to stay inside
    // [t0, t0+totalS] (a straddling span, a not-yet-clamped selection). If
    // this clamped, an out-of-range span would silently paint at the edge
    // rather than actually off the visible strip.
    expect(pctOf(60, 10, 40)).toBeGreaterThan(100);
  });
});

describe("laneSpanStyle", () => {
  it("colours by kind when there is no error", () => {
    const style = laneSpanStyle("query", false, true);
    expect(style.backgroundColor).toBe(`var(${KIND_SLOTS.query})`);
  });

  it("gives two different kinds two different colours", () => {
    // Would not fail if both kinds silently fell back to the same slot.
    const a = laneSpanStyle("phase", false, true);
    const b = laneSpanStyle("query", false, true);
    expect(a.backgroundColor).not.toBe(b.backgroundColor);
  });

  it("an error overrides hue regardless of kind", () => {
    const query = laneSpanStyle("query", true, true);
    const tool = laneSpanStyle("tool_call", true, true);
    expect(query.backgroundColor).toBe("var(--status-critical)");
    expect(tool.backgroundColor).toBe("var(--status-critical)");
  });

  it("falls back to the span slot for an unrecognised kind", () => {
    expect(laneSpanStyle("mystery-kind", false, true).backgroundColor).toBe(
      `var(${KIND_SLOTS.span})`,
    );
  });

  it("selection state changes opacity, never hue", () => {
    const on = laneSpanStyle("query", false, true);
    const off = laneSpanStyle("query", false, false);
    expect(on.backgroundColor).toBe(off.backgroundColor);
    expect(on.opacity).toBeGreaterThan(off.opacity);
  });

  it("an unselected error stays visibly more opaque than an unselected non-error", () => {
    // Guards against a fix that dims errors down to the same faintness as
    // ordinary unselected spans, which would make "off but still an error"
    // indistinguishable from "off and never was one".
    const errorOff = laneSpanStyle("query", true, false);
    const normalOff = laneSpanStyle("query", false, false);
    expect(errorOff.opacity).toBeGreaterThan(normalOff.opacity);
  });
});
