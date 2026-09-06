import { describe, expect, it } from "vitest";

import type { ProgressState } from "@/api";
import { stillRunning } from "@/features/live/LiveStrip";

const state = (run_id: string, status: string): ProgressState =>
  ({ run_id, status }) as ProgressState;

describe("stillRunning", () => {
  it("excludes runs the progress plane is only holding through its grace window", () => {
    // /api/progress legitimately returns a finished run for TERMINAL_GRACE_S so
    // a subscriber can read its handoff. Counting those as "running" — with a
    // clock ticking on them — is a lie the reader cannot catch.
    const runs = [
      state("a", "running"),
      state("b", "completed"),
      state("c", "failed"),
      state("d", "interrupted"),
      state("e", "abandoned"),
    ];
    expect(stillRunning(runs).map((run) => run.run_id)).toEqual(["a"]);
  });

  it("is empty rather than undefined for an empty list", () => {
    expect(stillRunning([])).toEqual([]);
  });
});
