import { describe, expect, it } from "vitest";

import { itemsHref, parseHash, recordHref, suiteHref, traceHref } from "@/router";

describe("parseHash", () => {
  it("routes the board", () => {
    expect(parseHash("")).toEqual({ name: "board" });
    expect(parseHash("#/")).toEqual({ name: "board" });
  });

  it("keeps the record routes the previous viewer shipped", () => {
    expect(parseHash("#/record/run-1")).toEqual({ name: "record", runId: "run-1" });
    expect(parseHash("#/record/run-1/trace")).toEqual({ name: "trace", runId: "run-1" });
  });

  it("routes the detail surface, with and without a metric scope", () => {
    expect(parseHash("#/record/run-1/items")).toEqual({
      name: "items",
      runId: "run-1",
      metric: null,
    });
    expect(parseHash("#/record/run-1/items/accuracy")).toEqual({
      name: "items",
      runId: "run-1",
      metric: "accuracy",
    });
  });

  it("decodes a by_group metric segment", () => {
    expect(parseHash("#/record/run-1/items/accuracy.by_group.abstract_algebra")).toEqual({
      name: "items",
      runId: "run-1",
      metric: "accuracy.by_group.abstract_algebra",
    });
  });

  it("rejects an undecodable metric segment instead of throwing", () => {
    expect(parseHash("#/record/run-1/items/%E0%A4%A")).toEqual({ name: "notFound" });
  });

  it("routes suites, runs and live", () => {
    expect(parseHash("#/suite/data-warehouse/tpc-h")).toEqual({
      name: "suite",
      domain: "data-warehouse",
      suiteId: "tpc-h",
    });
    expect(parseHash("#/runs")).toEqual({ name: "runs" });
    expect(parseHash("#/live")).toEqual({ name: "live" });
    expect(parseHash("#/live/run-1")).toEqual({ name: "liveRun", runId: "run-1" });
  });

  it("round-trips ids that need escaping", () => {
    const runId = "suite:tpc-h/weird id";
    expect(parseHash(recordHref(runId))).toEqual({ name: "record", runId });
    expect(parseHash(traceHref(runId))).toEqual({ name: "trace", runId });
    expect(parseHash(suiteHref("a/b", "c d"))).toEqual({ name: "suite", domain: "a/b", suiteId: "c d" });
  });

  it("resolves a malformed hash to notFound instead of throwing", () => {
    expect(parseHash("#/record/%E0%A4%A")).toEqual({ name: "notFound" });
    expect(parseHash("#/record/")).toEqual({ name: "notFound" });
    expect(parseHash("#/nope")).toEqual({ name: "notFound" });
    expect(parseHash("#/suite/only-one")).toEqual({ name: "notFound" });
    expect(parseHash("#/live/a/b")).toEqual({ name: "notFound" });
  });
});

describe("itemsHref", () => {
  it("builds the bare and the scoped href", () => {
    expect(itemsHref("run-1")).toBe("#/record/run-1/items");
    expect(itemsHref("run-1", "accuracy")).toBe("#/record/run-1/items/accuracy");
  });

  it("encodes both segments", () => {
    expect(itemsHref("run 1", "a/b")).toBe("#/record/run%201/items/a%2Fb");
  });

  it("treats null as unscoped, so a missing target cannot build a broken link", () => {
    expect(itemsHref("run-1", null)).toBe("#/record/run-1/items");
  });
});
