/**
 * #/record/:id/trace — trajectory spans as a Transcript (conversation-style
 * cards, the default tab) and an ECharts Waterfall. Span selection lives above
 * the tabs, so a span picked in one tab stays open in the other.
 */

import { ArrowLeft, Brain, Wrench } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { useJSON, type TrajectoryData } from "@/api";
import { Waterfall } from "@/charts/Waterfall";
import { CopyButton } from "@/components/CopyButton";
import { EmptyView, ErrorView, LoadingView } from "@/components/StateViews";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useI18n } from "@/i18n";
import { fmtDur } from "@/lib/format";
import { cn } from "@/lib/utils";
import { buildRows, totalSeconds, type SpanRow } from "@/lib/trace";
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
    <Card className={cn(row.isError && "border-l-2 border-l-destructive")}>
      <button type="button" onClick={onToggle} className="block w-full text-left" aria-expanded={selected}>
        <CardHeader className="flex-row items-center gap-2.5 space-y-0">
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
        </CardHeader>
      </button>
      {row.isError && (
        <div className="px-4 pb-2 text-xs text-destructive">
          {t('trace.error')}: {row.error ?? row.status}
        </div>
      )}
      {selected && (
        <CardContent className="border-t pt-3">
          <AttrsPanel row={row} />
        </CardContent>
      )}
    </Card>
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
          href="#/"
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
      </div>

      {rows.length === 0 ? (
        <Card>
          <CardContent className="pt-4">
            <EmptyView />
          </CardContent>
        </Card>
      ) : (
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="transcript">{t('trace.transcript')}</TabsTrigger>
            <TabsTrigger value="waterfall">{t('trace.waterfall')}</TabsTrigger>
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
            <Card>
              <CardContent className="pt-4">
                <Waterfall rows={rows} t0={t0} onSelect={toggle} />
              </CardContent>
            </Card>
            {selectedRow !== null && (
              <Card className={cn(selectedRow.isError && "border-l-2 border-l-destructive")}>
                <CardHeader className="flex-row items-center gap-2.5 space-y-0">
                  <div className="min-w-0 flex-1 truncate text-sm font-medium">
                    {selectedRow.name ?? t('common.unnamed')}
                  </div>
                  <span className="shrink-0 whitespace-nowrap font-mono text-xs text-muted-foreground">
                    +{fmtDur(selectedRow.startS - t0)} · {fmtDur(selectedRow.endS - selectedRow.startS)}
                  </span>
                </CardHeader>
                <CardContent>
                  <AttrsPanel row={selectedRow} />
                </CardContent>
              </Card>
            )}
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
