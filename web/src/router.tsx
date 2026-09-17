/**
 * Tiny hash router.
 *
 * The app is organised around five nouns, each with a list → detail → (where
 * it makes sense) create/edit loop:
 *
 *   #/suites                    the benchmark catalogue: what can be measured
 *   #/suites/:domain/:suite     one benchmark, platforms compared
 *   #/targets                   the things under test, as configured
 *   #/targets/:name             one target
 *   #/targets/new               compose a new target
 *   #/runs                      every run
 *   #/runs/new                  compose and launch a run
 *   #/runs/:id                  one run — overview / items / trace / config
 *   #/observe                   runs in flight right now
 *   #/observe/:id               one run, live
 *   #/config                    metrics vocabulary, plugins, environment
 *
 * **Every route the previous viewers shipped still resolves.** Links get
 * pasted into issues and chat and outlive the layout that produced them, so
 * the old spellings are kept as aliases that normalise to the new ones rather
 * than 404-ing. `legacyRedirect()` is what the shell calls to rewrite the bar;
 * `parseHash` resolves both spellings identically either way, so a stale link
 * works even if the rewrite is ever removed.
 *
 *   #/                    → #/runs
 *   #/suite/:d/:s         → #/suites/:d/:s
 *   #/record/:id[/…]      → #/runs/:id[?tab=…]
 *   #/live[/:id]          → #/observe[/:id]
 *
 * Malformed hashes — including segments that fail decodeURIComponent —
 * resolve to notFound rather than throwing.
 */

import { useEffect, useState } from "react";

/** Which face of a run's detail page is showing. */
export type RunTab = "overview" | "items" | "trace" | "config";

export const RUN_TABS: readonly RunTab[] = ["overview", "items", "trace", "config"] as const;

export type Route =
  | { name: "suites" }
  | { name: "suite"; domain: string; suiteId: string }
  | { name: "targets" }
  | { name: "targetNew" }
  | { name: "target"; targetName: string }
  | { name: "runs" }
  | { name: "runNew" }
  /** `metric` is only meaningful on the items tab; null everywhere else. */
  | { name: "run"; runId: string; tab: RunTab; metric: string | null }
  | { name: "observe" }
  | { name: "observeRun"; runId: string }
  | { name: "config" }
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

function isRunTab(value: string): value is RunTab {
  return (RUN_TABS as readonly string[]).includes(value);
}

/**
 * The old `#/record/:id/...` spellings, as a run route.
 *
 * `trace` and `items` were sibling routes before the four faces of a run were
 * collected onto one page; they are tabs now, and the same URL has to keep
 * landing on the same content.
 */
function legacyRecord(segments: string[]): Route {
  const runId = decodeSegment(segments[1]);
  if (runId === null) return { name: "notFound" };
  if (segments.length === 2) return { name: "run", runId, tab: "overview", metric: null };
  if (segments.length === 3 && segments[2] === "trace") {
    return { name: "run", runId, tab: "trace", metric: null };
  }
  if (segments[2] === "items") {
    if (segments.length === 3) return { name: "run", runId, tab: "items", metric: null };
    if (segments.length === 4) {
      const metric = decodeSegment(segments[3]);
      if (metric === null) return { name: "notFound" };
      return { name: "run", runId, tab: "items", metric };
    }
  }
  return { name: "notFound" };
}

