import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

/**
 * Unit tests for the viewer: the pure logic (the glossary, the formatters,
 * the ETA, the stream reducer, the router) and a thin layer of component
 * tests that render to static markup.
 *
 * There is still no DOM. `renderToStaticMarkup` from `react-dom/server` runs
 * under `environment: "node"` with nothing installed beyond what the app
 * already depends on, and it is enough to assert what a component *emits* —
 * style attributes, widths, `title`s, which rows exist. It is not enough to
 * assert anything about *layout*: there is no layout engine here, so a
 * wrapping row or a truncating cell cannot be observed and a test claiming to
 * check one would be asserting a behaviour it cannot see.
 *
 * The previous note here claimed the component layer was "already covered at
 * the artifact level by tests/test_viewer_frontend.py". Those tests grep a
 * minified bundle for literals; they cannot render anything. Every component
 * defect on this feature was found in a browser or by review, never by a
 * test, which is what that false claim bought.
 */
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    include: ["src/**/*.test.ts?(x)"],
    environment: "node",
  },
});
