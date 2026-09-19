import { describe, expect, it } from "vitest";

import {
  hrefOf,
  itemsHref,
  legacyRedirect,
  parseHash,
  recordHref,
  runHref,
  suiteHref,
  targetHref,
  traceHref,
  type Route,
} from "@/router";

describe("parseHash — the five sections", () => {
  it("routes the empty hash to runs", () => {
    expect(parseHash("")).toEqual({ name: "runs" });
    expect(parseHash("#/")).toEqual({ name: "runs" });
  });

  it("routes suites", () => {
    expect(parseHash("#/suites")).toEqual({ name: "suites" });
    expect(parseHash("#/suites/data-warehouse/tpc-h")).toEqual({
      name: "suite",
      domain: "data-warehouse",
      suiteId: "tpc-h",
    });
  });

  it("routes a target's edit form under the target itself", () => {
    expect(parseHash("#/targets/duckdb-sf1/edit")).toEqual({
      name: "targetEdit",
      targetName: "duckdb-sf1",
    });
    expect(parseHash("#/targets/duckdb-sf1/rm")).toEqual({ name: "notFound" });
  });

  it("routes the platform catalogue into the targets section", () => {
    // Two nouns, one section: the adapters this build has, and the targets
    // configured against them. A target's name can be anything, so the
    // catalogue gets its own top-level word rather than #/targets/platforms,
    // where it would be shadowed by a target called "platforms".
    expect(parseHash("#/platforms")).toEqual({ name: "platforms" });
    expect(parseHash("#/platforms/extra")).toEqual({ name: "notFound" });
    expect(parseHash("#/targets/platforms")).toEqual({
      name: "target",
      targetName: "platforms",
    });
  });

  it("routes targets, with create before lookup", () => {
    expect(parseHash("#/targets")).toEqual({ name: "targets" });
    expect(parseHash("#/targets/new")).toEqual({ name: "targetNew" });
    expect(parseHash("#/targets/duckdb-sf1")).toEqual({
      name: "target",
      targetName: "duckdb-sf1",
    });
  });

  it("routes runs, with create before lookup", () => {
    expect(parseHash("#/runs")).toEqual({ name: "runs" });
    expect(parseHash("#/runs/new")).toEqual({ name: "runNew" });
    expect(parseHash("#/runs/run-1")).toEqual({
      name: "run",
      runId: "run-1",
      tab: "overview",
      metric: null,
    });
  });

  it("routes observe and config", () => {
    expect(parseHash("#/observe")).toEqual({ name: "observe" });
    expect(parseHash("#/observe/run-1")).toEqual({ name: "observeRun", runId: "run-1" });
    expect(parseHash("#/config")).toEqual({ name: "config" });
  });
});

describe("parseHash — a run's four faces", () => {
  it("reads the tab from the query", () => {
    for (const tab of ["items", "trace", "config"] as const) {
      expect(parseHash(`#/runs/run-1?tab=${tab}`)).toEqual({
        name: "run",
        runId: "run-1",
        tab,
        metric: null,
      });
    }
  });

  it("carries a metric scope only on the items tab", () => {
    expect(parseHash("#/runs/run-1?tab=items&metric=accuracy")).toEqual({
      name: "run",
      runId: "run-1",
      tab: "items",
      metric: "accuracy",
    });
    // A metric on the trace tab is meaningless; it must not survive, or the
    // items tab would silently inherit a filter the reader never set.
    expect(parseHash("#/runs/run-1?tab=trace&metric=accuracy")).toEqual({
      name: "run",
      runId: "run-1",
      tab: "trace",
      metric: null,
    });
  });

  it("falls back to the overview for an unknown tab rather than 404", () => {
    // The run exists. Dropping the reader on "not found" because a query
    // string aged badly loses the thing they actually asked for.
    expect(parseHash("#/runs/run-1?tab=nope")).toEqual({
      name: "run",
      runId: "run-1",
      tab: "overview",
      metric: null,
    });
  });
});

