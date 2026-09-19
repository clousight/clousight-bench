import { describe, expect, it } from "vitest";

import {
  PlatformTable,
  StatusChip,
  type PlatformCatalogue,
} from "@/features/platforms/PlatformsView";
import { renderMarkup } from "@/test/render";

const catalogue = (over: Partial<PlatformCatalogue> = {}): PlatformCatalogue => ({
  counted: true,
  domains: [
    {
      domain: "key-value",
      description: "Key-value datastores.",
      platforms: [
        {
          platform: "ycsb-local",
          summary: "In-memory reference",
          status: "reference",
          docs: "",
          runs: 2,
        },
        {
          platform: "ycsb-endpoint",
          summary: "Config-connect to a running KV service",
          status: "experimental",
          docs: "https://example.invalid/kv",
          runs: 0,
        },
      ],
    },
  ],
  ...over,
});

describe("StatusChip", () => {
  it("prints a status the UI does not know rather than swallowing it", () => {
    // A plugin may ship a maturity word this build has never heard of. The
    // row must still say what the adapter claims — a blank chip would read
    // as "no opinion" when the adapter had one.
    expect(renderMarkup(<StatusChip status="alpha" />)).toContain("alpha");
  });

  it("gives skeleton the warning tone, not a neutral one", () => {
    // The row most likely to be misread as usable.
    expect(renderMarkup(<StatusChip status="skeleton" />)).toContain("status-warning");
    expect(renderMarkup(<StatusChip status="reference" />)).not.toContain("status-warning");
  });
});

describe("PlatformTable", () => {
  it("renders every platform under its domain", () => {
    const markup = renderMarkup(<PlatformTable data={catalogue()} />);
    expect(markup).toContain("ycsb-local");
    expect(markup).toContain("ycsb-endpoint");
    expect(markup).toContain("Key-value store");
    expect(markup).toContain("Key-value datastores.");
  });

  it("names a domain this build has no word for by its own name", () => {
    // Domains arrive from plugins; the glossary here cannot know them all.
    // Falling through to the i18n key would print "domain.vector-db".
    const data = catalogue();
    data.domains[0].domain = "vector-db";
    const markup = renderMarkup(<PlatformTable data={data} />);
    expect(markup).toContain("vector-db");
    expect(markup).not.toContain("domain.vector-db");
  });

  it("says nobody counted with an em dash, never with a zero", () => {
    // "counted, found none" and "could not count" are different claims, and
    // a 0 in the runs column asserts the first one.
    const data = catalogue({ counted: false });
    data.domains[0].platforms = data.domains[0].platforms.map((p) => ({
      ...p,
      runs: null,
    }));
    const markup = renderMarkup(<PlatformTable data={data} />);
    expect(markup).toContain("—");
    expect(markup).not.toContain(">0<");
  });

  it("links to the vendor's docs only when the adapter gave one", () => {
    const markup = renderMarkup(<PlatformTable data={catalogue()} />);
    expect(markup).toContain('href="https://example.invalid/kv"');
    expect(markup).toContain('rel="noopener noreferrer"');
    expect(markup.match(/<a /g)).toHaveLength(1);
  });

  it("reports a registry that would not load instead of showing an empty shelf", () => {
    // "the plugins are broken" must not render as "no platforms installed".
    const markup = renderMarkup(<PlatformTable data={catalogue({ error: "no entry points" })} />);
    expect(markup).toContain("no entry points");
    expect(markup).toContain('role="alert"');
    expect(markup).not.toContain("ycsb-local");
  });

  it("says the shelf is empty when it is", () => {
    const markup = renderMarkup(<PlatformTable data={catalogue({ domains: [] })} />);
    expect(markup).toContain("No platform adapters are installed.");
  });
});
