import { describe, expect, it } from "vitest";

import { DASH } from "@/lib/format";
import { fmtDifference, fmtMetric, fmtP, isActive, metricLabel, pointName } from "./format";
import { jitter, niceAxis } from "./scale";

describe("experiment formatting", () => {
  it("names the backend's metrics", () => {
    expect(metricLabel("spread_bps")).toBe("Spread (bps)");
    expect(metricLabel("market_maker.pnl")).toBe("Market maker · P&L");
    expect(metricLabel("fundamental.sharpe")).toBe("Fundamental · Sharpe");
    expect(metricLabel("something_new")).toBe("something_new");
  });

  it("formats values by magnitude, and undefined values as a dash", () => {
    expect(fmtMetric(12345.678)).toBe("12,346");
    expect(fmtMetric(123.456)).toBe("123.5");
    expect(fmtMetric(9.876)).toBe("9.88");
    expect(fmtMetric(0)).toBe("0.00");
    expect(fmtMetric(0.12345)).toBe("0.1235");
    expect(fmtMetric(-0.000012)).toBe("-1.20e-5");
    expect(fmtMetric(null)).toBe(DASH);
    expect(fmtDifference(-2.5)).toBe("−2.50");
    expect(fmtDifference(0.5)).toBe("+0.5000");
    expect(fmtP(0.0286)).toBe("0.0286");
    expect(fmtP(0.00001)).toBe("<0.0001");
    expect(fmtP(undefined)).toBe(DASH);
  });

  it("names sweep points by their parameters", () => {
    expect(pointName({ label: "x", parameters: {} })).toBe("all runs");
    expect(
      pointName({
        label: "x [population.market_makers=3]",
        parameters: { "population.market_makers": 3, "environment.volatility": 0.04 },
      }),
    ).toBe("market_makers=3, volatility=0.04");
  });

  it("knows which experiments are still active", () => {
    expect(
      ["queued", "running", "completed", "failed", "cancelled"].map((s) => isActive(s as never)),
    ).toEqual([true, true, false, false, false]);
  });
});

describe("distribution chart geometry", () => {
  it("covers the range with round ticks", () => {
    const axis = niceAxis(3.2, 9.7);
    expect(axis.low).toBeLessThanOrEqual(3.2);
    expect(axis.high).toBeGreaterThanOrEqual(9.7);
    expect(axis.ticks).toEqual([2, 4, 6, 8, 10]);
    expect(axis.decimals).toBe(0);
    expect(niceAxis(0.011, 0.019).ticks).toEqual([0.01, 0.012, 0.014, 0.016, 0.018, 0.02]);
  });

  it("widens an empty range instead of dividing by zero", () => {
    const flat = niceAxis(5, 5);
    expect(flat.low).toBeLessThan(5);
    expect(flat.high).toBeGreaterThan(5);
    const nothing = niceAxis(Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY);
    expect(nothing.ticks.length).toBeGreaterThan(1);
  });

  it("spreads run dots deterministically", () => {
    const offsets = Array.from({ length: 50 }, (_, seed) => jitter(seed));
    expect(offsets.every((x) => x >= -0.5 && x < 0.5)).toBe(true);
    expect(new Set(offsets).size).toBe(50);
    expect(jitter(7)).toBe(jitter(7));
    expect(jitter(2 ** 40 + 3)).toBe(jitter(2 ** 40 + 3));
  });
});
