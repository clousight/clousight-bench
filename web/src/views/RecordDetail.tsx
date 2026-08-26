import { ArrowLeft } from "lucide-react";
import { useMemo, type ReactNode } from "react";

import { useJSON, type RecordDetailData } from "@/api";
import { CopyButton } from "@/components/CopyButton";
import { EmptyView, ErrorView, LoadingView } from "@/components/StateViews";
import { StatusBadge } from "@/components/StatusBadge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useI18n } from "@/i18n";
import { fmtDur, fmtNum, truncate } from "@/lib/format";
import { traceHref } from "@/router";
import { cn } from "@/lib/utils";

function SectionCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

/** One provenance/artifact key-value row; omitted entirely for empty values. */
function KvRow({ label, value, copy, mono = true }: { label: string; value?: string; copy?: boolean; mono?: boolean }) {
  if (value === undefined || value === "") return null;
  return (
    <>
      <dt className="py-1 text-xs text-muted-foreground">{label}</dt>
      <dd className={cn("flex items-center gap-1 py-1 text-xs", mono && "font-mono")}>
        <span className="break-all">{truncate(value, 64)}</span>
        {copy === true && <CopyButton value={value} />}
      </dd>
    </>
  );
}

function stageTone(status: string): string {
  if (status === "ok" || status === "completed") return "border-success/40 text-success";
  if (status === "error" || status === "failed") return "border-destructive/40 text-destructive";
  if (status === "skipped") return "border-border text-muted-foreground/70";
  return "border-border text-muted-foreground";
}

export function RecordDetail({ runId }: { runId: string }) {
  const { t } = useI18n();
  const path = useMemo(() => `api/record/${encodeURIComponent(runId)}`, [runId]);
  const { data: record, error } = useJSON<RecordDetailData>(path);

  if (error !== null) return <ErrorView message={error} />;
  if (record === null) return <LoadingView />;

  const run = record.run ?? {};
  const provenance = record.provenance ?? {};
  const measurements = record.measurements ?? {};
  const measurementKeys = Object.keys(measurements).sort();
  const stages = run.stages ?? {};
  const stageNames = Object.keys(stages);
  const timings = run.stage_timings ?? {};
  const errors = (record.errors ?? []).filter((entry) => entry !== null && typeof entry === "object");
  const artifacts = (record.artifacts ?? []).filter((entry) => entry !== null && typeof entry === "object");
  const hasTrajectory = artifacts.some((artifact) => artifact.kind === "trajectory");

  return (
    <div className="flex flex-col gap-4">
      <nav className="flex items-center justify-between">
        <a
          href="#/"
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-4" /> {t('common.back')}
        </a>
        {hasTrajectory && (
          <a
            href={traceHref(runId)}
            className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          >
            🧵 {t('detail.open_trace')}
          </a>
        )}
      </nav>

      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold tracking-tight">{t('detail.title')}</h2>
        <span className="font-mono text-sm text-muted-foreground">{runId}</span>
        <StatusBadge status={record.status ?? ""} />
      </div>

      <SectionCard title={t('detail.measurements')}>
        {measurementKeys.length === 0 ? (
          <EmptyView />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t('detail.key')}</TableHead>
                <TableHead>{t('detail.value')}</TableHead>
                <TableHead>{t('detail.unit')}</TableHead>
                <TableHead>{t('detail.official')}</TableHead>
                <TableHead>{t('detail.repro')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {measurementKeys.map((key) => {
                const entry = measurements[key];
                return (
                  <TableRow key={key}>
                    <TableCell className="font-mono text-xs">{key}</TableCell>
                    <TableCell className="font-mono text-xs font-medium">{fmtNum(entry.value)}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{entry.unit ?? ""}</TableCell>
                    <TableCell className="text-xs">{entry.official === true ? "✓" : "—"}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {entry.reproducibility_class ?? ""}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </SectionCard>

      <SectionCard title={t('detail.provenance')}>
        <dl className="grid grid-cols-[max-content_1fr] items-center gap-x-6">
          <KvRow label={t('list.suite')} value={provenance.suite_id} mono={false} />
          <KvRow label={t('detail.suite_version')} value={provenance.suite_version} copy />
          <KvRow
            label={t('detail.evaluator')}
            value={
              provenance.evaluator_id === undefined || provenance.evaluator_id === ""
                ? undefined
                : provenance.evaluator_id + (provenance.evaluator_official === true ? " ✓" : "")
            }
            mono={false}
          />
          <KvRow label={t('detail.scaffold')} value={provenance.scaffold} mono={false} />
          <KvRow label={t('detail.digest')} value={provenance.dataset_digest} copy />
        </dl>
      </SectionCard>

      <SectionCard title={t('detail.stages')}>
        {stageNames.length === 0 ? (
          <EmptyView />
        ) : (
          <div className="flex flex-wrap gap-2">
            {stageNames.map((name) => {
              const status = String(stages[name] ?? "");
              const seconds = timings[name];
              return (
                <div
                  key={name}
                  className={cn("min-w-24 rounded-md border px-2.5 py-1.5", stageTone(status))}
                >
                  <div className="font-mono text-[11px] font-medium text-foreground">{name}</div>
                  <div className="text-xs">{status}</div>
                  <div className="font-mono text-[11px] text-muted-foreground">
                    {typeof seconds === "number" ? fmtDur(seconds) : "—"}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </SectionCard>

      {errors.length > 0 && (
        <SectionCard title={t('detail.errors')}>
          <ul className="flex flex-col gap-2">
            {errors.map((entry, index) => (
              <li
                key={index}
                className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm"
              >
                <span className="font-mono text-xs font-medium text-destructive">
                  [{entry.stage ?? "?"}] {entry.code ?? ""}
                </span>
                {entry.message !== undefined && entry.message !== "" && (
                  <div className="mt-1 text-xs text-foreground/90">{entry.message}</div>
                )}
              </li>
            ))}
          </ul>
        </SectionCard>
      )}

      <SectionCard title={t('detail.artifacts')}>
        {artifacts.length === 0 ? (
          <EmptyView />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t('detail.kind')}</TableHead>
                <TableHead>{t('detail.media')}</TableHead>
                <TableHead>{t('detail.sha256')}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {artifacts.map((artifact, index) => (
                <TableRow key={index}>
                  <TableCell className="text-xs">{artifact.kind ?? ""}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{artifact.media ?? ""}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {artifact.sha256 !== undefined && artifact.sha256 !== "" && (
                      <span className="inline-flex items-center gap-1">
                        {truncate(artifact.sha256, 26)}
                        <CopyButton value={artifact.sha256} />
                      </span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </SectionCard>
    </div>
  );
}
