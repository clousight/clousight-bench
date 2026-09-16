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
 *   colour everywhere it appears.
 * - An error always overrides hue with `--status-critical`, regardless of
 *   kind — matching `OverviewStrip`'s `bg-status-critical/70` for
 *   `row.isError`, so the same span never disagrees about being an error
 *   depending on which pane you are looking at.
 * - The track's checkbox only ever changes opacity, never hue: toggling a
 *   track off dims it so it reads as "present but not selected," not as a
 *   different kind of span or a span that stopped being an error.
 */
export function laneSpanStyle(
  kind: string,
  isError: boolean,
  selected: boolean,
): { backgroundColor: string; opacity: number } {
  const backgroundColor = isError
    ? "var(--status-critical)"
    : `var(${KIND_SLOTS[kind] ?? KIND_SLOTS.span})`;
  const opacity = isError ? (selected ? 0.7 : 0.25) : selected ? 0.85 : 0.2;
  return { backgroundColor, opacity };
}

interface LaneMark {
  id: string;
  left: number;
  width: number;
  style: { backgroundColor: string; opacity: number };
}

export function TrackList({ tracks, rows, t0, totalS, selection, onChange }: Props) {
  const { t } = useI18n();
  const byId = useMemo(() => new Map(rows.map((row) => [row.id, row])), [rows]);

  const toggle = useCallback(
    (trackId: string) => {
      const next = new Set(selection.trackIds);
      if (next.has(trackId)) next.delete(trackId);
      else next.add(trackId);
      onChange({ startS: selection.startS, endS: selection.endS, trackIds: next });
    },
    [onChange, selection.endS, selection.startS, selection.trackIds],
  );

  // Marks depend on which tracks are checked, but NOT on the time window
  // (selection.startS/endS): a dash's position and colour are absolute facts
  // about the trace, only its selectedness (checkbox state) changes how it
  // paints. Excluding the window from the dependency list means dragging the
  // OverviewStrip's brush — which fires on every pointermove — recomputes
  // only the one selection-window overlay per lane below, not every dash in
  // every track, the same cut OverviewStrip's own `spanMarks` memo makes.
  const lanes = useMemo(
    () =>
      tracks.map((track) => {
        const selected = selection.trackIds.has(track.id);
        const marks: LaneMark[] = [];
        for (const id of track.spanIds) {
          const row = byId.get(id);
          if (row === undefined) continue;
          const left = pctOf(row.startS, t0, totalS);
          const width = Math.max(pctOf(row.endS, t0, totalS) - left, 0.2);
          marks.push({ id, left, width, style: laneSpanStyle(row.kind, row.isError, selected) });
        }
        return { track, selected, marks };
      }),
    [tracks, byId, t0, totalS, selection.trackIds],
  );

  const windowLeft = pctOf(selection.startS, t0, totalS);
  const windowWidth = Math.max(pctOf(selection.endS, t0, totalS) - windowLeft, 0.2);

  return (
    <div className="flex flex-col">
      <div className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
        {t("timeline.tracks")}
      </div>
      {lanes.map(({ track, selected, marks }) => {
        const label = track.id === STAGE_TRACK_ID ? t("timeline.track_lifecycle") : track.label;
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
              {marks.map((mark) => (
                <span
                  key={mark.id}
                  aria-hidden
                  className="absolute top-1 h-2 rounded-[1px]"
                  style={{ left: `${mark.left}%`, width: `${mark.width}%`, ...mark.style }}
                />
              ))}
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
