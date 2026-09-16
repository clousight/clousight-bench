import { describe, expect, it } from "vitest";

import {
  CLICK_SLOP_PX,
  KEY_STEP_FRACTION,
  nudgeSelection,
  OverviewStrip,
  resolveDrag,
  secondsAtX,
} from "@/features/timeline/OverviewStrip";
import type { Selection } from "@/lib/selection";
import type { SpanRow } from "@/lib/trace";
import { focusableTags, renderMarkup } from "@/test/render";

describe("resolveDrag", () => {
  it("resolves a click with a pixel of tremor to the whole run, discarding the narrow window the tremor built", () => {
    // 1px of press-to-release travel is well inside CLICK_SLOP_PX, but the
    // seconds the tremor produced (12..45) are a real, non-degenerate range —
    // if the fix regressed to strict from!==to equality, this would return
    // null (keep the 12..45 sliver) instead of overriding it.
    const result = resolveDrag(100, 101, 12, 45, 0, 120);
    expect(result).toEqual({ startS: 0, endS: 120 });
  });

  it("leaves a real drag alone: press-to-release travel past the slop returns null", () => {
    // 160px of travel is far past CLICK_SLOP_PX, and from/to are a real,
    // distinct range. The continuous pointermove updates already committed
    // this window, so resolveDrag must not override it on release.
    const result = resolveDrag(100, 260, 10, 90, 0, 120);
    expect(result).toBeNull();
  });

  it("still resolves to a sane single point when totalS is 0, regardless of pixel travel", () => {
    // On a zero-duration trace secondsAtX always returns t0, so from === to
    // even though the press-to-release travel here (150px) would read as a
    // real drag by the pixel test alone. The degenerate-seconds branch must
    // catch this independently of the click/drag pixel distinction.
    const result = resolveDrag(50, 200, 5, 5, 5, 0);
    expect(result).toEqual({ startS: 5, endS: 5 });
  });

  it("treats exactly CLICK_SLOP_PX of travel as a click (the boundary is inclusive)", () => {
    const result = resolveDrag(100, 100 + CLICK_SLOP_PX, 12, 45, 0, 120);
    expect(result).toEqual({ startS: 0, endS: 120 });
  });
});

describe("secondsAtX", () => {
  it("maps a clientX to the proportional second within the strip", () => {
    // rect [100, 300), width 200; clientX 150 is 25% across.
    expect(secondsAtX(150, { left: 100, width: 200 }, 10, 40)).toBeCloseTo(20, 6);
  });

  it("clamps to t0/totalS bounds rather than extrapolating past the strip", () => {
    expect(secondsAtX(0, { left: 100, width: 200 }, 10, 40)).toBe(10);
    expect(secondsAtX(9999, { left: 100, width: 200 }, 10, 40)).toBe(50);
  });

  it("degrades to t0 instead of NaN when the strip has zero width (not yet laid out)", () => {
    expect(secondsAtX(150, { left: 100, width: 0 }, 10, 40)).toBe(10);
  });

  it("resolves every pixel of a zero-length trace to t0", () => {
    // This is arithmetic, not a guard: scaling any ratio by a zero-length
    // domain lands on t0. It is stated as a test because `resolveDrag`'s
    // degenerate branch depends on it — on such a trace press and release
    // must produce `from === to` however far the pointer travelled — and NOT
    // as a guard, because `secondsAtX` used to carry a `totalS <= 0` branch
    // returning exactly this same answer. A test cannot distinguish the two,
    // which is why the branch is gone and this comment says which one is
    // being checked.
    expect(secondsAtX(150, { left: 100, width: 200 }, 10, 0)).toBe(10);
    expect(secondsAtX(9999, { left: 100, width: 200 }, 10, 0)).toBe(10);
  });
});

