/**
 * dsh-style i18n: flat JSON dictionaries bundled at build time, a `t(key)`
 * hook, localStorage persistence (csb.locale), navigator default (zh-* ->
 * zh, everything else en). Missing keys fall back to en, then to the raw key
 * (so a typo shows up on screen instead of a blank).
 */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

import en from "@/i18n/en.json";
import zh from "@/i18n/zh.json";

export type Locale = "en" | "zh";

const LOCALE_KEY = "csb.locale";

const dictionaries: Record<Locale, Record<string, string>> = { en, zh };

function initialLocale(): Locale {
  try {
    const stored = localStorage.getItem(LOCALE_KEY);
    if (stored === "en" || stored === "zh") return stored;
  } catch {
    // storage unavailable — fall through to the navigator default
  }
  return (navigator.language ?? "").toLowerCase().startsWith("zh") ? "zh" : "en";
}

interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: string) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      localStorage.setItem(LOCALE_KEY, next);
    } catch {
      // best effort: the switch still applies for this session
    }
  }, []);

  const value = useMemo<I18nContextValue>(
    () => ({
      locale,
      setLocale,
      // Object.hasOwn: only own dictionary entries resolve — inherited names
      // like "constructor" or "toString" never leak in as translations.
      t: (key: string) => {
        const dict = dictionaries[locale];
        if (Object.hasOwn(dict, key)) return dict[key];
        if (Object.hasOwn(dictionaries.en, key)) return dictionaries.en[key];
        return key;
      },
    }),
    [locale, setLocale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const value = useContext(I18nContext);
  if (value === null) throw new Error("useI18n must be used inside <I18nProvider>");
  return value;
}
