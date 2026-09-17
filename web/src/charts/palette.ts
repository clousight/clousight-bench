/**
 * Chart colours, read from CSS at paint time.
 *
 * The design tokens hold `oklch()` values, which a canvas cannot consume. The
 * probe below makes the browser convert one to `rgb()` for us — and it has to
 * happen at render time rather than at import time, because a canvas paint
 * does not track a CSS variable the way a DOM style does. Every chart re-reads
 * these on `useThemeVersion()`.
 */

/** Resolve a CSS custom property to a canvas-safe rgb()/rgba() string. */
export function resolveCssColor(varName: string): string {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
  const probe = document.createElement("span");
  probe.style.color = raw;
  document.body.appendChild(probe);
  const resolved = getComputedStyle(probe).color;
  probe.remove();
  return resolved !== "" ? resolved : raw;
}

export interface ChartChrome {
  text: string;
  grid: string;
  axis: string;
  panel: string;
  panelText: string;
  error: string;
  good: string;
  running: string;
}

export function readChrome(): ChartChrome {
  return {
    text: resolveCssColor("--muted-foreground"),
    grid: resolveCssColor("--chart-grid"),
    axis: resolveCssColor("--chart-axis"),
    panel: resolveCssColor("--card"),
    panelText: resolveCssColor("--card-foreground"),
    error: resolveCssColor("--status-critical"),
    good: resolveCssColor("--status-good"),
    running: resolveCssColor("--status-running"),
  };
}

/**
 * The categorical slots, in their fixed order.
 *
 * A series takes a slot by identity and keeps it — never by rank, and never
 * cycled. That is why `seriesColor` below hashes nothing and generates
 * nothing: past four series we fold into "other" rather than inventing a hue
 * the palette was never validated for.
 */
export function readSeriesColors(): string[] {
  return [
    resolveCssColor("--chart-1"),
    resolveCssColor("--chart-2"),
    resolveCssColor("--chart-3"),
    resolveCssColor("--chart-4"),
  ];
}

/** The slot for the Nth entity in a stable ordering; the 5th+ share the last. */
export function seriesColor(colors: string[], index: number): string {
  return colors[Math.min(index, colors.length - 1)];
}

/** An error overrides hue whatever the kind; named once so the DOM answer,
 * the canvas answer and `readLaneColors`' vocabulary cannot drift apart. */
const ERROR_TOKEN = "--status-critical";

/**
 * Span kind → the token it paints with.
 *
 * These five are the whole vocabulary: `viewer/data.py::_v3_kind` derives them
 * from a span's semconv attributes, and the live stream's reader maps onto the
 * same five so a run and its sealed trace never colour the same thing
 * differently.
 *
 * The assignment separates the pair that actually co-occurs at volume. A data
 * benchmark's trace is ~900 `query` spans inside ~15 `phase` spans, so those
 * two take blue and orange (all-pairs CVD ΔE 24.7, normal-vision 33.6). An
 * agent trace's `llm_call`/`tool_call` pair takes aqua and yellow, which sits
 * at ΔE 9.1 protan — above the floor, and legal there only because the
 * waterfall names every row in text beside its bar.
 */
export const KIND_SLOTS: Record<string, string> = {
  // The run root and its stages are the frame the work hangs in, not a series
  // competing with it, so they take a recessive grey rather than a categorical
  // slot — which also keeps the palette at its four validated hues instead of
  // inventing a fifth for a kind that is not data.
  //
  // The grey is `--muted-foreground`, not `--chart-axis`. Measured against the
  // surface a lifecycle bar sits on, `--chart-axis` is 1.87:1 light and 1.99:1
  // dark — below the 3:1 floor index.css sets for a slot that carries identity
  // without a direct label — and in dark mode it carries a baked-in 22% alpha
  // that a lane mark's own opacity multiplies into ~19% white.
  // `--muted-foreground` is 4.83:1 / 7.09:1 opaque and 3.60:1 / 5.43:1 at the
  // 0.85 a selected mark paints at, and it is the grey the rest of the app
  // already uses for de-emphasised-but-legible content. `--chart-axis` keeps
  // its real job: the 1px axis line `readChrome()` reads it for.
  lifecycle: "--muted-foreground",
  phase: "--chart-1",
  query: "--chart-2",
  llm_call: "--chart-3",
  tool_call: "--chart-4",
  // `span` is data.py's "no discriminator matched" — it takes the first slot
  // rather than a colour invented for it.
  span: "--chart-1",
};

