/**
 * The de-jargon layer: one place that owns every human-facing name.
 *
 * A record speaks the harness's language — `tpc-h.geomean_latency_ms`,
 * `reproducibility_class`, `SEAL`, `PUBLISH: skipped`. Those are internal
 * states, and putting them on screen as content is what made the old viewer
 * unreadable. This module maps them to a sentence a person can act on.
 *
 * Two rules govern everything here:
 *
 * 1. **Two layers, never one.** The human label leads; the raw key stays
 *    visible underneath in mono. Nothing becomes unauditable, and an engineer
 *    can still match a field name at a glance. The UI enforces this by
 *    rendering `label` and `key` together — see components/MetricValue.
 *
 * 2. **Never invent.** An unknown key degrades to a prettified version of
 *    itself with no blurb and `betterIs: "none"`. We infer a format from the
 *    declared unit and, cautiously, from a suffix — but we never guess which
 *    direction is better, because guessing that wrong turns a regression into
 *    a celebration.
 */

import type { Locale } from "@/i18n";

/** How a value should be rendered. Drives units, precision and suffixes. */
export type MetricFormat =
  | "duration_ms"
  | "duration_us"
  | "duration_s"
  | "count"
  | "ratio"
  | "pass"
  | "throughput"
  | "usd"
  | "tokens"
  | "raw";

/** Which direction is an improvement. "none" = we do not know, so we say nothing. */
export type BetterIs = "lower" | "higher" | "none";

export interface Bilingual {
  zh: string;
  en: string;
}

export interface MetricSpec {
  /** Exact measurement key, or a prefix pattern ending in `*`. */
  key: string;
  label: Bilingual;
  /** One sentence: what it means and why a reader should care. */
  blurb?: Bilingual;
  format: MetricFormat;
  betterIs: BetterIs;
  /** Promoted onto the board and the comparison table's leading columns. */
  headline?: boolean;
}

export interface ResolvedMetric extends MetricSpec {
  /** False when this came from the fallback rather than a written spec. */
  known: boolean;
}

/** Terse constructor for a bilingual pair — this file is mostly these. */
const bi = (zh: string, en: string): Bilingual => ({ zh, en });

/**
 * Written specs, most specific first. Ordering matters only among patterns:
 * `lookupMetric` tries exact matches before prefixes, and the first matching
 * prefix wins, so a narrow pattern must precede a broad one.
 */
