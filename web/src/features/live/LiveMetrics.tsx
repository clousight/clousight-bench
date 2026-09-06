/**
 * Numbers computed in the browser from an incomplete stream.
 *
 * Every tile here carries a 初步/preliminary chip, and the panel says outright
 * where these come from. That is not modesty — a scored measurement and a
 * lagging browser-side average are different kinds of claim, and letting the
 * two look alike is exactly how a screenshot ends up in a slide deck
 * misrepresenting a run that had not finished.
 */

import type { ProgressStep } from "@/api";
import { LatencyBars } from "@/charts/LatencyBars";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useI18n } from "@/i18n";
import { formatMetric } from "@/lib/glossary";
import { useLiveAggregates, type SampleSeries } from "@/lib/progressStream";

export interface LiveMetricsProps {
  steps: ProgressStep[];
  samples: SampleSeries[];
}

export function LiveMetrics({ steps, samples }: LiveMetricsProps) {
  const { t } = useI18n();
  const aggregates = useLiveAggregates(steps);

  if (aggregates.count === 0 && samples.length === 0) return null;

  const bars = steps
    .filter((step) => step.parent !== "")
    .map((step) => ({
      name: step.name,
      value: Math.max(0, step.end_ms - step.start_ms),
      status: step.status,
    }));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {t("live.metrics")}
          <span className="rounded bg-status-warning/15 px-1.5 py-px text-[10px] font-medium text-status-serious">
            {t("live.preliminary")}
          </span>
        </CardTitle>
        <CardDescription>{t("live.metrics_blurb")}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Tile label={t("live.completed_steps")} text={String(aggregates.count)} unit="" />
          <Tile label={t("live.p50")} {...duration(aggregates.p50Ms)} />
          <Tile label={t("live.p99")} {...duration(aggregates.p99Ms)} />
          <Tile
            label={t("live.errors")}
            text={String(aggregates.errors)}
            unit=""
            tone={aggregates.errors > 0 ? "bad" : undefined}
          />
        </div>

        {aggregates.slowest !== null && (
          <p className="text-xs text-muted-foreground">
            {t("live.slowest")
              .replace("{name}", aggregates.slowest.name)
              .replace(
                "{time}",
                (() => {
                  const formatted = formatMetric(
                    aggregates.slowest.end_ms - aggregates.slowest.start_ms,
                    "duration_ms",
                  );
                  return `${formatted.text}${formatted.unit}`;
                })(),
              )}
          </p>
        )}

        {bars.length > 1 && (
          <div className="border-t pt-3">
            <p className="mb-2 text-xs text-muted-foreground">{t("live.slowest_chart")}</p>
            <LatencyBars bars={bars} limit={12} ariaLabel={t("live.slowest_chart")} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function duration(ms: number | null): { text: string; unit: string } {
  if (ms === null) return { text: "—", unit: "" };
  return formatMetric(ms, "duration_ms");
}

function Tile({
  label,
  text,
  unit,
  tone,
}: {
  label: string;
  text: string;
  unit: string;
  tone?: "bad";
}) {
  return (
    <div>
      <div className="flex items-baseline gap-1">
        <span
          className={
            tone === "bad"
              ? "tabular-nums text-xl font-semibold text-status-critical"
              : "tabular-nums text-xl font-semibold"
          }
        >
          {text}
        </span>
        {unit !== "" && <span className="text-xs text-muted-foreground">{unit}</span>}
      </div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}
