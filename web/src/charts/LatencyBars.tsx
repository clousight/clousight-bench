/**
 * Per-unit durations as a sorted bar chart — "which query is the slow one?".
 *
 * Sorted descending rather than left in run order, because the question this
 * answers is about the tail, not the sequence. The slowest few are labelled
 * directly; the rest are not, since a number on every bar is noise.
 */

import { BarChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type { CallbackDataParams } from "echarts/types/dist/shared";
import { useEffect, useMemo, useRef } from "react";

import { escapeHtml, readChrome, readSeriesColors } from "@/charts/palette";
import { formatMetric, type MetricFormat } from "@/lib/glossary";
import { useThemeVersion } from "@/lib/theme";

echarts.use([BarChart, GridComponent, TooltipComponent, CanvasRenderer]);

export interface LatencyBar {
  name: string;
  value: number;
  /** Anything other than "ok" paints in the critical status colour. */
  status?: string;
}

export interface LatencyBarsProps {
  bars: LatencyBar[];
  format?: MetricFormat;
  /** Show at most this many; the rest are summarised by the caller. */
  limit?: number;
  height?: number;
  ariaLabel: string;
}

/** How many of the leading bars carry a direct value label. */
const DIRECT_LABELS = 3;

export function LatencyBars({
  bars,
  format = "duration_ms",
  limit = 40,
  height,
  ariaLabel,
}: LatencyBarsProps) {
  const themeVersion = useThemeVersion();
  const containerRef = useRef<HTMLDivElement>(null);

  const shown = useMemo(
    () => bars.slice().sort((a, b) => b.value - a.value).slice(0, limit),
    [bars, limit],
  );
  const chartHeight = height ?? Math.max(140, shown.length * 18 + 40);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null || shown.length === 0) return;

    const chrome = readChrome();
    const [primary] = readSeriesColors();

    const chart = echarts.init(container);
    chart.setOption({
      animation: false,
      grid: { top: 6, bottom: 24, left: 8, right: 56, containLabel: true },
      tooltip: {
        trigger: "item",
        confine: true,
        backgroundColor: chrome.panel,
        borderColor: chrome.grid,
        textStyle: { color: chrome.panelText, fontSize: 12 },
        formatter: (params: CallbackDataParams | CallbackDataParams[]) => {
          const single = Array.isArray(params) ? params[0] : params;
          const bar = shown[single.dataIndex];
          const formatted = formatMetric(bar.value, format);
          const status = bar.status !== undefined && bar.status !== "ok" ? ` · ${escapeHtml(bar.status)}` : "";
          return `${escapeHtml(bar.name)} — ${formatted.text}${formatted.unit}${status}`;
        },
      },
      xAxis: {
        type: "value",
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { lineStyle: { color: chrome.grid } },
        axisLabel: {
          color: chrome.text,
          fontSize: 10,
          formatter: (value: number) => {
            const formatted = formatMetric(value, format);
            return `${formatted.text}${formatted.unit}`;
          },
        },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: shown.map((bar) => bar.name),
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: chrome.text, fontSize: 10, width: 110, overflow: "truncate" },
      },
      series: [
        {
          type: "bar",
          // Thin marks, rounded on the data end only — the baseline end stays
          // square so the bars visibly share one baseline.
          barMaxWidth: 12,
          itemStyle: {
            borderRadius: [0, 4, 4, 0],
            color: (params: CallbackDataParams) => {
              const bar = shown[params.dataIndex];
              return bar.status !== undefined && bar.status !== "ok" ? chrome.error : primary;
            },
          },
          label: {
            show: true,
            position: "right",
            color: chrome.text,
            fontSize: 10,
            formatter: (params: CallbackDataParams) => {
              if (params.dataIndex >= DIRECT_LABELS) return "";
              const formatted = formatMetric(shown[params.dataIndex].value, format);
              return `${formatted.text}${formatted.unit}`;
            },
          },
          data: shown.map((bar) => bar.value),
        },
      ],
    });

    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(container);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [shown, format, themeVersion]);

  if (shown.length === 0) return null;
  return <div ref={containerRef} style={{ height: chartHeight }} role="img" aria-label={ariaLabel} />;
}
