import { describe, expect, it } from "vitest";

import { MeasurementDetailLink, RecordLinks } from "@/features/record/RecordView";
import type { ItemResultData } from "@/lib/items";
import { renderMarkup } from "@/test/render";

describe("RecordLinks", () => {
  it("offers the detail surface when the run has items", () => {
    const markup = renderMarkup(
      <RecordLinks runId="run-1" hasItems suiteId="gsm8k" domain="llm" />,
    );
    expect(markup).toContain("#/record/run-1/items");
    expect(markup).toContain("#/record/run-1/trace");
  });

  it("opens no door to an empty room", () => {
    // TPC-H and friends measure an engine, not examples: they emit no items,
    // and a link into an empty table is worse than no link.
    const markup = renderMarkup(
      <RecordLinks runId="run-1" hasItems={false} suiteId="tpc-h" domain="data-warehouse" />,
    );
    expect(markup).not.toContain("/items");
    // The trace link is unconditional — every run has lifecycle spans — and it
    // must still be pushed to the right edge when it is the only one there.
    expect(markup).toContain("#/record/run-1/trace");
    expect(markup).toContain("ml-auto");
  });

  it("falls back to the board when the run has no suite", () => {
    const markup = renderMarkup(
      <RecordLinks runId="run-1" hasItems={false} suiteId="" domain="" />,
    );
    expect(markup).toContain('href="#/"');
  });
});

describe("MeasurementDetailLink", () => {
  const items: ItemResultData[] = [
    { item_id: "a", group: "algebra", scores: [{ metric: "accuracy", value: 1 }] },
  ];

  it("links a measurement that has item-level evidence", () => {
    const markup = renderMarkup(
      <MeasurementDetailLink
        runId="run-1"
        measurementKey="gsm8k.accuracy"
        suiteId="gsm8k"
        items={items}
      />,
    );
    expect(markup).toContain("#/record/run-1/items/accuracy");
  });

  it("renders nothing for a measurement summed from usage", () => {
    // avg_latency_ms never passes through an ItemScore. A link here would
    // promise a table that cannot show the number it claims to explain.
    const markup = renderMarkup(
      <MeasurementDetailLink
        runId="run-1"
        measurementKey="gsm8k.avg_latency_ms"
        suiteId="gsm8k"
        items={items}
      />,
    );
    expect(markup).toBe("");
  });

  it("carries the group through for a by_group measurement", () => {
    const markup = renderMarkup(
      <MeasurementDetailLink
        runId="run-1"
        measurementKey="mmlu.accuracy.by_group.algebra"
        suiteId="mmlu"
        items={items}
      />,
    );
    expect(markup).toContain("items/accuracy.by_group.algebra");
  });

  it("renders nothing when the run has no items at all", () => {
    const markup = renderMarkup(
      <MeasurementDetailLink
        runId="run-1"
        measurementKey="tpc-h.qphh_at_size"
        suiteId="tpc-h"
        items={[]}
      />,
    );
    expect(markup).toBe("");
  });
});
