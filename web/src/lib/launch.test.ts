import { describe, expect, it } from "vitest";

import type { LaunchOptions } from "@/api";
import { initialChoice, launchWarnings, paramsFromPairs, type ParamPair } from "@/lib/launch";

const options: LaunchOptions = {
  domains: [
    {
      domain: "llm",
      description: "",
      platforms: [
        { platform: "llm-mock", status: "reference" },
        { platform: "llm-endpoint", status: "experimental" },
      ],
    },
    {
      domain: "agent-runtime",
      description: "",
      platforms: [{ platform: "aws-agentcore", status: "skeleton" }],
    },
  ],
  suites: [
    { suite_id: "gsm8k", suite_version: "sample-v1", seen_platforms: ["llm-mock"] },
    { suite_id: "tpc-h", suite_version: "sf1", seen_platforms: [] },
  ],
  max_repeat: 20,
  max_warmup: 5,
};

describe("launchWarnings", () => {
  it("says nothing about a pairing that has run here before", () => {
    expect(
      launchWarnings({ domain: "llm", suiteId: "gsm8k", platform: "llm-mock" }, options),
    ).toEqual([]);
  });

  it("warns that a skeleton adapter cannot run yet", () => {
    // Its empty run count means "not wired to the vendor", not "untried".
    expect(
      launchWarnings(
        { domain: "agent-runtime", suiteId: "gsm8k", platform: "aws-agentcore" },
        options,
      ),
    ).toContain("skeleton");
  });

  it("warns that a pairing is untried, without calling it forbidden", () => {
    // Nothing in the registry maps a benchmark to a platform, so this is
    // evidence rather than a rule — and the button still works.
    expect(
      launchWarnings({ domain: "llm", suiteId: "tpc-h", platform: "llm-mock" }, options),
    ).toContain("unseen");
  });

  it("warns that a non-reference platform may cost real money", () => {
    const warnings = launchWarnings(
      { domain: "llm", suiteId: "gsm8k", platform: "llm-endpoint" },
      options,
    );
    expect(warnings).toContain("cost");
  });

  it("stays quiet about cost on a reference platform, which spends nothing", () => {
    expect(
      launchWarnings({ domain: "llm", suiteId: "gsm8k", platform: "llm-mock" }, options),
    ).not.toContain("cost");
  });
});

describe("paramsFromPairs", () => {
  it("drops blank rows so an untouched row is not a param", () => {
    const pairs: ParamPair[] = [
      { key: "limit", value: "2" },
      { key: "", value: "" },
    ];
    expect(paramsFromPairs(pairs)).toEqual({ limit: 2 });
  });

  it("types what it safely can and leaves the rest as text", () => {
    // The server takes scalars; "2" as a string would silently change what a
    // suite receives, and "2026-01-01" must not become a number.
    expect(
      paramsFromPairs([
        { key: "n", value: "2" },
        { key: "ratio", value: "0.5" },
        { key: "on", value: "true" },
        { key: "off", value: "false" },
        { key: "when", value: "2026-01-01" },
        { key: "list", value: "a,b" },
      ]),
    ).toEqual({ n: 2, ratio: 0.5, on: true, off: false, when: "2026-01-01", list: "a,b" });
  });

  it("keeps the last value when a key is repeated", () => {
    expect(paramsFromPairs([{ key: "n", value: "1" }, { key: "n", value: "2" }])).toEqual({ n: 2 });
  });
});

describe("initialChoice", () => {
  it("opens on a combination that has actually run here", () => {
    // The form's first state is a suggestion, and the reader may press
    // through it. So it must not suggest spending money: a pairing with a
    // record behind it is known to work and known to be free if it was.
    expect(initialChoice(options)).toEqual({
      domain: "llm",
      suiteId: "gsm8k",
      platform: "llm-mock",
    });
  });

  it("falls back to a reference platform before any other", () => {
    // Nothing has run yet, so there is no evidence — but `reference` still
    // means "offline, no account, no cost", and that is the safe default.
    const fresh: LaunchOptions = {
      ...options,
      suites: [{ suite_id: "gsm8k", suite_version: "v", seen_platforms: [] }],
    };
    expect(initialChoice(fresh).platform).toBe("llm-mock");
  });

  it("never opens on a platform that bills when a free one exists", () => {
    const billing: LaunchOptions = {
      domains: [
        {
          domain: "agent-runtime",
          description: "",
          platforms: [{ platform: "aliyun-agentrun", status: "experimental" }],
        },
        { domain: "llm", description: "", platforms: [{ platform: "llm-mock", status: "reference" }] },
      ],
      suites: [{ suite_id: "gsm8k", suite_version: "v", seen_platforms: [] }],
      max_repeat: 20,
      max_warmup: 5,
    };
    // agent-runtime sorts first, and taking the first domain would open the
    // form on a live cloud adapter.
    expect(initialChoice(billing)).toEqual({
      domain: "llm",
      suiteId: "gsm8k",
      platform: "llm-mock",
    });
  });

  it("copes with a build that has nothing installed", () => {
    expect(initialChoice({ domains: [], suites: [], max_repeat: 1, max_warmup: 0 })).toEqual({
      domain: "",
      suiteId: "",
      platform: "",
    });
  });
});
