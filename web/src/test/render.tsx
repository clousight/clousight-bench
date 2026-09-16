/**
 * The component test harness: render to a string, assert on the markup.
 *
 * `renderToStaticMarkup` needs no DOM, so this runs under vitest's `node`
 * environment with nothing installed that the app does not already ship. What
 * it buys is everything a component *emits* — inline styles and the widths in
 * them, class names, `title` and `aria-*` attributes, which rows exist and in
 * what order. What it cannot buy is layout: there is no box model here, so
 * "this row wraps" or "this cell truncates" are not observable and a test
 * claiming to check one would be asserting nothing.
 *
 * One render, no effects, no events: `useState` initialisers run and
 * `useMemo` computes, but nothing re-renders. Anything about behaviour across
 * renders (memo identity, drag handling) belongs in a pure test of the
 * function that decides it.
 */

import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { I18nProvider } from "@/i18n";

/**
 * Pin the locale to `en` for every component test.
 *
 * `I18nProvider` picks its initial locale from `localStorage`, then from
 * `navigator.language`. Node defines `navigator`, and its `language` follows
 * the machine's ICU default — so on a `LANG=zh_CN` box the provider would
 * hand every component the Chinese dictionary and any assertion naming an
 * English string would fail for a reason that has nothing to do with the
 * code. A stub that answers "en" is read first and makes the harness
 * environment-independent.
 */
const pinnedLocale = {
  getItem: () => "en",
  setItem: () => {},
  removeItem: () => {},
  clear: () => {},
  key: () => null,
  length: 0,
} satisfies Storage;

(globalThis as { localStorage?: Storage }).localStorage = pinnedLocale;

/** Render a component under the i18n provider it requires and return its HTML. */
export function renderMarkup(node: ReactElement): string {
  return renderToStaticMarkup(<I18nProvider>{node}</I18nProvider>);
}

/** Every `width:NN%` in the markup, in document order. The panes size their
 * bars and overlays in percent, which is the one geometric fact a string
 * render does carry. */
export function widthPercents(markup: string): number[] {
  return [...markup.matchAll(/width:([\d.]+)%/g)].map((match) => Number(match[1]));
}

/**
 * The opening tag of everything in a markup string a keyboard can land on.
 *
 * Focus treatment is a per-element attribute, so `expect(markup).toContain(
 * "focus-visible:ring-ring")` is satisfied by any ONE element having it —
 * which is exactly how the overview strip went without a ring while the reset
 * button six lines above it covered for the assertion. Returning the tags
 * makes the assertion per element, and the failure message name the element
 * that is missing it.
 */
export function focusableTags(markup: string): string[] {
  const pattern = /<(?:button|input|select|textarea|a)\b[^>]*>|<[a-z]+\b[^>]*tabindex="0"[^>]*>/g;
  return [...markup.matchAll(pattern)].map((match) => match[0]);
}
