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

  it("states plainly that it cannot count what uses this target", () => {
    // Records do not carry which config file a run was given, so any number
    // here would be invented. Saying so beats showing a confident 0.
    const markup = renderMarkup(<TargetDetailBody target={detail()} writable={false} />);
    expect(markup).toContain("not recorded");
  });
});
