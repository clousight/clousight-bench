import { describe, expect, it } from "vitest";

import { NAME_COL_PX, NUM_COL_PX, overlapS, ROW_PX, TraceTree } from "@/features/trace/TraceTree";
import { buildTree, flatten, type VisibleRow } from "@/lib/rowmodel";
import type { SpanRow } from "@/lib/trace";
import type { Viewport } from "@/lib/viewport";
import { focusableTags, renderMarkup } from "@/test/render";

/** A full `SpanRow`, built per call so no two tests share an object. */
function row(
  id: string,
  startS: number,
  endS: number,
  parentId: string | null = null,
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
    parentId,
    depth: 0,
    ancestors: [],
    ...extra,
  };
}

/**
 * Rows the way the app produces them: through the real `buildTree`/`flatten`,
 * never hand-assembled. A hand-built `VisibleRow` could carry a `lanes` array
 * the row model would never have produced, and the lane assertions below
 * would then be testing the fixture rather than the pipeline.
 */
function visible(spans: SpanRow[], expanded: string[] = []): VisibleRow[] {
  return flatten(buildTree(spans), new Set(expanded));
}

const noop = () => {};

function tree(rows: VisibleRow[], view: Viewport, selectedId: string | null = null) {
  return renderMarkup(
    <TraceTree rows={rows} view={view} onToggle={noop} selectedId={selectedId} onSelect={noop} />,
  );
}

/** Every opening tag carrying `data-col="<name>"`, in document order. The
 * column geometry is per element, so asserting over the whole markup string
 * would be satisfied by any ONE cell being right — the mistake `focusableTags`
 * exists to prevent for focus rings. */
function colTags(markup: string, col: string): string[] {
  return [...markup.matchAll(new RegExp(`<div data-col="${col}"[^>]*>`, "g"))].map(
    (match) => match[0],
  );
}

describe("overlapS", () => {
  it("is the part of the span the window actually contains", () => {
    expect(overlapS({ startS: 11.1, endS: 12.9 }, 12.201, 18.771)).toBeCloseTo(0.699, 6);
  });

  it("is zero, never negative, for a span entirely outside the window", () => {
    expect(overlapS({ startS: 5, endS: 6 }, 1, 2)).toBe(0);
  });

  it("is the whole span when the window contains it", () => {
    expect(overlapS({ startS: 0, endS: 10 }, 2, 5)).toBe(3);
  });
});

