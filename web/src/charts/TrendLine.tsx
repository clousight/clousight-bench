/**
 * One metric over successive runs, one line per platform.
 *
 * Deliberately a single-metric chart. Two measures of different scale get two
 * of these side by side, never two y-axes on one plot — a dual axis lets the
 * author choose where the lines cross, which is the fastest way to make a
 * chart lie.
 */

import { LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type { CallbackDataParams } from "echarts/types/dist/shared";
import { useEffect, useRef } from "react";

import { escapeHtml, readChrome, readSeriesColors, seriesColor } from "@/charts/palette";
import { useI18n } from "@/i18n";
import { formatMetric, type MetricFormat } from "@/lib/glossary";
import { useThemeVersion } from "@/lib/theme";

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

export interface TrendSeries {
  /** The entity this line belongs to — its colour follows this, not its rank. */
  name: string;
  /** `[label, value]` oldest → newest. */
  points: Array<[string, number]>;
}

export interface TrendLineProps {
  series: TrendSeries[];
  format: MetricFormat;
  height?: number;
  ariaLabel: string;
}

export function TrendLine({ series, format, height = 180, ariaLabel }: TrendLineProps) {
  const { t } = useI18n();
  const themeVersion = useThemeVersion();
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null || series.length === 0) return;

    const chrome = readChrome();
    const colors = readSeriesColors();
    // Categories are the union of every series' labels, in first-seen order,
    // so lines with different run counts still align on the same x positions.
    const categories: string[] = [];
    for (const line of series) {
      for (const [label] of line.points) if (!categories.includes(label)) categories.push(label);
    }

    const chart = echarts.init(container);
    chart.setOption({
      animation: false,
      grid: { top: series.length > 1 ? 28 : 10, bottom: 22, left: 8, right: 16, containLabel: true },
      // A legend is present for two or more series so identity is never
      // carried by colour alone; a single series is named by its own title.
      legend:
        series.length > 1
          ? {
              top: 0,
              left: 0,
              itemWidth: 10,
              itemHeight: 10,
              icon: "roundRect",
              textStyle: { color: chrome.text, fontSize: 11 },
            }
          : { show: false },
      tooltip: {
        trigger: "axis",
        confine: true,
        axisPointer: { type: "line", lineStyle: { color: chrome.axis } },
        backgroundColor: chrome.panel,
        borderColor: chrome.grid,
        textStyle: { color: chrome.panelText, fontSize: 12 },
        formatter: (params: CallbackDataParams | CallbackDataParams[]) => {
          const rows = Array.isArray(params) ? params : [params];
          if (rows.length === 0) return "";
          // `axisValue` is delivered by the axis trigger but is absent from
          // these typings; read it through a narrow local shape.
          const head = escapeHtml(String((rows[0] as { axisValue?: unknown }).axisValue ?? ""));
          const lines = rows.map((row) => {
            const formatted = formatMetric(row.value, format);
            return `${escapeHtml(String(row.seriesName ?? ""))} ${formatted.text}${formatted.unit}`;
          });
          return [head, ...lines].join("<br/>");
        },
      },
      xAxis: {
        type: "category",
        data: categories,
        boundaryGap: false,
        axisLine: { lineStyle: { color: chrome.axis } },
        axisTick: { show: false },
        axisLabel: { color: chrome.text, fontSize: 10, hideOverlap: true },
      },
      yAxis: {
        type: "value",
        scale: true,
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
      series: series.map((line, index) => {
        const color = seriesColor(colors, index);
        const byLabel = new Map(line.points);
        return {
          type: "line",
          name: line.name,
          showSymbol: line.points.length < 30,
          // >= 8px markers, 2px lines — thin marks, legible hit targets.
          symbolSize: 8,
          lineStyle: { width: 2, color },
          itemStyle: { color },
          // Only the last point is labelled: a number on every point is noise.
          endLabel: {
            show: series.length <= 4,
            color: chrome.text,
            fontSize: 10,
            formatter: () => line.name,
          },
          data: categories.map((label) => byLabel.get(label) ?? null),
          connectNulls: true,
        };
      }),
    });

    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(container);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [series, format, themeVersion, t]);

  if (series.length === 0) return null;
  return <div ref={containerRef} style={{ height }} role="img" aria-label={ariaLabel} />;
}
