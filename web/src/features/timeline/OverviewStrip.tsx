/**
 * The whole run at a glance, with a draggable window over it. Dragging is
 * pointer-based rather than a library: the strip is one div, the geometry is
 * one division, and a brush library would be more code than the thing it draws.
 */
import { useCallback, useMemo, useRef } from "react";

import { laneSpanStyle } from "@/charts/palette";
import { useI18n } from "@/i18n";
import { fmtDur } from "@/lib/format";
import { clampSelection, type Selection } from "@/lib/selection";
import type { SpanRow } from "@/lib/trace";

interface Props {
  rows: SpanRow[];
  t0: number;
  totalS: number;
  selection: Selection;
  onChange: (next: Selection) => void;
}

/** Pixels of press-to-release travel that still counts as a click, not a
 * drag — the platform convention for the click/drag boundary, not a value we
 * invented for this component. */
export const CLICK_SLOP_PX = 3;

/** A cheap stand-in for the one field of DOMRect the geometry needs, so the
 * resolution logic below can be exercised without a DOM. */
export interface StripRect {
  left: number;
  width: number;
}

/** Pixel position -> trace second, given a (possibly stale-free, cached)
 * strip rect. Degenerates to `t0` when there is nothing to place a ratio
 * against — an empty trace, or a not-yet-laid-out strip — rather than
 * dividing by zero into `NaN`. */
export function secondsAtX(clientX: number, rect: StripRect, t0: number, totalS: number): number {
  if (totalS <= 0 || rect.width <= 0) return t0;
  const ratio = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
  return t0 + ratio * totalS;
}

/**
 * What a completed drag gesture should commit.
 *
 * Two independent reasons collapse to the same answer, "select the whole
 * run": (1) the release is within `CLICK_SLOP_PX` of the press — a click,
 * however far `from`/`to` wandered on tremor alone, since on a long trace a
 * single pixel of unintentional movement is already a visible sliver of a
 * window; (2) `from === to` in seconds regardless of pixel travel — an empty
 * or not-yet-measured strip where every position resolves to the same
 * instant, so "keep what the drag built" would keep a point.
 *
 * A real drag returns null: the continuous updates already applied during
 * pointermove are the answer, and recomputing them here would just be the
 * same arithmetic twice.
 */
export function resolveDrag(
  pressX: number,
  releaseX: number,
  from: number,
  to: number,
  t0: number,
  totalS: number,
): { startS: number; endS: number } | null {
  const isClick = Math.abs(releaseX - pressX) <= CLICK_SLOP_PX;
  const isDegenerate = from === to;
  if (isClick || isDegenerate) return { startS: t0, endS: t0 + totalS };
  return null;
}

interface DragState {
  pressX: number;
  fromS: number;
  rect: StripRect;
}

export function OverviewStrip({ rows, t0, totalS, selection, onChange }: Props) {
  const { t } = useI18n();
  const stripRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);

  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      if (event.button !== 0) return;
      const el = stripRef.current;
      if (el === null) return;
      const rect = el.getBoundingClientRect();
      event.currentTarget.setPointerCapture(event.pointerId);
      dragRef.current = {
        pressX: event.clientX,
        fromS: secondsAtX(event.clientX, rect, t0, totalS),
        rect,
      };
    },
    [t0, totalS],
  );

  const onPointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      if (drag === null) return;
      const to = secondsAtX(event.clientX, drag.rect, t0, totalS);
      onChange(
        clampSelection(
          {
            startS: Math.min(drag.fromS, to),
            endS: Math.max(drag.fromS, to),
            trackIds: selection.trackIds,
          },
          t0,
          t0 + totalS,
        ),
      );
    },
    [onChange, selection.trackIds, t0, totalS],
  );

  const endDrag = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current;
      dragRef.current = null;
      if (drag === null) return;
      const to = secondsAtX(event.clientX, drag.rect, t0, totalS);
      const override = resolveDrag(drag.pressX, event.clientX, drag.fromS, to, t0, totalS);
      if (override !== null) onChange({ ...override, trackIds: selection.trackIds });
    },
    [onChange, selection.trackIds, t0, totalS],
  );

  const reset = useCallback(() => {
    onChange({ startS: t0, endS: t0 + totalS, trackIds: selection.trackIds });
  }, [onChange, selection.trackIds, t0, totalS]);

  const selectedS = Math.max(selection.endS - selection.startS, 0);
  const whole = selectedS >= totalS - 1e-9;

  // Colour comes from `laneSpanStyle`, the exact same function `TrackList`
  // uses for its lane dashes (with `selected` pinned to `true` -- the strip
  // has no per-track checkbox, it always shows everything at the "included"
  // strength). Importing the one function rather than re-deriving matching
  // constants here means the two panes cannot drift apart the way
  // `Waterfall.tsx`'s hand-duplicated kind swatch already has: a `query` span
  // is now colour-identical in the strip and in the lane directly beneath it,
  // by construction, not by two people tuning two numbers to agree today.
  const spanMarks = useMemo(() => {
    const pct = (seconds: number) => (totalS <= 0 ? 0 : ((seconds - t0) / totalS) * 100);
    return rows.map((row) => {
      const style = laneSpanStyle(row.kind, row.isError, true);
      return (
        <span
          key={row.id}
          aria-hidden
          className="absolute top-1 h-1.5 rounded-[1px]"
          style={{
            left: `${pct(row.startS)}%`,
            width: `${Math.max(pct(row.endS) - pct(row.startS), 0.15)}%`,
            ...style,
          }}
        />
      );
    });
  }, [rows, t0, totalS]);

  const pct = (seconds: number) => (totalS <= 0 ? 0 : ((seconds - t0) / totalS) * 100);

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline gap-2 font-mono text-[11px] text-muted-foreground">
        <span className="uppercase tracking-[0.1em]">{t("timeline.selection")}</span>
        <span className="tabular-nums font-mono text-foreground">{fmtDur(selectedS)}</span>
        <span>{t("timeline.of")}</span>
        <span className="tabular-nums font-mono">{fmtDur(totalS)}</span>
        {!whole && (
          <button
            type="button"
            onClick={reset}
            className="ml-auto border-b-2 border-transparent uppercase tracking-[0.06em] transition-colors hover:border-foreground hover:text-foreground"
          >
            {t("timeline.reset")}
          </button>
        )}
      </div>

      <div
        ref={stripRef}
        role="img"
        aria-label={t("timeline.drag_hint")}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        className="relative h-7 cursor-col-resize touch-none border-y border-border bg-muted/20"
      >
        {spanMarks}
        <span
          aria-hidden
          className="absolute inset-y-0 border-x border-foreground bg-foreground/[0.06]"
          style={{ left: `${pct(selection.startS)}%`, width: `${Math.max(pct(selection.endS) - pct(selection.startS), 0.2)}%` }}
        />
      </div>

      <div className="flex justify-between font-mono text-[10px] tabular-nums text-muted-foreground">
        <span>0</span>
        <span>{fmtDur(totalS)}</span>
      </div>
    </div>
  );
}
