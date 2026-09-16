import { describe, expect, it } from "vitest";

import { AggregatedPane, barWidthPct, grandSelfS, sharePct } from "@/features/timeline/AggregatedPane";
import type { Bucket } from "@/lib/aggregate";
import type { SpanRow } from "@/lib/trace";
import { renderMarkup, widthPercents } from "@/test/render";

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

describe("grandSelfS", () => {
  // A parent and its child, the shape that made the browser report
  // "13.0s · 32%": totals sum to 14 because the child's 4 s is counted once as
  // its own and again inside the parent, while self times sum to 10, the work
  // actually done. The two numbers differ here precisely so a denominator
  // built from `totalS` cannot pass this.
  const nested: Bucket[] = [
    { name: "parent", kind: "query", totalS: 10, selfS: 6, count: 1 },
    { name: "child", kind: "query", totalS: 4, selfS: 4, count: 1 },
  ];

  it("sums self time, so a parent is not counted again inside its child", () => {
    expect(grandSelfS(nested)).toBe(10);
  });

  it("gives shares that partition the work rather than summing past 100%", () => {
    const grand = grandSelfS(nested);
    expect(nested.map((b) => sharePct(b.selfS, grand))).toEqual([60, 40]);
  });

  it("is 0 for no buckets, which sharePct then degenerates on", () => {
    expect(grandSelfS([])).toBe(0);
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

function span(
  id: string,
  name: string,
  startS: number,
  endS: number,
  parentId: string | null = null,
): SpanRow {
  return {
    id,
    name,
    kind: "query",
    startS,
    endS,
    status: "ok",
    isError: false,
    error: null,
    attrs: {},
    parentId,
    depth: parentId === null ? 0 : 1,
    ancestors: [],
  };
}

describe("<AggregatedPane>", () => {
  const WIDE = { startS: -100, endS: 100 };
  // A container [0,10] whose only child [0,8] does nearly all the work, plus
  // an unrelated leaf. The container's honest self time is 2s.
  const container = span("p", "container", 0, 10);
  const child = span("c", "child", 0, 8, "p");
  const leaf = span("l", "leaf", 20, 24);

  it("keeps a parent's self time when the lane holding its children is unchecked", () => {
    // The browser defect: with the children's lane unchecked, `visible` no
    // longer contains them, and the container was credited with all 10s.
    const everything = renderMarkup(
      <AggregatedPane rows={[container, child, leaf]} allRows={[container, child, leaf]} window={WIDE} />,
    );
    const childLaneOff = renderMarkup(
      <AggregatedPane rows={[container, leaf]} allRows={[container, child, leaf]} window={WIDE} />,
    );
    expect(everything).toContain("2.00s");
    expect(childLaneOff).toContain("2.00s");
    expect(childLaneOff).not.toContain("10.00s");
    // ...and the unchecked row is genuinely gone from the list, not merely
    // still being subtracted.
    expect(everything).toContain(">child<");
    expect(childLaneOff).not.toContain(">child<");
  });

  it("draws each bar in proportion to the heaviest, with the heaviest on the 60% cap", () => {
    // Self times are leaf 4s and container 2s, so the bars are 60% and 30% —
    // the only geometry a string render can see, and the thing that would
    // silently break if the bar stopped reading `barWidthPct(bucket.selfS, …)`.
    const markup = renderMarkup(
      <AggregatedPane rows={[container, leaf]} allRows={[container, child, leaf]} window={WIDE} />,
    );
    expect(widthPercents(markup)).toEqual([60, 30]);
  });

  it("says so rather than rendering an empty list when the selection holds nothing", () => {
    const markup = renderMarkup(<AggregatedPane rows={[]} allRows={[container]} window={WIDE} />);
    expect(markup).toContain("No spans in this selection.");
  });
});
