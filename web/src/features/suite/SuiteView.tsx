/**
 * One benchmark, platforms side by side.
 *
 * This is the page a selection decision is actually made on, so the comparison
 * table leads and the trend charts follow. Metrics are ordered by the glossary
 * (headline first) rather than alphabetically, so the column that decides the
 * question is not buried between two diagnostics.
 */

import { ChevronRight } from "lucide-react";

import { useJSON, type SuiteCompareData, type SuitePlatform } from "@/api";
import { Glossed, StatusPill } from "@/components/Glossed";
import { ErrorView, LoadingView } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { TrendLine, type TrendSeries } from "@/charts/TrendLine";
import { useI18n } from "@/i18n";
import { fmtRelative } from "@/lib/format";
import { formatMetric, lookupMetric, metricBlurb, metricLabel } from "@/lib/glossary";
import { DOMAIN_LABELS, orderMetricKeys, suiteLabel } from "@/lib/headline";
import { boardHref, recordHref } from "@/router";
import { cn } from "@/lib/utils";

/** How many metrics get their own trend chart. Past this the page is a wall. */
const TREND_LIMIT = 4;

export function SuiteView({ domain, suiteId }: { domain: string; suiteId: string }) {
  const { t, locale } = useI18n();
  const path = `api/suite/${encodeURIComponent(domain)}/${encodeURIComponent(suiteId)}`;
  const compare = useJSON<SuiteCompareData>(path);

  if (compare.error !== null) return <ErrorView message={compare.error} />;
  if (compare.data === null) return <LoadingView />;

  const data = compare.data;
  // Only columns some platform's LATEST run actually produced. `metric_keys` is
  // the union over every run, and two runs of the same suite in different modes
  // (TPC-H plain vs official) share almost no keys — so the union renders a
  // table that is mostly em-dashes, comparing nothing.
  const comparable = new Set<string>();
  for (const platform of data.platforms) {
    for (const [key, value] of Object.entries(platform.latest?.measurements ?? {})) {
      if (typeof value === "number" && Number.isFinite(value)) comparable.add(key);
    }
  }
  const keys = orderMetricKeys(data.metric_keys.filter((key) => comparable.has(key)));
  const trendKeys = orderMetricKeys(data.metric_keys).slice(0, TREND_LIMIT);

  return (
    <div className="flex flex-col gap-5">
      <div>
        <a
          href={boardHref}
          className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          ← {t("board.title")}
        </a>
        <h1 className="mt-2 flex flex-wrap items-baseline gap-2 text-lg font-semibold tracking-tight">
          {suiteLabel(data.suite_id)}
          <span className="font-mono text-xs font-normal text-muted-foreground">{data.suite_id}</span>
          <span className="text-xs font-normal text-muted-foreground">
            {locale === "zh"
              ? (DOMAIN_LABELS[data.domain]?.zh ?? data.domain)
              : (DOMAIN_LABELS[data.domain]?.en ?? data.domain)}
          </span>
        </h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{t("suite.compare")}</CardTitle>
          <CardDescription>{t("suite.compare_blurb")}</CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <ComparisonTable platforms={data.platforms} keys={keys} />
        </CardContent>
      </Card>

      {trendKeys.length > 0 && data.platforms.some((platform) => platform.history.length > 1) && (
        <div className="grid gap-4 md:grid-cols-2">
          {trendKeys.map((key) => (
            <TrendCard key={key} metricKey={key} platforms={data.platforms} />
          ))}
        </div>
      )}
    </div>
  );
}

