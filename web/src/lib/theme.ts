import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

const THEME_KEY = "csb.theme";

/** Persisted choice if any, else the OS preference (media query) as default. */
export function initialTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // storage unavailable (private mode etc.) — fall through to media default
  }
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Class-driven dark mode: toggles `dark` on <html> so CSS vars swap. */
export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

/**
 * Bumps whenever the `dark` class on <html> flips — canvas consumers (the
 * ECharts waterfall) re-read CSS-var colors on that signal, since canvas
 * paints don't track CSS variables the way DOM styles do.
 */
export function useThemeVersion(): number {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const observer = new MutationObserver(() => setVersion((v) => v + 1));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return version;
}

export function useTheme(): { theme: Theme; toggleTheme: () => void } {
  // main.tsx applies the initial theme before render; state mirrors the DOM.
  const [theme, setTheme] = useState<Theme>(() =>
    document.documentElement.classList.contains("dark") ? "dark" : "light",
  );
  const toggleTheme = useCallback(() => {
    setTheme((current) => {
      const next: Theme = current === "dark" ? "light" : "dark";
      applyTheme(next);
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch {
        // best effort: the toggle still works for this session
      }
      return next;
    });
  }, []);
  return { theme, toggleTheme };
}
