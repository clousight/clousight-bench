/**
 * Estimating how much longer an in-flight run has.
 *
 * The estimate is deliberately dumb — a linear extrapolation from units
 * completed so far — because a benchmark's remaining work genuinely is roughly
 * linear in remaining units, and a cleverer model would be harder to distrust.
 * What matters more than the model is refusing to show one when it would be
 * noise: with two samples the number swings wildly, and a wildly wrong "3
 * seconds left" costs more trust than an honest blank.
 */

/** Below this many completed units an estimate is noise, so we show none. */
export const MIN_SAMPLES_FOR_ETA = 3;

export interface EtaInput {
  /** Units finished so far. */
  completed: number;
  /** Total units, or 0 when the suite could not say. */
  total: number;
  /** Milliseconds elapsed since this phase started. */
  elapsedMs: number;
}

/**
 * Milliseconds remaining, or null when we should not claim to know:
 * an unknown total, too few samples, or no time on the clock yet.
 */
export function estimateRemainingMs({ completed, total, elapsedMs }: EtaInput): number | null {
  if (total <= 0 || completed < MIN_SAMPLES_FOR_ETA || completed >= total) return null;
  if (!Number.isFinite(elapsedMs) || elapsedMs <= 0) return null;
  const perUnit = elapsedMs / completed;
  return Math.max(0, perUnit * (total - completed));
}

/** Completion as a 0..1 fraction, or null when the total is unknown. */
export function fractionDone(completed: number, total: number): number | null {
  if (total <= 0) return null;
  return Math.min(1, Math.max(0, completed / total));
}
