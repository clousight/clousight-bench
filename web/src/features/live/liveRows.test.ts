import { describe, expect, it } from "vitest";

import { stepsToRows } from "@/features/live/liveRows";

const step = (name: string, start: number, end: number, parent = "", status = "ok") => ({
  name,
  start_ms: start,
  end_ms: end,
  parent,
  status,
});

describe("stepsToRows", () => {
  it("nests a step under the phase that named it", () => {
    const { rows } = stepsToRows([
      step("tpc-h.power", 0, 400),
      step("tpc-h.q7", 100, 130, "tpc-h.power"),
    ]);
    const q7 = rows.find((row) => row.name === "tpc-h.q7");
    expect(q7?.depth).toBe(1);
    expect(q7?.ancestors).toEqual(["tpc-h.power"]);
  });

  it("gives repeated names distinct ids so concurrent streams do not collide", () => {
    const { rows } = stepsToRows([
      step("tpc-h.q21", 10, 20, "tpc-h.stream1"),
      step("tpc-h.q21", 12, 25, "tpc-h.stream2"),
    ]);
    expect(new Set(rows.map((row) => row.id)).size).toBe(2);
  });

  it("sorts by start time", () => {
    const { rows } = stepsToRows([step("b", 50, 60), step("a", 10, 20)]);
    expect(rows.map((row) => row.name)).toEqual(["a", "b"]);
  });

  it("survives a parent that was never reported", () => {
    const { rows } = stepsToRows([step("orphan", 10, 20, "never-seen")]);
    expect(rows[0].depth).toBe(0);
    expect(rows[0].ancestors).toEqual([]);
  });

  it("does not loop on a self-referencing step", () => {
    const { rows } = stepsToRows([step("loop", 0, 10, "loop")]);
    expect(rows[0].depth).toBe(0);
  });

  it("marks a non-ok step as an error and clamps a negative duration", () => {
    const { rows } = stepsToRows([step("bad", 100, 50, "", "failed")]);
    expect(rows[0].isError).toBe(true);
    expect(rows[0].endS).toBe(rows[0].startS);
  });

  it("colours a query step as a query", () => {
    const { rows } = stepsToRows([step("tpc-h.q7", 0, 1), step("tpc-h.load", 0, 1)]);
    expect(rows.find((row) => row.name === "tpc-h.q7")?.kind).toBe("db_query");
    expect(rows.find((row) => row.name === "tpc-h.load")?.kind).toBe("stage");
  });
});
