/**
 * The chart's colours and options. The canvas cannot read CSS variables, so the palette
 * repeats the tokens in src/app/globals.css (keep the two in step).
 */
import {
  ColorType,
  CrosshairMode,
  LineStyle,
  type ChartOptions,
  type DeepPartial,
  type Time,
} from "lightweight-charts";

import { fmtSimTime } from "@/lib/format";

export const PALETTE = {
  panel: "#0b0e12",
  line: "#1a2028",
  lineStrong: "#262e38",
  text: "#d3d9e0",
  muted: "#7d8896",
  faint: "#4a5562",
  bid: "#2fb98a",
  ask: "#ef5260",
  info: "#5aa2e0",
  accent: "#e2a640",
} as const;

/** Volume bars: the candle colours, quieter. */
export const VOLUME_UP = "rgba(47, 185, 138, 0.45)";
export const VOLUME_DOWN = "rgba(239, 82, 96, 0.45)";

/** Times on the chart are simulated seconds since the start of the run. */
export function simTime(time: Time): string {
  return typeof time === "number" ? fmtSimTime(time) : String(time);
}

export function chartOptions(): DeepPartial<ChartOptions> {
  return {
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: PALETTE.panel },
      textColor: PALETTE.muted,
      fontFamily: '"IBM Plex Mono", ui-monospace, Menlo, monospace',
      fontSize: 10,
      panes: {
        separatorColor: PALETTE.line,
        separatorHoverColor: PALETTE.lineStrong,
        enableResize: false,
      },
    },
    grid: {
      vertLines: { color: "rgba(26, 32, 40, 0.55)" },
      horzLines: { color: "rgba(26, 32, 40, 0.55)" },
    },
    rightPriceScale: { borderColor: PALETTE.line, scaleMargins: { top: 0.08, bottom: 0.06 } },
    timeScale: {
      borderColor: PALETTE.line,
      timeVisible: true,
      secondsVisible: true,
      rightOffset: 5,
      barSpacing: 7,
      minBarSpacing: 1,
      tickMarkFormatter: simTime,
    },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: {
        color: PALETTE.faint,
        width: 1,
        style: LineStyle.Dotted,
        labelBackgroundColor: PALETTE.lineStrong,
      },
      horzLine: {
        color: PALETTE.faint,
        width: 1,
        style: LineStyle.Dotted,
        labelBackgroundColor: PALETTE.lineStrong,
      },
    },
    localization: { locale: "en-US", timeFormatter: simTime },
  };
}
