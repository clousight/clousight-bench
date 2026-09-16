import { describe, expect, it } from "vitest";

import { pctOf, toggleTrack, trackLabel, TrackList } from "@/features/timeline/TrackList";
import type { Selection } from "@/lib/selection";
import type { SpanRow } from "@/lib/trace";
import { STAGE_TRACK_ID, type Track } from "@/lib/tracks";
import { focusableTags, renderMarkup } from "@/test/render";

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
    // The id is `lifecycle:0`, not `lifecycle`: the reserved group is packed
    // like every other one, so its lanes are numbered. Matching on the id
    // against STAGE_TRACK_ID would fall through to the raw English label.
    const track: Track = { id: `${STAGE_TRACK_ID}:0`, label: STAGE_TRACK_ID, kind: "lifecycle", spanIds: [] };
    expect(trackLabel(track, t)).toBe("[timeline.track_lifecycle]");
  });

  it("keeps the lane number when the lifecycle group needed more than one lane", () => {
    // The usual shape: `csbench.run` on one lane, the stages it contains on
    // the next. Both must be distinguishable in the checkbox list.
    const track: Track = { id: `${STAGE_TRACK_ID}:1`, label: "lifecycle 2", kind: "lifecycle", spanIds: [] };
    expect(trackLabel(track, t)).toBe("[timeline.track_lifecycle] 2");
  });

  it("translates the word but keeps the stream's identifying suffix", () => {
    const track: Track = { id: "stream:2:0", label: "stream 2", kind: "stream", spanIds: [] };
    expect(trackLabel(track, t)).toBe("[timeline.track_stream] 2");
  });

  it("keeps the whole suffix when a stream packed into more than one lane", () => {
    // `tracks.ts` labels a split stream "stream 2.2". The stream number is the
    // identity a reader needs; a prefix slice that took a fixed number of
    // characters, or stopped at the first token, would drop the lane it names.
    const track: Track = { id: "stream:2:1", label: "stream 2.2", kind: "stream", spanIds: [] };
    expect(trackLabel(track, t)).toBe("[timeline.track_stream] 2.2");
  });

  it("translates the word but keeps the lane's identifying number", () => {
    const track: Track = { id: "lane:0:1", label: "lane 3", kind: "work", spanIds: [] };
    expect(trackLabel(track, t)).toBe("[timeline.track_lane] 3");
  });
});

