/**
 * The "something is happening right now" strip.
 *
 * Shown above the board only while runs are in flight, and absent entirely
 * otherwise — a permanent empty "0 running" panel would train people to stop
 * looking at the one place that is supposed to catch their eye.
 *
 * Only genuinely running runs count. The progress plane keeps a finished run's
 * directory for a grace period so a subscriber can still read its handoff, so
 * `/api/progress` legitimately returns runs that have already ended; calling
 * those "running" — with a clock still ticking on them — would be a lie the
 * reader has no way to catch.
 */

import { ChevronRight } from "lucide-react";

import type { ProgressState } from "@/api";
import { Section, SectionBody } from "@/components/ui/section";
import { useI18n } from "@/i18n";
import { fmtClock, fmtRelative } from "@/lib/format";
import { fractionDone } from "@/lib/eta";
import { STAGE_GLOSSARY } from "@/lib/glossary";
import { StatusPill } from "@/components/Glossed";
import { suiteLabel } from "@/lib/headline";
import { liveHref, liveRunHref, recordHref } from "@/router";
import { cn } from "@/lib/utils";

/** The runs in this list that are actually still going. */
export function stillRunning(runs: ProgressState[]): ProgressState[] {
  return runs.filter((run) => run.status === "running");
}

export function LiveStrip({ runs }: { runs: ProgressState[] }) {
  const { t } = useI18n();
  const running = stillRunning(runs);
  if (running.length === 0) return null;
  return (
    <Section className="border-status-running/30 bg-status-running/[0.04]">
      <SectionBody className="flex flex-col gap-1 py-2">
        <div className="flex items-center gap-2 text-xs font-medium text-status-running">
          <span aria-hidden className="size-2 animate-pulse rounded-full bg-status-running" />
          {t("live.now_running").replace("{n}", String(running.length))}
          <a
            href={liveHref}
            className="ml-auto font-normal text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
          >
            {t("live.open_console")}
          </a>
        </div>
        {running.slice(0, 3).map((run) => (
          <LiveStripRow key={run.run_id} run={run} />
        ))}
      </SectionBody>
    </Section>
  );
}

/** Runs that ended within the progress plane's grace window — "what just finished". */
export function JustFinished({ runs }: { runs: ProgressState[] }) {
  const { t, locale } = useI18n();
  const done = runs.filter((run) => run.status !== "running");
  if (done.length === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      <h2 className="text-xs font-medium text-muted-foreground">{t("live.just_finished")}</h2>
      {done.map((run) => (
        <a
          key={run.run_id}
          href={recordHref(run.run_id)}
          className="group flex items-center gap-3 py-1.5 text-sm transition-colors hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span className="font-medium">{suiteLabel(run.suite_id)}</span>
          <span className="font-mono text-[11px] text-muted-foreground">{run.adapter}</span>
          <StatusPill status={run.status} />
          <span className="ml-auto text-xs text-muted-foreground">
            {fmtRelative(run.updated_at, locale)}
          </span>
          <ChevronRight
            className="size-3.5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
            aria-hidden
          />
        </a>
      ))}
    </div>
  );
}

function LiveStripRow({ run }: { run: ProgressState }) {
  const { locale } = useI18n();
  const stage = STAGE_GLOSSARY[run.stage];
  const step = run.step;
  const fraction = step === null ? null : fractionDone(step.completed, step.total);
  // Measured to `updated_at` once the run has stopped: a frozen row with a
  // clock still counting up reads as "still going".
  const end = run.status === "running" ? Date.now() : new Date(run.updated_at).getTime();
  const elapsed = Math.max(0, end - new Date(run.started_at).getTime());

  return (
    <a
      href={liveRunHref(run.run_id)}
      className="group flex items-center gap-3 py-1.5 text-sm transition-colors hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
