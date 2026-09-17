/**
 * #/record/:id/trace — the run as a tree you can zoom.
 *
 * This is where the redesign's pieces become a page. Top to bottom: the
 * chrome that states the conclusion, the overview strip that draws the whole
 * run and takes the drag, the tree, and — to the right of it — the docked
 * detail of whatever row is selected. Below that, the aggregate and table
 * panes, unchanged.
 *
 * **What this replaced, and why it is a replacement rather than a deletion.**
 * The `步骤` tab was the tree plus a detail panel, and `时序` was the tree
 * fully expanded; the tree subsumes both, so their tabs are gone from the page
 * while every module they used stays in the repo. `TrackList` is likewise no
 * longer mounted — the tree positions the same spans through the same
 * viewport, with a name column that leaves the lane 4x the width — and the
 * grouping work that will replace `聚合`/`表格` has not landed, so those two
 * tabs stay exactly as they were.
 *
 * **This component owns every piece of state the view has**: the window, the
 * expansion set, the selection and the query. Not one of them belongs to a
 * child — the strip's drag, the tree's chevrons and the chrome's filter box
 * all move the same picture, and a second copy anywhere is a copy that can
 * disagree. Everything handed down is memoised or a `useCallback`, because a
 * strip drag re-renders this component on every pointermove and a freshly
 * allocated array per render puts that work into every child's memo.
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
import { buildTree, flatten, slowestPath, type VisibleRow } from "@/lib/rowmodel";
import { fullSelection, sameRows, selectSpans } from "@/lib/selection";
import { buildRows, totalSeconds } from "@/lib/trace";
import { assignTracks } from "@/lib/tracks";
import { fullViewport, type Viewport } from "@/lib/viewport";
import { recordHref } from "@/router";

/** How wide the docked detail is when a row is selected. */
const DOCK_PX = 264;

export function TraceView({ runId }: { runId: string }) {
  const { t } = useI18n();
  const path = useMemo(() => `api/record/${encodeURIComponent(runId)}/trajectory`, [runId]);
  const { data, error } = useJSON<TrajectoryData>(path);

  const [tab, setTab] = useState("aggregated");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  // Null until the reader touches a chevron, which is what lets the arrival
  // state stay *derived*: the slowest path is a fact about the trace, so it
  // must follow the trace changing, and a set copied into state at mount
  // would keep pointing at the previous run's ids.
  const [opened, setOpened] = useState<ReadonlySet<string> | null>(null);

  const rows = useMemo(() => (data === null ? [] : buildRows(data)), [data]);
  const tracks = useMemo(() => assignTracks(rows), [rows]);
  // Read before the loading/error returns below, so the viewport hooks can
  // run unconditionally: `fullViewport` already answers for zero rows.
  const t0 = data === null ? 0 : data.t0;

  // The window. Zooming replaces the time domain every row renders through —
  // that is what makes the strip's drag a zoom rather than a filter — and
  // `bounds` stays the whole run, because every gesture clamps into it and the
  // strip draws it at every zoom level.
  const bounds = useMemo(() => fullViewport(rows, t0), [rows, t0]);
  const [zoom, setZoom] = useState<Viewport | null>(null);
  const view = zoom ?? bounds;

  const tree = useMemo(() => buildTree(rows), [rows]);
  // Arrival state: the descent through the heaviest child at every level, so
  // the page opens on where the wall clock went instead of on a closed root.
  const arrival = useMemo(() => slowestPath(tree), [tree]);
  const parentOf = useMemo(() => {
    const map = new Map<string, string | null>();
    for (const row of rows) map.set(row.id, row.parentId);
    return map;
  }, [rows]);

  const needle = query.trim().toLowerCase();
  const hits = useMemo(() => {
    if (needle === "") return null;
    const found = new Set<string>();
    for (const row of rows) {
      if ((row.name ?? "").toLowerCase().includes(needle)) found.add(row.id);
    }
    return found;
  }, [rows, needle]);
  // Every ancestor of every hit. A match inside a collapsed node is a match
  // the reader cannot see, so these are force-opened below and kept in the
  // filtered rows — a hit with no path to it reads as no hit at all.
  const ancestors = useMemo(() => {
    const out = new Set<string>();
    if (hits === null) return out;
    for (const id of hits) {
      let parent = parentOf.get(id) ?? null;
      // Stops at anything already accounted for: either a node an earlier
      // walk carried all the way to its root, or another hit, which this same
      // loop walks up from in its own turn.
      while (parent !== null && !out.has(parent) && !hits.has(parent)) {
        out.add(parent);
        parent = parentOf.get(parent) ?? null;
      }
    }
    return out;
  }, [hits, parentOf]);

  const expanded = useMemo(() => {
    const base = opened ?? arrival;
    if (hits === null) return base;
    const out = new Set(base);
    for (const id of ancestors) out.add(id);
    return out;
  }, [opened, arrival, hits, ancestors]);

  const visibleRows = useMemo(() => {
    const all = flatten(tree, expanded);
    if (hits === null) return all;
    const out: VisibleRow[] = [];
    for (const vrow of all) {
      const isHit = hits.has(vrow.row.id);
      if (!isHit && !ancestors.has(vrow.row.id)) continue;
      // The filter reaches inside the lanes too. A lane is drawn from the
      // TREE, not from this list, so leaving it alone would answer "q13" with
      // three lanes of sixty-six marks — the rows filtered and the marks not.
      const lanes =
        vrow.lanes === null
          ? null
          : vrow.lanes
              .map((lane) => lane.filter((row) => hits.has(row.id) || ancestors.has(row.id)))
              .filter((lane) => lane.length > 0);
      out.push(lanes === vrow.lanes ? vrow : { ...vrow, lanes: lanes !== null && lanes.length > 0 ? lanes : null });
    }
    return out;
  }, [tree, expanded, hits, ancestors]);

  const onToggle = useCallback(
    (id: string) => {
      const next = new Set(expanded);
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

  if (error !== null) return <ErrorView message={error} />;
  if (data === null) return <LoadingView />;

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
                matches={hits === null ? null : hits.size}
              />
              {kinds.length > 1 && <KindLegend kinds={kinds} />}
              <OverviewStrip rows={rows} bounds={bounds} view={view} onView={setZoom} />
              <div className="mt-3 flex items-stretch gap-3">
                <div className="min-w-0 flex-1">
                  <TraceTree
                    rows={visibleRows}
                    view={view}
                    onToggle={onToggle}
                    selectedId={selectedId}
                    onSelect={onSelect}
                  />
                </div>
                {/* Mounted only while a row is selected, and that is a
                    measured trade rather than a convenience. The content
                    column is 1104px at 1440px wide; the tree's name cap and
                    its two duration columns take a fixed 424px of any row, so
                    a permanent 264px dock would leave the lane 404px — 37% of
                    the row, below the 4x-wider lane this redesign was
                    accepted on. Empty, it would be spending that width to say
                    "select a span". */}
                {selectedRow !== null && (
                  <div
                    className="shrink-0 border-l border-border"
                    style={{ width: `${DOCK_PX}px` }}
                  >
                    <SpanDock row={selectedRow} view={view} />
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