describe("<TrackList>", () => {
  function span(id: string, startS: number, endS: number): SpanRow {
    return {
      id,
      name: id,
      kind: "query",
      startS,
      endS,
      status: "ok",
      isError: false,
      error: null,
      attrs: {},
      parentId: null,
      depth: 0,
      ancestors: [],
    };
  }
  const rows = [span("a", 0, 2), span("b", 4, 6)];
  const tracks: Track[] = [
    { id: "lane:0:0", label: "lane 1", kind: "work", spanIds: ["a"] },
    { id: "lane:1:0", label: "lane 2", kind: "work", spanIds: ["b"] },
  ];
  const noop = () => {};

  it("gives every track a row whose checkbox reports whether it is included", () => {
    // The second lane is unchecked. Both lanes still render — an excluded
    // track keeps its dashes (dimmed) so "not selected" reads differently
    // from "nothing happened here".
    const selection: Selection = { startS: 0, endS: 10, trackIds: new Set(["lane:0:0"]) };
    const markup = renderMarkup(
      <TrackList tracks={tracks} rows={rows} t0={0} totalS={10} selection={selection} onChange={noop} />,
    );
    expect(markup).toMatch(/selection: lane 1"[^>]*checked=""/);
    expect(markup).not.toMatch(/selection: lane 2"[^>]*checked=""/);
    // Both rows exist; only one is checked.
    expect([...markup.matchAll(/type="checkbox"/g)]).toHaveLength(2);
  });

  it("keeps the lane checkbox achromatic instead of painting the OS accent", () => {
    // Chrome is achromatic on this branch and hue carries span kind — and
    // these checkboxes sit in the one component where the dashes beside them
    // already use hue that way, so an OS-blue checkbox is a hue competing
    // with data at a distance of 8px. A class-presence assertion is exactly
    // what a string render can see; the colour it resolves to is not.
    const selection: Selection = { startS: 0, endS: 10, trackIds: new Set(["lane:0:0"]) };
    const markup = renderMarkup(
      <TrackList tracks={tracks} rows={rows} t0={0} totalS={10} selection={selection} onChange={noop} />,
    );
    for (const checkbox of markup.matchAll(/<input[^>]*type="checkbox"[^>]*>/g)) {
      expect(checkbox[0]).toContain("accent-foreground");
    }
  });

  it("gives each lane a textual summary, since its dashes are all aria-hidden", () => {
    // The lanes carry the whole concurrency structure and a screen reader got
    // nothing from them: ~900 absolutely-positioned spans, every one
    // aria-hidden, and no equivalent. Count and extent are what make one lane
    // comparable to the next. "spans" is the word the header three rows above
    // already uses, rather than a second name for the same thing.
    const selection: Selection = { startS: 0, endS: 10, trackIds: new Set(["lane:0:0"]) };
    const markup = renderMarkup(
      <TrackList tracks={tracks} rows={rows} t0={0} totalS={10} selection={selection} onChange={noop} />,
    );
    // Lane 1 holds span "a" [0,2]; lane 2 holds span "b" [4,6].
    expect(markup).toContain("1 spans · 0.00ms–2.00s");
    expect(markup).toContain("1 spans · 4.00s–6.00s");
  });

  it("keeps a truncated lane label recoverable from its title", () => {
    // The label column is 9rem; a declared stream can be named after a long
    // suite identifier, and the truncation is then the only thing between the
    // reader and the identity the lane exists to carry.
    const selection: Selection = { startS: 0, endS: 10, trackIds: new Set(["lane:0:0"]) };
    const markup = renderMarkup(
      <TrackList tracks={tracks} rows={rows} t0={0} totalS={10} selection={selection} onChange={noop} />,
    );
    expect(markup).toContain('title="lane 1"');
    expect(markup).toContain('title="lane 2"');
  });

  it("gives every focusable element the app's focus ring, not the UA outline", () => {
    // Deferred #9 and #28 were one defect counted twice: the timeline feature
    // fell through to the user agent's default outline while every other
    // control in the app used the --ring token. Asserted per element rather
    // than over the markup, because one ringed element otherwise satisfies
    // the whole string.
    const selection: Selection = { startS: 0, endS: 10, trackIds: new Set(["lane:0:0"]) };
    const markup = renderMarkup(
      <TrackList tracks={tracks} rows={rows} t0={0} totalS={10} selection={selection} onChange={noop} />,
    );
    const tags = focusableTags(markup);
    // Two checkboxes and the "select all" button, which shows because lane 2
    // is unchecked.
    expect(tags).toHaveLength(3);
    for (const tag of tags) expect(tag, tag).toContain("focus-visible:ring-ring");
  });

  it("draws the selected window over each lane at the window's own percentages", () => {
    // 2.5 -> 7.5 s of a 10 s trace is left 25%, width 50%; span "a" [0,2] is
    // the 20% mark ahead of it. The overlay is how a reader sees which part of
    // a lane the numbers below are about, and it is pure arithmetic on
    // absolute seconds, so it is observable without a layout engine.
    const selection: Selection = {
      startS: 2.5,
      endS: 7.5,
      trackIds: new Set(["lane:0:0", "lane:1:0"]),
    };
    const markup = renderMarkup(
      <TrackList tracks={tracks} rows={rows} t0={0} totalS={10} selection={selection} onChange={noop} />,
    );
    expect(markup).toContain("left:0%;width:20%");
    expect([...markup.matchAll(/left:25%;width:50%/g)]).toHaveLength(tracks.length);
  });
});
