import { describe, expect, it } from "vitest";

import { firstIdFor, sortBuckets, TablePane } from "@/features/timeline/TablePane";
import type { Bucket } from "@/lib/aggregate";
import type { SpanRow } from "@/lib/trace";
import { renderMarkup } from "@/test/render";

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

describe("<TablePane>", () => {
  const WIDE = { startS: -100, endS: 100 };
  function timed(id: string, name: string, startS: number, endS: number, parentId: string | null = null) {
    return { ...row(id, name, "query"), startS, endS, parentId };
  }
  const container = timed("p", "container", 0, 10);
  const child = timed("c", "child", 0, 8, "p");
  const noop = () => {};

  it("lays the table out fixed and keeps the full name recoverable from the cell", () => {
    // `table-fixed` is what lets the name column truncate at all (auto layout
    // sizes the column BY the name, so the class did nothing and a long name
    // pushed total/self/count off screen behind a scrollbar). Truncation then
    // makes the `title` load-bearing: it is the only way back to the whole
    // name. Neither the truncation nor the overflow is observable without a
    // layout engine — that the two attributes are emitted is.
    const long = timed("q", "tpc-h.stream1.q21.a-very-long-operation-name", 0, 3);
    const markup = renderMarkup(
      <TablePane rows={[long]} allRows={[long]} window={WIDE} onSelect={noop} />,
    );
    expect(markup).toContain("table-fixed");
    expect(markup).toContain('title="tpc-h.stream1.q21.a-very-long-operation-name"');
  });

  it("opens on self time, so a pure container does not head the table", () => {
    // Row order is the assertion: by total the container (10s) leads, by self
    // the child (8s) does and the container falls to 2s at the bottom. The
    // initial sort key is the only thing that decides which.
    const markup = renderMarkup(
      <TablePane rows={[container, child]} allRows={[container, child]} window={WIDE} onSelect={noop} />,
    );
    expect(markup.indexOf(">child<")).toBeLessThan(markup.indexOf(">container<"));
  });

  it("keeps a parent's self time when the lane holding its children is unchecked", () => {
    // Same defect as the aggregated pane, second call site: the self column
    // must not change meaning because a checkbox changed.
    const markup = renderMarkup(
      <TablePane rows={[container]} allRows={[container, child]} window={WIDE} onSelect={noop} />,
    );
    expect(markup).toContain("2.00s");
    expect(markup).not.toContain("10.00s");
  });
});
