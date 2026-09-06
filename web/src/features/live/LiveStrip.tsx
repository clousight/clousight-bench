/**
 * The "something is happening right now" strip.
 *
 * Shown above the board only while runs are in flight, and absent entirely
 * otherwise — a permanent empty "0 running" panel would train people to stop
 * looking at the one place that is supposed to catch their eye.
 */

import { ChevronRight } from "lucide-react";

import type { ProgressState } from "@/api";
import { Card, CardContent } from "@/components/ui/card";
import { useI18n } from "@/i18n";
import { fmtClock } from "@/lib/format";
import { fractionDone } from "@/lib/eta";
import { STAGE_GLOSSARY } from "@/lib/glossary";
import { suiteLabel } from "@/lib/headline";
import { liveHref, liveRunHref } from "@/router";
import { cn } from "@/lib/utils";

export function LiveStrip({ runs }: { runs: ProgressState[] }) {
  const { t } = useI18n();
  return (
    <Card className="border-status-running/30 bg-status-running/[0.04]">
      <CardContent className="flex flex-col gap-1 px-3 py-2">
        <div className="flex items-center gap-2 px-1 text-xs font-medium text-status-running">
          <span aria-hidden className="size-2 animate-pulse rounded-full bg-status-running" />
          {t("live.now_running").replace("{n}", String(runs.length))}
          <a
            href={liveHref}
            className="ml-auto font-normal text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          >
            {t("live.open_console")}
          </a>
        </div>
        {runs.slice(0, 3).map((run) => (
          <LiveStripRow key={run.run_id} run={run} />
        ))}
      </CardContent>
    </Card>
  );
}

function LiveStripRow({ run }: { run: ProgressState }) {
  const { locale } = useI18n();
  const stage = STAGE_GLOSSARY[run.stage];
  const step = run.step;
  const fraction = step === null ? null : fractionDone(step.completed, step.total);
  const elapsed = Date.now() - new Date(run.started_at).getTime();

  return (
    <a
      href={liveRunHref(run.run_id)}
      className="group flex items-center gap-3 rounded-md px-1 py-1.5 text-sm transition-colors hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span className="font-medium">{suiteLabel(run.suite_id)}</span>
      <span className="font-mono text-[11px] text-muted-foreground">{run.adapter}</span>

      {stage !== undefined && (
        <span className="text-xs text-muted-foreground">
          {locale === "zh" ? stage.label.zh : stage.label.en}
        </span>
      )}

      {step !== null && (
        <span className="flex min-w-0 flex-1 items-center gap-2">
          <span className="truncate text-xs text-muted-foreground">{step.label}</span>
          {fraction !== null && (
            <>
              <span
                aria-hidden
                className="h-1 w-24 shrink-0 overflow-hidden rounded-full bg-muted"
                role="presentation"
              >
                <span
                  className="block h-full rounded-full bg-status-running transition-[width] duration-500"
                  style={{ width: `${(fraction * 100).toFixed(1)}%` }}
                />
              </span>
              <span className="shrink-0 tabular-nums text-[11px] text-muted-foreground">
                {step.completed}/{step.total}
              </span>
            </>
          )}
        </span>
      )}

      <span className={cn("ml-auto shrink-0 tabular-nums text-xs text-muted-foreground")}>
        {fmtClock(elapsed)}
      </span>
      <ChevronRight
        className="size-3.5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
        aria-hidden
      />
    </a>
  );
}
