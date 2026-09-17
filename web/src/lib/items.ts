/**
 * The per-item substrate, as the viewer reads it.
 *
 * `core/observation.py::ItemResult` is the producer; every field beyond
 * `item_id` is optional there and therefore optional here.
 *
 * **The honesty invariant lives in these signatures.** `itemSummary` takes the
 * whole item list and no filter; `filterItems` returns rows and no aggregate.
 * No function in this module sees both, so a subset-derived ratio cannot be
 * written without first changing a signature — which is a thing a reviewer
 * sees. Selecting rows moves the observation; it never recomputes the verdict.
 */

export interface ItemScoreData {
  metric: string;
  value?: unknown;
  status?: string;
  reason?: string;
  error?: string;
}

export interface ItemResultData {
  item_id: string;
  group?: string;
  input?: unknown;
  output?: unknown;
  reference?: unknown;
  scores?: ItemScoreData[];
  usage?: Record<string, unknown>;
  attrs?: Record<string, unknown>;
}

export type ItemStatus = "ok" | "fail" | "skip" | "error";

/** Mirrors `core/observation.py::ITEM_SCORE_STATUSES`. */
export const ITEM_STATUSES: readonly ItemStatus[] = ["ok", "fail", "skip", "error"] as const;

const KNOWN = new Set<string>(ITEM_STATUSES);

/**
 * One score's state.
 *
 * An absent status is the dataclass default, `ok`. A status we do not know is
 * NOT: it means the producer emits a state this reader predates, and reporting
 * our own staleness as a pass is the failure mode worth avoiding.
 */
export function itemStatus(score: ItemScoreData): ItemStatus {
  const raw = score.status ?? "ok";
  return KNOWN.has(raw) ? (raw as ItemStatus) : "error";
}

/** The state of a whole item: its worst score. An item with no scores is `skip`. */
export function worstStatus(item: ItemResultData): ItemStatus {
  const scores = item.scores ?? [];
  if (scores.length === 0) return "skip";
  const states = scores.map(itemStatus);
  for (const candidate of ["error", "fail", "skip"] as const) {
    if (states.includes(candidate)) return candidate;
  }
  return "ok";
}

export interface ItemSummary {
  total: number;
  counts: Record<ItemStatus, number>;
}

/**
 * The four-state tally over the WHOLE run.
 *
 * Deliberately takes no filter: this line is printed above a table the reader
 * can narrow, and it must keep describing the run rather than the narrowing.
 */
export function itemSummary(items: ItemResultData[]): ItemSummary {
  const counts: Record<ItemStatus, number> = { ok: 0, fail: 0, skip: 0, error: 0 };
  for (const item of items) counts[worstStatus(item)] += 1;
  return { total: items.length, counts };
}

/** Metric ids present in any item's scores, in first-appearance order. */
export function itemMetricIds(items: ItemResultData[]): string[] {
  const seen: string[] = [];
  for (const item of items) {
    for (const score of item.scores ?? []) {
      if (!seen.includes(score.metric)) seen.push(score.metric);
    }
  }
  return seen;
}

/** Non-empty groups present, sorted. */
export function itemGroups(items: ItemResultData[]): string[] {
  const out = new Set<string>();
  for (const item of items) {
    if (item.group !== undefined && item.group !== "") out.add(item.group);
  }
  return [...out].sort();
}

/** Usage keys whose value is a finite number in at least one item, sorted. */
export function usageKeys(items: ItemResultData[]): string[] {
  const out = new Set<string>();
  for (const item of items) {
    for (const [key, value] of Object.entries(item.usage ?? {})) {
      if (typeof value === "number" && Number.isFinite(value)) out.add(key);
    }
  }
  return [...out].sort();
}

export interface MetricScope {
  metric: string;
  group: string | null;
}

const BY_GROUP = ".by_group.";

/**
 * Read a URL segment into a scope.
 *
 * Split at the FIRST `.by_group.`: everything after it is the group name, which
 * may itself contain dots (`aggregate_by_group` puts the raw group there).
 */
