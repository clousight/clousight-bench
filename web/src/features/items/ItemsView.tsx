/**
 * The detail surface: the per-item evidence a measurement was aggregated from.
 *
 * **What this page is for.** `gsm8k.accuracy = 0.667` is, on the record page, a
 * number a reader cannot interrogate. This is where they ask "which two of the
 * three, and why". The record already ships `items[]` over
 * `/api/record/:id` — nothing new is fetched here, the substrate was simply
 * never rendered.
 *
 * **The honesty invariant, and how it is kept.** Narrowing this table moves the
 * observation; it never recomputes the verdict. The tally above the table is
 * `itemSummary(items)` — the WHOLE list, computed before any filter exists —
 * and the rows are `filterItems(...)`, which returns rows and no aggregate.
 * The two values are never combined, and neither function's signature can see
 * the other's concern, so "100% fail" over two filtered rows is not a thing a
 * careless edit can produce. `tests/test_viewer_frontend.py` ratchets both the
 * signature and the absence of division in this directory.
 *
 * **Scoping is a link, not a pivot.** Arriving from a measurement pins that
 * metric (and, for a `by_group` measurement, that group) and prints the
 * record's own value for it. Unscoped, no measurement is shown at all: picking
 * one to display would imply the table beneath it is what produced it.
 */

import { useMemo, useState } from "react";

import { useJSON, type MeasurementEntry, type RecordDetailData } from "@/api";
import { Glossed, MetricValue, StatusPill } from "@/components/Glossed";
import { ErrorView, LoadingView } from "@/components/StateViews";
import { Input } from "@/components/ui/input";
import {
  Section,
  SectionBody,
  SectionHead,
  SectionNote,
  SectionTitle,
} from "@/components/ui/section";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ItemFields, hasFields } from "@/features/items/ItemFields";
import { useI18n } from "@/i18n";
import {
  ITEM_STATUSES,
  filterItems,
  formatMetricSegment,
  itemGroups,
  itemMetricIds,
  itemSummary,
  nextSortDir,
  parseMetricSegment,
  scoreOf,
  sortItems,
  usageKeys,
  worstStatus,
  type ItemResultData,
  type ItemStatus,
  type MetricScope,
  type SortDir,
} from "@/lib/items";
import { formatMetric, lookupMetric, metricBlurb, metricLabel } from "@/lib/glossary";
import { suiteLabel } from "@/lib/headline";
import { cn } from "@/lib/utils";
import { recordHref, traceHref } from "@/router";

const PAGE = 50;

/** One status, as colour AND icon-free word — colour never carries it alone. */
const STATUS_STYLES: Record<ItemStatus, string> = {
  ok: "bg-status-good/12 text-status-good",
  fail: "bg-status-critical/12 text-status-critical",
  skip: "bg-muted text-muted-foreground",
  error: "bg-status-warning/15 text-status-serious",
};

/**
 * `fail` and `error` are deliberately different shapes as well as different
 * hues: the first is a property of the thing under test, the second is a bug in
 * our own scorer, and a reader who reads them as one number is being misled
 * about whose fault the run's result is.
 */
function StatusChip({ status }: { status: ItemStatus }) {
  const { t } = useI18n();
  return (
    <span
      title={t(`items.status.${status}_blurb`)}
      className={cn(
        "inline-flex items-center rounded-sm px-1 py-0 font-mono text-[10px] uppercase tracking-[0.08em]",
        STATUS_STYLES[status],
        status === "error" && "ring-1 ring-status-warning/50",
      )}
    >
      {status === "error" ? "! " : ""}
      {t(`items.status.${status}`)}
    </span>
  );
}

interface Column {
  /** Sort key: a bare metric id, or `usage:<key>`. */
  key: string;
  label: string;
  blurb: string | null;
  raw: string;
  /**
   * The key the glossary is looked up with — `gsm8k.accuracy`, not `accuracy`.
   *
   * Header and cell MUST share it. They did not at first, and the column headed
   * "准确率" printed `1` where the record page printed `100 %` for the same
   * number: the header resolved the suite-qualified key and found the ratio
   * spec, the cell resolved the bare metric id and fell through to raw. One
   * field, read by both, is what makes that divergence unrepresentable.
   */
  specKey: string;
  kind: "metric" | "usage";
}

export interface ItemsTableProps {
  items: ItemResultData[];
  suiteId: string;
  /** Pinned metric (and group) when the reader arrived from a measurement. */
  scope: MetricScope | null;
  /** The record's own value for the scoped measurement — shown verbatim, never
   * recomputed from the rows below. Null when the view is unscoped. */
  measurement: { key: string; entry: MeasurementEntry } | null;
  /** Test seam: start with the failing-only switch already on. */
  initialFailingOnly?: boolean;
}

