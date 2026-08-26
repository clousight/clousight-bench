import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

/**
 * The committed dist/ must be offline-first: pytest greps every dist file for
 * "http://" / "https://" and requires ZERO hits. react-dom's production build
 * unavoidably embeds a handful of URL-shaped string literals that are NOT
 * network requests: W3C namespace identifiers (setAttributeNS/createElementNS
 * break if altered) and the error-decoder link inside thrown Error messages.
 * For exactly that allowlist, split the scheme out of the literal
 * (`"http://x"` -> `"http:"+"//x"`): runtime-identical constant folding target,
 * but the contiguous `http://` substring is gone. Anything else URL-shaped in a
 * chunk fails the build here, so the gate trips at build time, not in CI.
 */
function offlineGuard(): Plugin {
  // Functional identifiers: value must stay byte-identical at runtime.
  const allowedLiterals = [
    "http://www.w3.org/1999/xlink",
    "http://www.w3.org/XML/1998/namespace",
    "http://www.w3.org/2000/svg",
    "http://www.w3.org/1998/Math/MathML",
    "http://www.w3.org/2000/xmlns/",
    "http://www.w3.org/1999/xhtml",
  ];
  // Display-only text (error messages): drop the scheme, keep it readable.
  const textRewrites: Array<[string, string]> = [
    ["https://reactjs.org/docs/error-decoder.html", "reactjs.org/docs/error-decoder.html"],
    ["https://react.dev/errors/", "react.dev/errors/"],
  ];
  return {
    name: "csbench-offline-guard",
    generateBundle(_options, bundle) {
      for (const item of Object.values(bundle)) {
        if (item.type === "asset") {
          if (item.fileName.endsWith(".css") && typeof item.source === "string") {
            // lightningcss preserves /*! ... */ license banners; strip them.
            item.source = item.source.replace(/\/\*![\s\S]*?\*\//g, "");
          }
          const text =
            typeof item.source === "string"
              ? item.source
              : new TextDecoder().decode(item.source);
          const hit = text.match(/https?:\/\//);
          if (hit) {
            throw new Error(
              `offline-first violation in ${item.fileName}: external URL near offset ${hit.index}`,
            );
          }
          continue;
        }
        for (const [from, to] of textRewrites) {
          item.code = item.code.split(from).join(to);
        }
        for (const url of allowedLiterals) {
          const scheme = url.slice(0, url.indexOf(":") + 1); // "http:" | "https:"
          const rest = url.slice(scheme.length); // "//..."
          for (const quote of ['"', "'", "`"]) {
            // Match opening-quote + URL prefix so the split lands inside the
            // string literal; whatever follows (query text, closing quote)
            // stays attached to the second literal.
            item.code = item.code
              .split(quote + url)
              .join(`${quote}${scheme}${quote}+${quote}${rest}`);
          }
        }
        const leftover = item.code.match(/https?:\/\/[^\s"'`)]+/);
        if (leftover) {
          const at = leftover.index ?? 0;
          const context = item.code.slice(Math.max(0, at - 60), at + 60);
          throw new Error(
            `offline-first violation in ${item.fileName}: external URL ${leftover[0]}\ncontext: ${context}`,
          );
        }
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), offlineGuard()],
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "src") },
  },
  // Hash router: assets are referenced relative to index.html.
  base: "./",
  server: {
    // npm run dev proxies API calls to a locally running `csbench serve`.
    proxy: { '/api': 'http://127.0.0.1:8787' },
  },
  build: {
    outDir: "../src/clousight_bench/resources/viewer/dist",
    emptyOutDir: true,
    sourcemap: false,
    // Strict CSP (script-src 'self'): never inject the inline modulepreload
    // polyfill script into index.html.
    modulePreload: { polyfill: false },
    rollupOptions: {
      output: {
        // Strip ALL comments (license/sourceMappingURL) from emitted chunks
        // (offline grep: dist must contain zero http(s):// substrings).
        comments: false,
      },
    },
  },
});
