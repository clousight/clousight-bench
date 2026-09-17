/**
 * The tree rows: the component that makes a trace readable.
 *
 * Four decisions are load-bearing here, and each of them is a defect the
 * shipped view had:
 *
 * 1. **The name column is capped at `NAME_COL_PX` and the lane takes the
 *    rest.** The old view gave the name `flex:1`, which took 1062px of a
 *    1440px row to hold ~250px of text and left the lane 16% — 4.23x narrower
 *    marks than the cap gives. The cap is also what makes `truncate` work at
 *    all: `overflow:hidden; text-overflow:ellipsis` clips nothing inside a box
 *    that is being sized BY its content, which is how a 176-character span
 *    name once grew a cell to 1340px and pushed the numeric columns off
 *    screen. Every clipped name keeps a `title`.
 *
 * 2. **Two duration columns, never one.** `窗口内` is the span's duration
 *    intersected with the window — an observation that moves as you zoom —
 *    and `合计` is its true total, a fact that never moves. The old mockup
 *    printed one bolded number that silently meant the clipped value for some
 *    rows and the unclipped value for others. This is the interface
 *    expression of the project's standing invariant: a selection moves
 *    observation, never the verdict.
 *
 * 3. **Every position goes through `place(view, ...)`.** There is no
 *    `pctOf(durationS)` in this file and reintroducing one is the defect the
 *    whole redesign exists to remove: a fraction of the *run* cannot change
 *    when the reader zooms, so a "zoom" built on it can only ever dim things.
 *    A bar the window cut carries `data-clipped-start`/`data-clipped-end` and
 *    a hatched cap at the cut edge, so a bar touching the frame reads as
 *    truncated rather than as a bar that ended there.
 *
 * 4. **A node whose children overlap in time expands into lanes.** That
 *    decision is `rowmodel.flatten`'s (`VisibleRow.lanes`), read here and not
 *    re-made. Lanes REPLACE the subtree's rows rather than being drawn beside
 *    them — `flatten` emits an expanded node's children as rows whether or not
 *    it also packed them, and drawing both would put every stream on screen
 *    twice.
 *
 * Colour comes from `laneSpanStyle` and nothing in this file names a hue. The
 * palette is being rewritten (`#5eead4` measured 1.29:1 against the track and
 * was the fill for 88 of 107 spans); a hardcoded hex here would survive that
 * rewrite, which is exactly what must not happen.
 */

