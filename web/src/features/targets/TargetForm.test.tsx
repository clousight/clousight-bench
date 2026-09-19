import { describe, expect, it } from "vitest";

import { TargetFormBody, formProblem } from "@/features/targets/TargetForm";
import { newTargetTemplate } from "@/lib/targets";
import { renderMarkup } from "@/test/render";

describe("formProblem", () => {
  it("reports the name before the body", () => {
    // One message at a time, and the first field first: a form that lights up
    // every field at once reads as "everything is wrong".
    expect(formProblem("", "")).toEqual({ field: "name", code: "empty" });
    expect(formProblem("a/b", "target: {}\n")).toEqual({ field: "name", code: "charset" });
  });

  it("reports the body once the name is usable", () => {
    expect(formProblem("ok", "")).toEqual({ field: "yaml", code: "empty" });
    expect(formProblem("ok", 'token: "***"\n')).toEqual({ field: "yaml", code: "redacted" });
  });

  it("is silent when there is nothing to say", () => {
    expect(formProblem("ok", "target: {}\n")).toBeNull();
  });
});

describe("TargetFormBody", () => {
  it("starts a new target from a template that invents nothing", () => {
    const markup = renderMarkup(
      <TargetFormBody mode="new" name="" yaml={newTargetTemplate()} problem={null} />,
    );
    expect(markup).toContain("target:");
    expect(markup).not.toMatch(/https?:\/\//);
  });

  it("locks the name when editing, because renaming is a different act", () => {
    // A rename through this form would write a second file and leave the
    // first one behind. Delete and create is the honest spelling of that.
    const markup = renderMarkup(
      <TargetFormBody mode="edit" name="duckdb-sf1" yaml="target: {}\n" problem={null} />,
    );
    expect(markup).toMatch(/name="name"[^>]*readonly|readonly[^>]*name="name"/i);
  });

  it("puts the error under the field it belongs to", () => {
    const markup = renderMarkup(
      <TargetFormBody mode="new" name="a/b" yaml="" problem={{ field: "name", code: "charset" }} />,
    );
    expect(markup).toContain("a name is not a path");
    expect(markup).toContain('role="alert"');
  });

  it("shows the server's refusal verbatim", () => {
    const markup = renderMarkup(
      <TargetFormBody
        mode="new"
        name="x"
        yaml="target: {}\n"
        problem={null}
        failure="not valid YAML: found unexpected end of stream"
      />,
    );
    expect(markup).toContain("found unexpected end of stream");
  });
});
