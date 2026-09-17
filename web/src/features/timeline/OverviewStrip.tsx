/**
 * The whole run at a glance, with the current window drawn over it.
 *
 * This is a **minimap**, and that is the deliberate part: the strip always
 * renders `bounds` — every span of the run, at the same place, at every zoom
 * level — and draws `view` as a rectangle over it. What zooms is everything
 * *below* the strip. A minimap that zoomed with the view would be a second
 * copy of the rows, and the reader would lose the only element on the page
 * that says where in the run they currently are. Chrome DevTools and Perfetto
 * both work this way.
 *
 * The consequence for the arithmetic: every gesture here resolves against
 * `bounds`, never against `view`. `zoomTo`/`nudge`/`scale` clamp into the
 * viewport they are handed, so passing `view` would mean a reader who zoomed
 * into one second could never drag back out to a wider window — the strip
 * would be drawing the whole run while refusing to select any of it. That
 * failure is invisible in a unit test of `viewport.ts`, so the pure helpers
 * below take `bounds` explicitly and the tests name the mistake.
 *
 * Dragging is pointer-based rather than a library: the strip is one div, the
 * geometry is one division, and a brush library would be more code than the
 * thing it draws.
 */
import { useCallback, useMemo, useRef } from "react";

import { laneSpanStyle } from "@/charts/palette";
import { useI18n } from "@/i18n";
import { fmtSpanDur } from "@/lib/format";
import type { SpanRow } from "@/lib/trace";
import { back, commitHistory, nudge, place, scale, spanS, zoomTo, type Viewport } from "@/lib/viewport";

interface Props {
  rows: SpanRow[];
  /** The whole run: what the strip draws, and what every gesture clamps into. */
  bounds: Viewport;
  /** The window the rest of the page is showing, drawn as a rectangle. */
  view: Viewport;
  onView: (next: Viewport) => void;
}

/** Pixels of press-to-release travel that still counts as a click, not a
 * drag — the platform convention for the click/drag boundary, not a value we
 * invented for this component. */
export const CLICK_SLOP_PX = 3;

/** How far `A`/`D` pan, as a fraction of the window's own width. A fraction
 * rather than a number of seconds so the gesture means the same thing on a
 * 400ms trace and on a two-hour one: four presses cross the window either
 * way, which is Perfetto's pitch for the same keys. */
export const KEY_PAN_FRACTION = 0.25;

/** How far `W`/`S` zoom per press. Halving and doubling is the one ratio a
 * reader can undo by eye — "one press back" is exactly the window they left. */
export const KEY_ZOOM_FACTOR = 2;

/** The narrowest a mark may be drawn, in pixels. At full-run zoom a 2ms query
 * in a 7s run is 0.03% of the strip, which rounds to nothing: the throughput
 * phase would render as empty space rather than as the dense block it is.
 * Enforced in pixels rather than as a percentage floor because the honest
 * unit here is "can be seen", and that is not a fraction of the run. */
export const MARK_MIN_PX = 2;

/** A cheap stand-in for the one field of DOMRect the geometry needs, so the
 * resolution logic below can be exercised without a DOM. */
export interface StripRect {
  left: number;
  width: number;
}

/**
 * Pixel position -> trace second, given a cached strip rect.
 *
 * `bounds`, because the strip draws the whole run: the second under the
 * pointer is a fact about the run, not about the window currently zoomed to.
 *
 * The one guard is on the rect: a strip that has not been laid out yet has
 * width 0, and the ratio would be a division by zero.
 */
export function secondsAtX(clientX: number, rect: StripRect, bounds: Viewport): number {
  if (rect.width <= 0) return bounds.startS;
  const ratio = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
  return bounds.startS + ratio * spanS(bounds);
}

/**
 * The window a completed drag should commit.
 *
 * Two independent reasons collapse to the same answer, "show the whole run":
 * (1) the release is within `CLICK_SLOP_PX` of the press — a click, however
 * far `fromS`/`toS` wandered on tremor alone, since on a long trace a single
 * pixel of unintentional movement is already a visible sliver of a window;
 * (2) `fromS === toS` regardless of pixel travel — an empty or not-yet-measured
 * strip where every position resolves to the same instant, so "keep what the
 * drag built" would zoom to a point and blank the page.
 *
 * Otherwise it is a zoom, ordered and clamped by `zoomTo`. Note what this
 * signature does NOT take: the current view. A drag selects out of the run,
 * so there is no way to write the "clamp into the window you are already in"
 * bug here without adding a parameter.
 */
