import { describe, expect, it } from "vitest";

import type { RecordDetailData } from "@/api";
import { RunIdentity } from "@/features/run/RunIdentity";
import { renderMarkup } from "@/test/render";

const record = (over: Partial<RecordDetailData> = {}): RecordDetailData => ({
  status: "completed",
  run: {
    run_id: "run-1",
    started_at: "2026-01-01T00:00:00Z",
    finished_at: "2026-01-01T00:00:42Z",
  },
  identity: { domain: "llm", task_id: "suite:gsm8k", adapter: "llm-mock" },
  provenance: { suite_id: "gsm8k" },
  fingerprints: { record_digest: "sha256:abcdef0123456789" },
  ...over,
});

describe("RunIdentity", () => {
  it("answers 'what am I looking at' in one row", () => {
    const markup = renderMarkup(<RunIdentity data={record()} runId="run-1" writable={false} />);
    for (const fact of ["gsm8k", "llm-mock", "llm"]) {
      expect(markup).toContain(fact);
    }
  });

  it("shows how long it took, measured not guessed", () => {
    const markup = renderMarkup(<RunIdentity data={record()} runId="run-1" writable={false} />);
    expect(markup).toContain("42");
  });

  it("leaves the duration blank while a run is unfinished", () => {
    // An end time that is not there is not an end time of now.
    const markup = renderMarkup(
      <RunIdentity
        data={record({ run: { run_id: "run-1", started_at: "2026-01-01T00:00:00Z" } })}
        runId="run-1"
        writable={false}
      />,
    );
    expect(markup).toContain("—");
  });

  it("offers 'create like this' only where a run could be started", () => {
    expect(renderMarkup(<RunIdentity data={record()} runId="run-1" writable />)).toContain(
      "#/runs/new?from=run-1",
    );
    expect(
      renderMarkup(<RunIdentity data={record()} runId="run-1" writable={false} />),
    ).not.toContain("#/runs/new");
  });

  it("shows the digest that makes the run checkable", () => {
    const markup = renderMarkup(<RunIdentity data={record()} runId="run-1" writable={false} />);
    expect(markup).toContain("abcdef01");
  });
});
