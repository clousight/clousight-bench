import { describe, expect, it } from "vitest";

import { TraceChrome } from "@/features/trace/TraceChrome";
import { buildTree } from "@/lib/rowmodel";
import type { SpanRow } from "@/lib/trace";
import type { Viewport } from "@/lib/viewport";
import { focusableTags, renderMarkup } from "@/test/render";

/** A full `SpanRow`, built per call so no two tests share an object. */
function row(id: string, startS: number, endS: number, parentId: string | null = null): SpanRow {
  return {
    id,
    name: id,
    kind: "phase",
    startS,
    endS,
    status: "ok",
    isError: false,
    error: null,
    attrs: {},
    parentId,
    depth: 0,
    ancestors: [],
  };
}

/**
 * The reference trace's shape, at its real proportions: a 12.67s run whose
 * EXECUTE stage holds the official phase machine, whose heaviest phase is the
 * 6.34s load. Run through the real `buildTree` rather than hand-assembled, so
 * the conclusion the header prints comes out of the same pipeline the tree
 * renders from.
 */
const REAL_ROWS = [
  row("csbench.run", 0, 12.673),
  row("csbench.stage.PREFLIGHT", 0, 0.042, "csbench.run"),
  row("csbench.stage.EXECUTE", 0.1, 7.65, "csbench.run"),
  row("tpc-h.official", 0.12, 7.487, "csbench.stage.EXECUTE"),
  row("tpc-h.load", 0.13, 6.47, "tpc-h.official"),
  row("tpc-h.power", 6.47, 6.835, "tpc-h.official"),
  row("tpc-h.throughput", 6.825, 7.487, "tpc-h.official"),
];

const TREE = buildTree(REAL_ROWS);

/** The whole run, and a window zoomed to the throughput phase. */
const RUN: Viewport = { startS: 0, endS: 12.673 };
const PHASE: Viewport = { startS: 6.825, endS: 7.487 };

const noop = () => {};

function chrome(
  view: Viewport,
  bounds: Viewport,
  query = "",
  matches: number | null = null,
): string {
  return renderMarkup(
    <TraceChrome
      tree={TREE}
      view={view}
      bounds={bounds}
      query={query}
      onQuery={noop}
      onView={noop}
      matches={matches}
    />,
  );
}

describe("<TraceChrome>", () => {
  it("states the conclusion in the header", () => {
    // The operator should not have to hunt for the slowest thing: the header
    // prints the descent through the heaviest child at every level, ending at
    // the span the wall clock actually went into, with its duration.
    const html = chrome(RUN, RUN);
    expect(html).toContain("tpc-h.load");
    expect(html).toContain("6.34s");
    // The path, not just its end — "tpc-h.load" alone does not say where in
    // the run to look for it.
    expect(html).toContain("csbench.stage.EXECUTE");
    expect(html).toContain("tpc-h.official");
  });

  it("keeps the filter box's value across a re-render with a new window", () => {
    // The window changes on every pointermove of a strip drag. A query held
    // in this component's own state — seeded from the prop, or not fed by it
    // at all — is a second copy of what the reader typed, and the way that
    // fails is that it blanks mid-drag.
    const whole = chrome(RUN, RUN, "q13");
    const zoomed = chrome(PHASE, RUN, "q13");
    for (const html of [whole, zoomed]) expect(html).toContain('value="q13"');
  });

  it("shows the zoom breadcrumb only once the reader has zoomed", () => {
    // Unzoomed there is nowhere to go back to, and a breadcrumb with one
    // entry is furniture.
    expect(chrome(RUN, RUN)).not.toContain("data-crumb");

    const zoomed = chrome(PHASE, RUN);
    expect(zoomed).toContain("data-crumb");
    // And it says WHERE the window is, in run-relative seconds — the strip
    // below prints the window's width, and a width cannot locate it.
    expect(zoomed).toContain("6.83s");
    expect(zoomed).toContain("7.49s");
  });

  it("reports how many spans the filter matched, including none", () => {
    // A filter that hides rows without saying how many it found leaves the
    // reader unable to tell "no matches" from "matches I have to scroll to".
    expect(chrome(RUN, RUN, "q13", 3)).toContain(">3<");
    const empty = chrome(RUN, RUN, "zzz", 0);
    expect(empty).toContain("No span matches this filter.");
    // Zero is a sentence, not a count with a noun after it.
    expect(empty).not.toContain(">0<");
    // And with no query at all there is no count to report.
    expect(chrome(RUN, RUN)).not.toContain("matching spans");
  });

  it("gives every focusable element the app's focus ring, not the UA outline", () => {
    // Per element: one ringed control satisfies an assertion made over the
    // whole string, which is how the overview strip shipped without a ring.
    const tags = focusableTags(chrome(PHASE, RUN, "q13", 1));
    // The filter box and the breadcrumb's way back out of the zoom.
    expect(tags).toHaveLength(2);
    for (const tag of tags) expect(tag, tag).toContain("focus-visible:ring-ring");
  });
});
