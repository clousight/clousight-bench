import { describe, expect, it } from "vitest";

import type { TrajectoryData, TraceSpan } from "@/api";
import { TraceBody } from "@/features/trace/TraceView";
import { renderMarkup } from "@/test/render";

/**
 * `TraceBody`, not `TraceView`, and that split is the point.
 *
 * `TraceView` is the fetch shell: `useJSON` loads in an effect and
 * `renderToStaticMarkup` never runs effects, so rendering it here would only
 * ever produce the loading state — which is exactly why the file that owns
 * every piece of this view's state had no test of its own. `TraceBody` takes
 * the data as a prop, so the assembled page is renderable.
 *
 * What this harness can and cannot see is the same as everywhere else: what
 * the tree EMITS (which rows exist, in what order, carrying which attributes),
 * and never layout. So "the dock does not narrow the lane" is a browser
 * measurement — `web/probe/trace-gate-probe.mjs` — and the source invariant
 * behind it is `test_the_span_dock_is_an_overlay_not_a_column` in
 * `tests/test_viewer_frontend.py`. Neither is claimed here.
 *
 * The derivation this page runs on (arrival, the filter, the forced-open
 * ancestors, the lane filter) is `lib/tracerows.ts` and is tested directly in
 * `lib/tracerows.test.ts`. What is left for this file is the ASSEMBLY.
 */
function span(
  span_id: string,
  t_start: number,
  t_end: number,
  parent_id: string | null = null,
  kind = "query",
): TraceSpan {
  return { span_id, name: span_id, kind, t_start, t_end, parent_id, status: "ok", attrs: {} };
}

/** The reference trace's shape: run -> stages -> the official phase machine,
 * whose heaviest phase is the load and whose throughput phase runs two
 * concurrent streams with queries inside them. */
const DATA: TrajectoryData = {
  t0: 0,
  source: "full",
  spans: [
    span("csbench.run", 0, 12.673, null, "lifecycle"),
    span("csbench.stage.PREFLIGHT", 0, 0.042, "csbench.run", "lifecycle"),
    span("csbench.stage.EXECUTE", 0.1, 7.65, "csbench.run", "lifecycle"),
    span("tpc-h.official", 0.12, 7.487, "csbench.stage.EXECUTE", "phase"),
    span("tpc-h.load", 0.13, 6.47, "tpc-h.official", "phase"),
    span("tpc-h.power", 6.47, 6.835, "tpc-h.official", "phase"),
    span("tpc-h.throughput", 6.825, 7.487, "tpc-h.official", "phase"),
    span("tpc-h.stream1", 6.83, 7.47, "tpc-h.throughput", "phase"),
    span("tpc-h.s1.q13", 6.9, 6.95, "tpc-h.stream1"),
    span("tpc-h.stream2", 6.84, 7.48, "tpc-h.throughput", "phase"),
  ],
};

const body = (data: TrajectoryData = DATA) =>
  renderMarkup(<TraceBody runId="run-20260917-000718-fefc6d" data={data} />);

/** The name each span row draws, in document order. The name cell's `title`
 * is the first one in a row, because it precedes the bar's. */
function rowNames(markup: string): string[] {
  return [...markup.matchAll(/<div data-row="span"[\s\S]*?title="([^"]*)"/g)].map(
    (match) => match[1],
  );
}

/** The accessible name of every lane mark, in document order. A packed child
 * has no row, so this is the other half of "what the tree is showing" — and
 * the aggregate pane below the tree is run-scoped, so asserting absence over
 * the whole markup string would be asserting about the wrong component. */
