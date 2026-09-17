/**
 * One run, on four faces: overview, per-item detail, execution trace, config
 * snapshot.
 *
 * Before this, the same run was three sibling routes (`#/record/:id`,
 * `.../trace`, `.../items`) and a reader moving between them left the page
 * each time — the back button walked a history of pages rather than of
 * questions. They are one object asked four questions, so they are one page
 * with four tabs, and the tab rides in the query string so every face is still
 * a shareable link.
 *
 * **The items tab appears only when the run has items.** Most suites measure
 * an engine rather than examples and emit none; a tab that opens onto "this
 * benchmark produced no per-item detail" is a door to an empty room. The
 * cheap `?fields=` probe is what lets the tab bar know before the body does.
 */

import { useJSON, type RecordDetailData } from "@/api";
import { ErrorView, LoadingView } from "@/components/StateViews";
import { Crumbs } from "@/components/shell/Page";
import { EngineerPanel } from "@/features/record/EngineerPanel";
import { ItemsTable } from "@/features/items/ItemsView";
import { RecordBody } from "@/features/record/RecordView";
import { TraceView } from "@/features/trace/TraceView";
import { useI18n } from "@/i18n";
import { formatMetricSegment, parseMetricSegment } from "@/lib/items";
import { suiteLabel } from "@/lib/headline";
import { cn } from "@/lib/utils";
import { runHref, runsHref, type RunTab } from "@/router";

interface TabSpec {
  id: RunTab;
  labelKey: string;
}

const ALL_TABS: readonly TabSpec[] = [
  { id: "overview", labelKey: "run.tab_overview" },
  { id: "items", labelKey: "run.tab_items" },
  { id: "trace", labelKey: "run.tab_trace" },
  { id: "config", labelKey: "run.tab_config" },
] as const;

export function RunView({
  runId,
  tab,
  metric,
}: {
  runId: string;
  tab: RunTab;
  metric: string | null;
}) {
  const { t } = useI18n();
  const record = useJSON<RecordDetailData>(`api/record/${encodeURIComponent(runId)}`);

  if (record.error !== null) return <ErrorView message={record.error} />;
  if (record.data === null) return <LoadingView />;

  const data = record.data;
  const items = data.items ?? [];
  const suiteId = data.provenance?.suite_id ?? "";
  const tabs = ALL_TABS.filter((spec) => spec.id !== "items" || items.length > 0);
  // A link to the items tab on a run that has none lands on the overview
  // rather than on an empty tab that is not even in the bar.
  const active = tabs.some((spec) => spec.id === tab) ? tab : "overview";
  const scope = metric === null ? null : parseMetricSegment(metric);
  const scopedKey =
    scope === null
      ? null
      : suiteId === ""
        ? formatMetricSegment(scope)
        : `${suiteId}.${formatMetricSegment(scope)}`;
  const scopedEntry = scopedKey === null ? undefined : (data.measurements ?? {})[scopedKey];

  return (
    <div className="flex flex-col gap-4">
      <Crumbs
        items={[
          { label: t("section.runs"), href: runsHref },
          { label: suiteId !== "" ? suiteLabel(suiteId) : runId },
        ]}
      />

      <div role="tablist" className="flex items-center gap-1 border-b border-border">
        {tabs.map((spec) => (
          <a
            key={spec.id}
            role="tab"
            aria-selected={active === spec.id}
            href={runHref(runId, spec.id, spec.id === "items" ? metric : null)}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              active === spec.id
                ? "border-foreground font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {t(spec.labelKey)}
          </a>
        ))}
      </div>

      {active === "overview" && <RecordBody runId={runId} data={data} />}
      {active === "items" && (
        <ItemsTable
          items={items}
          suiteId={suiteId}
          scope={scope}
          measurement={
            scopedKey !== null && scopedEntry !== undefined
              ? { key: scopedKey, entry: scopedEntry }
              : null
          }
        />
      )}
      {active === "trace" && <TraceView key={runId} runId={runId} />}
      {active === "config" && <EngineerPanel data={data} forceOpen />}
    </div>
  );
}
