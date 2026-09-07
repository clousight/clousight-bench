/**
 * One run, read top-down: what it found, whether the machinery behaved, what
 * it measured, and whether the numbers can be trusted — in that order.
 *
 * The previous detail page opened with a measurements table and closed with
 * eleven stage cards, which meant the first thing a reader saw was a grid of
 * raw keys and the last thing was `PUBLISH: skipped`. Here the conclusion
 * leads, the machinery collapses to one line, and the fields that only an
 * engineer needs live behind the engineer-view switch.
 */

import { ArrowRight } from "lucide-react";

import { useJSON, type RecordDetailData } from "@/api";
import { CopyButton } from "@/components/CopyButton";
import { Field, Glossed, MetricValue, StatusPill } from "@/components/Glossed";
import { HealthLine } from "@/components/Lifecycle";
import { ErrorView, LoadingView } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { EngineerPanel } from "@/features/record/EngineerPanel";
import { useI18n } from "@/i18n";
import { fmtDate, fmtDurMs } from "@/lib/format";
import { lookupStatus } from "@/lib/glossary";
import { headlineMetrics, orderMetricKeys, suiteLabel } from "@/lib/headline";
import { boardHref, suiteHref, traceHref } from "@/router";

export function RecordView({ runId }: { runId: string }) {
  const { t, locale } = useI18n();
  const record = useJSON<RecordDetailData>(`api/record/${encodeURIComponent(runId)}`);

  if (record.error !== null) return <ErrorView message={record.error} />;
  if (record.data === null) return <LoadingView />;

  const data = record.data;
  const status = data.status ?? "";
  const run = data.run ?? {};
  const identity = data.identity ?? {};
  const provenance = data.provenance ?? {};
  const measurements = data.measurements ?? {};
  const errors = data.errors ?? [];
  const artifacts = data.artifacts ?? [];
  const stages = (run.stages ?? {}) as Record<string, string>;
  const timings = (run.stage_timings ?? {}) as Record<string, number>;

  const flatMeasurements: Record<string, number> = {};
  for (const [key, entry] of Object.entries(measurements)) {
    if (typeof entry.value === "number") flatMeasurements[key] = entry.value;
  }
  const heroes = headlineMetrics(flatMeasurements, 4);
  const orderedKeys = orderMetricKeys(Object.keys(measurements));

  const wallMs = durationMs(run.started_at, run.finished_at);
  const statusSpec = lookupStatus(status);
  const suiteId = provenance.suite_id ?? "";
  const domain = identity.domain ?? "";

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <a
          href={suiteId !== "" && domain !== "" ? suiteHref(domain, suiteId) : boardHref}
          className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          ← {suiteId !== "" ? suiteLabel(suiteId) : t("board.title")}
        </a>
        <a
          href={traceHref(runId)}
          className="ml-auto inline-flex items-center gap-1 text-sm underline-offset-4 hover:underline"
        >
          {t("record.view_trace")}
          <ArrowRight className="size-3.5" aria-hidden />
        </a>
      </div>

      {/* Verdict */}
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="text-lg font-semibold tracking-tight">
            {suiteId !== "" ? suiteLabel(suiteId) : (identity.task_id ?? runId)}
          </h1>
          <span className="font-mono text-xs text-muted-foreground">{identity.adapter}</span>
          <StatusPill status={status} />
        </div>
        <p className="text-sm text-muted-foreground">
          {locale === "zh" ? statusSpec.blurb.zh : statusSpec.blurb.en}
        </p>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground">
          <span>{runId}</span>
          <CopyButton value={runId} />
          {run.started_at !== undefined && <span className="font-sans">{fmtDate(run.started_at, locale)}</span>}
          {wallMs !== null && (
            <span className="font-sans">{t("record.wall").replace("{time}", fmtDurMs(wallMs))}</span>
          )}
        </div>
      </div>

      {errors.length > 0 && (
        <Card className="border-status-critical/40 bg-status-critical/[0.05]">
          <CardHeader>
            <CardTitle>{t("record.what_broke")}</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {errors.map((error, index) => (
              <div key={index} className="text-sm">
                <span className="font-mono text-xs text-muted-foreground">
                  {error.stage} · {error.code}
                </span>
                <p className="mt-0.5 break-words">{error.message}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {heroes.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{t("record.headline")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
            {heroes.map((metric) => {
              const entry = measurements[metric.key];
              return (
                <MetricValue
                  key={metric.key}
                  variant="hero"
                  measurementKey={metric.key}
                  value={metric.value}
                  unit={entry?.unit}
                  reproducibility={entry?.reproducibility_class}
                  official={entry?.official}
                />
              );
            })}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardContent className="px-3 py-2">
          <HealthLine stages={stages} timings={timings} status={status} />
        </CardContent>
      </Card>

      {orderedKeys.length > heroes.length && (
        <Card>
          <CardHeader>
            <CardTitle>{t("record.all_measurements")}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {orderedKeys.map((key) => {
              const entry = measurements[key];
              return (
                <MetricValue
                  key={key}
                  measurementKey={key}
                  value={entry.value}
                  unit={entry.unit}
                  reproducibility={entry.reproducibility_class}
                  official={entry.official}
                />
              );
            })}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>{t("record.trust")}</CardTitle>
          <CardDescription>{t("record.trust_blurb")}</CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="divide-y">
            {provenance.suite_id !== undefined && (
              <Field label={t("record.suite")}>{suiteLabel(provenance.suite_id)}</Field>
            )}
            {provenance.suite_version !== undefined && provenance.suite_version !== "" && (
              <Field label={t("record.suite_version")} blurb={t("record.suite_version_blurb")}>
                <span className="font-mono text-xs">{provenance.suite_version}</span>
                <CopyButton value={provenance.suite_version} />
              </Field>
            )}
            {provenance.evaluator_id !== undefined && provenance.evaluator_id !== "" && (
              <Field label={t("record.evaluator")} blurb={t("record.evaluator_blurb")}>
                <span className="font-mono text-xs">{provenance.evaluator_id}</span>
                {provenance.evaluator_official === true && (
                  <span className="ml-2 text-xs text-status-good">✓ {t("metric.official")}</span>
                )}
              </Field>
            )}
            {provenance.dataset_digest !== undefined && provenance.dataset_digest !== "" && (
              <Field label={t("record.dataset")} blurb={t("record.dataset_blurb")}>
                <span className="break-all font-mono text-[11px]">{provenance.dataset_digest}</span>
                <CopyButton value={provenance.dataset_digest} />
              </Field>
            )}
          </dl>
        </CardContent>
      </Card>

      {artifacts.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{t("record.artifacts")}</CardTitle>
            <CardDescription>{t("record.artifacts_blurb")}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col divide-y">
            {artifacts.map((artifact, index) => (
              <div key={index} className="flex flex-wrap items-baseline gap-x-3 py-2 text-sm">
                <span className="font-medium">{artifact.kind}</span>
                <span className="font-mono text-xs text-muted-foreground">{artifact.path}</span>
                {artifact.sha256 !== undefined && (
                  <span className="ml-auto flex items-center gap-1">
                    <Glossed blurb={t("record.sha_blurb")}>
                      <span className="font-mono text-[10px] text-muted-foreground">
                        {artifact.sha256.slice(0, 20)}…
                      </span>
                    </Glossed>
                    <CopyButton value={artifact.sha256} />
                  </span>
                )}
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <EngineerPanel data={data} />

      {/* A run with no measurements at all still deserves an explanation. */}
      {orderedKeys.length === 0 && errors.length === 0 && (
        <p className="text-sm text-muted-foreground">{t("record.no_measurements")}</p>
      )}
    </div>
  );
}

/** Wall-clock milliseconds between two ISO timestamps, or null if unusable. */
function durationMs(startedAt?: string, finishedAt?: string): number | null {
  if (startedAt === undefined || finishedAt === undefined) return null;
  const start = new Date(startedAt).getTime();
  const end = new Date(finishedAt).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return null;
  return end - start;
}
