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
  /** How many runs are in flight right now. Drives the nav's live badge. */
  progress_active?: number;
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
  identity?: {
    domain?: string;
    task_id?: string;
    adapter?: string;
    adapter_status?: string;
    core_version?: string;
    task_revision?: string;
    scorer_revision?: string;
    plugin_versions?: Record<string, string>;
  };
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
  /** Only the engineer view renders these; they are what makes a run
   * reproducible, and what makes the page unreadable if shown by default. */
  fingerprints?: Record<string, unknown>;
  environment?: Record<string, unknown>;
  extensions?: Record<string, unknown>;
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
  /** How complete this trace is. `full` = the run trace with the suite's own
   * spans merged in, the whole chain. `artifact` = a pre-merge record, where
   * the detail lives only in the suite's sidecar. `lifecycle` = the stages and
   * nothing finer, because the suite reported nothing. The UI says which, so a
   * reader knows how much detail to expect. */
  source?: "full" | "artifact" | "lifecycle";
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

// ----------------------------------------------------------------------
// Board & suite comparison
// ----------------------------------------------------------------------

export interface BoardRunSummary {
  run_id: string;
  adapter: string;
  started_at: string;
  status: string;
  measurements: Record<string, number>;
}

export interface BoardSuite {
  suite_id: string;
  task_id: string;
  platforms: number;
  runs: number;
  latest: BoardRunSummary | null;
}

export interface BoardDomain {
  domain: string;
  runs: number;
  suites: BoardSuite[];
}

export interface BoardData {
  domains: BoardDomain[];
}

export interface SuiteHistoryPoint {
  run_id: string;
  started_at: string;
  status: string;
  measurements: Record<string, number>;
}

export interface SuitePlatformLatest extends SuiteHistoryPoint {
  suite_version?: string;
  evaluator_id?: string;
}

export interface SuitePlatform {
  adapter: string;
  runs: number;
  latest: SuitePlatformLatest | null;
  history: SuiteHistoryPoint[];
}

export interface SuiteCompareData {
  domain: string;
  suite_id: string;
  metric_keys: string[];
  platforms: SuitePlatform[];
}

// ----------------------------------------------------------------------
// The progress plane (an in-flight run)
// ----------------------------------------------------------------------

/** The suite's current phase of work — "Power, 7 of 22 queries". */
export interface ProgressStepCounter {
  label: string;
  completed: number;
  total: number;
  unit: string;
  /** False when the phase knows its size but cannot tick through it — a window
   * whose own wall clock is the measurement. Absent on older snapshots, which
   * predate the flag; treat that as true. */
  reports_progress?: boolean;
  started_ms: number;
}

/** One `state.json` snapshot. Mirrors core/progress.py's schema `progress/1`. */
export interface ProgressState {
  schema: string;
  run_id: string;
  trace_id: string;
  domain: string;
  task_id: string;
  suite_id: string;
  adapter: string;
  mode: string;
  started_at: string;
  updated_at: string;
  seq: number;
  lifecycle_phase: string;
  stage: string;
  stages: Record<string, string>;
  stage_timings: Record<string, number>;
  stage_started_ms: number | null;
  step: ProgressStepCounter | null;
  status: string;
  cancel_requested: boolean;
  dropped: Record<string, number>;
  record_path: string | null;
}

/** A finished sub-step, as drawn on the live waterfall. */
export interface ProgressStep {
  name: string;
  start_ms: number;
  end_ms: number;
  status: string;
  parent: string;
}

/** One line of `stream.jsonl`. Every field beyond `kind`/`seq`/`t` is optional. */
export interface ProgressEvent {
  seq?: number;
  t?: number;
  kind: string;
  [field: string]: unknown;
}

export interface ProgressList {
  runs: ProgressState[];
}

/**
 * Ask the server to cancel an in-flight run.
 *
 * The custom header is load-bearing, not decoration: it is what makes this the
 * only mutating endpoint a plain cross-origin HTML form cannot forge, since a
 * form cannot set request headers. The server rejects the call without it.
 */
export async function cancelRun(runId: string): Promise<void> {
  const resp = await fetch(`api/progress/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    headers: { "X-Csbench-Progress": "1" },
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
}

/**
 * Fetch-on-mount plus a poll, for data that changes without a stream.
 *
 * Used for the list of in-flight runs: opening an SSE connection per run just
 * to know whether it still exists would spend the server's stream budget on
 * bookkeeping. `intervalMs <= 0` disables polling.
 */
export function usePolledJSON<T>(path: string, intervalMs: number): Loadable<T> {
  const [state, setState] = useState<Loadable<T>>({ data: null, error: null });
  useEffect(() => {
    let alive = true;
    let timer: number | undefined;

    const tick = () => {
      getJSON<T>(path)
        .then((data) => {
          if (alive) setState({ data, error: null });
        })
        .catch((err: unknown) => {
          if (alive) setState({ data: null, error: err instanceof Error ? err.message : String(err) });
        })
        .finally(() => {
          if (alive && intervalMs > 0) timer = window.setTimeout(tick, intervalMs);
        });
    };

    setState({ data: null, error: null });
    tick();
    return () => {
      alive = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [path, intervalMs]);
  return state;
}
