import { describe, expect, it } from "vitest";

import { AggregatedPane, barWidthPct, fmtShare, grandSelfS } from "@/features/timeline/AggregatedPane";
import type { Bucket } from "@/lib/aggregate";
import type { SpanRow } from "@/lib/trace";
import { renderMarkup, widthPercents } from "@/test/render";

describe("barWidthPct", () => {
  it("fills the bar's track for the heaviest bucket and scales the rest to it", () => {
    // The heaviest bucket is its own denominator, so it always lands on the
    // maximum — which is why the old "<= 60" assertion could not tell a cap
    // from a scale factor and the docstring claiming a cap went unchallenged.
    // Stated as the full width of the track instead: the cap is the track's
    // own `w-24 shrink-0`, not arithmetic in this function.
    expect(barWidthPct(10, 10)).toBe(100);
    expect(barWidthPct(5, 10)).toBe(50);
    expect(barWidthPct(1, 8)).toBe(12.5);
  });

  it("degrades to 0 instead of dividing by zero when there is no heaviest bucket", () => {
    // heaviestS <= 0 is the guard on the FIRST argument's denominator, not the
    // numerator: totalS is nonzero here, so a guard that checked totalS
    // instead of heaviestS would let this fall through to a divide-by-zero
    // (Infinity) instead of 0. Reachable, too: `aggregate` ranks by self time
    // and a window can hold nothing but pure containers, whose self time is
    // legitimately zero.
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
    expect(nested.map((b) => fmtShare(b.selfS, grand))).toEqual(["60%", "40%"]);
  });

  it("is 0 for no buckets, which sharePct then degenerates on", () => {
    expect(grandSelfS([])).toBe(0);
  });
});

describe("fmtShare", () => {
  it("rounds rather than floors", () => {
    // 2/3 is 66.67%. The previous test used 1/3 — round(33.33) and
    // floor(33.33) are both 33, so it could not tell the two apart and would
    // have stayed green through the change it existed to catch. 2/3 is the
    // nearest input that can: 67 vs 66.
    expect(fmtShare(2, 3)).toBe("67%");
    expect(fmtShare(25, 100)).toBe("25%");
  });

  it("says <1% rather than 0% for a bucket that did measurable work", () => {
    // A wide window holds hundreds of buckets, most of them under half a
    // percent. Rounding them all to "0%" is a row that measured something
    // claiming it measured nothing.
    expect(fmtShare(1, 1000)).toBe("<1%");
    expect(fmtShare(4.9, 1000)).toBe("<1%");
    // The boundary belongs to the whole percent: 1% is a number, not a tail.
    expect(fmtShare(10, 1000)).toBe("1%");
  });

  it("keeps 0% for a bucket that genuinely did no work of its own", () => {
    // A pure container's self time really is zero, and "<1%" would imply it
    // had done something too small to name.
    expect(fmtShare(0, 10)).toBe("0%");
  });

  it("degrades to 0% instead of dividing by zero when the grand total is 0", () => {
    // grandS <= 0 guards the denominator; totalS is nonzero here, so a guard
    // on the wrong argument would let this compute Infinity/NaN instead.
    expect(fmtShare(5, 0)).toBe("0%");
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

  it("draws each bar in proportion to the heaviest, which fills its track", () => {
    // Self times are leaf 4s and container 2s, so the bars are 100% and 50% of
    // their own tracks — the only geometry a string render can see, and the
    // thing that would silently break if the bar stopped reading
    // `barWidthPct(bucket.selfS, …)`. The tracks themselves carry a Tailwind
    // width class, not an inline one, so they do not appear here: these two
    // numbers are the bars.
    const markup = renderMarkup(
      <AggregatedPane rows={[container, leaf]} allRows={[container, child, leaf]} window={WIDE} />,
    );
    expect(widthPercents(markup)).toEqual([100, 50]);
  });

  it("keeps a sub-second bucket readable instead of rounding it to 0.00s", () => {
    // The feature's own premise: drag out a narrow window and every row read
    // "0.00s self · 0.00s total · 0% of work". Both the durations and the
    // share have to survive down there.
    const fast = span("f", "fast", 0, 0.004);
    const slow = span("s", "slow", 0, 4);
    const markup = renderMarkup(
      <AggregatedPane rows={[fast, slow]} allRows={[fast, slow]} window={WIDE} />,
    );
    expect(markup).toContain("4.00ms");
    // Escaped by the renderer, which is what a browser would receive.
    expect(markup).toContain("&lt;1%");
    expect(markup).not.toContain("0.00s");
    // ...and a value that genuinely belongs in seconds still reads in seconds.
    expect(markup).toContain("4.00s");
  });

  it("says so rather than rendering an empty list when the selection holds nothing", () => {
    const markup = renderMarkup(<AggregatedPane rows={[]} allRows={[container]} window={WIDE} />);
    expect(markup).toContain("No spans in this selection.");
  });
});
