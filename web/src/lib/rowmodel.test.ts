import { describe, expect, it } from "vitest";

import {
  buildTree,
  childrenOverlap,
  flatten,
  packChildren,
  slowestChain,
  slowestPath,
  type TreeNode,
} from "@/lib/rowmodel";
import type { SpanRow } from "@/lib/trace";

/** A full `SpanRow` with every field a real trace would carry, minus the
 * ones each test actually varies. Not a shared fixture — a factory each test
 * calls with its own numbers, so no two tests ever look at the same object. */
function row(id: string, startS: number, endS: number, parentId: string | null = null): SpanRow {
  return {
    id,
    name: id,
    kind: "span",
    startS,
    endS,
    status: "ok",
    isError: false,
    error: null,
    attrs: {},
    parentId,
    depth: 0,
    ancestors: [],
  };
}

/** A root "p" with one child per `[startS, endS]` interval, run through the
 * real `buildTree` so `idleS` and child ordering come from production code,
 * not from a hand-assembled `TreeNode`. */
function nodeWith(intervals: Array<[number, number]>): TreeNode {
  const maxEnd = intervals.reduce((acc, [, endS]) => Math.max(acc, endS), 0);
  const parentRow = row("p", 0, maxEnd);
  const kids = intervals.map(([startS, endS], index) => row(`c${index}`, startS, endS, "p"));
  return buildTree([parentRow, ...kids])[0];
}

/** Raw materials for a parent + children, for tests that need to call
 * `buildTree` themselves (the idle-time test builds the array by hand to
 * keep the union-vs-sum arithmetic visible at the call site). */
function parent(extent: [number, number], intervals: Array<[number, number]>) {
  const [startS, endS] = extent;
  return {
    row: row("p", startS, endS),
    kids: intervals.map(([s, e], index) => row(`c${index}`, s, e, "p")),
  };
}

describe("childrenOverlap", () => {
  it("is false when every child is sequential", () => {
    // 20 sequential children must stay ordinary rows, not become 20 lanes.
    expect(childrenOverlap(nodeWith([[0, 1], [1, 2], [2, 3]]))).toBe(false);
  });

  it("is true when ANY pair intersects", () => {
    // One overlapping pair among many sequential children is enough — greedy
    // packing then puts the sequential ones on one lane and the outlier on a
    // second, which is the correct picture.
    expect(childrenOverlap(nodeWith([[0, 1], [1, 2], [1.5, 3]]))).toBe(true);
  });

  it("is false for a single child", () => {
    expect(childrenOverlap(nodeWith([[0, 1]]))).toBe(false);
  });

  it("treats touching intervals as not overlapping", () => {
    // [0,1] and [1,2] share an instant; that is sequence, not concurrency.
    expect(childrenOverlap(nodeWith([[0, 1], [1, 2]]))).toBe(false);
  });
});

describe("packChildren", () => {
  it("puts three concurrent streams on three lanes", () => {
    expect(packChildren(nodeWith([[0, 3], [0, 3], [0, 3]]))).toHaveLength(3);
  });

  it("puts nineteen sequential children and one overlapper on two lanes", () => {
    const iv: Array<[number, number]> = [];
    for (let i = 0; i < 19; i++) iv.push([i, i + 1]);
    iv.push([0.5, 1.5]);
    expect(packChildren(nodeWith(iv))).toHaveLength(2);
  });
});

describe("idle time", () => {
  it("is the parent's extent minus the union of its children", () => {
    // Union, not sum: two children overlapping [0,2] and [1,3] cover 3s of a
    // 4s parent, leaving 1s idle. Summing would claim 4s covered and 0 idle.
    const n = parent([0, 4], [[0, 2], [1, 3]]);
    expect(buildTree([n.row, ...n.kids])[0].idleS).toBeCloseTo(1);
  });

  it("is zero for a leaf, which is working rather than waiting", () => {
    // The general form is `duration - union(children)`, and for a childless
    // span that is the whole duration. Most of a trace is leaves, so the
    // unguarded arithmetic said a 900-query run was idle throughout — and
    // nothing here caught it, because the only assertion on this field was
    // about a parent. `TraceTree` first compensated for it in the view; the
    // number is fixed at the source now, so this is the assertion that keeps
    // it fixed.
    //
    // Both halves in one test on purpose: "leaf is 0" alone is satisfied by a
    // model that returns 0 for everything, which would silently delete the
    // feature this field exists for.
    const container = nodeWith([[0, 2], [3, 4]]);
    expect(container.idleS).toBe(1); // [0,4] minus the 3s its children cover
    for (const leaf of container.children) expect(leaf.idleS).toBe(0);
  });
});

