import { describe, expect, it } from "vitest";

import { aggregate } from "@/lib/aggregate";
import type { SpanRow } from "@/lib/trace";

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
    const buckets = aggregate([
      row("1", "q21", 0, 2),
      row("2", "q1", 2, 3),
      row("3", "q21", 5, 8),
    ]);
    expect(buckets.map((b) => [b.name, b.count, b.totalS])).toEqual([
      ["q21", 2, 5],
      ["q1", 1, 1],
    ]);
  });

  it("sorts heaviest first, not alphabetically or chronologically", () => {
    const buckets = aggregate([row("1", "aaa", 0, 1), row("2", "zzz", 1, 9)]);
    expect(buckets.map((b) => b.name)).toEqual(["zzz", "aaa"]);
  });

  it("keeps spans with the same name but different kind in separate buckets", () => {
    const buckets = aggregate([
      row("1", "q21", 0, 2, null, "query"),
      row("2", "q21", 0, 2, null, "lifecycle"),
    ]);
    expect(buckets).toHaveLength(2);
    expect(buckets.map((b) => [b.kind, b.name, b.count]).sort()).toEqual([
      ["lifecycle", "q21", 1],
      ["query", "q21", 1],
    ]);
  });

  it("subtracts child time from a parent's self time", () => {
    // parent [0,10] with a child [2,6]: total 10, self 6.
    const buckets = aggregate([row("p", "parent", 0, 10), row("c", "child", 2, 6, "p")]);
    const parent = buckets.find((b) => b.name === "parent");
    expect(parent?.totalS).toBe(10);
    expect(parent?.selfS).toBe(6);
  });

  it("sums non-overlapping children's time rather than taking the largest one", () => {
    // parent [0,10] with three non-overlapping children of duration 2 each
    // (total child time 6): self = 10 - 6 = 4. A max-based accumulator would
    // instead see the single widest child (2) and report self = 10 - 2 = 8.
    const buckets = aggregate([
      row("p", "parent", 0, 10),
      row("c1", "child", 0, 2, "p"),
      row("c2", "child", 4, 6, "p"),
      row("c3", "child", 7, 9, "p"),
    ]);
    const parent = buckets.find((b) => b.name === "parent");
    expect(parent?.selfS).toBe(4);
  });

  it("does not let overlapping children drive self time negative", () => {
    const buckets = aggregate([
      row("p", "parent", 0, 4),
      row("c1", "child", 0, 4, "p"),
      row("c2", "child", 0, 4, "p"),
    ]);
    const parent = buckets.find((b) => b.name === "parent");
    expect(parent?.selfS).toBe(0);
  });

  it("merges unnamed spans into a single bucket rather than one each", () => {
    const anon1 = row("1", "", 0, 1);
    anon1.name = null;
    const anon2 = row("2", "", 10, 13);
    anon2.name = null;
    const buckets = aggregate([anon1, anon2]);
    expect(buckets).toHaveLength(1);
    expect(buckets[0].name).toBe("");
    expect(buckets[0].count).toBe(2);
    expect(buckets[0].totalS).toBe(4);
  });

  it("returns nothing for no spans", () => {
    expect(aggregate([])).toEqual([]);
  });
});
