import { describe, expect, it } from "vitest";

import { ItemFields, hasFields, renderValue } from "@/features/items/ItemFields";
import { renderMarkup } from "@/test/render";

describe("renderValue", () => {
  it("names an artifact pointer as a pointer", () => {
    expect(renderValue({ $artifact: "suite-x/patch.diff" })).toEqual({
      kind: "artifact",
      text: "suite-x/patch.diff",
    });
  });

  it("passes a string through without truncating it", () => {
    // An ellipsis on the output a reader came here to read is the worst of
    // both worlds: it hides the tail and looks complete.
    const long = "x".repeat(5000);
    expect(renderValue(long)).toEqual({ kind: "blob", text: long });
  });

  it("stringifies other objects rather than printing [object Object]", () => {
    expect(renderValue({ a: 1 }).text).toContain('"a": 1');
    expect(renderValue([1, 2]).text).toContain("1");
  });

  it("reports absence as empty, not as an empty string", () => {
    expect(renderValue(null).kind).toBe("empty");
    expect(renderValue(undefined).kind).toBe("empty");
  });

  it("keeps an empty string a blob, because the producer really wrote one", () => {
    // "" is a value the model returned; "absent" is a field it never filled.
    expect(renderValue("")).toEqual({ kind: "blob", text: "" });
  });

  it("keeps numbers and booleans scalar", () => {
    expect(renderValue(17)).toEqual({ kind: "scalar", text: "17" });
    expect(renderValue(false)).toEqual({ kind: "scalar", text: "false" });
  });

  it("does not mistake a plain object with other keys for a pointer", () => {
    expect(renderValue({ artifact: "x" }).kind).toBe("blob");
  });
});

describe("hasFields", () => {
  it("is false for an item with only scores and usage", () => {
    expect(
      hasFields({ item_id: "a", scores: [{ metric: "m", value: 1 }], usage: { latency_ms: 1 } }),
    ).toBe(false);
  });

  it("is true when a score carries a reason or an error", () => {
    expect(hasFields({ item_id: "a", scores: [{ metric: "m", reason: "why" }] })).toBe(true);
    expect(hasFields({ item_id: "a", scores: [{ metric: "m", error: "boom" }] })).toBe(true);
  });

  it("is true when any of input/output/reference/attrs is present", () => {
    expect(hasFields({ item_id: "a", output: "x" })).toBe(true);
    expect(hasFields({ item_id: "a", attrs: { k: 1 } })).toBe(true);
  });
});

describe("ItemFields", () => {
  it("renders output, reference and a score reason", () => {
    const markup = renderMarkup(
      <ItemFields
        item={{
          item_id: "t",
          output: "17",
          reference: "18",
          scores: [{ metric: "accuracy", value: 0, status: "fail", reason: "off by one" }],
        }}
      />,
    );
    expect(markup).toContain("17");
    expect(markup).toContain("18");
    expect(markup).toContain("off by one");
  });

  it("shows an artifact pointer as a reference, never as content", () => {
    const markup = renderMarkup(
      <ItemFields item={{ item_id: "t", output: { $artifact: "suite-x/patch.diff" } }} />,
    );
    expect(markup).toContain("suite-x/patch.diff");
    expect(markup).toContain("Artifact reference");
    expect(markup).not.toContain("[object Object]");
  });

  it("renders the error of an errored score, marked as ours", () => {
    const markup = renderMarkup(
      <ItemFields
        item={{
          item_id: "t",
          scores: [{ metric: "m", status: "error", error: "scorer blew up" }],
        }}
      />,
    );
    expect(markup).toContain("scorer blew up");
    expect(markup).toContain("status-serious");
  });

  it("caps a long blob's height instead of cutting its text", () => {
    const markup = renderMarkup(<ItemFields item={{ item_id: "t", output: "y".repeat(3000) }} />);
    expect(markup).toContain("max-h-64");
    expect(markup).toContain("overflow-auto");
    expect(markup).toContain("y".repeat(3000));
  });
});
