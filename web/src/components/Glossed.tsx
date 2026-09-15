/**
 * The two-layer rendering primitives.
 *
 * Every number on this app goes through here, which is what makes the
 * de-jargon rule mechanical rather than a habit: `MetricValue` cannot render a
 * human label without also rendering the raw key beneath it, and `StatusPill`
 * cannot render a status colour without an icon and a word beside it.
 */

import { AlertTriangle, CheckCircle2, CircleDashed, CircleHelp, Loader2, XCircle } from "lucide-react";
import type { ReactNode } from "react";

import { useI18n } from "@/i18n";
import {
  formatMetric,
  lookupMetric,
  lookupStatus,
  metricBlurb,
  metricLabel,
  REPRODUCIBILITY_GLOSSARY,
  type BetterIs,
  type StatusTone,
} from "@/lib/glossary";
import { cn } from "@/lib/utils";

/** A dotted-underline term with an explanation on hover and on focus. */
export function Glossed({ children, blurb }: { children: ReactNode; blurb: string | null }) {
  if (blurb === null || blurb === "") return <>{children}</>;
  return (
    <span
      tabIndex={0}
      title={blurb}
      className="cursor-help underline decoration-dotted decoration-muted-foreground/50 underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {children}
    </span>
  );
}

const TONE_STYLES: Record<StatusTone, string> = {
  good: "bg-status-good/12 text-status-good",
  warning: "bg-status-warning/15 text-status-serious",
  critical: "bg-status-critical/12 text-status-critical",
  running: "bg-status-running/12 text-status-running",
  neutral: "bg-muted text-muted-foreground",
};

const TONE_ICONS: Record<StatusTone, typeof CheckCircle2> = {
  good: CheckCircle2,
  warning: AlertTriangle,
  critical: XCircle,
  running: Loader2,
  neutral: CircleDashed,
};

/**
 * A run's verdict as icon + word + colour.
 *
 * The icon is not decoration: status colour is a reserved channel and never
 * carries meaning alone, so a reader who cannot separate the hues still reads
 * the shape and the word.
 */
export function StatusPill({ status, className }: { status: string; className?: string }) {
  const { locale } = useI18n();
  const spec = lookupStatus(status);
  const Icon = TONE_ICONS[spec.tone];
  return (
    <span
      title={locale === "zh" ? spec.blurb.zh : spec.blurb.en}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border border-transparent px-1.5 py-0 font-mono text-[10px] font-normal uppercase tracking-[0.08em]",
        TONE_STYLES[spec.tone],
        className,
      )}
    >
      <Icon className={cn("size-3", spec.tone === "running" && "animate-spin")} aria-hidden />
      {locale === "zh" ? spec.label.zh : spec.label.en}
    </span>
  );
}

/** Which direction is an improvement, said in words rather than only an arrow. */
function BetterHint({ betterIs }: { betterIs: BetterIs }) {
  const { t } = useI18n();
  if (betterIs === "none") return null;
  return (
    <span className="text-[10px] text-muted-foreground" title={t(`metric.better_${betterIs}`)}>
      {betterIs === "lower" ? "↓" : "↑"} {t(`metric.better_${betterIs}`)}
    </span>
  );
}

export interface MetricValueProps {
  measurementKey: string;
  value: unknown;
  unit?: string;
  reproducibility?: string;
  official?: boolean;
  /** `hero` for the big tiles, `row` for tables and lists. */
  variant?: "hero" | "row";
  className?: string;
}

/**
 * One measurement, rendered in two layers: the human label leads, the raw key
 * sits under it in mono.
 *
 * There is no prop to turn the raw key off. Hiding it would make a number
 * unauditable, and the whole point of the redesign was to make the record
 * readable without making it less trustworthy.
 */
export function MetricValue({
  measurementKey,
  value,
  unit,
  reproducibility,
  official,
  variant = "row",
  className,
}: MetricValueProps) {
  const { locale, t } = useI18n();
  const spec = lookupMetric(measurementKey, unit);
  const formatted = formatMetric(value, spec.format);
  const blurb = metricBlurb(spec, locale);
  const hero = variant === "hero";

  return (
    <div className={cn("min-w-0", className)}>
      <div className={cn("flex items-baseline gap-1.5", hero && "flex-wrap")}>
        <span
          className={cn("font-mono tabular-nums", hero ? "text-2xl font-medium tracking-tight" : "font-medium")}
        >
          {formatted.text}
        </span>
        {formatted.unit !== "" && (
          <span className={cn("text-muted-foreground", hero ? "text-sm" : "text-xs")}>{formatted.unit}</span>
        )}
      </div>
      <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5">
        <Glossed blurb={blurb}>
          <span className={cn("text-foreground/80", hero ? "text-sm" : "text-xs")}>
            {metricLabel(spec, locale)}
          </span>
        </Glossed>
        <BetterHint betterIs={spec.betterIs} />
      </div>
      <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-[10px] text-muted-foreground">
        <span className="truncate" title={measurementKey}>
          {measurementKey}
        </span>
        {reproducibility !== undefined && reproducibility !== "" && (
          <ReproducibilityChip value={reproducibility} />
        )}
        {official === true && (
          <span title={t("metric.official_blurb")} className="cursor-help">
            ✓ {t("metric.official")}
          </span>
        )}
        {!spec.known && (
          <span title={t("metric.unknown_blurb")} className="cursor-help">
            <CircleHelp className="inline size-3" aria-hidden /> {t("metric.unknown")}
          </span>
        )}
      </div>
    </div>
  );
}

/** `reproducibility_class`, as the sentence it actually means. */
export function ReproducibilityChip({ value }: { value: string }) {
  const { locale } = useI18n();
  const spec = REPRODUCIBILITY_GLOSSARY[value];
  if (spec === undefined) return <span>{value}</span>;
  return (
    <span
      title={locale === "zh" ? spec.blurb.zh : spec.blurb.en}
      className={cn(
        "cursor-help rounded px-1 py-px font-sans",
        spec.tone === "good" ? "bg-status-good/12 text-status-good" : "bg-status-warning/15 text-status-serious",
      )}
    >
      {locale === "zh" ? spec.label.zh : spec.label.en}
    </span>
  );
}

/** A label/value row for the detail panels. */
export function Field({
  label,
  blurb,
  children,
}: {
  label: string;
  blurb?: string | null;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-1.5">
      <dt className="w-32 shrink-0 text-xs text-muted-foreground">
        <Glossed blurb={blurb ?? null}>{label}</Glossed>
      </dt>
      <dd className="min-w-0 flex-1 text-sm">{children}</dd>
    </div>
  );
}
