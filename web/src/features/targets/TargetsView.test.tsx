import { describe, expect, it } from "vitest";

import type { TargetSummary } from "@/api";
import { TargetsTable, matchesFilter } from "@/features/targets/TargetsView";
import { renderMarkup } from "@/test/render";

const target = (over: Partial<TargetSummary> = {}): TargetSummary => ({
  name: "duckdb-sf1",
  filename: "duckdb-sf1.yaml",
  size: 120,
  modified: 1_757_000_000,
  mode: "mock",
  provider: "duckdb",
  region: "",
  error: "",
  ...over,
});

describe("matchesFilter", () => {
  it("matches on the parts a reader would type", () => {
    const row = target({ name: "aliyun-prod", provider: "aliyun", mode: "live", region: "cn-hangzhou" });
    for (const query of ["aliyun", "PROD", "live", "cn-hang"]) {
      expect(matchesFilter(row, query)).toBe(true);
    }
    expect(matchesFilter(row, "duckdb")).toBe(false);
  });

  it("matches everything on an empty query", () => {
    expect(matchesFilter(target(), "   ")).toBe(true);
  });
});

describe("TargetsTable", () => {
  it("says what a target IS when there are none at all", () => {
    // The empty list is the only place with room to explain the noun, and a
    // first-time reader meets it before anything else.
    const markup = renderMarkup(<TargetsTable targets={[]} filter="" writable={false} />);
    expect(markup).toContain("A target is one instance of a platform");
  });

  it("says something different when the filter is what emptied it", () => {
    // "There are none" and "none match" are different facts, and offering
    // "create your first target" to someone who typed a typo is wrong.
    const markup = renderMarkup(<TargetsTable targets={[target()]} filter="zzz" writable={false} />);
    expect(markup).not.toContain("A target is one instance of a platform");
    expect(markup).toContain("No target matches");
  });

  it("offers creating one only when the server would accept it", () => {
    expect(renderMarkup(<TargetsTable targets={[]} filter="" writable />)).toContain("#/targets/new");
    expect(renderMarkup(<TargetsTable targets={[]} filter="" writable={false} />)).not.toContain(
      "#/targets/new",
    );
  });

  it("links each target to its own page", () => {
    const markup = renderMarkup(<TargetsTable targets={[target()]} filter="" writable={false} />);
    expect(markup).toContain('href="#/targets/duckdb-sf1"');
    expect(markup).toContain("mock");
    expect(markup).toContain("duckdb");
  });

  it("shows a file it could not parse instead of hiding it", () => {
    // A broken config that vanishes from the list looks like a deleted one,
    // and the reader goes looking for a deletion that never happened.
    const markup = renderMarkup(
      <TargetsTable targets={[target({ error: "not valid YAML: line 3" })]} filter="" writable={false} />,
    );
    expect(markup).toContain("duckdb-sf1");
    expect(markup).toContain("not valid YAML: line 3");
  });
});
