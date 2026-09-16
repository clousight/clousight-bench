import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { KIND_SLOTS, laneSpanStyle } from "@/charts/palette";
import en from "@/i18n/en.json";
import zh from "@/i18n/zh.json";

/**
 * The kinds `viewer/data.py::_v3_kind` can return. If that function grows a
 * case, this list and the palette must grow with it — an unmapped kind paints
 * in the fallback slot, which silently stops colour from carrying identity.
 */
const KINDS_FROM_BACKEND = ["tool_call", "llm_call", "query", "phase", "lifecycle", "span"];

describe("span-kind colours", () => {
  it("covers every kind the backend can produce", () => {
    for (const kind of KINDS_FROM_BACKEND) {
      expect(Object.hasOwn(KIND_SLOTS, kind), kind).toBe(true);
    }
  });

  it("maps no kind the backend cannot produce", () => {
    // A stale entry is how "db_query" and "stage" survived a rename and left
    // every bar painting the same blue.
    for (const kind of Object.keys(KIND_SLOTS)) {
      expect(KINDS_FROM_BACKEND, kind).toContain(kind);
    }
  });

  it("separates the pair that co-occurs at volume", () => {
    // A data benchmark's trace is ~900 query spans inside ~15 phase spans.
    expect(KIND_SLOTS.query).not.toBe(KIND_SLOTS.phase);
  });

  it("keeps the lifecycle recessive and off the categorical slots", () => {
    // Since the merge the stages share a chart with the work inside them. They
    // are the frame, so they must not compete for a series colour.
    const categorical = [KIND_SLOTS.phase, KIND_SLOTS.query, KIND_SLOTS.llm_call, KIND_SLOTS.tool_call];
    expect(categorical).not.toContain(KIND_SLOTS.lifecycle);
  });

  it("names every kind in both locales", () => {
    for (const kind of KINDS_FROM_BACKEND) {
      expect(Object.hasOwn(en, `trace.kind.${kind}`), kind).toBe(true);
      expect(Object.hasOwn(zh, `trace.kind.${kind}`), kind).toBe(true);
    }
  });
});

describe("laneSpanStyle", () => {
  it("colours by kind when there is no error", () => {
    const style = laneSpanStyle("query", false, true);
    expect(style.backgroundColor).toBe(`var(${KIND_SLOTS.query})`);
  });

  it("gives two different kinds two different colours", () => {
    // Would not fail if both kinds silently fell back to the same slot.
    const a = laneSpanStyle("phase", false, true);
    const b = laneSpanStyle("query", false, true);
    expect(a.backgroundColor).not.toBe(b.backgroundColor);
  });

  it("an error overrides hue regardless of kind", () => {
    const query = laneSpanStyle("query", true, true);
    const tool = laneSpanStyle("tool_call", true, true);
    expect(query.backgroundColor).toBe("var(--status-critical)");
    expect(tool.backgroundColor).toBe("var(--status-critical)");
  });

  it("falls back to the span slot for an unrecognised kind", () => {
    expect(laneSpanStyle("mystery-kind", false, true).backgroundColor).toBe(`var(${KIND_SLOTS.span})`);
  });

  it("gives the lifecycle kind a muted-foreground grey, not --chart-axis", () => {
    // --chart-axis already carries a baked-in alpha in dark mode, which this
    // function's own opacity would multiply into an unreadably faint dash.
    // If this regressed to KIND_SLOTS.lifecycle directly, this test fails.
    expect(laneSpanStyle("lifecycle", false, true).backgroundColor).toBe("var(--muted-foreground)");
    expect(laneSpanStyle("lifecycle", false, true).backgroundColor).not.toBe(`var(${KIND_SLOTS.lifecycle})`);
  });

  it("selection state changes opacity, never hue", () => {
    const on = laneSpanStyle("query", false, true);
    const off = laneSpanStyle("query", false, false);
    expect(on.backgroundColor).toBe(off.backgroundColor);
    expect(on.opacity).toBeGreaterThan(off.opacity);
  });

  it("keeps the deselected floor at 0.4, not low enough to read as an empty lane", () => {
    expect(laneSpanStyle("query", false, false).opacity).toBe(0.4);
  });

  it("gives a kind exactly one answer, whoever asks", () => {
    // The lifecycle override is the whole reason this matters: KIND_SLOTS says
    // --chart-axis, laneSpanStyle says --muted-foreground, and for a while the
    // legend read the first while every mark on screen painted the second. A
    // legend that disagrees with its chart is worse than no legend, so the
    // legend is not allowed a colour table of its own — it has to ask this
    // function. Comments are stripped first; they are allowed to name tokens.
    const source = readFileSync(fileURLToPath(new URL("./Waterfall.tsx", import.meta.url)), "utf-8");
    const legend = source.slice(source.indexOf("export function KindLegend"));
    expect(legend, "KindLegend not found in Waterfall.tsx").not.toBe("");
    const code = legend.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    expect(code).toContain("laneSpanStyle(");
    expect(code).not.toMatch(/bg-chart-|bg-\[|--chart-|--muted-foreground|oklch\(|rgba?\(|#[0-9a-f]{3}/i);
  });

  it("an error is more opaque than a normal span in the same selection state", () => {
    // The strip and the lane must agree that an error pops above the
    // surrounding spans, not recede below them, in both the selected and the
    // deselected state.
    expect(laneSpanStyle("query", true, true).opacity).toBeGreaterThan(laneSpanStyle("query", false, true).opacity);
    expect(laneSpanStyle("query", true, false).opacity).toBeGreaterThan(
      laneSpanStyle("query", false, false).opacity,
    );
  });
});
