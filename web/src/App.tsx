/**
 * The app: a shell, five sections, and one route table.
 *
 * `legacyRedirect` runs before anything renders so an old link rewrites itself
 * in the address bar on arrival. The router resolves both spellings either
 * way — the rewrite is for the reader's benefit (so what they copy next is the
 * current spelling), not for correctness.
 */

import { useEffect } from "react";

import { useJSON, type Meta } from "@/api";
import { Header } from "@/components/Header";
import { AppShell, SideRail, sectionOf, type RailItem } from "@/components/shell/AppShell";
import { Section, SectionBody } from "@/components/ui/section";
import {
  ConfigSection,
  ObserveSection,
  PlatformsSection,
  RunsSection,
  SuitesSection,
  TargetsSection,
} from "@/features/sections/Sections";
import { TargetDetail } from "@/features/targets/TargetDetail";
import { TargetForm } from "@/features/targets/TargetForm";
import { LiveRunView } from "@/features/live/LiveRunView";
import { RunNewView } from "@/features/runs/RunNewView";
import { RunView } from "@/features/run/RunView";
import { SuiteView } from "@/features/suite/SuiteView";
import { I18nProvider, useI18n } from "@/i18n";
import {
  legacyRedirect,
  observeHref,
  platformsHref,
  runNewHref,
  runsHref,
  suitesHref,
  targetNewHref,
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
      return <TargetsSection />;
    case "platforms":
      return <PlatformsSection />;
    case "targetNew":
      return <TargetForm />;
    case "targetEdit":
      // Keyed on the name so moving between two targets' editors remounts:
      // the editor seeds its text once, and a stale seed would show the
      // previous target's YAML under the new one's name.
      return <TargetForm key={route.targetName} name={route.targetName} />;
    case "target":
      return <TargetDetail key={route.targetName} name={route.targetName} />;
    case "runs":
      return <RunsSection />;
    case "runNew":
      return <RunNewView />;
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
  // The create button exists only where the server would accept the write.
  // Offering it on a read-only viewer would trade a missing button for a 405
  // at the end of a filled-in form, which is the worse of the two.
  const meta = useJSON<Meta>("api/meta");
  const writable = meta.data?.write_enabled === true;
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
      items.push({
        href: targetsHref,
        label: t("target.title"),
        active: route.name !== "platforms",
      });
      items.push({
        href: platformsHref,
        label: t("platform.rail"),
        active: route.name === "platforms",
      });
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
  const primary = !writable
    ? null
    : section === "targets"
      ? { href: targetNewHref, label: t("target.new") }
      : section === "runs"
        ? { href: runNewHref, label: t("run.new_title") }
        : null;
  return <SideRail primary={primary} items={items} />;
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
