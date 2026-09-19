/**
 * What this build can measure: every installed benchmark, with its pin.
 *
 * The results board next door answers "what did we measure". This answers the
 * question a reader has before there are any results — which benchmarks exist
 * here, which data each one is pinned to, and what can score it.
 *
 * "Nobody has run this" and "nothing can score this" are different facts and
 * get different words: the first is ordinary, the second means the benchmark
 * cannot produce a result at all until an evaluator is installed.
 */

import { useJSON, type InstalledSuite, type InstalledSuiteList } from "@/api";
import { PageHeader } from "@/components/shell/Page";
import { useI18n } from "@/i18n";
import { suiteLabel } from "@/lib/headline";

export function SuitesTable({ suites }: { suites: InstalledSuite[] }) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col divide-y divide-border border-t border-border">
      {suites.map((suite) => (
        <section key={suite.suite_id} className="flex flex-col gap-1.5 py-3">
          <div className="flex flex-wrap items-baseline gap-x-3">
            <h2 className="text-sm font-semibold">{suiteLabel(suite.suite_id)}</h2>
            <span className="font-mono text-[11px] text-muted-foreground">{suite.suite_id}</span>
            <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">
              {suite.runs} {t("suites.runs")}
            </span>
          </div>
          <div className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted-foreground">
            <span>{t("suites.pinned")}</span>
            <span className="font-mono text-foreground">{suite.suite_version || "—"}</span>
          </div>
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <span>{t("suites.scored_by")}</span>
            {suite.evaluators.length === 0 ? (
              <span className="text-status-serious">{t("suites.no_evaluator")}</span>
            ) : (
              suite.evaluators.map((ev) => (
                <span key={ev.evaluator_id} className="font-mono text-foreground">
                  {ev.evaluator_id}
                  {ev.official && (
                    <span className="ml-1 font-sans text-[10px] uppercase tracking-[0.08em] text-status-good">
                      {t("suites.official")}
                    </span>
                  )}
                </span>
              ))
            )}
          </div>
          <div className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted-foreground">
            <span>{t("suites.run_on")}</span>
            <span className="font-mono text-foreground">
              {suite.seen_platforms.length === 0 ? t("suites.nowhere_yet") : suite.seen_platforms.join(", ")}
            </span>
          </div>
        </section>
      ))}
    </div>
  );
}

export function SuitesCatalogue() {
  const { t } = useI18n();
  const list = useJSON<InstalledSuiteList>("api/suites");
  return (
    <>
      <PageHeader title={t("suites.installed_title")} subtitle={t("suites.installed_blurb")} />
      {list.error !== null && (
        <p role="alert" className="border-t border-border py-6 text-sm text-status-critical">
          {list.error}
        </p>
      )}
      {list.data === null && list.error === null && (
        <p className="border-t border-border py-6 text-sm text-muted-foreground">{t("common.loading")}</p>
      )}
      {list.data !== null && <SuitesTable suites={list.data.suites} />}
    </>
  );
}