export const METRIC_SPECS: MetricSpec[] = [
  // ---- TPC-H / TPC-DS (data warehouse) ---------------------------------
  {
    key: "tpc-h.queries_passed",
    label: bi("正确性", "Correctness"),
    blurb: bi(
      "有多少条查询的结果和固定的 SF1 参考答案一致。1 表示全部通过。",
      "Share of queries whose result matches the pinned SF1 reference. 1 means all passed.",
    ),
    format: "ratio",
    betterIs: "higher",
    headline: true,
  },
  {
    key: "tpc-ds.queries_passed",
    label: bi("正确性", "Correctness"),
    blurb: bi(
      "有多少条查询的结果和固定的 SF1 参考答案一致。1 表示全部通过。",
      "Share of queries whose result matches the pinned SF1 reference. 1 means all passed.",
    ),
    format: "ratio",
    betterIs: "higher",
    headline: true,
  },
  {
    key: "tpc-h.geomean_latency_ms",
    label: bi("典型查询耗时", "Typical query time"),
    blurb: bi(
      "每条查询耗时的几何平均。比算术平均更能代表“一般一条查询要多久”，因为它不会被一条特别慢的查询带偏。",
      "Geometric mean of per-query wall time — a better stand-in for a typical query than the arithmetic mean, because one slow outlier cannot drag it.",
    ),
    format: "duration_ms",
    betterIs: "lower",
    headline: true,
  },
  {
    key: "tpc-ds.geomean_latency_ms",
    label: bi("典型查询耗时", "Typical query time"),
    blurb: bi(
      "每条查询耗时的几何平均。比算术平均更能代表“一般一条查询要多久”。",
      "Geometric mean of per-query wall time — a better stand-in for a typical query than the arithmetic mean.",
    ),
    format: "duration_ms",
    betterIs: "lower",
    headline: true,
  },
  {
    key: "tpc-h.total_runtime_ms",
    label: bi("查询集总耗时", "Total query-set time"),
    format: "duration_ms",
    betterIs: "lower",
  },
  {
    key: "tpc-ds.total_runtime_ms",
    label: bi("查询集总耗时", "Total query-set time"),
    format: "duration_ms",
    betterIs: "lower",
  },
  {
    key: "tpc-h.load_time_s",
    label: bi("数据导入耗时", "Data load time"),
    blurb: bi("生成并载入数据集所花的时间，计入官方 QphH 公式。", "Time to generate and load the dataset; it feeds the official QphH formula."),
    format: "duration_s",
    betterIs: "lower",
  },
  {
    key: "tpc-h.qphh_at_size",
    label: bi("QphH 综合分", "QphH composite"),
    blurb: bi(
      "官方 TPC-H 综合指标（单流 Power 与多流 Throughput 的几何平均）。按官方公式计算，但未经 TPC 审计。",
      "The official TPC-H composite (geometric mean of single-stream Power and multi-stream Throughput). Computed by the official formula, but not TPC-audited.",
    ),
    format: "count",
    betterIs: "higher",
    headline: true,
  },
  {
    key: "tpc-h.power_at_size",
    label: bi("单流性能 Power", "Power (single stream)"),
    format: "count",
    betterIs: "higher",
  },
  {
    key: "tpc-h.throughput_at_size",
    label: bi("多流吞吐 Throughput", "Throughput (multi-stream)"),
    format: "count",
    betterIs: "higher",
  },
  {
    key: "tpc-ds.qphds_at_size",
    label: bi("QphDS 综合分", "QphDS composite"),
    blurb: bi(
      "官方 TPC-DS 综合指标。按官方公式计算，但未经 TPC 审计。",
      "The official TPC-DS composite. Computed by the official formula, but not TPC-audited.",
    ),
    format: "count",
    betterIs: "higher",
    headline: true,
  },
  {
    key: "tpc-h.acid_*",
    label: bi("ACID 检查", "ACID check"),
    blurb: bi("对应的 ACID 性质是否通过检查。1 = 通过。", "Whether that ACID property passed its probe. 1 = pass."),
    format: "pass",
    betterIs: "higher",
  },
  {
    key: "tpc-ds.acid_*",
    label: bi("ACID 检查", "ACID check"),
    format: "pass",
    betterIs: "higher",
  },

  // ---- YCSB (key-value) ------------------------------------------------
  {
    key: "ycsb.throughput_ops",
    label: bi("吞吐", "Throughput"),
    blurb: bi("每秒完成的操作数，越高越好。", "Operations completed per second."),
    format: "throughput",
    betterIs: "higher",
    headline: true,
  },
  {
    key: "ycsb.read_p99_us",
    label: bi("读延迟 P99", "Read latency P99"),
    blurb: bi(
      "99% 的读操作比这个值快。P99 反映的是最糟的那 1% 用户的体验，比平均值更接近真实感受。",
      "99% of reads finish faster than this. P99 describes the worst 1% of requests — much closer to felt experience than an average.",
    ),
    format: "duration_us",
    betterIs: "lower",
    headline: true,
  },
  {
    key: "ycsb.update_p99_us",
    label: bi("写延迟 P99", "Update latency P99"),
    format: "duration_us",
    betterIs: "lower",
  },
  {
    key: "ycsb.overall_runtime_ms",
    label: bi("压测总时长", "Total run time"),
    format: "duration_ms",
    betterIs: "none",
  },
  {
    key: "ycsb.error_rate",
    label: bi("错误率", "Error rate"),
    blurb: bi("失败操作占比。故障注入开启时，这是可靠性的主要读数。", "Share of operations that failed. With fault injection on, this is the reliability headline."),
    format: "ratio",
    betterIs: "lower",
    headline: true,
  },

  // ---- TPC-C (transactional) -------------------------------------------
  {
    key: "tpc-c.throughput_req_per_sec",
    label: bi("事务吞吐", "Transaction throughput"),
    format: "throughput",
    betterIs: "higher",
    headline: true,
  },
  {
    key: "tpc-c.goodput_req_per_sec",
    label: bi("有效吞吐", "Goodput"),
    blurb: bi(
      "只统计成功事务的吞吐。和总吞吐的差额就是被丢掉的工作量。",
      "Throughput counting successful transactions only. The gap to total throughput is work that was thrown away.",
    ),
    format: "throughput",
    betterIs: "higher",
    headline: true,
  },
  {
    key: "tpc-c.goodput_ratio",
    label: bi("有效吞吐占比", "Goodput ratio"),
    format: "ratio",
    betterIs: "higher",
  },
  {
    key: "tpc-c.p99_latency_us",
    label: bi("事务延迟 P99", "Transaction latency P99"),
    format: "duration_us",
    betterIs: "lower",
    headline: true,
  },
  {
    key: "tpc-c.median_latency_us",
    label: bi("事务延迟中位数", "Median transaction latency"),
    format: "duration_us",
    betterIs: "lower",
  },
  {
    key: "tpc-c.avg_latency_us",
    label: bi("事务平均延迟", "Average transaction latency"),
    format: "duration_us",
    betterIs: "lower",
  },
  { key: "tpc-c.tpmc*", label: bi("tpmC 估算", "tpmC estimate"), format: "count", betterIs: "higher" },

  // ---- LLM / agent suites ----------------------------------------------
  {
    key: "swe-bench.resolved",
    label: bi("问题解决率", "Issues resolved"),
    blurb: bi("真实 GitHub issue 中被成功修复的比例。", "Share of real GitHub issues the agent actually fixed."),
    format: "ratio",
    betterIs: "higher",
    headline: true,
  },
  {
    key: "swe-bench.cost_per_resolved",
    label: bi("每解决一个问题的成本", "Cost per resolved issue"),
    format: "usd",
    betterIs: "lower",
    headline: true,
  },
  {
    key: "human-eval.pass_at_1",
    label: bi("一次通过率", "pass@1"),
    blurb: bi("第一次生成的代码就通过全部单元测试的比例。", "Share of problems where the first generated program passes every unit test."),
    format: "ratio",
    betterIs: "higher",
    headline: true,
  },
  {
    key: "*.accuracy",
    label: bi("准确率", "Accuracy"),
    format: "ratio",
    betterIs: "higher",
    headline: true,
  },
  {
    key: "*.answered_rate",
    label: bi("作答率", "Answered rate"),
    blurb: bi(
      "模型给出可解析答案的比例。准确率高但作答率低，说明它在大量题目上直接弃答。",
      "Share of items that produced a parseable answer. High accuracy with a low answered rate means it simply declined most of them.",
    ),
    format: "ratio",
    betterIs: "higher",
  },
  { key: "*.avg_latency_ms", label: bi("平均响应耗时", "Average response time"), format: "duration_ms", betterIs: "lower" },
  { key: "*.cost_usd", label: bi("花费", "Cost"), format: "usd", betterIs: "lower" },
  { key: "*.total_tokens", label: bi("token 用量", "Tokens used"), format: "tokens", betterIs: "lower" },
  { key: "*.cost_per_qphh", label: bi("每单位性能成本", "Cost per unit of performance"), format: "usd", betterIs: "lower" },
];

