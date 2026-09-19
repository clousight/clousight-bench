/**
 * Targets: the configured instances a benchmark can be pointed at.
 *
 * A target is `configs/<name>.yaml` — an adapter's instance configuration.
 * It is the one thing in this tool the console may write, and the only page
 * whose buttons depend on how the server was started: `csbench serve` is
 * read-only, `--allow-write` is not. The buttons follow `meta.write_enabled`
 * rather than hoping, because an action that 405s is worse than one that was
 * never offered.
 */

import { useState } from "react";

import { useJSON, type Meta, type TargetListData, type TargetSummary } from "@/api";
import { DataTable, FilterBar, FilterField, type Column } from "@/components/shell/DataTable";
import { PageHeader } from "@/components/shell/Page";
import { useI18n } from "@/i18n";
import { targetNewHref, targetHref } from "@/router";

/** Whether a row survives the filter box. Empty query keeps everything. */
export function matchesFilter(target: TargetSummary, query: string): boolean {
  const needle = query.trim().toLowerCase();
  if (needle === "") return true;
  return [target.name, target.provider, target.mode, target.region]
    .join(" ")
    .toLowerCase()
    .includes(needle);
}

/** Epoch seconds as a local date-time, or "" when the server sent nothing. */
function modified(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "";
  return new Date(seconds * 1000).toLocaleString();
}

export function TargetsTable({
  targets,
  filter,
  writable,
  loading,
  error,
}: {
  targets: TargetSummary[];
  filter: string;
  writable: boolean;
  loading?: boolean;
  error?: string | null;
}) {
  const { t } = useI18n();
  const rows = targets.filter((target) => matchesFilter(target, filter));
  // Two different empty tables. "There are none" is a teaching moment and
  // offers the create button; "none match" is a typo and offers nothing but
  // the news that the filter is what did it.
  const filtered = targets.length > 0 && rows.length === 0;
  const empty = filtered
    ? { title: t("target.none_matching"), blurb: t("target.none_matching_blurb") }
    : {
        title: t("target.none"),
        blurb: t("target.none_blurb"),
        action: writable ? (
          <a href={targetNewHref} className="text-sm underline underline-offset-4">
            {t("target.new")}
          </a>
        ) : undefined,
      };

  const columns: Column<TargetSummary>[] = [
    {
      key: "name",
      header: t("target.col_name"),
      cell: (row) => <span className="font-mono text-sm">{row.name}</span>,
    },
    {
      key: "provider",
      header: t("target.col_provider"),
      cell: (row) => row.provider || "—",
    },
    {
      key: "mode",
      header: t("target.col_mode"),
      cell: (row) => row.mode || "—",
    },
    {
      key: "region",
      header: t("target.col_region"),
      cell: (row) => row.region || "—",
    },
    {
      key: "state",
      header: t("target.col_state"),
      // A file that will not parse keeps its row and carries its reason. It
      // is still a file on disk; hiding it would read as having been deleted.
      cell: (row) =>
        row.error === "" ? (
          <span className="text-muted-foreground">{t("target.ok")}</span>
        ) : (
          <span className="text-status-critical">{row.error}</span>
        ),
    },
    {
      key: "modified",
      header: t("target.col_modified"),
      numeric: true,
      cell: (row) => <span className="font-mono text-xs tabular-nums">{modified(row.modified)}</span>,
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(row) => row.name}
      href={(row) => targetHref(row.name)}
      loading={loading}
      error={error}
      empty={empty}
    />
  );
}

export function TargetsView() {
  const { t } = useI18n();
  const [filter, setFilter] = useState("");
  const list = useJSON<TargetListData>("api/targets");
  const meta = useJSON<Meta>("api/meta");
  const writable = meta.data?.write_enabled === true;
  const targets = list.data?.targets ?? [];

  return (
    <>
      <PageHeader title={t("target.title")} subtitle={t("target.blurb")} />
      {!writable && meta.data !== null && (
        // Not an error — a statement of what this server is. Without it the
        // absence of every button looks like a bug.
        <p className="pb-3 text-xs text-muted-foreground">{t("target.readonly_notice")}</p>
      )}
      <FilterBar count={<span className="font-mono text-xs tabular-nums">{targets.length}</span>}>
        <FilterField label={t("common.filter")}>
          <input
            type="search"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder={t("common.filter_placeholder")}
            className="w-56 rounded-sm border border-border bg-background px-2 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </FilterField>
      </FilterBar>
      <TargetsTable
        targets={targets}
        filter={filter}
        writable={writable}
        loading={list.data === null && list.error === null}
        error={list.error}
      />
    </>
  );
}
