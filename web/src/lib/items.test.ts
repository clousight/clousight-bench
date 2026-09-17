import { describe, expect, it } from "vitest";

import {
  compareItemIds,
  detailTarget,
  filterItems,
  formatMetricSegment,
  itemGroups,
  itemMetricIds,
  itemStatus,
  itemSummary,
  nextSortDir,
  parseMetricSegment,
  scoreOf,
  sortItems,
  usageKeys,
  type ItemResultData,
} from "@/lib/items";

function item(
  id: string,
  scores: { metric: string; value?: unknown; status?: string; error?: string }[],
  extra: Partial<ItemResultData> = {},
): ItemResultData {
  return { item_id: id, scores, ...extra };
}

const CORPUS: ItemResultData[] = [
  item("test-0", [{ metric: "accuracy", value: 1, status: "ok" }], {
    usage: { latency_ms: 800 },
  }),
  item("test-1", [{ metric: "accuracy", value: 0, status: "fail" }], {
    group: "algebra",
    usage: { latency_ms: 760 },
  }),
  item("test-2", [{ metric: "accuracy", value: 0, status: "error", error: "boom" }], {
    group: "algebra",
  }),
  item("test-3", [{ metric: "accuracy", value: 0, status: "skip" }], { group: "geometry" }),
];

describe("itemStatus", () => {
  it("defaults a missing status to ok, the dataclass default", () => {
    expect(itemStatus({ metric: "m" })).toBe("ok");
  });

  it("maps an unrecognised status to error rather than silently to ok", () => {
    // A status the core does not emit means our reader is out of date with the
    // producer. Calling it "ok" would turn our own staleness into a pass.
    expect(itemStatus({ metric: "m", status: "weird" })).toBe("error");
  });
});

describe("itemSummary", () => {
  it("counts all four states over the whole corpus", () => {
    expect(itemSummary(CORPUS)).toEqual({
      total: 4,
      counts: { ok: 1, fail: 1, skip: 1, error: 1 },
    });
  });

  it("counts an item once per worst score, not once per score", () => {
    // Two scores on one item: the item is one row, and a fail anywhere in it
    // is what a reader needs to see. Counting scores would report 5 of 4.
    const two = [item("x", [{ metric: "a", status: "ok" }, { metric: "b", status: "fail" }])];
    expect(itemSummary(two)).toEqual({ total: 1, counts: { ok: 0, fail: 1, skip: 0, error: 0 } });
  });

  it("treats an item with no scores as skip, not ok", () => {
    expect(itemSummary([item("x", [])])).toEqual({
      total: 1,
      counts: { ok: 0, fail: 0, skip: 1, error: 0 },
    });
  });

  it("ranks error above fail when one item has both", () => {
    // A scorer crash on an item makes that item's fail unknowable: we cannot
    // say the subject failed when our own machinery did.
    const both = [item("x", [{ metric: "a", status: "fail" }, { metric: "b", status: "error" }])];
    expect(itemSummary(both).counts).toEqual({ ok: 0, fail: 0, skip: 0, error: 1 });
  });
});

describe("parseMetricSegment / formatMetricSegment", () => {
  it("reads a bare metric", () => {
    expect(parseMetricSegment("accuracy")).toEqual({ metric: "accuracy", group: null });
  });

  it("splits at the FIRST .by_group. and keeps the rest as the group", () => {
    expect(parseMetricSegment("accuracy.by_group.abstract_algebra")).toEqual({
      metric: "accuracy",
      group: "abstract_algebra",
    });
    expect(parseMetricSegment("accuracy.by_group.a.b")).toEqual({
      metric: "accuracy",
      group: "a.b",
    });
  });

  it("round-trips", () => {
    const scope = { metric: "accuracy", group: "abstract_algebra" };
    expect(parseMetricSegment(formatMetricSegment(scope))).toEqual(scope);
    expect(parseMetricSegment(formatMetricSegment({ metric: "pass_at_1", group: null }))).toEqual({
      metric: "pass_at_1",
      group: null,
    });
  });
});

describe("detailTarget", () => {
  it("links a measurement whose bare metric has item scores", () => {
    expect(detailTarget("gsm8k.accuracy", "gsm8k", CORPUS)).toBe("accuracy");
  });

  it("returns null for a measurement with no item substrate", () => {
    // avg_latency_ms is summed from usage, never an ItemScore. A link here
    // would open a table that cannot show the number it claims to explain.
    expect(detailTarget("gsm8k.avg_latency_ms", "gsm8k", CORPUS)).toBeNull();
  });

  it("only strips the exact suite prefix", () => {
    expect(detailTarget("accuracy", "gsm8k", CORPUS)).toBe("accuracy");
    expect(detailTarget("other.accuracy", "gsm8k", CORPUS)).toBeNull();
  });

  it("links a by_group measurement only when that group really exists", () => {
    expect(detailTarget("mmlu.accuracy.by_group.algebra", "mmlu", CORPUS)).toBe(
      "accuracy.by_group.algebra",
    );
    expect(detailTarget("mmlu.accuracy.by_group.nope", "mmlu", CORPUS)).toBeNull();
  });

  it("returns null when there are no items at all", () => {
    expect(detailTarget("tpc-h.qphh_at_size", "tpc-h", [])).toBeNull();
  });
});

