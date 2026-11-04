"use client";

import type { KeyboardEvent, ReactNode } from "react";

import { Empty } from "@/components/ui/Panel";
import type { ExperimentPointOut } from "@/lib/types";

import { fmtMetric, metricLabel, pointName } from "./format";

const TH = "label-caps sticky top-0 z-10 bg-panel px-2 py-1 text-right font-medium first:text-left";
const TD = "num px-2 py-[3px] text-right whitespace-nowrap";

/** Rows that select something answer Enter and Space too, like buttons. */
function activate(event: KeyboardEvent, select: () => void) {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    select();
  }
}

export function Th({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <th scope="col" className={TH} title={title}>
      {children}
    </th>
  );
}

/** The sweep points: their settings, runs so far, and the selected metric's mean and median. */
export function PointsTable({
  points,
  metric,
  selectedPoint,
  onSelectPoint,
}: {
  points: ExperimentPointOut[];
  metric: string;
  selectedPoint: number;
  onSelectPoint: (point: number) => void;
}) {
  if (points.length === 0) return <Empty>No points yet</Empty>;
  const parameterNames = [...new Set(points.flatMap((point) => Object.keys(point.parameters)))];
  return (
    <table className="w-full border-collapse text-[11px]" data-testid="points-table">
      <thead>
        <tr className="border-b border-line">
          <Th>#</Th>
          {parameterNames.length === 0 ? (
            <Th>Point</Th>
          ) : (
            parameterNames.map((name) => (
              <Th key={name} title={name}>
                {name.slice(name.lastIndexOf(".") + 1).replaceAll("_", " ")}
              </Th>
            ))
          )}
          <Th>Runs</Th>
          <Th title={`Mean of ${metric} over the point's runs`}>Mean</Th>
          <Th title={`Median of ${metric} over the point's runs`}>Median</Th>
        </tr>
      </thead>
      <tbody>
        {points.map((point) => {
          const summary = point.summary.find((s) => s.metric === metric);
          const selected = point.point === selectedPoint;
          return (
            <tr
              key={point.point}
              data-testid="points-row"
              data-point={point.point}
              aria-selected={selected}
              tabIndex={0}
              onClick={() => onSelectPoint(point.point)}
              onKeyDown={(event) => activate(event, () => onSelectPoint(point.point))}
              className={`cursor-pointer border-b border-line/60 ${
                selected ? "bg-raised text-text" : "hover:bg-hover"
              }`}
              title={point.label}
            >
              <td className={`${TD} text-left text-muted`}>{point.point}</td>
              {parameterNames.length === 0 ? (
                <td className="px-2 py-[3px] text-right text-muted">{pointName(point)}</td>
              ) : (
                parameterNames.map((name) => (
                  <td key={name} className={TD}>
                    {point.parameters[name] ?? "—"}
                  </td>
                ))
              )}
              <td className={TD}>{point.runs}</td>
              <td className={TD}>{fmtMetric(summary?.mean)}</td>
              <td className={TD}>{fmtMetric(summary?.median)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Every metric of one point, summarised over its runs by the backend. */
export function SummaryTable({
  point,
  metric,
  onSelectMetric,
}: {
  point: ExperimentPointOut | undefined;
  metric: string;
  onSelectMetric: (metric: string) => void;
}) {
  if (point === undefined || point.summary.length === 0) {
    return <Empty>No runs stored yet</Empty>;
  }
  return (
    <table className="w-full border-collapse text-[11px]" data-testid="summary-table">
      <thead>
        <tr className="border-b border-line">
          <Th>Metric</Th>
          <Th title="Runs in which the metric was defined">n</Th>
          <Th>Mean</Th>
          <Th title="95% confidence interval for the mean (Student t)">95% CI</Th>
          <Th title="Sample standard deviation">Stdev</Th>
          <Th>Median</Th>
          <Th>p05</Th>
          <Th>p95</Th>
        </tr>
      </thead>
      <tbody>
        {point.summary.map((summary) => {
          const selected = summary.metric === metric;
          return (
            <tr
              key={summary.metric}
              data-testid="summary-row"
              data-metric={summary.metric}
              aria-selected={selected}
              tabIndex={0}
              onClick={() => onSelectMetric(summary.metric)}
              onKeyDown={(event) => activate(event, () => onSelectMetric(summary.metric))}
              className={`cursor-pointer border-b border-line/60 ${
                selected ? "bg-raised text-text" : "hover:bg-hover"
              }`}
            >
              <td className="min-w-32 px-2 py-[3px] text-left leading-tight" title={summary.metric}>
                {metricLabel(summary.metric)}
              </td>
              <td className={`${TD} text-muted`}>{summary.count}</td>
              <td className={TD} data-column="mean">
                {fmtMetric(summary.mean)}
              </td>
              <td className={`${TD} text-muted`} data-column="ci">
                {summary.ci_low === null || summary.ci_high === null
                  ? "—"
                  : `${fmtMetric(summary.ci_low)} … ${fmtMetric(summary.ci_high)}`}
              </td>
              <td className={TD} data-column="stdev">
                {fmtMetric(summary.stdev)}
              </td>
              <td className={TD} data-column="median">
                {fmtMetric(summary.median)}
              </td>
              <td className={`${TD} text-muted`} data-column="p05">
                {fmtMetric(summary.p05)}
              </td>
              <td className={`${TD} text-muted`} data-column="p95">
                {fmtMetric(summary.p95)}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