export function parseMetricSegment(segment: string): MetricScope {
  const at = segment.indexOf(BY_GROUP);
  if (at < 0) return { metric: segment, group: null };
  return { metric: segment.slice(0, at), group: segment.slice(at + BY_GROUP.length) };
}

export function formatMetricSegment(scope: MetricScope): string {
  return scope.group === null ? scope.metric : `${scope.metric}${BY_GROUP}${scope.group}`;
}

/**
 * The detail-surface target for a measurement key, or null when there is none.
 *
 * Null is the answer for every measurement derived from `usage` rather than
 * from an `ItemScore` — `avg_latency_ms`, `cost_usd`, `total_tokens`. Offering
 * a link there would promise evidence the table cannot show.
 *
 * The suite prefix is stripped only on an exact `${suiteId}.` match rather than
 * at the first dot: suite ids are dotless today, and a heuristic that assumes
 * so would start eating metric names the day one is not.
 */
export function detailTarget(key: string, suiteId: string, items: ItemResultData[]): string | null {
  if (items.length === 0) return null;
  const prefix = `${suiteId}.`;
  const tail = suiteId !== "" && key.startsWith(prefix) ? key.slice(prefix.length) : key;
  const scope = parseMetricSegment(tail);
  if (!itemMetricIds(items).includes(scope.metric)) return null;
  if (scope.group !== null && !itemGroups(items).includes(scope.group)) return null;
  return formatMetricSegment(scope);
}

export function scoreOf(item: ItemResultData, metric: string): ItemScoreData | null {
  return (item.scores ?? []).find((score) => score.metric === metric) ?? null;
}

export interface ItemFilter {
  query: string;
  scope: MetricScope | null;
  failingOnly: boolean;
}

/**
 * The rows to draw. Rows only — this returns no counts and no ratios.
 */
export function filterItems(items: ItemResultData[], filter: ItemFilter): ItemResultData[] {
  const needle = filter.query.trim().toLowerCase();
  return items.filter((item) => {
    if (needle !== "") {
      const hay = `${item.item_id} ${item.group ?? ""}`.toLowerCase();
      if (!hay.includes(needle)) return false;
    }
    if (filter.scope !== null) {
      if (scoreOf(item, filter.scope.metric) === null) return false;
      if (filter.scope.group !== null && item.group !== filter.scope.group) return false;
    }
    if (filter.failingOnly) {
      const state = worstStatus(item);
      if (state !== "fail" && state !== "error") return false;
    }
    return true;
  });
}

export type SortDir = "none" | "desc" | "asc";

/** `a-9` before `a-10`, which a plain string compare gets backwards. */
export function compareItemIds(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

const USAGE_PREFIX = "usage:";

/** The sortable number in `item` for `columnKey`, or null when absent. */
function sortValue(item: ItemResultData, columnKey: string): number | null {
  if (columnKey.startsWith(USAGE_PREFIX)) {
    const raw = (item.usage ?? {})[columnKey.slice(USAGE_PREFIX.length)];
    return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
  }
  const raw = scoreOf(item, columnKey)?.value;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

/**
 * A copy, sorted. `dir === "none"` is natural id order whatever the column.
 *
 * Items missing the sorted value go last in BOTH directions: absent is not
 * zero, and a missing latency that sorts as 0 reads as the fastest item.
 */
export function sortItems(
  items: ItemResultData[],
  columnKey: string | null,
  dir: SortDir,
): ItemResultData[] {
  const out = [...items];
  if (columnKey === null || dir === "none") {
    return out.sort((a, b) => compareItemIds(a.item_id, b.item_id));
  }
  const sign = dir === "desc" ? -1 : 1;
  return out.sort((a, b) => {
    const left = sortValue(a, columnKey);
    const right = sortValue(b, columnKey);
    if (left === null && right === null) return compareItemIds(a.item_id, b.item_id);
    if (left === null) return 1;
    if (right === null) return -1;
    if (left !== right) return (left - right) * sign;
    return compareItemIds(a.item_id, b.item_id);
  });
}

/** The next direction for a header click: none → desc → asc → none. */
export function nextSortDir(dir: SortDir): SortDir {
  return dir === "none" ? "desc" : dir === "desc" ? "asc" : "none";
}
