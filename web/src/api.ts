/**
 * Typed fetch layer over the viewer's read-only JSON API. All paths are
 * RELATIVE ("api/...") — the app is always served from "/" by csbench serve,
 * and relative paths keep the strict connect-src 'self' CSP trivially true.
 */

import { useEffect, useState } from "react";

export interface Meta {
  results_dir: string;
  version: string;
  counts: { records: number };
}

export interface RecordSummary {
  run_id: string;
  domain: string;
  task_id: string;
  adapter: string;
  status: string;
  started_at: string;
  suite_id: string;
  scaffold: string;
  measurements: Record<string, unknown>;
  has_trajectory: boolean;
}

export interface MeasurementEntry {
  value?: unknown;
  unit?: string;
  official?: boolean;
  reproducibility_class?: string;
}

export interface RecordError {
  stage?: string;
  code?: string;
  message?: string;
}

export interface ArtifactEntry {
  kind?: string;
  media?: string;
  path?: string;
  sha256?: string;
}

export interface RecordDetailData {
  status?: string;
  run?: {
    run_id?: string;
    started_at?: string;
    finished_at?: string;
    stages?: Record<string, unknown>;
    stage_timings?: Record<string, unknown>;
  };
  identity?: { domain?: string; task_id?: string; adapter?: string };
  provenance?: {
    suite_id?: string;
    suite_version?: string;
    evaluator_id?: string;
    evaluator_official?: boolean;
    scaffold?: string;
    dataset_digest?: string;
  };
  measurements?: Record<string, MeasurementEntry>;
  errors?: RecordError[];
  artifacts?: ArtifactEntry[];
}

/** One raw span line from the trajectory artifact — every field optional. */
export interface TraceSpan {
  span_id?: string;
  trace_id?: string;
  parent_id?: string | null;
  name?: string;
  kind?: string;
  t_start?: number;
  t_end?: number;
  status?: string;
  error?: string;
  attrs?: Record<string, unknown>;
}

export interface TrajectoryData {
  spans: TraceSpan[];
  t0: number;
}

export async function getJSON<T>(path: string): Promise<T> {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json() as Promise<T>;
}

export interface Loadable<T> {
  data: T | null;
  error: string | null;
}

/** Fetch-on-mount hook; refetches when `path` changes, ignores stale results. */
export function useJSON<T>(path: string): Loadable<T> {
  const [state, setState] = useState<Loadable<T>>({ data: null, error: null });
  useEffect(() => {
    let alive = true;
    setState({ data: null, error: null });
    getJSON<T>(path)
      .then((data) => {
        if (alive) setState({ data, error: null });
      })
      .catch((err: unknown) => {
        if (alive) setState({ data: null, error: err instanceof Error ? err.message : String(err) });
      });
    return () => {
      alive = false;
    };
  }, [path]);
  return state;
}
