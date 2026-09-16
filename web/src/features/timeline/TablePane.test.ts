import { describe, expect, it } from "vitest";

import { firstIdFor, sortBuckets } from "@/features/timeline/TablePane";
import type { Bucket } from "@/lib/aggregate";
import type { SpanRow } from "@/lib/trace";

function bucket(name: string, kind: string, totalS: number, selfS: number, count: number): Bucket {
  return { name, kind, totalS, selfS, count };
}

function row(id: string, name: string | null, kind: string): SpanRow {
  return {
    id,
    name,
    kind,
    startS: 0,
    endS: 1,
    status: "ok",
    isError: false,
    error: null,
    attrs: {},
    parentId: null,
    depth: 0,
    ancestors: [],
  };
}

describe("sortBuckets", () => {
  // Each field produces a distinct permutation of these three buckets, so a
  // comparator that ignored `key` (e.g. always sorted by totalS) would fail
  // at least two of the three assertions below.
  const buckets = [
    bucket("b", "query", 1, 9, 5),
    bucket("a", "query", 5, 1, 9),
    bucket("c", "query", 9, 5, 1),
  ];

  it("sorts by totalS, heaviest first", () => {
    expect(sortBuckets(buckets, "totalS").map((b) => b.name)).toEqual(["c", "a", "b"]);
  });

  it("sorts by selfS, not totalS, when selfS is requested", () => {
    expect(sortBuckets(buckets, "selfS").map((b) => b.name)).toEqual(["b", "c", "a"]);
  });

  it("sorts by count, not totalS or selfS, when count is requested", () => {
    expect(sortBuckets(buckets, "count").map((b) => b.name)).toEqual(["a", "b", "c"]);
  });

  it("does not mutate the input array", () => {
    // The array literal above is declared in insertion order b, a, c — none
    // of totalS/selfS/count order. If sortBuckets sorted in place (e.g. via
    // Array.prototype.sort on the argument itself instead of a copy), this
    // would now read one of the three permutations asserted above instead of
    // the original insertion order.
    sortBuckets(buckets, "totalS");
    expect(buckets.map((b) => b.name)).toEqual(["b", "a", "c"]);
  });
});

describe("firstIdFor", () => {
  it("matches on the (kind, name) pair, not name alone", () => {
    const rows = [row("1", "q21", "phase"), row("2", "q21", "query")];
    // Both rows share the name "q21"; only the second has kind "query". If
    // matching ignored kind, this would return "1" instead of "2".
    expect(firstIdFor(rows, bucket("q21", "query", 1, 1, 1))).toBe("2");
  });

  it("matches a null-named row against the empty-string bucket name", () => {
    const rows = [row("1", null, "phase")];
    expect(firstIdFor(rows, bucket("", "phase", 1, 1, 1))).toBe("1");
  });

  it("returns null when no row matches the pair", () => {
    const rows = [row("1", "q21", "query")];
    expect(firstIdFor(rows, bucket("q21", "phase", 1, 1, 1))).toBeNull();
  });
});
