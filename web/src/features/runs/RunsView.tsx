/**
 * Every run, flat — the escape hatch.
 *
 * This is what the old landing page was, and it is genuinely useful once you
 * already know which run you are hunting for. It is no longer the front door,
 * because "178 rows, newest first" answers no question anybody arrives with.
 */

import { useMemo, useState } from "react";

import { useJSON, type RecordSummary } from "@/api";
import { StatusPill } from "@/components/Glossed";
import { EmptyView, ErrorView, LoadingView } from "@/components/StateViews";
import { Badge } from "@/components/ui/badge";
import { Section, SectionBody } from "@/components/ui/section";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { fmtRelative } from "@/lib/format";
import { formatMetric, lookupMetric, metricLabel } from "@/lib/glossary";
import { orderMetricKeys, suiteLabel } from "@/lib/headline";
import { recordHref, traceHref } from "@/router";

function filterKey(record: RecordSummary): string {
  return [
    record.run_id,
    record.task_id,
    record.domain,
    record.adapter,
    record.status,
    record.suite_id,
    record.scaffold,
  ]
    .join(" ")
    .toLowerCase();
}

export function RunsView() {
  const { t, locale } = useI18n();
  const { data: records, error } = useJSON<RecordSummary[]>("api/records");
  const [query, setQuery] = useState("");

  const visible = useMemo(() => {
    if (records === null) return [];
    const needle = query.trim().toLowerCase();
    return needle === "" ? records : records.filter((record) => filterKey(record).includes(needle));
  }, [records, query]);

  if (error !== null) return <ErrorView message={error} />;
  if (records === null) return <LoadingView />;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-lg font-semibold tracking-tight">
          {t('list.title')}
          <span className="ml-2 text-sm font-normal text-muted-foreground">{visible.length}</span>
        </h2>
        <Input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t('common.filter')}
          className="max-w-64"
        />
      </div>
      <Section>
        <SectionBody className="p-0">
          {visible.length === 0 ? (
            records.length > 0 ? (
              // Records exist but the filter matched none — say so, distinctly
              // from the genuinely-empty results directory.
              <p className="py-8 text-center text-sm text-muted-foreground">{t('list.no_match')}</p>
            ) : (
              <EmptyView />
            )
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('list.run')}</TableHead>
                  <TableHead>{t('list.task')}</TableHead>
                  <TableHead>{t('list.adapter')}</TableHead>
                  <TableHead>{t('list.status')}</TableHead>
                  <TableHead>{t('list.started')}</TableHead>
                  <TableHead>{t('list.measurements')}</TableHead>
                  <TableHead>{t('list.trace')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visible.map((record) => (
                  <TableRow key={record.run_id}>
                    <TableCell className="whitespace-nowrap">
                      <div className="flex items-center gap-2">
                        <a
                          href={recordHref(record.run_id)}
                          className="font-mono text-xs font-medium underline-offset-4 hover:underline"
                        >
                          {record.run_id}
                        </a>
                        {record.suite_id !== "" && <Badge variant="outline">{suiteLabel(record.suite_id)}</Badge>}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="text-sm">{record.task_id}</div>
                      <div className="text-xs text-muted-foreground">{record.domain}</div>
                    </TableCell>
                    <TableCell className="text-sm">{record.adapter}</TableCell>
                    <TableCell>
                      <StatusPill status={record.status} />
                    </TableCell>
                    <TableCell className="whitespace-nowrap font-mono text-xs text-muted-foreground">
                      {fmtRelative(record.started_at, locale)}
                    </TableCell>
                    <TableCell>
                      <div className="flex max-w-80 flex-wrap gap-1">
                        {orderMetricKeys(Object.keys(record.measurements)).map((key) => {
                          const spec = lookupMetric(key);
                          const formatted = formatMetric(record.measurements[key], spec.format);
                          return (
                            <span
                              key={key}
                              title={key}
                              className="cursor-help rounded border bg-muted/50 px-1.5 py-0.5 text-[11px] leading-4"
                            >
                              <span className="text-muted-foreground">{metricLabel(spec, locale)}</span>{" "}
                              <span className="tabular-nums">
                                {formatted.text}
                                {formatted.unit}
                              </span>
                            </span>
                          );
                        })}
                      </div>
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {record.has_trajectory && (
                        <a
                          href={traceHref(record.run_id)}
                          className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                        >
                          🧵 {t('list.trace')}
                        </a>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </SectionBody>
      </Section>
    </div>
  );
}
