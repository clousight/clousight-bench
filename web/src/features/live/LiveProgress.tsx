/**
 * Where the run is, and how much is left.
 *
 * The estimate is labelled 预估/est. and withheld until there are enough
 * samples to mean anything — see lib/eta. A confidently wrong "3 seconds left"
 * costs more trust than an honest blank.
 */

import type { ProgressState } from "@/api";
import { StageStrip } from "@/components/Lifecycle";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useI18n } from "@/i18n";
import { estimateRemainingMs, fractionDone } from "@/lib/eta";
import { fmtClock, fmtDurMs } from "@/lib/format";
import { STAGE_GLOSSARY } from "@/lib/glossary";

export interface LiveProgressProps {
  state: ProgressState;
  /** Milliseconds since the run started, ticking. */
  elapsedMs: number;
}

export function LiveProgress({ state, elapsedMs }: LiveProgressProps) {
  const { t, locale } = useI18n();
  const step = state.step;
  // A phase that declares it cannot tick gets an indeterminate bar and a
  // sentence, not a 0% bar: the longest, most carefully measured phase of a TPC
  // run is exactly the one that cannot report from inside itself, and leaving
  // it pinned at 0/396 reads as a hang.
  const reports = step === null || step.reports_progress !== false;
  const fraction = step === null || !reports ? null : fractionDone(step.completed, step.total);

  const phaseElapsedMs = step === null ? 0 : Math.max(0, elapsedMs - step.started_ms);
  const remainingMs =
    step === null || !reports
      ? null
      : estimateRemainingMs({ completed: step.completed, total: step.total, elapsedMs: phaseElapsedMs });

  const stageSpec = STAGE_GLOSSARY[state.stage];
  const stageElapsedMs =
    state.stage_started_ms === null ? undefined : Math.max(0, elapsedMs - state.stage_started_ms);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("live.progress")}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <span className="text-sm font-medium">
            {stageSpec === undefined
              ? (state.stage ?? "")
              : locale === "zh"
                ? stageSpec.label.zh
                : stageSpec.label.en}
          </span>
          {state.stage !== "" && (
            <span className="font-mono text-[10px] text-muted-foreground">{state.stage}</span>
          )}
          {stageElapsedMs !== undefined && (
            <span className="tabular-nums text-xs text-muted-foreground">{fmtDurMs(stageElapsedMs)}</span>
          )}
        </div>

        {step !== null && (
          <div className="flex flex-col gap-1.5">
            <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 text-sm">
              <span className="font-medium">{step.label}</span>
              <span className="tabular-nums text-xs text-muted-foreground">
                {!reports && step.total > 0
                  ? t("live.of_total_unreported")
                      .replace("{total}", String(step.total))
                      .replace("{unit}", step.unit)
                  : step.total > 0
                    ? t("live.of_total")
                        .replace("{done}", String(step.completed))
                        .replace("{total}", String(step.total))
                        .replace("{unit}", step.unit)
                    : t("live.done_count")
                        .replace("{done}", String(step.completed))
                        .replace("{unit}", step.unit)}
              </span>
            </div>
            <div
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={step.total > 0 ? step.total : undefined}
              aria-valuenow={step.completed}
              aria-label={step.label}
              className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
            >
              <div
                className={
                  fraction === null
                    ? "h-full w-1/3 animate-[csb-indeterminate_1.6s_ease-in-out_infinite] rounded-full bg-status-running"
                    : "h-full rounded-full bg-status-running transition-[width] duration-500"
                }
                style={fraction === null ? undefined : { width: `${(fraction * 100).toFixed(1)}%` }}
              />
            </div>
            <div className="flex items-baseline justify-between text-xs text-muted-foreground">
              <span className="tabular-nums">{fmtClock(elapsedMs)}</span>
              {remainingMs === null ? (
                <span>{reports ? t("live.eta_unknown") : t("live.no_intermediate_progress")}</span>
              ) : (
                <span className="tabular-nums">
                  {t("live.eta").replace("{time}", fmtDurMs(remainingMs))}
                </span>
              )}
            </div>
          </div>
        )}

        <div className="border-t pt-3">
          <StageStrip
            stages={state.stages}
            timings={state.stage_timings}
            activeStage={state.status === "running" ? state.stage : undefined}
            activeElapsedMs={stageElapsedMs}
          />
        </div>
      </CardContent>
    </Card>
  );
}