describe("parseHash — every old link still resolves", () => {
  it("resolves the previous viewer's record routes onto the run's tabs", () => {
    expect(parseHash("#/record/run-1")).toEqual({
      name: "run",
      runId: "run-1",
      tab: "overview",
      metric: null,
    });
    expect(parseHash("#/record/run-1/trace")).toEqual({
      name: "run",
      runId: "run-1",
      tab: "trace",
      metric: null,
    });
    expect(parseHash("#/record/run-1/items")).toEqual({
      name: "run",
      runId: "run-1",
      tab: "items",
      metric: null,
    });
    expect(parseHash("#/record/run-1/items/accuracy.by_group.algebra")).toEqual({
      name: "run",
      runId: "run-1",
      tab: "items",
      metric: "accuracy.by_group.algebra",
    });
  });

  it("resolves the old suite and live routes", () => {
    expect(parseHash("#/suite/llm/gsm8k")).toEqual({
      name: "suite",
      domain: "llm",
      suiteId: "gsm8k",
    });
    expect(parseHash("#/live")).toEqual({ name: "observe" });
    expect(parseHash("#/live/run-1")).toEqual({ name: "observeRun", runId: "run-1" });
  });

  it("rejects malformed segments instead of throwing", () => {
    expect(parseHash("#/runs/%E0%A4%A")).toEqual({ name: "notFound" });
    expect(parseHash("#/record/%E0%A4%A")).toEqual({ name: "notFound" });
    expect(parseHash("#/suites/a/b/c")).toEqual({ name: "notFound" });
    expect(parseHash("#/nope")).toEqual({ name: "notFound" });
  });
});

describe("legacyRedirect", () => {
  it("rewrites an old hash to its canonical spelling", () => {
    expect(legacyRedirect("#/")).toBe("#/runs");
    expect(legacyRedirect("#/record/run-1")).toBe("#/runs/run-1");
    expect(legacyRedirect("#/record/run-1/trace")).toBe("#/runs/run-1?tab=trace");
    expect(legacyRedirect("#/record/run-1/items/accuracy")).toBe(
      "#/runs/run-1?tab=items&metric=accuracy",
    );
    expect(legacyRedirect("#/suite/llm/gsm8k")).toBe("#/suites/llm/gsm8k");
    expect(legacyRedirect("#/live/run-1")).toBe("#/observe/run-1");
  });

  it("leaves a canonical hash alone, so navigation does not rewrite itself", () => {
    for (const hash of ["#/runs", "#/runs/run-1", "#/targets/new", "#/observe", "#/config"]) {
      expect(legacyRedirect(hash)).toBeNull();
    }
  });
});

describe("href builders round-trip through parseHash", () => {
  const routes: Route[] = [
    { name: "suites" },
    { name: "suite", domain: "data-warehouse", suiteId: "tpc-h" },
    { name: "targets" },
    { name: "targetNew" },
    { name: "target", targetName: "duckdb-sf1" },
    { name: "runs" },
    { name: "runNew" },
    { name: "run", runId: "run-1", tab: "overview", metric: null },
    { name: "run", runId: "run-1", tab: "trace", metric: null },
    { name: "run", runId: "run-1", tab: "items", metric: "accuracy" },
    { name: "observe" },
    { name: "observeRun", runId: "run-1" },
    { name: "config" },
  ];

  it("parses every generated href back to the route that made it", () => {
    for (const route of routes) {
      expect(parseHash(hrefOf(route))).toEqual(route);
    }
  });

  it("encodes segments", () => {
    expect(runHref("run 1")).toBe("#/runs/run%201");
    expect(targetHref("a/b")).toBe("#/targets/a%2Fb");
    expect(suiteHref("d o", "s/u")).toBe("#/suites/d%20o/s%2Fu");
  });

  it("keeps the previous viewer's builder names working", () => {
    expect(recordHref("run-1")).toBe("#/runs/run-1");
    expect(traceHref("run-1")).toBe("#/runs/run-1?tab=trace");
    expect(itemsHref("run-1")).toBe("#/runs/run-1?tab=items");
    expect(itemsHref("run-1", "accuracy")).toBe("#/runs/run-1?tab=items&metric=accuracy");
    expect(itemsHref("run-1", null)).toBe("#/runs/run-1?tab=items");
  });
});