/** Suffix → format/direction, used only when there is no written spec. */
const SUFFIX_RULES: Array<{ suffix: string; format: MetricFormat; betterIs: BetterIs }> = [
  { suffix: "_p99_us", format: "duration_us", betterIs: "lower" },
  { suffix: "_p95_ms", format: "duration_ms", betterIs: "lower" },
  { suffix: "_p50_ms", format: "duration_ms", betterIs: "lower" },
  { suffix: "_us", format: "duration_us", betterIs: "none" },
  { suffix: "_ms", format: "duration_ms", betterIs: "none" },
  { suffix: "_s", format: "duration_s", betterIs: "none" },
  { suffix: "_usd", format: "usd", betterIs: "lower" },
  { suffix: "_rate", format: "ratio", betterIs: "none" },
  { suffix: "_ratio", format: "ratio", betterIs: "none" },
  { suffix: "_rps", format: "throughput", betterIs: "higher" },
  { suffix: "_count", format: "count", betterIs: "none" },
];

/** Declared unit → format, for keys whose name carries no hint. */
const UNIT_FORMATS: Record<string, MetricFormat> = {
  ms: "duration_ms",
  us: "duration_us",
  s: "duration_s",
  ratio: "ratio",
  pass: "pass",
  count: "count",
  usd: "usd",
  USD: "usd",
  tokens: "tokens",
  ops_per_sec: "throughput",
  req_per_sec: "throughput",
  rps: "throughput",
};

