/**
 * The landing page: what has been measured, grouped by what it measures.
 *
 * The previous landing page was 178 runs, flat and newest-first, which put the
 * burden of finding a conclusion on the reader. This one answers "what do we
 * know about each kind of system?" first, and only drills down to an
 * individual run when someone asks for one.
 */

import { ChevronRight } from "lucide-react";
import { useState } from "react";

import {
  useJSON,
  usePolledJSON,
  type BoardData,
  type BoardDomain,
  type BoardSuite,
  type ProgressList,
} from "@/api";
import { StatusPill } from "@/components/Glossed";
import { ErrorView, LoadingView } from "@/components/StateViews";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LiveStrip } from "@/features/live/LiveStrip";
import { useI18n } from "@/i18n";
import { fmtRelative } from "@/lib/format";
import { formatMetric, metricLabel } from "@/lib/glossary";
import { DOMAIN_LABELS, headlineMetrics, suiteLabel } from "@/lib/headline";
import { runsHref, suiteHref } from "@/router";

/** How often to re-check for in-flight runs while sitting on the board. */
const LIVE_POLL_MS = 4000;

export function BoardView() {
  const { t, locale } = useI18n();
  const board = useJSON<BoardData>("api/board");
  const live = usePolledJSON<ProgressList>("api/progress", LIVE_POLL_MS);

  if (board.error !== null) return <ErrorView message={board.error} />;
  if (board.data === null) return <LoadingView />;

  // Most recently active domain first, not alphabetical. This results tree
  // still holds 26 suites from a benchmark generation that was removed in the
  // suite-first pivot; sorted by name they push everything anyone is actually
  // running today off the first screen.
  const domains = board.data.domains
    .slice()
    .sort((a, b) => lastActivity(b).localeCompare(lastActivity(a)));
  const running = live.data?.runs ?? [];

  return (
    <div className="flex flex-col gap-6">
      {running.length > 0 && <LiveStrip runs={running} />}

      <div className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold tracking-tight">{t("board.title")}</h1>
        <a
          href={runsHref}
          className="text-xs text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          {t("board.all_runs")}
        </a>
      </div>

      {domains.length === 0 ? (
        <Card>
          <CardContent className="px-4 py-8 text-center text-sm text-muted-foreground">
            {t("board.empty")}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {domains.map((domain) => (
            <Card key={domain.domain}>
              <CardHeader>
                <CardTitle className="flex items-baseline gap-2">
                  <span>
                    {locale === "zh"
                      ? (DOMAIN_LABELS[domain.domain]?.zh ?? domain.domain)
                      : (DOMAIN_LABELS[domain.domain]?.en ?? domain.domain)}
                  </span>
                  <span className="font-mono text-[10px] font-normal text-muted-foreground">
                    {domain.domain}
                  </span>
                </CardTitle>
              </CardHeader>
              <DomainSuites domain={domain} />
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

/** ISO timestamp of the newest run anywhere in a domain; "" when it has none. */
function lastActivity(domain: BoardDomain): string {
  let newest = "";
  for (const suite of domain.suites) {
    const started = suite.latest?.started_at ?? "";
    if (started > newest) newest = started;
  }
  return newest;
}

/** How many suites a domain card shows before it asks to be expanded. */
const SUITE_PREVIEW = 5;

/**
 * A domain's suites, newest first and capped.
 *
 * The cap is what keeps a domain with a long tail of retired benchmarks from
 * turning the board into a scroll — the point of this page is that the answer
 * is on the first screen.
 */
function DomainSuites({ domain }: { domain: BoardDomain }) {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);
  const ordered = domain.suites
    .slice()
    .sort((a, b) => (b.latest?.started_at ?? "").localeCompare(a.latest?.started_at ?? ""));
  const shown = expanded ? ordered : ordered.slice(0, SUITE_PREVIEW);
  const hidden = ordered.length - shown.length;

  return (
    <CardContent className="flex flex-col divide-y">
      {shown.map((suite) => (
        <SuiteTile key={suite.suite_id} domain={domain.domain} suite={suite} />
      ))}
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="-mx-2 rounded-md px-2 py-2 text-left text-xs text-muted-foreground transition-colors hover:bg-accent/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {t("board.show_more").replace("{n}", String(hidden))}
        </button>
      )}
    </CardContent>
  );
}

function SuiteTile({ domain, suite }: { domain: string; suite: BoardSuite }) {
  const { t, locale } = useI18n();
  const latest = suite.latest;
  const metrics = latest === null ? [] : headlineMetrics(latest.measurements, 3);

  return (
    <a
      href={suiteHref(domain, suite.suite_id)}
      className="group -mx-2 flex flex-col gap-2 rounded-md px-2 py-3 transition-colors first:pt-1 hover:bg-accent/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex items-baseline gap-2">
        <span className="text-sm font-medium">{suiteLabel(suite.suite_id)}</span>
        <span className="font-mono text-[10px] text-muted-foreground">{suite.suite_id}</span>
        <span className="ml-auto flex items-center gap-1 text-xs text-muted-foreground">
          {latest !== null && <span>{fmtRelative(latest.started_at, locale)}</span>}
          <ChevronRight className="size-3.5 transition-transform group-hover:translate-x-0.5" aria-hidden />
        </span>
      </div>

      {latest === null ? (
        <span className="text-xs text-muted-foreground">{t("board.no_runs")}</span>
      ) : (
        <div className="flex flex-wrap items-baseline gap-x-5 gap-y-1">
          {metrics.map((metric) => {
            const formatted = formatMetric(metric.value, metric.spec.format);
            return (
              <span key={metric.key} className="flex items-baseline gap-1.5">
                <span className="tabular-nums text-base font-semibold">{formatted.text}</span>
                {formatted.unit !== "" && (
                  <span className="text-[11px] text-muted-foreground">{formatted.unit}</span>
                )}
                <span className="text-xs text-muted-foreground">{metricLabel(metric.spec, locale)}</span>
              </span>
            );
          })}
          {metrics.length === 0 && <StatusPill status={latest.status} />}
        </div>
      )}

      <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
        <span>{t("board.platforms").replace("{n}", String(suite.platforms))}</span>
        <span>{t("board.runs").replace("{n}", String(suite.runs))}</span>
        {latest !== null && latest.status !== "completed" && <StatusPill status={latest.status} />}
      </div>
    </a>
  );
}
