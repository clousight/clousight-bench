import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "@/App";
import { applyTheme, initialTheme } from "@/lib/theme";
import "@/index.css";

// Apply the persisted/media theme class before the first render (no flash;
// strict CSP forbids an inline <head> script, so this module is the earliest
// place it can happen).
applyTheme(initialTheme());

const root = document.getElementById("root");
if (!root) throw new Error("missing #root mount point");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
