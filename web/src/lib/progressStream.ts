/**
 * The live feed: an EventSource over `api/progress/<run_id>/stream`, reduced
 * into the shape the live views render.
 *
 * Two things this module is careful about.
 *
 * **Bounded memory.** A long run can emit tens of thousands of events. The
 * reducer keeps a full list of nothing: steps are capped, samples are capped,
 * and the log is a ring buffer. A viewer tab left open overnight must not grow
 * without limit — the same discipline the writer applies on disk.
 *
 * **`seq` is the resume token.** Every event carries a monotonic `seq`, so a
 * dropped connection reconnects with `?since=<last seq>` and loses nothing.
 * EventSource reconnects on its own, but it would replay from zero without it.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import type { ProgressEvent, ProgressState, ProgressStep } from "@/api";

/** Ring-buffer caps. Generous enough to be useful, bounded enough to be safe. */
export const MAX_STEPS = 4000;
export const MAX_SAMPLES_PER_KEY = 600;
export const MAX_LOG_LINES = 500;

export interface LogLine {
  seq: number;
  t: number;
  level: string;
  msg: string;
}

export interface SampleSeries {
  key: string;
  /** `[t_ms_since_run_start, value]`, oldest first. */
  points: Array<[number, number]>;
}

export interface LiveFeed {
  state: ProgressState | null;
  steps: ProgressStep[];
  samples: SampleSeries[];
  logs: LogLine[];
  /** Highest `seq` seen; the resume token. */
  seq: number;
  /** True once the run reached a terminal status and the stream said `done`. */
  done: boolean;
  /** Path of the sealed record, once there is one. */
  recordPath: string | null;
  /** Set when the stream itself failed (not when the run failed). */
  error: string | null;
  connected: boolean;
}

const EMPTY: LiveFeed = {
  state: null,
  steps: [],
  samples: [],
  logs: [],
  seq: 0,
  done: false,
  recordPath: null,
  error: null,
  connected: false,
};

interface Accumulator {
  steps: ProgressStep[];
  samples: Map<string, Array<[number, number]>>;
  logs: LogLine[];
  seq: number;
}

function emptyAccumulator(): Accumulator {
  return { steps: [], samples: new Map(), logs: [], seq: 0 };
}

/** Fold one event into the accumulator. Exported for unit tests. */
export function applyEvent(acc: Accumulator, event: ProgressEvent): void {
  const seq = typeof event.seq === "number" ? event.seq : 0;
  if (seq <= acc.seq) return; // replayed after a reconnect — already folded
  acc.seq = seq;
  const t = typeof event.t === "number" ? event.t : 0;

  switch (event.kind) {
    case "step": {
      if (typeof event.name !== "string") return;
      acc.steps.push({
        name: event.name,
        start_ms: typeof event.start_ms === "number" ? event.start_ms : t,
        end_ms: typeof event.end_ms === "number" ? event.end_ms : t,
        status: typeof event.status === "string" ? event.status : "ok",
        parent: typeof event.parent === "string" ? event.parent : "",
      });
      // Drop from the front: the tail is what a watcher is looking at.
      if (acc.steps.length > MAX_STEPS) acc.steps.splice(0, acc.steps.length - MAX_STEPS);
      return;
    }
    case "sample": {
      if (typeof event.key !== "string" || typeof event.value !== "number") return;
      const series = acc.samples.get(event.key) ?? [];
      series.push([t, event.value]);
      if (series.length > MAX_SAMPLES_PER_KEY) series.splice(0, series.length - MAX_SAMPLES_PER_KEY);
      acc.samples.set(event.key, series);
      return;
    }
    case "log": {
      acc.logs.push({
        seq,
        t,
        level: typeof event.level === "string" ? event.level : "INFO",
        msg: typeof event.msg === "string" ? event.msg : "",
      });
      if (acc.logs.length > MAX_LOG_LINES) acc.logs.splice(0, acc.logs.length - MAX_LOG_LINES);
      return;
    }
    default:
      // stage / progress / cancel / done drive `state`, which the server sends
      // as its own frame — folding them here would duplicate that source.
      return;
  }
}

function snapshot(acc: Accumulator, base: Omit<LiveFeed, "steps" | "samples" | "logs" | "seq">): LiveFeed {
  return {
    ...base,
    steps: acc.steps.slice(),
    samples: [...acc.samples.entries()].map(([key, points]) => ({ key, points: points.slice() })),
    logs: acc.logs.slice(),
    seq: acc.seq,
  };
}

