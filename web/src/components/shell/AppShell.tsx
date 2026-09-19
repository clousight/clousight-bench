/**
 * The frame every page is hung on: five sections across the top, an optional
 * per-section rail on the left, content in the middle.
 *
 * **Why a shell at all.** The previous viewer had no page skeleton — each view
 * hand-rolled its own heading, its own back link, its own spacing. The visual
 * system was consistent (one mono, hairlines, one palette) and the LAYOUT was
 * not: two list pages disagreed on where the filter went, two detail pages
 * disagreed on where the identity of the thing lived. That is not a styling
 * problem and no amount of palette work fixes it. This file is the fix: the
 * structure is a component, so it cannot drift per view.
 *
 * **Five sections, one per noun.** Suites (what can be measured), Targets (the
 * things under test), Runs (what happened), Observe (what is happening),
 * Config (the vocabulary and the machine). A reader who knows which noun they
 * are asking about knows which tab to press, and every section then has the
 * same list → detail shape underneath.
 *
 * **The rail carries the section's primary action**, pinned top-left, rather
 * than letting each page float a button somewhere in its content. "Create the
 * thing this section is about" is a property of the section, not of whichever
 * list happens to be on screen.
 */

import type { ReactNode } from "react";

import { useI18n } from "@/i18n";
import { cn } from "@/lib/utils";
import {
  configHref,
  observeHref,
  runsHref,
  suitesHref,
  targetsHref,
  type Route,
} from "@/router";

export type SectionId = "suites" | "targets" | "runs" | "observe" | "config";

interface SectionSpec {
  id: SectionId;
  href: string;
  /** i18n key for the tab label. */
  labelKey: string;
}

export const SECTIONS: readonly SectionSpec[] = [
  { id: "suites", href: suitesHref, labelKey: "section.suites" },
  { id: "targets", href: targetsHref, labelKey: "section.targets" },
  { id: "runs", href: runsHref, labelKey: "section.runs" },
  { id: "observe", href: observeHref, labelKey: "section.observe" },
  { id: "config", href: configHref, labelKey: "section.config" },
] as const;

/**
 * Which top-level section a route belongs to.
 *
 * A pure function over the route rather than a flag each view sets: a view
 * that forgets to declare its section would light up the wrong tab, and the
 * reader would learn to distrust the whole bar.
 */
export function sectionOf(route: Route): SectionId | null {
  switch (route.name) {
    case "suites":
    case "suitesInstalled":
    case "suite":
      return "suites";
    case "targets":
    case "targetNew":
    case "target":
    case "targetEdit":
    case "platforms":
      return "targets";
    case "runs":
    case "runNew":
    case "run":
      return "runs";
    case "observe":
    case "observeRun":
      return "observe";
    case "config":
      return "config";
    case "notFound":
      return null;
  }
}

/** One entry in a section's left rail. */
export interface RailItem {
  href: string;
  label: string;
  active: boolean;
  /** Shown to the right of the label; a count, usually. */
  badge?: number;
}

export interface SideRailProps {
  /** The section's one create affordance, or null for a read-only section. */
  primary?: { href: string; label: string } | null;
  items: RailItem[];
}

/**
 * The left rail: the section's primary action, then its views.
 *
 * Rendered as a `<nav>` with its own label so a screen reader can tell it from
 * the top-level section bar — two navigations on a page with no way to name
 * them apart is the classic landmark mistake.
 */
export function SideRail({ primary, items }: SideRailProps) {
  const { t } = useI18n();
  // A rail carrying one link to the page you are already on is furniture: it
  // costs 208px of gutter and tells the reader nothing. It earns its width
  // once the section has a create affordance or more than one view — which is
  // exactly when the sections that are still read-only grow one.
  const useful = (primary !== null && primary !== undefined) || items.length > 1;
  if (!useful) return null;
  return (
    <nav aria-label={t("shell.section_nav")} className="flex w-52 shrink-0 flex-col gap-4">
      {primary !== null && primary !== undefined && (
        <a
          href={primary.href}
          className="inline-flex items-center justify-center gap-1.5 rounded-sm bg-foreground px-3 py-2 text-sm font-medium text-background transition-colors hover:bg-foreground/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span aria-hidden>+</span>
          {primary.label}
        </a>
      )}
      {items.length > 0 && (
        <ul className="flex flex-col gap-px">
          {items.map((item) => (
            <li key={item.href}>
              <a
                href={item.href}
                aria-current={item.active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-2 rounded-sm px-2 py-1.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  item.active
                    ? "bg-muted font-medium text-foreground"
                    : "text-muted-foreground hover:bg-muted/50 hover:text-foreground",
                )}
              >
                <span className="min-w-0 truncate">{item.label}</span>
                {item.badge !== undefined && (
                  <span className="ml-auto font-mono text-[11px] tabular-nums text-muted-foreground">
                    {item.badge}
                  </span>
                )}
              </a>
            </li>
          ))}
        </ul>
      )}
    </nav>
  );
}

export interface AppShellProps {
  route: Route;
  /** The section bar, already rendered by the caller (it owns the app's meta). */
  topBar: ReactNode;
  rail?: ReactNode;
  children: ReactNode;
}

/** Top bar, then rail + content on one row. */
export function AppShell({ topBar, rail, children }: AppShellProps) {
  return (
    <div className="min-h-screen bg-background text-foreground antialiased">
      {topBar}
      <div className="mx-auto flex max-w-7xl gap-8 px-6 py-6">
        {rail}
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
