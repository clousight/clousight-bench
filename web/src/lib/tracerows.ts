/**
 * What the trace page shows, as one pure function.
 *
 * `TraceView` owns the state — the window, the expansion set, the selection,
 * the query — and that ownership is deliberate. But the *derivation* from that
 * state to the rows on screen is arithmetic, and it was the only non-trivial
 * untested logic left on this view: `renderToStaticMarkup` cannot reach it
 * through `TraceView` (`useJSON` fetches in an effect a static render never
 * runs, so the component resolves to its loading state), so it lives here,
 * where it can be asserted on directly. Every other decision in this redesign
 * was lifted for the same reason.
 *
 * Three things happen here and they are not separable:
 *
 * 1. **Arrival is DERIVED, never copied into state.** With nothing opened the
 *    expansion set is `slowestPath(tree)` — the descent through the heaviest
 *    child at every level — so the page opens on where the wall clock went.
 *    Copying that into state at mount would leave it pointing at the previous
 *    run's span ids when the trace changes.
 *
 * 2. **A hit force-opens its ancestors.** A match inside a collapsed node is a
 *    match the reader cannot see, so every ancestor of every hit is added to
 *    the expansion set and kept in the filtered rows. A hit with no path to it
 *    reads as no hit at all.
 *
 * 3. **The filter reaches inside the lanes.** A lane is drawn from the TREE,
 *    not from the row list, so filtering rows alone would answer "q13" with
 *    three lanes of sixty-six marks — the rows filtered and the marks not.
 */

import { flatten, slowestPath, type TreeNode, type VisibleRow } from "@/lib/rowmodel";
import type { SpanRow } from "@/lib/trace";

export interface TraceRowsInput {
  /** The whole trace as a forest — `buildTree`'s output. */
  tree: TreeNode[];
  /** The same trace, flat, for the name scan and the parent index. */
  rows: SpanRow[];
  /** What the reader has opened, or null if they have not touched a chevron
   * yet — which is what lets the arrival state stay derived. */
  opened: ReadonlySet<string> | null;
  /** The filter box's contents, raw. Trimmed and lowercased here. */
  query: string;
}

export interface TraceRowsResult {
  /** The rows to draw, in order, already reflecting expansion and the filter. */
  visible: VisibleRow[];
  /** The expansion set actually in force — arrival plus any forced-open
   * ancestors. This is what a chevron toggles against, so a click on a node
   * the filter opened removes exactly that node rather than re-deriving from
   * a set the reader never saw. */
  expanded: ReadonlySet<string>;
  /** How many spans the query matched, or null when there is no query. */
  matches: number | null;
}

export function traceRows({ tree, rows, opened, query }: TraceRowsInput): TraceRowsResult {
  const needle = query.trim().toLowerCase();

  let hits: Set<string> | null = null;
  if (needle !== "") {
    hits = new Set<string>();
    for (const row of rows) {
      if ((row.name ?? "").toLowerCase().includes(needle)) hits.add(row.id);
    }
  }

  const base = opened ?? slowestPath(tree);
  if (hits === null) {
    return { visible: flatten(tree, base), expanded: base, matches: null };
  }

  const parentOf = new Map<string, string | null>();
  for (const row of rows) parentOf.set(row.id, row.parentId);

  const ancestors = new Set<string>();
  for (const id of hits) {
    let parent = parentOf.get(id) ?? null;
    // Stops at anything already accounted for: either a node an earlier walk
    // carried all the way to its root, or another hit, which this same loop
    // walks up from in its own turn.
    while (parent !== null && !ancestors.has(parent) && !hits.has(parent)) {
      ancestors.add(parent);
      parent = parentOf.get(parent) ?? null;
    }
  }

  const expanded = new Set(base);
  for (const id of ancestors) expanded.add(id);

  const keep = (id: string) => hits.has(id) || ancestors.has(id);
  const visible: VisibleRow[] = [];
  for (const vrow of flatten(tree, expanded)) {
    if (!keep(vrow.row.id)) continue;
    const lanes =
      vrow.lanes === null
        ? null
        : vrow.lanes
            .map((lane) => lane.filter((row) => keep(row.id)))
            .filter((lane) => lane.length > 0);
    visible.push(
      lanes === vrow.lanes
        ? vrow
        : { ...vrow, lanes: lanes !== null && lanes.length > 0 ? lanes : null },
    );
  }

  return { visible, expanded, matches: hits.size };
}
