import { afterEach, describe, expect, it, vi } from "vitest";

import { deleteTarget, putTarget, writeJSON } from "@/api";

function respondWith(status: number, body: unknown): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("writeJSON", () => {
  it("carries the header that makes a cross-site form unable to forge this", async () => {
    respondWith(200, { name: "x" });
    await writeJSON("api/targets/x", "PUT", { yaml: "a: 1\n" });
    const [, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(init.headers["X-Csbench-Write"]).toBe("1");
    expect(init.method).toBe("PUT");
  });

  it("raises the server's own sentence, not a status code", async () => {
    // The server explains exactly what it refused and why — "refusing to save
    // the redaction placeholder at: target.api_token". Replacing that with
    // "HTTP 400" throws away the only part the reader can act on.
    respondWith(400, { error: "not valid YAML: found unexpected end of stream" });
    await expect(putTarget("x", "target: [", false)).rejects.toThrow(
      "not valid YAML: found unexpected end of stream",
    );
  });

  it("falls back to the status when the body explains nothing", async () => {
    respondWith(500, {});
    await expect(deleteTarget("x")).rejects.toThrow("HTTP 500");
  });
});
