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
 *    re-made. A lane REPLACES the row of each child it packs — `flatten` emits
 *    an expanded node's children as rows whether or not it also packed them,
 *    and drawing both would put every stream on screen twice.
 *
 * 5. **A lane mark is a control, and drilling into one is how the spans
 *    underneath it are reached at all.** For one task this file drew lane
 *    marks as `aria-hidden` rectangles and skipped the whole packed subtree,
 *    which on the reference trace left 66 of 107 spans with no row, no
 *    button and no mark — the three throughput streams were on screen but
 *    every query inside them was not, in either expansion state. So: each
 *    mark is a real `<button>` whose accessible name is the span, clicking it
 *    docks the detail, and when the span has children the click also drills —
 *    its subtree returns to being ordinary rows underneath the lanes, and the
 *    mark says `aria-expanded` so the two readings agree. Only the rows of
 *    children a lane is currently STANDING IN FOR are dropped, which is what
 *    keeps "twice on screen" from coming back.
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

/** What turns a bar into a lane mark: which lane it sits in, whether the span
 * is currently drilled open (null when it has nothing to drill into), and what
 * a click does. */
interface LaneMark {
  index: number;
  expanded: boolean | null;
  /** Whether this span is the selected one. A lane mark has no row behind it
   * to tint, so without this its selection is conveyed by nothing at all —
   * not by colour, and not to a screen reader. */
  selected: boolean;
  onActivate: () => void;
}

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
 *
 * Every mark carries `data-surface="tree"` beside `data-mark`. `OverviewStrip`
 * marks its own with `data-surface="strip"`, and the distinction is not
 * cosmetic: the strip floors every mark at `MARK_MIN_PX` and nothing here
 * does, so `[data-mark]` alone mixes a floored population with an unfloored
 * one. The browser measurement is this branch's only acceptance evidence and
 * its selector must not be ambiguous — the first pass on this branch measured
 * 88 marks all exactly 2px and believed they were the tree's.
 *
 * AND IT IS SUPPRESSED THE MOMENT THE WINDOW CUTS THE BAR. `idleS / durationS`
 * is a fraction in the TOTAL domain; the box it would paint into is what the
 * window left of the bar. Multiplying the two is `pctOf(total)` — the exact
 * defect the redesign exists to remove — smuggled in as a hatch: a parent
 * [0,10] whose one child covers [0,2], seen through the window [0,2], drew a
 * full-width bar with 80% of it hatched over an interval whose real idle time
 * is zero. Doing it honestly needs the child intervals clipped to the window,
 * and `VisibleRow` does not carry them (for a collapsed node they are not even
 * on screen), so the answer is to draw nothing rather than to draw a number
 * from the wrong domain. The `窗口内` column still moves, which is where a
 * zoomed reader's information about this bar correctly lives.
 */
