/**
 * The app: a shell, five sections, and one route table.
 *
 * `legacyRedirect` runs before anything renders so an old link rewrites itself
 * in the address bar on arrival. The router resolves both spellings either
 * way — the rewrite is for the reader's benefit (so what they copy next is the
 * current spelling), not for correctness.
 */

import { useEffect } from "react";

import { Header } from "@/components/Header";
import { AppShell, SideRail, sectionOf, type RailItem } from "@/components/shell/AppShell";
import { Section, SectionBody } from "@/components/ui/section";
import {
  ConfigSection,
  ObserveSection,
  RunsSection,
  SuitesSection,
  TargetsSection,
} from "@/features/sections/Sections";
import { LiveRunView } from "@/features/live/LiveRunView";
import { RunView } from "@/features/run/RunView";
import { SuiteView } from "@/features/suite/SuiteView";
import { I18nProvider, useI18n } from "@/i18n";
import {
  legacyRedirect,
  observeHref,
  runsHref,
  suitesHref,
  targetsHref,
  useRoute,
  type Route,
} from "@/router";

function NotFound() {
  const { t } = useI18n();
  return (
    <Section>
      <SectionBody className="py-3 text-sm">
        <span className="font-medium">{t("common.error")}</span>
        <span className="ml-2 font-mono text-xs text-muted-foreground">{window.location.hash}</span>
        <a href={runsHref} className="ml-4 text-muted-foreground underline-offset-4 hover:underline">
          {t("common.back")}
        </a>
      </SectionBody>
    </Section>
  );
}

function Routed({ route }: { route: Route }) {
  switch (route.name) {
    case "suites":
      return <SuitesSection />;
    case "suite":
      return <SuiteView domain={route.domain} suiteId={route.suiteId} />;
    case "targets":
    case "targetNew":
    case "target":
      return <TargetsSection />;
    case "runs":
    case "runNew":
      return <RunsSection />;
    case "run":
      // Keyed on the run so a move between runs mounts a fresh page: the trace
      // tab holds a selection of absolute timestamps and track ids that mean
      // nothing in another run.
      return (
        <RunView key={route.runId} runId={route.runId} tab={route.tab} metric={route.metric} />
      );
    case "observe":
      return <ObserveSection />;
    case "observeRun":
      return <LiveRunView runId={route.runId} />;
    case "config":
      return <ConfigSection />;
    case "notFound":
      return <NotFound />;
  }
}

/** The section's rail: its views, and its create affordance when it has one. */
function Rail({ route }: { route: Route }) {
  const { t } = useI18n();
  const section = sectionOf(route);
  if (section === null) return null;

  // Each section declares its views here. `SideRail` drops itself when the
  // list does not yet earn its gutter, so a section with one view renders full
  // width until it grows a second one or a create button.
  const items: RailItem[] = [];
  switch (section) {
    case "suites":
      items.push({ href: suitesHref, label: t("shell.all"), active: route.name === "suites" });
      break;
    case "targets":
      items.push({ href: targetsHref, label: t("shell.all"), active: true });
      break;
    case "runs":
      items.push({ href: runsHref, label: t("shell.all"), active: route.name !== "run" });
      break;
    case "observe":
      items.push({ href: observeHref, label: t("shell.all"), active: route.name === "observe" });
      break;
    case "config":
      return null;
  }
  return <SideRail items={items} />;
}

function Body() {
  const route = useRoute();

  useEffect(() => {
    const next = legacyRedirect(window.location.hash);
    if (next !== null) window.location.replace(next);
  }, [route]);

  return (
    <AppShell route={route} topBar={<Header />} rail={<Rail route={route} />}>
      <Routed route={route} />
    </AppShell>
  );
}

export default function App() {
  return (
    <I18nProvider>
      <Body />
    </I18nProvider>
  );
}
