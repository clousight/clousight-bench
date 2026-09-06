/**
 * Live steps → the same `SpanRow` shape the sealed trace produces, so one
 * waterfall renderer serves both feeds.
 *
 * The two sources differ in one way that matters: a sealed span references its
 * parent by span id, while a live step names its parent, because a suite
 * reporting progress knows what it called the enclosing phase but not what id
 * the tracer will eventually mint for it. Resolution is therefore by name,
 * first occurrence wins.
 */

import type { ProgressStep } from "@/api";
import { MAX_PARENT_HOPS, type SpanRow } from "@/lib/trace";

/** Steps are milliseconds since run start; SpanRow is absolute seconds. */
export function stepsToRows(steps: ProgressStep[]): { rows: SpanRow[]; t0: number } {
  const rows: SpanRow[] = steps.map((step, index) => ({
    // Names repeat across concurrent streams, so the id carries the index too.
    id: `${step.name}#${index}`,
    name: step.name,
    kind: kindOf(step.name),
    startS: step.start_ms / 1000,
    endS: Math.max(step.end_ms, step.start_ms) / 1000,
    status: step.status,
    isError: step.status !== "ok" && step.status !== "",
    error: null,
    attrs: {},
    parentId: step.parent === "" ? null : step.parent,
    depth: 0,
    ancestors: [],
  }));

  rows.sort((a, b) => a.startS - b.startS);

  // First row with a given NAME is the parent everyone by that name refers to.
  const byName = new Map<string, SpanRow>();
  for (const row of rows) {
    if (row.name !== null && !byName.has(row.name)) byName.set(row.name, row);
  }
  for (const row of rows) {
    const chain: Array<string | null> = [];
    const seen = new Set<string>([row.id]);
    let parentName = row.parentId;
    for (let hop = 0; hop < MAX_PARENT_HOPS && parentName !== null; hop += 1) {
      const parent = byName.get(parentName);
      if (parent === undefined || seen.has(parent.id)) break;
      chain.unshift(parent.name);
      seen.add(parent.id);
      parentName = parent.parentId;
    }
    row.ancestors = chain;
    row.depth = chain.length;
  }

  return { rows, t0: 0 };
}

/**
 * Guess a span kind from the step name so the waterfall colours it.
 *
 * Only patterns we actually emit are recognised; anything else stays unkinded
 * and paints in the first categorical slot rather than being given a made-up
 * category.
 */
function kindOf(name: string): string {
  if (/\.(q\d+|rf\d+|s\d+\.q\d+)$/.test(name)) return "db_query";
  if (name.includes("llm") || name.includes("chat")) return "llm_call";
  if (name.includes("tool")) return "tool_call";
  return "stage";
}
