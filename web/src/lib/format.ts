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

/** Duration in MILLISECONDS -> human string. `run.stage_timings` is in ms
 * (see `orchestrator._ms`); handing those to `fmtDur` renders 496 ms as "8.3m". */
export function fmtDurMs(ms: number): string {
  if (!Number.isFinite(ms)) return "";
  if (ms < 1000) return `${ms.toFixed(ms < 10 ? 2 : 0)}ms`;
  return fmtDur(ms / 1000);
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

/**
 * "2 minutes ago" / "3 天前". Falls back to the absolute date past a week,
 * where "9 days ago" stops being easier to place than the date itself.
 */
export function fmtRelative(iso: string, locale: Locale, now: number = Date.now()): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const seconds = Math.round((now - then) / 1000);
  if (seconds < 0) return fmtDate(iso, locale);
  const zh = locale === "zh";
  if (seconds < 45) return zh ? "刚刚" : "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return zh ? `${minutes} 分钟前` : `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return zh ? `${hours} 小时前` : `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days <= 7) return zh ? `${days} 天前` : `${days}d ago`;
  return fmtDate(iso, locale);
}

/** Elapsed milliseconds as a running clock: "0:42", "12:07", "1:03:11". */
export function fmtClock(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "0:00";
  const total = Math.floor(ms / 1000);
  const seconds = total % 60;
  const minutes = Math.floor(total / 60) % 60;
  const hours = Math.floor(total / 3600);
  const mm = String(minutes).padStart(hours > 0 ? 2 : 1, "0");
  return hours > 0
    ? `${hours}:${mm}:${String(seconds).padStart(2, "0")}`
    : `${mm}:${String(seconds).padStart(2, "0")}`;
}
