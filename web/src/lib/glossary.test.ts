/**
 * The glossary is the de-jargon layer, so its failure modes are all about
 * saying something we do not know: claiming a direction, inventing a unit, or
 * quietly presenting a fallback as if it were a written spec. These tests are
 * mostly about that.
 */

import { describe, expect, it } from "vitest";

import {
  formatMetric,
  lookupMetric,
  lookupStatus,
  METRIC_SPECS,
  prettifyKey,
  stageTone,
  STAGE_GLOSSARY,
  STAGE_ORDER,
  PHASE_OF_STAGE,
} from "@/lib/glossary";

describe("lookupMetric", () => {
  it("prefers an exact spec over a pattern", () => {
    const exact = lookupMetric("tpc-h.queries_passed");
    expect(exact.known).toBe(true);
    expect(exact.label.en).toBe("Correctness");
    expect(exact.betterIs).toBe("higher");
  });

  it("matches a prefix pattern", () => {
    const acid = lookupMetric("tpc-h.acid_atomicity");
    expect(acid.known).toBe(true);
    expect(acid.format).toBe("pass");
    // The resolved key is the real one, not the pattern — the UI prints it.
    expect(acid.key).toBe("tpc-h.acid_atomicity");
  });

  it("matches a suffix pattern across suites", () => {
    expect(lookupMetric("gsm8k.accuracy").label.en).toBe("Accuracy");
    expect(lookupMetric("mmlu.accuracy").label.en).toBe("Accuracy");
  });

  it("degrades an unknown key without claiming a direction", () => {
    const unknown = lookupMetric("frobnicator.wibble_ms");
    expect(unknown.known).toBe(false);
    expect(unknown.betterIs).toBe("none");
    expect(unknown.format).toBe("duration_ms"); // inferred from the suffix
    expect(unknown.label.en).toBe("wibble");
  });

  it("falls back to the declared unit when the key carries no hint", () => {
    expect(lookupMetric("some.opaque_thing", "ratio").format).toBe("ratio");
    expect(lookupMetric("some.opaque_thing").format).toBe("raw");
  });

  it("never infers a direction from an ambiguous suffix", () => {
    // "_ms" alone does not mean lower is better — a warmup duration that grows
    // can be the desired outcome. Only explicitly-written specs claim one.
    expect(lookupMetric("x.some_ms").betterIs).toBe("none");
    expect(lookupMetric("x.some_ratio").betterIs).toBe("none");
  });
});

describe("METRIC_SPECS", () => {
  it("has no duplicate keys", () => {
    const keys = METRIC_SPECS.map((spec) => spec.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("gives every spec both languages", () => {
    for (const spec of METRIC_SPECS) {
      expect(spec.label.zh, spec.key).not.toBe("");
      expect(spec.label.en, spec.key).not.toBe("");
      if (spec.blurb !== undefined) {
        expect(spec.blurb.zh, spec.key).not.toBe("");
        expect(spec.blurb.en, spec.key).not.toBe("");
      }
    }
  });

  it("orders narrow patterns before broad ones", () => {
    // `*.accuracy` must not shadow a suite-specific accuracy spec added later.
    const wildcardIndex = METRIC_SPECS.findIndex((spec) => spec.key.startsWith("*"));
    const exactAfter = METRIC_SPECS.slice(wildcardIndex).filter((spec) => !spec.key.includes("*"));
    for (const spec of exactAfter) {
      expect(lookupMetric(spec.key).label.en, `${spec.key} is shadowed by a wildcard`).toBe(
        spec.label.en,
      );
    }
  });
});

describe("formatMetric", () => {
  it("scales durations to a readable unit", () => {
    expect(formatMetric(11.42, "duration_ms")).toEqual({ text: "11.4", unit: "ms" });
    expect(formatMetric(0.5, "duration_ms")).toEqual({ text: "0.50", unit: "ms" });
    expect(formatMetric(6604, "duration_ms")).toEqual({ text: "6.60", unit: "s" });
    expect(formatMetric(402, "duration_us")).toEqual({ text: "402", unit: "µs" });
    expect(formatMetric(5870, "duration_us")).toEqual({ text: "5.87", unit: "ms" });
  });

  it("renders a whole ratio without misleading decimals", () => {
    expect(formatMetric(1, "ratio")).toEqual({ text: "100", unit: "%" });
    expect(formatMetric(0.8333, "ratio")).toEqual({ text: "83.3", unit: "%" });
  });

  it("compacts large counts", () => {
    expect(formatMetric(346512, "count").text).toBe("347K");
    expect(formatMetric(7027, "throughput")).toEqual({ text: "7,027", unit: "/s" });
  });

  it("shows a dash rather than NaN for a missing value", () => {
    expect(formatMetric(undefined, "duration_ms").text).toBe("—");
    expect(formatMetric(Number.NaN, "ratio").text).toBe("—");
    expect(formatMetric(null, "count").text).toBe("—");
  });

  it("passes a string value through instead of blanking it", () => {
    expect(formatMetric("auto-retry", "raw").text).toBe("auto-retry");
  });
});

describe("prettifyKey", () => {
  it("strips the namespace and the unit suffix", () => {
    expect(prettifyKey("tpc-x.warm_start_p50_ms")).toBe("warm start p50");
    expect(prettifyKey("throughput_rps")).toBe("throughput");
    expect(prettifyKey("winner")).toBe("winner");
  });
});

describe("lifecycle vocabulary", () => {
  it("explains every stage in both languages", () => {
    for (const stage of STAGE_ORDER) {
      const spec = STAGE_GLOSSARY[stage];
      expect(spec, stage).toBeDefined();
      expect(spec.blurb.zh, stage).not.toBe("");
      expect(spec.blurb.en, stage).not.toBe("");
    }
  });

  it("assigns every stage to a phase", () => {
    for (const stage of STAGE_ORDER) expect(PHASE_OF_STAGE[stage], stage).toBeDefined();
  });

  it("maps an unrecorded stage to pending, not to ok", () => {
    expect(stageTone(undefined)).toBe("pending");
    expect(stageTone("")).toBe("pending");
    expect(stageTone("ok")).toBe("ok");
    expect(stageTone("skipped")).toBe("skipped");
  });
});

describe("lookupStatus", () => {
  it("distinguishes 'never measured' from 'measured and broke'", () => {
    // The single most important distinction the raw words hide.
    expect(lookupStatus("invalid").tone).toBe("warning");
    expect(lookupStatus("failed").tone).toBe("critical");
    expect(lookupStatus("invalid").label.en).toBe("Never measured");
  });

  it("passes an unrecognised status through neutrally", () => {
    const odd = lookupStatus("something-new");
    expect(odd.label.en).toBe("something-new");
    expect(odd.tone).toBe("neutral");
  });
});
