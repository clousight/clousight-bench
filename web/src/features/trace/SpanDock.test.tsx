import { describe, expect, it } from "vitest";

import { SpanDock } from "@/features/trace/SpanDock";
import type { SpanRow } from "@/lib/trace";
import type { Viewport } from "@/lib/viewport";
import { renderMarkup } from "@/test/render";

/** A full `SpanRow`, built per call so no two tests share an object. */
function row(
  id: string,
  startS: number,
  endS: number,
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
    parentId: null,
    depth: 0,
    ancestors: [],
    ...extra,
  };
}

const V: Viewport = { startS: 0, endS: 20 };

// The span from `TraceTree.test.tsx`'s "prints the window value and the true
// total in separate columns" case: 12.201 -> 18.771 (6.57s total), seen
// through an [11.1, 12.9] window (699ms of it).
const load = row("tpc-h.load", 12.201, 18.771, { kind: "phase" });

const q13 = row("tpc-h.q13", 1, 2, {
  kind: "query",
  attrs: { "db.operation.name": "query 13" },
});

describe("<SpanDock>", () => {
  it("prompts rather than showing a blank panel when nothing is selected", () => {
    // Rendered through `renderMarkup` (the shared harness, pinned to `en`),
    // so `t("dock.empty")` resolves to its copy rather than the raw key.
    const html = renderMarkup(<SpanDock row={null} view={V} />);
    expect(html).toMatch(/\S/);
    expect(html).toContain("Select a span"); // rendered copy for dock.empty, en locale
  });

  it("shows the window value and the true total as different numbers", () => {
    const html = renderMarkup(<SpanDock row={load} view={{ startS: 11.1, endS: 12.9 }} />);
    expect(html).toContain("699ms");
    expect(html).toContain("6.57s");
  });

  it("renders the span's attributes", () => {
    const html = renderMarkup(<SpanDock row={q13} view={V} />);
    expect(html).toContain("db.operation.name");
    expect(html).toContain("query 13");
  });

  it("escapes attribute values rather than trusting them", () => {
    // Attributes come from the traced system; they are data, not markup.
    const row2 = { ...q13, attrs: { evil: "<img src=x onerror=1>" } };
    const html = renderMarkup(<SpanDock row={row2} view={V} />);
    expect(html).not.toContain("<img");
  });

  it("positions the share-of-window mark through place(), not a fraction of the total", () => {
    // A span the window does not overlap at all draws no share mark — a
    // `pctOf(totalS)` implementation would still draw something, since the
    // span has nonzero duration; `place()` correctly says it is not visible.
    const outside = row("elsewhere", 100, 101);
    const html = renderMarkup(<SpanDock row={outside} view={V} />);
    expect(html).not.toMatch(/data-share-mark/);

    // A span that exactly fills the window is the full width of the mark,
    // not some other fraction computed from its share of a larger total.
    const full = row("fills-window", 0, 20);
    const htmlFull = renderMarkup(<SpanDock row={full} view={V} />);
    expect(htmlFull).toMatch(/data-share-mark[^>]*style="[^"]*left:0%;width:100%/);
  });
});
