/**
 * Typed fetch layer over the viewer's read-only JSON API. All paths are
 * RELATIVE ("api/...") — the app is always served from "/" by csbench serve,
 * and relative paths keep the strict connect-src 'self' CSP trivially true.
 */

import { useEffect, useState } from "react";

import type { ItemResultData } from "@/lib/items";

export interface Meta {
  results_dir: string;
  version: string;
  counts: { records: number };
  /** How many runs are in flight right now. Drives the nav's live badge. */
  progress_active?: number;
  /** Whether this server was started with --allow-write. The UI must not
   * offer a button the server would answer 405 to. Absent on older servers,
   * which had no write routes at all — so treat absent as false. */
  write_enabled?: boolean;
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
  /** Per-item evidence — the substrate the measurements above were aggregated
   * from. Present only for suites that score example by example; the TPC and
   * YCSB families measure an engine rather than examples and emit none, which
   * is why every reader of this field must handle it being absent. */
  items?: ItemResultData[];
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

// ----------------------------------------------------------------------
// Targets (configs/*.yaml)
// ----------------------------------------------------------------------

export interface TargetSummary {
  name: string;
  /** How many runs THIS console started with it. Sealed records do not name
   * the config file they were handed, so runs started any other way are not
   * counted here — and the UI says so rather than implying the number is
   * every run that ever used this target. */
  launched?: number;
  filename: string;
  size: number;
  /** mtime, epoch seconds. */
  modified: number;
  mode: string;
  provider: string;
  region: string;
  /** Why this file could not be understood; "" when it was fine. */
  error: string;
}

export interface TargetDetailData extends TargetSummary {
  /** The parsed config, with credential-shaped values replaced by "***". */
  data: Record<string, unknown>;
  /** Dotted paths that were redacted. Non-empty means `yaml` is null. */
  redacted: string[];
  /** The file's own text — served only when nothing had to be redacted, so
   * that editing it here cannot reformat it or drop its comments. */
  yaml: string | null;
}

export interface TargetListData {
  targets: TargetSummary[];
}

/**
 * A mutating request, with the server's own error message preserved.
 *
 * Two things this does that `getJSON` does not. The custom header is what a
 * cross-origin <form> cannot set, which is what makes these routes unforgeable
 * from another site. And a failure raises what the server *said* — "refusing
 * to save the redaction placeholder at: target.api_token" — rather than the
 * status code, because the status code is not something the reader can act on.
 */
export async function writeJSON<T>(path: string, method: string, body?: unknown): Promise<T> {
  const resp = await fetch(path, {
    method,
    headers: { "X-Csbench-Write": "1", "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload: unknown = await resp.json().catch(() => null);
  if (!resp.ok) {
    const message =
      payload !== null && typeof payload === "object" && typeof (payload as { error?: unknown }).error === "string"
        ? (payload as { error: string }).error
        : `HTTP ${resp.status}`;
    throw new Error(message);
  }
  return payload as T;
}

export async function putTarget(name: string, yaml: string, overwrite: boolean): Promise<void> {
  await writeJSON(`api/targets/${encodeURIComponent(name)}`, "PUT", { yaml, overwrite });
}

export async function deleteTarget(name: string): Promise<void> {
  await writeJSON(`api/targets/${encodeURIComponent(name)}`, "DELETE");
}

// ----------------------------------------------------------------------
// Starting a run
// ----------------------------------------------------------------------

export interface LaunchPlatform {
  platform: string;
  status: string;
}

export interface LaunchDomain {
  domain: string;
  description: string;
  platforms: LaunchPlatform[];
}

export interface LaunchSuite {
  suite_id: string;
  suite_version: string;
  /** Platforms this benchmark has actually produced records on, here. It is
   * evidence, not permission: nothing in the registry maps a benchmark to a
   * platform, so an empty list means "untried", never "forbidden". */
  seen_platforms: string[];
}

export interface LaunchOptions {
  domains: LaunchDomain[];
  suites: LaunchSuite[];
  max_repeat: number;
  max_warmup: number;
}

/** What a "create like this" run starts from. Repeat/warmup are absent on
 * purpose: a batch size is a decision about one run. */
export interface LaunchSeed {
  domain: string;
  task_id: string;
  platform: string;
  target: string | null;
  params: Record<string, string | number | boolean>;
}

export interface LaunchRequest {
  domain: string;
  task_id: string;
  platform: string;
  target: string | null;
  params: Record<string, string | number | boolean>;
  repeat: number;
  warmup: number;
}

/** Start a run. Answers with the id before the run has done anything. */
export async function startRun(request: LaunchRequest): Promise<string> {
  const { run_id } = await writeJSON<{ run_id: string }>("api/runs", "POST", request);
  return run_id;
}

// ----------------------------------------------------------------------
// The catalogue faces (read-only)
// ----------------------------------------------------------------------

export interface SuiteEvaluator {
  evaluator_id: string;
  /** True = the suite's canonical numbers, not a number someone computed. */
  official: boolean;
}

export interface InstalledSuite {
  suite_id: string;
  /** The pin: the same benchmark id always means the same data. */
  suite_version: string;
  evaluators: SuiteEvaluator[];
  seen_platforms: string[];
  runs: number;
}

export interface InstalledSuiteList {
  suites: InstalledSuite[];
}

export interface PluginEntry {
  kind: string;
  name: string;
  target: string;
  /** Which distribution provides it; "" when the environment cannot say. */
  distribution: string;
}

export interface PluginInventory {
  plugins: PluginEntry[];
  core_version: string;
  plugin_api: string;
}
