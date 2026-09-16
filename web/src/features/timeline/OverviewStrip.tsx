/**
 * The whole run at a glance, with a draggable window over it. Dragging is
 * pointer-based rather than a library: the strip is one div, the geometry is
 * one division, and a brush library would be more code than the thing it draws.
 */
import { useCallback, useRef } from "react";

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

export function OverviewStrip({ rows, t0, totalS, selection, onChange }: Props) {
  const { t } = useI18n();
  const stripRef = useRef<HTMLDivElement | null>(null);
  const dragFrom = useRef<number | null>(null);

  const secondsAt = useCallback(
    (clientX: number): number => {
      const el = stripRef.current;
      if (el === null || totalS <= 0) return t0;
      const box = el.getBoundingClientRect();
      const ratio = Math.min(Math.max((clientX - box.left) / box.width, 0), 1);
      return t0 + ratio * totalS;
    },
    [t0, totalS],
  );

  const onPointerDown = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      event.currentTarget.setPointerCapture(event.pointerId);
      dragFrom.current = secondsAt(event.clientX);
    },
    [secondsAt],
  );

  const onPointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const from = dragFrom.current;
      if (from === null) return;
      const to = secondsAt(event.clientX);
      onChange(
        clampSelection(
          { startS: Math.min(from, to), endS: Math.max(from, to), trackIds: selection.trackIds },
          t0,
          t0 + totalS,
        ),
      );
    },
    [onChange, secondsAt, selection.trackIds, t0, totalS],
  );

  const endDrag = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const from = dragFrom.current;
      dragFrom.current = null;
      if (from === null) return;
      const to = secondsAt(event.clientX);
      if (from !== to) return;
      // A click without movement (or a drag that snapped back to its origin)
      // yields startS === endS. selectSpans matches by strict inequality, so a
      // degenerate point selection would show every pane as empty rather than
      // "everything" or "nothing" — neither of which is what a tap communicates.
      // Treat it as "whole run" so a stray click never looks like data loss.
      onChange({ startS: t0, endS: t0 + totalS, trackIds: selection.trackIds });
    },
    [onChange, secondsAt, selection.trackIds, t0, totalS],
  );

  const reset = useCallback(() => {
    onChange({ startS: t0, endS: t0 + totalS, trackIds: selection.trackIds });
  }, [onChange, selection.trackIds, t0, totalS]);

  const pct = (seconds: number) => (totalS <= 0 ? 0 : ((seconds - t0) / totalS) * 100);
  const selectedS = Math.max(selection.endS - selection.startS, 0);
  const whole = selectedS >= totalS - 1e-9;

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
        role="slider"
        aria-label={t("timeline.drag_hint")}
        aria-valuemin={0}
        aria-valuemax={Math.round(totalS)}
        aria-valuenow={Math.round(selectedS)}
        tabIndex={0}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        className="relative h-7 cursor-col-resize touch-none border-y border-border bg-muted/20"
      >
        {rows.map((row) => (
          <span
            key={row.id}
            aria-hidden
            className="absolute top-1 h-1.5 rounded-[1px] bg-chart-1/40"
            style={{
              left: `${pct(row.startS)}%`,
              width: `${Math.max(pct(row.endS) - pct(row.startS), 0.15)}%`,
            }}
          />
        ))}
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