export function parseHash(hash: string): Route {
  const path = hash.startsWith("#") ? hash.slice(1) : hash;
  const [pathPart, queryPart = ""] = path.split("?", 2);
  if (pathPart === "" || pathPart === "/") return { name: "runs" };
  const segments = pathPart.split("/").filter((segment) => segment !== "");
  const query = new URLSearchParams(queryPart);

  // ---- legacy spellings, normalised ------------------------------------
  if (segments[0] === "record" && segments.length >= 2) return legacyRecord(segments);

  if (segments[0] === "suite" && segments.length === 3) {
    const domain = decodeSegment(segments[1]);
    const suiteId = decodeSegment(segments[2]);
    if (domain === null || suiteId === null) return { name: "notFound" };
    return { name: "suite", domain, suiteId };
  }

  if (segments[0] === "live") {
    if (segments.length === 1) return { name: "observe" };
    if (segments.length === 2) {
      const runId = decodeSegment(segments[1]);
      return runId === null ? { name: "notFound" } : { name: "observeRun", runId };
    }
    return { name: "notFound" };
  }

  // ---- current spellings -----------------------------------------------
  if (segments[0] === "suites") {
    if (segments.length === 1) return { name: "suites" };
    if (segments.length === 3) {
      const domain = decodeSegment(segments[1]);
      const suiteId = decodeSegment(segments[2]);
      if (domain === null || suiteId === null) return { name: "notFound" };
      return { name: "suite", domain, suiteId };
    }
    return { name: "notFound" };
  }

  if (segments[0] === "targets") {
    if (segments.length === 1) return { name: "targets" };
    if (segments.length === 2) {
      if (segments[1] === "new") return { name: "targetNew" };
      const targetName = decodeSegment(segments[1]);
      return targetName === null ? { name: "notFound" } : { name: "target", targetName };
    }
    return { name: "notFound" };
  }

  if (segments[0] === "runs") {
    if (segments.length === 1) return { name: "runs" };
    if (segments.length === 2) {
      if (segments[1] === "new") return { name: "runNew" };
      const runId = decodeSegment(segments[1]);
      if (runId === null) return { name: "notFound" };
      const rawTab = query.get("tab") ?? "overview";
      // An unknown tab is the overview rather than a 404: the run exists, and
      // dropping a reader on "not found" because a query string aged badly
      // loses the thing they actually asked for.
      const tab = isRunTab(rawTab) ? rawTab : "overview";
      const metric = tab === "items" ? query.get("metric") : null;
      return { name: "run", runId, tab, metric: metric === "" ? null : metric };
    }
    return { name: "notFound" };
  }

  if (segments[0] === "observe") {
    if (segments.length === 1) return { name: "observe" };
    if (segments.length === 2) {
      const runId = decodeSegment(segments[1]);
      return runId === null ? { name: "notFound" } : { name: "observeRun", runId };
    }
    return { name: "notFound" };
  }

  if (segments[0] === "config" && segments.length === 1) return { name: "config" };

  return { name: "notFound" };
}

/**
 * The canonical spelling for a hash that arrived in an old one, or null when
 * it is already canonical.
 *
 * Returning null rather than the unchanged hash is what lets the caller do
 * `if (next !== null) replace(next)` without comparing strings, and keeps a
 * canonical URL from being rewritten to itself on every navigation.
 */
export function legacyRedirect(hash: string): string | null {
  const path = (hash.startsWith("#") ? hash.slice(1) : hash).split("?", 1)[0];
  const head = path.split("/").filter((segment) => segment !== "")[0];
  if (path === "" || path === "/") return runsHref;
  if (head !== "record" && head !== "suite" && head !== "live") return null;
  const route = parseHash(hash);
  return hrefOf(route);
}

/** The canonical href for a parsed route. */
export function hrefOf(route: Route): string {
  switch (route.name) {
    case "suites":
      return suitesHref;
    case "suite":
      return suiteHref(route.domain, route.suiteId);
    case "targets":
      return targetsHref;
    case "targetNew":
      return targetNewHref;
    case "target":
      return targetHref(route.targetName);
    case "runs":
      return runsHref;
    case "runNew":
      return runNewHref;
    case "run":
      return runHref(route.runId, route.tab, route.metric);
    case "observe":
      return observeHref;
    case "observeRun":
      return observeRunHref(route.runId);
    case "config":
      return configHref;
    case "notFound":
      return runsHref;
  }
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

export const suitesHref = "#/suites";
export const targetsHref = "#/targets";
export const targetNewHref = "#/targets/new";
export const runsHref = "#/runs";
export const runNewHref = "#/runs/new";
export const observeHref = "#/observe";
export const configHref = "#/config";

/** Kept for links that predate the suites section. */
export const boardHref = runsHref;
export const liveHref = observeHref;

export function suiteHref(domain: string, suiteId: string): string {
  return `${suitesHref}/${encodeURIComponent(domain)}/${encodeURIComponent(suiteId)}`;
}

export function targetHref(name: string): string {
  return `${targetsHref}/${encodeURIComponent(name)}`;
}

/**
 * One run, on one of its four faces.
 *
 * The tab is a query parameter rather than a path segment so that every face
 * of a run shares one path — which is what makes "the same run, a different
 * question" a filter on one object instead of four sibling pages.
 */
export function runHref(runId: string, tab: RunTab = "overview", metric?: string | null): string {
  const base = `${runsHref}/${encodeURIComponent(runId)}`;
  const query = new URLSearchParams();
  if (tab !== "overview") query.set("tab", tab);
  if (tab === "items" && metric !== undefined && metric !== null && metric !== "") {
    query.set("metric", metric);
  }
  const suffix = query.toString();
  return suffix === "" ? base : `${base}?${suffix}`;
}

/** Kept: the spelling the previous viewer shipped, now an alias. */
export const recordHref = (runId: string): string => runHref(runId);
export const traceHref = (runId: string): string => runHref(runId, "trace");
export const itemsHref = (runId: string, metric?: string | null): string =>
  runHref(runId, "items", metric);

export function observeRunHref(runId: string): string {
  return `${observeHref}/${encodeURIComponent(runId)}`;
}

/** Kept: the spelling the previous viewer shipped, now an alias. */
export const liveRunHref = observeRunHref;
