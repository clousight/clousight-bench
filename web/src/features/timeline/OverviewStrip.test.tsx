import { describe, expect, it } from "vitest";

import {
  CLICK_SLOP_PX,
  KEY_PAN_FRACTION,
  KEY_ZOOM_FACTOR,
  keyView,
  OverviewStrip,
  resolveDrag,
  secondsAtX,
} from "@/features/timeline/OverviewStrip";
import type { SpanRow } from "@/lib/trace";
import { MIN_SPAN_S, spanS, type Viewport } from "@/lib/viewport";
import { focusableTags, renderMarkup } from "@/test/render";

/** The whole run. Every gesture resolves against this, never against the
 * window currently on screen — see the `keyView` tests below. */
const BOUNDS: Viewport = { startS: 0, endS: 100 };

describe("secondsAtX", () => {
  it("maps a pixel to the second under it, across the WHOLE run", () => {
    // rect [100, 300), width 200; clientX 150 is 25% across a strip that
    // always draws bounds — so 25% of [10, 50) is 20s.
    expect(secondsAtX(150, { left: 100, width: 200 }, { startS: 10, endS: 50 })).toBeCloseTo(20, 6);
  });

  it("clamps to the run's ends rather than extrapolating past the strip", () => {
    expect(secondsAtX(0, { left: 100, width: 200 }, { startS: 10, endS: 50 })).toBe(10);
    expect(secondsAtX(9999, { left: 100, width: 200 }, { startS: 10, endS: 50 })).toBe(50);
  });

  it("degrades to the run's start instead of NaN when the strip has no width yet", () => {
    expect(secondsAtX(150, { left: 100, width: 0 }, { startS: 10, endS: 50 })).toBe(10);
  });
});

describe("resolveDrag", () => {
  it("zooms to the two seconds the drag spanned, in either direction", () => {
    // This is the behaviour the whole redesign exists for: a drag produces a
    // NEW WINDOW, not a filter over the old one. Dragged right-to-left, so an
    // implementation that forgot to order its endpoints returns {45, 12}.
    expect(resolveDrag(260, 100, 45, 12, BOUNDS)).toEqual({ startS: 12, endS: 45 });
  });

  it("resolves a click with a pixel of tremor back to the whole run", () => {
    // 1px of press-to-release travel is inside CLICK_SLOP_PX, but the seconds
    // the tremor produced (12..45) are a real range — a strict `from !== to`
    // test would zoom to that sliver instead of resetting.
    expect(resolveDrag(100, 101, 12, 45, BOUNDS)).toEqual(BOUNDS);
  });

  it("treats exactly CLICK_SLOP_PX of travel as a click (the boundary is inclusive)", () => {
    expect(resolveDrag(100, 100 + CLICK_SLOP_PX, 12, 45, BOUNDS)).toEqual(BOUNDS);
  });

  it("resets rather than committing a point when every pixel resolves to one instant", () => {
    // A not-yet-measured strip: 150px of travel by the pixel test, but both
    // ends land on the same second. Zooming there would blank the view.
    expect(resolveDrag(50, 200, 5, 5, BOUNDS)).toEqual(BOUNDS);
  });

  it("refuses to collapse a fast drag below a usable window", () => {
    const zoomed = resolveDrag(100, 300, 50, 50 + MIN_SPAN_S / 10, BOUNDS);
    expect(spanS(zoomed)).toBeGreaterThanOrEqual(MIN_SPAN_S);
  });
});

describe("keyView", () => {
  // A 10s window sitting in the middle of a 100s run. Every expectation below
  // is a number this test names outright rather than recomputing from the
  // constants — a pan step that silently became 0 would still satisfy
  // "the answer is startS + step".
  const view: Viewport = { startS: 40, endS: 50 };

  it("takes steps big enough to see", () => {
    expect(KEY_PAN_FRACTION * spanS(view)).toBe(2.5);
    expect(KEY_ZOOM_FACTOR).toBe(2);
  });

  it("W zooms in about the window's centre", () => {
    expect(keyView(view, BOUNDS, "w")).toEqual({ startS: 42.5, endS: 47.5 });
  });

  it("S zooms out about the window's centre", () => {
    // Wider than the window it was given — which is only possible because the
    // clamp is against `bounds`. Pass `view` there and this stays 40..50.
    expect(keyView(view, BOUNDS, "s")).toEqual({ startS: 35, endS: 55 });
  });

  it("A pans left by a quarter of the window", () => {
    expect(keyView(view, BOUNDS, "a")).toEqual({ startS: 37.5, endS: 47.5 });
  });

  it("D pans right by a quarter of the window", () => {
    // Same ruling as above: panning is clamped into the RUN. Clamped into the
    // window being panned, a pan can never leave it, and no unit test of
    // `nudge` alone would show it.
    expect(keyView(view, BOUNDS, "d")).toEqual({ startS: 42.5, endS: 52.5 });
  });

  it("pans and zooms out of the window it was given, up to the whole run", () => {
    // Four zoom-outs from a 10s window is 160s of width, wider than the run:
    // it must land on exactly the run, not overshoot it and not stall at 10s.
    let out = view;
    for (let press = 0; press < 4; press += 1) out = keyView(out, BOUNDS, "s") ?? out;
    expect(out).toEqual(BOUNDS);
  });

  it("answers to the shifted letters too, since W/S/A/D are letters", () => {
    expect(keyView(view, BOUNDS, "W")).toEqual({ startS: 42.5, endS: 47.5 });
    expect(keyView(view, BOUNDS, "D")).toEqual({ startS: 42.5, endS: 52.5 });
  });

  it("returns null for every key it does not own, so the browser keeps them", () => {
    // Tab is why this matters: swallowing it makes the strip a focus trap.
    // Backspace is in this list on purpose — it pops the zoom stack, which is
    // history rather than arithmetic, so the component handles it (and still
    // calls preventDefault, or the browser navigates back).
    for (const key of ["Tab", "Escape", "Backspace", "ArrowLeft", "ArrowRight", " ", "q"]) {
      expect(keyView(view, BOUNDS, key), key).toBeNull();
    }
  });
});

