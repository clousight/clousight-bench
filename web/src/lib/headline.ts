/**
 * Choosing which numbers lead.
 *
 * A record can carry a dozen measurements; a card has room for two. The choice
 * is made from the written specs (`headline: true`) rather than by taking the
 * first few keys, so the same suite always leads with the same numbers and two
 * platforms stay comparable at a glance.
 */

import { lookupMetric, type ResolvedMetric } from "@/lib/glossary";

export interface HeadlineMetric {
  key: string;
  value: number;
  spec: ResolvedMetric;
}

/**
 * The headline measurements of a run, in the order the specs declare them.
 *
 * When a suite declares none — an unrecognised or in-development suite — this
 * falls back to the first few keys in sorted order rather than showing nothing.
 * A fallback pick is honest about being one: `spec.known` is false, and
 * `MetricValue` marks it on screen.
 */
export function headlineMetrics(
  measurements: Record<string, number>,
  limit = 3,
): HeadlineMetric[] {
  const entries = Object.entries(measurements).filter(
    ([, value]) => typeof value === "number" && Number.isFinite(value),
  );
  const resolved = entries.map(([key, value]) => ({ key, value, spec: lookupMetric(key) }));
  const declared = resolved.filter((entry) => entry.spec.headline === true);
  if (declared.length > 0) return declared.slice(0, limit);
  return resolved.sort((a, b) => a.key.localeCompare(b.key)).slice(0, limit);
}

/**
 * Order a set of measurement keys for a comparison table: headline metrics
 * first (in spec order), then everything else alphabetically.
 */
export function orderMetricKeys(keys: string[]): string[] {
  const withSpec = keys.map((key) => ({ key, spec: lookupMetric(key) }));
  const headline = withSpec.filter((entry) => entry.spec.headline === true).map((entry) => entry.key);
  const rest = withSpec
    .filter((entry) => entry.spec.headline !== true)
    .map((entry) => entry.key)
    .sort((a, b) => a.localeCompare(b));
  return [...headline, ...rest];
}

/** A domain id → the words a reader uses for it. Unknown ids pass through. */
export const DOMAIN_LABELS: Record<string, { zh: string; en: string }> = {
  "data-warehouse": { zh: "数据仓库", en: "Data warehouse" },
  "key-value": { zh: "键值存储", en: "Key-value store" },
  "transactional-db": { zh: "事务数据库", en: "Transactional database" },
  "agent-runtime": { zh: "Agent 运行时", en: "Agent runtime" },
  llm: { zh: "大模型", en: "LLM" },
};

/** A suite id → its conventional display name. Unknown ids pass through. */
export const SUITE_LABELS: Record<string, string> = {
  "tpc-h": "TPC-H",
  "tpc-ds": "TPC-DS",
  "tpc-c": "TPC-C",
  ycsb: "YCSB",
  "swe-bench": "SWE-bench",
  "human-eval": "HumanEval",
  gsm8k: "GSM8K",
  mmlu: "MMLU",
};

export function suiteLabel(suiteId: string): string {
  return SUITE_LABELS[suiteId] ?? suiteId;
}
