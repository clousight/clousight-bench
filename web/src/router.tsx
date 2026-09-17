/**
 * Tiny hash router.
 *
 *   #/                      the results board (domains)
 *   #/suite/:domain/:suite  one suite, platforms compared
 *   #/record/:id            one run
 *   #/record/:id/trace      that run's execution trace
 *   #/record/:id/items      that run's per-item detail
 *   #/record/:id/items/:m   the same, scoped to one metric (and, via
 *                           `<metric>.by_group.<group>`, one group)
 *   #/runs                  the flat table of every run
 *   #/live                  runs in flight right now
 *   #/live/:id              one run, live
 *
 * `#/record/:id` and `#/record/:id/trace` are the routes the previous viewer
 * shipped, kept verbatim so links people already have keep resolving.
 *
 * Malformed hashes — including segments that fail decodeURIComponent —
 * resolve to notFound rather than throwing.
 */

import { useEffect, useState } from "react";

export type Route =
  | { name: "board" }
  | { name: "suite"; domain: string; suiteId: string }
  | { name: "record"; runId: string }
  | { name: "trace"; runId: string }
  | { name: "items"; runId: string; metric: string | null }
  | { name: "runs" }
  | { name: "live" }
  | { name: "liveRun"; runId: string }
  | { name: "notFound" };

/** decodeURIComponent that reports failure instead of throwing. */
function decodeSegment(segment: string): string | null {
  try {
    const decoded = decodeURIComponent(segment);
    return decoded === "" ? null : decoded;
  } catch {
    return null;
  }
}

export function parseHash(hash: string): Route {
  const path = hash.startsWith("#") ? hash.slice(1) : hash;
  if (path === "" || path === "/") return { name: "board" };
  const segments = path.split("/").filter((segment) => segment !== "");

  if (segments[0] === "record" && segments.length >= 2) {
    const runId = decodeSegment(segments[1]);
    if (runId === null) return { name: "notFound" };
    if (segments.length === 2) return { name: "record", runId };
    if (segments.length === 3 && segments[2] === "trace") return { name: "trace", runId };
    if (segments.length === 3 && segments[2] === "items") {
      return { name: "items", runId, metric: null };
    }
    if (segments.length === 4 && segments[2] === "items") {
      const metric = decodeSegment(segments[3]);
      if (metric === null) return { name: "notFound" };
      return { name: "items", runId, metric };
    }
  }

  if (segments[0] === "suite" && segments.length === 3) {
    const domain = decodeSegment(segments[1]);
    const suiteId = decodeSegment(segments[2]);
    if (domain === null || suiteId === null) return { name: "notFound" };
    return { name: "suite", domain, suiteId };
  }

  if (segments[0] === "live") {
    if (segments.length === 1) return { name: "live" };
    if (segments.length === 2) {
      const runId = decodeSegment(segments[1]);
      return runId === null ? { name: "notFound" } : { name: "liveRun", runId };
    }
  }

  if (segments[0] === "runs" && segments.length === 1) return { name: "runs" };

  return { name: "notFound" };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onHashChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  return route;
}

export const boardHref = "#/";
export const runsHref = "#/runs";
export const liveHref = "#/live";

export function suiteHref(domain: string, suiteId: string): string {
  return `#/suite/${encodeURIComponent(domain)}/${encodeURIComponent(suiteId)}`;
}

export function recordHref(runId: string): string {
  return `#/record/${encodeURIComponent(runId)}`;
}

export function traceHref(runId: string): string {
  return `${recordHref(runId)}/trace`;
}

/**
 * The detail surface, optionally scoped to one measurement's metric.
 *
 * `metric` is the measurement key with its suite prefix stripped — see
 * `lib/items.ts::detailTarget`, which is what decides whether a link exists at
 * all. Passing `null` (or omitting it) is the unscoped table.
 */
export function itemsHref(runId: string, metric?: string | null): string {
  const base = `${recordHref(runId)}/items`;
  return metric === undefined || metric === null ? base : `${base}/${encodeURIComponent(metric)}`;
}

export function liveRunHref(runId: string): string {
  return `#/live/${encodeURIComponent(runId)}`;
}