describe("<TraceTree>", () => {
  it("caps the name column so the lane gets the width", () => {
    // The old view gave the name column flex:1 — 1062px of a 1440px row to
    // hold 250px of text — leaving the lane 16%. That is 4.23x narrower marks.
    // Asserted per cell, and paired with the lane's `flex-1`: a name column
    // that is capped while the lane is ALSO fixed would hand the width to
    // nothing, which the cap exists to prevent.
    const rows = visible([row("p", 0, 10), row("c", 1, 2, "p")], ["p"]);
    const html = tree(rows, { startS: 0, endS: 10 });

    const names = colTags(html, "name");
    // The header plus one cell per row — a cap that only reached the header
    // would leave every actual row uncapped.
    expect(names).toHaveLength(rows.length + 1);
    for (const cell of names) expect(cell, cell).toContain(`width:${NAME_COL_PX}px`);

    const lanes = colTags(html, "lane");
    expect(lanes).toHaveLength(rows.length + 1);
    for (const cell of lanes) expect(cell, cell).toContain("flex-1");
  });

  it("gives the two numeric columns a fixed width so the lane edge does not move", () => {
    // A column that sizes to its content puts a 6.57s row and a 699ms row on
    // two different lane origins, and the bars below each other stop being
    // comparable — which is the whole point of a shared time axis.
    const rows = visible([row("a", 0, 1), row("b", 2, 9)]);
    const html = tree(rows, { startS: 0, endS: 10 });
    for (const col of ["window", "total"]) {
      const cells = colTags(html, col);
      expect(cells).toHaveLength(rows.length + 1);
      for (const cell of cells) expect(cell, cell).toContain(`width:${NUM_COL_PX}px`);
    }
  });

  it("prints the window value and the true total in separate columns", () => {
    // Never one bolded number that silently means the clipped value. The span
    // runs 12.201 -> 18.771 (6.57s); the window holds 12.201 -> 12.9 of it
    // (699ms). Both numbers are on screen, each in the column that says what
    // it means, so "this moved when I zoomed" is never ambiguous.
    const rows = visible([row("tpc-h.load", 12.201, 18.771)]);
    const html = tree(rows, { startS: 11.1, endS: 12.9 });
    expect(html).toMatch(/data-col="window"[^>]*>699ms</);
    expect(html).toMatch(/data-col="total"[^>]*>6\.57s</);
  });

  it("marks a bar the window cut", () => {
    // [5,15] against a [10,20] window: cut at the start, complete at the end.
    // The flags are separate because a bar cut at both edges (the root span,
    // at every zoom) has to say so twice.
    const html = tree(visible([row("straddles", 5, 15)]), { startS: 10, endS: 20 });
    expect(html).toMatch(/data-clipped-start="true"/);
    expect(html).not.toMatch(/data-clipped-end="true"/);
    // And the cut edge carries a visible cap, not just an attribute a test
    // can read — the whole reason this exists is that a clipped bar otherwise
    // looks like a bar that ended there.
    expect(html).toMatch(/data-cut="start"/);
    expect(html).not.toMatch(/data-cut="end"/);
  });

  it("draws idle time as its own segment", () => {
    // A 10s parent whose one child covers 2s has 8s nothing accounts for.
    // The old view hid it, so containers read "self 0ms" with nowhere for
    // their duration to go.
    const withIdle = visible([row("p", 0, 10), row("c", 1, 3, "p")], ["p"]);
    expect(tree(withIdle, { startS: 0, endS: 10 })).toMatch(/data-idle="true"/);

    // A parent its children cover completely has no idle segment to draw.
    const noIdle = visible([row("p", 0, 10), row("c", 0, 10, "p")], ["p"]);
    expect(tree(noIdle, { startS: 0, endS: 10 })).not.toMatch(/data-idle="true"/);
  });

  it("never hatches a leaf, which is working rather than waiting", () => {
    // Most of a trace is leaves. This used to be true because the view
    // suppressed the segment for a childless node while the model still
    // reported its whole duration as idle; the model returns 0 now and the
    // view draws what it is given, so the assertion reads the same and means
    // something different. Both halves are here because either alone can pass
    // while the other is wrong.
    const leaf = visible([row("only", 0, 10)]);
    expect(leaf[0].idleS).toBe(0);
    expect(tree(leaf, { startS: 0, endS: 10 })).not.toMatch(/data-idle="true"/);
  });

  it("never hatches a bar the window cut, because idle is a total-domain number", () => {
    // `idleS / durationS` is a fraction of the span's WHOLE extent; the box it
    // paints into is whatever the window left of the bar. Multiplying the two
    // is `pctOf(total)` — the defect this redesign exists to remove — wearing
    // a hatch.
    //
    // A [0,10] parent whose one child covers [0,2], seen through the window
    // [0,2]: the bar fills the lane and the real unaccounted time inside that
    // window is ZERO, but the total-domain ratio is 8/10 and painted 80% of
    // it. Nothing in the suite saw this until the review reproduced it.
    const rows = visible([row("p", 0, 10), row("c", 0, 2, "p")], ["p"]);
    const clipped = tree(rows, { startS: 0, endS: 2 });
    expect(clipped).toMatch(/data-clipped-end="true"/);
    expect(clipped).not.toMatch(/data-idle="true"/);

    // The same rows, unclipped, still draw it — the suppression is about the
    // window cutting the bar, not about quietly deleting the feature.
    expect(tree(rows, { startS: 0, endS: 10 })).toMatch(/data-idle="true"/);
  });

  it("renders a concurrent node's children as lanes, sequential ones as rows", () => {
    // Three children that overlap pack into more than one lane; twenty that
    // do not stay ordinary rows. The node decides, not a global mode — which
    // is `rowmodel.flatten`'s call, read here rather than re-made.
    const concurrent = visible(
      [row("p", 0, 10), row("s1", 0, 8, "p"), row("s2", 1, 9, "p"), row("s3", 2, 10, "p")],
      ["p"],
    );
    expect(tree(concurrent, { startS: 0, endS: 10 })).toMatch(/data-lane="1"/);

    const sequential = visible(
      [row("p", 0, 10), row("a", 0, 3, "p"), row("b", 3, 6, "p"), row("c", 6, 9, "p")],
      ["p"],
    );
    expect(tree(sequential, { startS: 0, endS: 10 })).not.toMatch(/data-lane=/);
  });

  it("does not draw a lane node's children twice", () => {
    // `flatten` emits the children of an expanded node as rows whether or not
    // it also packed them into lanes. Drawing both would put every stream on
    // screen twice, once as a lane and once as a row underneath it.
    const concurrent = visible(
      [row("p", 0, 10), row("s1", 0, 8, "p"), row("s2", 1, 9, "p")],
      ["p"],
    );
    const html = tree(concurrent, { startS: 0, endS: 10 });
    expect([...html.matchAll(/title="s1"/g)]).toHaveLength(1);
  });

  it("makes the disclosure a real button that announces its state", () => {
    // A div with onClick is unreachable by keyboard and silent to a screen
    // reader; this repo spent a whole fix round on exactly that shape in
    // `TablePane`.
    const collapsed = tree(visible([row("p", 0, 10), row("c", 1, 2, "p")]), { startS: 0, endS: 10 });
    expect(collapsed).toMatch(/<button[^>]*aria-expanded="false"/);

    const expanded = tree(visible([row("p", 0, 10), row("c", 1, 2, "p")], ["p"]), {
      startS: 0,
      endS: 10,
    });
    expect(expanded).toMatch(/<button[^>]*aria-expanded="true"/);

    // A leaf has nothing to disclose, so it gets no control at all rather
    // than a disabled one a keyboard would still have to walk past.
    const leaf = tree(visible([row("only", 0, 10)]), { startS: 0, endS: 10 });
    expect(leaf).not.toMatch(/aria-expanded/);
  });

  it("keeps a clipped name recoverable from its title", () => {
    // 176 characters is the real case: it grew an uncapped cell to 1340px and
    // pushed the numeric columns off screen. The cap is what makes `truncate`
    // fire, and `title` is then the only path back to the full name.
    const long = "a.very.long.span.name".repeat(9);
    const html = tree(visible([row(long, 0, 10)]), { startS: 0, endS: 10 });
    expect(html).toContain(`title="${long}"`);
    expect(html).toMatch(/class="[^"]*truncate/);
  });

  it("gives every focusable element the app's focus ring, not the UA outline", () => {
    // Per element: one ringed control otherwise satisfies an assertion made
    // over the whole string, which is how the overview strip shipped without
    // a ring while the reset button above it covered for the test.
    const rows = visible([row("p", 0, 10), row("c", 1, 2, "p")], ["p"]);
    const tags = focusableTags(tree(rows, { startS: 0, endS: 10 }));
    // One disclosure on the parent, one name button per row.
    expect(tags).toHaveLength(3);
    for (const tag of tags) expect(tag, tag).toContain("focus-visible:ring-ring");
  });

  it("positions every bar through the window, so a zoom actually moves it", () => {
    // The defect the whole redesign exists to remove: `pctOf(totalS)` put
    // every bar at a fraction of the RUN, so dragging could only dim things.
    // Window [4,6] of a 10s run puts span "b" [4,6] across the full lane and
    // span "a" [0,2] two lane-widths off to the left.
    const html = tree(visible([row("a", 0, 2), row("b", 4, 6)]), { startS: 4, endS: 6 });
    const marks = [...html.matchAll(/data-mark="true"[^>]*style="([^"]*)"/g)].map(
      (match) => match[1],
    );
    expect(marks).toEqual([expect.stringContaining("left:0%;width:100%")]);
    // "a" is outside the window entirely, so it has no mark at all — but it
    // keeps its row, because a span the window excludes is still a span.
    expect([...html.matchAll(/data-row="span"/g)]).toHaveLength(2);
  });

  it("keeps every row exactly one row tall", () => {
    // 22px is the density the 300px name cap was measured against; a row that
    // grows with its content breaks the alignment the shared axis depends on.
    const rows = visible([row("p", 0, 10), row("c", 1, 2, "p")], ["p"]);
    const html = tree(rows, { startS: 0, endS: 10 });
    const heights = [...html.matchAll(/data-row="[a-z]+"[^>]*style="([^"]*)"/g)].map(
      (match) => match[1],
    );
    expect(heights).toHaveLength(rows.length + 1);
    for (const style of heights) expect(style, style).toContain(`height:${ROW_PX}px`);
  });
});
