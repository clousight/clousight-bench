/**
 * The docked span detail: the right-hand panel a selected row fills in,
 * replacing the "expand a block that shoves every row below it out of view"
 * pattern the old transcript cards used.
 *
 * **Two durations, never one, and never printed under the other's label.**
 * `窗口内` (`trace.col_window`) is this span's duration intersected with the
 * CURRENT window — an observation that moves as the reader zooms. `合计`
 * (`trace.col_total`) is the span's true total — a fact that never moves. An
 * earlier slice of this rebuild shipped a single bolded number that silently
 * meant the clipped value while its neighbours showed true totals, and two
 * independent reviewers caught it; this component's whole reason to keep
 * both columns, each under its own label, is to make that mistake impossible
 * to reintroduce.
 *
 * **The share-of-window mark is positioned through `place()`, never a
 * fraction of the run.** A `pctOf(totalS)` fraction cannot narrow when the
 * viewport does — see `viewport.ts`'s module docstring — so this panel reads
 * position and visibility from `place(view, row.startS, row.endS)` exactly
 * like `TraceTree`'s bars do, and draws nothing when the span is entirely
 * outside the window rather than some fraction of a wider total.
 *
 * **Attributes are data, not markup.** They arrive on the traced system's own
 * `attrs` bag, so they are rendered as plain React text children — escaped
 * by default — and this file must never bypass that escaping.
 *
 * Colour comes from `laneSpanStyle`, never a hardcoded hex: Task 9 rewrites
 * the palette this reads from, and this component must inherit that change
 * rather than surviving it as a second, unmigrated copy.
 */

import type { JSX } from "react";

import { overlapS } from "@/features/trace/TraceTree";
import { laneSpanStyle } from "@/charts/palette";
import { useI18n } from "@/i18n";
import { fmtNum, fmtSpanDur } from "@/lib/format";
import type { SpanRow } from "@/lib/trace";
import { place, type Viewport } from "@/lib/viewport";

/** One attribute: name left, value right, both monospace so the column of
 * values stays scannable. The value is a plain text child — React escapes it
 * — since an attribute is data the traced system produced, not markup this
 * app can trust. */
function AttrRow({ name, value }: { name: string; value: unknown }) {
  const rendered =
    typeof value === "object" && value !== null ? JSON.stringify(value) : fmtNum(value);
  return (
    <div
      data-attr={name}
      className="flex items-baseline gap-3 border-b border-border/30 py-1 last:border-b-0"
    >
      <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{name}</span>
      <span className="min-w-0 flex-1 break-words text-right font-mono text-[11px]">
        {rendered}
      </span>
    </div>
  );
}

/** A span's kind, labelled the same way `KindLegend` labels it: the
 * translated name when the dictionary has one, the raw kind string
 * otherwise — so an unrecognised kind still reads as something rather than
 * as a blank cell. */
function kindLabel(t: (key: string) => string, kind: string): string {
  const key = `trace.kind.${kind}`;
  const translated = t(key);
  return translated === key ? kind : translated;
}

export function SpanDock({ row, view }: { row: SpanRow | null; view: Viewport }): JSX.Element {
  const { t } = useI18n();

  if (row === null) {
    return (
      <aside
        data-panel="span-dock"
        className="flex h-full flex-col items-center justify-center gap-1 p-4 text-center text-xs text-muted-foreground"
      >
        <p>{t("dock.empty")}</p>
      </aside>
    );
  }

  const name = row.name ?? t("common.unnamed");
  const durationS = Math.max(row.endS - row.startS, 0);
  const windowS = overlapS(view, row.startS, row.endS);
  const placed = place(view, row.startS, row.endS);
  const attrEntries = Object.entries(row.attrs);
  const swatch = laneSpanStyle(row.kind, row.isError, true);

  return (
    <aside
      data-panel="span-dock"
      className="flex h-full flex-col gap-3 overflow-y-auto p-3 text-xs"
    >
      <header className="flex flex-col gap-1">
        <div className="flex items-center gap-1.5">
          <span aria-hidden className="size-2.5 shrink-0 rounded-[3px]" style={swatch} />
          <span className="min-w-0 flex-1 truncate text-sm font-medium" title={name}>
            {name}
          </span>
        </div>
        <span className="font-mono text-[11px] text-muted-foreground">
          {kindLabel(t, row.kind)}
        </span>
      </header>

      {row.isError && (
        <p className="text-destructive">
          {t("trace.error")}: {row.error ?? row.status}
        </p>
      )}

      <div data-section="durations" className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground" title={t("trace.col_window")}>
            {t("trace.col_window")}
          </span>
          <span data-value="window" className="font-mono tabular-nums">
            {fmtSpanDur(windowS)}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground" title={t("trace.col_total")}>
            {t("trace.col_total")}
          </span>
          <span data-value="total" className="font-mono tabular-nums">
            {fmtSpanDur(durationS)}
          </span>
        </div>
        {/* The span's share of the CURRENT window, positioned through
            `place()` — never a fraction of the run total. Drawn only when
            `place()` says the span is visible: outside the window it has no
            share of it to show, not a small one. */}
        <div
          data-share-track="true"
          className="relative h-1.5 overflow-hidden rounded-full bg-muted"
        >
          {placed.visible && (
            <span
              data-share-mark="true"
              aria-hidden
              className="absolute inset-y-0 rounded-full"
              style={{ left: `${placed.leftPct}%`, width: `${placed.widthPct}%`, ...swatch }}
            />
          )}
        </div>
      </div>

      <div data-section="attrs" className="flex flex-col gap-1">
        <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
          {t("trace.attrs")}
        </span>
        {attrEntries.length === 0 ? (
          <p className="text-muted-foreground">{t("dock.no_attrs")}</p>
        ) : (
          <div className="flex flex-col">
            {attrEntries.map(([key, value]) => (
              <AttrRow key={key} name={key} value={value} />
            ))}
          </div>
        )}
      </div>
    </aside>
  );
}
