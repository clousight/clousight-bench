/**
 * The row model: turns the flat, sorted `SpanRow[]` that `trace.ts` produces
 * into a collapsible tree, and decides — per node, not globally — whether
 * that node's children should render as ordinary sequential rows or as
 * concurrent lanes.
 *
 * The tree is the structural fact (who is whose child, in time order). The
 * lane decision is a judgment about one node's children: three query streams
 * running at once are not the same picture as twenty stages running in
 * sequence, even though both are "a node with several children". Rendering
 * the wrong one either hides real concurrency behind a single-height row or
 * fans twenty sequential children out into twenty pointless lanes.
 *
 * `idleS`/`selfS` are the time inside a node's own extent that none of its
 * children cover. They are computed from the UNION of child intervals, never
 * the sum: children commonly overlap (that is the whole reason lanes exist),
 * and summing overlapping intervals double-counts the overlap, which can
 * even drive "idle" negative before the clamp hides the bug. Two children
 * covering [0,2] and [1,3] cover 3s of a 4s parent, not 4s.
 */

import type { SpanRow } from "@/lib/trace";

export interface TreeNode {
  row: SpanRow;
  children: TreeNode[];
  /** Parent's own extent minus the union of its children's extents. Never
   * negative. Currently identical to `idleS` — see the module docstring —
   * kept as a separate field because the two names serve different readers
   * downstream (a "how long did this take on its own" question vs. a "what
   * fraction of this bar is unaccounted for" one). */
  selfS: number;
  idleS: number;
}

export interface VisibleRow {
  row: SpanRow;
  /** Depth in the VISIBLE tree (0 for a root row), not `row.depth` — a
   * collapsed ancestor removes a level from what's on screen without
   * changing the span's place in the underlying trace. */
  depth: number;
  hasChildren: boolean;
  childCount: number;
  expanded: boolean;
  /** Non-null only when this row is expanded AND its children overlap in
   * time; otherwise the children render as ordinary sequential rows and
   * `lanes` carries nothing for them to draw differently. */
  lanes: SpanRow[][] | null;
  idleS: number;
}

/** Sort key shared by sibling ordering and root ordering: start time first,
 * id as a tiebreak so a fixed input always produces the same order. */
function timeOrder(a: SpanRow, b: SpanRow): number {
  return a.startS - b.startS || a.id.localeCompare(b.id, "en");
}

/**
 * Total length covered by a set of intervals, merging overlaps AND touching
 * intervals (an instant of contact adds no gap either way, so merging them
 * here changes nothing about the answer). Intervals need not arrive sorted.
 */
function unionLength(intervals: Array<[number, number]>): number {
  if (intervals.length === 0) return 0;
  const sorted = [...intervals].sort((a, b) => a[0] - b[0]);
  let total = 0;
  let [curStart, curEnd] = sorted[0];
  for (let i = 1; i < sorted.length; i += 1) {
    const [start, end] = sorted[i];
    if (start <= curEnd) {
      if (end > curEnd) curEnd = end;
    } else {
      total += curEnd - curStart;
      curStart = start;
      curEnd = end;
    }
  }
  total += curEnd - curStart;
  return total;
}

/**
 * Flat, sorted spans -> a forest of `TreeNode`s, roots and children each in
 * time order. A row whose `parentId` does not resolve to another row in this
 * same list (null, dangling, or self-referential) becomes a root rather than
 * being dropped — a dangling parent is a data problem, not a reason to lose
 * the span from the view.
 */
export function buildTree(rows: SpanRow[]): TreeNode[] {
  const byId = new Map<string, SpanRow>();
  for (const row of rows) if (!byId.has(row.id)) byId.set(row.id, row);

  const childrenOf = new Map<string, SpanRow[]>();
  const roots: SpanRow[] = [];
  for (const row of rows) {
    const parentId = row.parentId;
    if (parentId !== null && parentId !== row.id && byId.has(parentId)) {
      const bucket = childrenOf.get(parentId);
      if (bucket === undefined) childrenOf.set(parentId, [row]);
      else bucket.push(row);
    } else {
      roots.push(row);
    }
  }

  // Guards a parent-chain cycle (A's parent is B, B's parent is A): without
  // it, neither row is ever a root and `build` would recurse forever.
  function build(row: SpanRow, ancestry: Set<string>): TreeNode {
    const kids = (childrenOf.get(row.id) ?? [])
      .filter((child) => !ancestry.has(child.id))
      .slice()
      .sort(timeOrder);
    const childAncestry = new Set(ancestry);
    childAncestry.add(row.id);
    const children = kids.map((kid) => build(kid, childAncestry));

    const covered = unionLength(children.map((child): [number, number] => [child.row.startS, child.row.endS]));
    const duration = Math.max(row.endS - row.startS, 0);
    const uncovered = Math.max(duration - covered, 0);
    return { row, children, selfS: uncovered, idleS: uncovered };
  }

  return roots
    .slice()
    .sort(timeOrder)
    .map((root) => build(root, new Set([root.id])));
}

