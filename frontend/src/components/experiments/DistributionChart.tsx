"use client";

import { useEffect, useRef, useState } from "react";

import type { ExperimentPointOut, MetricSummaryOut, RunOut } from "@/lib/types";

import { fmtMetric, metricLabel, pointName } from "./format";
import { jitter, niceAxis } from "./scale";

const MARGIN = { top: 14, right: 14, bottom: 34, left: 58 };
const COLORS = {
  grid: "var(--color-line)",
  axis: "var(--color-line-strong)",
  label: "var(--color-muted)",
  box: "var(--color-info)",
  boxFill: "var(--color-info-soft)",
  median: "var(--color-text)",
  mean: "var(--color-accent)",
  ci: "var(--color-accent-soft)",
  dot: "var(--color-text)",
  selected: "var(--color-raised)",
};

/** The size of an element, kept current as it is resized. */
function useElementSize() {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  useEffect(() => {
    const element = ref.current;
    if (element === null) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry === undefined) return;
      const { width, height } = entry.contentRect;
      setSize((current) =>
        current !== null && current.width === width && current.height === height
          ? current
          : { width, height },
      );
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return { ref, size };
}

interface Column {
  point: ExperimentPointOut;
  summary: MetricSummaryOut | undefined;
  runs: { seed: number; value: number }[];
}

/**
 * One column per sweep point for one metric, all from the backend: the box spans p05–p95, the
 * whiskers minimum–maximum, the line is the median, the diamond the mean with its 95% Student-t
 * confidence interval beside the box, and each dot one run (spread sideways by its seed).
 */
export function DistributionChart({
  points,
  runs,
  metric,
  selectedPoint,
  onSelectPoint,
}: {
  points: ExperimentPointOut[];
  runs: RunOut[];
  metric: string;
  selectedPoint: number;
  onSelectPoint: (point: number) => void;
}) {
  const { ref, size } = useElementSize();

  const columns: Column[] = points.map((point) => ({
    point,
    summary: point.summary.find((summary) => summary.metric === metric),
    runs: runs.flatMap((run) => {
      const value = run.metrics[metric];
      return run.point === point.point && value !== null && value !== undefined
        ? [{ seed: run.seed, value }]
        : [];
    }),
  }));
  // The axis covers what is drawn (boxes, whiskers and dots); a confidence interval reaching
  // past it is cut at the edge and marked.
  const shown: number[] = [];
  for (const column of columns) {
    for (const value of [column.summary?.minimum, column.summary?.maximum]) {
      if (value !== null && value !== undefined) shown.push(value);
    }
    for (const run of column.runs) shown.push(run.value);
  }
  const axis = niceAxis(Math.min(...shown), Math.max(...shown));

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={ref} className="relative min-h-[200px] flex-1">
        {size !== null && size.width > 0 && size.height > 0 && (
          <Plot
            columns={columns}
            axis={axis}
            width={size.width}
            height={size.height}
            metric={metric}
            empty={shown.length === 0}
            selectedPoint={selectedPoint}
            onSelectPoint={onSelectPoint}
          />
        )}
      </div>
      <Legend />
    </div>
  );
}

