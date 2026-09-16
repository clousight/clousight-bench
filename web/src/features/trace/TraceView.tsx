/**
 * #/record/:id/trace — trajectory spans as a Transcript (conversation-style
 * cards, the default tab) and an ECharts Waterfall. Span selection lives above
 * the tabs, so a span picked in one tab stays open in the other.
 *
 * Two feeds can land here and they carry very different detail, so the page
 * says which one it got. A suite that writes its own trajectory artifact gives
 * per-query spans; every other run falls back to the run trace, which is the
 * eleven lifecycle stages and nothing finer. Before this fallback existed,
 * "view trace" simply did not appear for most runs, which read as "this run
 * has no trace" when in fact one had been on disk the whole time.
 */

import { ArrowLeft, Brain, Wrench } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { useJSON, type TrajectoryData } from "@/api";
import { KindLegend, Waterfall } from "@/charts/Waterfall";
import { CopyButton } from "@/components/CopyButton";
import { EmptyView, ErrorView, LoadingView } from "@/components/StateViews";
import { Section, SectionBody, SectionHead } from "@/components/ui/section";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AggregatedPane } from "@/features/timeline/AggregatedPane";
import { OverviewStrip } from "@/features/timeline/OverviewStrip";
import { TablePane } from "@/features/timeline/TablePane";
import { TrackList } from "@/features/timeline/TrackList";
import { useI18n } from "@/i18n";
import { fmtDur } from "@/lib/format";
import { fullSelection, selectSpans, type Selection } from "@/lib/selection";
import { buildRows, totalSeconds, type SpanRow } from "@/lib/trace";
import { assignTracks } from "@/lib/tracks";
import { cn } from "@/lib/utils";
import { recordHref } from "@/router";

function attrsJson(row: SpanRow): string {
  return JSON.stringify(row.attrs, null, 2);
}

/** The expandable attributes panel — identical in both tabs. */
function AttrsPanel({ row }: { row: SpanRow }) {
  const { t } = useI18n();
  const json = attrsJson(row);
  return (
    <div>
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">{t('trace.attrs')}</span>
        <CopyButton value={json} />
      </div>
      <pre className="mt-1 overflow-x-auto rounded-md bg-muted/50 p-2 font-mono text-[11px] leading-4">
        {json}
      </pre>
    </div>
  );
}

function TranscriptCard({
  row,
  t0,
  selected,
  onToggle,
}: {
  row: SpanRow;
  t0: number;
  selected: boolean;
  onToggle: () => void;
}) {
  const { t } = useI18n();
  const name = row.name ?? t('common.unnamed');
  const Icon = row.kind === "llm_call" ? Brain : Wrench;
  return (
    <Section className={cn(row.isError && "border-l-2 border-l-destructive")}>
      <button type="button" onClick={onToggle} className="block w-full text-left" aria-expanded={selected}>
        <SectionHead className="flex-row items-center gap-2.5">
          <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          <div className="min-w-0 flex-1">
            {row.ancestors.length > 0 && (
              <div className="truncate text-[11px] text-muted-foreground">
                {[...row.ancestors.map((ancestor) => ancestor ?? t('common.unnamed')), name].join(" › ")}
              </div>
            )}
            <div className="truncate text-sm font-medium leading-tight">{name}</div>
          </div>
          <span className="shrink-0 whitespace-nowrap font-mono text-xs text-muted-foreground">
            +{fmtDur(row.startS - t0)} · {fmtDur(row.endS - row.startS)}
          </span>
        </SectionHead>
      </button>
      {row.isError && (
        <div className="pb-2 text-xs text-destructive">
          {t('trace.error')}: {row.error ?? row.status}
        </div>
      )}
      {selected && (
        <SectionBody className="border-t pt-3">
          <AttrsPanel row={row} />
        </SectionBody>
      )}
    </Section>
  );
}