/**
 * True the moment ANY two children's intervals intersect for a non-zero
 * instant. Children arrive time-ordered from `buildTree`, so a single sweep
 * tracking the running max end suffices — comparing only against the
 * immediately preceding child would miss a later child nested inside an
 * earlier, still-open one.
 *
 * Touching does not count: `[0,1]` immediately followed by `[1,2]` is
 * sequence sharing an instant, not concurrency, so the comparison is a
 * strict `<`, not `<=`.
 */
export function childrenOverlap(node: TreeNode): boolean {
  let maxEnd = -Infinity;
  for (const child of node.children) {
    if (child.row.startS < maxEnd) return true;
    if (child.row.endS > maxEnd) maxEnd = child.row.endS;
  }
  return false;
}

/**
 * Greedy interval packing: an item goes in the first lane whose last item
 * ends at or before this one starts (touching intervals may share a lane);
 * otherwise it opens a new lane. `items` must already be sorted by start.
 *
 * This is the algorithm `tracks.ts::packGroup` implements over span ids;
 * `packGroup` now delegates here rather than keeping its own copy.
 */
export function packIntervals<T extends { startS: number; endS: number }>(items: T[]): T[][] {
  const laneEnds: number[] = [];
  const lanes: T[][] = [];
  for (const item of items) {
    let lane = laneEnds.findIndex((end) => end <= item.startS);
    if (lane === -1) {
      lane = laneEnds.length;
      laneEnds.push(item.endS);
      lanes.push([item]);
    } else {
      laneEnds[lane] = item.endS;
      lanes[lane].push(item);
    }
  }
  return lanes;
}

/** `node`'s children, greedily packed into lanes. Children are already
 * time-ordered by `buildTree`, so no re-sort is needed here. */
export function packChildren(node: TreeNode): SpanRow[][] {
  return packIntervals(node.children.map((child) => child.row));
}

/**
 * Depth-first, time-ordered flattening for rendering. A collapsed node
 * contributes exactly one `VisibleRow` — its children are neither flattened
 * nor counted beyond `childCount` — so toggling one node can only ever
 * change what is under IT, never reorder anything else on screen.
 */
export function flatten(tree: TreeNode[], expanded: ReadonlySet<string>): VisibleRow[] {
  const out: VisibleRow[] = [];

  function walk(node: TreeNode, depth: number): void {
    const isExpanded = expanded.has(node.row.id);
    const lanes = isExpanded && childrenOverlap(node) ? packChildren(node) : null;
    out.push({
      row: node.row,
      depth,
      hasChildren: node.children.length > 0,
      childCount: node.children.length,
      expanded: isExpanded,
      lanes,
      idleS: node.idleS,
    });
    if (isExpanded) {
      for (const child of node.children) walk(child, depth + 1);
    }
  }

  for (const root of tree) walk(root, 0);
  return out;
}

/**
 * The path an operator should see first without clicking anything: at every
 * level, descend into whichever child has the longest wall-clock duration
 * (`endS - startS`), all the way to a leaf. Includes the roots themselves,
 * so expanding this set opens every ancestor down to the slow leaf, not just
 * the leaf's own id.
 *
 * "Heaviest" is duration, not `selfS`/`idleS` — a container with a huge
 * self/idle time is exactly the case (see `aggregate.ts`) that ranks
 * differently for a different question; this one answers "where did the
 * wall clock go", which a pure container answers as loudly as anything it
 * contains.
 */
export function slowestPath(tree: TreeNode[]): Set<string> {
  const ids = new Set<string>();

  function heaviest(children: TreeNode[]): TreeNode | null {
    let best: TreeNode | null = null;
    let bestDuration = -Infinity;
    for (const child of children) {
      const duration = child.row.endS - child.row.startS;
      if (duration > bestDuration) {
        bestDuration = duration;
        best = child;
      }
    }
    return best;
  }

  for (const root of tree) {
    let node: TreeNode | null = root;
    while (node !== null) {
      ids.add(node.row.id);
      node = heaviest(node.children);
    }
  }

  return ids;
}
