/**
 * The trace view's acceptance harness: drives the real page in headless
 * Chrome and measures the rendered DOM.
 *
 *   # 1. serve a results directory that has a run with a detailed trajectory
 *   uv run --extra dev --extra store csbench serve --results <dir> --port 8799
 *
 *   # 2. start Chrome with a debugging port
 *   "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
 *     --headless --disable-gpu --no-first-run --remote-debugging-port=9333 \
 *     --user-data-dir="$(mktemp -d)" about:blank &
 *
 *   # 3. measure
 *   node web/probe/trace-gate-probe.mjs 9333 \
 *     'http://127.0.0.1:8799/#/record/<run_id>/trace'
 *
 * WHY THIS FILE IS COMMITTED. The gate this view was accepted on ("the zoom is
 * real: sub-2px marks go to zero at the phase window") is not observable from
 * a unit test — `renderToStaticMarkup` has no box model, so a mark's width in
 * pixels does not exist there. It is only observable in a browser, which means
 * the measurement is only as trustworthy as its ability to be re-run. A
 * previous pass deleted its probe after reporting its numbers, and the review
 * that followed could verify none of them. So: the probe lives with the code.
 *
 * WHAT IT SELECTS, AND WHY THAT IS THE WHOLE POINT. Marks are scoped by
 * `[data-surface="tree"]`. `OverviewStrip` draws marks under the same
 * `data-mark` attribute and floors every one of them at `MARK_MIN_PX`, so a
 * bare `[data-mark]` selector mixes a floored population with an unfloored
 * one — the first browser pass on this branch reported "88 marks, all exactly
 * 2px, unchanged by the drag" because it had measured the minimap.
 *
 * AND IT MEASURES WITH THE DOCK OPEN. The dock is drawn OVER the tree rather
 * than inset beside it, precisely so that selecting a span cannot narrow the
 * lane; this probe is what holds that to account, by reporting the lane share
 * and the mark widths in both states. They are expected to be EQUAL.
 */
const [, , port, url] = process.argv;
if (port === undefined || url === undefined) {
  console.error("usage: node trace-gate-probe.mjs <cdp-port> <trace-url>");
  process.exit(2);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let target;
for (let i = 0; i < 40; i++) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    target = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl);
    if (target) break;
  } catch {
    /* Chrome not up yet */
  }
  await sleep(500);
}
if (!target) {
  console.error(`no page target on CDP port ${port}`);
  process.exit(2);
}

