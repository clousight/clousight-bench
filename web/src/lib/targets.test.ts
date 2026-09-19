import { describe, expect, it } from "vitest";

import {
  REDACTION_PLACEHOLDER,
  newTargetTemplate,
  targetBodyProblem,
  targetFilename,
  targetNameProblem,
} from "@/lib/targets";

describe("targetNameProblem", () => {
  it("accepts the names the server accepts", () => {
    for (const name of ["duckdb-sf1", "a", "A.B_c-1", "x".repeat(64)]) {
      expect(targetNameProblem(name)).toBeNull();
    }
  });

  it("rejects anything that could be read as a path", () => {
    // The server refuses these too. Checking here as well is not duplication:
    // it is the difference between a message under the field and a 404 after
    // a round trip that already looked like it worked.
    for (const name of ["a/b", "../etc/passwd", "a\\b", "with space"]) {
      expect(targetNameProblem(name)).toBe("charset");
    }
  });

  it("rejects the two names that are directories", () => {
    expect(targetNameProblem(".")).toBe("reserved");
    expect(targetNameProblem("..")).toBe("reserved");
  });

  it("tells empty apart from malformed", () => {
    // They need different sentences: one is "you have not finished", the
    // other is "what you typed cannot work".
    expect(targetNameProblem("")).toBe("empty");
    expect(targetNameProblem("   ")).toBe("empty");
    expect(targetNameProblem("x".repeat(65))).toBe("too_long");
  });
});

describe("targetBodyProblem", () => {
  it("passes ordinary YAML through, since the server is the parser", () => {
    expect(targetBodyProblem("target:\n  mode: mock\n")).toBeNull();
  });

  it("catches an empty body before the round trip", () => {
    expect(targetBodyProblem("")).toBe("empty");
    expect(targetBodyProblem("\n  \n")).toBe("empty");
  });

  it("catches a redacted read on its way back to disk", () => {
    // Saving this would replace a real credential with three asterisks.
    expect(targetBodyProblem(`target:\n  api_token: "${REDACTION_PLACEHOLDER}"\n`)).toBe("redacted");
  });
});

describe("targetFilename", () => {
  it("is the name plus the one suffix a target has", () => {
    expect(targetFilename("duckdb-sf1")).toBe("duckdb-sf1.yaml");
  });
});

describe("newTargetTemplate", () => {
  it("invents no endpoint, no region, no credential", () => {
    // A filled-in placeholder looks like configuration that is already there.
    const text = newTargetTemplate();
    expect(text).not.toMatch(/https?:\/\//);
    expect(text).not.toMatch(/cn-\w+/);
    expect(text).toContain("target:");
    expect(text).toContain("params:");
  });
});
