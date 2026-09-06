import { describe, expect, it } from "vitest";

import type { ProgressEvent, ProgressStep } from "@/api";
import { applyEvent, MAX_LOG_LINES, MAX_STEPS, type LogLine } from "@/lib/progressStream";

function accumulator() {
  return {
    steps: [] as ProgressStep[],
    samples: new Map<string, Array<[number, number]>>(),
    logs: [] as LogLine[],
    seq: 0,
  };
}

const event = (seq: number, extra: Partial<ProgressEvent> & { kind: string }): ProgressEvent => ({
  seq,
  t: seq * 10,
  ...extra,
});

describe("applyEvent", () => {
  it("folds a step into the waterfall feed", () => {
    const acc = accumulator();
    applyEvent(acc, event(1, { kind: "step", name: "tpc-h.q7", start_ms: 100, end_ms: 130, parent: "tpc-h.power" }));
    expect(acc.steps).toEqual([
      { name: "tpc-h.q7", start_ms: 100, end_ms: 130, status: "ok", parent: "tpc-h.power" },
    ]);
  });

  it("ignores a replayed event after a reconnect", () => {
    const acc = accumulator();
    applyEvent(acc, event(5, { kind: "step", name: "a", start_ms: 0, end_ms: 1 }));
    applyEvent(acc, event(5, { kind: "step", name: "a", start_ms: 0, end_ms: 1 }));
    applyEvent(acc, event(4, { kind: "step", name: "older", start_ms: 0, end_ms: 1 }));
    expect(acc.steps).toHaveLength(1);
    expect(acc.seq).toBe(5);
  });

  it("bounds the step buffer by dropping the oldest", () => {
    const acc = accumulator();
    for (let i = 1; i <= MAX_STEPS + 10; i += 1) {
      applyEvent(acc, event(i, { kind: "step", name: `q${i}`, start_ms: 0, end_ms: 1 }));
    }
    expect(acc.steps).toHaveLength(MAX_STEPS);
    // The tail survives: a watcher is looking at what just happened.
    expect(acc.steps[acc.steps.length - 1].name).toBe(`q${MAX_STEPS + 10}`);
  });

  it("bounds the log ring buffer", () => {
    const acc = accumulator();
    for (let i = 1; i <= MAX_LOG_LINES + 5; i += 1) {
      applyEvent(acc, event(i, { kind: "log", level: "INFO", msg: `line ${i}` }));
    }
    expect(acc.logs).toHaveLength(MAX_LOG_LINES);
    expect(acc.logs[acc.logs.length - 1].msg).toBe(`line ${MAX_LOG_LINES + 5}`);
  });

  it("groups samples by key", () => {
    const acc = accumulator();
    applyEvent(acc, event(1, { kind: "sample", key: "lat", value: 1.5 }));
    applyEvent(acc, event(2, { kind: "sample", key: "lat", value: 2.5 }));
    applyEvent(acc, event(3, { kind: "sample", key: "other", value: 9 }));
    expect(acc.samples.get("lat")).toEqual([
      [10, 1.5],
      [20, 2.5],
    ]);
    expect(acc.samples.get("other")).toHaveLength(1);
  });

  it("skips a malformed event rather than corrupting the feed", () => {
    const acc = accumulator();
    applyEvent(acc, event(1, { kind: "step" })); // no name
    applyEvent(acc, event(2, { kind: "sample", key: "lat" })); // no value
    expect(acc.steps).toHaveLength(0);
    expect(acc.samples.size).toBe(0);
    // seq still advanced: those events happened, we just had nothing to fold.
    expect(acc.seq).toBe(2);
  });

  it("leaves state-bearing kinds to the state frame", () => {
    const acc = accumulator();
    applyEvent(acc, event(1, { kind: "stage", stage: "EXECUTE", status: "start" }));
    applyEvent(acc, event(2, { kind: "progress", label: "Power", completed: 1, total: 22 }));
    expect(acc.steps).toHaveLength(0);
    expect(acc.logs).toHaveLength(0);
  });
});
