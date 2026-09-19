/**
 * The five sections' landing pages.
 *
 * Each one is thin on purpose: a crumb, a header that says what the noun IS,
 * and then the existing view that already answers it. The value of this file
 * is not its code — it is that every section now has the same shape, so a
 * reader who has used one has used all five.
 *
 * The platforms section holds two nouns: the adapter catalogue (what this
 * build can reach at all) and the targets configured against them (where one
 * particular instance is). They are one section because choosing a platform
 * and configuring an instance of it is one errand.
 *
 * Every section now has content. The two that used to be honest placeholders
 * — Targets and Config — are real pages, and the placeholder strings they used
 * are gone rather than left lying around to be rendered by accident.
 */

import { BoardView } from "@/features/board/BoardView";
import { LiveConsole } from "@/features/live/LiveConsole";
import { ConfigView } from "@/features/config/ConfigView";
import { PlatformsView } from "@/features/platforms/PlatformsView";
import { SuitesCatalogue } from "@/features/suites/SuitesCatalogue";
import { TargetsView } from "@/features/targets/TargetsView";
import { RunsView } from "@/features/runs/RunsView";
import { PageHeader } from "@/components/shell/Page";
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

/** Targets — the configured instances, this section's actionable noun. */
export function TargetsSection() {
  return <TargetsView />;
}

/** Platforms — the managed cloud products this build has an adapter for. */
export function PlatformsSection() {
  const { t } = useI18n();
  return (
    <>
      <PageHeader title={t("targets.title")} subtitle={t("section.targets_blurb")} />
      <PlatformsView />
    </>
  );
}

/** Suites, the other face: what this build can measure at all. */
export function SuitesInstalledSection() {
  return <SuitesCatalogue />;
}

/** Config — the vocabulary and the machine, both read-only. */
export function ConfigSection() {
  const { t } = useI18n();
  return (
    <>
      <PageHeader title={t("config.title")} subtitle={t("section.config_blurb")} />
      <ConfigView />
    </>
  );
}
