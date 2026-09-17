import { Moon, Sun, Wrench } from "lucide-react";

import { usePolledJSON, type Meta, type ProgressList } from "@/api";
import { Button } from "@/components/ui/button";
import { useI18n, type Locale } from "@/i18n";
import { useEngineerView } from "@/lib/engineerView";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { SECTIONS, sectionOf } from "@/components/shell/AppShell";
import { boardHref, useRoute } from "@/router";

/** How often the nav re-checks whether anything is running. */
const LIVE_POLL_MS = 5000;

/**
 * How often the nav re-reads the results directory.
 *
 * This was a fetch-once, and the Header never unmounts — so the record count
 * froze at whatever it was when the tab opened. Navigating to the runs table
 * after two runs had sealed put "15 records" in the nav directly above a table
 * headed "All runs 18": two claims about the same directory, on one screen,
 * disagreeing. The count only changes when a run seals, so this is slow on
 * purpose; it exists to bound the disagreement, not to animate a number.
 */
const META_POLL_MS = 30000;

function LocaleSwitch() {
  const { locale, setLocale, t } = useI18n();
  const options: Array<{ value: Locale; label: string }> = [
    { value: "en", label: "EN" },
    { value: "zh", label: "中文" },
  ];
  return (
    <div
      role="group"
      aria-label={t("header.lang")}
      className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground"
    >
      {options.map((option) => {
        const active = locale === option.value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            onClick={() => setLocale(option.value)}
            className={cn(
              "border-b-2 border-transparent px-1.5 py-1 transition-colors hover:text-foreground",
              active && "border-foreground text-foreground",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}

/**
 * The five sections, one per noun the product is about.
 *
 * The bar is derived from `SECTIONS` and the active entry from `sectionOf()`,
 * a pure function of the route — not a flag each view sets. A view that forgot
 * to declare its section would light the wrong tab, and a nav bar that is
 * wrong even occasionally is one people stop reading.
 *
 * The live badge rides on Observe and appears only while something is running:
 * a permanent "0" teaches people to ignore the one indicator meant to say
 * "look here now".
 */
function Nav({ activeCount }: { activeCount: number }) {
  const { t } = useI18n();
  const active = sectionOf(useRoute());

  return (
    <nav className="flex items-center gap-1">
      {SECTIONS.map((section) => (
        <a
          key={section.id}
          href={section.href}
          aria-current={active === section.id ? "page" : undefined}
          className={cn(
            "inline-flex items-center gap-1.5 border-b-2 border-transparent px-2.5 py-1.5 text-sm font-medium transition-colors",
            active === section.id
              ? "border-foreground text-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {t(section.labelKey)}
          {section.id === "observe" && activeCount > 0 && (
            <span className="inline-flex items-center gap-1 rounded-sm border border-transparent bg-status-running/15 px-1.5 py-0 font-mono text-[10px] font-medium uppercase tracking-[0.08em] text-status-running transition-colors dark:bg-status-running/20">
              <span aria-hidden className="size-1.5 animate-pulse rounded-full bg-status-running" />
              {activeCount}
            </span>
          )}
        </a>
      ))}
    </nav>
  );
}

/** Wordmark + nav + engineer-view / locale / theme switches. */
export function Header() {
  const { t } = useI18n();
  const { theme, toggleTheme } = useTheme();
  const { engineerView, toggleEngineerView } = useEngineerView();
  const meta = usePolledJSON<Meta>("api/meta", META_POLL_MS);
  const live = usePolledJSON<ProgressList>("api/progress", LIVE_POLL_MS);
  const activeCount = live.data?.runs.filter((run) => run.status === "running").length ?? 0;

  return (
    <header className="sticky top-0 z-10 border-b border-border bg-background">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-6">
        <a href={boardHref} className="flex items-baseline gap-2">
          <span className="text-base font-semibold tracking-tight">{t("header.title")}</span>
        </a>
        <Nav activeCount={activeCount} />
        <span className="hidden text-xs text-muted-foreground lg:inline">
          {meta.data !== null && (
            <>
              {meta.data.counts.records} {t("header.records")}
              <span className="mx-1.5">·</span>v{meta.data.version}
              <span className="mx-1.5">·</span>
              <span className="font-mono">{meta.data.results_dir}</span>
            </>
          )}
          {meta.data === null && meta.error !== null && (
            <span className="text-destructive/60">{t("common.error")}</span>
          )}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <Button
            variant={engineerView ? "outline" : "ghost"}
            size="sm"
            onClick={toggleEngineerView}
            aria-pressed={engineerView}
            title={t("engineer.toggle_blurb")}
            className={cn("gap-1.5", !engineerView && "text-muted-foreground")}
          >
            <Wrench aria-hidden />
            <span className="hidden sm:inline">{t("engineer.toggle")}</span>
          </Button>
          <LocaleSwitch />
          <Button
            variant="ghost"
            size="icon"
            onClick={toggleTheme}
            aria-label={t("header.theme")}
            title={t("header.theme")}
          >
            {theme === "dark" ? <Sun /> : <Moon />}
          </Button>
        </div>
      </div>
    </header>
  );
}
