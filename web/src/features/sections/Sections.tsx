/**
 * The five sections' landing pages.
 *
 * Each one is thin on purpose: a crumb, a header that says what the noun IS,
 * and then the existing view that already answers it. The value of this file
 * is not its code — it is that every section now has the same shape, so a
 * reader who has used one has used all five.
 *
 * Config is still an honest placeholder: it states what the section will hold
 * and what to use meanwhile, rather than rendering an empty table that implies
 * the data is missing. "Not built yet" and "nothing here" are different
 * sentences and the reader is owed the right one.
 */

import { BoardView } from "@/features/board/BoardView";
import { LiveConsole } from "@/features/live/LiveConsole";
import { PlatformsView } from "@/features/platforms/PlatformsView";
import { RunsView } from "@/features/runs/RunsView";
import { EmptyState, PageHeader } from "@/components/shell/Page";
import { useI18n } from "@/i18n";

/** Suites — the catalogue of what can be measured. */
export function SuitesSection() {
  const { t } = useI18n();
  return (
    <>
      <PageHeader title={t("suites.title")} subtitle={t("section.suites_blurb")} />
      <BoardView />
    </>
  );
}

/** Runs — every run and its verdict. */
export function RunsSection() {
  const { t } = useI18n();
  return (
    <>
      <PageHeader title={t("runs.title")} subtitle={t("section.runs_blurb")} />
      <RunsView />
    </>
  );
}

/** Observe — what is in flight. */
export function ObserveSection() {
  const { t } = useI18n();
  return (
    <>
      <PageHeader title={t("observe.title")} subtitle={t("section.observe_blurb")} />
      <LiveConsole />
    </>
  );
}

/** Platforms — the managed cloud products this build can measure. */
export function TargetsSection() {
  const { t } = useI18n();
  return (
    <>
      <PageHeader title={t("targets.title")} subtitle={t("section.targets_blurb")} />
      <PlatformsView />
    </>
  );
}

/** Config — placeholder until the vocabulary plane lands. */
export function ConfigSection() {
  const { t } = useI18n();
  return (
    <>
      <PageHeader title={t("config.title")} subtitle={t("section.config_blurb")} />
      <EmptyState title={t("config.pending")} blurb={t("config.pending_blurb")} />
    </>
  );
}
