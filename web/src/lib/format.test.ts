import { describe, expect, it } from "vitest";

import { fmtClock, fmtDur, fmtDurMs, fmtRelative } from "@/lib/format";

describe("fmtDurMs", () => {
  it("does not read milliseconds as seconds", () => {
    // The bug this formatter exists for: 496 ms once rendered as "8.3m".
    expect(fmtDurMs(496)).toBe("496ms");
    expect(fmtDur(496)).toBe("8.3m");
  });

  it("crosses into seconds above 1000ms", () => {
    expect(fmtDurMs(6604.913)).toBe("6.60s");
    expect(fmtDurMs(0.025)).toBe("0.03ms");
  });
});

describe("fmtRelative", () => {
  const now = Date.parse("2026-09-07T12:00:00Z");
  const ago = (ms: number) => new Date(now - ms).toISOString();

  it("reads recent times as relative", () => {
    expect(fmtRelative(ago(10_000), "en", now)).toBe("just now");
    expect(fmtRelative(ago(5 * 60_000), "en", now)).toBe("5m ago");
    expect(fmtRelative(ago(3 * 3600_000), "en", now)).toBe("3h ago");
    expect(fmtRelative(ago(2 * 86_400_000), "zh", now)).toBe("2 天前");
  });

  it("falls back to an absolute date past a week", () => {
    // "12 days ago" is no easier to place than the date itself.
    expect(fmtRelative(ago(12 * 86_400_000), "en", now)).toContain("2026");
  });

  it("passes an unparseable timestamp through rather than showing NaN", () => {
    expect(fmtRelative("not-a-date", "en", now)).toBe("not-a-date");
    expect(fmtRelative("", "en", now)).toBe("");
  });
});

describe("fmtClock", () => {
  it("renders a running clock", () => {
    expect(fmtClock(42_000)).toBe("0:42");
    expect(fmtClock(727_000)).toBe("12:07");
    expect(fmtClock(3_791_000)).toBe("1:03:11");
  });

  it("never renders a negative or non-finite clock", () => {
    expect(fmtClock(-5)).toBe("0:00");
    expect(fmtClock(Number.NaN)).toBe("0:00");
  });
});
