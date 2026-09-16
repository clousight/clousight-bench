/**
 * The viewport: an explicit time window that everything in the trace view
 * renders through, and the foundation the rest of the redesign builds on.
 *
 * The shipped design positioned every bar with `pctOf(totalS)` — a percentage
 * of the *whole run* — and made a selection "zoom" by filtering spans that
 * overlapped it, dimming the rest. That can never actually narrow the view:
 * the root span overlaps every window by construction, so it (and the full
 * width it occupies) never disappears, no matter how tight the selection.
 * Filtering changes which spans are *emphasized*; it cannot change what
 * fraction of the screen a second of time occupies.
 *
 * A viewport fixes this by making the denominator explicit. Every position on
 * screen is `place(viewport, span.startS, span.endS)`, a fraction of
 * `viewport.endS - viewport.startS`, not of the run total. Zooming just
 * replaces the viewport with a narrower one (`zoomTo`); the same `place` call
 * then spreads a 2-second span across the whole width. Do not reintroduce
 * `pctOf(totalS)` or overlap-filtering here — they are the bug this module
 * exists to remove.
 */

import type { SpanRow } from "@/lib/trace";

/** The smallest window (in seconds) we will ever show or zoom to. Below this,
 * `toPct` would be dividing by (near) zero. */
export const MIN_SPAN_S = 0.001;

export interface Viewport {
  startS: number;
  endS: number;
}

/** Window width in seconds; never negative even if inputs are out of order. */
export function spanS(v: Viewport): number {
  return Math.max(0, v.endS - v.startS);
}

/** The window covering every row, root-first — the initial, unzoomed view.
 * Falls back to a `MIN_SPAN_S`-wide window when every row degrades to the
 * same instant (or there are no rows), so callers never divide by zero. */
export function fullViewport(rows: SpanRow[], t0: number): Viewport {
  if (rows.length === 0) {
    return { startS: t0, endS: t0 + MIN_SPAN_S };
  }
  let startS = Infinity;
  let endS = -Infinity;
  for (const row of rows) {
    if (row.startS < startS) startS = row.startS;
    if (row.endS > endS) endS = row.endS;
  }
  if (endS - startS < MIN_SPAN_S) {
    return { startS, endS: startS + MIN_SPAN_S };
  }
  return { startS, endS };
}

/** Seconds -> 0..100 within the window. Guards a collapsed/degenerate
 * viewport by substituting `MIN_SPAN_S` for the divisor. */
export function toPct(v: Viewport, s: number): number {
  const width = spanS(v) > 0 ? spanS(v) : MIN_SPAN_S;
  return ((s - v.startS) / width) * 100;
}

export interface Placed {
  leftPct: number;
  widthPct: number;
  clippedStart: boolean;
  clippedEnd: boolean;
  visible: boolean;
}

/** Where a span [startS, endS) lands within the current window: a left/width
 * percentage pair, plus whether either edge was cut off by the window (as
 * opposed to the span's own extent). A span entirely outside the window is
 * `visible: false`; a zero-duration span inside the window still gets a
 * visible hairline so it stays clickable. */
export function place(v: Viewport, startS: number, endS: number): Placed {
  const clippedStart = startS < v.startS;
  const clippedEnd = endS > v.endS;

  const clampedStart = Math.max(startS, v.startS);
  const clampedEnd = Math.min(endS, v.endS);

  if (clampedEnd < clampedStart) {
    return { leftPct: 0, widthPct: 0, clippedStart, clippedEnd, visible: false };
  }

  const leftPct = toPct(v, clampedStart);
  const width = spanS(v) > 0 ? spanS(v) : MIN_SPAN_S;
  const minWidthPct = (MIN_SPAN_S / width) * 100;
  const widthPct = Math.max(toPct(v, clampedEnd) - leftPct, minWidthPct);

  return { leftPct, widthPct, clippedStart, clippedEnd, visible: true };
}

/** A new window from a drag's two endpoints, in either order. Refuses to
 * collapse below `MIN_SPAN_S`, and clamps into the viewport being dragged
 * within — a selection cannot zoom to somewhere outside what was on screen. */
export function zoomTo(v: Viewport, a: number, b: number): Viewport {
  let startS = Math.min(a, b);
  let endS = Math.max(a, b);
  if (endS - startS < MIN_SPAN_S) {
    const mid = (startS + endS) / 2;
    startS = mid - MIN_SPAN_S / 2;
    endS = mid + MIN_SPAN_S / 2;
  }
  return clampWindow(startS, endS, v);
}

/** Clamp a candidate window into `bounds`, preserving its width by shifting
 * rather than shrinking — and only shrinking (to `bounds` itself) when the
 * candidate is wider than `bounds` allows. */
function clampWindow(startS: number, endS: number, bounds: Viewport): Viewport {
  let s = startS;
  let e = endS;

  if (s < bounds.startS) {
    const delta = bounds.startS - s;
    s += delta;
    e += delta;
  }
  if (e > bounds.endS) {
    const delta = e - bounds.endS;
    s -= delta;
    e -= delta;
  }
  // Wider than bounds: clip both edges to bounds rather than shift forever.
  if (s < bounds.startS) s = bounds.startS;
  if (e > bounds.endS) e = bounds.endS;

  return { startS: s, endS: e };
}

/** Pan the window by `frac` of its own width, in direction `dir`, staying
 * inside `bounds` without changing width. */
export function nudge(v: Viewport, dir: -1 | 1, frac: number, bounds: Viewport): Viewport {
  const shift = dir * frac * spanS(v);
  return clampWindow(v.startS + shift, v.endS + shift, bounds);
}

/** Zoom about the window's centre by `factor` (< 1 narrows, > 1 widens),
 * clamped so the result never exceeds `bounds`. */
export function scale(v: Viewport, factor: number, bounds: Viewport): Viewport {
  const centre = (v.startS + v.endS) / 2;
  const boundsWidth = spanS(bounds);
  let width = spanS(v) * factor;
  if (width > boundsWidth) width = boundsWidth;
  if (width < MIN_SPAN_S) width = MIN_SPAN_S;

  return clampWindow(centre - width / 2, centre + width / 2, bounds);
}

/** Push `v` onto the zoom-history stack, unless it is the same window as the
 * current top (a no-op zoom shouldn't grow the stack). */
export function zoomStackPush(stack: Viewport[], v: Viewport): Viewport[] {
  const top = stack[stack.length - 1];
  if (top && top.startS === v.startS && top.endS === v.endS) {
    return stack;
  }
  return [...stack, v];
}

/** Pop the current window off the stack, returning to the previous one. When
 * only one entry remains there is nowhere left to go back to, so `view` is
 * `null` rather than an empty window. */
export function zoomStackPop(stack: Viewport[]): { stack: Viewport[]; view: Viewport | null } {
  if (stack.length <= 1) {
    return { stack, view: null };
  }
  const nextStack = stack.slice(0, -1);
  return { stack: nextStack, view: nextStack[nextStack.length - 1] };
}