function matches(pattern: string, key: string): boolean {
  if (!pattern.includes("*")) return pattern === key;
  if (pattern.startsWith("*")) return key.endsWith(pattern.slice(1));
  if (pattern.endsWith("*")) return key.startsWith(pattern.slice(0, -1));
  return false;
}

/**
 * Turn a raw measurement key into something renderable.
 *
 * Falls back rather than failing: an unrecognised key still gets a readable
 * label and a sane format, but never a direction we did not verify.
 */
export function lookupMetric(key: string, unit?: string): ResolvedMetric {
  const exact = METRIC_SPECS.find((spec) => spec.key === key);
  if (exact !== undefined) return { ...exact, known: true };

  const pattern = METRIC_SPECS.find((spec) => spec.key.includes("*") && matches(spec.key, key));
  if (pattern !== undefined) return { ...pattern, key, known: true };

  const rule = SUFFIX_RULES.find((entry) => key.endsWith(entry.suffix));
  const format = rule?.format ?? (unit !== undefined ? UNIT_FORMATS[unit] : undefined) ?? "raw";
  const pretty = prettifyKey(key);
  return {
    key,
    label: { zh: pretty, en: pretty },
    format,
    // Deliberately not inferred from the suffix alone: calling a rising number
    // "better" when it is actually a regression is worse than saying nothing.
    betterIs: rule?.betterIs ?? "none",
    known: false,
  };
}

/** `tpc-x.warm_start_p50_ms` -> `warm start p50` — readable, still recognisable. */
export function prettifyKey(key: string): string {
  const tail = key.includes(".") ? key.slice(key.lastIndexOf(".") + 1) : key;
  return tail
    .replace(/_(ms|us|s|usd|ratio|rate|count|rps)$/, "")
    .replace(/_/g, " ")
    .trim();
}

export function metricLabel(spec: MetricSpec, locale: Locale): string {
  return locale === "zh" ? spec.label.zh : spec.label.en;
}

export function metricBlurb(spec: MetricSpec, locale: Locale): string | null {
  if (spec.blurb === undefined) return null;
  return locale === "zh" ? spec.blurb.zh : spec.blurb.en;
}

/** Format a measurement value for display: `{text, unit}` so the unit can be
 * rendered smaller than the figure without the caller re-parsing a string. */
export function formatMetric(value: unknown, format: MetricFormat): { text: string; unit: string } {
  if (typeof value === "boolean") return { text: value ? "yes" : "no", unit: "" };
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return { text: typeof value === "string" && value !== "" ? value : "—", unit: "" };
  }
  switch (format) {
    case "duration_ms":
      if (value < 1) return { text: value.toFixed(2), unit: "ms" };
      if (value < 1000) return { text: value.toFixed(value < 10 ? 2 : 1), unit: "ms" };
      if (value < 60_000) return { text: (value / 1000).toFixed(2), unit: "s" };
      return { text: (value / 60_000).toFixed(1), unit: "min" };
    case "duration_us":
      if (value < 1000) return { text: value.toFixed(0), unit: "µs" };
      return { text: (value / 1000).toFixed(value < 10_000 ? 2 : 1), unit: "ms" };
    case "duration_s":
      if (value < 60) return { text: value.toFixed(value < 10 ? 2 : 1), unit: "s" };
      return { text: (value / 60).toFixed(1), unit: "min" };
    case "ratio":
      return { text: (value * 100).toFixed(value === 0 || value === 1 ? 0 : 1), unit: "%" };
    case "pass":
      return { text: value >= 1 ? "pass" : "fail", unit: "" };
    case "throughput":
      return { text: compact(value), unit: "/s" };
    case "usd":
      return { text: value < 0.01 ? value.toFixed(5) : value.toFixed(2), unit: "USD" };
    case "tokens":
      return { text: compact(value), unit: "" };
    case "count":
    case "raw":
      return { text: compact(value), unit: "" };
  }
}

/** 7027 -> "7,027"; 346512 -> "347K"; 1.23 -> "1.23". */
function compact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (abs >= 10_000) return `${Math.round(value / 1000).toLocaleString("en-US")}K`;
  if (Number.isInteger(value)) return value.toLocaleString("en-US");
  if (abs >= 100) return value.toFixed(0);
  return String(Number(value.toPrecision(4)));
}

