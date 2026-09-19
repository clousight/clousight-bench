import { describe, expect, it } from "vitest";

import type { TargetDetailData } from "@/api";
import { TargetDetailBody } from "@/features/targets/TargetDetail";
import { renderMarkup } from "@/test/render";

const detail = (over: Partial<TargetDetailData> = {}): TargetDetailData => ({
  name: "duckdb-sf1",
  filename: "duckdb-sf1.yaml",
  size: 120,
  modified: 1_757_000_000,
  mode: "mock",
  provider: "duckdb",
  region: "",
  error: "",
  data: { target: { mode: "mock" }, params: { scale_factor: 1 } },
  redacted: [],
  yaml: "target:\n  mode: mock\nparams:\n  scale_factor: 1\n",
  ...over,
});

describe("TargetDetailBody", () => {
  it("shows the file's own text when nothing had to be hidden", () => {
    const markup = renderMarkup(<TargetDetailBody target={detail()} writable={false} />);
    expect(markup).toContain("scale_factor");
  });

  it("says a value was redacted rather than showing a blank", () => {
    // "Redacted" and "empty" must not read the same: one means the value is
    // there and not being shown, the other means there is no value.
    const markup = renderMarkup(
      <TargetDetailBody
        target={detail({ redacted: ["target.access_key_secret"], yaml: null })}
        writable={false}
      />,
    );
    expect(markup).toContain("target.access_key_secret");
    expect(markup).toContain("redacted");
  });

  it("does not offer to edit a file it was not given the text of", () => {
    // Editing from a redacted read would save "***" over the real credential.
    const markup = renderMarkup(
      <TargetDetailBody target={detail({ redacted: ["target.token"], yaml: null })} writable />,
    );
    expect(markup).not.toContain("#/targets/duckdb-sf1/edit");
  });

  it("offers edit and delete only on a writable server", () => {
    const readonly = renderMarkup(<TargetDetailBody target={detail()} writable={false} />);
    expect(readonly).not.toContain("target.delete");
    expect(readonly.toLowerCase()).not.toContain(">delete<");

    const writable = renderMarkup(<TargetDetailBody target={detail()} writable />);
    expect(writable.toLowerCase()).toContain("delete");
  });

  it("counts only the runs it can honestly count", () => {
    // Sealed records do not carry the config file they were handed, so the
    // only countable runs are the ones this console started. The number says
    // which population it is counting; a bare "3" would read as every run
    // that ever used this target.
    const markup = renderMarkup(<TargetDetailBody target={detail({ launched: 3 })} writable={false} />);
    expect(markup).toContain("3");
    expect(markup).toContain("started from this console");
  });

  it("says nothing was counted rather than nothing exists", () => {
    const markup = renderMarkup(<TargetDetailBody target={detail({ launched: 0 })} writable={false} />);
    expect(markup).toContain("started from this console");
    // Runs started any other way are uncounted, and the page has to say so or
    // the 0 reads as "safe to delete".
    expect(markup).toContain("does not carry");
  });
});
