import { Header } from "@/components/Header";
import { Card, CardContent } from "@/components/ui/card";
import { I18nProvider, useI18n } from "@/i18n";
import { useRoute } from "@/router";
import { RecordDetail } from "@/views/RecordDetail";
import { RecordList } from "@/views/RecordList";
import { TraceView } from "@/views/TraceView";

function NotFound() {
  const { t } = useI18n();
  return (
    <Card>
      <CardContent className="px-4 py-3 text-sm">
        <span className="font-medium">{t('common.error')}</span>
        <span className="ml-2 font-mono text-xs text-muted-foreground">{window.location.hash}</span>
        <a href="#/" className="ml-4 text-muted-foreground underline-offset-4 hover:underline">
          {t('common.back')}
        </a>
      </CardContent>
    </Card>
  );
}

function Routed() {
  const route = useRoute();
  switch (route.name) {
    case "list":
      return <RecordList />;
    case "record":
      return <RecordDetail runId={route.runId} />;
    case "trace":
      return <TraceView runId={route.runId} />;
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
