import { Header } from "@/components/Header";
import { Section, SectionBody } from "@/components/ui/section";
import { BoardView } from "@/features/board/BoardView";
import { LiveConsole } from "@/features/live/LiveConsole";
import { LiveRunView } from "@/features/live/LiveRunView";
import { ItemsView } from "@/features/items/ItemsView";
import { RecordView } from "@/features/record/RecordView";
import { RunsView } from "@/features/runs/RunsView";
import { SuiteView } from "@/features/suite/SuiteView";
import { TraceView } from "@/features/trace/TraceView";
import { I18nProvider, useI18n } from "@/i18n";
import { boardHref, useRoute } from "@/router";

function NotFound() {
  const { t } = useI18n();
  return (
    <Section>
      <SectionBody className="py-3 text-sm">
        <span className="font-medium">{t("common.error")}</span>
        <span className="ml-2 font-mono text-xs text-muted-foreground">{window.location.hash}</span>
        <a href={boardHref} className="ml-4 text-muted-foreground underline-offset-4 hover:underline">
          {t("common.back")}
        </a>
      </SectionBody>
    </Section>
  );
}

function Routed() {
  const route = useRoute();
  switch (route.name) {
    case "board":
      return <BoardView />;
    case "suite":
      return <SuiteView domain={route.domain} suiteId={route.suiteId} />;
    case "record":
      return <RecordView runId={route.runId} />;
    case "trace":
      // Keyed on the run, so navigating from one trace to another mounts a
      // new component instead of reusing the old one's state. `TraceView`
      // holds a `Selection` of absolute epoch seconds and track ids from the
      // trace it was built for; neither means anything in a different run,
      // and carrying them over rendered every pane as "No spans in this
      // selection" with nothing on screen to say why. A key resets all of it
      // at once — including the open span id and the active tab — and cannot
      // be forgotten by whatever state the view grows next, which a
      // reset-on-change effect inside the component could.
      return <TraceView key={route.runId} runId={route.runId} />;
    case "items":
      // Keyed on run + scope: the table holds sort, filter and paging state
      // that means nothing in another run, and a scope change is a different
      // question about the same run, so both deserve a fresh table.
      return (
        <ItemsView key={`${route.runId}:${route.metric ?? ""}`} runId={route.runId} metric={route.metric} />
      );
    case "runs":
      return <RunsView />;
    case "live":
      return <LiveConsole />;
    case "liveRun":
      return <LiveRunView runId={route.runId} />;
    case "notFound":
      return <NotFound />;
  }
}

export default function App() {
  return (
    <I18nProvider>
      <div className="min-h-screen bg-background text-foreground antialiased">
        <Header />
        <main className="mx-auto max-w-6xl px-6 py-6">
          <Routed />
        </main>
      </div>
    </I18nProvider>
  );
}