function ComparisonTable({ platforms, keys }: { platforms: SuitePlatform[]; keys: string[] }) {
  const { t, locale } = useI18n();
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b text-left align-bottom">
          <th className="py-2 pr-4 font-medium">{t("suite.platform")}</th>
          {keys.map((key) => {
            const spec = lookupMetric(key);
            return (
              <th key={key} className="px-3 py-2 font-medium">
                <Glossed blurb={metricBlurb(spec, locale)}>
                  <span>{metricLabel(spec, locale)}</span>
                </Glossed>
                <div className="font-mono text-[10px] font-normal text-muted-foreground">{key}</div>
                {spec.betterIs !== "none" && (
                  <div className="text-[10px] font-normal text-muted-foreground">
                    {spec.betterIs === "lower" ? "↓" : "↑"} {t(`metric.better_${spec.betterIs}`)}
                  </div>
                )}
              </th>
            );
          })}
          <th className="py-2 pl-3 font-medium">{t("suite.latest_run")}</th>
        </tr>
      </thead>
      <tbody>
        {platforms.map((platform) => {
          const best = bestByMetric(platforms, keys);
          return (
            <tr key={platform.adapter} className="border-b last:border-0">
              <td className="py-2.5 pr-4">
                <div className="font-medium">{platform.adapter}</div>
                <div className="text-[11px] text-muted-foreground">
                  {t("board.runs").replace("{n}", String(platform.runs))}
                </div>
              </td>
              {keys.map((key) => {
                const value = platform.latest?.measurements[key];
                const spec = lookupMetric(key);
                if (typeof value !== "number") {
                  return (
                    <td key={key} className="px-3 py-2.5 text-muted-foreground">
                      —
                    </td>
                  );
                }
                const formatted = formatMetric(value, spec.format);
                const leads = best.get(key) === platform.adapter && platforms.length > 1;
                return (
                  <td key={key} className="px-3 py-2.5">
                    <span
                      className={cn("tabular-nums", leads && "font-semibold")}
                      title={leads ? t("suite.leads") : undefined}
                    >
                      {formatted.text}
                      {formatted.unit !== "" && (
                        <span className="ml-0.5 text-[11px] text-muted-foreground">{formatted.unit}</span>
                      )}
                    </span>
                  </td>
                );
              })}
              <td className="py-2.5 pl-3">
                {platform.latest === null ? (
                  <span className="text-muted-foreground">—</span>
                ) : (
                  <a
                    href={recordHref(platform.latest.run_id)}
                    className="group inline-flex items-center gap-1.5 underline-offset-4 hover:underline"
                  >
                    <span className="text-xs text-muted-foreground">
                      {fmtRelative(platform.latest.started_at, locale)}
                    </span>
                    {platform.latest.status !== "completed" && (
                      <StatusPill status={platform.latest.status} />
                    )}
                    <ChevronRight
                      className="size-3.5 text-muted-foreground transition-transform group-hover:translate-x-0.5"
                      aria-hidden
                    />
                  </a>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/**
 * Which platform currently leads on each metric.
 *
 * Only computed where the glossary states a direction — a metric we do not
 * know how to read has no winner, and inventing one would be worse than
 * leaving the column unmarked.
 */
function bestByMetric(platforms: SuitePlatform[], keys: string[]): Map<string, string> {
  const best = new Map<string, string>();
  for (const key of keys) {
    const spec = lookupMetric(key);
    if (spec.betterIs === "none") continue;
    let leader: { adapter: string; value: number } | null = null;
    for (const platform of platforms) {
      const value = platform.latest?.measurements[key];
      if (typeof value !== "number" || !Number.isFinite(value)) continue;
      if (
        leader === null ||
        (spec.betterIs === "lower" ? value < leader.value : value > leader.value)
      ) {
        leader = { adapter: platform.adapter, value };
      }
    }
    if (leader !== null) best.set(key, leader.adapter);
  }
  return best;
}

function TrendCard({ metricKey, platforms }: { metricKey: string; platforms: SuitePlatform[] }) {
  const { locale, t } = useI18n();
  const spec = lookupMetric(metricKey);

  const series: TrendSeries[] = platforms
    .map((platform) => ({
      name: platform.adapter,
      points: platform.history
        .filter((point) => typeof point.measurements[metricKey] === "number")
        .map((point, index): [string, number] => [
          `#${index + 1}`,
          point.measurements[metricKey],
        ]),
    }))
    .filter((line) => line.points.length > 1);

  if (series.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{metricLabel(spec, locale)}</CardTitle>
        <CardDescription className="font-mono">{metricKey}</CardDescription>
      </CardHeader>
      <CardContent>
        <TrendLine
          series={series}
          format={spec.format}
          ariaLabel={t("suite.trend_of").replace("{metric}", metricLabel(spec, locale))}
        />
      </CardContent>
    </Card>
  );
}
