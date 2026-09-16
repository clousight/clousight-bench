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
  // competing with it, so they take the recessive axis colour rather than a
  // categorical slot. That also keeps the palette at its four validated hues
  // instead of inventing a fifth for a kind that is not data.
  lifecycle: "--chart-axis",
  phase: "--chart-1",
  query: "--chart-2",
  llm_call: "--chart-3",
  tool_call: "--chart-4",
  // `span` is data.py's "no discriminator matched" — it takes the first slot
  // rather than a colour invented for it.
  span: "--chart-1",
};

export function readKindColors(): Record<string, string> {
  const resolved: Record<string, string> = {};
  for (const [kind, varName] of Object.entries(KIND_SLOTS)) {
    resolved[kind] = resolveCssColor(varName);
  }
  return resolved;
}

/** The colour for a span kind; unknown kinds fall back to slot 1. */
export function kindColor(colors: Record<string, string>, kind: string, fallback: string): string {
  return colors[kind] ?? fallback;
}

/**
 * A span mark's paint, as three independent facts:
 *
 * - Hue carries kind identity, read from `KIND_SLOTS` above, so a `query`
 *   span is the same colour everywhere it appears. `OverviewStrip` and
 *   `TrackList` both call this one function (the strip with `selected`
 *   pinned to `true`, since it has no per-track checkbox) so the two panes
 *   are, by construction, incapable of disagreeing about a given span's
 *   paint — not just visually tuned to match.
 * - An error always overrides hue with `--status-critical`, regardless of
 *   kind, and is always the single most opaque mark in the lane (1.0
 *   selected / 0.6 deselected — both above the corresponding non-error
 *   values) so that on a shared x-axis an error pops above the surrounding
 *   spans rather than receding below them.
 * - The lifecycle lane is the one track guaranteed to exist on every trace,
 *   so its colour has to survive being drawn as a 2px dash rather than the
 *   BAR_HEIGHT-tall echarts bar with a text label beside it that
 *   `--chart-axis` was tuned for. In dark mode that token already carries a
 *   baked-in 22% alpha (`index.css`), and multiplying that by this
 *   function's own opacity collapsed a selected lifecycle dash to ~19% white
 *   and a deselected one to ~4% — indistinguishable from an empty lane. It
 *   does not need a categorical slot to read as recessive, so it takes
 *   `--muted-foreground`: an opaque, contrast-tuned grey already used
 *   everywhere else in the app for de-emphasised-but-legible text.
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
    return { backgroundColor: "var(--status-critical)", opacity: selected ? 1 : 0.6 };
  }
  const backgroundColor =
    kind === "lifecycle" ? "var(--muted-foreground)" : `var(${KIND_SLOTS[kind] ?? KIND_SLOTS.span})`;
  return { backgroundColor, opacity: selected ? 0.85 : 0.4 };
}

/** Tooltip bodies are echarts-rendered HTML — escape everything data-derived. */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
