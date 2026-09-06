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

/** Span kind → the meaning-named token it paints with. */
const KIND_VARS: Record<string, string> = {
  llm_call: "--chart-llm",
  llm: "--chart-llm",
  tool_call: "--chart-tool",
  tool: "--chart-tool",
  db_query: "--chart-db",
  db: "--chart-db",
  stage: "--chart-stage",
};

export function readKindColors(): Record<string, string> {
  const resolved: Record<string, string> = {};
  for (const [kind, varName] of Object.entries(KIND_VARS)) {
    resolved[kind] = resolveCssColor(varName);
  }
  return resolved;
}

/** The colour for a span kind; unknown kinds fall back to slot 1. */
export function kindColor(colors: Record<string, string>, kind: string, fallback: string): string {
  return colors[kind] ?? fallback;
}

/** Tooltip bodies are echarts-rendered HTML — escape everything data-derived. */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
