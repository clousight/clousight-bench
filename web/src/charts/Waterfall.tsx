/**
 * ECharts custom-series waterfall: one row per span in t_start order, bars
 * positioned at (t_start − t0)…(t_end − t0), coloured by kind.
 *
 * One renderer serves two feeds — the sealed trace on a finished run, and the
 * live step stream on a run still going — because `SpanRow` is the only shape
 * it knows about and both sources produce it. That is deliberate: a live view
 * that drew its own approximation of the waterfall would eventually disagree
 * with the sealed one, and the disagreement would be invisible.
 *
 * Only the pieces we render are imported (echarts/core + CustomChart +
 * Grid/Tooltip + CanvasRenderer) to keep the offline bundle lean. The chart
 * re-initializes on theme and locale changes so CSS-var colours are re-read —
 * canvas paints don't track CSS variables.
 */

import { CustomChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import type { ECElementEvent } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type {
  CallbackDataParams,
  CustomSeriesRenderItemAPI,
  CustomSeriesRenderItemParams,
  CustomSeriesRenderItemReturn,
} from "echarts/types/dist/shared";
import { useEffect, useMemo, useRef } from "react";

import { escapeHtml, kindColor, readChrome, readKindColors, readSeriesColors } from "@/charts/palette";
import { useI18n } from "@/i18n";
import { fmtDur } from "@/lib/format";
import { useThemeVersion } from "@/lib/theme";
import type { SpanRow } from "@/lib/trace";
import { cn } from "@/lib/utils";

echarts.use([CustomChart, GridComponent, TooltipComponent, CanvasRenderer]);

const ROW_HEIGHT = 28;
const BAR_HEIGHT = 16;
/** Degenerate spans (t_start == t_end) still get a visible sliver. */
const MIN_BAR_WIDTH = 2;

export interface WaterfallProps {
  rows: SpanRow[];
  t0: number;
  onSelect: (id: string) => void;
  /** Pin the axis to this many ms so a live chart's scale stops jumping. */
  axisMaxMs?: number;
}

export function Waterfall({ rows, t0, onSelect, axisMaxMs }: WaterfallProps) {
  const { locale, t } = useI18n();
  const themeVersion = useThemeVersion();
  const containerRef = useRef<HTMLDivElement>(null);
  const unnamed = t("common.unnamed");

  const height = useMemo(() => Math.max(160, rows.length * ROW_HEIGHT + 60), [rows.length]);
  const kinds = useMemo(() => {
    const seen: string[] = [];
    for (const row of rows) if (row.kind !== "" && !seen.includes(row.kind)) seen.push(row.kind);
    return seen;
  }, [rows]);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null || rows.length === 0) return;

    const chrome = readChrome();
    const kindColors = readKindColors();
    const [fallback] = readSeriesColors();

    const spanMax = Math.max(...rows.map((row) => (row.endS - t0) * 1000), 0);
    const totalMs = Math.max(spanMax, axisMaxMs ?? 0);
    const inSeconds = totalMs > 10_000;
    const labels = rows.map((row) => "  ".repeat(Math.min(row.depth, 8)) + (row.name ?? unnamed));
    const data = rows.map((row, index) => [index, (row.startS - t0) * 1000, (row.endS - t0) * 1000]);

    const renderItem = (
      params: CustomSeriesRenderItemParams,
      api: CustomSeriesRenderItemAPI,
    ): CustomSeriesRenderItemReturn => {
      const index = api.value(0) as number;
      const start = api.coord([api.value(1), index]);
      const end = api.coord([api.value(2), index]);
      const row = rows[params.dataIndex];
      const width = Math.max(end[0] - start[0], MIN_BAR_WIDTH);
      return {
        type: "rect",
        shape: { x: start[0], y: start[1] - BAR_HEIGHT / 2, width, height: BAR_HEIGHT, r: 4 },
        style: row.isError
          ? { fill: chrome.error, stroke: chrome.error, lineWidth: 1.5 }
          : { fill: kindColor(kindColors, row.kind, fallback) },
      };
    };

    const chart = echarts.init(container);
    chart.setOption({
      animation: false,
      grid: { top: 10, bottom: 26, left: 8, right: 24, containLabel: true },
      tooltip: {
        trigger: "item",
        confine: true,
        backgroundColor: chrome.panel,
        borderColor: chrome.grid,
        textStyle: { color: chrome.panelText, fontSize: 12 },
        formatter: (params: CallbackDataParams | CallbackDataParams[]) => {
          const single = Array.isArray(params) ? params[0] : params;
          const row = rows[single.dataIndex];
          const parts = [
            escapeHtml(row.name ?? unnamed),
            escapeHtml(row.kind),
            fmtDur(row.endS - row.startS),
            escapeHtml(row.status),
          ];
          return parts.filter((part) => part !== "").join(" · ");
        },
      },
      xAxis: {
        type: "value",
        min: 0,
        // All-degenerate traces (every t == t0) still need a nonzero axis.
        max: totalMs > 0 ? totalMs : 1,
        axisLabel: {
          color: chrome.text,
          fontSize: 11,
          formatter: (value: number) =>
            inSeconds ? `${(value / 1000).toFixed(1)}s` : `${Number(value.toFixed(1))}ms`,
        },
        splitLine: { lineStyle: { color: chrome.grid } },
      },
      yAxis: {
        type: "category",
        data: labels,
        inverse: true,
        axisLine: { show: false },
        axisTick: { show: false },
        // These row labels are also the palette's light-mode contrast relief:
        // every bar is named in text, so no hue has to carry identity alone.
        axisLabel: { color: chrome.text, fontSize: 11, width: 220, overflow: "truncate" },
      },
      series: [{ type: "custom", clip: true, renderItem, data, encode: { x: [1, 2], y: 0 } }],
    });
    chart.on("click", (event) => {
      const { dataIndex } = event as ECElementEvent;
      const row = rows[dataIndex];
      if (row !== undefined) onSelect(row.id);
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(container);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
    // themeVersion re-reads colours; locale re-renders labels via `unnamed`.
  }, [rows, t0, onSelect, unnamed, locale, themeVersion, axisMaxMs]);

  return (
    <div>
      {kinds.length > 1 && <KindLegend kinds={kinds} />}
      <div ref={containerRef} style={{ height }} role="img" aria-label={t("trace.waterfall")} />
    </div>
  );
}

/** Present whenever more than one kind is on screen, so hue is never the only cue. */
function KindLegend({ kinds }: { kinds: string[] }) {
  const { t } = useI18n();
  // Kept in step with KIND_VARS in charts/palette.ts by
  // charts/palette.test.ts — a legend that disagrees with its chart is worse
  // than no legend.
  const swatch: Record<string, string> = {
    phase: "bg-chart-1",
    query: "bg-chart-2",
    llm_call: "bg-chart-3",
    tool_call: "bg-chart-4",
    span: "bg-chart-1",
  };
  return (
    <div className="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {kinds.map((kind) => (
        <span key={kind} className="inline-flex items-center gap-1.5">
          <span aria-hidden className={cn("size-2.5 rounded-[3px]", swatch[kind] ?? "bg-chart-1")} />
          {t(`trace.kind.${kind}`) === `trace.kind.${kind}` ? kind : t(`trace.kind.${kind}`)}
        </span>
      ))}
    </div>
  );
}