function Plot({
  columns,
  axis,
  width,
  height,
  metric,
  empty,
  selectedPoint,
  onSelectPoint,
}: {
  columns: Column[];
  axis: ReturnType<typeof niceAxis>;
  width: number;
  height: number;
  metric: string;
  empty: boolean;
  selectedPoint: number;
  onSelectPoint: (point: number) => void;
}) {
  const plotLeft = MARGIN.left;
  const plotRight = width - MARGIN.right;
  const plotTop = MARGIN.top;
  const plotBottom = height - MARGIN.bottom;
  const band = (plotRight - plotLeft) / Math.max(1, columns.length);
  const boxWidth = Math.max(6, Math.min(44, band * 0.36));
  const y = (value: number) =>
    plotBottom - ((value - axis.low) / (axis.high - axis.low)) * (plotBottom - plotTop);
  const clampY = (value: number) => Math.min(plotBottom, Math.max(plotTop, y(value)));
  const tick = (value: number) =>
    value.toLocaleString("en-US", {
      minimumFractionDigits: axis.decimals,
      maximumFractionDigits: axis.decimals,
    });

  return (
    <svg
      width={width}
      height={height}
      className="absolute inset-0 select-none"
      role="img"
      aria-label={`Distribution of ${metricLabel(metric)} over the runs of each point`}
      data-testid="distribution-chart"
      data-metric={metric}
    >
      {columns.map((column, index) =>
        column.point.point === selectedPoint ? (
          <rect
            key="selected"
            x={plotLeft + band * index}
            y={plotTop}
            width={band}
            height={plotBottom - plotTop}
            fill={COLORS.selected}
          />
        ) : null,
      )}
      {!empty &&
        axis.ticks.map((value) => (
          <g key={value}>
            <line x1={plotLeft} x2={plotRight} y1={y(value)} y2={y(value)} stroke={COLORS.grid} />
            <text
              x={plotLeft - 6}
              y={y(value)}
              dy="0.32em"
              textAnchor="end"
              fill={COLORS.label}
              className="num text-[10px]"
            >
              {tick(value)}
            </text>
          </g>
        ))}
      <line x1={plotLeft} x2={plotLeft} y1={plotTop} y2={plotBottom} stroke={COLORS.axis} />
      <line x1={plotLeft} x2={plotRight} y1={plotBottom} y2={plotBottom} stroke={COLORS.axis} />
      {columns.map((column, index) => {
        const center = plotLeft + band * (index + 0.5);
        const s = column.summary;
        const name = pointName(column.point);
        const half = boxWidth / 2;
        const ciX = center + half + 8;
        const defined = s !== undefined && s.count > 0 && s.minimum !== null && s.maximum !== null;
        return (
          <g
            key={column.point.point}
            data-testid="distribution-point"
            data-point={column.point.point}
            data-count={s?.count ?? 0}
            onClick={() => onSelectPoint(column.point.point)}
            className="cursor-pointer"
          >
            <title>
              {`${name}\nn = ${s?.count ?? 0}\nmean ${fmtMetric(s?.mean)} (95% CI ${fmtMetric(s?.ci_low)} to ${fmtMetric(s?.ci_high)})\nmedian ${fmtMetric(s?.median)} · p05 ${fmtMetric(s?.p05)} · p95 ${fmtMetric(s?.p95)}\nmin ${fmtMetric(s?.minimum)} · max ${fmtMetric(s?.maximum)}`}
            </title>
            <rect
              x={plotLeft + band * index}
              y={plotTop}
              width={band}
              height={plotBottom - plotTop}
              fill="transparent"
            />
            {defined && s.minimum !== null && s.maximum !== null && (
              <>
                <line
                  x1={center}
                  x2={center}
                  y1={y(s.maximum)}
                  y2={y(s.minimum)}
                  stroke={COLORS.box}
                />
                <line
                  x1={center - half / 2}
                  x2={center + half / 2}
                  y1={y(s.maximum)}
                  y2={y(s.maximum)}
                  stroke={COLORS.box}
                />
                <line
                  x1={center - half / 2}
                  x2={center + half / 2}
                  y1={y(s.minimum)}
                  y2={y(s.minimum)}
                  stroke={COLORS.box}
                />
              </>
            )}
            {defined && s.p05 !== null && s.p95 !== null && (
              <rect
                x={center - half}
                y={y(s.p95)}
                width={boxWidth}
                height={Math.max(1, y(s.p05) - y(s.p95))}
                fill={COLORS.boxFill}
                stroke={COLORS.box}
              />
            )}
            {defined && s.median !== null && (
              <line
                x1={center - half}
                x2={center + half}
                y1={y(s.median)}
                y2={y(s.median)}
                stroke={COLORS.median}
                strokeWidth={2}
              />
            )}
            {column.runs.map((run) => (
              <circle
                key={run.seed}
                cx={center + jitter(run.seed) * boxWidth * 0.8}
                cy={y(run.value)}
                r={2.25}
                fill={COLORS.dot}
                fillOpacity={0.55}
              >
                <title>{`seed ${run.seed}: ${fmtMetric(run.value)}`}</title>
              </circle>
            ))}
            {defined && s.ci_low !== null && s.ci_high !== null && (
              <>
                <rect
                  x={ciX - 3}
                  y={clampY(s.ci_high)}
                  width={6}
                  height={Math.max(1, clampY(s.ci_low) - clampY(s.ci_high))}
                  fill={COLORS.ci}
                  stroke={COLORS.mean}
                  strokeOpacity={0.5}
                />
                {y(s.ci_high) < plotTop && (
                  <path
                    d={`M${ciX - 4},${plotTop + 5} L${ciX},${plotTop} L${ciX + 4},${plotTop + 5}`}
                    fill="none"
                    stroke={COLORS.mean}
                  />
                )}
                {y(s.ci_low) > plotBottom && (
                  <path
                    d={`M${ciX - 4},${plotBottom - 5} L${ciX},${plotBottom} L${ciX + 4},${plotBottom - 5}`}
                    fill="none"
                    stroke={COLORS.mean}
                  />
                )}
              </>
            )}
            {defined && s.mean !== null && (
              <path
                d={`M${ciX},${y(s.mean) - 4} L${ciX + 4},${y(s.mean)} L${ciX},${y(s.mean) + 4} L${ciX - 4},${y(s.mean)} Z`}
                fill={COLORS.mean}
              />
            )}
            {!defined && (
              <text
                x={center}
                y={(plotTop + plotBottom) / 2}
                textAnchor="middle"
                fill={COLORS.label}
                className="text-[10.5px]"
              >
                {column.point.runs === 0 ? "no runs yet" : "undefined"}
              </text>
            )}
            <text
              x={center}
              y={plotBottom + 14}
              textAnchor="middle"
              fill={column.point.point === selectedPoint ? "var(--color-text)" : COLORS.label}
              className="num text-[10px]"
            >
              {truncate(name, Math.max(6, Math.floor(band / 6.2)))}
            </text>
            <text
              x={center}
              y={plotBottom + 26}
              textAnchor="middle"
              fill="var(--color-faint)"
              className="num text-[9.5px]"
            >
              n={s?.count ?? 0}
            </text>
          </g>
        );
      })}
      {empty && (
        <text
          x={(plotLeft + plotRight) / 2}
          y={(plotTop + plotBottom) / 2}
          textAnchor="middle"
          fill={COLORS.label}
          className="text-[11px]"
        >
          No values for this metric yet
        </text>
      )}
    </svg>
  );
}