/**
 * A span mark's paint, as three independent facts:
 *
 * - Hue carries kind identity, read from `KIND_SLOTS` above, so a `query`
 *   span is the same colour everywhere it appears. `OverviewStrip`,
 *   `TraceTree` and `SpanDock` all call this one function (each with
 *   `selected` pinned to `true`, since none of them has a per-track
 *   checkbox) so the minimap, the rows and the docked detail are, by
 *   construction, incapable of disagreeing about a given span's paint — not
 *   just visually tuned to match.
 * - An error always overrides hue with `--status-critical`, regardless of
 *   kind, and is always the single most opaque mark in the lane (1.0
 *   selected / 0.6 deselected — both above the corresponding non-error
 *   values) so that on a shared x-axis an error pops above the surrounding
 *   spans rather than receding below them.
 * - There is no per-kind exception, and the absence is the point. This
 *   function used to special-case `lifecycle` to `--muted-foreground` because
 *   `KIND_SLOTS` named `--chart-axis`, which is invisible at a lane dash's
 *   size. So the table and this function gave two different answers: the
 *   legend read the table, every mark on screen read the override, and a
 *   green suite never saw it because the test that claimed to check it could
 *   only see the legend. The correction lives in `KIND_SLOTS` itself now,
 *   where one answer serves the DOM, the canvas and the legend alike.
 * - A track's checkbox (or, for the strip, nothing) only ever changes
 *   opacity, never hue: toggling a track off dims it so it reads as
 *   "present but not selected," not as a different kind of span or a span
 *   that stopped being an error. The deselected floor is 0.4, matching
 *   `OverviewStrip`'s own dimmest mark (`bg-chart-1/40`, pre-this-change) —
 *   low enough to read as background, not so low it collapses into "no span
 *   here at all".
 */
export function laneSpanStyle(
  kind: string,
  isError: boolean,
  selected: boolean,
): { backgroundColor: string; opacity: number } {
  if (isError) {
    return { backgroundColor: `var(${ERROR_TOKEN})`, opacity: selected ? 1 : 0.6 };
  }
  return {
    backgroundColor: `var(${KIND_SLOTS[kind] ?? KIND_SLOTS.span})`,
    opacity: selected ? 0.85 : 0.4,
  };
}

/**
 * Every token a lane paint can name, resolved once per canvas paint.
 *
 * `laneSpanStyle` answers in `var(--x)` form, which a DOM style consumes
 * directly and a canvas cannot. Resolving one costs a `getComputedStyle`
 * probe, so the whole vocabulary is resolved here — once per paint — rather
 * than inside a per-span render callback, where a data benchmark's ~900 spans
 * would mean ~900 probes a frame.
 */
export function readLaneColors(): Record<string, string> {
  const resolved: Record<string, string> = {};
  for (const token of new Set([...Object.values(KIND_SLOTS), ERROR_TOKEN])) {
    resolved[token] = resolveCssColor(token);
  }
  return resolved;
}

/**
 * `laneSpanStyle`'s answer in the form a canvas can consume.
 *
 * Two translations and nothing else: the CSS variable is looked up in a table
 * from `readLaneColors`, and the DOM's `backgroundColor`/`opacity` become
 * zrender's `fill`/`opacity`. The error outline is part of the answer rather
 * than something the call site adds, so that the *whole* of a canvas bar's
 * paint is this one call — a caller that took the fill and left the opacity
 * behind is exactly how the waterfall's legend swatch (0.85) and the bars it
 * named (opaque) ended up two different strengths of one hue while a comment
 * two lines above it claimed "there is only one answer to give".
 */
export function laneSpanPaint(
  colors: Record<string, string>,
  kind: string,
  isError: boolean,
  selected: boolean,
): { fill: string; opacity: number; stroke?: string; lineWidth?: number } {
  const { backgroundColor, opacity } = laneSpanStyle(kind, isError, selected);
  const fill = colors[backgroundColor.slice("var(".length, -1)];
  return isError ? { fill, opacity, stroke: fill, lineWidth: 1.5 } : { fill, opacity };
}

/** Tooltip bodies are echarts-rendered HTML — escape everything data-derived. */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
