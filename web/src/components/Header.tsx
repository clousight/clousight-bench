import { Moon, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useJSON, type Meta } from "@/api";
import { useI18n, type Locale } from "@/i18n";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

function LocaleSwitch() {
  const { locale, setLocale, t } = useI18n();
  const options: Array<{ value: Locale; label: string }> = [
    { value: "en", label: "EN" },
    { value: "zh", label: "中文" },
  ];
  return (
    <div
      role="group"
      aria-label={t('header.lang')}
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
            locale === option.value
              ? "bg-background text-foreground shadow-sm"
              : "hover:text-foreground",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** Wordmark + /api/meta line + locale switch + theme toggle. */
export function Header() {
  const { t } = useI18n();
  const { theme, toggleTheme } = useTheme();
  const meta = useJSON<Meta>("api/meta");

  return (
    <header className="sticky top-0 z-10 border-b bg-background/90 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-6">
        <a href="#/" className="flex items-baseline gap-2">
          <span className="text-base font-semibold tracking-tight">{t('header.title')}</span>
        </a>
        <span className="text-xs text-muted-foreground">
          {meta.data !== null && (
            <>
              {meta.data.counts.records} {t('header.records')}
              <span className="mx-1.5">·</span>v{meta.data.version}
              <span className="mx-1.5">·</span>
              <span className="font-mono">{meta.data.results_dir}</span>
            </>
          )}
          {meta.data === null && meta.error !== null && (
            <span className="text-destructive/60">{t('common.error')}</span>
          )}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <LocaleSwitch />
          <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label={t('header.theme')} title={t('header.theme')}>
            {theme === "dark" ? <Sun /> : <Moon />}
          </Button>
        </div>
      </div>
    </header>
  );
}