export function TraceView({ runId }: { runId: string }) {
  const { t } = useI18n();
  const path = useMemo(() => `api/record/${encodeURIComponent(runId)}/trajectory`, [runId]);
  const { data, error } = useJSON<TrajectoryData>(path);

  const [tab, setTab] = useState("transcript");
  // Shared across the tabs: the span whose attributes are open.
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const rows = useMemo(() => (data === null ? [] : buildRows(data)), [data]);
  const tracks = useMemo(() => assignTracks(rows), [rows]);
  const [selection, setSelection] = useState<Selection | null>(null);
  // Memoised, not computed inline: `fullSelection` builds a fresh object and a
  // fresh Set every call, so while `selection` is null — the arrival state,
  // where a reader spends most of their time — an inline call would hand every
  // consumer a new identity on every render. That churns `visible`, invalidates
  // TrackList's ~900-element lane memo, and lands in Waterfall's effect
  // dependencies, which dispose and re-init the ECharts instance. A click on a
  // bar would have rebuilt the chart.
  const effective = useMemo(
    () => selection ?? fullSelection(rows, tracks),
    [selection, rows, tracks],
  );
  const visible = useMemo(() => selectSpans(rows, tracks, effective), [rows, tracks, effective]);
  const kinds = useMemo(() => {
    const seen: string[] = [];
    for (const row of rows) if (row.kind !== "" && !seen.includes(row.kind)) seen.push(row.kind);
    return seen;
  }, [rows]);
  const toggle = useCallback(
    (id: string) => setSelectedId((current) => (current === id ? null : id)),
    [],
  );

  if (error !== null) return <ErrorView message={error} />;
  if (data === null) return <LoadingView />;

  const t0 = data.t0;
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
              {kinds.length > 1 && <KindLegend kinds={kinds} />}
              <OverviewStrip
                rows={rows}
                t0={t0}
                totalS={total}
                selection={effective}
                onChange={setSelection}
              />
              <div className="mt-3">
                <TrackList
                  tracks={tracks}
                  rows={rows}
                  t0={t0}
                  totalS={total}
                  selection={effective}
                  onChange={setSelection}
                />
              </div>
            </SectionBody>
          </Section>
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabsTrigger value="transcript">{t('trace.transcript')}</TabsTrigger>
              <TabsTrigger value="waterfall">{t('timeline.time_order')}</TabsTrigger>
              <TabsTrigger value="aggregated">{t('timeline.aggregated')}</TabsTrigger>
              <TabsTrigger value="table">{t('timeline.table')}</TabsTrigger>
            </TabsList>

            <TabsContent value="transcript" className="flex flex-col gap-2">
              {rows.map((row) => (
                <TranscriptCard
                  key={row.id}
                  row={row}
                  t0={t0}
                  selected={selectedId === row.id}
                  onToggle={() => toggle(row.id)}
                />
              ))}
            </TabsContent>

            <TabsContent value="waterfall" className="flex flex-col gap-3">
              <Section>
                <SectionBody className="pt-4">
                  {/* The legend is in the chrome above the tabs, where it is
                      co-visible with the strip and the lanes on every tab. */}
                  <Waterfall rows={visible} t0={t0} onSelect={toggle} hideLegend />
                </SectionBody>
              </Section>
              {selectedRow !== null && (
                <Section className={cn(selectedRow.isError && "border-l-2 border-l-destructive")}>
                  <SectionHead className="flex-row items-center gap-2.5">
                    <div className="min-w-0 flex-1 truncate text-sm font-medium">
                      {selectedRow.name ?? t('common.unnamed')}
                    </div>
                    <span className="shrink-0 whitespace-nowrap font-mono text-xs text-muted-foreground">
                      +{fmtDur(selectedRow.startS - t0)} · {fmtDur(selectedRow.endS - selectedRow.startS)}
                    </span>
                  </SectionHead>
                  <SectionBody>
                    <AttrsPanel row={selectedRow} />
                  </SectionBody>
                </Section>
              )}
            </TabsContent>

            <TabsContent value="aggregated">
              <Section>
                <SectionBody className="pt-4">
                  {/* The window travels with the rows because `aggregate`
                      clips to it: `visible` is an overlap filter, so a span
                      straddling an edge arrives whole and would be counted
                      whole. Passed as the memoised `effective` object rather
                      than as two numbers or a fresh `{startS, endS}` literal —
                      each pane memoises on prop identity, and a literal built
                      in this JSX would be a new object on every render, which
                      during a brush drag means re-aggregating every pointermove
                      instead of once per window.

                      `allRows` is the whole trace and is not a duplicate of
                      `visible`: self time is a property of the span, so the
                      children subtracted from a parent must not disappear
                      when the reader unchecks the lane that holds them. It is
                      the same memoised `rows` array both panes already see
                      through `visible`, so it adds no instability. */}
                  <AggregatedPane rows={visible} allRows={rows} window={effective} />
                </SectionBody>
              </Section>
            </TabsContent>

            <TabsContent value="table">
              <Section>
                <SectionBody className="pt-4">
                  {/* `allRows` for the same reason as the pane above: the
                      self column must not change meaning when a lane is
                      unchecked. */}
                  <TablePane rows={visible} allRows={rows} window={effective} onSelect={toggle} />
                </SectionBody>
              </Section>
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  );
}