describe("nudgeSelection", () => {
  // A 10 s run, window 4..6 s. KEY_STEP_FRACTION of 10 s is 0.5 s, so every
  // expectation below is a number this test can name rather than recompute
  // from the constant — a step that silently became 0 would still satisfy
  // "the answer is start + step".
  const t0 = 0;
  const totalS = 10;
  const ids = new Set(["lane:0:0"]);
  const window: Selection = { startS: 4, endS: 6, trackIds: ids };

  it("takes a step that actually moves the window", () => {
    expect(KEY_STEP_FRACTION * totalS).toBe(0.5);
  });

  it("slides the window right at its current width", () => {
    expect(nudgeSelection(window, "ArrowRight", false, t0, totalS)).toMatchObject({
      startS: 4.5,
      endS: 6.5,
    });
  });

  it("slides it left by the same step", () => {
    expect(nudgeSelection(window, "ArrowLeft", false, t0, totalS)).toMatchObject({
      startS: 3.5,
      endS: 5.5,
    });
  });

  it("stops a slide at the run's edge without squashing the window", () => {
    // Against the start: an unclamped slide would put the window at
    // -0.25..1.75, a quarter of it outside the run; clamping the START rather than
    // clamping each edge separately is what keeps the 2 s width intact.
    const atStart: Selection = { startS: 0.25, endS: 2.25, trackIds: ids };
    expect(nudgeSelection(atStart, "ArrowLeft", false, t0, totalS)).toMatchObject({
      startS: 0,
      endS: 2,
    });
    // And against the end.
    const atEnd: Selection = { startS: 7.75, endS: 9.75, trackIds: ids };
    expect(nudgeSelection(atEnd, "ArrowRight", false, t0, totalS)).toMatchObject({
      startS: 8,
      endS: 10,
    });
  });

  it("extends only the far edge when shift is held", () => {
    // The distinguishing property: the start does NOT move. A handler that
    // slid the window here would give 4.5..6.5 and pass any assertion that
    // only looked at the end.
    expect(nudgeSelection(window, "ArrowRight", true, t0, totalS)).toMatchObject({
      startS: 4,
      endS: 6.5,
    });
    expect(nudgeSelection(window, "ArrowLeft", true, t0, totalS)).toMatchObject({
      startS: 4,
      endS: 5.5,
    });
  });

  it("collapses an extend to a point rather than inverting the window", () => {
    // Four shift-lefts from a 2 s window cross the start. The window becomes
    // empty, which is a thing a reader can see and widen back out of; an
    // inverted one is not.
    const narrow: Selection = { startS: 4, endS: 4.2, trackIds: ids };
    const result = nudgeSelection(narrow, "ArrowLeft", true, t0, totalS);
    expect(result).toMatchObject({ startS: 4, endS: 4 });
  });

  it("returns null for a key it does not handle, so the browser keeps it", () => {
    // Tab is the reason this matters: swallowing it would make the strip a
    // focus trap, which is a worse accessibility defect than the one the
    // keyboard route fixes.
    for (const key of ["Tab", "ArrowUp", "ArrowDown", "Escape", "a", " "]) {
      expect(nudgeSelection(window, key, false, t0, totalS), key).toBeNull();
      expect(nudgeSelection(window, key, true, t0, totalS), key).toBeNull();
    }
  });

  it("passes the track filter through by identity", () => {
    // Every pane memoises on this Set's identity; rebuilding it would redraw
    // ~900 lane marks on a keystroke that did not touch the track filter.
    expect(nudgeSelection(window, "ArrowRight", false, t0, totalS)?.trackIds).toBe(ids);
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
  const rows = [span("a", 0, 2), span("b", 4, 6)];
  const selection: Selection = { startS: 2.5, endS: 7.5, trackIds: new Set(["lane:0:0"]) };
  const noop = () => {};
  const markup = renderMarkup(
    <OverviewStrip rows={rows} t0={0} totalS={10} selection={selection} onChange={noop} />,
  );

  it("is reachable from the keyboard at all", () => {
    // The largest control on the page had no tabIndex, no key handlers, and
    // every mark inside aria-hidden, so choosing a time window — the feature's
    // primary interaction — was mouse-only. What a string render can see is
    // the way in; that the arrow keys then do the right thing is
    // `nudgeSelection`'s own tests above.
    expect(markup).toMatch(/role="img"[^>]*tabindex="0"|tabindex="0"[^>]*role="img"/);
  });

  it("states where the window is, not only how to drag it", () => {
    // 2.5 -> 7.5 s of a 10 s run, read out in run-relative seconds. A label
    // that only said "Drag to select a range" told a screen-reader user
    // nothing about what was currently selected.
    expect(markup).toContain("2.50s – 7.50s of 10.0s");
  });

  it("announces the window as it changes", () => {
    expect(markup).toMatch(/aria-live="polite"[^>]*>5\.00s/);
  });

  it("does not let a drag start a native text selection", () => {
    // Without `select-none`, dragging highlights the labels around the strip.
    // A CDP-driven drag never does, which is why two browser passes missed it.
    expect(markup).toMatch(/class="[^"]*select-none[^"]*"/);
  });


  it("places the window at exactly the percentages the lanes use", () => {
    // 2.5 -> 7.5 s of a 10 s trace is left 25%, width 50%. TrackList's own
    // test asserts the same two numbers for the same window, from the same
    // `pctOf` — which is the point: the strip used to carry two private
    // copies of that arithmetic (one inside its marks memo, one at module
    // scope) under a comment claiming it and TrackList "cannot disagree by
    // construction". Span "a" [0,2] is the 20% mark that proves the marks
    // read the same function as the overlay.
    expect(markup).toContain("left:0%;width:20%");
    expect(markup).toContain("left:25%;width:50%");
  });

  it("gives every focusable element the app's focus ring, not the UA outline", () => {
    // Deferred #9 and #28 were one defect counted twice: the strip's reset
    // button had no ring, and the timeline feature as a whole fell through to
    // the user agent's default outline while every other control in the app
    // used the --ring token. Asserted per element rather than over the markup,
    // because one ringed element otherwise satisfies the whole string.
    const tags = focusableTags(markup);
    expect(tags.length, "nothing focusable rendered — is this still the right markup?").toBeGreaterThan(0);
    for (const tag of tags) expect(tag, tag).toContain("focus-visible:ring-ring");
  });
});
