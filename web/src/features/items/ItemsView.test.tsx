import { describe, expect, it } from "vitest";

import { ItemsTable, type ItemsTableProps } from "@/features/items/ItemsView";
import type { ItemResultData } from "@/lib/items";
import { focusableTags, renderMarkup } from "@/test/render";

const CORPUS: ItemResultData[] = [
  {
    item_id: "test-0",
    group: "algebra",
    scores: [{ metric: "accuracy", value: 1, status: "ok" }],
    usage: { latency_ms: 800 },
  },
  {
    item_id: "test-1",
    group: "algebra",
    output: "17",
    reference: "18",
    scores: [{ metric: "accuracy", value: 0, status: "fail", reason: "off by one" }],
    usage: { latency_ms: 760 },
  },
  {
    item_id: "test-2",
    group: "geometry",
    scores: [{ metric: "accuracy", value: 0, status: "error", error: "scorer blew up" }],
  },
];

function table(props: Partial<ItemsTableProps> = {}): string {
  const merged: ItemsTableProps = {
    items: CORPUS,
    suiteId: "gsm8k",
    scope: null,
    measurement: null,
    ...props,
  };
  return renderMarkup(<ItemsTable {...merged} />);
}

describe("ItemsTable", () => {
  it("prints the four-state tally for the whole corpus", () => {
    const markup = table();
    for (const word of ["ok", "fail", "error", "skip"]) {
      expect(markup).toContain(word);
    }
  });

  it("renders one column per metric and per numeric usage key", () => {
    const markup = table();
    expect(markup).toContain("accuracy");
    expect(markup).toContain("latency_ms");
  });

  it("omits the group column when no item has a group", () => {
    const ungrouped = CORPUS.map(({ group: _group, ...rest }) => rest);
    expect(table({ items: ungrouped })).not.toContain("Group");
    expect(table()).toContain("Group");
  });

  it("gives fail and error different treatments, so a scorer bug never reads as a bad subject", () => {
    // fail is a property of the thing under test; error is a bug in our own
    // scorer. Same colour for both would attribute our defect to the subject.
    const markup = table();
    expect(markup).toContain("status-critical");
    expect(markup).toContain("status-warning");
    expect(markup).toContain("ring-status-warning/50");
  });

  it("puts every sortable header in a focusable button carrying aria-sort", () => {
    const markup = table();
    expect(markup).toContain('aria-sort="none"');
    const buttons = focusableTags(markup).filter((tag) => tag.startsWith("<button"));
    expect(buttons.length).toBeGreaterThan(0);
    for (const tag of buttons) {
      expect(tag).toContain("focus-visible:ring-ring");
    }
  });

  it("renders its numbers in the monospace face with tabular figures", () => {
    // The source-level ratchet greps lines for the pair; this checks what is
    // actually emitted, where a number that inherited the sans stack would
    // really show up. Asserting the two classes together is the rule itself:
    // tabular figures in the sans stack are what regressed three times.
    const markup = table();
    expect(markup).toContain("font-mono text-sm tabular-nums");
    expect(markup).toContain("font-mono text-xs tabular-nums");
  });

  it("shows the anchored measurement only when the view is scoped to one metric", () => {
    const scoped = table({
      scope: { metric: "accuracy", group: null },
      measurement: { key: "gsm8k.accuracy", entry: { value: 0.667 } },
    });
    expect(scoped).toContain("gsm8k.accuracy");
    // Unscoped, picking a measurement to display would imply the rows beneath
    // it are what produced it.
    expect(table()).not.toContain("gsm8k.accuracy");
  });

  it("narrows the columns to the scoped metric", () => {
    const corpus: ItemResultData[] = [
      { item_id: "a", scores: [{ metric: "accuracy", value: 1 }, { metric: "answered_rate", value: 1 }] },
    ];
    const scoped = table({ items: corpus, scope: { metric: "accuracy", group: null } });
    expect(scoped).not.toContain("answered_rate");
    expect(table({ items: corpus })).toContain("answered_rate");
  });

  it("offers an expander only for items that have fields to show", () => {
    // test-0 has no input/output/reference/attrs and no reason — an expander
    // there would open an empty drawer.
    const one = table({ items: [CORPUS[0]] });
    const other = table({ items: [CORPUS[1]] });
    expect(one).not.toContain("aria-expanded");
    expect(other).toContain("aria-expanded");
  });
});

describe("the honesty invariant, on screen", () => {
  it("keeps the whole-run tally while a filter narrows the table", () => {
    // 3 items, 2 of them not ok. With failing-only on, NO passing row is on
    // screen — and the tally must still say there is one, because it describes
    // the run and not the narrowing. Reading the summary as text makes that
    // one assertion instead of four fragile substring checks.
    const filtered = table({ initialFailingOnly: true });
    const summary = filtered.slice(0, filtered.indexOf("<input"));
    const text = summary
      .replace(/<[^>]*>/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    expect(text).toBe("Per-item detail 3 items 1 ok 1 fail 0 skip 1 ! error");
    expect(filtered).toContain("2 matching of 3");
    // …and the table below really is narrowed, so the assertion above is not
    // passing because the filter silently did nothing.
    expect(filtered).not.toContain(">test-0<");
  });

  it("prints no percentage anywhere", () => {
    // A ratio over a filtered subset is the one thing this surface must never
    // compute. "No % at all" is cruder than "no subset ratio" and is the rule
    // a test can actually enforce.
    expect(table()).not.toContain("%");
    expect(table({ initialFailingOnly: true })).not.toContain("%");
  });
});
