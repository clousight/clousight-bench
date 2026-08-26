import type { Locale } from "@/i18n";

/** Compact numeric rendering: integers verbatim, floats to 4 significant digits. */
export function fmtNum(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Number.isInteger(value) ? String(value) : String(Number(value.toPrecision(4)));
  }
  if (typeof value === "boolean" || typeof value === "string") return String(value);
  return "";
}

/** Seconds -> "1.25s" / "45.3s" / "8.3m". */
export function fmtDur(seconds: number): string {
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 10) return `${seconds.toFixed(2)}s`;
  if (seconds < 120) return `${seconds.toFixed(1)}s`;
  return `${(seconds / 60).toFixed(1)}m`;
}

/** ISO timestamp -> locale-formatted date/time; unparseable input verbatim. */
export function fmtDate(iso: string, locale: Locale): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(locale === "zh" ? "zh-CN" : "en-GB", {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** Middle-agnostic truncation for long digests: keep a prefix, add an ellipsis. */
export function truncate(value: string, max: number): string {
  return value.length <= max ? value : `${value.slice(0, max - 1)}…`;
}
