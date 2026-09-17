/**
 * #/record/:id/trace — the run as a tree you can zoom.
 *
 * This is where the redesign's pieces become a page. Top to bottom: the
 * chrome that states the conclusion, the overview strip that draws the whole
 * run and takes the drag, the tree, and — floating over the tree's right edge
 * while a row is selected — the docked detail of that row. Below that, the
 * aggregate and table panes, unchanged.
 *
 * **What this replaced, and why it is a replacement rather than a deletion.**
 * The `步骤` tab was the tree plus a detail panel, and `时序` was the tree
 * fully expanded; the tree subsumes both, so their tabs are gone from the page
 * while every module they used stays in the repo. The lane list went further
 * and was DELETED, because the tree is a strict replacement for it: same
 * spans, same viewport, a name column that leaves the lane four times the
 * width, and — the part the lane list could never do — the spans inside a
 * packed lane are reachable. It took `pctOf(totalS)`, the defect this
 * redesign exists to remove, out of the repo with it. The grouping work that
 * will replace `聚合`/`表格` has not landed, so those two tabs stay exactly as
 * they were.
 *
 * **`TraceBody` owns every piece of state the view has**: the window, the
 * expansion set, the selection and the query. Not one of them belongs to a
 * child — the strip's drag, the tree's chevrons and the chrome's filter box
 * all move the same picture, and a second copy anywhere is a copy that can
 * disagree. Everything handed down is memoised or a `useCallback`, because a
 * strip drag re-renders this component on every pointermove and a freshly
 * allocated array per render puts that work into every child's memo. The
 * DERIVATION from that state to the rows on screen is not here: it is
 * `lib/tracerows.ts`, because it is arithmetic and arithmetic belongs where a
 * test can call it.
 *
 * The fetch is split off into `TraceView` above `TraceBody`, so that the
 * assembled page can be rendered from data in a test at all.
 *
 * Two feeds can land here and they carry very different detail, so the page
 * says which one it got. A suite that writes its own trajectory artifact gives
 * per-query spans; every other run falls back to the run trace, which is the
 * eleven lifecycle stages and nothing finer.
 */

import { ArrowLeft } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";

import { useJSON, type TrajectoryData } from "@/api";
import { KindLegend } from "@/charts/Waterfall";
import { EmptyView, ErrorView, LoadingView } from "@/components/StateViews";
import { Section, SectionBody } from "@/components/ui/section";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AggregatedPane } from "@/features/timeline/AggregatedPane";
import { OverviewStrip } from "@/features/timeline/OverviewStrip";
import { TablePane } from "@/features/timeline/TablePane";
import { SpanDock } from "@/features/trace/SpanDock";
import { TraceChrome } from "@/features/trace/TraceChrome";
import { TraceTree } from "@/features/trace/TraceTree";
import { useI18n } from "@/i18n";
import { fmtDur } from "@/lib/format";
import { buildTree } from "@/lib/rowmodel";
import { fullSelection, sameRows, selectSpans } from "@/lib/selection";
import { buildRows, totalSeconds } from "@/lib/trace";
import { traceRows } from "@/lib/tracerows";
import { assignTracks } from "@/lib/tracks";
import { fullViewport, type Viewport } from "@/lib/viewport";
import { recordHref } from "@/router";

/**
 * How wide the docked detail is when a row is selected.
 *
 * It costs the tree NOTHING, because the dock is drawn OVER the tree's right
 * edge rather than inset beside it — see the mount site below for why that is
 * a correctness property and not a style choice.
 */
const DOCK_PX = 264;

/**
 * The route: fetch, and nothing else.
 *
 * Split from the body below on purpose. `useJSON` fetches in an effect, and
 * `renderToStaticMarkup` never runs effects — so as one component this whole
 * view resolved to `LoadingView` in every test that could reach it, and the
 * assembly was covered by a source-level prop scan and one browser pass. With
 * the fetch in its own shell, `TraceBody` renders from data a test can hand it.
 */
export function TraceView({ runId }: { runId: string }) {
  const path = useMemo(() => `api/record/${encodeURIComponent(runId)}/trajectory`, [runId]);
  const { data, error } = useJSON<TrajectoryData>(path);

  if (error !== null) return <ErrorView message={error} />;
  if (data === null) return <LoadingView />;
  return <TraceBody runId={runId} data={data} />;
}