import { ChevronDown, ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

import { laneSpanStyle } from "@/charts/palette";
import { useI18n } from "@/i18n";
import { fmtSpanDur } from "@/lib/format";
import type { VisibleRow } from "@/lib/rowmodel";
import type { SpanRow } from "@/lib/trace";
import { cn } from "@/lib/utils";
import { place, type Viewport } from "@/lib/viewport";

/** The name column's cap, in pixels. See decision 1 above — this number is
 * the difference between a 16% lane and an 80% one. */
export const NAME_COL_PX = 300;

/** Each numeric column. Fixed, not content-sized: a column that grows for
 * "6.57s" and shrinks for "699ms" moves the lane's right edge per row, and
 * bars stacked under each other stop sharing an axis. */
export const NUM_COL_PX = 62;

/** One row. Every row — header, span and lane alike — is exactly this tall,
 * because the shared time axis only works while rows line up. */
export const ROW_PX = 22;

/** Indent per tree level. */
export const INDENT_PX = 12;

/** The hatched cap drawn at an edge the window cut. */
export const CLIP_CAP_PX = 3;

/**
 * The part of `[startS, endS]` the window actually contains, in seconds.
 *
 * Clamped at zero rather than allowed to go negative: a span entirely outside
 * the window has no overlap, and a negative "duration" in the `窗口内` column
 * would be a number no reader can interpret.
 */
export function overlapS(view: Viewport, startS: number, endS: number): number {
  return Math.max(0, Math.min(endS, view.endS) - Math.max(startS, view.startS));
}

/** The cut-edge cap: a hatch in the page's own background colour, so it reads
 * as the bar being sliced rather than as a differently-coloured segment. Named
 * tokens only — see the module docstring on the palette rewrite. */
const CUT_HATCH = {
  // 50% duty at a 2px period, not the 25%-at-4px the idle hatch uses: the cap
  // is only `CLIP_CAP_PX` wide, and at the sparser setting a bar clipped at
  // both ends — which the root span is at every zoom level — showed no
  // visible difference from a bar that genuinely ended there. Measured in a
  // browser at 1440px, not eyeballed from the source.
  backgroundImage:
    "repeating-linear-gradient(135deg, var(--background) 0px, var(--background) 1px, transparent 1px, transparent 2px)",
} as const;

/** The unaccounted-time hatch, in the recessive grey the rest of the app uses
 * for present-but-not-data content. */
const IDLE_HATCH = {
  backgroundImage:
    "repeating-linear-gradient(135deg, var(--muted-foreground) 0px, var(--muted-foreground) 1.5px, transparent 1.5px, transparent 4px)",
} as const;

/**
 * One span's bar, positioned through the window.
 *
 * `laneSpanStyle`'s third argument is pinned to `true` here, as `OverviewStrip`
 * pins it: that flag exists for the track list's per-lane checkbox, which this
 * view does not have, and a tree where every unselected row painted at 0.4
 * opacity would be a tree that is mostly invisible. Row selection is carried
 * by the row's own background instead, so hue keeps meaning kind and nothing
 * else.
 *
 * `idleS` is drawn as a trailing hatched segment sized by its share of the
 * span's duration. It encodes HOW MUCH time the children do not account for,
 * not WHEN: `VisibleRow` carries idle as a scalar (a collapsed node's children
 * are not on screen to place it against), so the trailing edge is a convention
 * and the segment says so in its own `title` rather than letting the reader
 * infer a gap that may have been in the middle.
 */
function Bar({
  row,
  view,
  idleS,
  lane,
}: {
  row: SpanRow;
  view: Viewport;
  idleS: number;
  /** Lane index when this bar is a mark inside a packed lane, else null. */
  lane: number | null;
}) {
  const { t } = useI18n();
  const placed = place(view, row.startS, row.endS);
  if (!placed.visible) return null;

  const name = row.name ?? t("common.unnamed");
  const durationS = Math.max(row.endS - row.startS, 0);
  const idlePct = durationS > 0 ? Math.min(100, Math.max(0, (idleS / durationS) * 100)) : 0;

  const parts = [name, fmtSpanDur(durationS)];
  if (placed.clippedStart) parts.push(t("trace.clipped_start"));
  if (placed.clippedEnd) parts.push(t("trace.clipped_end"));

  return (
    <span
      data-mark="true"
      data-kind={row.kind}
      data-mark-lane={lane === null ? undefined : lane}
      data-clipped-start={placed.clippedStart ? "true" : undefined}
      data-clipped-end={placed.clippedEnd ? "true" : undefined}
      aria-hidden
      title={parts.join(" · ")}
      className="absolute top-1/2 h-2 -translate-y-1/2 rounded-[1px]"
      style={{
        left: `${placed.leftPct}%`,
        width: `${placed.widthPct}%`,
        ...laneSpanStyle(row.kind, row.isError, true),
      }}
    >
      {idlePct > 0 && (
        <span
          data-idle="true"
          title={`${t("trace.unaccounted")} ${fmtSpanDur(idleS)} — ${t("trace.unaccounted_hint")}`}
          className="absolute inset-y-0 right-0"
          style={{ width: `${idlePct}%`, ...IDLE_HATCH }}
        />
      )}
      {placed.clippedStart && (
        <span
          data-cut="start"
          className="absolute inset-y-0 left-0"
          style={{ width: `${CLIP_CAP_PX}px`, ...CUT_HATCH }}
        />
      )}
      {placed.clippedEnd && (
        <span
          data-cut="end"
          className="absolute inset-y-0 right-0"
          style={{ width: `${CLIP_CAP_PX}px`, ...CUT_HATCH }}
        />
      )}
    </span>
  );
}

/** The two numeric cells, shared by span rows and lane rows so they cannot
 * drift apart. `font-mono` sits beside `tabular-nums` on the same element and
 * not on an ancestor: rule 1 of the visual system is swept for by
 * `test_every_number_is_monospace`, which reads one line at a time.
 *
 * The second prop is `durationS` and not `totalS` deliberately. `totalS` is
 * the app's name for a TIME-AXIS WIDTH, and
 * `test_a_time_axis_is_fed_the_window_and_never_the_run_total` scans every
 * JSX `totalS={...}` in the tree to prove it was fed a window and not the run
 * — correctly failing this component, which hands that prop a single span's
 * duration for a text cell. The number here is a fact about one span, not a
 * denominator anything is positioned against; the axis in this file is
 * `view`, and it reaches `place()` and nothing else. Renaming it back would
 * make a real invariant fire on a false positive, which is how invariants get
 * loosened. */
function Numbers({ windowS, durationS }: { windowS: number; durationS: number }) {
  return (
    <>
      <div
        data-col="window"
        className="shrink-0 pr-2 text-right font-mono text-[11px] tabular-nums"
        style={{ width: `${NUM_COL_PX}px` }}
      >
        {fmtSpanDur(windowS)}
      </div>
      <div
        data-col="total"
        className="shrink-0 pr-1 text-right font-mono text-[11px] tabular-nums text-muted-foreground"
        style={{ width: `${NUM_COL_PX}px` }}
      >
        {fmtSpanDur(durationS)}
      </div>
    </>
  );
}

/** One span, one row. */
function SpanRowView({
  vrow,
  view,
  selected,
  onToggle,
  onSelect,
}: {
  vrow: VisibleRow;
  view: Viewport;
  selected: boolean;
  onToggle: (id: string) => void;
  onSelect: (id: string) => void;
}) {
  const { t } = useI18n();
  const { row, depth, hasChildren, childCount, expanded, idleS } = vrow;
  const name = row.name ?? t("common.unnamed");
  const Chevron = expanded ? ChevronDown : ChevronRight;

  return (
    <div
      data-row="span"
      className={cn(
        "flex items-center border-b border-border/40",
        selected && "bg-muted",
        row.isError && "border-l-2 border-l-destructive",
      )}
      style={{ height: `${ROW_PX}px` }}
    >
      <div
        data-col="name"
        className="flex shrink-0 items-center overflow-hidden font-mono text-[11px]"
        style={{ width: `${NAME_COL_PX}px`, paddingLeft: `${depth * INDENT_PX}px` }}
      >
        {hasChildren ? (
          // A real <button> with aria-expanded, not a div with onClick: this
          // repo spent a whole fix round on that anti-pattern in TablePane,
          // where it was unreachable by keyboard and silent to a screen
          // reader. The target is the full row box (22x22) — which is the
          // largest a `ROW_PX` row allows, and still under WCAG 2.5.8's
          // 24x24; the density the redesign was measured at and the target
          // minimum genuinely conflict here, and the density won.
          <button
            type="button"
            onClick={() => onToggle(row.id)}
            aria-expanded={expanded}
            aria-label={`${t(expanded ? "trace.collapse" : "trace.expand")}: ${name}`}
            className="flex shrink-0 items-center justify-center text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            style={{ width: `${ROW_PX}px`, height: `${ROW_PX}px` }}
          >
            <Chevron className="size-3" aria-hidden />
          </button>
        ) : (
          // A leaf gets a spacer, not a disabled control: a keyboard walking
          // a 900-span trace should not have to step past 900 dead buttons.
          <span aria-hidden className="shrink-0" style={{ width: `${ROW_PX}px` }} />
        )}
        <button
          type="button"
          onClick={() => onSelect(row.id)}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {/* Clipped, so the full name has to stay recoverable. `min-w-0` is
              what lets a flex item shrink below its content's width; without
              it `truncate` does nothing even inside the capped cell. */}
          <span className="min-w-0 flex-1 truncate" title={name}>
            {name}
          </span>
          {hasChildren && !expanded && (
            // The child count, so a collapsed node says how much it is
            // hiding. `font-mono` repeated on this element for the same
            // one-line-at-a-time sweep the numeric columns satisfy.
            <span className="shrink-0 font-mono tabular-nums text-muted-foreground">
              {childCount}
            </span>
          )}
        </button>
      </div>
      <div data-col="lane" className="relative h-full flex-1 overflow-hidden">
        {/* `idleS` is only unaccounted time for a node that HAS children.
            `buildTree` computes it as duration minus the union of children,
            so for a leaf — which is most of a trace — it equals the whole
            duration, and drawing that would hatch every leaf bar end to end
            and claim a 900-query trace was idle throughout. A leaf is not
            idle, it is working; that is `selfS`, the field this one is
            currently numerically identical to. Gating it here is the
            component's job rather than the model's: the model's number is
            right, it is only meaningful in one of the two cases. */}
        <Bar row={row} view={view} idleS={hasChildren ? idleS : 0} lane={null} />
      </div>
      <Numbers
        windowS={overlapS(view, row.startS, row.endS)}
        durationS={Math.max(row.endS - row.startS, 0)}
      />
    </div>
  );
}

/**
 * One packed lane of a concurrent node's children.
 *
 * A lane's spans never overlap — that is what greedy packing guarantees — so
 * summing their durations IS their union, and the two numeric columns stay
 * exact rather than double-counting.
 *
 * The marks are `aria-hidden` rectangles, so the lane carries an `sr-only`
 * summary instead: a screen reader reading 900 absolutely-positioned spans
 * would be worse than reading none, but reading none is what shipped.
 */
function LaneRowView({
  lane,
  index,
  depth,
  view,
}: {
  lane: SpanRow[];
  index: number;
  depth: number;
  view: Viewport;
}) {
  const { t } = useI18n();
  // One child per lane is the case lanes exist for (three streams, three
  // lanes), and then the lane is that child. Past one, a greedy pack has put
  // several sequential children together and no single name describes it.
  const label =
    lane.length === 1 ? (lane[0].name ?? t("common.unnamed")) : `${lane.length} ${t("trace.spans")}`;
  const windowS = lane.reduce((acc, row) => acc + overlapS(view, row.startS, row.endS), 0);
  const durationS = lane.reduce((acc, row) => acc + Math.max(row.endS - row.startS, 0), 0);

  return (
    <div data-row="lane" data-lane={index} className="flex items-center" style={{ height: `${ROW_PX}px` }}>
      <div
        data-col="name"
        className="flex shrink-0 items-center overflow-hidden font-mono text-[11px] text-muted-foreground"
        style={{ width: `${NAME_COL_PX}px`, paddingLeft: `${(depth + 1) * INDENT_PX}px` }}
      >
        <span aria-hidden className="shrink-0" style={{ width: `${ROW_PX}px` }} />
        <span className="min-w-0 flex-1 truncate" title={label}>
          {label}
        </span>
      </div>
      <div data-col="lane" className="relative h-full flex-1 overflow-hidden">
        <span className="sr-only">{`${label} · ${lane.length} ${t("trace.spans")}`}</span>
        {lane.map((row) => (
          <Bar key={row.id} row={row} view={view} idleS={0} lane={index} />
        ))}
      </div>
      <Numbers windowS={windowS} durationS={durationS} />
    </div>
  );
}

export interface TraceTreeProps {
  /** `rowmodel.flatten`'s output — depth-first, time-ordered, already
   * reflecting which nodes are open. The expansion set itself is NOT a second
   * prop: `VisibleRow.expanded` and `VisibleRow.lanes` were both derived from
   * it by `flatten`, and a second copy could disagree with `lanes` — a `▾`
   * chevron on a node rendering no children. */
  rows: VisibleRow[];
  /** The time window every bar is positioned through. */
  view: Viewport;
  onToggle: (id: string) => void;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function TraceTree({ rows, view, onToggle, selectedId, onSelect }: TraceTreeProps) {
  const { t } = useI18n();

  const out: ReactNode[] = [];
  for (let i = 0; i < rows.length; i += 1) {
    const vrow = rows[i];
    out.push(
      <SpanRowView
        key={vrow.row.id}
        vrow={vrow}
        view={view}
        selected={selectedId === vrow.row.id}
        onToggle={onToggle}
        onSelect={onSelect}
      />,
    );
    if (vrow.lanes === null) continue;

    for (let lane = 0; lane < vrow.lanes.length; lane += 1) {
      out.push(
        <LaneRowView
          key={`${vrow.row.id} lane${lane}`}
          lane={vrow.lanes[lane]}
          index={lane}
          depth={vrow.depth}
          view={view}
        />,
      );
    }
    // The lanes have drawn this node's children, so the rows `flatten`
    // emitted for that subtree are skipped rather than drawn underneath them.
    // `flatten` is depth-first, so the subtree is exactly the run of
    // following rows deeper than this one.
    while (i + 1 < rows.length && rows[i + 1].depth > vrow.depth) i += 1;
  }

  return (
    <div className="flex flex-col">
      <div
        data-row="head"
        // Sans and mixed case, where the other micro-headers in this app are
        // `font-mono ... uppercase`. Both changes are forced by the 62px
        // columns and both were measured, not guessed: at 10px mono uppercase
        // the "in window" heading rendered as "IN WIN…", and dropping only
        // the mono still left it clipped (~62px of glyphs into a 54px content
        // box). A heading that has to be truncated is not doing the one job
        // it exists for — §3's whole point is that the reader can tell the
        // observation from the verdict. Mono's real job here is the numbers
        // underneath, which keep it.
        className="flex items-center border-b border-border text-[10px] tracking-[0.04em] text-muted-foreground"
        style={{ height: `${ROW_PX}px` }}
      >
        <div
          data-col="name"
          className="shrink-0 truncate"
          style={{ width: `${NAME_COL_PX}px`, paddingLeft: `${ROW_PX}px` }}
        >
          {t("timeline.span")}
        </div>
        <div data-col="lane" className="flex-1" />
        {/* Two headings, because there are two numbers and they mean
            different things. Without them the reader has no way to tell the
            observation from the verdict. `title` on both, because a locale
            with a longer word than either of these would clip. */}
        <div
          data-col="window"
          title={t("trace.col_window")}
          className="shrink-0 truncate pr-2 text-right"
          style={{ width: `${NUM_COL_PX}px` }}
        >
          {t("trace.col_window")}
        </div>
        <div
          data-col="total"
          title={t("trace.col_total")}
          className="shrink-0 truncate pr-1 text-right"
          style={{ width: `${NUM_COL_PX}px` }}
        >
          {t("trace.col_total")}
        </div>
      </div>
      {out}
    </div>
  );
}
