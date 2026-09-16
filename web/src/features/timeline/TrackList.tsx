/**
 * One lane per concurrent actor, each with a checkbox that adds or removes it
 * from the selection. This is what makes "did the four streams stagger or
 * collide?" a question the interface can answer — the flat waterfall could not
 * express it at all.
 */
import { useCallback, useMemo } from "react";

import { laneSpanStyle } from "@/charts/palette";
import { useI18n } from "@/i18n";
import { fmtSpanDur } from "@/lib/format";
import type { Selection } from "@/lib/selection";
import type { SpanRow } from "@/lib/trace";
import { STAGE_TRACK_ID, type Track } from "@/lib/tracks";

interface Props {
  tracks: Track[];
  rows: SpanRow[];
  t0: number;
  totalS: number;
  selection: Selection;
  onChange: (next: Selection) => void;
}

/** Absolute seconds -> percent across [t0, t0+totalS]. Degenerates to 0 for a
 * zero-length or empty trace rather than dividing by zero into NaN.
 *
 * Exported because `OverviewStrip` draws the same geometry over the same
 * domain directly above these lanes, and used to do it with two private copies
 * of this arithmetic — one inside its marks memo, one at module scope — under
 * a comment claiming the two panes "cannot disagree by construction". They
 * call this now, so they cannot. */
export function pctOf(seconds: number, t0: number, totalS: number): number {
  if (totalS <= 0) return 0;
  return ((seconds - t0) / totalS) * 100;
}

/**
 * Toggle one track's membership in a selection without mutating the input.
 *
 * `Selection.trackIds` is a `ReadonlySet` passed by identity through
 * `clampSelection` and every other pane's memo dependency list; mutating it
 * in place would corrupt those silently, with none of them re-rendering to
 * reveal it.
 */
export function toggleTrack(selection: Selection, trackId: string): Selection {
  const next = new Set(selection.trackIds);
  if (next.has(trackId)) next.delete(trackId);
  else next.add(trackId);
  return { startS: selection.startS, endS: selection.endS, trackIds: next };
}

/**
 * `tracks.ts` bakes English words straight into `Track.label`
 * (`"stream 2"`, `"lane 3"`) and is frozen; `t()` has no interpolation. So
 * localizing the word while keeping the identifying number/key it labels has
 * to happen here: switch on the track's `kind` (a real discriminant `tracks.ts`
 * already sets, not a guess from the label text), strip the fixed English
 * prefix that kind always uses, and recompose with the translated word.
 */
export function trackLabel(track: Track, t: (key: string) => string): string {
  if (track.kind === "lifecycle") {
    // The reserved group packs like any other, so it can be more than one
    // lane (`lifecycle 1`, `lifecycle 2`). Matched on `kind` rather than on
    // an id equal to STAGE_TRACK_ID, which only the first lane would be.
    if (track.label === STAGE_TRACK_ID) return t("timeline.track_lifecycle");
    return `${t("timeline.track_lifecycle")} ${track.label.slice(`${STAGE_TRACK_ID} `.length)}`;
  }
  if (track.kind === "stream") {
    return `${t("timeline.track_stream")} ${track.label.slice("stream ".length)}`;
  }
  if (track.kind === "work") {
    return `${t("timeline.track_lane")} ${track.label.slice("lane ".length)}`;
  }
  return track.label;
}