function laneMarkLabels(markup: string): string[] {
  return [...markup.matchAll(/data-mark-lane="[^"]*"[^>]*aria-label="([^"]*)"/g)].map(
    (match) => match[1],
  );
}

describe("<TraceBody>", () => {
  it("mounts the chrome, the strip and the tree, in that order", () => {
    // The three instruments the page is made of, asserted present AND in
    // order: the conclusion line has to precede the thing it is a conclusion
    // about, and the strip is the drag target for the window the tree renders
    // through.
    const html = body();
    const conclusion = html.indexOf('data-conclusion="true"');
    const strip = html.indexOf('role="img"');
    const head = html.indexOf('data-row="head"');

    expect(conclusion, "no conclusion line — is TraceChrome still mounted?").toBeGreaterThan(-1);
    expect(strip, "no overview strip").toBeGreaterThan(-1);
    expect(head, "no tree header row").toBeGreaterThan(-1);
    expect(conclusion).toBeLessThan(strip);
    expect(strip).toBeLessThan(head);

    // The filter box, which the chrome advertises a match count for.
    expect(html).toContain('data-filter="span"');
    // And the run's own identity, so the page cannot be read as another run's
    // trace.
    expect(html).toContain("run-20260917-000718-fefc6d");
    expect(html).toContain("12.7s");
  });

  it("states the conclusion the tree then opens to", () => {
    // The header names the descent through the heaviest child at every level
    // and the tree opens exactly that path, because both read `rowmodel`'s own
    // descent. A header naming a span the tree left closed is the failure this
    // pairing exists to prevent.
    const html = body();
    const conclusion = /data-conclusion="true"[\s\S]*?<\/p>/.exec(html)?.[0] ?? "";
    expect(conclusion).toContain("tpc-h.load");
    expect(conclusion).toContain("6.34s");

    const names = rowNames(html);
    for (const id of ["csbench.run", "csbench.stage.EXECUTE", "tpc-h.official", "tpc-h.load"]) {
      expect(names, id).toContain(id);
    }
    // `load` having a row at all is the arrival state doing its job. The
    // official phase machine's children overlap (power ends 6.835s, throughput
    // starts 6.825s), so they pack into lanes and a packed child's row is
    // suppressed — unless the reader drilled it, which on arrival is exactly
    // the slowest path and nothing else.
    const marks = laneMarkLabels(html);
    for (const id of ["tpc-h.power", "tpc-h.throughput"]) {
      expect(names, id).not.toContain(id);
      // Nothing has gone missing from the page: they are marks, not rows.
      expect(marks.some((label) => label.startsWith(id)), id).toBe(true);
    }

    // And the descent stops there. throughput is not drilled, so its
    // concurrent streams are on screen in NEITHER form — an arrival state
    // that opened everything would bury the conclusion it had just printed.
    for (const id of ["tpc-h.stream1", "tpc-h.stream2", "tpc-h.s1.q13"]) {
      expect(names, id).not.toContain(id);
      expect(marks.some((label) => label.startsWith(id)), id).toBe(false);
    }
  });

  it("gives every child the window, never the run total", () => {
    // The zoom is entirely a question of which time domain the children are
    // handed, and at arrival `view === bounds`, so no rendered number can
    // tell them apart. What CAN be seen is that the strip is drawn over the
    // whole run while the tree's bars are placed through the same domain the
    // strip's window rectangle marks out — at arrival, the full width.
    const html = body();
    // The window rectangle covers the whole strip: nothing is zoomed, and the
    // breadcrumb that says otherwise is absent.
    expect(html).toMatch(/data-window="true"[^>]*style="[^"]*left:0%;width:100%/);
    expect(html).not.toContain('data-crumb="true"');
  });

  it("does not mount the dock until something is selected", () => {
    // Nothing is selected on arrival, so the panel has nothing to say —
    // empty, it would be occluding the tree in order to print "select a span".
    const html = body();
    expect(html).not.toContain('data-dock="overlay"');
    expect(html).not.toContain('data-panel="span-dock"');
  });

  it("renders an empty trace as an empty state rather than an empty tree", () => {
    // A run whose trace did not seal is a real case, and a header row over
    // nothing reads as a broken page.
    const html = body({ t0: 0, spans: [] });
    expect(html).not.toContain('data-row="head"');
    expect(html).not.toContain('data-conclusion="true"');
    expect(html).toMatch(/\S/);
  });
});
