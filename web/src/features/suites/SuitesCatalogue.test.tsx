import { describe, expect, it } from "vitest";

import type { InstalledSuite } from "@/api";
import { SuitesTable } from "@/features/suites/SuitesCatalogue";
import { renderMarkup } from "@/test/render";

const suite = (over: Partial<InstalledSuite> = {}): InstalledSuite => ({
  suite_id: "tpc-h",
  suite_version: "duckdb-1.5.4/tpch/sf1-ref-v1",
  evaluators: [{ evaluator_id: "official-tpch-evaluator", official: true }],
  seen_platforms: ["duckdb-local"],
  runs: 5,
  ...over,
});

describe("SuitesTable", () => {
  it("leads with the pin, because that is what makes two numbers comparable", () => {
    const markup = renderMarkup(<SuitesTable suites={[suite()]} />);
    expect(markup).toContain("duckdb-1.5.4/tpch/sf1-ref-v1");
  });

  it("marks which scorer speaks for the benchmark itself", () => {
    // `official` is the difference between the suite's canonical numbers and
    // a number someone computed, and it travels with every measurement.
    const markup = renderMarkup(<SuitesTable suites={[suite()]} />);
    expect(markup).toContain("official-tpch-evaluator");
    expect(markup.toLowerCase()).toContain("official");
  });

  it("tells a benchmark nobody has run from one that cannot be scored", () => {
    // Zero runs is ordinary. Zero evaluators means this benchmark cannot
    // produce a result at all, and the page must not let them look alike.
    const unrun = renderMarkup(<SuitesTable suites={[suite({ runs: 0, seen_platforms: [] })]} />);
    expect(unrun).not.toContain("cannot be scored");

    const unscorable = renderMarkup(<SuitesTable suites={[suite({ evaluators: [] })]} />);
    expect(unscorable).toContain("cannot be scored");
  });

  it("says where each benchmark has actually run", () => {
    expect(renderMarkup(<SuitesTable suites={[suite()]} />)).toContain("duckdb-local");
  });
});
