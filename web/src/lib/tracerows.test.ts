import { describe, expect, it } from "vitest";

import { buildTree } from "@/lib/rowmodel";
import type { SpanRow } from "@/lib/trace";
import { traceRows } from "@/lib/tracerows";

/** A full `SpanRow`, built per call so no two tests share an object. */
function row(
  id: string,
  startS: number,
  endS: number,
  parentId: string | null = null,
  extra: Partial<SpanRow> = {},
): SpanRow {
  return {
    id,
    name: id,
    kind: "query",
    startS,
    endS,
    status: "ok",
    isError: false,
    error: null,
    attrs: {},
    parentId,
    depth: 0,
    ancestors: [],
    ...extra,
  };
}

/**
 * The reference trace's shape at its real proportions, with one deep query so
 * the ancestor walk has something to climb: a 12.673s run whose EXECUTE stage
 * holds the official phase machine, whose heaviest phase is the 6.34s load,
 * and whose throughput phase runs three concurrent streams.
 */
const ROWS = [
  row("csbench.run", 0, 12.673, null, { kind: "lifecycle" }),
  row("csbench.stage.PREFLIGHT", 0, 0.042, "csbench.run", { kind: "lifecycle" }),
  row("csbench.stage.EXECUTE", 0.1, 7.65, "csbench.run", { kind: "lifecycle" }),
  row("tpc-h.official", 0.12, 7.487, "csbench.stage.EXECUTE", { kind: "phase" }),
  row("tpc-h.load", 0.13, 6.47, "tpc-h.official", { kind: "phase" }),
  row("tpc-h.power", 6.47, 6.835, "tpc-h.official", { kind: "phase" }),
  row("tpc-h.throughput", 6.825, 7.487, "tpc-h.official", { kind: "phase" }),
  row("tpc-h.stream1", 6.83, 7.47, "tpc-h.throughput", { kind: "phase" }),
  row("tpc-h.s1.q13", 6.9, 6.95, "tpc-h.stream1"),
  row("tpc-h.s1.q22", 6.96, 7.0, "tpc-h.stream1"),
  row("tpc-h.stream2", 6.84, 7.48, "tpc-h.throughput", { kind: "phase" }),
  row("tpc-h.s2.q13", 6.99, 7.05, "tpc-h.stream2"),
];

const TREE = buildTree(ROWS);

/** The ids of the rows a call put on screen, in order. */
function ids(result: { visible: Array<{ row: SpanRow }> }): string[] {
  return result.visible.map((vrow) => vrow.row.id);
}