/**
 * Subscribe to one run's live feed.
 *
 * Pass `runId: null` to stay disconnected (so a component can mount before it
 * knows which run to watch without opening a stream it will immediately drop).
 */
export function useProgressStream(runId: string | null): LiveFeed {
  const [feed, setFeed] = useState<LiveFeed>(EMPTY);
  const accRef = useRef<Accumulator>(emptyAccumulator());

  useEffect(() => {
    if (runId === null) {
      accRef.current = emptyAccumulator();
      setFeed(EMPTY);
      return;
    }
    accRef.current = emptyAccumulator();
    setFeed({ ...EMPTY, connected: false });

    let closed = false;
    let source: EventSource | null = null;
    let base: Omit<LiveFeed, "steps" | "samples" | "logs" | "seq"> = {
      state: null,
      done: false,
      recordPath: null,
      error: null,
      connected: false,
    };

    const push = () => setFeed(snapshot(accRef.current, base));

    const connect = () => {
      if (closed) return;
      const since = accRef.current.seq;
      source = new EventSource(`api/progress/${encodeURIComponent(runId)}/stream?since=${since}`);

      source.addEventListener("open", () => {
        base = { ...base, connected: true, error: null };
        push();
      });

      source.addEventListener("state", (event) => {
        const parsed = parseData<ProgressState>(event);
        if (parsed === null) return;
        base = { ...base, state: parsed, connected: true };
        push();
      });

      source.addEventListener("events", (event) => {
        const parsed = parseData<{ events?: ProgressEvent[] }>(event);
        if (parsed === null || !Array.isArray(parsed.events)) return;
        for (const item of parsed.events) applyEvent(accRef.current, item);
        push();
      });

      source.addEventListener("done", (event) => {
        const parsed = parseData<{ status?: string; record_path?: string | null }>(event);
        base = {
          ...base,
          done: true,
          connected: false,
          recordPath: parsed?.record_path ?? null,
        };
        push();
        // A finished run has nothing more to say; closing here also stops
        // EventSource from reconnecting to a stream that will 404 shortly.
        closed = true;
        source?.close();
      });

      source.addEventListener("error", () => {
        // EventSource retries on its own. Only surface an error once we are
        // sure this is not the ordinary reconnect that follows `done`.
        if (closed) return;
        base = { ...base, connected: false };
        push();
      });
    };

    connect();
    return () => {
      closed = true;
      source?.close();
    };
  }, [runId]);

  return feed;
}

function parseData<T>(event: Event): T | null {
  const data = (event as MessageEvent<string>).data;
  if (typeof data !== "string" || data === "") return null;
  try {
    return JSON.parse(data) as T;
  } catch {
    // A truncated frame is possible mid-write; skipping it is correct, and the
    // next snapshot frame carries the full state anyway.
    return null;
  }
}

/**
 * Lagging aggregates over the steps seen so far.
 *
 * These are explicitly NOT measurements: they are computed in the browser from
 * an incomplete stream, and every surface that shows them labels them as
 * preliminary. Nothing scored ever comes from here.
 */
export interface LiveAggregates {
  count: number;
  p50Ms: number | null;
  p99Ms: number | null;
  slowest: ProgressStep | null;
  errors: number;
}

export function useLiveAggregates(steps: ProgressStep[]): LiveAggregates {
  return useMemo(() => {
    if (steps.length === 0) {
      return { count: 0, p50Ms: null, p99Ms: null, slowest: null, errors: 0 };
    }
    const durations = steps.map((step) => Math.max(0, step.end_ms - step.start_ms));
    const sorted = durations.slice().sort((a, b) => a - b);
    let slowest = steps[0];
    for (const step of steps) {
      if (step.end_ms - step.start_ms > slowest.end_ms - slowest.start_ms) slowest = step;
    }
    return {
      count: steps.length,
      p50Ms: quantile(sorted, 0.5),
      p99Ms: quantile(sorted, 0.99),
      slowest,
      errors: steps.filter((step) => step.status !== "ok").length,
    };
  }, [steps]);
}

/** Nearest-rank quantile over a pre-sorted array. */
function quantile(sorted: number[], q: number): number | null {
  if (sorted.length === 0) return null;
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(q * sorted.length) - 1));
  return sorted[index];
}