describe("column derivation", () => {
  it("lists metrics in first-appearance order", () => {
    const corpus = [item("a", [{ metric: "b" }, { metric: "a" }]), item("b", [{ metric: "c" }])];
    expect(itemMetricIds(corpus)).toEqual(["b", "a", "c"]);
  });

  it("lists only groups that exist, sorted", () => {
    expect(itemGroups(CORPUS)).toEqual(["algebra", "geometry"]);
    expect(itemGroups([item("a", [])])).toEqual([]);
  });

  it("lists numeric usage keys only, sorted", () => {
    const corpus = [item("a", [], { usage: { latency_ms: 1, note: "x", tokens: 2 } })];
    expect(usageKeys(corpus)).toEqual(["latency_ms", "tokens"]);
  });
});

describe("filterItems", () => {
  const base = { query: "", scope: null, failingOnly: false };

  it("returns everything by default", () => {
    expect(filterItems(CORPUS, base)).toHaveLength(4);
  });

  it("matches item_id and group, case-insensitively", () => {
    expect(filterItems(CORPUS, { ...base, query: "TEST-1" }).map((i) => i.item_id)).toEqual([
      "test-1",
    ]);
    expect(filterItems(CORPUS, { ...base, query: "geo" }).map((i) => i.item_id)).toEqual(["test-3"]);
  });

  it("keeps fail AND error under failingOnly, and drops skip", () => {
    // skip is "not scored", not "scored badly" — folding it in would inflate
    // what a reader reads as the failure list.
    expect(filterItems(CORPUS, { ...base, failingOnly: true }).map((i) => i.item_id)).toEqual([
      "test-1",
      "test-2",
    ]);
  });

  it("scopes to a metric and, when present, a group", () => {
    const scoped = filterItems(CORPUS, {
      ...base,
      scope: { metric: "accuracy", group: "algebra" },
    });
    expect(scoped.map((i) => i.item_id)).toEqual(["test-1", "test-2"]);
  });

  it("drops items lacking the scoped metric", () => {
    const corpus = [...CORPUS, item("test-4", [{ metric: "other" }])];
    expect(filterItems(corpus, { ...base, scope: { metric: "accuracy", group: null } })).toHaveLength(
      4,
    );
  });
});

describe("sortItems", () => {
  it("sorts item ids naturally, so -10 follows -9", () => {
    expect(compareItemIds("a-9", "a-10")).toBeLessThan(0);
  });

  it("returns natural id order for dir=none regardless of column", () => {
    const shuffled = [CORPUS[2], CORPUS[0], CORPUS[3], CORPUS[1]];
    expect(sortItems(shuffled, "accuracy", "none").map((i) => i.item_id)).toEqual([
      "test-0",
      "test-1",
      "test-2",
      "test-3",
    ]);
  });

  it("does not mutate its input", () => {
    const input = [CORPUS[1], CORPUS[0]];
    sortItems(input, null, "asc");
    expect(input.map((i) => i.item_id)).toEqual(["test-1", "test-0"]);
  });

  it("sorts by a metric column's numeric value", () => {
    expect(sortItems(CORPUS, "accuracy", "desc")[0].item_id).toBe("test-0");
  });

  it("sorts by a usage column", () => {
    expect(sortItems(CORPUS, "usage:latency_ms", "desc")[0].item_id).toBe("test-0");
    expect(sortItems(CORPUS, "usage:latency_ms", "asc")[0].item_id).toBe("test-1");
  });

  it("puts items missing the sorted value last in both directions", () => {
    // Absent is not zero. A missing latency sorting as 0 would read as the
    // fastest item in the run.
    expect(
      sortItems(CORPUS, "usage:latency_ms", "asc")
        .slice(-2)
        .map((i) => i.item_id),
    ).toEqual(["test-2", "test-3"]);
    expect(
      sortItems(CORPUS, "usage:latency_ms", "desc")
        .slice(-2)
        .map((i) => i.item_id),
    ).toEqual(["test-2", "test-3"]);
  });
});

describe("nextSortDir", () => {
  it("cycles none → desc → asc → none", () => {
    expect(nextSortDir("none")).toBe("desc");
    expect(nextSortDir("desc")).toBe("asc");
    expect(nextSortDir("asc")).toBe("none");
  });
});

describe("scoreOf", () => {
  it("finds a score by metric id", () => {
    expect(scoreOf(CORPUS[0], "accuracy")?.value).toBe(1);
  });

  it("returns null for an absent metric", () => {
    expect(scoreOf(CORPUS[0], "nope")).toBeNull();
  });
});
