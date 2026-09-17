/**
 * The trace view's chrome: the one line that states the conclusion, the filter
 * box, and the breadcrumb back out of a zoom.
 *
 * **It states the conclusion.** Everything else on this page is an instrument
 * the reader has to drive; this line is the answer they came for, printed
 * without a click — the descent through the heaviest child at every level
 * (`run › EXECUTE › official › load`) and how long the thing at the end of it
 * took. The tree opens exactly that path on arrival (`rowmodel.slowestPath`),
 * and both read `rowmodel`'s own descent rather than each re-deciding what
 * "slowest" means, so the header cannot name a span the tree left closed.
 *
 * **The filter's value is a prop, never state.** The window moves on every
 * pointermove of a strip drag; a query held in here would be a second copy
 * that has to be kept in step with the parent's, and the way that fails is
 * that a re-render for an unrelated reason blanks what the reader typed.
 * `TraceView` owns it, this draws it.
 *
 * **The breadcrumb appears only once there is somewhere to go back to.** Zoom
 * is the capability this redesign exists to deliver, and a reader who has
 * narrowed the window is looking at a page that no longer shows the whole run
 * — the crumb is what says so, and the way out. Unzoomed it renders nothing,
 * because a breadcrumb with one entry is furniture.
 *
 * Two numbers, never one: the crumb prints the window's offsets INTO the run,
 * and the strip underneath keeps printing the run's own total beside the
 * window's width. Colour is inherited; this file names no hue.
 */

import { useCallback, useMemo } from "react";

import { Input } from "@/components/ui/input";
import { useI18n } from "@/i18n";
import { fmtSpanDur } from "@/lib/format";
import { slowestChain, type TreeNode } from "@/lib/rowmodel";
import { spanS, type Viewport } from "@/lib/viewport";

export interface TraceChromeProps {
  /** The whole trace as a forest — `rowmodel.buildTree`'s output, memoised by
   * the parent, which is also what the tree below renders from. */
  tree: TreeNode[];
  /** The window the page is currently showing. */
  view: Viewport;
  /** The whole run. `view === bounds` means the reader has not zoomed. */
  bounds: Viewport;
  query: string;
  onQuery: (next: string) => void;
  onView: (next: Viewport) => void;
  /** How many spans the query matched, or null when there is no query. Counted
   * by the parent, which is what actually applies the filter — a count made
   * here could report a different number than the rows on screen. */
  matches: number | null;
}

export function TraceChrome({
  tree,
  view,
  bounds,
  query,
  onQuery,
  onView,
  matches,
}: TraceChromeProps) {
  const { t } = useI18n();

  const chain = useMemo(() => slowestChain(tree), [tree]);
  const onReset = useCallback(() => onView(bounds), [onView, bounds]);
  const onInput = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => onQuery(event.target.value),
    [onQuery],
  );

  const deepest = chain.length === 0 ? null : chain[chain.length - 1];
  const zoomed = spanS(view) < spanS(bounds) - 1e-9;

  return (
    <div className="flex flex-col gap-2 pb-3">
      {deepest !== null && (
        <p data-conclusion="true" className="flex flex-wrap items-baseline gap-x-1.5 text-xs">
          <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-muted-foreground">
            {t("trace.slowest")}
          </span>
          {chain.map((node, index) => (
            <span key={node.row.id} className="text-muted-foreground">
              {index > 0 && <span className="mr-1.5">›</span>}
              <span className={index === chain.length - 1 ? "text-foreground" : undefined}>
                {node.row.name ?? t("common.unnamed")}
              </span>
            </span>
          ))}
          <span className="font-mono tabular-nums text-foreground">
            {fmtSpanDur(Math.max(deepest.row.endS - deepest.row.startS, 0))}
          </span>
        </p>
      )}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Input
          data-filter="span"
          value={query}
          onChange={onInput}
          placeholder={t("common.filter")}
          aria-label={t("trace.filter_hint")}
          className="h-7 w-56 text-xs"
        />
        {matches !== null && (
          <span className="text-xs text-muted-foreground">
            {matches === 0 ? (
              t("trace.no_match")
            ) : (
              <>
                <span className="font-mono tabular-nums text-foreground">{matches}</span>{" "}
                {t("trace.matches")}
              </>
            )}
          </span>
        )}

        {zoomed && (
          // Not a second reset button in disguise: it says WHERE the window
          // sits in the run, which nothing else on the page does — the strip
          // prints the window's width, and a width cannot locate it.
          <nav
            data-crumb="true"
            aria-label={t("trace.crumb")}
            className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground"
          >
            <button
              type="button"
              onClick={onReset}
              className="border-b-2 border-transparent transition-colors hover:border-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {t("timeline.reset")}
            </button>
            <span aria-hidden>›</span>
            <span className="font-mono tabular-nums text-foreground">
              {fmtSpanDur(view.startS - bounds.startS)} – {fmtSpanDur(view.endS - bounds.startS)}
            </span>
          </nav>
        )}
      </div>
    </div>
  );
}