const ws = new WebSocket(target.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
ws.addEventListener("message", (ev) => {
  const message = JSON.parse(ev.data);
  if (message.id && pending.has(message.id)) {
    pending.get(message.id)(message);
    pending.delete(message.id);
  }
});
const send = (method, params = {}) =>
  new Promise((resolve) => {
    const n = ++id;
    pending.set(n, resolve);
    ws.send(JSON.stringify({ id: n, method, params }));
  });
await new Promise((r) => ws.addEventListener("open", r));
await send("Page.enable");
// 1440x900 is the width spec §2 designs the row for; the content column is
// 1104px of it. Measuring at any other width measures a different layout.
await send("Emulation.setDeviceMetricsOverride", {
  width: 1440,
  height: 900,
  deviceScaleFactor: 1,
  mobile: false,
});
await send("Page.navigate", { url: "about:blank" });
await sleep(300);
await send("Page.navigate", { url });
await sleep(3500);

const evaluate = async (expression) => {
  const out = await send("Runtime.evaluate", { returnByValue: true, expression });
  if (out.result?.exceptionDetails) {
    console.error(JSON.stringify(out.result.exceptionDetails).slice(0, 500));
    return null;
  }
  return out.result?.result?.value ?? null;
};

/** Rects only. Nothing here is inferred from a class name. */
const MEASURE = `(() => {
  const round = (n) => +n.toFixed(2);
  const rows = [...document.querySelectorAll('[data-row="span"]')];
  const share = (row) => {
    const lane = row.querySelector('[data-col="lane"]');
    if (!lane) return null;
    const r = row.getBoundingClientRect(), l = lane.getBoundingClientRect();
    return { rowPx: round(r.width), lanePx: round(l.width), lanePct: round((l.width / r.width) * 100) };
  };
  // data-surface, not data-mark: see this file's header. The strip's marks
  // carry a 2px floor; nothing in the tree does, which is what makes
  // "under 2px" a meaningful count here rather than a count of the floor.
  const marks = [...document.querySelectorAll('[data-surface="tree"]')];
  const w = (m) => round(m.getBoundingClientRect().width);
  const inLane = marks.filter((m) => m.hasAttribute('data-mark-lane'));
  const queries = marks.filter((m) => m.getAttribute('data-kind') === 'query').map(w).sort((a, b) => a - b);
  const stats = (list) => ({
    n: list.length,
    min: list[0] ?? null,
    p50: list.length ? list[Math.floor(list.length / 2)] : null,
    max: list[list.length - 1] ?? null,
    under2px: list.filter((x) => x < 2).length,
  });
  const crumb = document.querySelector('[data-crumb="true"]');
  const dock = document.querySelector('[data-dock="overlay"]');
  return JSON.stringify({
    spanRows: rows.length,
    laneRows: document.querySelectorAll('[data-row="lane"]').length,
    laneMarks: inLane.length,
    laneMarkButtons: inLane.filter((m) => m.tagName === 'BUTTON').length,
    ariaHiddenLaneMarks: inLane.filter((m) => m.getAttribute('aria-hidden') === 'true').length,
    // The discriminator doing its job: how many marks a naive selector would
    // have swept up, and how many of those are the strip's floored ones.
    marksByBareSelector: document.querySelectorAll('[data-mark="true"]').length,
    stripMarks: document.querySelectorAll('[data-surface="strip"]').length,
    laneShare: rows.length ? share(rows[rows.length - 1]) : null,
    queryMarks: stats(queries),
    allTreeMarks: stats(marks.map(w).sort((a, b) => a - b)),
    selectedMarks: marks.filter((m) => m.getAttribute('aria-pressed') === 'true').length,
    selectedRows: [...document.querySelectorAll('[data-col="name"] button[aria-pressed="true"]')].length,
    dockOpen: dock !== null,
    dockPx: dock ? round(dock.getBoundingClientRect().width) : null,
    crumb: crumb ? crumb.textContent.trim() : null,
  }, null, 1);
})()`;

const report = async (label) => {
  console.log(`\n===== ${label} =====\n${await evaluate(MEASURE)}`);
};

const mouse = (type, x, y) =>
  send("Input.dispatchMouseEvent", {
    type,
    x,
    y,
    button: "left",
    buttons: type === "mouseReleased" ? 0 : 1,
    clickCount: 1,
    pointerType: "mouse",
  });
const click = async (x, y) => {
  await mouse("mousePressed", x, y);
  await mouse("mouseReleased", x, y);
  await sleep(450);
};
// The strip is focused through the DOM rather than by clicking it: a click on
// the strip is a "show the whole run" gesture and would undo the zoom being
// measured.
const key = async (k, code) => {
  await evaluate(`document.querySelector('[role="img"][tabindex="0"]').focus(), 1`);
  await send("Input.dispatchKeyEvent", { type: "keyDown", key: k, text: k, windowsVirtualKeyCode: code });
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: k, windowsVirtualKeyCode: code });
  await sleep(450);
};

/** The disclosure button of the row whose name starts with `name`. */
const chevron = async (name) =>
  JSON.parse(
    await evaluate(`(() => {
      const row = [...document.querySelectorAll('[data-row="span"]')].find((r) =>
        r.querySelector('[data-col="name"]').textContent.trim().startsWith(${JSON.stringify(name)}));
      if (!row) return JSON.stringify({ found: false });
      const b = row.querySelector('button[aria-expanded]');
      if (!b) return JSON.stringify({ found: false, reason: 'no disclosure' });
      const r = b.getBoundingClientRect();
      return JSON.stringify({ found: true, x: r.left + r.width / 2, y: r.top + r.height / 2 });
    })()`),
  );

