import { describe, expect, it } from "vitest";

import type { LaunchOptions } from "@/api";
import { RunNewBody, RunNewForm, RunConfirm } from "@/features/runs/RunNewView";
import { renderMarkup } from "@/test/render";

const options: LaunchOptions = {
  domains: [
    {
      domain: "llm",
      description: "Managed LLM endpoints.",
      platforms: [
        { platform: "llm-mock", status: "reference" },
        { platform: "llm-endpoint", status: "experimental" },
      ],
    },
  ],
  suites: [{ suite_id: "gsm8k", suite_version: "sample-v1", seen_platforms: ["llm-mock"] }],
  max_repeat: 20,
  max_warmup: 5,
};

const choice = { domain: "llm", suiteId: "gsm8k", platform: "llm-mock" };

describe("RunNewForm", () => {
  it("offers only the platforms of the chosen domain", () => {
    const markup = renderMarkup(
      <RunNewForm
        options={options}
        targets={[]}
        choice={choice}
        target={null}
        params={[]}
        repeat={1}
        warmup={0}
      />,
    );
    expect(markup).toContain("llm-mock");
    expect(markup).toContain("gsm8k");
  });

  it("states the bound on repeats rather than discovering it on submit", () => {
    const markup = renderMarkup(
      <RunNewForm
        options={options}
        targets={[]}
        choice={choice}
        target={null}
        params={[]}
        repeat={1}
        warmup={0}
      />,
    );
    expect(markup).toContain("20");
  });
});

describe("RunConfirm", () => {
  it("shows what the run will freeze that nobody typed", () => {
    // The point of the step: the pinned data version is part of the result
    // and the reader never chose it.
    const markup = renderMarkup(
      <RunConfirm options={options} choice={choice} target={null} params={{}} repeat={1} warmup={0} />,
    );
    expect(markup).toContain("sample-v1");
  });

  it("warns before a run that reaches a real service", () => {
    const markup = renderMarkup(
      <RunConfirm
        options={options}
        choice={{ ...choice, platform: "llm-endpoint" }}
        target={null}
        params={{}}
        repeat={1}
        warmup={0}
      />,
    );
    expect(markup.toLowerCase()).toContain("cost");
  });

  it("is quiet on a reference platform, which spends nothing", () => {
    const markup = renderMarkup(
      <RunConfirm options={options} choice={choice} target={null} params={{}} repeat={1} warmup={0} />,
    );
    expect(markup.toLowerCase()).not.toContain("cost");
  });

  it("does not claim an evaluator or a dataset digest it cannot know", () => {
    // Both are resolved by the run itself. Printing a guess here would be a
    // number that looks like provenance and is not.
    const markup = renderMarkup(
      <RunConfirm options={options} choice={choice} target={null} params={{}} repeat={1} warmup={0} />,
    );
    expect(markup).not.toContain("sha256:");
  });
});

describe("RunNewBody", () => {
  const props = {
    options,
    targets: [],
    choice,
    target: null,
    params: [],
    repeat: 1,
    warmup: 0,
  };

  it("shows the form until it is reviewed", () => {
    const markup = renderMarkup(<RunNewBody confirming={false} {...props} />);
    expect(markup).toContain('id="repeat"');
    expect(markup).not.toContain("sample-v1");
  });

  it("replaces the form with the review, rather than adding it underneath", () => {
    // Appended, the review lands below a sticky action bar and off the bottom
    // of the page: the reader presses "review", sees the button change, and
    // never learns that something appeared. Replacing makes it a step.
    const markup = renderMarkup(<RunNewBody confirming {...props} />);
    expect(markup).toContain("sample-v1");
    expect(markup).not.toContain('id="repeat"');
  });
});
