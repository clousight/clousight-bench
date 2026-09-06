import { useEffect, useState } from "react";

/**
 * A value that changes on an interval, to drive elapsed-time displays.
 *
 * Returned as `Date.now()` rather than a counter so a caller can compute an
 * elapsed time directly. Pass `active: false` to stop the timer — a finished
 * run must not keep re-rendering the page once its clock has stopped.
 */
export function useNow(intervalMs: number, active = true): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs, active]);
  return now;
}