// ----------------------------------------------------------------------
// Lifecycle vocabulary
// ----------------------------------------------------------------------

/** The four lifecycle phases, in order. Mirrors core/progress.PHASE_OF_STAGE. */
export const PHASES = ["prepare", "connect", "measure", "conclude"] as const;
export type Phase = (typeof PHASES)[number];

export const PHASE_LABELS: Record<Phase, Bilingual> = {
  prepare: bi("准备", "Prepare"),
  connect: bi("连接", "Connect"),
  measure: bi("测量", "Measure"),
  conclude: bi("定论", "Conclude"),
};

export const STAGE_ORDER = [
  "VALIDATE",
  "DESCRIBE",
  "PREFLIGHT",
  "SETUP",
  "EXECUTE",
  "SEAL",
  "TEARDOWN",
  "SCORE",
  "ENRICH",
  "PERSIST",
  "PUBLISH",
] as const;

export const PHASE_OF_STAGE: Record<string, Phase> = {
  VALIDATE: "prepare",
  DESCRIBE: "prepare",
  PREFLIGHT: "prepare",
  SETUP: "connect",
  TEARDOWN: "connect",
  EXECUTE: "measure",
  SEAL: "measure",
  SCORE: "conclude",
  ENRICH: "conclude",
  PERSIST: "conclude",
  PUBLISH: "conclude",
};

export interface StageSpec {
  label: Bilingual;
  blurb: Bilingual;
}

/**
 * What each stage actually does, in a sentence — including the two that
 * routinely confuse readers: `SEAL` (which is not "collecting results") and
 * `PUBLISH: skipped` (which is a deliberate guarantee, not a failure).
 */
export const STAGE_GLOSSARY: Record<string, StageSpec> = {
  VALIDATE: {
    label: bi("校验请求", "Validate request"),
    blurb: bi("检查这次运行的参数是否合法。失败说明请求本身有问题，不是被测系统的问题。", "Checks this run's parameters. A failure means the request was wrong, not the system under test."),
  },
  DESCRIBE: {
    label: bi("识别环境", "Describe environment"),
    blurb: bi("记录被测目标、运行环境和指纹，供之后复现和比对。", "Records the target, the environment and the fingerprints that make this run reproducible and comparable."),
  },
  PREFLIGHT: {
    label: bi("起飞前检查", "Preflight"),
    blurb: bi("在动用任何资源之前确认凭据和连通性。失败则什么都不会被创建。", "Confirms credentials and connectivity before anything is provisioned. On failure nothing is ever created."),
  },
  SETUP: {
    label: bi("建立连接", "Set up"),
    blurb: bi("连接到已有服务，或按需开通资源。", "Connects to an existing service, or provisions one."),
  },
  EXECUTE: {
    label: bi("执行测评", "Execute"),
    blurb: bi("真正跑负载的阶段。这里的耗时就是被测系统的耗时。", "The stage that actually runs the workload. Time spent here is time the system under test took."),
  },
  SEAL: {
    label: bi("封存证据", "Seal evidence"),
    blurb: bi("把执行期间产生的证据定格，之后任何环节都不能再改动它。", "Freezes the evidence produced during execution; nothing downstream may alter it."),
  },
  TEARDOWN: {
    label: bi("释放资源", "Tear down"),
    blurb: bi("无论前面成功还是失败都会执行，确保不留下在计费的资源。", "Runs whether the rest succeeded or not, so nothing is left behind still billing."),
  },
  SCORE: {
    label: bi("计分", "Score"),
    blurb: bi("纯函数，只读封存的证据。被测系统在这一步已经无法影响结论。", "A pure function over the sealed evidence. By this point the system under test can no longer move the verdict."),
  },
  ENRICH: {
    label: bi("补充信息", "Enrich"),
    blurb: bi("附加价格等外部信息。失败不影响已经得出的结论。", "Attaches external context such as pricing. A failure here cannot change the verdict."),
  },
  PERSIST: {
    label: bi("写入记录", "Persist"),
    blurb: bi("把这条记录落盘。", "Writes the record to disk."),
  },
  PUBLISH: {
    label: bi("对外发布", "Publish"),
    blurb: bi(
      "总是 skipped——这是一条刻意保留的承诺：没有任何发布器改动过这条已封存的记录。",
      "Always skipped — a deliberate, durable denial that any publisher touched this sealed record.",
    ),
  },
};

