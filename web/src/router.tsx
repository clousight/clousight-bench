/**
 * Tiny hash router: #/ (list), #/record/:id (detail), #/record/:id/trace
 * (trace). Malformed hashes — including run_id segments that fail
 * decodeURIComponent — resolve to notFound rather than throwing.
 */

import { useEffect, useState } from "react";

export type Route =
  | { name: "list" }
  | { name: "record"; runId: string }
  | { name: "trace"; runId: string }
  | { name: "notFound" };

export function parseHash(hash: string): Route {
  const path = hash.startsWith("#") ? hash.slice(1) : hash;
  if (path === "" || path === "/") return { name: "list" };
  const segments = path.split("/").filter((segment) => segment !== "");
  if (segments[0] === "record" && segments.length >= 2) {
    let runId: string;
    try {
      runId = decodeURIComponent(segments[1]);
    } catch {
      return { name: "notFound" };
    }
    if (runId === "") return { name: "notFound" };
    if (segments.length === 2) return { name: "record", runId };
    if (segments.length === 3 && segments[2] === "trace") return { name: "trace", runId };
  }
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

export function recordHref(runId: string): string {
  return `#/record/${encodeURIComponent(runId)}`;
}

export function traceHref(runId: string): string {
  return `${recordHref(runId)}/trace`;
}