export function TrackList({ tracks, rows, t0, totalS, selection, onChange }: Props) {
  const { t } = useI18n();
  // The header directly above these lanes already says "N spans" through this
  // key; the lane summaries reuse it rather than introducing a second word
  // for the same thing three rows apart.
  const spansWord = t("trace.spans");
  const byId = useMemo(() => new Map(rows.map((row) => [row.id, row])), [rows]);

  const toggle = useCallback((trackId: string) => onChange(toggleTrack(selection, trackId)), [onChange, selection]);

  const selectAll = useCallback(
    () =>
      onChange({
        startS: selection.startS,
        endS: selection.endS,
        trackIds: new Set(tracks.map((track) => track.id)),
      }),
    [onChange, selection.startS, selection.endS, tracks],
  );
  const allSelected = tracks.length > 0 && tracks.every((track) => selection.trackIds.has(track.id));

  // Marks depend on which tracks are checked, but NOT on the time window
  // (selection.startS/endS): a dash's position and colour are absolute facts
  // about the trace, only its selectedness (checkbox state) changes how it
  // paints. Excluding the window from the dependency list, AND memoizing the
  // JSX itself (not just the data behind it), means dragging the
  // OverviewStrip's brush — which fires on every pointermove — leaves these
  // ~900 <span> elements untouched by reference; only the one
  // selection-window overlay per lane (computed at render scope below, from
  // live `selection.startS/endS`) is rebuilt on each move. Memoizing plain
  // data and re-mapping it to JSX in the render body, as an earlier version
  // of this component did, would still reallocate every element on every
  // parent re-render regardless of the dependency array — this is the same
  // cut `OverviewStrip`'s own `spanMarks` memo makes.
  const lanes = useMemo(
    () =>
      tracks.map((track) => {
        const selected = selection.trackIds.has(track.id);
        const laneRows = track.spanIds.flatMap((id) => {
          const row = byId.get(id);
          return row === undefined ? [] : [row];
        });
        const marks = laneRows.map((row) => {
          const left = pctOf(row.startS, t0, totalS);
          const width = Math.max(pctOf(row.endS, t0, totalS) - left, 0.2);
          const style = laneSpanStyle(row.kind, row.isError, selected);
          return (
            <span
              key={row.id}
              aria-hidden
              className="absolute top-1 h-2 rounded-[1px]"
              style={{ left: `${left}%`, width: `${width}%`, ...style }}
            />
          );
        });
        // The lane's extent, for the textual summary below. Numbers, not a
        // formatted string: `t()` would have to join the memo's dependency
        // list to build one here, and the composition is per lane rather than
        // per span, so the render body is the cheaper place for it.
        return {
          track,
          selected,
          marks,
          count: laneRows.length,
          fromS: laneRows.length > 0 ? Math.min(...laneRows.map((row) => row.startS)) : t0,
          toS: laneRows.length > 0 ? Math.max(...laneRows.map((row) => row.endS)) : t0,
        };
      }),
    [tracks, byId, t0, totalS, selection.trackIds],
  );

  const windowLeft = pctOf(selection.startS, t0, totalS);
  const windowWidth = Math.max(pctOf(selection.endS, t0, totalS) - windowLeft, 0.2);

  return (
    <div className="flex flex-col">
      <div className="flex items-baseline justify-between font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
        <span>{t("timeline.tracks")}</span>
        {/* All tracks unchecked would otherwise be a one-way door: nothing
            else restores them (the strip's "Whole run" reset only touches
            the time window, not trackIds), and recovery from a mis-click on
            a many-lane run would be one click per lane. Hidden once nothing
            is left to select, same as the strip's own reset button. */}
        {!allSelected && (
          <button
            type="button"
            onClick={selectAll}
            className="border-b-2 border-transparent uppercase tracking-[0.06em] transition-colors hover:border-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {t("timeline.select_all")}
          </button>
        )}
      </div>
      {lanes.map(({ track, selected, marks, count, fromS, toS }) => {
        const label = trackLabel(track, t);
        return (
          <div key={track.id} className="flex items-center border-b border-border py-1.5">
            <label className="flex w-36 shrink-0 items-center gap-2 font-mono text-[11px] text-muted-foreground">
              {/* `accent-foreground`: left to itself a native checkbox paints
                  the OS accent colour, which on this page is the only hue in
                  the chrome — and it sits in the one component where hue
                  already means something (a lane's dashes carry span kind).
                  Pointing `accent-color` at the existing foreground token
                  keeps the control achromatic in both themes without
                  replacing it with a div that only looks like a checkbox. */}
              <input
                type="checkbox"
                checked={selected}
                onChange={() => toggle(track.id)}
                aria-label={`${t("timeline.include_track")}: ${label}`}
                className="size-3 accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              {/* Truncated, so the full name has to stay recoverable — a
                  declared stream's lane can be named after a long suite
                  identifier and this column is 9rem wide. Same idiom as the
                  table's name cell. */}
              <span className="truncate" title={label}>
                {label}
              </span>
            </label>
            <div className="relative h-4 flex-1">
              {/* Every dash in this lane is aria-hidden — they are absolutely
                  positioned rectangles, and a screen reader reading 900 of
                  them would be worse than reading none. But reading none is
                  what shipped, and the lanes are where the whole concurrency
                  structure lives: without this a screen-reader user could
                  toggle a track's checkbox and never learn what was in it.
                  The count and the extent are the two facts that make a lane
                  comparable to the one under it, which is the question these
                  rows exist to answer. */}
              <span className="sr-only">{`${count} ${spansWord} · ${fmtSpanDur(fromS - t0)}–${fmtSpanDur(toS - t0)}`}</span>
              {marks}
              {/* The selected time window, drawn over the dashes — same
                  idiom as OverviewStrip's own overlay (border + a faint
                  fill). A track with every span outside this window still
                  renders its lane and this overlay; it just has no dashes
                  inside the tinted region, which is how "nothing happened
                  here in the selected window" reads differently from a track
                  that has dashes in the window but is merely unchecked (those
                  dashes are still visible, only dimmed). No separate empty
                  state is needed. */}
              <span
                aria-hidden
                className="absolute inset-y-0 border-x border-foreground/30 bg-foreground/[0.06]"
                style={{ left: `${windowLeft}%`, width: `${windowWidth}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
