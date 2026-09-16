import { describe, expect, it } from "vitest";

import { CLICK_SLOP_PX, resolveDrag, secondsAtX } from "@/features/timeline/OverviewStrip";

describe("resolveDrag", () => {
  it("resolves a click with a pixel of tremor to the whole run, discarding the narrow window the tremor built", () => {
    // 1px of press-to-release travel is well inside CLICK_SLOP_PX, but the
    // seconds the tremor produced (12..45) are a real, non-degenerate range —
    // if the fix regressed to strict from!==to equality, this would return
    // null (keep the 12..45 sliver) instead of overriding it.
    const result = resolveDrag(100, 101, 12, 45, 0, 120);
    expect(result).toEqual({ startS: 0, endS: 120 });
  });

  it("leaves a real drag alone: press-to-release travel past the slop returns null", () => {
    // 160px of travel is far past CLICK_SLOP_PX, and from/to are a real,
    // distinct range. The continuous pointermove updates already committed
    // this window, so resolveDrag must not override it on release.
    const result = resolveDrag(100, 260, 10, 90, 0, 120);
    expect(result).toBeNull();
  });

  it("still resolves to a sane single point when totalS is 0, regardless of pixel travel", () => {
    // On a zero-duration trace secondsAtX always returns t0, so from === to
    // even though the press-to-release travel here (150px) would read as a
    // real drag by the pixel test alone. The degenerate-seconds branch must
    // catch this independently of the click/drag pixel distinction.
    const result = resolveDrag(50, 200, 5, 5, 5, 0);
    expect(result).toEqual({ startS: 5, endS: 5 });
  });

  it("treats exactly CLICK_SLOP_PX of travel as a click (the boundary is inclusive)", () => {
    const result = resolveDrag(100, 100 + CLICK_SLOP_PX, 12, 45, 0, 120);
    expect(result).toEqual({ startS: 0, endS: 120 });
  });
});

describe("secondsAtX", () => {
  it("maps a clientX to the proportional second within the strip", () => {
    // rect [100, 300), width 200; clientX 150 is 25% across.
    expect(secondsAtX(150, { left: 100, width: 200 }, 10, 40)).toBeCloseTo(20, 6);
  });

  it("clamps to t0/totalS bounds rather than extrapolating past the strip", () => {
    expect(secondsAtX(0, { left: 100, width: 200 }, 10, 40)).toBe(10);
    expect(secondsAtX(9999, { left: 100, width: 200 }, 10, 40)).toBe(50);
  });

  it("degrades to t0 instead of NaN when the strip has zero width (not yet laid out)", () => {
    expect(secondsAtX(150, { left: 100, width: 0 }, 10, 40)).toBe(10);
  });

  it("degrades to t0 instead of NaN when totalS is 0 (empty trace)", () => {
    expect(secondsAtX(150, { left: 100, width: 200 }, 10, 0)).toBe(10);
  });
});
