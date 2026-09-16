/**
 * One lane per concurrent actor, each with a checkbox that adds or removes it
 * from the selection. This is what makes "did the four streams stagger or
 * collide?" a question the interface can answer — the flat waterfall could not
 * express it at all.
 */
import { useCallback, useMemo } from "react";

import { KIND_SLOTS } from "@/charts/palette";
import { useI18n } from "@/i18n";
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
 * zero-length or empty trace rather than dividing by zero into NaN — the same
 * guard OverviewStrip's `pct()` applies, kept here as one function shared by
 * every lane instead of the two identical inline copies that component
 * carries (a gap Task 4 flagged and deferred). */
export function pctOf(seconds: number, t0: number, totalS: number): number {
  if (totalS <= 0) return 0;
  return ((seconds - t0) / totalS) * 100;
}

/**
 * A span mark's paint, as three independent facts:
 *
 * - Hue carries kind identity, read from the same `KIND_SLOTS` vocabulary the
 *   waterfall and the aggregate view use, so a `query` span is the same
 *   colour everywhere it appears. `OverviewStrip` imports this same function
 *   (with `selected` pinned to `true`, since it has no per-track checkbox) so
 *   the two panes are, by construction, incapable of disagreeing about a
 *   given span's paint — not just visually tuned to match.
 * - An error always overrides hue with `--status-critical`, regardless of
 *   kind, and is always the single most opaque mark in the lane (1.0
 *   selected / 0.6 deselected — both above the corresponding non-error
 *   values) so that on a shared x-axis an error pops above the surrounding
 *   spans rather than receding below them.
 * - The lifecycle lane is the one track guaranteed to exist on every trace,
 *   so its colour has to survive being drawn as a 2px dash rather than the
 *   BAR_HEIGHT-tall echarts bar with a text label beside it that
 *   `--chart-axis` was tuned for. In dark mode that token already carries a
 *   baked-in 22% alpha (`index.css`), and multiplying that by this
 *   function's own opacity collapsed a selected lifecycle dash to ~19% white
 *   and a deselected one to ~4% — indistinguishable from an empty lane. It
 *   does not need a categorical slot to read as recessive, so it takes
 *   `--muted-foreground`: an opaque, contrast-tuned grey already used
 *   everywhere else in the app for de-emphasised-but-legible text.
 * - The track's checkbox only ever changes opacity, never hue: toggling a
 *   track off dims it so it reads as "present but not selected," not as a
 *   different kind of span or a span that stopped being an error. The
 *   deselected floor is 0.4, matching `OverviewStrip`'s own dimmest mark
 *   (`bg-chart-1/40`, pre-this-change) — low enough to read as background,
 *   not so low it collapses into "no span here at all".
 */
export function laneSpanStyle(
  kind: string,
  isError: boolean,
  selected: boolean,
): { backgroundColor: string; opacity: number } {
  if (isError) {
    return { backgroundColor: "var(--status-critical)", opacity: selected ? 1 : 0.6 };
  }
  const backgroundColor =
    kind === "lifecycle" ? "var(--muted-foreground)" : `var(${KIND_SLOTS[kind] ?? KIND_SLOTS.span})`;
  return { backgroundColor, opacity: selected ? 0.85 : 0.4 };
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
  if (track.id === STAGE_TRACK_ID) return t("timeline.track_lifecycle");
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
        const marks = track.spanIds.flatMap((id) => {
          const row = byId.get(id);
          if (row === undefined) return [];
          const left = pctOf(row.startS, t0, totalS);
          const width = Math.max(pctOf(row.endS, t0, totalS) - left, 0.2);
          const style = laneSpanStyle(row.kind, row.isError, selected);
          return [
            <span
              key={id}
              aria-hidden
              className="absolute top-1 h-2 rounded-[1px]"
              style={{ left: `${left}%`, width: `${width}%`, ...style }}
            />,
          ];
        });
        return { track, selected, marks };
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
            className="border-b-2 border-transparent uppercase tracking-[0.06em] transition-colors hover:border-foreground hover:text-foreground"
          >
            {t("timeline.select_all")}
          </button>
        )}
      </div>
      {lanes.map(({ track, selected, marks }) => {
        const label = trackLabel(track, t);
        return (
          <div key={track.id} className="flex items-center border-b border-border py-1.5">
            <label className="flex w-36 shrink-0 items-center gap-2 font-mono text-[11px] text-muted-foreground">
              <input
                type="checkbox"
                checked={selected}
                onChange={() => toggle(track.id)}
                aria-label={`${t("timeline.include_track")}: ${label}`}
                className="size-3"
              />
              <span className="truncate">{label}</span>
            </label>
            <div className="relative h-4 flex-1">
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