/** Stage outcome → how it should read and which status colour it earns. */
export type StageTone = "ok" | "skipped" | "failed" | "running" | "pending";

export const STAGE_STATUS_LABELS: Record<StageTone, Bilingual> = {
  ok: bi("通过", "ok"),
  skipped: bi("跳过", "skipped"),
  failed: bi("失败", "failed"),
  running: bi("进行中", "running"),
  pending: bi("未开始", "pending"),
};

export function stageTone(status: string | undefined): StageTone {
  if (status === "ok") return "ok";
  if (status === "failed") return "failed";
  if (status === "skipped") return "skipped";
  if (status === "running") return "running";
  return "pending";
}

// ----------------------------------------------------------------------
// Run status
// ----------------------------------------------------------------------

export type StatusTone = "good" | "warning" | "critical" | "running" | "neutral";

export interface StatusSpec {
  label: Bilingual;
  blurb: Bilingual;
  tone: StatusTone;
}

/**
 * The verdict, in the reader's language. The distinction that matters most
 * and that the raw word hides: `invalid` means *we never measured anything*,
 * while `failed` means we measured and it broke.
 */
export const STATUS_GLOSSARY: Record<string, StatusSpec> = {
  completed: {
    label: bi("正常完成", "Completed"),
    blurb: bi("跑完了，结果可用。", "Ran to completion; the numbers are usable."),
    tone: "good",
  },
  failed: {
    label: bi("执行失败", "Failed"),
    blurb: bi("跑起来了但中途出错，结果不完整。", "It ran and then broke. The result is incomplete."),
    tone: "critical",
  },
  invalid: {
    label: bi("未能开始", "Never measured"),
    blurb: bi(
      "在真正开始测量前就被拦下了——参数不对、凭据不通或前置检查没过。这不是被测系统的失败。",
      "Stopped before measurement began — bad parameters, credentials, or a failed gate. This is not a failure of the system under test.",
    ),
    tone: "warning",
  },
  interrupted: {
    label: bi("被中断", "Interrupted"),
    blurb: bi("被中止了；资源已经释放，已完成的部分也已保存。", "Stopped early. Resources were released and whatever finished was saved."),
    tone: "warning",
  },
  unsupported: {
    label: bi("不支持", "Unsupported"),
    blurb: bi("这个平台不具备本项测评需要的能力。", "This platform lacks a capability this benchmark requires."),
    tone: "neutral",
  },
  running: {
    label: bi("正在运行", "Running"),
    blurb: bi("还在跑。", "Still going."),
    tone: "running",
  },
  abandoned: {
    label: bi("失去联系", "Abandoned"),
    blurb: bi("跑这次运行的进程消失了，没有留下结果。", "The process running this disappeared without leaving a result."),
    tone: "critical",
  },
};

export function lookupStatus(status: string): StatusSpec {
  return (
    STATUS_GLOSSARY[status] ?? {
      label: { zh: status, en: status },
      blurb: { zh: "", en: "" },
      tone: "neutral" as StatusTone,
    }
  );
}

// ----------------------------------------------------------------------
// Reproducibility class
// ----------------------------------------------------------------------

/**
 * `reproducibility_class` is the record's honest answer to "will this number
 * be the same tomorrow?". It is one of the most useful fields in a record and
 * one of the most opaque names, so it always renders as a sentence.
 */
export const REPRODUCIBILITY_GLOSSARY: Record<string, StatusSpec> = {
  deterministic: {
    label: bi("可重现", "Reproducible"),
    blurb: bi("同样的输入必然得到同样的值。差异就是真差异。", "The same input always yields the same value. A difference here is a real difference."),
    tone: "good",
  },
  environmental: {
    label: bi("受环境影响", "Environment-dependent"),
    blurb: bi(
      "会随机器、负载和时间波动。跨机器比较这个数之前，先确认环境可比。",
      "Drifts with the machine, the load and the hour. Confirm the environments are comparable before comparing this number across them.",
    ),
    tone: "warning",
  },
  stochastic: {
    label: bi("本身有随机性", "Inherently random"),
    blurb: bi("即使环境完全一致，重跑也会得到不同的值。看趋势，不要看单次。", "Re-running gives a different value even in an identical environment. Read the trend, not one run."),
    tone: "warning",
  },
};
