import { describe, expect, it } from "vitest";

import { aggregate } from "@/lib/aggregate";
import type { SpanRow } from "@/lib/trace";

function row(id: string, name: string, startS: number, endS: number, parentId: string | null = null): SpanRow {
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

  it("subtracts child time from a parent's self time", () => {
    // parent [0,10] with a child [2,6]: total 10, self 6.
    const buckets = aggregate([row("p", "parent", 0, 10), row("c", "child", 2, 6, "p")]);
    const parent = buckets.find((b) => b.name === "parent");
    expect(parent?.totalS).toBe(10);
    expect(parent?.selfS).toBe(6);
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

  it("names an unnamed span once rather than merging all of them", () => {
    const anon = row("1", "", 0, 1);
    anon.name = null;
    expect(aggregate([anon])[0].name).toBe("");
  });

  it("returns nothing for no spans", () => {
    expect(aggregate([])).toEqual([]);
  });
});
