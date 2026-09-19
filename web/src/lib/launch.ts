/**
 * Starting a run, as pure functions: what to warn about, and how a typed-in
 * parameter becomes a value.
 *
 * The warnings are the interesting half. None of them blocks the button —
 * they exist because the console knows three things the form cannot show in a
 * dropdown, and a reader about to spend money deserves all three before the
 * click, not after:
 *
 * - a `skeleton` adapter is wired but unproven against the vendor, so its run
 *   will not get far, and its empty history means "cannot" rather than "has
 *   not";
 * - nothing in the registry says which benchmark belongs on which platform,
 *   so a pairing nothing has ever run is worth saying out loud — as evidence,
 *   not as a rule;
 * - anything that is not a `reference` adapter reaches a real service, and
 *   real services bill.
 */

import type { LaunchOptions } from "@/api";

export interface LaunchChoice {
  domain: string;
  suiteId: string;
  platform: string;
}

export type LaunchWarning = "skeleton" | "unseen" | "cost";

/** Everything worth saying before the run starts. Never a refusal. */
export function launchWarnings(choice: LaunchChoice, options: LaunchOptions): LaunchWarning[] {
  const warnings: LaunchWarning[] = [];
  const domain = options.domains.find((entry) => entry.domain === choice.domain);
  const platform = domain?.platforms.find((entry) => entry.platform === choice.platform);
  const suite = options.suites.find((entry) => entry.suite_id === choice.suiteId);

  if (platform?.status === "skeleton") warnings.push("skeleton");
  if (suite !== undefined && !suite.seen_platforms.includes(choice.platform)) {
    warnings.push("unseen");
  }
  if (platform !== undefined && platform.status !== "reference") warnings.push("cost");
  return warnings;
}

export interface ParamPair {
  key: string;
  value: string;
}

/**
 * The typed-in rows as the object the server takes.
 *
 * Conversion is deliberately narrow: a whole number, a decimal, and the two
 * booleans. Everything else stays a string, because guessing wider would turn
 * `2026-01-01` into a number and hand a suite something it never asked for.
 */
export function paramsFromPairs(pairs: ParamPair[]): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {};
  for (const pair of pairs) {
    const key = pair.key.trim();
    if (key === "") continue;
    const raw = pair.value.trim();
    if (raw === "true") out[key] = true;
    else if (raw === "false") out[key] = false;
    else if (raw !== "" && /^-?\d+(\.\d+)?$/.test(raw)) out[key] = Number(raw);
    else out[key] = pair.value;
  }
  return out;
}

/**
 * The combination the form opens on.
 *
 * The first state of a form is a suggestion, and a reader may press straight
 * through it — so it must never suggest spending money. In order: a pairing
 * this results directory has actually produced a record for, then any domain
 * with a `reference` (offline, free) platform, then whatever is installed.
 *
 * Taking "the first domain" instead would open the console on
 * `agent-runtime` / `aliyun-agentrun`, which sorts first and bills.
 */
export function initialChoice(options: LaunchOptions): LaunchChoice {
  const suite = options.suites[0];
  const empty = { domain: "", suiteId: "", platform: "" };
  if (suite === undefined) return empty;

  for (const seen of suite.seen_platforms) {
    const domain = options.domains.find((entry) =>
      entry.platforms.some((platform) => platform.platform === seen),
    );
    if (domain !== undefined) {
      return { domain: domain.domain, suiteId: suite.suite_id, platform: seen };
    }
  }
  const reference = options.domains.find((entry) =>
    entry.platforms.some((platform) => platform.status === "reference"),
  );
  if (reference !== undefined) {
    const platform = reference.platforms.find((entry) => entry.status === "reference");
    return {
      domain: reference.domain,
      suiteId: suite.suite_id,
      platform: platform?.platform ?? "",
    };
  }
  const first = options.domains[0];
  return first === undefined
    ? { ...empty, suiteId: suite.suite_id }
    : { domain: first.domain, suiteId: suite.suite_id, platform: first.platforms[0]?.platform ?? "" };
}
