import { describe, expect, it } from "vitest";

import { barWidthPct, sharePct } from "@/features/timeline/AggregatedPane";

describe("barWidthPct", () => {
  it("scales proportionally to the heaviest bucket, capped at 60%", () => {
    // The heaviest bucket itself must land exactly on the 60% cap.
    expect(barWidthPct(10, 10)).toBe(60);
    expect(barWidthPct(5, 10)).toBe(30);
  });

  it("degrades to 0 instead of dividing by zero when there is no heaviest bucket", () => {
    // heaviestS <= 0 is the guard on the FIRST argument's denominator, not the
    // numerator: totalS is nonzero here, so a guard that checked totalS
    // instead of heaviestS would let this fall through to a divide-by-zero
    // (Infinity * 60) instead of 0.
    expect(barWidthPct(5, 0)).toBe(0);
  });
});

describe("sharePct", () => {
  it("rounds a bucket's share of the grand total to a whole percent", () => {
    expect(sharePct(25, 100)).toBe(25);
    expect(sharePct(1, 3)).toBe(33);
  });

  it("degrades to 0 instead of dividing by zero when the grand total is 0", () => {
    // grandS <= 0 guards the denominator; totalS is nonzero here, so a guard
    // on the wrong argument would let this compute Infinity/NaN instead.
    expect(sharePct(5, 0)).toBe(0);
  });
});
