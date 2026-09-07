/**
 * Everything in flight, at once.
 *
 * With exactly one run going this is a redundant hop, so it forwards straight
 * into that run — the common case is one benchmark in one terminal, and making
 * that person click a list of one is a small insult repeated often.
 */

import { useEffect } from "react";

import { usePolledJSON, type ProgressList } from "@/api";
import { ErrorView, LoadingView } from "@/components/StateViews";
import { Card, CardContent } from "@/components/ui/card";
import { JustFinished, LiveStrip, stillRunning } from "@/features/live/LiveStrip";
import { useI18n } from "@/i18n";
import { boardHref, liveRunHref } from "@/router";

const POLL_MS = 3000;

export function LiveConsole() {
  const { t } = useI18n();
  const live = usePolledJSON<ProgressList>("api/progress", POLL_MS);
  const runs = live.data?.runs ?? null;
  const running = runs === null ? [] : stillRunning(runs);
  // Forward only for a single RUNNING run: a lone finished one is a page worth
  // reading, not a redirect.
  const only = runs !== null && running.length === 1 && runs.length === 1 ? running[0].run_id : null;

  useEffect(() => {
    if (only !== null) window.location.hash = liveRunHref(only).slice(1);
  }, [only]);

  if (live.error !== null) return <ErrorView message={live.error} />;
  if (runs === null) return <LoadingView />;

  if (runs.length === 0) {
    return (
      <Card>
        <CardContent className="flex flex-col items-center gap-2 px-4 py-12 text-center">
          <p className="text-sm text-muted-foreground">{t("live.idle")}</p>
          <p className="max-w-md text-xs text-muted-foreground">{t("live.idle_hint")}</p>
          <a href={boardHref} className="mt-2 text-xs underline-offset-4 hover:underline">
            {t("live.back_to_board")}
          </a>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold tracking-tight">{t("live.console")}</h1>
      <LiveStrip runs={runs} />
      <JustFinished runs={runs} />
    </div>
  );
}