export function ItemsTable({
  items,
  suiteId,
  scope,
  measurement,
  initialFailingOnly = false,
}: ItemsTableProps) {
  const { t, locale } = useI18n();
  const [query, setQuery] = useState("");
  const [failingOnly, setFailingOnly] = useState(initialFailingOnly);
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>("none");
  const [shown, setShown] = useState(PAGE);
  const [openId, setOpenId] = useState<string | null>(null);

  // The whole run, before anything narrows it. Kept out of the memo that
  // depends on the filter so the two can never be accidentally fused.
  const summary = useMemo(() => itemSummary(items), [items]);
  const showGroup = useMemo(() => itemGroups(items).length > 0, [items]);

  const columns = useMemo<Column[]>(() => {
    const metricCols: Column[] = itemMetricIds(items)
      .filter((metric) => scope === null || metric === scope.metric)
      .map((metric) => {
        const specKey = suiteId === "" ? metric : `${suiteId}.${metric}`;
        const spec = lookupMetric(specKey);
        return {
          key: metric,
          label: metricLabel(spec, locale),
          blurb: metricBlurb(spec, locale),
          raw: metric,
          specKey,
          kind: "metric" as const,
        };
      });
    const usageCols: Column[] = usageKeys(items).map((key) => {
      const spec = lookupMetric(key);
      return {
        key: `usage:${key}`,
        label: metricLabel(spec, locale),
        blurb: metricBlurb(spec, locale),
        raw: key,
        specKey: key,
        kind: "usage" as const,
      };
    });
    return [...metricCols, ...usageCols];
  }, [items, scope, suiteId, locale]);

  const rows = useMemo(
    () => sortItems(filterItems(items, { query, scope, failingOnly }), sortKey, sortDir),
    [items, query, scope, failingOnly, sortKey, sortDir],
  );

  const page = rows.slice(0, shown);
  const remaining = rows.length - page.length;
  const leadingCols = 1 + (showGroup ? 1 : 0);
  const totalCols = leadingCols + columns.length + 1;

  function onSort(key: string) {
    if (sortKey !== key) {
      setSortKey(key);
      setSortDir("desc");
      return;
    }
    const next = nextSortDir(sortDir);
    setSortDir(next);
    if (next === "none") setSortKey(null);
  }

  function ariaSort(key: string): "none" | "ascending" | "descending" {
    if (sortKey !== key || sortDir === "none") return "none";
    return sortDir === "asc" ? "ascending" : "descending";
  }

  return (
    <div className="flex flex-col gap-4">
      <Section>
        <SectionHead>
          <SectionTitle>{t("items.title")}</SectionTitle>
        </SectionHead>
        <SectionBody className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className="font-mono text-sm tabular-nums">
              {summary.total} <span className="text-muted-foreground">{t("items.count")}</span>
            </span>
            {ITEM_STATUSES.map((status) => (
              <span key={status} className="flex items-center gap-1.5">
                <span className="font-mono text-sm tabular-nums">{summary.counts[status]}</span>
                <StatusChip status={status} />
              </span>
            ))}
          </div>
          {measurement !== null && (
            <div className="flex flex-col gap-1 border-t pt-3">
              <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
                {t("items.evidence_for")}
              </span>
              <MetricValue
                measurementKey={measurement.key}
                value={measurement.entry.value}
                unit={measurement.entry.unit}
                reproducibility={measurement.entry.reproducibility_class}
                official={measurement.entry.official}
              />
            </div>
          )}
        </SectionBody>
      </Section>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Input
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setShown(PAGE);
          }}
          placeholder={t("items.filter_hint")}
          aria-label={t("items.filter_hint")}
          className="w-72 max-w-full"
        />
        <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground focus-within:text-foreground">
          <input
            type="checkbox"
            checked={failingOnly}
            onChange={(event) => {
              setFailingOnly(event.target.checked);
              setShown(PAGE);
            }}
            className="size-3.5 accent-status-critical focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          {t("items.failing_only")}
        </label>
        <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">
          {t("items.matching").replace("{n}", String(rows.length)).replace("{total}", String(summary.total))}
        </span>
      </div>

      {rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("items.no_match")}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("items.col_item")}</TableHead>
              {showGroup && <TableHead>{t("items.col_group")}</TableHead>}
              {columns.map((column) => (
                <TableHead
                  key={column.key}
                  aria-sort={ariaSort(column.key)}
                  // `h-9` + `leading-none` is sized for one line; this header is
                  // two (label over raw key, the house's two-layer rule) and the
                  // second line printed straight through the first. Height comes
                  // from the content here, and the leading is restored.
                  className="h-auto py-2 align-bottom leading-normal"
                >
                  <button
                    type="button"
                    onClick={() => onSort(column.key)}
                    className="flex flex-col items-start gap-0.5 rounded-sm text-left uppercase tracking-[0.1em] hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <Glossed blurb={column.blurb}>
                      <span>{column.label}</span>
                    </Glossed>
                    <span className="font-mono text-[9px] normal-case tracking-normal opacity-70">
                      {column.raw}
                      {ariaSort(column.key) === "descending" && " ↓"}
                      {ariaSort(column.key) === "ascending" && " ↑"}
                    </span>
                  </button>
                </TableHead>
              ))}
              <TableHead className="w-8" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {page.map((item) => {
              const expandable = hasFields(item);
              const open = openId === item.item_id;
              return [
                <TableRow key={item.item_id}>
                  <TableCell className="font-mono text-xs">
                    <span className="flex items-center gap-2">
                      {item.item_id}
                      <StatusChip status={worstStatus(item)} />
                    </span>
                  </TableCell>
                  {showGroup && (
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      {item.group ?? "—"}
                    </TableCell>
                  )}
                  {columns.map((column) => (
                    <TableCell key={column.key} className="font-mono text-xs tabular-nums">
                      <ItemCell item={item} column={column} />
                    </TableCell>
                  ))}
                  <TableCell className="text-right">
                    {expandable && (
                      <button
                        type="button"
                        onClick={() => setOpenId(open ? null : item.item_id)}
                        aria-expanded={open}
                        aria-label={open ? t("items.collapse") : t("items.expand")}
                        title={open ? t("items.collapse") : t("items.expand")}
                        className="rounded-sm px-1 font-mono text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        {open ? "▾" : "▸"}
                      </button>
                    )}
                  </TableCell>
                </TableRow>,
                open ? (
                  <TableRow key={`${item.item_id}-fields`}>
                    <TableCell colSpan={totalCols} className="bg-muted/30">
                      <ItemFields item={item} />
                    </TableCell>
                  </TableRow>
                ) : null,
              ];
            })}
          </TableBody>
        </Table>
      )}

      {remaining > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setShown(shown + PAGE)}
            className="rounded-sm border px-2 py-1 font-mono text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {t("items.more").replace("{n}", String(Math.min(PAGE, remaining)))}
          </button>
        </div>
      )}
    </div>
  );
}

