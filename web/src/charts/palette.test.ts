import { describe, expect, it } from "vitest";

import { KIND_SLOTS } from "@/charts/palette";
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
