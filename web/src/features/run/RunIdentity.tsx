/**
 * The identity bar: what this run IS, before any tab is pressed.
 *
 * Borrowed from DataBench's task header, which answers "what was run, against
 * what, for how long" in one line. The record page used to scatter the same
 * facts between a title, a mono line and a section three screens down.
 *
 * `create like this` is here rather than on the form because this is where a
 * reader decides they want another one — and it carries only what identifies
 * the work (benchmark, platform, target, params), never the batch size. A
 * repeat count is a decision about one run; carrying it forward silently turns
 * one "like this" into twenty runs nobody asked for.
 */

import type { RecordDetailData } from "@/api";
import { StatusPill } from "@/components/Glossed";
import { IdentityBar } from "@/components/shell/Page";
import { useI18n } from "@/i18n";
import { fmtDate, fmtDurMs, truncate } from "@/lib/format";
import { runLikeHref } from "@/router";

/** Wall-clock milliseconds between the two stamps, or null if unmeasurable. */
export function runDurationMs(started?: string, finished?: string): number | null {
  if (started === undefined || finished === undefined) return null;
  const from = new Date(started).getTime();
  const to = new Date(finished).getTime();
  if (Number.isNaN(from) || Number.isNaN(to) || to < from) return null;
  return to - from;
}

export function RunIdentity({
  data,
  runId,
  writable,
}: {
  data: RecordDetailData;
  runId: string;
  writable: boolean;
}) {
  const { t, locale } = useI18n();
  const duration = runDurationMs(data.run?.started_at, data.run?.finished_at);
  const digest = String((data.fingerprints ?? {}).record_digest ?? "");

  return (
    <div className="flex flex-col gap-3">
      <IdentityBar
        facts={[
          { label: t("run.status"), value: <StatusPill status={data.status ?? ""} /> },
          { label: t("run.benchmark"), value: data.provenance?.suite_id ?? "—" },
          { label: t("run.platform"), value: data.identity?.adapter ?? "—", mono: true },
          { label: t("run.domain"), value: data.identity?.domain ?? "—", mono: true },
          {
            label: t("run.started"),
            value: data.run?.started_at ? fmtDate(data.run.started_at, locale) : "—",
          },
          {
            // An absent end time is not an end time of now: a run still going,
            // or one that died without writing one, has no duration to show.
            label: t("run.duration"),
            value: duration === null ? "—" : fmtDurMs(duration),
            mono: true,
          },
          {
            label: t("run.digest"),
            value: digest === "" ? "—" : truncate(digest.replace("sha256:", ""), 12),
            mono: true,
          },
        ]}
      />
      {writable && (
        <div className="flex items-center gap-3">
          <a
            href={runLikeHref(runId)}
            className="rounded-sm border border-border px-3 py-1.5 text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {t("run.like_this")}
          </a>
          <span className="text-xs text-muted-foreground">{t("run.like_this_hint")}</span>
        </div>
      )}
    </div>
  );
}
