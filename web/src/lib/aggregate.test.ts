import { describe, expect, it } from "vitest";

import { aggregate } from "@/lib/aggregate";
import type { SpanRow } from "@/lib/trace";

/** A window wider than every span below, so these cases exercise bucketing
 * and ranking without clipping. The clipping cases name their own window. */
const WIDE = { startS: -100, endS: 100 };

function row(
  id: string,
  name: string,
  startS: number,
  endS: number,
  parentId: string | null = null,
  kind = "query",
): SpanRow {
  return {
    id,
    name,
    kind,
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

describe("aggregate", () => {
  it("merges identical names regardless of when they occurred", () => {
    const buckets = aggregate(
      [row("1", "q21", 0, 2), row("2", "q1", 2, 3), row("3", "q21", 5, 8)],
      WIDE,
    );
    expect(buckets.map((b) => [b.name, b.count, b.totalS])).toEqual([
      ["q21", 2, 5],
      ["q1", 1, 1],
    ]);
  });

  it("sorts heaviest first, not alphabetically or chronologically", () => {
    const buckets = aggregate([row("1", "aaa", 0, 1), row("2", "zzz", 1, 9)], WIDE);
    expect(buckets.map((b) => b.name)).toEqual(["zzz", "aaa"]);
  });

  it("keeps spans with the same name but different kind in separate buckets", () => {
    const buckets = aggregate(
      [row("1", "q21", 0, 2, null, "query"), row("2", "q21", 0, 2, null, "lifecycle")],
      WIDE,
    );
    expect(buckets).toHaveLength(2);
    expect(buckets.map((b) => [b.kind, b.name, b.count]).sort()).toEqual([
      ["lifecycle", "q21", 1],
      ["query", "q21", 1],
    ]);
  });

  it("subtracts child time from a parent's self time", () => {
    // parent [0,10] with a child [2,6]: total 10, self 6.
    const buckets = aggregate([row("p", "parent", 0, 10), row("c", "child", 2, 6, "p")], WIDE);
    const parent = buckets.find((b) => b.name === "parent");
    expect(parent?.totalS).toBe(10);
    expect(parent?.selfS).toBe(6);
  });

  it("sums non-overlapping children's time rather than taking the largest one", () => {
    // parent [0,10] with three non-overlapping children of duration 2 each
    // (total child time 6): self = 10 - 6 = 4. A max-based accumulator would
    // instead see the single widest child (2) and report self = 10 - 2 = 8.
    const buckets = aggregate(
      [
        row("p", "parent", 0, 10),
        row("c1", "child", 0, 2, "p"),
        row("c2", "child", 4, 6, "p"),
        row("c3", "child", 7, 9, "p"),
      ],
      WIDE,
    );
    const parent = buckets.find((b) => b.name === "parent");
    expect(parent?.selfS).toBe(4);
  });

  it("does not let overlapping children drive self time negative", () => {
    const buckets = aggregate(
      [row("p", "parent", 0, 4), row("c1", "child", 0, 4, "p"), row("c2", "child", 0, 4, "p")],
      WIDE,
    );
    const parent = buckets.find((b) => b.name === "parent");
    expect(parent?.selfS).toBe(0);
  });

  it("merges unnamed spans into a single bucket rather than one each", () => {
    const anon1 = row("1", "", 0, 1);
    anon1.name = null;
    const anon2 = row("2", "", 10, 13);
    anon2.name = null;
    const buckets = aggregate([anon1, anon2], WIDE);
    expect(buckets).toHaveLength(1);
    expect(buckets[0].name).toBe("");
    expect(buckets[0].count).toBe(2);
    expect(buckets[0].totalS).toBe(4);
  });

  it("returns nothing for no spans", () => {
    expect(aggregate([], WIDE)).toEqual([]);
  });

  it("ranks a leaf above a pure container that holds more total time", () => {
    // Measured in the browser: `tpc-h.official` (total 7.48s, self 0.00s)
    // outranked `tpc-h.load` (6.43s, all of it its own). A container's total
    // is its children's time, so ranking by total answers "what is expensive"
    // with a span that spent nothing. Sorted by totalS this reads
    // container (10), child (10), leaf (3).
    const buckets = aggregate(
      [row("p", "container", 0, 10), row("c", "child", 0, 10, "p"), row("l", "leaf", 20, 23)],
      WIDE,
    );
    expect(buckets.map((b) => [b.name, b.selfS])).toEqual([
      ["child", 10],
      ["leaf", 3],
      ["container", 0],
    ]);
  });
});

describe("aggregate clipping", () => {
  it("counts only the part of a straddling span that is inside the window", () => {
    // `tpc-h.load` ran 5.41 -> 11.84 s and reported 6.43 s of self time inside
    // a 1.56 s window, because selectSpans admits a span on overlap and hands
    // it over whole. Unclipped this bucket reads 10, not 5.
    const buckets = aggregate([row("s", "straddler", 0, 10)], { startS: 5, endS: 15 });
    expect(buckets).toEqual([{ name: "straddler", kind: "query", totalS: 5, selfS: 5, count: 1 }]);
  });

  it("drops a span lying entirely outside the window instead of counting it whole", () => {
    // selectSpans would normally have filtered this row out, but aggregate is
    // the thing that owns the window now and must not depend on its caller
    // having pre-filtered: unclipped, "before" arrives as a full 2 s bucket.
    const buckets = aggregate([row("a", "before", 0, 2), row("b", "inside", 6, 8)], {
      startS: 5,
      endS: 15,
    });
    expect(buckets.map((b) => b.name)).toEqual(["inside"]);
  });

  it("clips a parent while leaving a child that is already inside untouched", () => {
    // parent [0,10] clipped to [5,15] is 5; the child [6,8] is wholly inside,
    // so self = 5 - 2 = 3. Unclipped the parent reads total 10, self 8.
    const buckets = aggregate([row("p", "parent", 0, 10), row("c", "child", 6, 8, "p")], {
      startS: 5,
      endS: 15,
    });
    expect(buckets.find((b) => b.name === "parent")).toEqual({
      name: "parent",
      kind: "query",
      totalS: 5,
      selfS: 3,
      count: 1,
    });
  });

  it("clips child time too, rather than subtracting a child's full duration", () => {
    // parent [0,10] -> 6 inside [4,15]; child [0,8] -> 4 inside it, so
    // self = 6 - 4 = 2. An implementation that clipped only the parent would
    // subtract the child's whole 8 s from 6 and hit the zero clamp, reporting
    // a parent that did nothing in a window where it did 2 s of work.
    const buckets = aggregate([row("p", "parent", 0, 10), row("c", "child", 0, 8, "p")], {
      startS: 4,
      endS: 15,
    });
    expect(buckets.find((b) => b.name === "parent")?.selfS).toBe(2);
  });

  it("returns nothing for a degenerate window, however much the spans overlap it", () => {
    // A point in time contains no time. selectSpans deliberately admits spans
    // against a zero-width window (that is the arrival state for a trace whose
    // timestamps all collapse to t0), so aggregate is the only thing standing
    // between that and a pane of full-length durations under a "0.00s
    // selected" caption.
    const rows = [row("p", "parent", 0, 10), row("c", "child", 2, 6, "p")];
    expect(aggregate(rows, { startS: 7, endS: 7 })).toEqual([]);
  });
});

describe("aggregate under a track filter", () => {
  // What a reader can see is filtered by the track checkboxes; what a span
  // spent on itself is not. Unchecking the lane holding a parent's children
  // used to drop them from the child-time map as well, so the parent's self
  // time jumped to its full duration: in the browser `tpc-h.stream1`, a pure
  // container that did essentially no work of its own, went from absent to
  // third in a ranking that exists to answer "what is expensive".
  const parent = row("p", "container", 0, 10);
  const child = row("c", "child", 2, 6, "p");

  it("subtracts children the track filter has hidden", () => {
    const filtered = aggregate([parent], WIDE, [parent, child]);
    expect(filtered.find((b) => b.name === "container")?.selfS).toBe(6);
  });

  it("gives a parent the same self time whether or not its children are shown", () => {
    const all = aggregate([parent, child], WIDE, [parent, child]);
    const filtered = aggregate([parent], WIDE, [parent, child]);
    expect(filtered.find((b) => b.name === "container")?.selfS).toBe(
      all.find((b) => b.name === "container")?.selfS,
    );
  });

  it("still keeps hidden rows out of the buckets themselves", () => {
    // Child time comes from the whole trace; the rows on screen do not. A fix
    // that simply aggregated `allRows` would put the unchecked lane's spans
    // back into the table the reader just unchecked them out of.
    const filtered = aggregate([parent], WIDE, [parent, child]);
    expect(filtered.map((b) => b.name)).toEqual(["container"]);
  });

  it("defaults the child-time source to the rows it was given", () => {
    // The two-argument form is the no-filter case, where the sets coincide.
    expect(aggregate([parent, child], WIDE)).toEqual(
      aggregate([parent, child], WIDE, [parent, child]),
    );
  });
});
