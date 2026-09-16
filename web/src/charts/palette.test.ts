import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { KIND_SLOTS, laneSpanPaint, laneSpanStyle } from "@/charts/palette";
import en from "@/i18n/en.json";
import zh from "@/i18n/zh.json";

/**
 * The kinds `viewer/data.py::_v3_kind` can return.
 *
 * Nothing in this file can check that claim: the list is hand-copied out of
 * Python, so a test asserting "the palette covers every backend kind" against
 * it is really asserting "the palette covers what someone typed here", and
 * both would go green together the day `_v3_kind` grows a case. That is the
 * defect this branch has already shipped once — `tracks.ts` grouped lanes by
 * an attribute name nothing in the repo ever wrote, and its unit tests used
 * the invented name too, so the bug and the test agreed with each other for
 * seven tasks.
 *
 * The cross-language pin is therefore `test_kind_slots_match_the_backend` in
 * `tests/test_viewer_frontend.py`: it parses `_v3_kind`'s `return` statements
 * out of the Python AST and holds them against `KIND_SLOTS` *and* against this
 * literal. Keep the array on one line so it stays greppable from there. What
 * the tests below add on top of that pin is the palette's own rules — which
 * slot each kind may take, and that both locales name it.
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
    // `phase` and `query` are the pair that co-occurs at volume, so this is
    // the collapse that would matter: if either stopped being mapped and fell
    // through to the `span` fallback, both would read --chart-1 and this
    // fails. (The comment that used to sit here claimed the opposite — that
    // the test "would not fail" in exactly that case.)
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

  it("gives an unmapped kind the same paint an explicit `span` gets", () => {
    // Asserted against `laneSpanStyle("span", ...)`, not against a token name.
    // `span` and `phase` deliberately share --chart-1, so an assertion naming
    // the token passes just as happily if the fallback points at `phase`, or
    // at any future kind that lands on the same slot — it cannot see the
    // difference it claims to check. Comparing the two answers can, and it
    // also catches the fallback going away entirely, which yields the string
    // "var(undefined)" rather than a wrong-but-plausible colour.
    expect(laneSpanStyle("mystery-kind", false, true)).toEqual(laneSpanStyle("span", false, true));
    expect(laneSpanStyle("mystery-kind", false, true).backgroundColor).not.toContain("undefined");
  });

  it("gives the lifecycle kind the legible grey, in the table as well as here", () => {
    // Measured against the surface a lifecycle bar sits on, --chart-axis is
    // 1.87:1 light and 1.99:1 dark — under index.css's own 3:1 floor — and in
    // dark mode it carries a baked-in 22% alpha this function's opacity
    // multiplies into ~19% white. It used to be what KIND_SLOTS said while
    // this function overrode it, so the legend and the marks painted two
    // different greys. Both assertions are needed: the first fails if the
    // table regresses, the second if an override comes back.
    expect(KIND_SLOTS.lifecycle).toBe("--muted-foreground");
    expect(laneSpanStyle("lifecycle", false, true).backgroundColor).toBe(`var(${KIND_SLOTS.lifecycle})`);
  });

  it("routes every kind through KIND_SLOTS, with no per-kind exception", () => {
    // The general form of the test above. A special case here is invisible to
    // the legend, which reads the same function but cannot be asked about a
    // kind the caller did not pass it.
    for (const [kind, token] of Object.entries(KIND_SLOTS)) {
      expect(laneSpanStyle(kind, false, true).backgroundColor, kind).toBe(`var(${token})`);
    }
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
    // Two things in Waterfall.tsx paint a kind: the legend swatch and the
    // canvas's renderItem. The previous version of this test sliced the file
    // at `export function KindLegend` and grepped the tail, so renderItem —
    // the other asker, and the one that was actually wrong — sat outside the
    // slice BY CONSTRUCTION. It named a whole-module invariant and could
    // observe one function, which is how KIND_SLOTS and laneSpanStyle
    // disagreeing about `lifecycle` survived a green suite.
    //
    // So: the whole module, comments stripped (prose is allowed to name
    // tokens; code is not).
    const source = readFileSync(fileURLToPath(new URL("./Waterfall.tsx", import.meta.url)), "utf-8");
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    expect(code, "Waterfall.tsx no longer defines KindLegend — is this still the right file?").toContain(
      "export function KindLegend",
    );
    // No colour of its own, anywhere in the file: no token, no tailwind colour
    // utility, no literal.
    expect(code).not.toMatch(
      /bg-chart-|bg-\[|--chart-|--muted-foreground|--status-|oklch\(|rgba?\(|#[0-9a-f]{3}/i,
    );
    // And no second derivation path either. Reading KIND_SLOTS (or the
    // kindColor/readKindColors pair this module used to own) lets a caller
    // rebuild the answer from the table instead of asking for it — which is
    // precisely what renderItem did, and it rebuilt only the hue, leaving the
    // bars opaque under a swatch drawn at 0.85.
    expect(code).not.toMatch(/KIND_SLOTS|kindColor/);
    // Both askers go through the one function: the DOM swatch directly, the
    // canvas through `laneSpanPaint`, which is that function translated.
    expect(code).toContain("laneSpanStyle(");
    expect(code).toMatch(/style: laneSpanPaint\(/);
  });

  it("hands the canvas the same hue AND the same opacity as the swatch", () => {
    // `laneSpanPaint` is the whole of a canvas bar's paint, so there is no
    // half of the answer for a caller to drop. Before it, renderItem set a
    // fill and nothing else: every bar drew opaque while the legend swatch
    // beside it drew at 0.85, under a comment claiming "there is only one
    // answer to give".
    const colors = { [KIND_SLOTS.query]: "rgb(1, 2, 3)", "--status-critical": "rgb(9, 9, 9)" };
    const swatch = laneSpanStyle("query", false, true);
    expect(laneSpanPaint(colors, "query", false, true)).toEqual({
      fill: "rgb(1, 2, 3)",
      opacity: swatch.opacity,
    });
    // An error keeps its own outline, and the outline is the same colour as
    // the fill rather than a second one chosen at the call site.
    expect(laneSpanPaint(colors, "query", true, true)).toEqual({
      fill: "rgb(9, 9, 9)",
      opacity: laneSpanStyle("query", true, true).opacity,
      stroke: "rgb(9, 9, 9)",
      lineWidth: 1.5,
    });
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