describe("<OverviewStrip>", () => {
  function span(id: string, startS: number, endS: number): SpanRow {
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
    };
  }
  const rows = [span("a", 0, 20), span("b", 40, 60)];
  const noop = () => {};

  function strip(view: Viewport): string {
    return renderMarkup(
      <OverviewStrip rows={rows} bounds={BOUNDS} view={view} onView={noop} />,
    );
  }

  /** The style of the window rectangle — the one element that moves. */
  function windowStyle(markup: string): string {
    const match = /<span[^>]*data-window="true"[^>]*style="([^"]*)"/.exec(markup);
    expect(match, "no window rectangle rendered — is this still the right markup?").not.toBeNull();
    return match?.[1] ?? "";
  }

  /** Every span mark's style, in document order. */
  function markStyles(markup: string): string[] {
    return [...markup.matchAll(/<span[^>]*data-mark="true"[^>]*style="([^"]*)"/g)].map(
      (match) => match[1],
    );
  }

  it("draws the window rectangle at the view's position within the run", () => {
    const html = strip({ startS: 50, endS: 60 });
    expect(html).toContain("left:50%");
    expect(html).toContain("width:10%");
  });

  it("is focusable and announces the window it is showing", () => {
    const html = strip({ startS: 50, endS: 60 });
    expect(html).toContain('tabindex="0"');
    expect(html).toMatch(/aria-label="[^"]*50/);
  });

  it("is a minimap: zooming moves the window rectangle and leaves the run alone", () => {
    // The defining property, and the one a reviewer cannot check by reading
    // the diff: the marks are placed through `bounds`, so they are identical
    // at every zoom level, while the rectangle over them moves. Place the
    // marks through `view` instead — the intuitive mistake, since every other
    // view in the redesign does exactly that — and the two mark lists diverge.
    const whole = strip(BOUNDS);
    const zoomed = strip({ startS: 50, endS: 60 });
    expect(markStyles(whole)).toHaveLength(rows.length);
    expect(markStyles(zoomed)).toEqual(markStyles(whole));
    expect(windowStyle(zoomed)).not.toBe(windowStyle(whole));
  });

  it("places each mark at its share of the whole run", () => {
    // Span "a" [0,20] of a 100s run is left 0%, width 20%; "b" [40,60] is
    // left 40%. Read off `place(bounds, …)`, the same function the rows below
    // use — so the strip and the lanes cannot disagree by construction.
    const styles = markStyles(strip(BOUNDS));
    expect(styles[0]).toContain("left:0%;width:20%");
    expect(styles[1]).toContain("left:40%;width:20%");
  });

  it("offers a way back to the whole run only while there is one", () => {
    // The reset is the escape hatch from a drag that landed somewhere
    // unreadable; shown unzoomed it is a control that does nothing.
    expect(strip({ startS: 50, endS: 60 })).toContain("Whole run");
    expect(strip(BOUNDS)).not.toContain("Whole run");
  });

  it("does not let a drag start a native text selection", () => {
    // Without `select-none`, dragging highlights the labels around the strip.
    // A CDP-driven drag never does, which is why two browser passes missed it.
    expect(strip(BOUNDS)).toMatch(/class="[^"]*select-none[^"]*"/);
  });

  it("gives every focusable element the app's focus ring, not the UA outline", () => {
    const tags = focusableTags(strip({ startS: 50, endS: 60 }));
    expect(tags.length, "nothing focusable rendered — is this still the right markup?").toBeGreaterThan(0);
    for (const tag of tags) expect(tag, tag).toContain("focus-visible:ring-ring");
  });
});