export function resolveDrag(
  pressX: number,
  releaseX: number,
  fromS: number,
  toS: number,
  bounds: Viewport,
): Viewport {
  const isClick = Math.abs(releaseX - pressX) <= CLICK_SLOP_PX;
  if (isClick || fromS === toS) return bounds;
  return zoomTo(bounds, fromS, toS);
}

/**
 * The window a key press should commit, or null for a key this strip does not
 * handle — so the caller leaves the event alone rather than swallowing Tab.
 *
 * `W`/`S` zoom and `A`/`D` pan: Perfetto's bindings, borrowed rather than
 * invented, and reachable with one hand while the other is on the pointer.
 * The arrow keys are deliberately not taken — they scroll the page, and this
 * strip sits at the top of a long one.
 *
 * `Backspace` is not here either. Popping the zoom stack is history, not
 * arithmetic: it needs the windows this strip has already committed, which
 * the component holds. A null answer for it is correct and the component
 * still calls `preventDefault`, or the browser navigates back.
 */
export function keyView(view: Viewport, bounds: Viewport, key: string): Viewport | null {
  switch (key.toLowerCase()) {
    case "w":
      return scale(view, 1 / KEY_ZOOM_FACTOR, bounds);
    case "s":
      return scale(view, KEY_ZOOM_FACTOR, bounds);
    case "a":
      return nudge(view, -1, KEY_PAN_FRACTION, bounds);
    case "d":
      return nudge(view, 1, KEY_PAN_FRACTION, bounds);
    default:
      return null;
  }
}

interface DragState {
  pressX: number;
  fromS: number;
  rect: StripRect;
  /** The window in force when the press landed — what `Backspace` returns to,
   * rather than whichever intermediate window the drag was passing through. */
  fromView: Viewport;
}

