/**
 * Normalization over raw trajectory spans, shared by the transcript cards and
 * the ECharts waterfall: stable t_start ordering, id fallbacks, parent-chain
 * resolution (depth + breadcrumb) with a cycle guard.
 */

import type { TraceSpan, TrajectoryData } from "@/api";

/** How many parent hops we follow before assuming a cycle / garbage chain. */
export const MAX_PARENT_HOPS = 8;

export interface SpanRow {
  /** span_id, or a synthetic `#<index>` when the line carries none. */
  id: string;
  /** Raw name; null when absent — callers render t('common.unnamed'). */
  name: string | null;
  kind: string;
  /** Absolute seconds; missing/garbage timestamps degrade to t0. */
  startS: number;
  endS: number;
  status: string;
  isError: boolean;
  /** Error message when the span carries one (may be null on error spans). */
  error: string | null;
  attrs: Record<string, unknown>;
  parentId: string | null;
  /** Parent hops to the root (0 = root), capped at MAX_PARENT_HOPS. */
  depth: number;
  /** Resolved ancestor names, root-first (nulls = unnamed ancestors). */
  ancestors: Array<string | null>;
}

function num(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

/** Raw trajectory payload -> rows sorted by t_start (stable for ties). */
export function buildRows(data: TrajectoryData): SpanRow[] {
  const t0 = num(data.t0, 0);
  const spans = Array.isArray(data.spans) ? data.spans : [];

  const rows = spans.map((span: TraceSpan, index: number): SpanRow => {
    const startS = num(span.t_start, t0);
    return {
      id: str(span.span_id) ?? `#${index}`,
      name: str(span.name),
      kind: str(span.kind) ?? "",
      startS,
      endS: Math.max(num(span.t_end, startS), startS),
      status: str(span.status) ?? "",
      isError: span.status === "error",
      error: str(span.error),
      attrs:
        span.attrs !== null && typeof span.attrs === "object" && !Array.isArray(span.attrs)
          ? span.attrs
          : {},
      parentId: str(span.parent_id),
      depth: 0,
      ancestors: [],
    };
  });

  // Stable sort: Array.prototype.sort is spec-stable, so equal t_start spans
  // keep their artifact line order.
  rows.sort((a, b) => a.startS - b.startS);

  // Resolve parent chains AFTER sorting so `ancestors`/`depth` line up with
  // the id map regardless of artifact ordering. First occurrence of an id wins.
  const byId = new Map<string, SpanRow>();
  for (const row of rows) if (!byId.has(row.id)) byId.set(row.id, row);
  for (const row of rows) {
    const chain: Array<string | null> = [];
    const seen = new Set<string>([row.id]);
    let parentId = row.parentId;
    for (let hop = 0; hop < MAX_PARENT_HOPS && parentId !== null; hop += 1) {
      if (seen.has(parentId)) break; // cycle — stop climbing
      const parent = byId.get(parentId);
      if (parent === undefined) break; // dangling parent_id
      chain.unshift(parent.name);
      seen.add(parentId);
      parentId = parent.parentId;
    }
    row.ancestors = chain;
    row.depth = chain.length;
  }
  return rows;
}

/** Total trace duration in seconds: max t_end − t0 (0 for empty traces). */
export function totalSeconds(rows: SpanRow[], t0: number): number {
  let maxEnd = t0;
  for (const row of rows) if (row.endS > maxEnd) maxEnd = row.endS;
  return Math.max(maxEnd - t0, 0);
}