/** One cell: a score's value with its state, or a usage number. */
function ItemCell({ item, column }: { item: ItemResultData; column: Column }) {
  if (column.kind === "usage") {
    const raw = (item.usage ?? {})[column.raw];
    if (typeof raw !== "number" || !Number.isFinite(raw)) return <span>—</span>;
    const spec = lookupMetric(column.specKey);
    const formatted = formatMetric(raw, spec.format);
    return (
      <span>
        {formatted.text}
        {formatted.unit !== "" && <span className="ml-1 text-muted-foreground">{formatted.unit}</span>}
      </span>
    );
  }
  const score = scoreOf(item, column.key);
  if (score === null) return <span>—</span>;
  const spec = lookupMetric(column.specKey);
  const formatted =
    typeof score.value === "number"
      ? formatMetric(score.value, spec.format)
      : { text: score.value === undefined ? "—" : String(score.value), unit: "" };
  return (
    <span className="flex items-center gap-1.5">
      {formatted.text}
      {formatted.unit !== "" && <span className="text-muted-foreground">{formatted.unit}</span>}
    </span>
  );
}

export function ItemsView({ runId, metric }: { runId: string; metric: string | null }) {
  const { t } = useI18n();
  const record = useJSON<RecordDetailData>(`api/record/${encodeURIComponent(runId)}`);

  if (record.error !== null) return <ErrorView message={record.error} />;
  if (record.data === null) return <LoadingView />;

  const data = record.data;
  const items = data.items ?? [];
  const suiteId = data.provenance?.suite_id ?? "";
  const scope = metric === null ? null : parseMetricSegment(metric);

  // The record's own number for the scoped metric, looked up by the key the
  // producer wrote — not reassembled from the rows on this page.
  const measurements = data.measurements ?? {};
  const scopedKey =
    scope === null ? null : suiteId === "" ? formatMetricSegment(scope) : `${suiteId}.${formatMetricSegment(scope)}`;
  const scopedEntry = scopedKey === null ? undefined : measurements[scopedKey];
  const measurement =
    scopedKey !== null && scopedEntry !== undefined ? { key: scopedKey, entry: scopedEntry } : null;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <a
          href={recordHref(runId)}
          className="text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >
          ← {t("items.back")}
        </a>
        <a
          href={traceHref(runId)}
          className="ml-auto text-sm underline-offset-4 hover:underline"
        >
          {t("record.view_trace")} →
        </a>
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="text-lg font-semibold tracking-tight">{t("items.title")}</h1>
        <span className="font-mono text-xs text-muted-foreground">{runId}</span>
        {suiteId !== "" && <span className="text-sm text-muted-foreground">{suiteLabel(suiteId)}</span>}
        {data.status !== undefined && <StatusPill status={data.status} />}
      </div>

      {items.length === 0 ? (
        <Section>
          <SectionHead>
            <SectionTitle>{t("items.title")}</SectionTitle>
            <SectionNote>{t("items.none_blurb")}</SectionNote>
          </SectionHead>
          <SectionBody>
            <p className="text-sm">{t("items.none")}</p>
          </SectionBody>
        </Section>
      ) : (
        <ItemsTable items={items} suiteId={suiteId} scope={scope} measurement={measurement} />
      )}
    </div>
  );
}