export function OverviewStrip({ rows, bounds, view, onView }: Props) {
  const { t } = useI18n();
  const stripRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  // The zoom history. Every *operation* on it is pure and lives in
  // `viewport.ts` (`commitHistory`, `back`), where it can be asserted on
  // without a DOM; what lives here is only the ref holding the array, because
  // the gesture that walks it (`Backspace`) is bound here and the component's
  // props carry no way to ask the parent for a pop. Entries are the windows
  // this strip committed — never an intermediate window a drag passed through.
  const historyRef = useRef<Viewport[]>([bounds]);

  const commit = useCallback(
    (from: Viewport, next: Viewport) => {
      historyRef.current = commitHistory(historyRef.current, from, next);
      onView(next);
    },
    [onView],
  );

  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      const el = stripRef.current;
      if (el === null) return;
      // Cached once: reading it per pointermove would lay out the page on
      // every frame of a drag, and the strip cannot move mid-gesture.
      const rect = el.getBoundingClientRect();
      event.currentTarget.setPointerCapture(event.pointerId);
      dragRef.current = {
        pressX: event.clientX,
        fromS: secondsAtX(event.clientX, rect, bounds),
        rect,
        fromView: view,
      };
    },
    [bounds, view],
  );

  const onPointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (drag === null) return;
      // Under the slop the gesture is still a click, and zooming to the
      // sliver a tremor drew would make a click flash the page.
      if (Math.abs(event.clientX - drag.pressX) <= CLICK_SLOP_PX) return;
      onView(zoomTo(bounds, drag.fromS, secondsAtX(event.clientX, drag.rect, bounds)));
    },
    [bounds, onView],
  );

  const endDrag = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      dragRef.current = null;
      if (drag === null) return;
      const toS = secondsAtX(event.clientX, drag.rect, bounds);
      commit(drag.fromView, resolveDrag(drag.pressX, event.clientX, drag.fromS, toS, bounds));
    },
    [bounds, commit],
  );

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      // Leave anything modified to the OS and the browser: Cmd+S saves the
      // page, Ctrl+W closes the tab, and a single letter is not worth either.
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (event.key === "Backspace") {
        // Always ours, even when there is nowhere to go back to: unhandled
        // Backspace on a non-input element still means "back" in some
        // browsers, which would leave the page entirely.
        event.preventDefault();
        const previous = back(historyRef.current, view);
        historyRef.current = previous.stack;
        if (previous.view !== null) onView(previous.view);
        return;
      }
      const next = keyView(view, bounds, event.key);
      if (next === null) return;
      // Only once a key was ours: Tab and Escape must stay the browser's.
      event.preventDefault();
      commit(view, next);
    },
    [bounds, commit, onView, view],
  );

  const reset = useCallback(() => commit(view, bounds), [bounds, commit, view]);

  const windowS = spanS(view);
  const totalS = spanS(bounds);
  const zoomed = windowS < totalS - 1e-9;
  const windowRect = place(bounds, view.startS, view.endS);
  // Where the window is, not just how to move it: a screen reader landing on
  // the strip gets the current window in run-relative seconds. Composed here
  // rather than through an interpolated message because `t()` has no
  // interpolation, so the alternative is a dictionary of sentence fragments.
  const rangeLabel = `${t("timeline.zoom_hint")}: ${fmtSpanDur(view.startS - bounds.startS)} – ${fmtSpanDur(
    view.endS - bounds.startS,
  )} ${t("timeline.of")} ${fmtSpanDur(totalS)}`;

  // Colour comes from `laneSpanStyle` and geometry from `place` — the same two
  // functions every row below uses (the strip calls the first with `selected`
  // pinned to `true`: it has no per-track checkbox, so it always shows
  // everything at the "included" strength). Importing both rather than
  // re-deriving matching constants here is what makes "the strip and the rows
  // cannot disagree" a fact rather than a claim.
  //
  // Memoised on `bounds`, not on `view`: these marks are the run, and the run
  // does not move when the window does. That is what makes dragging cheap —
  // a pointermove rebuilds one rectangle, not every mark.
  const spanMarks = useMemo(() => {
    return rows.map((row) => {
      const placed = place(bounds, row.startS, row.endS);
      if (!placed.visible) return null;
      return (
        <span
          key={row.id}
          aria-hidden
          data-mark="true"
          data-kind={row.kind}
          className="absolute top-1 h-1.5 rounded-[1px]"
          style={{
            left: `${placed.leftPct}%`,
            width: `${placed.widthPct}%`,
            minWidth: MARK_MIN_PX,
            ...laneSpanStyle(row.kind, row.isError, true),
          }}
        />
      );
    });
  }, [rows, bounds]);

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline gap-2 font-mono text-[11px] text-muted-foreground">
        <span className="uppercase tracking-[0.1em]">{t("timeline.window")}</span>
        {/* This line is already the window stated in words, so it is also the
            announcement: a keyboard user zooming hears the new duration from
            the element that was going to change anyway, rather than from a
            second live region duplicating it. `aria-live` sits on the value
            alone — wrapping the row would re-announce the reset button's
            label every time the window moved. */}
        <span aria-live="polite" className="font-mono tabular-nums text-foreground">
          {fmtSpanDur(windowS)}
        </span>
        <span>{t("timeline.of")}</span>
        <span className="font-mono tabular-nums">{fmtSpanDur(totalS)}</span>
        {zoomed && (
          <button
            type="button"
            onClick={reset}
            className="ml-auto border-b-2 border-transparent uppercase tracking-[0.06em] transition-colors hover:border-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {t("timeline.reset")}
          </button>
        )}
      </div>

      {/* `role="img"` stays. It was chosen over `role="slider"` deliberately:
          a slider has one value and this has two, and lying about the role
          buys a promise of slider keys that a two-ended control cannot keep.
          What it needs instead is a way in — tabIndex, a key handler, and an
          aria-label that states the window rather than only how to drag it.

          `select-none` because a drag across the strip otherwise starts a
          native text selection over the labels around it. A CDP-driven drag
          never does that, which is why two browser passes missed it. */}
      <div
        ref={stripRef}
        role="img"
        tabIndex={0}
        aria-label={rangeLabel}
        onKeyDown={onKeyDown}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        className="relative h-7 cursor-col-resize touch-none select-none overflow-hidden border-y border-border bg-muted/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {spanMarks}
        <span
          aria-hidden
          data-window="true"
          className="absolute inset-y-0 border-x border-foreground bg-foreground/[0.06]"
          style={{
            left: `${windowRect.leftPct}%`,
            width: `${windowRect.widthPct}%`,
            minWidth: 3,
          }}
        />
      </div>

      <div className="flex justify-between font-mono text-[10px] tabular-nums text-muted-foreground">
        <span>0</span>
        <span>{fmtSpanDur(totalS)}</span>
      </div>
    </div>
  );
}
