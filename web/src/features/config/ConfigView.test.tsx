import { describe, expect, it } from "vitest";

import type { PluginInventory } from "@/api";
import { MetricGlossary, PluginTable, RetiredVocabulary } from "@/features/config/ConfigView";
import { renderMarkup } from "@/test/render";

const inventory: PluginInventory = {
  plugins: [
    { kind: "domain", name: "llm", target: "pkg:LLMDomain", distribution: "clousight-bench 0.7.0" },
    { kind: "evaluator", name: "gsm8k", target: "pkg:Gsm8kEvaluator", distribution: "" },
  ],
  core_version: "0.7.0",
  plugin_api: "3.1",
};

describe("PluginTable", () => {
  it("groups by extension point, so an empty one is visible", () => {
    // "Why can't I run X" is usually "nothing is registered for X". A flat
    // list hides that; a grouped one shows the hole.
    const markup = renderMarkup(<PluginTable inventory={inventory} />);
    expect(markup).toContain("domain");
    expect(markup).toContain("evaluator");
    expect(markup).toContain("llm");
  });

  it("names where a plugin came from, and says so when it cannot", () => {
    const markup = renderMarkup(<PluginTable inventory={inventory} />);
    expect(markup).toContain("clousight-bench 0.7.0");
    expect(markup).toContain("—");
  });

  it("prints the plugin API this build speaks", () => {
    // The number that decides whether a third-party plugin loads at all.
    expect(renderMarkup(<PluginTable inventory={inventory} />)).toContain("3.1");
  });
});

describe("MetricGlossary", () => {
  it("renders the vocabulary the rest of the app reads from", () => {
    const markup = renderMarkup(<MetricGlossary />);
    expect(markup).toContain("tpc-h");
    // Direction is part of a metric's meaning: without it a reader cannot
    // tell a good number from a bad one.
    expect(markup.toLowerCase()).toContain("higher");
  });
});

describe("RetiredVocabulary", () => {
  it("says what a dead word became, not just that it is dead", () => {
    const markup = renderMarkup(<RetiredVocabulary />);
    expect(markup).toContain("COLLECT");
    expect(markup).toContain("SEAL");
    expect(markup).toContain("#/record");
  });

  it("covers the words that were deleted outright", () => {
    // "Renamed to nothing" is a real answer and the hardest one to find by
    // searching, which is exactly why the table exists.
    expect(renderMarkup(<RetiredVocabulary />)).toContain("report");
  });
});
