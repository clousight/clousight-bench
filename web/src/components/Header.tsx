import { Moon, Sun, Wrench } from "lucide-react";

import { usePolledJSON, useJSON, type Meta, type ProgressList } from "@/api";
import { Button } from "@/components/ui/button";
import { useI18n, type Locale } from "@/i18n";
import { useEngineerView } from "@/lib/engineerView";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { boardHref, liveHref, useRoute } from "@/router";

/** How often the nav re-checks whether anything is running. */
const LIVE_POLL_MS = 5000;

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
      className="inline-flex items-center rounded-md bg-muted p-0.5 text-xs font-medium text-muted-foreground"
    >
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={locale === option.value}
          onClick={() => setLocale(option.value)}
          className={cn(
            "rounded-[5px] px-2 py-1 transition-colors",
            locale === option.value ? "bg-background text-foreground shadow-sm" : "hover:text-foreground",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/**
 * The two top-level destinations.
 *
 * The live tab carries a count badge only while something is running. A
 * permanent "0" would teach people to ignore the one indicator that is
 * supposed to mean "look here now".
 */
function Nav({ activeCount }: { activeCount: number }) {
  const { t } = useI18n();
  const route = useRoute();
  const onLive = route.name === "live" || route.name === "liveRun";

  const items: Array<{ href: string; label: string; active: boolean; badge?: number }> = [
    {
      href: liveHref,
      label: t("nav.live"),
      active: onLive,
      badge: activeCount > 0 ? activeCount : undefined,
    },
    { href: boardHref, label: t("nav.results"), active: !onLive },
  ];

  return (
    <nav className="flex items-center gap-1">
      {items.map((item) => (
        <a
          key={item.href}
          href={item.href}
          aria-current={item.active ? "page" : undefined}
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors",
            item.active ? "bg-secondary text-secondary-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {item.label}
          {item.badge !== undefined && (
            <span className="inline-flex items-center gap-1 rounded-full bg-status-running/15 px-1.5 text-[10px] font-semibold text-status-running">
              <span aria-hidden className="size-1.5 animate-pulse rounded-full bg-status-running" />
              {item.badge}
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
  const meta = useJSON<Meta>("api/meta");
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
