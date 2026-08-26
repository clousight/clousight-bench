/**
 * ECharts custom-series waterfall: one row per span (t_start order, depth-
 * indented labels), bars positioned at (t_start − t0)…(t_end − t0), colored by
 * kind with error bars in the destructive color. Only the pieces we render are
 * imported (echarts/core + CustomChart + Grid/Tooltip + CanvasRenderer) to
 * keep the offline bundle lean. The chart re-initializes on theme and locale
 * changes so CSS-var colors are re-read — canvas paints don't track CSS vars.
 */

import { useEffect, useMemo, useRef } from "react";

import * as echarts from "echarts/core";
import { CustomChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { ECElementEvent } from "echarts/core";
import type {
  CallbackDataParams,
  CustomSeriesRenderItemAPI,
  CustomSeriesRenderItemParams,
  CustomSeriesRenderItemReturn,
} from "echarts/types/dist/shared";

import { useI18n } from "@/i18n";
import { fmtDur } from "@/lib/format";
import { useThemeVersion } from "@/lib/theme";
import type { SpanRow } from "@/lib/trace";

echarts.use([CustomChart, GridComponent, TooltipComponent, CanvasRenderer]);

const ROW_HEIGHT = 28;
const BAR_HEIGHT = 16;
/** Degenerate spans (t_start == t_end) still get a visible sliver. */
const MIN_BAR_WIDTH = 2;

/**
 * Resolve a CSS custom property to a canvas-safe rgb()/rgba() string. The
 * vars hold oklch() values; assigning them to a probe element's `color` and
 * reading it back makes the browser do the conversion.
 */
function resolveCssColor(varName: string): string {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
  const probe = document.createElement("span");
  probe.style.color = raw;
  document.body.appendChild(probe);
  const resolved = getComputedStyle(probe).color;
  probe.remove();
  return resolved !== "" ? resolved : raw;
}

/** Tooltip content is echarts-rendered HTML — escape everything span-derived. */
function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export interface WaterfallProps {
  rows: SpanRow[];
  t0: number;
  onSelect: (id: string) => void;
}

export function Waterfall({ rows, t0, onSelect }: WaterfallProps) {
  const { locale, t } = useI18n();
  const themeVersion = useThemeVersion();
  const containerRef = useRef<HTMLDivElement>(null);
  const unnamed = t('common.unnamed');

  const height = useMemo(() => Math.max(160, rows.length * ROW_HEIGHT + 60), [rows.length]);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null || rows.length === 0) return;

    const colors = {
      llm: resolveCssColor("--chart-llm"),
      tool: resolveCssColor("--chart-tool"),
      error: resolveCssColor("--destructive"),
      text: resolveCssColor("--muted-foreground"),
      grid: resolveCssColor("--border"),
      panel: resolveCssColor("--card"),
      panelText: resolveCssColor("--card-foreground"),
    };

    const totalMs = Math.max(...rows.map((row) => (row.endS - t0) * 1000), 0);
    const inSeconds = totalMs > 10_000;
    const labels = rows.map(
      (row) => "  ".repeat(Math.min(row.depth, 8)) + (row.name ?? unnamed),
    );
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
        shape: { x: start[0], y: start[1] - BAR_HEIGHT / 2, width, height: BAR_HEIGHT, r: 3 },
        style: row.isError
          ? { fill: colors.error, stroke: colors.error, lineWidth: 1.5 }
          : { fill: row.kind === "llm_call" ? colors.llm : colors.tool },
      };
    };

    const chart = echarts.init(container);
    chart.setOption({
      animation: false,
      grid: { top: 10, bottom: 26, left: 8, right: 24, containLabel: true },
      tooltip: {
        trigger: "item",
        confine: true,
        backgroundColor: colors.panel,
        borderColor: colors.grid,
        textStyle: { color: colors.panelText, fontSize: 12 },
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
          color: colors.text,
          fontSize: 11,
          formatter: (value: number) =>
            inSeconds ? `${(value / 1000).toFixed(1)}s` : `${Number(value.toFixed(1))}ms`,
        },
        splitLine: { lineStyle: { color: colors.grid } },
      },
      yAxis: {
        type: "category",
        data: labels,
        inverse: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: colors.text, fontSize: 11, width: 220, overflow: "truncate" },
      },
      series: [{ type: "custom",
        clip: true, renderItem, data, encode: { x: [1, 2], y: 0 } }],
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
    // themeVersion re-reads colors; locale re-renders labels via `unnamed`.
  }, [rows, t0, onSelect, unnamed, locale, themeVersion]);

  return <div ref={containerRef} style={{ height }} role="img" aria-label={t('trace.waterfall')} />;
}