function truncate(text: string, length: number): string {
  return text.length <= length ? text : `${text.slice(0, length - 1)}…`;
}

function Legend() {
  const item = "flex items-center gap-1";
  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-t border-line px-2.5 py-1 text-[10.5px] text-muted">
      <span className={item}>
        <svg width="12" height="10" aria-hidden="true">
          <rect x="1" y="1" width="10" height="8" fill={COLORS.boxFill} stroke={COLORS.box} />
        </svg>
        p05–p95
      </span>
      <span className={item}>
        <svg width="12" height="10" aria-hidden="true">
          <line x1="6" x2="6" y1="0" y2="10" stroke={COLORS.box} />
        </svg>
        min–max
      </span>
      <span className={item}>
        <svg width="12" height="10" aria-hidden="true">
          <line x1="0" x2="12" y1="5" y2="5" stroke={COLORS.median} strokeWidth="2" />
        </svg>
        median
      </span>
      <span className={item}>
        <svg width="12" height="10" aria-hidden="true">
          <rect x="4" y="0" width="4" height="10" fill={COLORS.ci} />
          <path d="M6,1 L9,5 L6,9 L3,5 Z" fill={COLORS.mean} />
        </svg>
        mean, 95% CI
      </span>
      <span className={item}>
        <svg width="12" height="10" aria-hidden="true">
          <circle cx="6" cy="5" r="2.25" fill={COLORS.dot} fillOpacity="0.55" />
        </svg>
        one run
      </span>
    </div>
  );
}
