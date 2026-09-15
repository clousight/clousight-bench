/**
 * One run, watched while it happens.
 *
 * Everything on this page comes from a single SSE connection. When the run
 * ends the page does not go blank or 404 — it swaps to a handoff card pointing
 * at the sealed record, because "where did my run go?" is the worst possible
 * ending for a page whose whole job is to keep you informed.
 */

import { ArrowRight, OctagonX } from "lucide-react";
import { useState } from "react";

import { cancelRun } from "@/api";
import { StatusPill } from "@/components/Glossed";
import { ErrorView, LoadingView } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Section, SectionBody, SectionHead, SectionTitle } from "@/components/ui/section";
import { Waterfall } from "@/charts/Waterfall";
import { LiveMetrics } from "@/features/live/LiveMetrics";
import { LiveProgress } from "@/features/live/LiveProgress";
import { LogStream } from "@/features/live/LogStream";
import { stepsToRows } from "@/features/live/liveRows";
import { useI18n } from "@/i18n";
import { fmtClock } from "@/lib/format";
import { suiteLabel } from "@/lib/headline";
import { lookupStatus, type StatusTone } from "@/lib/glossary";
import { useProgressStream } from "@/lib/progressStream";
import { useNow } from "@/lib/ticker";
import { boardHref, recordHref } from "@/router";

/** The elapsed clock ticks once a second; anything faster is just churn. */
const TICK_MS = 1000;

export function LiveRunView({ runId }: { runId: string }) {
  const { t } = useI18n();
  const feed = useProgressStream(runId);
  const running = feed.state?.status === "running";
  const now = useNow(TICK_MS, running);

  if (feed.state === null) {
    return feed.error !== null ? <ErrorView message={feed.error} /> : <LoadingView />;
  }

  const state = feed.state;
  const startedMs = new Date(state.started_at).getTime();
  const elapsedMs = Number.isNaN(startedMs) ? 0 : Math.max(0, now - startedMs);
  const { rows, t0 } = stepsToRows(feed.steps);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {/* Back to the board, not to the console: with a single run in flight
            the console forwards straight back here, so "back" would loop. The
            nav's Live entry still reaches the console when there are several. */}
        <a
          href={boardHref}
          className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          ← {t("board.title")}
        </a>
        <h1 className="flex items-baseline gap-2 text-lg font-semibold tracking-tight">
          {suiteLabel(state.suite_id)}
          <span className="font-mono text-xs font-normal text-muted-foreground">{state.adapter}</span>
        </h1>
        <StatusPill status={state.status} />
        {running && <span className="font-mono tabular-nums text-sm text-muted-foreground">{fmtClock(elapsedMs)}</span>}
        <div className="ml-auto flex items-center gap-2">
          {running && <CancelButton runId={runId} requested={state.cancel_requested} />}
        </div>
      </div>

      <div className="font-mono text-[11px] text-muted-foreground">{state.run_id}</div>

      {feed.done && <Handoff recordPath={feed.recordPath} runId={runId} status={state.status} />}

      <LiveProgress state={state} elapsedMs={elapsedMs} />

      {rows.length > 0 && (
        <Section>
          <SectionHead>
            {/* Drops the "(live)" qualifier once the stream has ended, so a
                finished page does not keep claiming to be updating. */}
            <SectionTitle>{t(feed.done ? "live.waterfall_done" : "live.waterfall")}</SectionTitle>
          </SectionHead>
          <SectionBody>
            <Waterfall rows={rows} t0={t0} onSelect={() => undefined} axisMaxMs={elapsedMs} />
          </SectionBody>
        </Section>
      )}

      <LiveMetrics steps={feed.steps} samples={feed.samples} />
      <LogStream lines={feed.logs} />

      {Object.values(state.dropped).some((count) => count > 0) && (
        <p className="text-xs text-muted-foreground">
          {t("live.dropped").replace(
            "{summary}",
            Object.entries(state.dropped)
              .filter(([, count]) => count > 0)
              .map(([kind, count]) => `${kind} ×${count}`)
              .join(", "),
          )}
        </p>
      )}
    </div>
  );
}

// The band's tone follows the run's actual verdict — a failed run must not
// render inside a band that asserts "good", the one place on this branch
// where colour stated something the status beside it contradicted.
const BAND_STYLES: Record<StatusTone, string> = {
  good: "border-status-good/30 bg-status-good/[0.05]",
  warning: "border-status-warning/30 bg-status-warning/[0.05]",
  critical: "border-status-critical/30 bg-status-critical/[0.05]",
  running: "border-status-running/30 bg-status-running/[0.05]",
  neutral: "border-border bg-muted/20",
};

/** The end of the stream, pointing at the record that replaced it. */
function Handoff({
  recordPath,
  runId,
  status,
}: {
  recordPath: string | null;
  runId: string;
  status: string;
}) {
  const { t } = useI18n();
  const tone = lookupStatus(status).tone;
  return (
    <Section className={BAND_STYLES[tone]}>
      <SectionBody className="flex flex-wrap items-center gap-3 py-3">
        <StatusPill status={status} />
        <span className="text-sm">{t("live.finished")}</span>
        <a
          href={recordHref(runId)}
          className="ml-auto inline-flex items-center gap-1 text-sm font-medium underline-offset-4 hover:underline"
        >
          {t("live.open_record")}
          <ArrowRight className="size-3.5" aria-hidden />
        </a>
        {recordPath !== null && (
          <span className="w-full font-mono text-[10px] text-muted-foreground">{recordPath}</span>
        )}
      </SectionBody>
    </Section>
  );
}

/**
 * The viewer's only mutating control.
 *
 * Two-step on purpose: a stray click must not throw away a benchmark that has
 * been running for half an hour. Once requested it stays disabled — the run
 * decides when it can stop, and pretending otherwise would be a lie about who
 * is in control.
 */
function CancelButton({ runId, requested }: { runId: string; requested: boolean }) {
  const { t } = useI18n();
  const [arming, setArming] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (requested || sent) {
    return <span className="text-xs text-status-serious">{t("live.cancel_requested")}</span>;
  }

  if (!arming) {
    return (
      <Button variant="outline" size="sm" onClick={() => setArming(true)}>
        <OctagonX aria-hidden />
        {t("live.cancel")}
      </Button>
    );
  }

  return (
    <span className="flex items-center gap-2">
      {error !== null && <span className="text-xs text-destructive">{error}</span>}
      <span className="text-xs text-muted-foreground">{t("live.cancel_confirm")}</span>
      <Button
        variant="destructive"
        size="sm"
        onClick={() => {
          cancelRun(runId)
            .then(() => setSent(true))
            .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
        }}
      >
        {t("live.cancel_yes")}
      </Button>
      <Button variant="ghost" size="sm" onClick={() => setArming(false)}>
        {t("live.cancel_no")}
      </Button>
    </span>
  );
}
