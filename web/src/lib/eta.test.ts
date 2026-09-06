import { describe, expect, it } from "vitest";

import { estimateRemainingMs, fractionDone, MIN_SAMPLES_FOR_ETA } from "@/lib/eta";

describe("estimateRemainingMs", () => {
  it("extrapolates linearly once there are enough samples", () => {
    // 10 of 20 done in 10s -> 1s per unit -> 10s left.
    expect(estimateRemainingMs({ completed: 10, total: 20, elapsedMs: 10_000 })).toBe(10_000);
  });

  it("withholds an estimate below the sample floor", () => {
    for (let completed = 0; completed < MIN_SAMPLES_FOR_ETA; completed += 1) {
      expect(estimateRemainingMs({ completed, total: 20, elapsedMs: 5000 })).toBeNull();
    }
    expect(estimateRemainingMs({ completed: MIN_SAMPLES_FOR_ETA, total: 20, elapsedMs: 5000 })).not.toBeNull();
  });

  it("withholds an estimate when the total is unknown", () => {
    expect(estimateRemainingMs({ completed: 50, total: 0, elapsedMs: 60_000 })).toBeNull();
  });

  it("withholds an estimate once everything is done", () => {
    expect(estimateRemainingMs({ completed: 20, total: 20, elapsedMs: 10_000 })).toBeNull();
    expect(estimateRemainingMs({ completed: 21, total: 20, elapsedMs: 10_000 })).toBeNull();
  });

  it("withholds an estimate with no time on the clock", () => {
    expect(estimateRemainingMs({ completed: 5, total: 20, elapsedMs: 0 })).toBeNull();
    expect(estimateRemainingMs({ completed: 5, total: 20, elapsedMs: Number.NaN })).toBeNull();
  });
});

describe("fractionDone", () => {
  it("is null when the total is unknown", () => {
    expect(fractionDone(3, 0)).toBeNull();
  });

  it("clamps into 0..1 so a bad count cannot overflow a progress bar", () => {
    expect(fractionDone(30, 20)).toBe(1);
    expect(fractionDone(-3, 20)).toBe(0);
    expect(fractionDone(5, 20)).toBe(0.25);
  });
});