describe("traceRows", () => {
  it("opens the slowest path on arrival, and only that path", () => {
    // The page should open on where the wall clock went, not on a closed
    // root — and not on everything, which is the other way to make the
    // conclusion unreadable.
    const arrival = traceRows({ tree: TREE, rows: ROWS, opened: null, query: "" });

    expect([...arrival.expanded].sort()).toEqual([
      "csbench.run",
      "csbench.stage.EXECUTE",
      "tpc-h.load",
      "tpc-h.official",
    ]);
    // load is the heaviest child of official, so the descent ends there (it
    // is a leaf here, so being "open" shows nothing extra). throughput is on
    // screen but CLOSED, so its streams are not.
    expect(ids(arrival)).toContain("tpc-h.load");
    expect(ids(arrival)).toContain("tpc-h.throughput");
    expect(ids(arrival)).not.toContain("tpc-h.stream1");
    expect(arrival.matches).toBeNull();
  });

  it("stops deriving arrival the moment the reader opens something", () => {
    // `opened` is null until a chevron is touched, which is what keeps the
    // arrival state a fact about the trace rather than a copy of one.
    const chosen = traceRows({
      tree: TREE,
      rows: ROWS,
      opened: new Set(["csbench.run"]),
      query: "",
    });
    expect([...chosen.expanded]).toEqual(["csbench.run"]);
    expect(ids(chosen)).not.toContain("tpc-h.official");
  });

  it("force-opens the ancestors of a match, so the hit is reachable", () => {
    // THE PROPERTY THIS EXISTS FOR. `q13` is four levels below anything the
    // arrival state opens; a filter that only hid non-matching rows would
    // report "2 matching spans" over a tree in which neither is on screen.
    const found = traceRows({ tree: TREE, rows: ROWS, opened: null, query: "q13" });

    expect(found.matches).toBe(2);
    // Every ancestor of both hits is open — including throughput and the two
    // streams, which arrival left closed.
    for (const id of [
      "csbench.run",
      "csbench.stage.EXECUTE",
      "tpc-h.official",
      "tpc-h.throughput",
      "tpc-h.stream1",
      "tpc-h.stream2",
    ]) {
      expect(found.expanded.has(id), id).toBe(true);
    }
    // And the hits really are drawn, not merely expanded-toward.
    expect(ids(found)).toContain("tpc-h.s1.q13");
    expect(ids(found)).toContain("tpc-h.s2.q13");
  });

  it("keeps the path to a hit and drops everything beside it", () => {
    // The other half: an ancestor survives because it is a path, a sibling
    // does not survive at all. PREFLIGHT and load are neither hits nor
    // ancestors of one.
    const found = traceRows({ tree: TREE, rows: ROWS, opened: null, query: "q13" });
    const visible = ids(found);

    expect(visible).not.toContain("csbench.stage.PREFLIGHT");
    expect(visible).not.toContain("tpc-h.load");
    expect(visible).not.toContain("tpc-h.power");
    // q22 is a sibling of a hit inside a matching stream — still not a hit.
    expect(visible).not.toContain("tpc-h.s1.q22");
    // Nothing on screen that is neither a hit nor on the path to one.
    const allowed = new Set([
      "csbench.run",
      "csbench.stage.EXECUTE",
      "tpc-h.official",
      "tpc-h.throughput",
      "tpc-h.stream1",
      "tpc-h.stream2",
      "tpc-h.s1.q13",
      "tpc-h.s2.q13",
    ]);
    for (const id of visible) expect(allowed.has(id), id).toBe(true);
  });

  it("filters the lane marks too, not only the rows", () => {
    // A lane is drawn from the TREE, not from the row list, so a filter that
    // stopped at rows would answer "q13" with a row list of two and a lane
    // still carrying every concurrent sibling as a mark.
    const throughput = (query: string) =>
      traceRows({ tree: TREE, rows: ROWS, opened: null, query }).visible.find(
        (vrow) => vrow.row.id === "tpc-h.throughput",
      );

    // Unfiltered but open: both streams are packed into lanes.
    const open = traceRows({
      tree: TREE,
      rows: ROWS,
      opened: new Set(["csbench.run", "csbench.stage.EXECUTE", "tpc-h.official", "tpc-h.throughput"]),
      query: "",
    }).visible.find((vrow) => vrow.row.id === "tpc-h.throughput");
    expect(open?.lanes?.flat().map((row) => row.id).sort()).toEqual([
      "tpc-h.stream1",
      "tpc-h.stream2",
    ]);

    // Filtered to a query only stream1 holds: stream2 must leave the lanes.
    const narrowed = throughput("s1.q13");
    expect(narrowed?.lanes?.flat().map((row) => row.id)).toEqual(["tpc-h.stream1"]);
  });

  it("reports zero matches rather than falling back to the whole tree", () => {
    // "No match" and "no filter" are different answers and the count is what
    // distinguishes them; returning null here would make an empty tree look
    // like a bug.
    const none = traceRows({ tree: TREE, rows: ROWS, opened: null, query: "zzzz" });
    expect(none.matches).toBe(0);
    expect(none.visible).toHaveLength(0);
  });

  it("treats a blank query as no query at all", () => {
    // The box starts empty and a reader who clears it is back to no filter,
    // not to a filter matching everything.
    const blank = traceRows({ tree: TREE, rows: ROWS, opened: null, query: "   " });
    const arrival = traceRows({ tree: TREE, rows: ROWS, opened: null, query: "" });
    expect(blank.matches).toBeNull();
    expect(ids(blank)).toEqual(ids(arrival));
  });

  it("matches on the name, case-insensitively, and never on the id alone", () => {
    const named = ROWS.map((r) =>
      r.id === "tpc-h.s1.q13" ? { ...r, name: "SELECT Lineitem" } : r,
    );
    const tree = buildTree(named);
    expect(traceRows({ tree, rows: named, opened: null, query: "lineitem" }).matches).toBe(1);
    // The row still has its old id; the filter is about what the reader sees.
    expect(traceRows({ tree, rows: named, opened: null, query: "s1.q13" }).matches).toBe(0);
  });
});