describe("flatten", () => {
  it("hides the children of a collapsed node but reports the count", () => {
    const kids: Array<[number, number]> = [];
    for (let i = 0; i < 22; i++) kids.push([i, i + 1]);
    const tree = [nodeWith(kids)];

    const out = flatten(tree, new Set());
    expect(out).toHaveLength(1);
    expect(out[0].childCount).toBe(22);
    expect(out[0].expanded).toBe(false);
  });

  it("attaches lanes only when the expanded node's children overlap", () => {
    const sequentialTree = [nodeWith([[0, 1], [1, 2], [2, 3]])];
    const concurrentTree = [nodeWith([[0, 3], [0, 3], [0, 3]])];

    const seq = flatten(sequentialTree, new Set(["p"]));
    expect(seq[0].lanes).toBeNull();
    const par = flatten(concurrentTree, new Set(["p"]));
    expect(par[0].lanes).toHaveLength(3);
  });
});

describe("slowestPath", () => {
  it("walks the heaviest child at each level down to a leaf", () => {
    // On the real trace this opens run > EXECUTE > official > load, which is
    // the 6.57s the operator should see first.
    const realShapedTree = buildTree([
      row("run", 0, 10),
      row("EXECUTE", 0, 10, "run"),
      row("SEAL", 8, 9, "run"),
      row("official", 0, 6.57, "EXECUTE"),
      row("other", 7, 8, "EXECUTE"),
      row("load", 0, 6.57, "official"),
      row("small", 6, 6.2, "official"),
    ]);
    expect([...slowestPath(realShapedTree)]).toContain("official");
  });

  it("does not expand a leaf's absent children", () => {
    const leafOnly = row("leaf", 0, 1);
    expect(slowestPath(buildTree([leafOnly])).size).toBeLessThanOrEqual(1);
  });
});

describe("slowestChain", () => {
  it("is the same descent as slowestPath, in order and with the nodes", () => {
    // The header prints this as a path (`run > EXECUTE > official > load`),
    // so order is the whole reason it exists. It must also stay the SAME
    // descent the tree opens: a header naming a span the tree left closed
    // sends the reader hunting for a row that is not there.
    const tree = buildTree([
      row("run", 0, 10),
      row("EXECUTE", 0, 10, "run"),
      row("SEAL", 8, 9, "run"),
      row("official", 0, 6.57, "EXECUTE"),
      row("other", 7, 8, "EXECUTE"),
      row("load", 0, 6.57, "official"),
      row("small", 6, 6.2, "official"),
    ]);
    expect(slowestChain(tree).map((node) => node.row.id)).toEqual([
      "run",
      "EXECUTE",
      "official",
      "load",
    ]);
    // Every node on the chain is one the tree opens, so the two cannot name
    // different spans.
    const opened = slowestPath(tree);
    for (const node of slowestChain(tree)) expect(opened.has(node.row.id)).toBe(true);
  });

  it("follows the heaviest root, and is empty for no roots at all", () => {
    // A dangling parent makes extra roots. `slowestPath` unions all of them
    // because each is a real place time went; a path is one line of text, so
    // this one picks the heaviest and descends from there.
    const forest = buildTree([row("brief", 0, 1), row("long", 0, 9), row("inside", 1, 8, "long")]);
    expect(slowestChain(forest).map((node) => node.row.id)).toEqual(["long", "inside"]);
    expect(slowestChain([])).toEqual([]);
  });
});
