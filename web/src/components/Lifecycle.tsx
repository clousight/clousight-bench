/**
 * The lifecycle, told two ways.
 *
 * `HealthLine` is the default: one sentence saying whether the machinery
 * behaved, which is all most readers ever need from eleven stages. `StageStrip`
 * is what it expands into — the same eleven stages grouped into their four
 * phases, each with a plain-language explanation of what it was for.
 *
 * The old viewer showed the strip unconditionally, which is how a reader ended
 * up staring at `PUBLISH: skipped` and wondering what they had done wrong.
 */

import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import { useI18n } from "@/i18n";
import { fmtDurMs } from "@/lib/format";
import {
  PHASE_LABELS,
  PHASE_OF_STAGE,
  PHASES,
  STAGE_GLOSSARY,
  STAGE_ORDER,
  STAGE_STATUS_LABELS,
  stageTone,
  type Phase,
  type StageTone,
} from "@/lib/glossary";
import { cn } from "@/lib/utils";

// A stage tile is a hairline, not a box: the left rule carries the tone (the
// same status colours the text below already uses), so pass/fail/skipped read
// at a glance without drawing the rounded, filled chip the chrome rule forbids.
const TONE_RULE: Record<StageTone, string> = {
  ok: "border-status-good/70",
  failed: "border-status-critical/80",
  // `--border` is close to invisible at a 2px rule (~1.2:1 on white, 10% white
  // alpha in dark) — skipped and pending would both read as "no mark". Both
  // get a `muted-foreground` rule instead, which is still achromatic but
  // actually renders; solid-vs-dashed then tells the two apart rather than
  // relying on a hue neither of them has.
  skipped: "border-muted-foreground/45",
  running: "border-status-running/80",
  pending: "border-dashed border-muted-foreground/25",
};

const TONE_TEXT: Record<StageTone, string> = {
  ok: "text-status-good",
  failed: "text-status-critical",
  skipped: "text-muted-foreground",
  running: "text-status-running",
  pending: "text-muted-foreground/60",
};

export interface StageStripProps {
  stages: Record<string, string>;
  timings?: Record<string, number>;
  /** The stage currently in flight, drawn as `running`. */
  activeStage?: string;
  /** Milliseconds the active stage has been running, for a live clock. */
  activeElapsedMs?: number;
}

/** All eleven stages, grouped by phase, each with its outcome and duration. */
export function StageStrip({ stages, timings = {}, activeStage, activeElapsedMs }: StageStripProps) {
  const { locale } = useI18n();
  const byPhase = new Map<Phase, string[]>(PHASES.map((phase) => [phase, []]));
  for (const stage of STAGE_ORDER) {
    // A stage with no recorded outcome and no live activity never happened for
    // this run; showing it as "pending" forever would be a lie, so it is only
    // drawn when the run is still in flight.
    const known = Object.hasOwn(stages, stage);
    if (!known && stage !== activeStage && activeStage === undefined) continue;
    byPhase.get(PHASE_OF_STAGE[stage])?.push(stage);
  }

  return (
    <div className="flex flex-col gap-3">
      {PHASES.map((phase) => {
        const members = byPhase.get(phase) ?? [];
        if (members.length === 0) return null;
        return (
          <div key={phase}>
            <div className="mb-1.5 font-mono text-[10px] font-normal uppercase leading-none tracking-[0.1em] text-muted-foreground">
              {locale === "zh" ? PHASE_LABELS[phase].zh : PHASE_LABELS[phase].en}
            </div>
            <div className="flex flex-wrap gap-2">
              {members.map((stage) => {
                const isActive = stage === activeStage;
                const tone = isActive ? "running" : stageTone(stages[stage]);
                const spec = STAGE_GLOSSARY[stage];
                const ms = isActive ? activeElapsedMs : timings[stage];
                return (
                  <div
                    key={stage}
                    title={spec === undefined ? stage : locale === "zh" ? spec.blurb.zh : spec.blurb.en}
                    className={cn(
                      "min-w-[7.5rem] cursor-help border-l-2 py-0.5 pl-2.5 transition-colors",
                      TONE_RULE[tone],
                    )}
                  >
                    <div className="text-xs font-medium">
                      {spec === undefined ? stage : locale === "zh" ? spec.label.zh : spec.label.en}
                    </div>
                    <div className="font-mono text-[10px] text-muted-foreground">{stage}</div>
                    <div className={cn("mt-0.5 flex items-baseline gap-1.5 text-[11px]", TONE_TEXT[tone])}>
                      <span>{locale === "zh" ? STAGE_STATUS_LABELS[tone].zh : STAGE_STATUS_LABELS[tone].en}</span>
                      {typeof ms === "number" && (
                        <span className="font-mono tabular-nums text-muted-foreground">{fmtDurMs(ms)}</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export interface HealthLineProps {
  stages: Record<string, string>;
  timings?: Record<string, number>;
  status: string;
}

/**
 * "Everything behaved · 11 stages, all passed [expand]".
 *
 * When something did NOT behave the line names the stage that broke, because
 * that is the one case where the detail is the headline.
 */
export function HealthLine({ stages, timings, status }: HealthLineProps) {
  const { t, locale } = useI18n();
  const [open, setOpen] = useState(false);

  const entries = Object.entries(stages);
  const failed = entries.filter(([, value]) => value === "failed").map(([stage]) => stage);
  const total = entries.length;
  const healthy = failed.length === 0;

  const summary = healthy
    ? t("health.all_ok").replace("{n}", String(total))
    : t("health.failed_at").replace(
        "{stages}",
        failed
          .map((stage) => {
            const spec = STAGE_GLOSSARY[stage];
            return spec === undefined ? stage : locale === "zh" ? spec.label.zh : spec.label.en;
          })
          .join("、"),
      );

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-md px-1 py-1 text-left text-sm transition-colors hover:bg-accent/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span
          aria-hidden
          className={cn(
            "size-2 shrink-0 rounded-full",
            healthy ? "bg-status-good" : "bg-status-critical",
            status === "running" && "animate-pulse bg-status-running",
          )}
        />
        <span className="min-w-0 flex-1 truncate text-muted-foreground">{summary}</span>
        {open ? (
          <ChevronDown className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        ) : (
          <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        )}
      </button>
      {open && (
        <div className="mt-3 border-t pt-3">
          <StageStrip stages={stages} timings={timings} />
        </div>
      )}
    </div>
  );
}