export function TraceBody({ runId, data }: { runId: string; data: TrajectoryData }) {
  const { t } = useI18n();

  const [tab, setTab] = useState("aggregated");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  // Null until the reader touches a chevron, which is what lets the arrival
  // state stay *derived*: the slowest path is a fact about the trace, so it
  // must follow the trace changing, and a set copied into state at mount
  // would keep pointing at the previous run's ids.
  const [opened, setOpened] = useState<ReadonlySet<string> | null>(null);

  const rows = useMemo(() => buildRows(data), [data]);
  const tracks = useMemo(() => assignTracks(rows), [rows]);
  const t0 = data.t0;

  // The window. Zooming replaces the time domain every row renders through —
  // that is what makes the strip's drag a zoom rather than a filter — and
  // `bounds` stays the whole run, because every gesture clamps into it and the
  // strip draws it at every zoom level.
  const bounds = useMemo(() => fullViewport(rows, t0), [rows, t0]);
  const [zoom, setZoom] = useState<Viewport | null>(null);
  const view = zoom ?? bounds;

  const tree = useMemo(() => buildTree(rows), [rows]);
  // Arrival, the filter, the forced-open ancestors and the lane filter, in
  // one pure call — see `lib/tracerows.ts`. Deliberately NOT split into four
  // memos here: every one of them depends on the same four inputs, none of
  // which a strip drag touches, and as component-local code it was the one
  // piece of this view nothing could test.
  const {
    visible: visibleRows,
    expanded,
    matches,
  } = useMemo(() => traceRows({ tree, rows, opened, query }), [tree, rows, opened, query]);

  const onToggle = useCallback(
    (id: string) => {
      const next = new Set<string>(expanded);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      setOpened(next);
    },
    [expanded],
  );
  const onSelect = useCallback(
    (id: string) => setSelectedId((current) => (current === id ? null : id)),
    [],
  );
  // The dock's own way out. Named rather than an inline arrow for two
  // reasons: it keeps `SpanDock`'s props referentially stable across a drag,
  // and an inline `() => …` inside a JSX tag ends that tag early for the
  // regex in `test_a_time_axis_is_fed_the_window_and_never_the_run_total`,
  // which would stop seeing the `view={view}` it is here to check.
  const onClearSelection = useCallback(() => setSelectedId(null), []);

  // Memoised, not computed inline: `fullSelection` builds a fresh object and a
  // fresh Set every call, so while nothing is unchecked an inline call would
  // hand every consumer a new identity on every render.
  const allTrackIds = useMemo(() => fullSelection(rows, tracks).trackIds, [rows, tracks]);
  // The window, restated as the `Selection` the two panes still take. This
  // memo alone does NOT stabilise what it produces: a drag genuinely moves the
  // window, so this object genuinely changes on every pointermove, and
  // `selectSpans` allocates a fresh array each time. That is handled below.
  const effective = useMemo(
    () => ({ startS: view.startS, endS: view.endS, trackIds: allTrackIds }),
    [view, allTrackIds],
  );
  const filtered = useMemo(() => selectSpans(rows, tracks, effective), [rows, tracks, effective]);
  // Collapse an identical result back onto the previous array. The window
  // moves continuously during a drag; the rows inside it change a handful of
  // times, when a span crosses an edge. Without this, dragging the strip
  // re-ran both panes' aggregation on every pointermove. A ref rather than a
  // memo because the answer must survive for as long as the component does,
  // and it is safe to write during render because the value is derived from
  // this render's own inputs.
  const stableVisible = useRef(filtered);
  if (!sameRows(stableVisible.current, filtered)) stableVisible.current = filtered;
  const visible = stableVisible.current;
  // Derived from `visible`, not from `rows`: the legend sits above a strip and
  // a tree that are both window-aware, so naming a kind the window contains
  // none of makes it the one element in that group describing a different
  // trace than the rest.
  const kinds = useMemo(() => {
    const seen: string[] = [];
    for (const row of visible) if (row.kind !== "" && !seen.includes(row.kind)) seen.push(row.kind);
    return seen;
  }, [visible]);

  const total = totalSeconds(rows, t0);
  const selectedRow = rows.find((row) => row.id === selectedId) ?? null;

  return (
    <div className="flex flex-col gap-4">
      <nav className="flex items-center justify-between">
        <a
          href={recordHref(runId)}
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4" /> {t('common.back')}
        </a>
        <a
          href={recordHref(runId)}
          className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          {t('detail.title')} →
        </a>
      </nav>

      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-lg font-semibold tracking-tight">{t('trace.title')}</h2>
        <span className="font-mono text-sm text-muted-foreground">{runId}</span>
        <span className="text-xs text-muted-foreground">
          {t('trace.total')} {fmtDur(total)}
          <span className="mx-1.5">·</span>
          {rows.length} {t('trace.spans')}
        </span>
        {data.source !== undefined && (
          <span
            title={t(`trace.source.${data.source}_blurb`)}
            className="cursor-help rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
          >
            {t(`trace.source.${data.source}`)}
          </span>
        )}
      </div>

      {rows.length === 0 ? (
        <Section>
          <SectionBody className="pt-4">
            <EmptyView />
          </SectionBody>
        </Section>
      ) : (
        <>
          <Section>
            <SectionBody className="pt-3">
              <TraceChrome
                tree={tree}
                view={view}
                bounds={bounds}
                query={query}
                onQuery={setQuery}
                onView={setZoom}
                matches={matches}
              />
              {kinds.length > 1 && <KindLegend kinds={kinds} />}
              <OverviewStrip rows={rows} bounds={bounds} view={view} onView={setZoom} />
              {/* THE DOCK IS AN OVERLAY, AND THAT IS AN ENCODING DECISION.
                  Inset beside the tree it took real width: the content column
                  is 1104px at 1440px wide and the tree spends a fixed 424px
                  of every row on the name cap and the two duration columns,
                  so a 264px dock plus its gap cut the lane from 680px to
                  404px — 61.6% of the row down to 36.6% — and scaled every
                  bar by 0.594 with it. The narrowest query at the throughput
                  window went from 2.56px to ~1.52px, i.e. the acceptance
                  gate's "0 marks under 2px" held only while the dock was
                  shut, and the branch's own drill gesture opens it.

                  A per-mark `min-width` would have "fixed" that by drawing a
                  1.5ms span at 2ms, which is the encoding lie this whole
                  redesign exists to remove. Narrowing the name column while
                  the dock is open cannot reach the old widths either — it
                  would need the fixed columns down to 148px — and lands at
                  ~1.97px, under the line by arithmetic rather than by
                  principle.

                  Overlaying costs the lane nothing: the tree's row box does
                  not know the dock exists, so every bar is the same width in
                  both states and the gate numbers are one set of numbers
                  rather than two. What it costs instead is occlusion — the
                  right 264px of the lane is covered while the dock is open —
                  which is visible, recoverable in one click, and never a
                  wrong number. */}
              <div className="relative mt-3">
                <TraceTree
                  rows={visibleRows}
                  view={view}
                  onToggle={onToggle}
                  selectedId={selectedId}
                  onSelect={onSelect}
                />
                {selectedRow !== null && (
                  <div
                    data-dock="overlay"
                    // `top-0 max-h-full`, not `inset-y-0`: the panel occludes
                    // whatever it covers, so it takes its content's height and
                    // no more, and only grows a scrollbar once a span has
                    // enough attributes to need one. Stretched to the tree's
                    // full height it was hiding the right edge of every row
                    // below its last line for no reason.
                    className="absolute right-0 top-0 max-h-full overflow-y-auto border-l border-border bg-background shadow-lg"
                    style={{ width: `${DOCK_PX}px` }}
                  >
                    <SpanDock row={selectedRow} view={view} onClose={onClearSelection} />
                  </div>
                )}
              </div>
            </SectionBody>
          </Section>

          {/* The two panes the grouping slice will replace. Until it lands
              they stay mounted and unchanged — a replacement has to exist
              before the thing it replaces is removed. */}
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabsTrigger value="aggregated">{t('timeline.aggregated')}</TabsTrigger>
              <TabsTrigger value="table">{t('timeline.table')}</TabsTrigger>
            </TabsList>

            <TabsContent value="aggregated">
              <Section>
                <SectionBody className="pt-4">
                  {/* The window travels with the rows because `aggregate`
                      clips to it: `visible` is an overlap filter, so a span
                      straddling an edge arrives whole and would be counted
                      whole. Passed as the memoised `effective` object rather
                      than as a fresh literal — each pane memoises on prop
                      identity, and a literal built in this JSX would mean
                      re-aggregating on every pointermove of a drag.

                      `allRows` is the whole trace and is not a duplicate of
                      `visible`: self time is a property of the span, so the
                      children subtracted from a parent must not disappear
                      when the window excludes them. */}
                  <AggregatedPane rows={visible} allRows={rows} window={effective} />
                </SectionBody>
              </Section>
            </TabsContent>

            <TabsContent value="table">
              <Section>
                <SectionBody className="pt-4">
                  {/* `allRows` for the same reason as the pane above: the
                      self column must not change meaning with the window. */}
                  <TablePane rows={visible} allRows={rows} window={effective} onSelect={onSelect} />
                </SectionBody>
              </Section>
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  );
}