/** A lane mark by the span it stands for. */
const laneMark = async (name) =>
  JSON.parse(
    await evaluate(`(() => {
      const m = [...document.querySelectorAll('button[data-mark-lane]')].find((b) =>
        (b.getAttribute('aria-label') || '').startsWith(${JSON.stringify(name)}));
      if (!m) return JSON.stringify({ found: false });
      const r = m.getBoundingClientRect();
      return JSON.stringify({
        found: true, label: m.getAttribute('aria-label'), expanded: m.getAttribute('aria-expanded'),
        pressed: m.getAttribute('aria-pressed'),
        x: r.left + r.width / 2, y: r.top + r.height / 2,
        width: +r.width.toFixed(2), height: +r.height.toFixed(2),
      });
    })()`),
  );

await report("1. arrival — whole run, slowest path open, nothing selected");

// Drag the strip to the throughput phase, as fractions of the run. Defaults
// are the canonical gate trace's throughput window (11.927s -> 12.589s of
// 12.673s); override with FROM/TO in the environment for another trace.
const from = Number(process.env.FROM ?? 0.9411942512963567);
const to = Number(process.env.TO ?? 0.9934343734146445);
const rect = JSON.parse(
  await evaluate(`(() => {
    const el = document.querySelector('[role="img"][tabindex="0"]');
    const r = el.getBoundingClientRect();
    return JSON.stringify({ left: r.left, top: r.top, width: r.width, height: r.height });
  })()`),
);
const x0 = rect.left + from * rect.width;
const x1 = rect.left + to * rect.width;
const y = rect.top + rect.height / 2;
await mouse("mousePressed", x0, y);
await sleep(80);
await mouse("mouseMoved", (x0 + x1) / 2, y);
await sleep(80);
await mouse("mouseMoved", x1, y);
await sleep(80);
await mouse("mouseReleased", x1, y);
await sleep(600);
await report("2. zoomed to the phase window — dock still closed");

const tp = await chevron(process.env.CONCURRENT_ROW ?? "tpc-h.throughput");
if (!tp.found) console.error("concurrent row not found", JSON.stringify(tp));
else await click(tp.x, tp.y);
await report("3. the concurrent node expanded — its children pack into lanes");

// Clicking a lane mark selects AND drills, which is the gesture the whole I1
// finding is about: it is how the packed spans are reached, and it is what
// opens the dock.
const stream = await laneMark(process.env.LANE_SPAN ?? "tpc-h.stream1");
console.log(`\nlane mark: ${JSON.stringify(stream)}`);
if (stream.found) await click(stream.x, stream.y);
await report("4. DRILLED THROUGH A LANE MARK — DOCK OPEN (the state the gate omitted)");

// Numbers say the lane did not narrow; they do not say the overlay is opaque
// and sits where it should. `SHOT=<path>` captures the dock-open frame so
// that half can be checked by looking.
if (process.env.SHOT !== undefined) {
  const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
  const fs = await import("node:fs");
  fs.writeFileSync(process.env.SHOT, Buffer.from(shot.result.data, "base64"));
  console.log(`\nwrote ${process.env.SHOT}`);
}

// Dismiss through the dock's own control, so the "dock closed" numbers are
// reached without touching the tree's layout on the way.
const closer = JSON.parse(
  await evaluate(`(() => {
    const b = document.querySelector('[data-action="close-dock"]');
    if (!b) return JSON.stringify({ found: false });
    const r = b.getBoundingClientRect();
    return JSON.stringify({ found: true, x: r.left + r.width / 2, y: r.top + r.height / 2 });
  })()`),
);
if (closer.found) await click(closer.x, closer.y);
await report("5. same tree, dock closed — compare mark-for-mark against 4");

await key("w", 87);
await report("6. one further W zoom");

ws.close();
