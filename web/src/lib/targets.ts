/**
 * Targets, as pure functions: what a name may be, and what a body may not be.
 *
 * The server is the authority on all of this — it has to be, since it owns the
 * filesystem — and every rule here is a copy of one of its rules. The copy
 * earns its place by *when* it speaks: a message under the field as you type,
 * instead of a 404 after a save that already looked like it worked.
 *
 * What is deliberately NOT here: parsing the YAML. There is no YAML parser in
 * this bundle, and half-parsing one in a regex would produce a second opinion
 * about validity that could disagree with the server's. The body goes up, and
 * the server's own message comes back.
 */

/** The string the server writes where a credential-shaped value was. */
export const REDACTION_PLACEHOLDER = "***";

/** The one suffix a target file has, matching viewer/targets.py. */
export const TARGET_SUFFIX = ".yaml";

const NAME_RE = /^[A-Za-z0-9._-]{1,64}$/;

export type TargetNameProblem = "empty" | "charset" | "too_long" | "reserved";

/** The first thing wrong with a target name, or null when it is usable. */
export function targetNameProblem(name: string): TargetNameProblem | null {
  if (name.trim() === "") return "empty";
  if (name === "." || name === "..") return "reserved";
  if (name.length > 64) return "too_long";
  return NAME_RE.test(name) ? null : "charset";
}

export type TargetBodyProblem = "empty" | "redacted";

/** The first thing wrong with a target's YAML text, or null. */
export function targetBodyProblem(text: string): TargetBodyProblem | null {
  if (text.trim() === "") return "empty";
  if (text.includes(REDACTION_PLACEHOLDER)) return "redacted";
  return null;
}

export function targetFilename(name: string): string {
  return `${name}${TARGET_SUFFIX}`;
}

/**
 * The starting text for a new target.
 *
 * Every value is absent rather than exemplary. A filled-in region or endpoint
 * would read as configuration that is already there — and a plausible-looking
 * wrong value is worse than a missing one, because nobody goes looking for it.
 */
export function newTargetTemplate(): string {
  return [
    "# A target is one instance of a platform: where it is, and how to reach it.",
    "# Credentials belong in environment variables — put their NAMES here, never",
    "# the values themselves.",
    "target:",
    "  mode: mock # mock = simulated, no account and no cost; remove it to run for real",
    "params: {}",
    "",
  ].join("\n");
}