function Bar({ row, view, idleS, mark }: { row: SpanRow; view: Viewport; idleS: number; mark: LaneMark | null }) {
  const { t } = useI18n();
  const placed = place(view, row.startS, row.endS);
  if (!placed.visible) return null;

  const name = row.name ?? t("common.unnamed");
  const durationS = Math.max(row.endS - row.startS, 0);
  const clipped = placed.clippedStart || placed.clippedEnd;
  const idlePct =
    clipped || durationS <= 0 ? 0 : Math.min(100, Math.max(0, (idleS / durationS) * 100));

  const parts = [name, fmtSpanDur(durationS)];
  if (placed.clippedStart) parts.push(t("trace.clipped_start"));
  if (placed.clippedEnd) parts.push(t("trace.clipped_end"));
  const label = parts.join(" · ");

  const box = { left: `${placed.leftPct}%`, width: `${placed.widthPct}%` };
  const fill = laneSpanStyle(row.kind, row.isError, true);
  const caps = (
    <>
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
    </>
  );

  if (mark === null) {
    // A span row's own bar. The row already carries a name button and a
    // disclosure, so the bar is decoration and stays out of the accessibility
    // tree rather than announcing the row's name a second time.
    return (
      <span
        data-mark="true"
        data-surface="tree"
        data-kind={row.kind}
        data-clipped-start={placed.clippedStart ? "true" : undefined}
        data-clipped-end={placed.clippedEnd ? "true" : undefined}
        aria-hidden
        title={label}
        className="absolute top-1/2 h-2 -translate-y-1/2 rounded-[1px]"
        style={{ ...box, ...fill }}
      >
        {caps}
      </span>
    );
  }

  // A lane mark: the ONLY thing on screen standing for this span, so it is a
  // control. `inset-y-0` makes the button the full row height while the fill
  // stays the same 8px bar a row draws — a 2.5ms query is ~4px wide at the
  // throughput window, and an 8px-tall target that narrow is not a target.
  // The measured width stays on the element carrying `data-mark`, so the
  // browser gate measures the mark and not its padding.
  return (
    <button
      type="button"
      data-mark="true"
      data-surface="tree"
      data-kind={row.kind}
      data-mark-lane={mark.index}
      data-clipped-start={placed.clippedStart ? "true" : undefined}
      data-clipped-end={placed.clippedEnd ? "true" : undefined}
      onClick={mark.onActivate}
      aria-label={label}
      aria-expanded={mark.expanded ?? undefined}
      // Same reasoning as the row's name button: the mark is a selection
      // toggle, and `aria-selected` on something with no supporting role
      // announces nothing. Both attributes can be true at once and both are
      // worth saying — "drilled open" and "this is the one in the dock" are
      // different facts about the same mark.
      aria-pressed={mark.selected}
      title={label}
      className="absolute inset-y-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      style={box}
    >
      <span
        aria-hidden
        className={cn(
          "absolute inset-x-0 top-1/2 h-2 -translate-y-1/2 rounded-[1px]",
          // The visual half of the same fact. An outline rather than a fill
          // change, because the fill is the span's KIND and must keep meaning
          // only that.
          mark.selected && "ring-2 ring-foreground",
        )}
        style={fill}
      >
        {caps}
      </span>
    </button>
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
          // SELECTION, ANNOUNCED. It used to be carried by `bg-muted` on the
          // row and nothing else, i.e. by colour alone — and selection is now
          // the primary interaction on this page, because it is what fills
          // the dock.
          //
          // `aria-pressed` here rather than `aria-selected` on the row div:
          // `aria-selected` is only supported on `option`/`row`/`treeitem`
          // and friends, so on a role-less `<div>` it is ignored outright —
          // it would have LOOKED like a fix and announced nothing, which is
          // the exact defect class this review was about. Giving the tree
          // `role="tree"`/`role="treegrid"` to earn the attribute would
          // promise arrow-key navigation this component does not implement.
          // This button is genuinely a toggle (`onSelect` clears on a second
          // click), which is what `aria-pressed` means.
          aria-pressed={selected}
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
        {/* `idleS` unmodified. It used to be gated on `hasChildren` here,
            because `buildTree` returned a leaf's whole duration as its idle
            time and drawing that hatched every query bar end to end. That was
            the view papering over a wrong number: `rowmodel.ts` returns 0 for
            a leaf now, so every reader gets the right answer instead of this
            one compensating for it. */}
        <Bar row={row} view={view} idleS={idleS} mark={null} />
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
 * Every mark is a button (decision 5), so the lane is a list of controls and
 * a screen reader can walk it. The `sr-only` summary stays, because what it
 * says — how many spans are packed in here — is the one thing the marks
 * cannot say individually.
 */
function LaneRowView({
  lane,
  index,
  depth,
  view,
  states,
  selectedId,
  onToggle,
  onSelect,
}: {
  lane: SpanRow[];
  index: number;
  depth: number;
  view: Viewport;
  /** Every row `flatten` emitted, by id — a packed child's row is suppressed
   * but its `VisibleRow` is still what says whether it is drilled open. */
  states: Map<string, VisibleRow>;
  selectedId: string | null;
  onToggle: (id: string) => void;
  onSelect: (id: string) => void;
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
        {lane.map((row) => {
          const state = states.get(row.id);
          return (
            <Bar
              key={row.id}
              row={row}
              view={view}
              idleS={0}
              mark={{
                index,
                selected: selectedId === row.id,
                // `hasChildren` and not `childCount > 0` for one reason: a
                // mark says `aria-expanded` only when the click will actually
                // drill, so the attribute never promises a disclosure that
                // does nothing.
                expanded: state !== undefined && state.hasChildren ? state.expanded : null,
                // Select AND drill, because the mark is the span's only
                // affordance: there is no row here to carry a chevron beside
                // a name. Selecting is unconditional (the dock is the point
                // of clicking a leaf); the toggle is what makes the subtree
                // reachable, and clicking again puts it back in the lane.
                onActivate: () => {
                  onSelect(row.id);
                  if (state !== undefined && state.hasChildren) onToggle(row.id);
                },
              }}
            />
          );
        })}
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

  // Every row `flatten` emitted, by id. A packed child's own row is
  // suppressed below, but its `VisibleRow` is still what says whether it has
  // children and whether the reader drilled into it, which is what the lane
  // mark has to announce.
  const states = new Map<string, VisibleRow>();
  for (const vrow of rows) states.set(vrow.row.id, vrow);

  const out: ReactNode[] = [];
  // Whether the row that most recently occupied each depth draws lanes.
  // `flatten` is depth-first, so the last row seen at `depth - 1` IS this
  // row's parent, and one array indexed by depth is the whole bookkeeping.
  const laneParent: boolean[] = [];
  for (const vrow of rows) {
    const packed = vrow.depth > 0 && laneParent[vrow.depth - 1] === true;
    laneParent[vrow.depth] = vrow.lanes !== null;
    // A lane already draws this child as a mark, so its row would be the same
    // span twice — UNLESS the reader drilled into it, in which case the mark
    // is the overview and these rows are what was inside. A collapsed child
    // has no descendants in `rows` at all (`flatten` does not walk a closed
    // node), so dropping its own row is the whole of the suppression; what
    // this replaced dropped the entire following subtree, which is how 66 of
    // the reference trace's 107 spans came to have nothing on screen.
    if (packed && !vrow.expanded) continue;

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
          key={`${vrow.row.id}#lane${lane}`}
          lane={vrow.lanes[lane]}
          index={lane}
          depth={vrow.depth}
          view={view}
          states={states}
          selectedId={selectedId}
          onToggle={onToggle}
          onSelect={onSelect}
        />,
      );
    }
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
