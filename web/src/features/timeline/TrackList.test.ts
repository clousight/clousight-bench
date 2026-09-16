import { describe, expect, it } from "vitest";

import { KIND_SLOTS } from "@/charts/palette";
import { laneSpanStyle, pctOf, toggleTrack, trackLabel } from "@/features/timeline/TrackList";
import type { Selection } from "@/lib/selection";
import { STAGE_TRACK_ID, type Track } from "@/lib/tracks";

describe("pctOf", () => {
  it("maps a second to its percentage across [t0, t0+totalS]", () => {
    expect(pctOf(25, 10, 40)).toBeCloseTo(37.5, 6);
  });

  it("degrades to 0 instead of NaN when totalS is 0 (empty/zero-length trace)", () => {
    expect(pctOf(25, 10, 0)).toBe(0);
  });

  it("does not clamp: a point past the trace's end still returns a percentage over 100", () => {
    // Unlike secondsAtX (pixel input, must clamp to a valid position), pctOf
    // takes a trace-domain second and is used to place both spans and the
    // selection window, neither of which is guaranteed to stay inside
    // [t0, t0+totalS] (a straddling span, a not-yet-clamped selection). If
    // this clamped, an out-of-range span would silently paint at the edge
    // rather than actually off the visible strip.
    expect(pctOf(60, 10, 40)).toBeGreaterThan(100);
  });
});

describe("laneSpanStyle", () => {
  it("colours by kind when there is no error", () => {
    const style = laneSpanStyle("query", false, true);
    expect(style.backgroundColor).toBe(`var(${KIND_SLOTS.query})`);
  });

  it("gives two different kinds two different colours", () => {
    // Would not fail if both kinds silently fell back to the same slot.
    const a = laneSpanStyle("phase", false, true);
    const b = laneSpanStyle("query", false, true);
    expect(a.backgroundColor).not.toBe(b.backgroundColor);
  });

  it("an error overrides hue regardless of kind", () => {
    const query = laneSpanStyle("query", true, true);
    const tool = laneSpanStyle("tool_call", true, true);
    expect(query.backgroundColor).toBe("var(--status-critical)");
    expect(tool.backgroundColor).toBe("var(--status-critical)");
  });

  it("falls back to the span slot for an unrecognised kind", () => {
    expect(laneSpanStyle("mystery-kind", false, true).backgroundColor).toBe(`var(${KIND_SLOTS.span})`);
  });

  it("gives the lifecycle kind a muted-foreground grey, not --chart-axis", () => {
    // --chart-axis already carries a baked-in alpha in dark mode, which this
    // function's own opacity would multiply into an unreadably faint dash.
    // If this regressed to KIND_SLOTS.lifecycle directly, this test fails.
    expect(laneSpanStyle("lifecycle", false, true).backgroundColor).toBe("var(--muted-foreground)");
    expect(laneSpanStyle("lifecycle", false, true).backgroundColor).not.toBe(`var(${KIND_SLOTS.lifecycle})`);
  });

  it("selection state changes opacity, never hue", () => {
    const on = laneSpanStyle("query", false, true);
    const off = laneSpanStyle("query", false, false);
    expect(on.backgroundColor).toBe(off.backgroundColor);
    expect(on.opacity).toBeGreaterThan(off.opacity);
  });

  it("keeps the deselected floor at 0.4, not low enough to read as an empty lane", () => {
    expect(laneSpanStyle("query", false, false).opacity).toBe(0.4);
  });

  it("an error is more opaque than a normal span in the same selection state", () => {
    // The strip and the lane must agree that an error pops above the
    // surrounding spans, not recede below them, in both the selected and the
    // deselected state.
    expect(laneSpanStyle("query", true, true).opacity).toBeGreaterThan(laneSpanStyle("query", false, true).opacity);
    expect(laneSpanStyle("query", true, false).opacity).toBeGreaterThan(
      laneSpanStyle("query", false, false).opacity,
    );
  });
});

describe("toggleTrack", () => {
  function selectionWith(ids: string[]): Selection {
    return { startS: 3, endS: 9, trackIds: new Set(ids) };
  }

  it("adds a track that is absent", () => {
    const result = toggleTrack(selectionWith(["a"]), "b");
    expect([...result.trackIds].sort()).toEqual(["a", "b"]);
  });

  it("removes a track that is present", () => {
    const result = toggleTrack(selectionWith(["a", "b"]), "b");
    expect([...result.trackIds]).toEqual(["a"]);
  });

  it("preserves startS/endS untouched", () => {
    const result = toggleTrack(selectionWith(["a"]), "b");
    expect(result.startS).toBe(3);
    expect(result.endS).toBe(9);
  });

  it("does not mutate the input selection's trackIds set", () => {
    const input = selectionWith(["a"]);
    const originalSet = input.trackIds;
    toggleTrack(input, "b");
    // Same object identity as before the call, and still exactly what it was
    // -- if toggleTrack mutated in place instead of copying, `originalSet`
    // would now contain "b" too, corrupting every other pane holding the
    // same reference (clampSelection, the strip's drag handlers).
    expect(input.trackIds).toBe(originalSet);
    expect([...input.trackIds]).toEqual(["a"]);
  });
});

describe("trackLabel", () => {
  const t = (key: string) => `[${key}]`;

  it("uses the lifecycle key for the reserved lane, ignoring its raw label", () => {
    const track: Track = { id: STAGE_TRACK_ID, label: STAGE_TRACK_ID, kind: "lifecycle", spanIds: [] };
    expect(trackLabel(track, t)).toBe("[timeline.track_lifecycle]");
  });

  it("translates the word but keeps the stream's identifying suffix", () => {
    const track: Track = { id: "stream:2", label: "stream 2", kind: "stream", spanIds: [] };
    expect(trackLabel(track, t)).toBe("[timeline.track_stream] 2");
  });

  it("translates the word but keeps the lane's identifying number", () => {
    const track: Track = { id: "lane:0:1", label: "lane 3", kind: "work", spanIds: [] };
    expect(trackLabel(track, t)).toBe("[timeline.track_lane] 3");
  });
});
