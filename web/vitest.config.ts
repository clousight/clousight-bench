import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/**
 * Unit tests for the viewer's pure logic — the glossary, the formatters, the
 * ETA, the stream reducer, the router. Deliberately no DOM tests: the
 * component layer is already covered at the artifact level by
 * tests/test_viewer_frontend.py, and a jsdom harness would be a large
 * dependency for little added confidence.
 */
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    include: ["src/**/*.test.ts"],
    environment: "node",
  },
});
