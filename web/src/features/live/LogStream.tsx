/**
 * The live log tail.
 *
 * Auto-scroll is sticky-to-bottom rather than unconditional: the moment a
 * reader scrolls up they are reading something, and yanking them back to the
 * newest line is the fastest way to make a log pane useless during the exact
 * incident it exists for.
 */

import { useEffect, useRef, useState } from "react";

import { useI18n } from "@/i18n";
import { fmtClock } from "@/lib/format";
import type { LogLine } from "@/lib/progressStream";
import { cn } from "@/lib/utils";
import { Section, SectionBody, SectionHead, SectionTitle } from "@/components/ui/section";

const LEVELS = ["ALL", "INFO", "WARNING", "ERROR"] as const;
type LevelFilter = (typeof LEVELS)[number];

const LEVEL_RANK: Record<string, number> = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 4 };

const LEVEL_STYLE: Record<string, string> = {
  WARNING: "text-status-serious",
  ERROR: "text-status-critical",
  CRITICAL: "text-status-critical",
};

/** Distance from the bottom, in px, still counted as "pinned to the bottom". */
const STICK_THRESHOLD_PX = 24;

export function LogStream({ lines }: { lines: LogLine[] }) {
  const { t } = useI18n();
  const [filter, setFilter] = useState<LevelFilter>("ALL");
  const scrollRef = useRef<HTMLDivElement>(null);
  const stuckRef = useRef(true);

  const visible =
    filter === "ALL"
      ? lines
      : lines.filter((line) => (LEVEL_RANK[line.level] ?? 1) >= (LEVEL_RANK[filter] ?? 1));

  useEffect(() => {
    const node = scrollRef.current;
    if (node === null || !stuckRef.current) return;
    node.scrollTop = node.scrollHeight;
  }, [visible.length]);

  const onScroll = () => {
    const node = scrollRef.current;
    if (node === null) return;
    stuckRef.current = node.scrollHeight - node.scrollTop - node.clientHeight <= STICK_THRESHOLD_PX;
  };

  return (
    <Section>
      <SectionHead className="flex-row items-center justify-between space-y-0">
        <SectionTitle>{t("live.logs")}</SectionTitle>
        <div role="group" aria-label={t("live.log_level")} className="flex items-center gap-1">
          {LEVELS.map((level) => (
            <button
              key={level}
              type="button"
              aria-pressed={filter === level}
              onClick={() => setFilter(level)}
              className={cn(
                "rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors",
                filter === level
                  ? "bg-secondary text-secondary-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {level === "ALL" ? t("live.log_all") : level}
            </button>
          ))}
        </div>
      </SectionHead>
      <SectionBody>
        {visible.length === 0 ? (
          <p className="py-4 text-center text-xs text-muted-foreground">{t("live.no_logs")}</p>
        ) : (
          <div
            ref={scrollRef}
            onScroll={onScroll}
            className="max-h-72 overflow-y-auto rounded-md border bg-muted/30 p-2 font-mono text-[11px] leading-relaxed"
          >
            {visible.map((line) => (
              <div key={line.seq} className="flex gap-2">
                <span className="shrink-0 tabular-nums text-muted-foreground">{fmtClock(line.t)}</span>
                <span className={cn("min-w-0 break-words", LEVEL_STYLE[line.level] ?? "")}>{line.msg}</span>
              </div>
            ))}
          </div>
        )}
      </SectionBody>
    </Section>
  );
}
